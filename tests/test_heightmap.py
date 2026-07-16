from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from klipper_cnc_assistant.api import create_app
from klipper_cnc_assistant.application.errors import ApplicationError
from klipper_cnc_assistant.heightmap import ProbeRegion, interpolate_height
from klipper_cnc_assistant.heightmap.coverage import DOMAIN_TOLERANCE_MM, build_coverage_report, check_domain
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
        self.assertFalse(session["lista_para_compensacion"])

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
        self.assertEqual(session["estado"], "referencia_maquina_pendiente")
        self.assertEqual(session["pasos"][4]["estado"], "disponible")
        self.assertEqual(session["pasos"][4]["detalle"], "Mapa disponible, pendiente de referencias.")

        session = self.client.post(f"/api/projects/{self.project.id}/operations/{self.operation.id}/reference-session/machine-reference").json()
        self.assertEqual(session["estado"], "origen_xy_pendiente")

        session = self.client.post(
            f"/api/projects/{self.project.id}/operations/{self.operation.id}/reference-session/work-origin",
            json={"x_mm": 0, "y_mm": 0},
        ).json()
        self.assertEqual(session["estado"], "referencia_z_pendiente")

        session = self.client.post(
            f"/api/projects/{self.project.id}/operations/{self.operation.id}/reference-session/z-reference",
            json={"x_mm": 0, "y_mm": 0, "z_mm": 0},
        ).json()
        self.assertEqual(session["estado"], "mapa_disponible")
        self.assertFalse(session["lista_para_compensacion"])

        session = self.client.post(f"/api/projects/{self.project.id}/operations/{self.operation.id}/height-map/validate").json()
        self.assertEqual(session["estado"], "mapa_validado")
        self.assertTrue(session["lista_para_compensacion"])

        session = self.client.post(
            f"/api/projects/{self.project.id}/operations/{self.operation.id}/reference-session/z-reference",
            json={"x_mm": 0, "y_mm": 0, "z_mm": 0.15},
        ).json()
        self.assertEqual(session["estado"], "mapa_disponible")
        self.assertFalse(session["lista_para_compensacion"])
        self.assertIn("referencia Z", session["motivo_invalidacion"])

    def test_zero_values_are_valid_for_xy_and_z(self) -> None:
        self.client.post(f"/api/projects/{self.project.id}/operations/{self.operation.id}/reference-session/machine-reference")
        origin = self.client.post(
            f"/api/projects/{self.project.id}/operations/{self.operation.id}/reference-session/work-origin",
            json={"x_mm": 0, "y_mm": 0},
        )
        reference_z = self.client.post(
            f"/api/projects/{self.project.id}/operations/{self.operation.id}/reference-session/z-reference",
            json={"x_mm": 0, "y_mm": 0, "z_mm": 0},
        )
        self.assertEqual(origin.status_code, 200)
        self.assertEqual(reference_z.status_code, 200)
        self.assertEqual(reference_z.json()["referencia_z"]["z_mm"], 0)

    def test_validation_blocks_points_outside_domain(self) -> None:
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
            json={"x_mm": 0, "y_mm": 0},
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
        validation = self.client.post(f"/api/projects/{self.project.id}/operations/{self.operation.id}/height-map/validate")
        self.assertEqual(validation.status_code, 400)
        self.assertIn("cobertura", validation.json()["detalle"].lower())
        self.assertIn("puntos quedan fuera", validation.json()["detalle"])


    def test_domain_checks_edges_tolerance_and_real_outside_points(self) -> None:
        current = self.height_map_service.generate_simulated_map(
            project_id=self.project.id,
            operation_id=self.operation.id,
            filas=3,
            columnas=3,
            superficie_simulada="inclinada",
            repeticion_simulacion=1,
            probe_region=ProbeRegion(**self._probe_region()),
            exclusion_zones=(),
        )
        self.assertTrue(check_domain(current, 20.0, 20.0).inside)
        self.assertTrue(check_domain(current, 10.0, 8.0).inside)
        self.assertTrue(check_domain(current, 10.0 - DOMAIN_TOLERANCE_MM / 2, 8.0).inside)
        just_outside = check_domain(current, 10.0 - DOMAIN_TOLERANCE_MM * 2, 8.0)
        clearly_outside = check_domain(current, 2.0, 8.0)
        self.assertFalse(just_outside.inside)
        self.assertLess(just_outside.distance_mm, 0.001)
        self.assertFalse(clearly_outside.inside)
        self.assertGreater(clearly_outside.distance_mm, 1.0)

    def test_coverage_report_ignores_initial_non_cutting_move_outside_domain(self) -> None:
        gcode = "G21\nG90\nG0 Z5.0\nG0 X0 Y0\nG0 X20 Y20\nG1 X20 Y20 Z-0.1 F100\nG1 X30 Y20 Z-0.1 F100\n"
        self.project_service.upload_operation_gcode(
            project_id=self.project.id,
            operation_id=self.operation.id,
            filename="entry.nc",
            content=gcode,
        )
        operation = self.project_service.analyze_operation(self.project.id, self.operation.id)
        current = self.height_map_service.generate_simulated_map(
            project_id=self.project.id,
            operation_id=self.operation.id,
            filas=3,
            columnas=3,
            superficie_simulada="inclinada",
            repeticion_simulacion=1,
            probe_region=ProbeRegion(**self._probe_region()),
            exclusion_zones=(),
        )
        report = build_coverage_report(
            height_map=current,
            operations=((operation.id, operation.nombre, operation.analisis),),
        )
        self.assertTrue(report.sufficient)
        self.assertEqual(report.points_outside, 0)

    def test_coverage_report_identifies_operation_and_arc_points_outside_domain(self) -> None:
        gcode = "G21\nG90\nG1 X20 Y20 Z-0.1 F100\nG2 X75 Y20 I20 J0 Z-0.1 F100\n"
        self.project_service.upload_operation_gcode(
            project_id=self.project.id,
            operation_id=self.operation.id,
            filename="arc.nc",
            content=gcode,
        )
        operation = self.project_service.analyze_operation(self.project.id, self.operation.id)
        second = self.project_service.add_operation(
            project_id=self.project.id,
            nombre="Taladrado 0,8 mm",
            tipo="taladrado",
            cara="superior",
            orden=1,
        )
        self.project_service.upload_operation_gcode(
            project_id=self.project.id,
            operation_id=second.id,
            filename="drill.nc",
            content="G21\nG90\nG1 X30 Y30 Z-0.1 F100\n",
        )
        second = self.project_service.analyze_operation(self.project.id, second.id)
        current = self.height_map_service.generate_simulated_map(
            project_id=self.project.id,
            operation_id=self.operation.id,
            filas=3,
            columnas=3,
            superficie_simulada="inclinada",
            repeticion_simulacion=1,
            probe_region=ProbeRegion(**self._probe_region()),
            exclusion_zones=(),
        )
        report = build_coverage_report(
            height_map=current,
            operations=((operation.id, operation.nombre, operation.analisis), (second.id, second.nombre, second.analisis)),
        )
        self.assertFalse(report.sufficient)
        self.assertGreater(report.points_outside, 0)
        self.assertEqual(report.issues[0].operation_id, operation.id)
        self.assertGreater(report.max_distance_outside_mm, 0.0)


    def test_interpolation_supports_single_row_and_single_column(self) -> None:
        row_map = self.height_map_service.configure_map(
            project_id=self.project.id,
            operation_id=self.operation.id,
            filas=1,
            columnas=2,
            probe_region=ProbeRegion(min_x_mm=0, min_y_mm=0, max_x_mm=10, max_y_mm=0),
            exclusion_zones=(),
        )
        row_map = self.height_map_service.update_sample(project_id=self.project.id, operation_id=self.operation.id, sample_id="hm_0_0", z_mm=0.0)
        row_map = self.height_map_service.update_sample(project_id=self.project.id, operation_id=self.operation.id, sample_id="hm_0_1", z_mm=0.1)
        self.assertAlmostEqual(interpolate_height(row_map, x_mm=5, y_mm=0).valor_mm or 0.0, 0.05)

        column_map = self.height_map_service.configure_map(
            project_id=self.project.id,
            operation_id=self.operation.id,
            filas=2,
            columnas=1,
            probe_region=ProbeRegion(min_x_mm=0, min_y_mm=0, max_x_mm=0, max_y_mm=10),
            exclusion_zones=(),
        )
        column_map = self.height_map_service.update_sample(project_id=self.project.id, operation_id=self.operation.id, sample_id="hm_0_0", z_mm=0.0)
        column_map = self.height_map_service.update_sample(project_id=self.project.id, operation_id=self.operation.id, sample_id="hm_1_0", z_mm=0.2)
        self.assertAlmostEqual(interpolate_height(column_map, x_mm=0, y_mm=5).valor_mm or 0.0, 0.1)

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
