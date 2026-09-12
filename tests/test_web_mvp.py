from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from klipper_cnc_assistant.api import create_app


SAMPLE_GCODE = """G21
G90
G94
G0 X1 Y1
G1 X5 Y3 F120
G2 X9 Y3 I2 J0
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

    def test_machine_settings_endpoint_blocks_started_run_until_terminal(self) -> None:
        no_run_response = self.client.put(
            "/api/machine/settings",
            json={"reference_approach_z_feed_mm_min": 30.0},
        )
        self.assertEqual(no_run_response.status_code, 200)

        run_path = (
            self.app.state.job_service.repository.projects_dir
            / "project-settings-barrier"
            / "reports"
            / "jobs"
            / "setup-main"
            / "superior"
            / "current_run.json"
        )
        run_path.parent.mkdir(parents=True, exist_ok=True)
        run_path.write_text(json.dumps({"state": "JOB_READY", "started_at": None}), encoding="utf-8")

        prepared_response = self.client.put(
            "/api/machine/settings",
            json={"reference_approach_z_feed_mm_min": 31.0},
        )
        self.assertEqual(prepared_response.status_code, 200)

        run_path.write_text(
            json.dumps({"state": "OPERATION_RUNNING", "started_at": "2026-08-17T12:00:00+00:00"}),
            encoding="utf-8",
        )
        active_response = self.client.put(
            "/api/machine/settings",
            json={"reference_approach_z_feed_mm_min": 32.0},
        )
        self.assertEqual(active_response.status_code, 400)
        self.assertIn("No se puede modificar la configuración física", active_response.json()["detalle"])
        self.assertEqual(
            self.client.get("/api/machine/settings").json()["reference_approach_z_feed_mm_min"],
            31.0,
        )

        run_path.write_text(
            json.dumps({"state": "JOB_CANCELLED", "started_at": "2026-08-17T12:00:00+00:00"}),
            encoding="utf-8",
        )
        terminal_response = self.client.put(
            "/api/machine/settings",
            json={"reference_approach_z_feed_mm_min": 33.0},
        )
        self.assertEqual(terminal_response.status_code, 200)
        self.assertEqual(terminal_response.json()["reference_approach_z_feed_mm_min"], 33.0)

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
        self.assertEqual(len(analysis_payload["segmentos_vista_previa"]), 3)
        self.assertEqual(analysis_payload["segmentos_vista_previa"][2]["tipo"], "G2")
        self.assertEqual(analysis_payload["segmentos_vista_previa"][2]["numero_linea"], 6)
        self.assertGreater(len(analysis_payload["segmentos_vista_previa"][2]["puntos"]), 12)
        self.assertIn("M3", analysis_payload["comandos_manuales"])
        self.assertFalse(analysis_payload["analisis_incompleto"])
        self.assertEqual(analysis_payload["tolerancia_arco_mm"], 0.05)

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
