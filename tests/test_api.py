from __future__ import annotations

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
        self.app = create_app(
            data_dir=Path(self.tempdir.name)
        )
        self.client = TestClient(self.app)

    def test_health_endpoint(self) -> None:
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["estado"],
            "ok",
        )

    def test_create_project_and_list_projects(self) -> None:
        create_response = self.client.post(
            "/api/projects",
            json={
                "nombre": "Proyecto API",
                "material": {
                    "ancho_mm": 80.0,
                    "alto_mm": 50.0,
                    "espesor_mm": 1.6,
                },
            },
        )
        self.assertEqual(
            create_response.status_code,
            201,
        )
        project_id = create_response.json()["id"]

        list_response = self.client.get(
            "/api/projects"
        )
        self.assertEqual(
            list_response.status_code,
            200,
        )
        self.assertEqual(
            list_response.json()[0]["id"],
            project_id,
        )

    def test_operations_and_analysis_endpoints(self) -> None:
        project_id = self.client.post(
            "/api/projects",
            json={
                "nombre": "Proyecto operaciones",
                "material": {
                    "ancho_mm": 20.0,
                    "alto_mm": 20.0,
                },
                "doble_cara": True,
                "eje_volteo": "y",
                "agujeros_alineacion": [
                    {
                        "x_mm": 2.0,
                        "y_mm": 2.0,
                        "diametro_mm": 3.0,
                    }
                ],
            },
        ).json()["id"]

        operation_response = self.client.post(
            f"/api/projects/{project_id}/operations",
            json={
                "nombre": "Aislamiento",
                "tipo": "aislamiento",
                "cara": "superior",
                "orden": 0,
                "herramienta": "V-bit 30",
            },
        )
        self.assertEqual(
            operation_response.status_code,
            201,
        )
        operation_id = operation_response.json()["id"]

        upload_response = self.client.post(
            f"/api/projects/{project_id}/operations/{operation_id}/gcode",
            json={
                "nombre_archivo": "job.nc",
                "contenido": (
                    "G21\nG90\nG1 X10 Y10 F120\n"
                    "M3\nT1\n"
                ),
            },
        )
        self.assertEqual(
            upload_response.status_code,
            200,
        )
        self.assertTrue(
            upload_response.json()["archivo_gcode"].startswith(
                "originals/"
            )
        )

        analyze_response = self.client.post(
            f"/api/projects/{project_id}/operations/{operation_id}/analyze"
        )
        self.assertEqual(
            analyze_response.status_code,
            200,
        )
        self.assertTrue(
            analyze_response.json()["cabe_en_material"]
        )
        self.assertEqual(
            analyze_response.json()["acciones_husillo"],
            ["M3"],
        )
        self.assertEqual(
            analyze_response.json()["cambios_herramienta"],
            ["T1"],
        )

        get_analysis_response = self.client.get(
            f"/api/projects/{project_id}/operations/{operation_id}/analysis"
        )
        self.assertEqual(
            get_analysis_response.status_code,
            200,
        )
        self.assertEqual(
            get_analysis_response.json()["cantidad_movimientos"],
            1,
        )

    def test_delete_operation_endpoint(self) -> None:
        project_id = self.client.post(
            "/api/projects",
            json={
                "nombre": "Proyecto delete",
                "material": {
                    "ancho_mm": 30.0,
                    "alto_mm": 30.0,
                },
            },
        ).json()["id"]
        operation_id = self.client.post(
            f"/api/projects/{project_id}/operations",
            json={
                "nombre": "Operacion",
                "tipo": "personalizada",
                "cara": "superior",
                "orden": 0,
            },
        ).json()["id"]

        delete_response = self.client.delete(
            f"/api/projects/{project_id}/operations/{operation_id}"
        )
        self.assertEqual(
            delete_response.status_code,
            200,
        )
        self.assertEqual(
            delete_response.json()["detalle"],
            "Operacion eliminada.",
        )

    def test_http_errors_are_returned_in_spanish(self) -> None:
        response = self.client.get(
            "/api/projects/no-existe"
        )
        self.assertEqual(response.status_code, 404)
        self.assertIn(
            "El proyecto",
            response.json()["detalle"],
        )

        invalid_response = self.client.post(
            "/api/projects",
            json={
                "nombre": "",
                "material": {
                    "ancho_mm": -1,
                    "alto_mm": 10.0,
                },
            },
        )
        self.assertEqual(
            invalid_response.status_code,
            422,
        )
        self.assertIn(
            "Solicitud invalida",
            invalid_response.json()["detalle"],
        )

    def test_machine_session_is_simulated(self) -> None:
        response = self.client.get(
            "/api/machine/session"
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            payload["estado"],
            "simulada_lista_para_preparacion",
        )
        self.assertTrue(
            payload["home_realizado"]
        )

    def test_api_paths_do_not_touch_hardware(self) -> None:
        with patch(
            "klipper_cnc_assistant.moonraker.client.MoonrakerClient.__init__",
            side_effect=AssertionError("No debe tocar Moonraker."),
        ), patch(
            "klipper_cnc_assistant.input.serial_driver.SerialDriver.__init__",
            side_effect=AssertionError("No debe tocar serial."),
        ):
            app = create_app(
                data_dir=Path(self.tempdir.name) / "safe"
            )
            client = TestClient(app)
            response = client.get(
                "/api/machine/session"
            )
            self.assertEqual(
                response.status_code,
                200,
            )
