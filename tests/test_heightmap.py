from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from klipper_cnc_assistant.api import create_app
from klipper_cnc_assistant.application.heightmap_service import HeightMapService
from klipper_cnc_assistant.heightmap import interpolate_height
from klipper_cnc_assistant.storage import JsonProjectRepository


class HeightMapBackendTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        data_dir = Path(self.temp_dir.name)
        self.app = create_app(data_dir=data_dir, frontend_dist_dir=data_dir / "dist")
        self.client = TestClient(self.app)
        self.project_service = self.app.state.project_service
        self.height_map_service = self.app.state.height_map_service
        self.project = self.project_service.create_project(
            nombre="Mapa de prueba",
            ancho_mm=80,
            alto_mm=60,
            espesor_mm=1.6,
        )
        self.operation = self.project_service.add_operation(
            project_id=self.project.id,
            nombre="Fresado superior",
            tipo="aislamiento",
            cara="superior",
            orden=0,
        )

    def tearDown(self) -> None:
        self.client.close()
        self.temp_dir.cleanup()

    def test_simulated_height_map_persists_and_keeps_machine_simulated(self) -> None:
        before = self.app.state.machine_session_service.get_status()
        response = self.client.post(
            f"/api/projects/{self.project.id}/operations/{self.operation.id}/height-map/simulate",
            json={"filas": 5, "columnas": 7, "escenario": "inclinacion_y_deformacion", "semilla": 9},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["etiqueta_simulada"])
        self.assertEqual(payload["fuente_datos"], "simulado")
        self.assertEqual(payload["estadisticas"]["cantidad_puntos"], 35)
        self.assertIn("bruto", payload["superficies"])

        second_app = create_app(data_dir=Path(self.temp_dir.name), frontend_dist_dir=Path(self.temp_dir.name) / "dist")
        second_client = TestClient(second_app)
        try:
            persisted = second_client.get(
                f"/api/projects/{self.project.id}/operations/{self.operation.id}/height-map"
            )
            self.assertEqual(persisted.status_code, 200)
            self.assertEqual(persisted.json()["version"], 1)
        finally:
            second_client.close()

        after = self.app.state.machine_session_service.get_status()
        self.assertEqual(before, after)

    def test_plane_fit_and_outliers_are_reported(self) -> None:
        tilted = self.height_map_service.generate_simulated_map(
            project_id=self.project.id,
            operation_id=self.operation.id,
            filas=4,
            columnas=4,
            escenario="inclinada",
            semilla=3,
        )
        self.assertIsNotNone(tilted.plano)
        assert tilted.plano is not None
        self.assertNotEqual(round(tilted.plano.a, 6), 0.0)
        self.assertNotEqual(round(tilted.plano.b, 6), 0.0)

        outlier = self.height_map_service.generate_simulated_map(
            project_id=self.project.id,
            operation_id=self.operation.id,
            filas=5,
            columnas=5,
            escenario="punto_atipico",
            semilla=4,
        )
        self.assertGreater(outlier.estadisticas.cantidad_puntos_atipicos, 0)

    def test_bilinear_interpolation_is_deterministic_and_respects_missing_points(self) -> None:
        height_map = self.height_map_service.configure_map(
            project_id=self.project.id,
            operation_id=self.operation.id,
            filas=2,
            columnas=2,
        )
        current = height_map
        values = {
            "hm_0_0": 0.0,
            "hm_0_1": 1.0,
            "hm_1_0": 1.0,
            "hm_1_1": 2.0,
        }
        for sample_id, value in values.items():
            current = self.height_map_service.update_sample(
                project_id=self.project.id,
                operation_id=self.operation.id,
                sample_id=sample_id,
                z_mm=value,
            )

        interpolated = interpolate_height(current, x_mm=40, y_mm=30, mode="bruto")
        self.assertEqual(interpolated.estado, "ok")
        self.assertAlmostEqual(interpolated.valor_mm or 0.0, 1.0, places=6)

        current = self.height_map_service.update_sample(
            project_id=self.project.id,
            operation_id=self.operation.id,
            sample_id="hm_1_1",
            z_mm=None,
        )
        missing = interpolate_height(current, x_mm=40, y_mm=30, mode="bruto")
        self.assertEqual(missing.estado, "insuficiente")

        outside = interpolate_height(current, x_mm=200, y_mm=30, mode="bruto")
        self.assertEqual(outside.estado, "fuera de dominio")

    def test_import_json_csv_and_recalculate_increment_version(self) -> None:
        json_payload = {
            "grid": {
                "filas": 2,
                "columnas": 2,
                "ancho_mm": 80,
                "alto_mm": 60,
                "paso_x_mm": 80,
                "paso_y_mm": 60,
            },
            "muestras": [
                {"id": "hm_0_0", "fila": 0, "columna": 0, "x_mm": 0, "y_mm": 0, "z_mm": 0.0},
                {"id": "hm_0_1", "fila": 0, "columna": 1, "x_mm": 80, "y_mm": 0, "z_mm": 0.03},
                {"id": "hm_1_0", "fila": 1, "columna": 0, "x_mm": 0, "y_mm": 60, "z_mm": -0.01},
                {"id": "hm_1_1", "fila": 1, "columna": 1, "x_mm": 80, "y_mm": 60, "z_mm": 0.02},
            ],
        }
        imported = self.height_map_service.import_json_map(
            project_id=self.project.id,
            operation_id=self.operation.id,
            content=json.dumps(json_payload),
        )
        self.assertEqual(imported.fuente_datos, "json")
        self.assertEqual(imported.version, 1)

        recalculated = self.height_map_service.recalculate_map(
            self.project.id,
            self.operation.id,
        )
        self.assertEqual(recalculated.version, 2)

        csv_content = "\n".join(
            [
                "id,fila,columna,x_mm,y_mm,z_mm,incluida",
                "hm_0_0,0,0,0,0,0.0,true",
                "hm_0_1,0,1,80,0,0.02,true",
                "hm_1_0,1,0,0,60,-0.01,true",
                "hm_1_1,1,1,80,60,0.01,true",
            ]
        )
        imported_csv = self.height_map_service.import_csv_map(
            project_id=self.project.id,
            operation_id=self.operation.id,
            content=csv_content,
        )
        self.assertEqual(imported_csv.fuente_datos, "csv")
        self.assertEqual(imported_csv.grid.filas, 2)

    def test_repository_persists_original_samples_without_touching_project_file(self) -> None:
        repository = JsonProjectRepository(Path(self.temp_dir.name))
        original_project = repository.load_project(self.project.id)
        self.height_map_service.generate_simulated_map(
            project_id=self.project.id,
            operation_id=self.operation.id,
            filas=3,
            columnas=3,
            escenario="deformacion_suave",
            semilla=2,
        )
        reloaded_project = repository.load_project(self.project.id)
        self.assertEqual(original_project.operaciones, reloaded_project.operaciones)
        payload = repository.load_height_map_payload(self.project.id, self.operation.id)
        self.assertEqual(len(payload["muestras"]), 9)

