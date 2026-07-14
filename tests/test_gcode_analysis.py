from __future__ import annotations

import unittest

from klipper_cnc_assistant.domain import MaterialBruto
from klipper_cnc_assistant.gcode import analyze_gcode_text


class GCodeAnalysisTest(unittest.TestCase):
    def test_gcode_within_material(self) -> None:
        analysis = analyze_gcode_text(
            """G21
G90
G1 X10 Y10 F120
""",
            material=MaterialBruto(
                ancho_mm=20.0,
                alto_mm=20.0,
            ),
        )
        self.assertTrue(analysis.cabe_en_material)

    def test_gcode_outside_material_reports_axis_overflow(self) -> None:
        analysis = analyze_gcode_text(
            """G21
G90
G1 X30 Y10 F120
""",
            material=MaterialBruto(
                ancho_mm=20.0,
                alto_mm=20.0,
            ),
        )
        self.assertFalse(analysis.cabe_en_material)
        self.assertEqual(analysis.desbordes_material[0].eje, "X")
        self.assertAlmostEqual(analysis.desbordes_material[0].exceso_mm, 10.0)

    def test_negative_coordinates_are_reported_as_material_overflow(self) -> None:
        analysis = analyze_gcode_text(
            """G21
G90
G1 X-2 Y-1 F120
""",
            material=MaterialBruto(ancho_mm=10.0, alto_mm=10.0),
        )
        self.assertFalse(analysis.cabe_en_material)
        self.assertEqual({item.eje for item in analysis.desbordes_material}, {"X", "Y"})

    def test_units_g20_and_g21_are_normalized_to_mm(self) -> None:
        analysis = analyze_gcode_text(
            """G20
G90
G1 X1.0 Y2.0 F10
""",
        )
        self.assertAlmostEqual(analysis.limites.max_x_mm, 25.4)
        self.assertAlmostEqual(analysis.avances_mm_min[0], 254.0)
        self.assertEqual(set(analysis.unidades_detectadas), {"mm", "inch"})

    def test_positioning_g90_and_g91_are_tracked(self) -> None:
        analysis = analyze_gcode_text(
            """G21
G90
G1 X10
G91
G1 X5 Y2
""",
        )
        self.assertEqual(analysis.limites.max_x_mm, 15.0)
        self.assertEqual(analysis.limites.max_y_mm, 2.0)
        self.assertEqual(set(analysis.modos_posicionamiento), {"absolute", "relative"})

    def test_g94_is_recognized_without_warning(self) -> None:
        analysis = analyze_gcode_text(
            """G21
G94
G1 X5 Y5 F120
"""
        )
        self.assertNotIn("G94", analysis.comandos_desconocidos)
        self.assertEqual(analysis.avances_mm_min, (120.0,))

    def test_detects_g28_as_critical_error(self) -> None:
        analysis = analyze_gcode_text("G28\n")
        self.assertTrue(analysis.tiene_errores_criticos)
        self.assertEqual(analysis.incidencias[0].codigo, "g28")

    def test_detects_g92_as_critical_error(self) -> None:
        analysis = analyze_gcode_text("G92 X0 Y0\n")
        self.assertTrue(analysis.tiene_errores_criticos)
        self.assertEqual(analysis.incidencias[0].codigo, "g92")

    def test_spindle_commands_are_reported(self) -> None:
        analysis = analyze_gcode_text(
            """M3
S12000
M5
"""
        )
        self.assertEqual(list(analysis.acciones_husillo), ["M3", "S12000", "M5"])

    def test_tool_changes_are_reported(self) -> None:
        analysis = analyze_gcode_text(
            """T1
M6
"""
        )
        self.assertEqual(list(analysis.cambios_herramienta), ["T1", "M6"])

    def test_arc_g2_is_represented_geometrically(self) -> None:
        analysis = analyze_gcode_text(
            """G21
G90
G0 X0 Y0
G2 X10 Y0 I5 J0 F120
"""
        )
        self.assertFalse(analysis.analisis_incompleto)
        self.assertEqual(len(analysis.segmentos_vista_previa), 2)
        arc = analysis.segmentos_vista_previa[1]
        self.assertEqual(arc.tipo, "G2")
        self.assertEqual(arc.numero_linea, 4)
        self.assertGreater(len(arc.puntos), 12)
        self.assertAlmostEqual(arc.desde.x_mm, 0.0)
        self.assertAlmostEqual(arc.hasta.x_mm, 10.0)
        self.assertEqual(arc.avance_mm_min, 120.0)

    def test_arc_g3_supports_full_circle(self) -> None:
        analysis = analyze_gcode_text(
            """G21
G90
G0 X5 Y0
G3 I-5 J0 F100
"""
        )
        arc = analysis.segmentos_vista_previa[1]
        self.assertEqual(arc.tipo, "G3")
        self.assertGreater(len(arc.puntos), 12)
        self.assertAlmostEqual(arc.desde.x_mm, arc.hasta.x_mm, places=3)
        self.assertAlmostEqual(arc.desde.y_mm, arc.hasta.y_mm, places=3)

    def test_ambiguous_arc_is_marked_incomplete(self) -> None:
        analysis = analyze_gcode_text(
            """G21
G90
G2 X10 Y0
"""
        )
        self.assertTrue(analysis.analisis_incompleto)
        self.assertIn("G2", analysis.comandos_no_compatibles)
        self.assertEqual(analysis.incidencias[-1].codigo, "arco_no_representable")

    def test_segment_metadata_preserves_line_z_and_distance(self) -> None:
        analysis = analyze_gcode_text(
            """G21
G90
G1 X3 Y4 Z-0.5 F150
"""
        )
        segment = analysis.segmentos_lineales[0]
        self.assertEqual(segment.numero_linea, 3)
        self.assertEqual(segment.z_mm, -0.5)
        self.assertAlmostEqual(segment.distancia_mm, (3**2 + 4**2 + 0.5**2) ** 0.5)
