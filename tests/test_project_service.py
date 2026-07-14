from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from klipper_cnc_assistant.application import (
    ProjectService,
)
from klipper_cnc_assistant.storage import (
    JsonProjectRepository,
)


SIMPLE_GCODE = "G21\nG90\nG1 X10 Y5 F120\nG1 Z-0.2\n"


class ProjectServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.base_dir = Path(self.tempdir.name)
        self.service = ProjectService(
            JsonProjectRepository(self.base_dir)
        )

    def test_create_and_load_project(self) -> None:
        project = self.service.create_project(
            nombre="PCB demo",
            ancho_mm=100.0,
            alto_mm=80.0,
            espesor_mm=1.6,
        )

        loaded = self.service.get_project(project.id)

        self.assertEqual(loaded.id, project.id)
        self.assertEqual(loaded.nombre, "PCB demo")
        self.assertEqual(
            loaded.material.ancho_mm,
            100.0,
        )

    def test_add_and_delete_operation(self) -> None:
        project = self.service.create_project(
            nombre="PCB simple",
            ancho_mm=50.0,
            alto_mm=40.0,
        )

        operation = self.service.add_operation(
            project_id=project.id,
            nombre="Aislamiento",
            tipo="aislamiento",
            cara="superior",
            orden=0,
        )
        loaded = self.service.get_project(project.id)

        self.assertEqual(len(loaded.operaciones), 1)
        self.assertEqual(
            loaded.operaciones[0].id,
            operation.id,
        )

        self.service.delete_operation(
            project.id,
            operation.id,
        )
        loaded_after_delete = self.service.get_project(
            project.id
        )
        self.assertEqual(
            len(loaded_after_delete.operaciones),
            0,
        )

    def test_multiple_operations_keep_configurable_order(self) -> None:
        project = self.service.create_project(
            nombre="PCB multiple",
            ancho_mm=60.0,
            alto_mm=60.0,
        )

        self.service.add_operation(
            project_id=project.id,
            nombre="Corte",
            tipo="corte exterior",
            cara="superior",
            orden=2,
        )
        self.service.add_operation(
            project_id=project.id,
            nombre="Taladrado",
            tipo="taladrado",
            cara="superior",
            orden=1,
        )

        loaded = self.service.get_project(project.id)
        self.assertEqual(
            [item.orden for item in loaded.operaciones],
            [1, 2],
        )

    def test_double_side_project_stores_flip_axis_and_holes(self) -> None:
        project = self.service.create_project(
            nombre="PCB doble cara",
            ancho_mm=70.0,
            alto_mm=50.0,
            doble_cara=True,
            eje_volteo="x",
            agujeros_alineacion=[
                {
                    "x_mm": 5.0,
                    "y_mm": 5.0,
                    "diametro_mm": 3.0,
                },
                {
                    "x_mm": 55.0,
                    "y_mm": 5.0,
                    "diametro_mm": 3.0,
                },
            ],
        )

        loaded = self.service.get_project(project.id)
        self.assertTrue(
            loaded.configuracion_alineacion.doble_cara
        )
        self.assertEqual(
            loaded.configuracion_alineacion.eje_volteo,
            "x",
        )
        self.assertEqual(
            len(
                loaded.configuracion_alineacion.agujeros_alineacion
            ),
            2,
        )

    def test_upload_gcode_preserves_original_and_sha256(self) -> None:
        project = self.service.create_project(
            nombre="PCB gcode",
            ancho_mm=100.0,
            alto_mm=100.0,
        )
        operation = self.service.add_operation(
            project_id=project.id,
            nombre="Operacion",
            tipo="personalizada",
            cara="superior",
            orden=0,
        )

        updated = self.service.upload_operation_gcode(
            project_id=project.id,
            operation_id=operation.id,
            filename="job.nc",
            content=SIMPLE_GCODE,
        )
        original_path = (
            self.base_dir
            / "projects"
            / project.id
            / updated.archivo_gcode
        )

        self.assertTrue(original_path.exists())
        self.assertEqual(
            original_path.read_text(encoding="utf-8"),
            SIMPLE_GCODE,
        )
        self.assertEqual(
            updated.sha256,
            hashlib.sha256(
                SIMPLE_GCODE.encode("utf-8")
            ).hexdigest(),
        )

    def test_original_file_remains_immutable_on_second_upload(self) -> None:
        project = self.service.create_project(
            nombre="PCB inmutable",
            ancho_mm=100.0,
            alto_mm=100.0,
        )
        operation = self.service.add_operation(
            project_id=project.id,
            nombre="Operacion",
            tipo="personalizada",
            cara="superior",
            orden=0,
        )
        first = self.service.upload_operation_gcode(
            project_id=project.id,
            operation_id=operation.id,
            filename="job.nc",
            content="G21\nG90\nG1 X1\n",
        )
        first_path = (
            self.base_dir
            / "projects"
            / project.id
            / first.archivo_gcode
        )

        second = self.service.upload_operation_gcode(
            project_id=project.id,
            operation_id=operation.id,
            filename="job.nc",
            content="G21\nG90\nG1 X2\n",
        )
        second_path = (
            self.base_dir
            / "projects"
            / project.id
            / second.archivo_gcode
        )

        self.assertNotEqual(
            first.archivo_gcode,
            second.archivo_gcode,
        )
        self.assertEqual(
            first_path.read_text(encoding="utf-8"),
            "G21\nG90\nG1 X1\n",
        )
        self.assertEqual(
            second_path.read_text(encoding="utf-8"),
            "G21\nG90\nG1 X2\n",
        )

    def test_analyze_operation_uses_project_material(self) -> None:
        project = self.service.create_project(
            nombre="PCB analisis",
            ancho_mm=20.0,
            alto_mm=20.0,
        )
        operation = self.service.add_operation(
            project_id=project.id,
            nombre="Aislamiento",
            tipo="aislamiento",
            cara="superior",
            orden=0,
        )
        self.service.upload_operation_gcode(
            project_id=project.id,
            operation_id=operation.id,
            filename="fit.nc",
            content=SIMPLE_GCODE,
        )

        analyzed = self.service.analyze_operation(
            project.id,
            operation.id,
        )

        self.assertTrue(
            analyzed.analisis.cabe_en_material
        )
        self.assertEqual(
            analyzed.analisis.cantidad_movimientos,
            2,
        )
