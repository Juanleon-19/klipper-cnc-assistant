from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

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
