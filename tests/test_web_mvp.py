from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from klipper_cnc_assistant.api import create_app


SAMPLE_GCODE = """G21
G90
G0 X1 Y1
G1 X5 Y3 F120
G2 X6 Y4
M3
"""


class WebMvpApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.data_dir = Path(self.tempdir.name)
        self.app = create_app(data_dir=self.data_dir)
        self.client = TestClient(self.app)

    def _create_project(self) -> str:
        response = self.client.post(
            "/api/projects",
            json={
                "nombre": "Proyecto remoto",
                "material": {
                    "ancho_mm": 20.0,
                    "alto_mm": 20.0,
                    "espesor_mm": 1.6,
                },
                "doble_cara": True,
                "eje_volteo": "y",
            },
        )
        self.assertEqual(response.status_code, 201)
        return response.json()["id"]

    def _create_operation(self, project_id: str) -> str:
        response = self.client.post(
            f"/api/projects/{project_id}/operations",
            json={
                "nombre": "Fresado cara superior",
                "tipo": "aislamiento",
                "cara": "superior",
                "orden": 0,
                "herramienta": "V-bit 30",
            },
        )
        self.assertEqual(response.status_code, 201)
        return response.json()["id"]

    def test_health_endpoint_reports_version_mode_and_storage(self) -> None:
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["estado"], "ok")
        self.assertEqual(payload["modo_maquina"], "simulado")
        self.assertEqual(payload["almacenamiento"], "disponible")
        self.assertTrue(payload["version"])

    def test_system_info_endpoint_is_safe(self) -> None:
        response = self.client.get("/api/system/info")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["estado_api"], "operativa")
        self.assertTrue(payload["almacenamiento_disponible"])
        self.assertIn("version_python", payload)
        self.assertIn("hora_servidor", payload)
        self.assertNotIn("token", payload)
        self.assertNotIn("env", payload)

    def test_multipart_upload_and_analysis_return_preview_segments(self) -> None:
        project_id = self._create_project()
        operation_id = self._create_operation(project_id)

        upload_response = self.client.post(
            f"/api/projects/{project_id}/operations/{operation_id}/gcode",
            files={
                "archivo": (
                    "sample_top.nc",
                    SAMPLE_GCODE.encode("utf-8"),
                    "text/plain",
                )
            },
        )
        self.assertEqual(upload_response.status_code, 200)
        upload_payload = upload_response.json()
        self.assertEqual(upload_payload["nombre_archivo_original"], "sample_top.nc")
        self.assertEqual(upload_payload["estado"], "lista para analizar")

        analysis_response = self.client.post(
            f"/api/projects/{project_id}/operations/{operation_id}/analyze"
        )
        self.assertEqual(analysis_response.status_code, 200)
        analysis_payload = analysis_response.json()
        self.assertEqual(analysis_payload["cantidad_movimientos"], 3)
        self.assertEqual(len(analysis_payload["segmentos_lineales"]), 2)
        self.assertEqual(analysis_payload["segmentos_lineales"][0]["tipo"], "G0")
        self.assertEqual(analysis_payload["segmentos_lineales"][1]["tipo"], "G1")
        self.assertIn("M3", analysis_payload["comandos_manuales"])
        self.assertTrue(analysis_payload["analisis_incompleto"])
        self.assertIn("G2", analysis_payload["comandos_no_compatibles"])

    def test_upload_validation_rejects_path_and_extension(self) -> None:
        project_id = self._create_project()
        operation_id = self._create_operation(project_id)

        path_response = self.client.post(
            f"/api/projects/{project_id}/operations/{operation_id}/gcode",
            json={
                "nombre_archivo": "../peligroso.nc",
                "contenido": "G21\n",
            },
        )
        self.assertEqual(path_response.status_code, 400)
        self.assertIn("no puede incluir rutas", path_response.json()["detalle"])

        extension_response = self.client.post(
            f"/api/projects/{project_id}/operations/{operation_id}/gcode",
            files={
                "archivo": (
                    "programa.exe",
                    b"G21\n",
                    "application/octet-stream",
                )
            },
        )
        self.assertEqual(extension_response.status_code, 400)
        self.assertIn("Extension no permitida", extension_response.json()["detalle"])

    def test_remove_file_association_and_persist_project_state(self) -> None:
        project_id = self._create_project()
        operation_id = self._create_operation(project_id)

        self.client.post(
            f"/api/projects/{project_id}/operations/{operation_id}/gcode",
            files={
                "archivo": (
                    "sample_top.nc",
                    SAMPLE_GCODE.encode("utf-8"),
                    "text/plain",
                )
            },
        )
        self.client.post(
            f"/api/projects/{project_id}/operations/{operation_id}/analyze"
        )

        remove_response = self.client.delete(
            f"/api/projects/{project_id}/operations/{operation_id}/gcode"
        )
        self.assertEqual(remove_response.status_code, 200)
        self.assertIsNone(remove_response.json()["archivo_gcode"])
        self.assertEqual(remove_response.json()["estado"], "esperando archivo")

        reloaded_client = TestClient(create_app(data_dir=self.data_dir))
        reloaded_project = reloaded_client.get(f"/api/projects/{project_id}")
        self.assertEqual(reloaded_project.status_code, 200)
        reloaded_operation = reloaded_project.json()["operaciones"][0]
        self.assertIsNone(reloaded_operation["archivo_gcode"])
        self.assertIsNone(reloaded_operation["analisis"])

    def test_analysis_flow_never_executes_machine_gcode(self) -> None:
        project_id = self._create_project()
        operation_id = self._create_operation(project_id)

        with patch(
            "klipper_cnc_assistant.moonraker.client.MoonrakerClient.send_gcode",
            side_effect=AssertionError("No debe enviar G-code a la maquina."),
        ):
            upload_response = self.client.post(
                f"/api/projects/{project_id}/operations/{operation_id}/gcode",
                files={
                    "archivo": (
                        "sample_top.nc",
                        SAMPLE_GCODE.encode("utf-8"),
                        "text/plain",
                    )
                },
            )
            self.assertEqual(upload_response.status_code, 200)
            analyze_response = self.client.post(
                f"/api/projects/{project_id}/operations/{operation_id}/analyze"
            )
            self.assertEqual(analyze_response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
