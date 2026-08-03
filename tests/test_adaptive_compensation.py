from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import klipper_cnc_assistant.application.adaptive_compensation as adaptive_compensation_module
from klipper_cnc_assistant.application.adaptive_compensation import ReferenceFrame, generate_adaptive_gcode
from klipper_cnc_assistant.application.errors import ApplicationError
from klipper_cnc_assistant.application.services import ProjectService
from klipper_cnc_assistant.gcode.tokenizer import tokenize_gcode
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


def emitted_motion_records(output: str) -> list[dict[str, object]]:
    positioning = "absolute"
    active_motion = None
    machine_x = 0.0
    machine_y = 0.0
    machine_z = 0.0
    records: list[dict[str, object]] = []
    for line in tokenize_gcode(output):
        if not line.tokens:
            continue
        axes: dict[str, float] = {}
        ij: dict[str, float] = {}
        for token in line.tokens:
            if token.letter == "G":
                command = adaptive_compensation_module._normalize_g_command(token.raw_value)
                if command in {"G0", "G1", "G2", "G3"}:
                    active_motion = command
                elif command == "G90":
                    positioning = "absolute"
                elif command == "G91":
                    positioning = "relative"
            elif token.letter in {"X", "Y", "Z"} and token.raw_value is not None:
                axes[token.letter] = float(token.raw_value)
            elif token.letter in {"I", "J"} and token.raw_value is not None:
                ij[token.letter] = float(token.raw_value)
        if active_motion not in {"G0", "G1", "G2", "G3"} or (not axes and not ij):
            continue
        start = (machine_x, machine_y, machine_z)
        if positioning == "absolute":
            machine_x = axes.get("X", machine_x)
            machine_y = axes.get("Y", machine_y)
            machine_z = axes.get("Z", machine_z)
        else:
            machine_x += axes.get("X", 0.0)
            machine_y += axes.get("Y", 0.0)
            machine_z += axes.get("Z", 0.0)
        end = (machine_x, machine_y, machine_z)
        center = None
        if active_motion in {"G2", "G3"}:
            center = (start[0] + ij.get("I", 0.0), start[1] + ij.get("J", 0.0))
        records.append(
            {
                "command": active_motion,
                "line_number": line.line_number,
                "raw": line.raw,
                "start": start,
                "end": end,
                "center": center,
                "i": ij.get("I"),
                "j": ij.get("J"),
            }
        )
    return records


def arc_program(
    *,
    command: str,
    center_x_mm: float,
    center_y_mm: float,
    radius_mm: float,
    start_angle_deg: float,
    sweep_deg: float,
    start_z_mm: float = -0.1,
    end_z_mm: float | None = None,
) -> tuple[str, dict[str, float]]:
    start_angle = math.radians(start_angle_deg)
    sweep_angle = math.radians(sweep_deg)
    end_angle = start_angle + sweep_angle
    start_x = center_x_mm + radius_mm * math.cos(start_angle)
    start_y = center_y_mm + radius_mm * math.sin(start_angle)
    end_x = start_x if math.isclose(abs(sweep_deg), 360.0, abs_tol=1e-9) else center_x_mm + radius_mm * math.cos(end_angle)
    end_y = start_y if math.isclose(abs(sweep_deg), 360.0, abs_tol=1e-9) else center_y_mm + radius_mm * math.sin(end_angle)
    target_z = start_z_mm if end_z_mm is None else end_z_mm
    text = (
        "G21\n"
        "G90\n"
        f"G1 X{start_x:.5f} Y{start_y:.5f} Z{start_z_mm:.5f} F120\n"
        f"{command} X{end_x:.5f} Y{end_y:.5f} I{center_x_mm - start_x:.5f} J{center_y_mm - start_y:.5f} Z{target_z:.5f} F120\n"
    )
    return text, {
        "start_x_mm": start_x,
        "start_y_mm": start_y,
        "end_x_mm": end_x,
        "end_y_mm": end_y,
        "center_x_mm": center_x_mm,
        "center_y_mm": center_y_mm,
        "radius_mm": radius_mm,
        "sweep_deg": sweep_deg,
        "target_z_mm": target_z,
    }


def signed_arc_sweep_deg(start: tuple[float, float, float], end: tuple[float, float, float], center: tuple[float, float], command: str) -> float:
    start_angle = math.atan2(start[1] - center[1], start[0] - center[0])
    end_angle = math.atan2(end[1] - center[1], end[0] - center[0])
    sweep = math.degrees(end_angle - start_angle)
    if command == "G2" and sweep >= 0.0:
        sweep -= 360.0
    if command == "G3" and sweep <= 0.0:
        sweep += 360.0
    return sweep


class AdaptiveCompensationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.reference = ReferenceFrame(
            machine_origin_x_mm=100.0,
            machine_origin_y_mm=200.0,
            surface_reference_z_mm=10.0,
        )
        self.default_safe_rapid_z_mm = 0.3
        self.default_rapid_clearance_margin_mm = 0.05

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
            configured_safe_z_mm=self.default_safe_rapid_z_mm,
        )
        motions = [line for line in result["output"].splitlines() if line.startswith("G0")]
        self.assertEqual(len(motions), 2)
        self.assertEqual(result["preview"]["segments_subdivided"], 0)

    def test_g0_vertical_retract_from_cut_to_safe_is_allowed(self) -> None:
        height_map = build_height_map(lambda x_mm, _y: 0.005 * x_mm)
        result = generate_adaptive_gcode(
            original_text="G21\nG90\nG1 X10 Y0 Z-0.1 F120\nG0 Z1\n",
            height_map=height_map,
            reference_frame=self.reference,
            max_z_error_mm=0.01,
            operation_id="op",
            operation_name="g0-retract",
            min_segment_length_mm=0.05,
            configured_safe_z_mm=self.default_safe_rapid_z_mm,
        )
        rapid = [record for record in emitted_motion_records(result["output"]) if record["command"] == "G0"][-1]
        self.assertAlmostEqual(float(rapid["end"][2]), 11.0, places=6)

    def test_g0_xy_below_surface_is_blocked_with_line_and_reason(self) -> None:
        height_map = build_height_map(lambda _x, _y: 0.0)
        with self.assertRaisesRegex(ApplicationError, r"L4: G0 bloqueado: G0 XY bajo Z=0 no permitido"):
            generate_adaptive_gcode(
                original_text="G21\nG90\nG1 X0 Y0 Z-0.1 F120\nG0 X20 Y0\n",
                height_map=height_map,
                reference_frame=self.reference,
                max_z_error_mm=0.01,
                operation_id="op",
                operation_name="g0-xy-below",
                min_segment_length_mm=0.05,
                configured_safe_z_mm=self.default_safe_rapid_z_mm,
            )

    def test_g0_plunge_toward_cut_is_blocked_with_line_and_reason(self) -> None:
        height_map = build_height_map(lambda _x, _y: 0.0)
        with self.assertRaisesRegex(ApplicationError, r"L3: G0 bloqueado: G0 plunge rápido no permitido"):
            generate_adaptive_gcode(
                original_text="G21\nG90\nG0 Z-0.1\n",
                height_map=height_map,
                reference_frame=self.reference,
                max_z_error_mm=0.01,
                operation_id="op",
                operation_name="g0-plunge",
                min_segment_length_mm=0.05,
            )

    def test_g0_diagonal_crossing_surface_is_blocked_with_line_and_reason(self) -> None:
        height_map = build_height_map(lambda _x, _y: 0.0)
        with self.assertRaisesRegex(ApplicationError, r"L3: G0 bloqueado: G0 diagonal que entra o cruza por debajo del clearance"):
            generate_adaptive_gcode(
                original_text="G21\nG90\nG0 X20 Y0 Z-0.1\n",
                height_map=height_map,
                reference_frame=self.reference,
                max_z_error_mm=0.01,
                operation_id="op",
                operation_name="g0-diagonal",
                min_segment_length_mm=0.05,
                configured_safe_z_mm=self.default_safe_rapid_z_mm,
            )

    def test_g0_xy_at_surface_is_blocked_when_map_requires_clearance(self) -> None:
        height_map = build_height_map(lambda _x, _y: 0.20)
        with self.assertRaisesRegex(ApplicationError, r"L3: G0 bloqueado: G0 XY en Z=0 no permitido"):
            generate_adaptive_gcode(
                original_text="G21\nG90\nG0 X20 Y0 Z0\n",
                height_map=height_map,
                reference_frame=self.reference,
                max_z_error_mm=0.01,
                operation_id="op",
                operation_name="g0-z0",
                min_segment_length_mm=0.05,
                configured_safe_z_mm=0.10,
                rapid_clearance_margin_mm=self.default_rapid_clearance_margin_mm,
            )

    def test_g0_xy_with_positive_z_below_required_clearance_is_blocked(self) -> None:
        height_map = build_height_map(lambda _x, _y: 0.20)
        with self.assertRaisesRegex(ApplicationError, r"L5: G0 bloqueado: G0 XY por debajo del clearance requerido"):
            generate_adaptive_gcode(
                original_text="G21\nG90\nG0 Z0.3\nG1 Z0.1 F120\nG0 X20 Y0\n",
                height_map=height_map,
                reference_frame=self.reference,
                max_z_error_mm=0.01,
                operation_id="op",
                operation_name="g0-z0.1",
                min_segment_length_mm=0.05,
                configured_safe_z_mm=0.10,
                rapid_clearance_margin_mm=self.default_rapid_clearance_margin_mm,
            )

    def test_g0_xy_exactly_at_required_clearance_is_allowed(self) -> None:
        height_map = build_height_map(lambda _x, _y: 0.20)
        result = generate_adaptive_gcode(
            original_text="G21\nG90\nG0 Z0.25\nG0 X20 Y0\n",
            height_map=height_map,
            reference_frame=self.reference,
            max_z_error_mm=0.01,
            operation_id="op",
            operation_name="g0-exact-clearance",
            min_segment_length_mm=0.05,
            configured_safe_z_mm=0.10,
            rapid_clearance_margin_mm=self.default_rapid_clearance_margin_mm,
        )
        self.assertTrue(result["preview"]["rapid_clearance_verified"])
        self.assertAlmostEqual(result["preview"]["required_rapid_z_mm"], 0.25, places=6)
        self.assertAlmostEqual(result["preview"]["minimum_programmed_g0_z_mm"], 0.0, places=6)

    def test_g0_xy_above_required_clearance_is_allowed(self) -> None:
        height_map = build_height_map(lambda _x, _y: 0.20)
        result = generate_adaptive_gcode(
            original_text="G21\nG90\nG0 Z0.3\nG0 X20 Y0\n",
            height_map=height_map,
            reference_frame=self.reference,
            max_z_error_mm=0.01,
            operation_id="op",
            operation_name="g0-above-clearance",
            min_segment_length_mm=0.05,
            configured_safe_z_mm=0.10,
            rapid_clearance_margin_mm=self.default_rapid_clearance_margin_mm,
        )
        rapid = [record for record in emitted_motion_records(result["output"]) if record["command"] == "G0"][-1]
        self.assertAlmostEqual(float(rapid["end"][2]), 10.3, places=6)

    def test_g0_vertical_retract_only_to_zero_is_blocked_when_required_clearance_is_higher(self) -> None:
        height_map = build_height_map(lambda _x, _y: 0.20)
        with self.assertRaisesRegex(ApplicationError, r"L4: G0 bloqueado: retracción vertical insuficiente para alcanzar la altura segura"):
            generate_adaptive_gcode(
                original_text="G21\nG90\nG1 X10 Y0 Z-0.1 F120\nG0 Z0\n",
                height_map=height_map,
                reference_frame=self.reference,
                max_z_error_mm=0.01,
                operation_id="op",
                operation_name="g0-zero-retract",
                min_segment_length_mm=0.05,
                configured_safe_z_mm=0.10,
                rapid_clearance_margin_mm=self.default_rapid_clearance_margin_mm,
            )

    def test_g0_diagonal_above_full_clearance_is_blocked_by_policy(self) -> None:
        height_map = build_height_map(lambda _x, _y: 0.20)
        with self.assertRaisesRegex(
            ApplicationError,
            r"L4: G0 bloqueado: G0 diagonal rápida no permitida; solo se permite XY seguro o retracción vertical",
        ):
            generate_adaptive_gcode(
                original_text="G21\nG90\nG0 Z0.3\nG0 X20 Y0 Z0.35\n",
                height_map=height_map,
                reference_frame=self.reference,
                max_z_error_mm=0.01,
                operation_id="op",
                operation_name="g0-diagonal-above",
                min_segment_length_mm=0.05,
                configured_safe_z_mm=0.10,
                rapid_clearance_margin_mm=self.default_rapid_clearance_margin_mm,
            )

    def test_g0_diagonal_crossing_required_clearance_is_blocked(self) -> None:
        height_map = build_height_map(lambda _x, _y: 0.20)
        with self.assertRaisesRegex(ApplicationError, r"L4: G0 bloqueado: G0 diagonal que entra o cruza por debajo del clearance"):
            generate_adaptive_gcode(
                original_text="G21\nG90\nG0 Z0.3\nG0 X20 Y0 Z0.2\n",
                height_map=height_map,
                reference_frame=self.reference,
                max_z_error_mm=0.01,
                operation_id="op",
                operation_name="g0-diagonal-crossing-clearance",
                min_segment_length_mm=0.05,
                configured_safe_z_mm=0.10,
                rapid_clearance_margin_mm=self.default_rapid_clearance_margin_mm,
            )

    def test_missing_safe_z_marks_preview_non_executable_in_permissive_mode(self) -> None:
        height_map = build_height_map(lambda _x, _y: 0.20)
        result = generate_adaptive_gcode(
            original_text="G21\nG90\nG0 X20 Y0 Z0.3\n",
            height_map=height_map,
            reference_frame=self.reference,
            max_z_error_mm=0.01,
            operation_id="op",
            operation_name="g0-missing-safe-z",
            min_segment_length_mm=0.05,
            enforce_rapid_clearance=False,
            rapid_clearance_margin_mm=self.default_rapid_clearance_margin_mm,
        )
        self.assertFalse(result["preview"]["rapid_clearance_verified"])
        self.assertIn("falta una altura segura de desplazamiento configurada", str(result["preview"]["rapid_clearance_block_reason"]))
        self.assertAlmostEqual(result["preview"]["required_rapid_z_mm"], 0.25, places=6)

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
            configured_safe_z_mm=self.default_safe_rapid_z_mm,
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

    def test_arc_geometry_preserves_center_radius_direction_and_ij(self) -> None:
        height_map = build_height_map(lambda _x, _y: 0.0)
        cases = (
            ("G3", 60.0, 0.0, None),
            ("G2", -90.0, 45.0, None),
            ("G3", 180.0, 90.0, -0.2),
            ("G3", 240.0, 180.0, None),
            ("G2", -360.0, 270.0, None),
        )
        for command, sweep_deg, start_angle_deg, end_z_mm in cases:
            with self.subTest(command=command, sweep_deg=sweep_deg, start_angle_deg=start_angle_deg, end_z_mm=end_z_mm):
                original_text, expected = arc_program(
                    command=command,
                    center_x_mm=10.0,
                    center_y_mm=10.0,
                    radius_mm=5.0,
                    start_angle_deg=start_angle_deg,
                    sweep_deg=sweep_deg,
                    start_z_mm=-0.1,
                    end_z_mm=end_z_mm,
                )
                result = generate_adaptive_gcode(
                    original_text=original_text,
                    height_map=height_map,
                    reference_frame=self.reference,
                    max_z_error_mm=0.01,
                    operation_id="op",
                    operation_name="arc-geometry",
                    min_segment_length_mm=0.05,
                )
                arc_moves = [record for record in emitted_motion_records(result["output"]) if record["command"] in {"G2", "G3"}]
                self.assertEqual(len(arc_moves), 1)
                arc_move = arc_moves[0]
                expected_center = (
                    self.reference.machine_origin_x_mm + expected["center_x_mm"],
                    self.reference.machine_origin_y_mm + expected["center_y_mm"],
                )
                self.assertIsNotNone(arc_move["center"])
                self.assertAlmostEqual(float(arc_move["center"][0]), expected_center[0], places=4)
                self.assertAlmostEqual(float(arc_move["center"][1]), expected_center[1], places=4)
                radius_start = math.dist(arc_move["start"][:2], arc_move["center"])
                radius_end = math.dist(arc_move["end"][:2], arc_move["center"])
                self.assertAlmostEqual(radius_start, expected["radius_mm"], places=4)
                self.assertAlmostEqual(radius_end, expected["radius_mm"], places=4)
                self.assertAlmostEqual(float(arc_move["i"]), expected["center_x_mm"] - expected["start_x_mm"], places=4)
                self.assertAlmostEqual(float(arc_move["j"]), expected["center_y_mm"] - expected["start_y_mm"], places=4)
                if abs(sweep_deg) < 359.999:
                    self.assertAlmostEqual(
                        signed_arc_sweep_deg(arc_move["start"], arc_move["end"], arc_move["center"], command),
                        sweep_deg,
                        places=3,
                    )
                else:
                    self.assertAlmostEqual(float(arc_move["start"][0]), float(arc_move["end"][0]), places=4)
                    self.assertAlmostEqual(float(arc_move["start"][1]), float(arc_move["end"][1]), places=4)

    def test_subdivided_arc_preserves_original_center_radius_direction_and_continuity(self) -> None:
        height_map = build_height_map(lambda x_mm, y_mm: 0.0015 * ((x_mm - 10.0) ** 2 + (y_mm - 10.0) ** 2))
        original_text, expected = arc_program(
            command="G3",
            center_x_mm=10.0,
            center_y_mm=10.0,
            radius_mm=5.0,
            start_angle_deg=0.0,
            sweep_deg=240.0,
            start_z_mm=-0.1,
            end_z_mm=-0.2,
        )
        result = generate_adaptive_gcode(
            original_text=original_text,
            height_map=height_map,
            reference_frame=self.reference,
            max_z_error_mm=0.003,
            operation_id="op",
            operation_name="subdivided-arc",
            min_segment_length_mm=0.05,
        )
        arc_moves = [record for record in emitted_motion_records(result["output"]) if record["command"] == "G3"]
        self.assertGreater(len(arc_moves), 1)
        expected_center = (
            self.reference.machine_origin_x_mm + expected["center_x_mm"],
            self.reference.machine_origin_y_mm + expected["center_y_mm"],
        )
        for index, move in enumerate(arc_moves):
            self.assertAlmostEqual(float(move["center"][0]), expected_center[0], places=4)
            self.assertAlmostEqual(float(move["center"][1]), expected_center[1], places=4)
            self.assertAlmostEqual(math.dist(move["start"][:2], move["center"]), expected["radius_mm"], places=4)
            self.assertAlmostEqual(math.dist(move["end"][:2], move["center"]), expected["radius_mm"], places=4)
            if index > 0:
                previous = arc_moves[index - 1]
                self.assertAlmostEqual(float(previous["end"][0]), float(move["start"][0]), places=5)
                self.assertAlmostEqual(float(previous["end"][1]), float(move["start"][1]), places=5)
                self.assertAlmostEqual(float(previous["end"][2]), float(move["start"][2]), places=5)

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
            original_text="G21\nG90\nG0 Z0.3\nG0 X10 Y10\nG1 Z-0.1 F120\n",
            height_map=height_map,
            reference_frame=self.reference,
            max_z_error_mm=0.01,
            operation_id="op",
            operation_name="plunge",
            min_segment_length_mm=0.05,
            configured_safe_z_mm=self.default_safe_rapid_z_mm,
        )
        motions = [line for line in result["output"].splitlines() if line.startswith("G1 ")]
        self.assertEqual(len(motions), 2)
        self.assertIn("X110", motions[-1])
        self.assertIn("Y210", motions[-1])
        self.assertIn("Z10.01", motions[-1])
        self.assertAlmostEqual(result["preview"]["trace"][-1]["delta_z_mm"], 0.11, places=6)

    def test_relative_plunge_uses_last_emitted_machine_position(self) -> None:
        height_map = build_height_map(lambda x_mm, y_mm: 0.01 * x_mm + 0.001 * y_mm)
        result = generate_adaptive_gcode(
            original_text="G21\nG91\nG0 Z0.3\nG0 X10 Y10\nG1 Z-0.4 F120\n",
            height_map=height_map,
            reference_frame=self.reference,
            max_z_error_mm=0.01,
            operation_id="op",
            operation_name="relative-plunge",
            min_segment_length_mm=0.05,
            configured_safe_z_mm=self.default_safe_rapid_z_mm,
        )
        motions = [record for record in emitted_motion_records(result["output"]) if record["command"] == "G1"]
        self.assertEqual(len(motions), 2)
        self.assertAlmostEqual(float(motions[0]["start"][2]), 0.3, places=6)
        self.assertAlmostEqual(float(motions[0]["end"][2]), 0.0, places=6)
        self.assertAlmostEqual(float(motions[0]["end"][2]), float(motions[1]["start"][2]), places=6)
        self.assertAlmostEqual(float(motions[1]["end"][2]), 0.01, places=6)
        self.assertIn("Z0.01", str(motions[-1]["raw"]))

    def test_xy_cut_after_pure_z_plunge_keeps_compensated_start(self) -> None:
        height_map = build_height_map(lambda x_mm, _y: 0.005 * x_mm)
        result = generate_adaptive_gcode(
            original_text="G21\nG90\nG0 Z0.3\nG0 X10 Y0\nG1 Z-0.1 F120\nG1 X20 F120\n",
            height_map=height_map,
            reference_frame=self.reference,
            max_z_error_mm=0.005,
            operation_id="op",
            operation_name="plunge-then-cut",
            min_segment_length_mm=0.05,
            configured_safe_z_mm=self.default_safe_rapid_z_mm,
        )
        motions = [line for line in result["output"].splitlines() if line.startswith("G1 ")]
        self.assertEqual(len(motions), 3)
        records = [record for record in emitted_motion_records(result["output"]) if record["command"] == "G1"]
        self.assertAlmostEqual(float(records[1]["end"][0]), float(records[2]["start"][0]), places=6)
        self.assertAlmostEqual(float(records[1]["end"][1]), float(records[2]["start"][1]), places=6)
        self.assertAlmostEqual(float(records[1]["end"][2]), float(records[2]["start"][2]), places=6)
        self.assertIn("X120", motions[-1])
        self.assertIn("Z10", motions[-1])

    def test_retract_to_surface_keeps_compensation_until_crossing(self) -> None:
        height_map = build_height_map(lambda x_mm, _y: 0.005 * x_mm)
        result = generate_adaptive_gcode(
            original_text="G21\nG90\nG0 Z0.3\nG0 X10 Y0\nG1 Z-0.1 F120\nG1 Z0 F120\n",
            height_map=height_map,
            reference_frame=self.reference,
            max_z_error_mm=0.01,
            operation_id="op",
            operation_name="retract",
            min_segment_length_mm=0.05,
            configured_safe_z_mm=self.default_safe_rapid_z_mm,
        )
        motions = [line for line in result["output"].splitlines() if line.startswith("G1 ")]
        self.assertIn("Z10.05", motions[-1])
        self.assertAlmostEqual(result["preview"]["trace"][-1]["delta_z_mm"], 0.05, places=6)

    def test_relative_retract_uses_last_emitted_machine_position(self) -> None:
        height_map = build_height_map(lambda x_mm, _y: 0.005 * x_mm)
        result = generate_adaptive_gcode(
            original_text="G21\nG91\nG0 Z0.3\nG0 X10 Y0\nG1 Z-0.4 F120\nG1 Z0.1 F120\n",
            height_map=height_map,
            reference_frame=self.reference,
            max_z_error_mm=0.01,
            operation_id="op",
            operation_name="relative-retract",
            min_segment_length_mm=0.05,
            configured_safe_z_mm=self.default_safe_rapid_z_mm,
        )
        motions = [record for record in emitted_motion_records(result["output"]) if record["command"] == "G1"]
        self.assertEqual(len(motions), 3)
        self.assertAlmostEqual(float(motions[0]["end"][2]), float(motions[1]["start"][2]), places=6)
        self.assertAlmostEqual(float(motions[1]["end"][2]), float(motions[2]["start"][2]), places=6)
        self.assertIn("Z0.1", str(motions[-1]["raw"]))

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
            configured_safe_z_mm=self.default_safe_rapid_z_mm,
        )
        motions = [record for record in emitted_motion_records(result["output"]) if record["command"] == "G1"]
        self.assertGreaterEqual(len(motions), 2)
        seen_xy_progress: dict[tuple[float, float], float] = {}
        for point in result["preview"]["trace"]:
            key = (round(float(point["pcb_x_mm"]), 5), round(float(point["pcb_y_mm"]), 5))
            if key in seen_xy_progress:
                self.assertAlmostEqual(seen_xy_progress[key], float(point["final_z_mm"]), places=5)
            else:
                seen_xy_progress[key] = float(point["final_z_mm"])

    def test_safe_retraction_finishes_without_surface_compensation(self) -> None:
        height_map = build_height_map(lambda x_mm, _y: 0.005 * x_mm)
        result = generate_adaptive_gcode(
            original_text="G21\nG90\nG0 Z0.3\nG0 X10 Y0\nG1 Z-0.1 F120\nG1 Z1 F120\n",
            height_map=height_map,
            reference_frame=self.reference,
            max_z_error_mm=0.01,
            operation_id="op",
            operation_name="safe-retract",
            min_segment_length_mm=0.05,
            configured_safe_z_mm=self.default_safe_rapid_z_mm,
        )
        self.assertAlmostEqual(result["preview"]["trace"][-1]["delta_z_mm"], 0.0, places=6)

    def test_global_motion_continuity_uses_previous_real_endpoint(self) -> None:
        height_map = build_height_map(lambda x_mm, y_mm: 0.01 * x_mm + 0.001 * y_mm)
        result = generate_adaptive_gcode(
            original_text="G21\nG91\nG0 Z0.3\nG0 X10 Y10\nG1 Z-0.4 F120\nG1 X10 Z0.05 F120\nG1 Z0.05 F120\n",
            height_map=height_map,
            reference_frame=self.reference,
            max_z_error_mm=0.01,
            operation_id="op",
            operation_name="continuity",
            min_segment_length_mm=0.05,
            configured_safe_z_mm=self.default_safe_rapid_z_mm,
        )
        motions = emitted_motion_records(result["output"])
        for previous, current in zip(motions, motions[1:]):
            self.assertAlmostEqual(float(previous["end"][0]), float(current["start"][0]), places=6)
            self.assertAlmostEqual(float(previous["end"][1]), float(current["start"][1]), places=6)
            self.assertAlmostEqual(float(previous["end"][2]), float(current["start"][2]), places=6)

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

    def test_cell_interval_sampling_detects_adversarial_peak_between_global_quarters(self) -> None:
        height_map = build_height_map(lambda x_mm, y_mm: 0.0008 * x_mm * y_mm)
        result = generate_adaptive_gcode(
            original_text="G21\nG90\nG1 X0 Y2.5 Z-0.1 F120\nG1 X20 Y17.5 Z-0.1 F120\n",
            height_map=height_map,
            reference_frame=self.reference,
            max_z_error_mm=0.004,
            operation_id="op",
            operation_name="adversarial-line",
            min_segment_length_mm=0.05,
        )
        self.assertGreater(result["preview"]["segments_subdivided"], 0)
        self.assertLessEqual(result["preview"]["max_approximation_error_mm"], 0.004)

    def test_arc_cell_interval_sampling_handles_adversarial_surface(self) -> None:
        height_map = build_height_map(lambda x_mm, y_mm: 0.0012 * x_mm * y_mm)
        original_text, _expected = arc_program(
            command="G2",
            center_x_mm=10.0,
            center_y_mm=10.0,
            radius_mm=5.0,
            start_angle_deg=180.0,
            sweep_deg=-270.0,
            start_z_mm=-0.1,
            end_z_mm=-0.2,
        )
        result = generate_adaptive_gcode(
            original_text=original_text,
            height_map=height_map,
            reference_frame=self.reference,
            max_z_error_mm=0.004,
            operation_id="op",
            operation_name="adversarial-arc",
            min_segment_length_mm=0.05,
        )
        arc_moves = [record for record in emitted_motion_records(result["output"]) if record["command"] == "G2"]
        self.assertGreater(len(arc_moves), 1)
        self.assertLessEqual(result["preview"]["max_approximation_error_mm"], 0.004)

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

    def test_arc_impossible_tolerance_due_to_min_segment_length_blocks_generation(self) -> None:
        height_map = build_height_map(lambda x_mm, y_mm: 0.0015 * ((x_mm - 10.0) ** 2 + (y_mm - 10.0) ** 2))
        original_text, _expected = arc_program(
            command="G3",
            center_x_mm=10.0,
            center_y_mm=10.0,
            radius_mm=5.0,
            start_angle_deg=0.0,
            sweep_deg=240.0,
            start_z_mm=-0.1,
        )
        with self.assertRaisesRegex(ApplicationError, "longitud mínima"):
            generate_adaptive_gcode(
                original_text=original_text,
                height_map=height_map,
                reference_frame=self.reference,
                max_z_error_mm=0.0001,
                operation_id="op",
                operation_name="arc-min-length",
                min_segment_length_mm=50.0,
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
