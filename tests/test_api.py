from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from klipper_cnc_assistant.api import create_app


class ApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.data_dir = Path(self.tempdir.name)
        self.app = create_app(data_dir=self.data_dir)
        self.client = TestClient(self.app)

    def _create_project(self) -> str:
        return self.client.post(
            "/api/projects",
            json={
                "nombre": "Proyecto API",
                "material": {"ancho_mm": 80.0, "alto_mm": 50.0, "espesor_mm": 1.6},
            },
        ).json()["id"]

    def _create_operation(self, project_id: str) -> str:
        return self.client.post(
            f"/api/projects/{project_id}/operations",
            json={
                "nombre": "Aislamiento",
                "tipo": "aislamiento",
                "cara": "superior",
                "orden": 0,
                "herramienta": "V-bit 30",
            },
        ).json()["id"]

    def test_health_endpoint(self) -> None:
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["estado"], "ok")

    def test_create_project_and_list_projects(self) -> None:
        project_id = self._create_project()
        list_response = self.client.get("/api/projects")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()[0]["id"], project_id)

    def test_operations_and_analysis_endpoints(self) -> None:
        project_id = self._create_project()
        operation_id = self._create_operation(project_id)

        upload_response = self.client.post(
            f"/api/projects/{project_id}/operations/{operation_id}/gcode",
            json={
                "nombre_archivo": "job.nc",
                "contenido": "G21\nG90\nG1 X10 Y10 F120\nM3\nT1\n",
            },
        )
        self.assertEqual(upload_response.status_code, 200)
        self.assertTrue(upload_response.json()["archivo_gcode"].startswith("originals/"))

        analyze_response = self.client.post(f"/api/projects/{project_id}/operations/{operation_id}/analyze")
        self.assertEqual(analyze_response.status_code, 200)
        payload = analyze_response.json()
        self.assertTrue(payload["cabe_en_material"])
        self.assertEqual(payload["acciones_husillo"], ["M3"])
        self.assertEqual(payload["cambios_herramienta"], ["T1"])
        self.assertEqual(payload["analysis_version"], payload["current_analysis_version"])
        self.assertFalse(payload["analisis_desactualizado"])

    def test_delete_operation_endpoint(self) -> None:
        project_id = self._create_project()
        operation_id = self._create_operation(project_id)
        delete_response = self.client.delete(f"/api/projects/{project_id}/operations/{operation_id}")
        self.assertEqual(delete_response.status_code, 200)
        self.assertEqual(delete_response.json()["detalle"], "Operacion eliminada.")

    def test_http_errors_are_returned_in_spanish(self) -> None:
        response = self.client.get("/api/projects/no-existe")
        self.assertEqual(response.status_code, 404)
        self.assertIn("El proyecto", response.json()["detalle"])

        invalid_response = self.client.post(
            "/api/projects",
            json={"nombre": "", "material": {"ancho_mm": -1, "alto_mm": 10.0}},
        )
        self.assertEqual(invalid_response.status_code, 422)
        self.assertIn("Solicitud invalida", invalid_response.json()["detalle"])

    def test_reference_validation_errors_are_translated(self) -> None:
        project_id = self._create_project()
        operation_id = self._create_operation(project_id)
        self.client.post(f"/api/projects/{project_id}/operations/{operation_id}/reference-session/machine-reference")
        self.client.post(
            f"/api/projects/{project_id}/operations/{operation_id}/reference-session/work-origin",
            json={"x_mm": 0, "y_mm": 0},
        )
        response = self.client.post(
            f"/api/projects/{project_id}/operations/{operation_id}/reference-session/z-reference",
            json={"x_mm": 0, "y_mm": 0, "z_mm": "abc"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("z_mm", response.json()["detalle"])
        self.assertIn("numero valido", response.json()["detalle"])

    def test_machine_session_is_simulated_and_home_is_unknown_until_confirmed(self) -> None:
        response = self.client.get("/api/machine/session")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["estado"], "simulada_lista_para_preparacion")
        self.assertFalse(payload["home_realizado"])
        self.assertIsNone(payload["referencia_maquina_confirmada_en"])

    def test_simulated_home_is_confirmed_once_per_session(self) -> None:
        project_id = self._create_project()
        operation_id = self._create_operation(project_id)
        first = self.client.post(f"/api/projects/{project_id}/operations/{operation_id}/reference-session/machine-reference")
        second = self.client.post(f"/api/projects/{project_id}/operations/{operation_id}/reference-session/machine-reference")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["machine_reference"]["fecha"], second.json()["machine_reference"]["fecha"])

    def test_old_analysis_is_detected_as_stale(self) -> None:
        project_id = self._create_project()
        operation_id = self._create_operation(project_id)
        self.client.post(
            f"/api/projects/{project_id}/operations/{operation_id}/gcode",
            json={"nombre_archivo": "job.nc", "contenido": "G21\nG94\nG1 X5 Y5 F120\n"},
        )
        self.client.post(f"/api/projects/{project_id}/operations/{operation_id}/analyze")

        project_file = self.data_dir / "projects" / project_id / "project.json"
        payload = json.loads(project_file.read_text(encoding="utf-8"))
        del payload["operaciones"][0]["analisis"]["analysis_version"]
        project_file.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")

        stale_client = TestClient(create_app(data_dir=self.data_dir))
        try:
            response = stale_client.get(f"/api/projects/{project_id}")
            self.assertEqual(response.status_code, 200)
            analysis = response.json()["operaciones"][0]["analisis"]
            self.assertTrue(analysis["analisis_desactualizado"])
        finally:
            stale_client.close()

    def test_api_paths_do_not_touch_hardware(self) -> None:
        project_id = self._create_project()
        operation_id = self._create_operation(project_id)
        with patch(
            "klipper_cnc_assistant.moonraker.client.MoonrakerClient.__init__",
            side_effect=AssertionError("No debe tocar Moonraker."),
        ), patch(
            "klipper_cnc_assistant.input.serial_driver.SerialDriver.__init__",
            side_effect=AssertionError("No debe tocar serial."),
        ):
            app = create_app(data_dir=self.data_dir)
            client = TestClient(app)
            response = client.post(f"/api/projects/{project_id}/operations/{operation_id}/reference-session/machine-reference")
            self.assertEqual(response.status_code, 200)
            client.close()


    def test_setup_operations_api_and_shared_references(self) -> None:
        project_id = self._create_project()
        project = self.client.get(f"/api/projects/{project_id}").json()
        setup_id = project["montajes"][0]["id"]
        first = self.client.post(
            f"/api/projects/{project_id}/operations",
            json={
                "setup_id": setup_id, "nombre": "Taladrado 0,8 mm",
                "tipo": "taladrado", "herramienta": "Broca 0,8 mm",
            },
        ).json()
        second = self.client.post(
            f"/api/projects/{project_id}/operations",
            json={
                "setup_id": setup_id, "nombre": "Taladrado 1,0 mm",
                "tipo": "taladrado", "herramienta": "Broca 1,0 mm",
            },
        ).json()
        self.assertEqual(first["setup_id"], setup_id)
        self.assertEqual(second["orden"], 1)

        self.client.post(
            f"/api/projects/{project_id}/operations/{first['id']}/reference-session/machine-reference"
        )
        self.client.post(
            f"/api/projects/{project_id}/operations/{first['id']}/reference-session/work-origin",
            json={"x_mm": 0, "y_mm": 0},
        )
        shared = self.client.get(
            f"/api/projects/{project_id}/operations/{second['id']}/reference-session"
        ).json()
        self.assertEqual(shared["origen_trabajo"]["x_mm"], 0)
        self.assertEqual(shared["origen_trabajo"]["y_mm"], 0)

        simulated = self.client.post(
            f"/api/projects/{project_id}/operations/{first['id']}/height-map/simulate",
            json={
                "filas": 3,
                "columnas": 3,
                "superficie_simulada": "inclinada",
                "repeticion_simulacion": 4,
                "probe_region": {
                    "min_x_mm": 2, "min_y_mm": 2,
                    "max_x_mm": 78, "max_y_mm": 48,
                },
                "exclusion_zones": [],
            },
        )
        self.assertEqual(simulated.status_code, 200)
        shared_map = self.client.get(
            f"/api/projects/{project_id}/operations/{second['id']}/height-map"
        )
        self.assertEqual(shared_map.status_code, 200)
        self.assertEqual(shared_map.json()["version"], simulated.json()["version"])

    def test_system_info_exposes_build_compatibility(self) -> None:
        payload = self.client.get("/api/system/info").json()
        self.assertIn("backend_version", payload)
        self.assertIn("frontend_build", payload)
        self.assertIn("git_commit", payload)
        self.assertEqual(payload["schema_version"], "1.5")
