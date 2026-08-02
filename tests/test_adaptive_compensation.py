from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import klipper_cnc_assistant.application.adaptive_compensation as adaptive_compensation_module
from klipper_cnc_assistant.application.adaptive_compensation import ReferenceFrame, generate_adaptive_gcode
from klipper_cnc_assistant.application.errors import ApplicationError
from klipper_cnc_assistant.application.services import ProjectService
from klipper_cnc_assistant.heightmap import HeightGrid, HeightSample, ProbeRegion, SampleQuality, compute_height_map
from klipper_cnc_assistant.storage import JsonProjectRepository


def build_height_map(fn) -> object:
    grid = HeightGrid(filas=5, columnas=5, ancho_mm=20.0, alto_mm=20.0, paso_x_mm=5.0, paso_y_mm=5.0)
    region = ProbeRegion(min_x_mm=0.0, min_y_mm=0.0, max_x_mm=20.0, max_y_mm=20.0)
    samples = []
    for row in range(5):
        for column in range(5):
            x_mm = column * 5.0
            y_mm = row * 5.0
            samples.append(
                HeightSample(
                    id=f"s-{row}-{column}",
                    x_mm=x_mm,
                    y_mm=y_mm,
                    z_mm=float(fn(x_mm, y_mm)),
                    fila=row,
                    columna=column,
                    origen_datos="measured",
                    estado_calidad=SampleQuality.VALIDA,
                )
            )
    return compute_height_map(
        proyecto_id="project",
        operacion_id="operation",
        version=1,
        fuente_datos="measured",
        superficie_simulada=None,
        repeticion_simulacion=None,
        etiqueta_simulada=False,
        grid=grid,
        probe_region=region,
        exclusion_zones=(),
        muestras=samples,
        estado="medido relativo",
    )


class AdaptiveCompensationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.reference = ReferenceFrame(
            machine_origin_x_mm=100.0,
            machine_origin_y_mm=200.0,
            surface_reference_z_mm=10.0,
        )

    def test_flat_mesh_does_not_add_unnecessary_segments(self) -> None:
        height_map = build_height_map(lambda _x, _y: 0.0)
        result = generate_adaptive_gcode(
            original_text="G21\nG90\nG1 X0 Y0 Z-0.1 F120\nG1 X20 Y0 Z-0.1 F120\n",
            height_map=height_map,
            reference_frame=self.reference,
            max_z_error_mm=0.01,
            operation_id="op",
            operation_name="flat",
            min_segment_length_mm=0.05,
        )
        motions = [line for line in result["output"].splitlines() if line.startswith("G1")]
        self.assertEqual(len(motions), 2)
        self.assertEqual(result["preview"]["segments_subdivided"], 0)

    def test_sloped_plane_keeps_long_line_compact(self) -> None:
        height_map = build_height_map(lambda x_mm, _y: 0.01 * x_mm)
        result = generate_adaptive_gcode(
            original_text="G21\nG90\nG1 X0 Y0 Z-0.1 F120\nG1 X20 Y0 Z-0.1 F120\n",
            height_map=height_map,
            reference_frame=self.reference,
            max_z_error_mm=0.01,
            operation_id="op",
            operation_name="slope",
            min_segment_length_mm=0.05,
        )
        motions = [line for line in result["output"].splitlines() if line.startswith("G1")]
        self.assertEqual(len(motions), 2)
        self.assertLessEqual(result["preview"]["max_approximation_error_mm"], 0.01)

    def test_curved_surface_subdivides_when_needed(self) -> None:
        height_map = build_height_map(lambda x_mm, _y: 0.002 * (x_mm - 10.0) ** 2)
        result = generate_adaptive_gcode(
            original_text="G21\nG90\nG1 X0 Y0 Z-0.1 F120\nG1 X20 Y0 Z-0.1 F120\n",
            height_map=height_map,
            reference_frame=self.reference,
            max_z_error_mm=0.01,
            operation_id="op",
            operation_name="curve",
            min_segment_length_mm=0.05,
        )
        motions = [line for line in result["output"].splitlines() if line.startswith("G1")]
        self.assertGreater(len(motions), 2)
        self.assertGreater(result["preview"]["segments_subdivided"], 0)
        self.assertLessEqual(result["preview"]["max_approximation_error_mm"], 0.01)

    def test_safe_g0_is_not_subdivided(self) -> None:
        height_map = build_height_map(lambda x_mm, _y: 0.002 * (x_mm - 10.0) ** 2)
        result = generate_adaptive_gcode(
            original_text="G21\nG90\nG0 Z5\nG0 X20 Y0\n",
            height_map=height_map,
            reference_frame=self.reference,
            max_z_error_mm=0.01,
            operation_id="op",
            operation_name="rapid",
            min_segment_length_mm=0.05,
        )
        motions = [line for line in result["output"].splitlines() if line.startswith("G0")]
        self.assertEqual(len(motions), 2)
        self.assertEqual(result["preview"]["segments_subdivided"], 0)

    def test_relative_mode_is_preserved(self) -> None:
        height_map = build_height_map(lambda _x, _y: 0.0)
        result = generate_adaptive_gcode(
            original_text="G21\nG91\nG1 X10 Y0 Z-0.1 F120\nG1 X10 Y0 Z0 F120\n",
            height_map=height_map,
            reference_frame=self.reference,
            max_z_error_mm=0.01,
            operation_id="op",
            operation_name="relative",
            min_segment_length_mm=0.05,
        )
        self.assertIn("\nG91\n", result["output"])
        self.assertNotIn("\nG90\n", result["output"])

    def test_arc_stays_as_arc_when_surface_is_flat(self) -> None:
        height_map = build_height_map(lambda _x, _y: 0.0)
        result = generate_adaptive_gcode(
            original_text="G21\nG90\nG1 X0 Y0 Z-0.1 F120\nG2 X10 Y0 I5 J0 Z-0.1 F120\n",
            height_map=height_map,
            reference_frame=self.reference,
            max_z_error_mm=0.01,
            operation_id="op",
            operation_name="flat-arc",
            min_segment_length_mm=0.05,
        )
        self.assertEqual(sum(1 for line in result["output"].splitlines() if line.startswith("G2 ")), 1)

    def test_arc_subdivides_adaptively_when_curved(self) -> None:
        height_map = build_height_map(lambda x_mm, y_mm: 0.0015 * ((x_mm - 10.0) ** 2 + (y_mm - 10.0) ** 2))
        result = generate_adaptive_gcode(
            original_text="G21\nG90\nG1 X0 Y0 Z-0.1 F120\nG2 X10 Y0 I5 J0 Z-0.1 F120\n",
            height_map=height_map,
            reference_frame=self.reference,
            max_z_error_mm=0.005,
            operation_id="op",
            operation_name="curved-arc",
            min_segment_length_mm=0.05,
        )
        self.assertGreater(sum(1 for line in result["output"].splitlines() if line.startswith("G2 ")), 1)

    def test_unsupported_r_arc_blocks_generation(self) -> None:
        height_map = build_height_map(lambda _x, _y: 0.0)
        with self.assertRaises(ApplicationError):
            generate_adaptive_gcode(
                original_text="G21\nG90\nG1 X0 Y0 Z-0.1 F120\nG2 X10 Y0 R5 Z-0.1 F120\n",
                height_map=height_map,
                reference_frame=self.reference,
                max_z_error_mm=0.01,
                operation_id="op",
                operation_name="r-arc",
                min_segment_length_mm=0.05,
            )

    def test_pure_z_plunge_uses_surface_delta_at_current_xy(self) -> None:
        height_map = build_height_map(lambda x_mm, y_mm: 0.01 * x_mm + 0.001 * y_mm)
        result = generate_adaptive_gcode(
            original_text="G21\nG90\nG0 X10 Y10\nG1 Z-0.1 F120\n",
            height_map=height_map,
            reference_frame=self.reference,
            max_z_error_mm=0.01,
            operation_id="op",
            operation_name="plunge",
            min_segment_length_mm=0.05,
        )
        plunge = next(line for line in result["output"].splitlines() if line.startswith("G1 "))
        self.assertIn("X110", plunge)
        self.assertIn("Y210", plunge)
        self.assertIn("Z10.01", plunge)
        self.assertAlmostEqual(result["preview"]["trace"][-1]["delta_z_mm"], 0.11, places=6)

    def test_xy_cut_after_pure_z_plunge_keeps_compensated_start(self) -> None:
        height_map = build_height_map(lambda x_mm, _y: 0.005 * x_mm)
        result = generate_adaptive_gcode(
            original_text="G21\nG90\nG0 X10 Y0\nG1 Z-0.1 F120\nG1 X20 F120\n",
            height_map=height_map,
            reference_frame=self.reference,
            max_z_error_mm=0.005,
            operation_id="op",
            operation_name="plunge-then-cut",
            min_segment_length_mm=0.05,
        )
        motions = [line for line in result["output"].splitlines() if line.startswith("G1 ")]
        self.assertEqual(len(motions), 2)
        self.assertIn("X120", motions[1])
        self.assertIn("Z10", motions[1])

    def test_retract_to_surface_keeps_compensation_until_crossing(self) -> None:
        height_map = build_height_map(lambda x_mm, _y: 0.005 * x_mm)
        result = generate_adaptive_gcode(
            original_text="G21\nG90\nG0 X10 Y0\nG1 Z-0.1 F120\nG1 Z0 F120\n",
            height_map=height_map,
            reference_frame=self.reference,
            max_z_error_mm=0.01,
            operation_id="op",
            operation_name="retract",
            min_segment_length_mm=0.05,
        )
        motions = [line for line in result["output"].splitlines() if line.startswith("G1 ")]
        self.assertIn("Z10.05", motions[-1])
        self.assertAlmostEqual(result["preview"]["trace"][-1]["delta_z_mm"], 0.05, places=6)

    def test_ramp_crossing_surface_threshold_is_split_at_crossing(self) -> None:
        height_map = build_height_map(lambda x_mm, _y: 0.01 * x_mm)
        result = generate_adaptive_gcode(
            original_text="G21\nG90\nG0 Z1\nG1 X20 Z-0.1 F120\n",
            height_map=height_map,
            reference_frame=self.reference,
            max_z_error_mm=0.01,
            operation_id="op",
            operation_name="ramp",
            min_segment_length_mm=0.05,
        )
        motions = [line for line in result["output"].splitlines() if line.startswith("G1 ")]
        self.assertGreaterEqual(len(motions), 2)
        self.assertTrue(any("X118.18182" in line and "Z10" in line for line in motions))
        crossing_lines = [line for line in motions if "X118.18182" in line]
        self.assertGreaterEqual(len(crossing_lines), 2)

    def test_safe_retraction_finishes_without_surface_compensation(self) -> None:
        height_map = build_height_map(lambda x_mm, _y: 0.005 * x_mm)
        result = generate_adaptive_gcode(
            original_text="G21\nG90\nG0 X10 Y0\nG1 Z-0.1 F120\nG1 Z1 F120\n",
            height_map=height_map,
            reference_frame=self.reference,
            max_z_error_mm=0.01,
            operation_id="op",
            operation_name="safe-retract",
            min_segment_length_mm=0.05,
        )
        self.assertAlmostEqual(result["preview"]["trace"][-1]["delta_z_mm"], 0.0, places=6)

    def test_dwell_line_is_preserved_literally(self) -> None:
        height_map = build_height_map(lambda _x, _y: 0.0)
        result = generate_adaptive_gcode(
            original_text="G21\nG90\nG1 X10 Y0 Z-0.1 F120\nG4 P1000\n",
            height_map=height_map,
            reference_frame=self.reference,
            max_z_error_mm=0.01,
            operation_id="op",
            operation_name="dwell",
            min_segment_length_mm=0.05,
        )
        self.assertIn("G4 P1000", result["output"])

    def test_g92_blocks_adaptive_generation_with_line_detail(self) -> None:
        height_map = build_height_map(lambda _x, _y: 0.0)
        with self.assertRaisesRegex(ApplicationError, "L4:G92"):
            generate_adaptive_gcode(
                original_text="G21\nG90\nG1 X10 Y0 Z-0.1 F120\nG92 X0 Y0\n",
                height_map=height_map,
                reference_frame=self.reference,
                max_z_error_mm=0.01,
                operation_id="op",
                operation_name="g92",
                min_segment_length_mm=0.05,
            )

    def test_auxiliary_commands_are_preserved_literally(self) -> None:
        height_map = build_height_map(lambda _x, _y: 0.0)
        result = generate_adaptive_gcode(
            original_text="G21\nG90\nM3 S1000\nM5\n",
            height_map=height_map,
            reference_frame=self.reference,
            max_z_error_mm=0.01,
            operation_id="op",
            operation_name="aux",
            min_segment_length_mm=0.05,
        )
        self.assertIn("M3 S1000", result["output"])
        self.assertIn("M5", result["output"])

    def test_feed_only_line_is_preserved_without_becoming_motion(self) -> None:
        height_map = build_height_map(lambda _x, _y: 0.0)
        result = generate_adaptive_gcode(
            original_text="G21\nG90\nF300\nG1 X10 Y0 Z-0.1\n",
            height_map=height_map,
            reference_frame=self.reference,
            max_z_error_mm=0.01,
            operation_id="op",
            operation_name="feed-only",
            min_segment_length_mm=0.05,
        )
        self.assertIn("\nF300\n", result["output"])

    def test_g90_1_blocks_arc_generation(self) -> None:
        height_map = build_height_map(lambda _x, _y: 0.0)
        with self.assertRaisesRegex(ApplicationError, "G90.1"):
            generate_adaptive_gcode(
                original_text="G21\nG90\nG90.1\nG1 X0 Y0 Z-0.1 F120\nG2 X10 Y0 I5 J0 Z-0.1 F120\n",
                height_map=height_map,
                reference_frame=self.reference,
                max_z_error_mm=0.01,
                operation_id="op",
                operation_name="arc-center-mode",
                min_segment_length_mm=0.05,
            )

    def test_unknown_macro_line_is_preserved_literally(self) -> None:
        height_map = build_height_map(lambda _x, _y: 0.0)
        result = generate_adaptive_gcode(
            original_text="G21\nG90\nSET_PIN VALUE=1\n",
            height_map=height_map,
            reference_frame=self.reference,
            max_z_error_mm=0.01,
            operation_id="op",
            operation_name="macro",
            min_segment_length_mm=0.05,
        )
        self.assertIn("SET_PIN VALUE=1", result["output"])

    def test_movement_comment_is_preserved(self) -> None:
        height_map = build_height_map(lambda _x, _y: 0.0)
        result = generate_adaptive_gcode(
            original_text="G21\nG90\nG1 X10 Y0 Z-0.1 F120 ; corte principal\n",
            height_map=height_map,
            reference_frame=self.reference,
            max_z_error_mm=0.01,
            operation_id="op",
            operation_name="comment",
            min_segment_length_mm=0.05,
        )
        self.assertIn("; corte principal", result["output"])

    def test_motion_with_side_effect_is_preserved_when_not_subdivided(self) -> None:
        height_map = build_height_map(lambda _x, _y: 0.0)
        result = generate_adaptive_gcode(
            original_text="G21\nG90\nG1 X10 Y0 Z-0.1 F120 M3 S1000\n",
            height_map=height_map,
            reference_frame=self.reference,
            max_z_error_mm=0.01,
            operation_id="op",
            operation_name="side-effect",
            min_segment_length_mm=0.05,
        )
        self.assertIn("M3 S1000", result["output"])

    def test_quarter_points_force_subdivision_even_when_midpoint_matches(self) -> None:
        height_map = build_height_map(lambda x_mm, _y: 0.00002 * x_mm * (x_mm - 10.0) * (x_mm - 20.0))
        result = generate_adaptive_gcode(
            original_text="G21\nG90\nG1 X0 Y0 Z-0.1 F120\nG1 X20 Y0 Z-0.1 F120\n",
            height_map=height_map,
            reference_frame=self.reference,
            max_z_error_mm=0.002,
            operation_id="op",
            operation_name="quarters",
            min_segment_length_mm=0.05,
        )
        self.assertGreater(result["preview"]["segments_subdivided"], 0)
        self.assertLessEqual(result["preview"]["max_approximation_error_mm"], 0.002)

    def test_crossing_multiple_grid_cells_is_revalidated_within_tolerance(self) -> None:
        height_map = build_height_map(lambda x_mm, y_mm: 0.001 * ((x_mm - 10.0) ** 2 + (y_mm - 10.0) ** 2))
        result = generate_adaptive_gcode(
            original_text="G21\nG90\nG1 X0 Y0 Z-0.1 F120\nG1 X20 Y20 Z-0.1 F120\n",
            height_map=height_map,
            reference_frame=self.reference,
            max_z_error_mm=0.01,
            operation_id="op",
            operation_name="grid-crossing",
            min_segment_length_mm=0.05,
        )
        self.assertGreater(result["preview"]["segments_subdivided"], 0)
        self.assertLessEqual(result["preview"]["max_approximation_error_mm"], 0.01)

    def test_impossible_tolerance_due_to_min_segment_length_blocks_generation(self) -> None:
        height_map = build_height_map(lambda x_mm, _y: 0.002 * (x_mm - 10.0) ** 2)
        with self.assertRaisesRegex(ApplicationError, "longitud mínima"):
            generate_adaptive_gcode(
                original_text="G21\nG90\nG1 X0 Y0 Z-0.1 F120\nG1 X20 Y0 Z-0.1 F120\n",
                height_map=height_map,
                reference_frame=self.reference,
                max_z_error_mm=0.0001,
                operation_id="op",
                operation_name="min-length",
                min_segment_length_mm=10.0,
            )

    def test_recursion_limit_reports_real_error(self) -> None:
        height_map = build_height_map(lambda x_mm, _y: 0.002 * (x_mm - 10.0) ** 2)
        with patch.object(adaptive_compensation_module, "MAX_RECURSION_DEPTH", 1):
            with self.assertRaisesRegex(ApplicationError, "profundidad máxima de recursión"):
                generate_adaptive_gcode(
                    original_text="G21\nG90\nG1 X0 Y0 Z-0.1 F120\nG1 X20 Y0 Z-0.1 F120\n",
                    height_map=height_map,
                    reference_frame=self.reference,
                    max_z_error_mm=0.0001,
                    operation_id="op",
                    operation_name="recursion-limit",
                    min_segment_length_mm=0.05,
                )

    def test_global_simplification_preserves_verified_error_bound(self) -> None:
        height_map = build_height_map(lambda x_mm, _y: 0.0012 * (x_mm - 10.0) ** 2)
        result = generate_adaptive_gcode(
            original_text="G21\nG90\nG1 X0 Y0 Z-0.1 F120\nG1 X20 Y0 Z-0.1 F120\n",
            height_map=height_map,
            reference_frame=self.reference,
            max_z_error_mm=0.01,
            operation_id="op",
            operation_name="simplification",
            min_segment_length_mm=0.05,
        )
        self.assertGreaterEqual(result["preview"]["segments_fused"], 0)
        self.assertLessEqual(result["preview"]["max_approximation_error_mm"], 0.01)


class CompensationModePersistenceTests(unittest.TestCase):
    def test_existing_default_is_legacy_and_updates_are_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            repository = JsonProjectRepository(Path(tempdir))
            service = ProjectService(repository)
            project = service.create_project(nombre="P", ancho_mm=20.0, alto_mm=20.0, espesor_mm=1.6, doble_cara=False, eje_volteo=None, agujeros_alineacion=[])
            operation = service.add_operation(project_id=project.id, nombre="Op", tipo="fresado_superior")
            self.assertEqual(str(operation.compensation_mode), "legacy")
            self.assertEqual(operation.max_z_error_mm, 0.05)
            updated = service.update_operation(
                project_id=project.id,
                operation_id=operation.id,
                nombre="Op",
                compensation_mode="adaptive_fast",
                max_z_error_mm=0.02,
            )
            self.assertEqual(str(updated.compensation_mode), "adaptive_fast")
            self.assertEqual(updated.max_z_error_mm, 0.02)
            reloaded = repository.load_project(project.id).get_operation(operation.id)
            self.assertEqual(str(reloaded.compensation_mode), "adaptive_fast")
            self.assertEqual(reloaded.max_z_error_mm, 0.02)
