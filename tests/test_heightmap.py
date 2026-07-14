from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from klipper_cnc_assistant.api import create_app
from klipper_cnc_assistant.application.errors import ApplicationError
from klipper_cnc_assistant.heightmap import ProbeRegion, interpolate_height
from klipper_cnc_assistant.storage import JsonProjectRepository


class HeightMapBackendTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        data_dir = Path(self.temp_dir.name)
        self.app = create_app(data_dir=data_dir, frontend_dist_dir=data_dir / "dist")
        self.client = TestClient(self.app)
        self.project_service = self.app.state.project_service
        self.height_map_service = self.app.state.height_map_service
        self.project = self.project_service.create_project(nombre="Mapa de prueba", ancho_mm=80, alto_mm=60, espesor_mm=1.6)
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

    def _probe_region(self) -> dict[str, float]:
        return {"min_x_mm": 10.0, "min_y_mm": 8.0, "max_x_mm": 70.0, "max_y_mm": 52.0}

    def test_simulated_height_map_persists_and_keeps_machine_simulated(self) -> None:
        before = self.app.state.machine_session_service.get_status()
        response = self.client.post(
            f"/api/projects/{self.project.id}/operations/{self.operation.id}/height-map/simulate",
            json={
                "filas": 5,
                "columnas": 7,
                "probe_region": self._probe_region(),
                "exclusion_zones": [],
                "superficie_simulada": "inclinacion_y_deformacion",
                "repeticion_simulacion": 9,
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["etiqueta_simulada"])
        self.assertEqual(payload["fuente_datos"], "simulado")
        self.assertEqual(payload["estadisticas"]["cantidad_puntos"], 35)
        self.assertEqual(payload["probe_region"]["min_x_mm"], 10.0)
        self.assertIn("bruto", payload["superficies"])

        second_app = create_app(data_dir=Path(self.temp_dir.name), frontend_dist_dir=Path(self.temp_dir.name) / "dist")
        second_client = TestClient(second_app)
        try:
            persisted = second_client.get(f"/api/projects/{self.project.id}/operations/{self.operation.id}/height-map")
            self.assertEqual(persisted.status_code, 200)
            self.assertEqual(persisted.json()["version"], 1)
        finally:
            second_client.close()

        after = self.app.state.machine_session_service.get_status()
        self.assertEqual(before, after)

    def test_probe_region_must_stay_inside_material(self) -> None:
        with self.assertRaises(ApplicationError):
            self.height_map_service.configure_map(
                project_id=self.project.id,
                operation_id=self.operation.id,
                filas=4,
                columnas=4,
                probe_region=ProbeRegion(min_x_mm=-1, min_y_mm=0, max_x_mm=60, max_y_mm=50),
                exclusion_zones=(),
            )

    def test_exclusion_zones_are_validated_and_respected_by_interpolation(self) -> None:
        with self.assertRaises(ApplicationError):
            self.height_map_service.configure_map(
                project_id=self.project.id,
                operation_id=self.operation.id,
                filas=3,
                columnas=3,
                probe_region=ProbeRegion(**self._probe_region()),
                exclusion_zones=(
                    self._zone("z1", "esquina", 10.0, 8.0, 16.0, 14.0),
                ),
            )

        current = self.height_map_service.configure_map(
            project_id=self.project.id,
            operation_id=self.operation.id,
            filas=2,
            columnas=2,
            probe_region=ProbeRegion(**self._probe_region()),
            exclusion_zones=(self._zone("z2", "centro", 30.0, 20.0, 40.0, 30.0),),
        )
        for sample_id, value in {"hm_0_0": 0.0, "hm_0_1": 1.0, "hm_1_0": 1.0, "hm_1_1": 2.0}.items():
            current = self.height_map_service.update_sample(
                project_id=self.project.id,
                operation_id=self.operation.id,
                sample_id=sample_id,
                z_mm=value,
            )

        outside = interpolate_height(current, x_mm=35.0, y_mm=25.0, mode="bruto")
        self.assertEqual(outside.estado, "fuera de dominio")

    def test_perfect_tilt_has_nonzero_range_and_rms_close_to_zero(self) -> None:
        current = self.height_map_service.configure_map(
            project_id=self.project.id,
            operation_id=self.operation.id,
            filas=3,
            columnas=3,
            probe_region=ProbeRegion(**self._probe_region()),
            exclusion_zones=(),
        )
        for sample in current.muestras:
            z_mm = 0.002 * sample.x_mm - 0.0015 * sample.y_mm + 0.2
            current = self.height_map_service.update_sample(
                project_id=self.project.id,
                operation_id=self.operation.id,
                sample_id=sample.id,
                z_mm=z_mm,
            )
        self.assertGreater(current.estadisticas.rango_alturas_mm or 0.0, 0.0)
        self.assertAlmostEqual(current.estadisticas.desviacion_rms_respecto_plano_mm or 0.0, 0.0, places=6)

    def test_reference_session_states_and_invalidations(self) -> None:
        session = self.client.get(f"/api/projects/{self.project.id}/operations/{self.operation.id}/reference-session").json()
        self.assertEqual(session["estado"], "sin_iniciar")

        session = self.client.post(f"/api/projects/{self.project.id}/operations/{self.operation.id}/reference-session/machine-reference").json()
        self.assertEqual(session["estado"], "origen_xy_pendiente")

        session = self.client.post(
            f"/api/projects/{self.project.id}/operations/{self.operation.id}/reference-session/work-origin",
            json={"x_mm": 0, "y_mm": 0, "z_mm": None},
        ).json()
        self.assertEqual(session["estado"], "referencia_z_pendiente")

        session = self.client.post(
            f"/api/projects/{self.project.id}/operations/{self.operation.id}/reference-session/z-reference",
            json={"x_mm": 10, "y_mm": 8, "z_mm": 0},
        ).json()
        self.assertEqual(session["estado"], "referencia_z_confirmada")

        self.client.post(
            f"/api/projects/{self.project.id}/operations/{self.operation.id}/height-map/simulate",
            json={
                "filas": 3,
                "columnas": 3,
                "probe_region": self._probe_region(),
                "exclusion_zones": [],
                "superficie_simulada": "inclinada",
                "repeticion_simulacion": 4,
            },
        )
        session = self.client.get(f"/api/projects/{self.project.id}/operations/{self.operation.id}/reference-session").json()
        self.assertEqual(session["estado"], "mapa_disponible")

        session = self.client.post(f"/api/projects/{self.project.id}/operations/{self.operation.id}/height-map/validate").json()
        self.assertEqual(session["estado"], "mapa_validado")

        current_map = self.client.get(f"/api/projects/{self.project.id}/operations/{self.operation.id}/height-map").json()
        self.client.patch(
            f"/api/projects/{self.project.id}/operations/{self.operation.id}/height-map/samples/{current_map['muestras'][0]['id']}",
            json={"z_mm": 0.123},
        )
        session = self.client.get(f"/api/projects/{self.project.id}/operations/{self.operation.id}/reference-session").json()
        self.assertEqual(session["estado"], "mapa_disponible")

    def test_compensation_preview_detects_points_outside_domain(self) -> None:
        gcode = "G21\nG90\nG1 X5 Y5 Z-0.1 F100\nG1 X75 Y45 Z-0.1 F100\n"
        self.project_service.upload_operation_gcode(
            project_id=self.project.id,
            operation_id=self.operation.id,
            filename="sample.nc",
            content=gcode,
        )
        self.project_service.analyze_operation(self.project.id, self.operation.id)

        self.client.post(f"/api/projects/{self.project.id}/operations/{self.operation.id}/reference-session/machine-reference")
        self.client.post(
            f"/api/projects/{self.project.id}/operations/{self.operation.id}/reference-session/work-origin",
            json={"x_mm": 0, "y_mm": 0, "z_mm": None},
        )
        self.client.post(
            f"/api/projects/{self.project.id}/operations/{self.operation.id}/reference-session/z-reference",
            json={"x_mm": 10, "y_mm": 8, "z_mm": 0},
        )
        self.client.post(
            f"/api/projects/{self.project.id}/operations/{self.operation.id}/height-map/simulate",
            json={
                "filas": 3,
                "columnas": 3,
                "probe_region": self._probe_region(),
                "exclusion_zones": [],
                "superficie_simulada": "inclinada",
                "repeticion_simulacion": 2,
            },
        )
        self.client.post(f"/api/projects/{self.project.id}/operations/{self.operation.id}/height-map/validate")
        preview = self.client.post(f"/api/projects/{self.project.id}/operations/{self.operation.id}/compensation-preview")
        self.assertEqual(preview.status_code, 200)
        self.assertGreater(preview.json()["preview"]["puntos_fuera_dominio"], 0)
        self.assertIn("z_compensada = z_original", preview.json()["preview"]["convencion_matematica"])

    def test_import_json_csv_and_recalculate_increment_version(self) -> None:
        json_payload = {
            "probe_region": self._probe_region(),
            "grid": {"filas": 2, "columnas": 2, "ancho_mm": 60, "alto_mm": 44, "paso_x_mm": 60, "paso_y_mm": 44},
            "muestras": [
                {"id": "hm_0_0", "fila": 0, "columna": 0, "x_mm": 10, "y_mm": 8, "z_mm": 0.0},
                {"id": "hm_0_1", "fila": 0, "columna": 1, "x_mm": 70, "y_mm": 8, "z_mm": 0.03},
                {"id": "hm_1_0", "fila": 1, "columna": 0, "x_mm": 10, "y_mm": 52, "z_mm": -0.01},
                {"id": "hm_1_1", "fila": 1, "columna": 1, "x_mm": 70, "y_mm": 52, "z_mm": 0.02},
            ],
        }
        imported = self.height_map_service.import_json_map(project_id=self.project.id, operation_id=self.operation.id, content=json.dumps(json_payload))
        self.assertEqual(imported.fuente_datos, "json")
        self.assertEqual(imported.version, 1)

        recalculated = self.height_map_service.recalculate_map(self.project.id, self.operation.id)
        self.assertEqual(recalculated.version, 2)

        csv_content = "\n".join([
            "id,fila,columna,x_mm,y_mm,z_mm,incluida",
            "hm_0_0,0,0,10,8,0.0,true",
            "hm_0_1,0,1,70,8,0.02,true",
            "hm_1_0,1,0,10,52,-0.01,true",
            "hm_1_1,1,1,70,52,0.01,true",
        ])
        imported_csv = self.height_map_service.import_csv_map(project_id=self.project.id, operation_id=self.operation.id, content=csv_content)
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
            superficie_simulada="deformacion_suave",
            repeticion_simulacion=2,
            probe_region=ProbeRegion(**self._probe_region()),
            exclusion_zones=(),
        )
        reloaded_project = repository.load_project(self.project.id)
        self.assertEqual(original_project.operaciones[0].analisis, reloaded_project.operaciones[0].analisis)
        payload = repository.load_height_map_payload(self.project.id, self.operation.id)
        self.assertEqual(len(payload["muestras"]), 9)

    def _zone(self, zone_id: str, nombre: str, min_x: float, min_y: float, max_x: float, max_y: float):
        from klipper_cnc_assistant.heightmap import ExclusionZone

        return ExclusionZone(id=zone_id, nombre=nombre, min_x_mm=min_x, min_y_mm=min_y, max_x_mm=max_x, max_y_mm=max_y)
