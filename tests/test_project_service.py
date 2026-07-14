from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from klipper_cnc_assistant.application import (
    HeightMapService,
    ProjectService,
)
from klipper_cnc_assistant.heightmap import ProbeRegion
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


    def test_repeated_drilling_operations_keep_tools_and_order(self) -> None:
        project = self.service.create_project(
            nombre="Taladrados", ancho_mm=50.0, alto_mm=40.0
        )
        setup_id = project.montajes[0].id
        tools = ("Broca 0,8 mm", "Broca 1,0 mm", "Broca 3,0 mm")
        for tool in tools:
            self.service.add_operation(
                project_id=project.id,
                setup_id=setup_id,
                nombre=f"Taladrado {tool.split()[-2]}",
                tipo="taladrado",
                herramienta=tool,
            )

        loaded = self.service.get_project(project.id)
        operations = loaded.operations_for_setup(setup_id)
        self.assertEqual(len(operations), 3)
        self.assertEqual(tuple(item.orden for item in operations), (0, 1, 2))
        self.assertEqual(tuple(item.herramienta for item in operations), tools)

    def test_move_operation_changes_only_setup_order(self) -> None:
        project = self.service.create_project(
            nombre="Orden", ancho_mm=50.0, alto_mm=40.0
        )
        setup_id = project.montajes[0].id
        operations = [
            self.service.add_operation(
                project_id=project.id,
                setup_id=setup_id,
                nombre=name,
                tipo="taladrado",
            )
            for name in ("A", "B", "C")
        ]

        self.service.move_operation(
            project_id=project.id, operation_id=operations[2].id, direction="up"
        )

        loaded = self.service.get_project(project.id)
        self.assertEqual(
            tuple(item.nombre for item in loaded.operations_for_setup(setup_id)),
            ("A", "C", "B"),
        )

    def test_files_and_analyses_are_independent_per_operation(self) -> None:
        project = self.service.create_project(
            nombre="Trayectorias", ancho_mm=50.0, alto_mm=40.0
        )
        first = self.service.add_operation(
            project_id=project.id, nombre="Taladrado 0,8", tipo="taladrado"
        )
        second = self.service.add_operation(
            project_id=project.id, nombre="Taladrado 1,0", tipo="taladrado"
        )
        self.service.upload_operation_gcode(
            project_id=project.id, operation_id=first.id, filename="first.nc",
            content="G21\nG90\nG1 X1 Y1 F100\n",
        )
        self.service.upload_operation_gcode(
            project_id=project.id, operation_id=second.id, filename="second.nc",
            content="G21\nG90\nG1 X20 Y10 F200\n",
        )
        first_result = self.service.analyze_operation(project.id, first.id)
        second_result = self.service.analyze_operation(project.id, second.id)

        self.assertEqual(first_result.nombre_archivo_original, "first.nc")
        self.assertEqual(second_result.nombre_archivo_original, "second.nc")
        self.assertNotEqual(
            first_result.analisis.limites.max_x_mm,
            second_result.analisis.limites.max_x_mm,
        )

    def test_legacy_project_and_map_migrate_to_main_setup(self) -> None:
        project = self.service.create_project(
            nombre="Legado", ancho_mm=50.0, alto_mm=40.0
        )
        operation = self.service.add_operation(
            project_id=project.id, nombre="Legada", tipo="taladrado"
        )
        self.service.upload_operation_gcode(
            project_id=project.id, operation_id=operation.id, filename="legacy.nc",
            content="G21\nG90\nG1 X5 Y5 F100\n",
        )
        self.service.analyze_operation(project.id, operation.id)
        repository = self.service.repository
        height_maps = HeightMapService(repository)
        height_maps.generate_simulated_map(
            project_id=project.id,
            operation_id=operation.id,
            filas=3,
            columnas=3,
            superficie_simulada="inclinada",
            repeticion_simulacion=2,
            probe_region=ProbeRegion(
                min_x_mm=2.0, min_y_mm=2.0, max_x_mm=48.0, max_y_mm=38.0
            ),
            exclusion_zones=(),
        )
        project_dir = self.base_dir / "projects" / project.id
        (project_dir / "maps" / "setup-main").rename(project_dir / "maps" / operation.id)
        project_file = project_dir / "project.json"
        payload = json.loads(project_file.read_text(encoding="utf-8"))
        payload.pop("montajes")
        payload["version_esquema"] = "1.3"
        for item in payload["operaciones"]:
            item.pop("setup_id", None)
            item.pop("tool_id", None)
        project_file.write_text(json.dumps(payload), encoding="utf-8")

        migrated_service = ProjectService(JsonProjectRepository(self.base_dir))
        migrated = migrated_service.get_project(project.id)
        migrated_map = HeightMapService(migrated_service.repository).get_map(
            project.id, operation.id
        )
        self.assertEqual(migrated.montajes[0].nombre, "Montaje principal")
        persisted = json.loads(project_file.read_text(encoding="utf-8"))
        self.assertEqual(persisted["version_esquema"], "1.4")
        self.assertEqual(persisted["montajes"][0]["nombre"], "Montaje principal")
        self.assertEqual(migrated.operaciones[0].setup_id, "setup-main")
        self.assertEqual(migrated.operaciones[0].nombre_archivo_original, "legacy.nc")
        self.assertIsNotNone(migrated.operaciones[0].analisis)
        self.assertEqual(migrated_map.version, 1)
        self.assertTrue((project_dir / "maps" / operation.id / "height_map.json").exists())
        self.assertTrue((project_dir / "maps" / "setup-main" / "height_map.json").exists())
