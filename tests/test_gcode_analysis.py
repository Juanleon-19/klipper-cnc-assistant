from __future__ import annotations

import unittest

from klipper_cnc_assistant.domain import (
    MaterialBruto,
)
from klipper_cnc_assistant.gcode import (
    analyze_gcode_text,
)


class GCodeAnalysisTest(unittest.TestCase):
    def test_gcode_within_material(self) -> None:
        analysis = analyze_gcode_text(
            "G21\nG90\nG1 X10 Y10 F120\n",
            material=MaterialBruto(
                ancho_mm=20.0,
                alto_mm=20.0,
            ),
        )
        self.assertTrue(analysis.cabe_en_material)

    def test_gcode_outside_material(self) -> None:
        analysis = analyze_gcode_text(
            "G21\nG90\nG1 X30 Y10 F120\n",
            material=MaterialBruto(
                ancho_mm=20.0,
                alto_mm=20.0,
            ),
        )
        self.assertFalse(analysis.cabe_en_material)

    def test_units_g20_and_g21_are_normalized_to_mm(self) -> None:
        analysis = analyze_gcode_text(
            "G20\nG90\nG1 X1.0 Y2.0 F10\n",
        )
        self.assertAlmostEqual(
            analysis.limites.max_x_mm,
            25.4,
        )
        self.assertAlmostEqual(
            analysis.avances_mm_min[0],
            254.0,
        )
        self.assertEqual(
            set(analysis.unidades_detectadas),
            {"mm", "inch"},
        )

    def test_positioning_g90_and_g91_are_tracked(self) -> None:
        analysis = analyze_gcode_text(
            "G21\nG90\nG1 X10\nG91\nG1 X5 Y2\n",
        )
        self.assertEqual(
            analysis.limites.max_x_mm,
            15.0,
        )
        self.assertEqual(
            analysis.limites.max_y_mm,
            2.0,
        )
        self.assertEqual(
            set(analysis.modos_posicionamiento),
            {"absolute", "relative"},
        )

    def test_detects_g28_as_critical_error(self) -> None:
        analysis = analyze_gcode_text("G28\n")
        self.assertTrue(
            analysis.tiene_errores_criticos
        )
        self.assertEqual(
            analysis.incidencias[0].codigo,
            "g28",
        )

    def test_detects_g92_as_critical_error(self) -> None:
        analysis = analyze_gcode_text("G92 X0 Y0\n")
        self.assertTrue(
            analysis.tiene_errores_criticos
        )
        self.assertEqual(
            analysis.incidencias[0].codigo,
            "g92",
        )

    def test_spindle_commands_are_reported(self) -> None:
        analysis = analyze_gcode_text(
            "M3\nS12000\nM5\n"
        )
        self.assertEqual(
            list(analysis.acciones_husillo),
            ["M3", "S12000", "M5"],
        )

    def test_tool_changes_are_reported(self) -> None:
        analysis = analyze_gcode_text(
            "T1\nM6\n"
        )
        self.assertEqual(
            list(analysis.cambios_herramienta),
            ["T1", "M6"],
        )

    def test_unsupported_arc_command_marks_analysis_incomplete(self) -> None:
        analysis = analyze_gcode_text(
            "G21\nG90\nG2 X10 Y0\n"
        )
        self.assertTrue(
            analysis.analisis_incompleto
        )
        self.assertIn(
            "G2",
            analysis.comandos_no_compatibles,
        )
