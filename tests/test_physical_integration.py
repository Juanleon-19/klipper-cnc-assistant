from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from klipper_cnc_assistant.application.compensated_gcode_service import CompensatedGCodeService
from klipper_cnc_assistant.application.physical_map_service import PhysicalExclusion, PhysicalMapService, PhysicalMeshConfig
from klipper_cnc_assistant.input.serial_driver import HEADER, SerialDriver
from klipper_cnc_assistant.machine.state import AxisLimits, MachinePosition, MachineState
from klipper_cnc_assistant.moonraker.telemetry import MoonrakerTelemetry
from klipper_cnc_assistant.storage import JsonProjectRepository
from klipper_cnc_assistant.application.services import ProjectService


def packet(direction: int, flags: int = 0, x: int = 512, y: int = 512) -> bytes:
    raw = bytes([
        HEADER,
        direction,
        flags,
        x & 0xFF,
        (x >> 8) & 0xFF,
        y & 0xFF,
        (y >> 8) & 0xFF,
    ])
    checksum = 0
    for value in raw[:7]:
        checksum ^= value
    return raw + bytes([checksum])


class FakeSerial:
    def __init__(self, *_, **__):
        self.is_open = True
        bad = bytearray(packet(4, flags=0x03))
        bad[-1] ^= 0xFF
        self.buffer = bytearray(b"noise" + bytes(bad) + packet(3, flags=0x04, x=300, y=700))

    def read(self, size: int) -> bytes:
        if not self.buffer:
            return b""
        result = bytes(self.buffer[:size])
        del self.buffer[:size]
        return result

    def reset_input_buffer(self) -> None:
        pass

    def close(self) -> None:
        self.is_open = False


class PhysicalIntegrationTest(unittest.TestCase):
    def test_serial_driver_resynchronizes_and_reports_diagnostics(self) -> None:
        with patch("klipper_cnc_assistant.input.serial_driver.serial.Serial", FakeSerial):
            driver = SerialDriver(startup_delay=0)
            received = driver.read_packet()
        self.assertEqual(received.direction, "LEFT")
        self.assertTrue(received.probe)
        diagnostics = driver.diagnostics.snapshot()
        self.assertGreaterEqual(diagnostics["sync_drops"], 5)
        self.assertEqual(diagnostics["checksum_errors"], 1)
        self.assertEqual(diagnostics["valid_packets"], 1)
        self.assertEqual(diagnostics["packets_complete"], 2)

    def test_telemetry_updates_toolhead_homed_axes(self) -> None:
        machine = MachineState(
            position=MachinePosition(0, 0, 0),
            x_limits=AxisLimits(0, 100),
            y_limits=AxisLimits(0, 100),
            z_limits=AxisLimits(0, 50),
            homed_axes="",
            max_velocity=100,
            max_accel=500,
        )
        telemetry = MoonrakerTelemetry("ws://example", machine)
        telemetry._process_message({
            "method": "notify_status_update",
            "params": [{
                "toolhead": {
                    "position": [1, 2, 3],
                    "homed_axes": "xyz",
                    "axis_minimum": [-5, -6, 0],
                    "axis_maximum": [120, 130, 60],
                    "max_velocity": 150,
                    "max_accel": 800,
                }
            }],
        })
        self.assertEqual(machine.homed_axes, "xyz")
        self.assertTrue(machine.is_homed)
        self.assertEqual(machine.x_limits.minimum, -5)
        self.assertEqual(machine.position.z, 3)

    def test_physical_map_is_keyed_by_setup_face_and_persists_relative_points(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository = JsonProjectRepository(Path(temp))
            project_service = ProjectService(repository)
            project = project_service.create_project(nombre="PCB", ancho_mm=50, alto_mm=40, espesor_mm=1.6)
            first = project_service.add_operation(project_id=project.id, nombre="Aislamiento 0.2", tipo="aislamiento", cara="superior", orden=0, tool_id="tool-02", herramienta="V-bit 0.2 mm")
            second = project_service.add_operation(project_id=project.id, nombre="Corte 1.0", tipo="corte exterior", cara="superior", orden=1, tool_id="tool-10", herramienta="Fresa 1.0 mm")
            project_service.upload_operation_gcode(project_id=project.id, operation_id=first.id, filename="first.nc", content="G21\nG90\nG1 X2 Y3 F120\nG1 X20 Y12 F120\n")
            project_service.upload_operation_gcode(project_id=project.id, operation_id=second.id, filename="second.nc", content="G21\nG90\nG1 X30 Y5 F120\nG1 X40 Y20 F120\n")
            project_service.analyze_operation(project.id, first.id)
            project_service.analyze_operation(project.id, second.id)
            service = PhysicalMapService(repository)
            plan = service.capture_reference_and_plan(
                project_id=project.id,
                operation_id=first.id,
                machine_origin_x=100.0,
                machine_origin_y=200.0,
                reference_z=1.23,
                machine_position={"x_mm": 100.0, "y_mm": 200.0, "z_mm": 1.23},
                homed_axes="xyz",
                machine_label="test",
                session_id="session",
            )
            self.assertEqual(plan["tool_id"], "tool-02")
            self.assertEqual(set(plan["operation_ids"]), {first.id, second.id})
            self.assertEqual(plan["source"], "MEASURED")
            self.assertEqual(plan["map_model"], "SURFACE_BY_SETUP_FACE_PLACEMENT")
            self.assertTrue(plan["map_id"].startswith("measured/setup-main/superior/placement-1/"))
            self.assertEqual(plan["local_region"], {"min_x_mm": 2.0, "min_y_mm": 2.0, "max_x_mm": 48.0, "max_y_mm": 38.0})
            self.assertEqual(plan["grid"], {"rows": 7, "columns": 6, "dx_mm": 9.2, "dy_mm": 6.0})
            updated = service.record_point(project_id=project.id, map_id=plan["map_id"], point_index=0, z_measured=1.2)
            self.assertEqual(updated["points"][0]["status"], "MEASURED")
            self.assertAlmostEqual(updated["points"][0]["delta_z"], -0.03)
            self.assertEqual(updated["height_map"]["fuente_datos"], "measured")

            second_reference = service.capture_reference_and_plan(
                project_id=project.id,
                operation_id=second.id,
                machine_origin_x=100.0,
                machine_origin_y=200.0,
                reference_z=2.5,
                machine_position={"x_mm": 100.0, "y_mm": 200.0, "z_mm": 2.5},
                homed_axes="xyz",
                machine_label="test",
                session_id="session-2",
            )
            self.assertEqual(second_reference["map_id"], plan["map_id"])
            self.assertIn("tool-10", second_reference["tool_references"])

    def test_physical_mesh_uses_material_edge_retreat_rows_columns_and_serpentine(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository = JsonProjectRepository(Path(temp))
            project_service = ProjectService(repository)
            project = project_service.create_project(nombre="PCB", ancho_mm=60, alto_mm=60, espesor_mm=1.6)
            operation = project_service.add_operation(project_id=project.id, nombre="Aislamiento", tipo="aislamiento", cara="superior", orden=0, tool_id="tool-v", herramienta="V-bit")
            project_service.upload_operation_gcode(project_id=project.id, operation_id=operation.id, filename="job.nc", content="G21\nG90\nG1 X0 Y0\nG1 X10 Y10\n")
            project_service.analyze_operation(project.id, operation.id)
            service = PhysicalMapService(repository)
            plan = service.capture_reference_and_plan(
                project_id=project.id, operation_id=operation.id, machine_origin_x=10.0, machine_origin_y=20.0, reference_z=1.0,
                machine_position={"x_mm": 10.0, "y_mm": 20.0, "z_mm": 1.0}, homed_axes="xyz", machine_label="test", session_id="session",
                config=PhysicalMeshConfig(rows=7, columns=6, edge_margin_left_mm=2.0, edge_margin_right_mm=2.0, edge_margin_bottom_mm=2.0, edge_margin_top_mm=2.0),
            )
            self.assertEqual(plan["local_region"], {"min_x_mm": 2.0, "min_y_mm": 2.0, "max_x_mm": 58.0, "max_y_mm": 58.0})
            self.assertEqual(plan["point_count"], 42)
            self.assertAlmostEqual(plan["grid"]["dx_mm"], 11.2)
            self.assertAlmostEqual(plan["grid"]["dy_mm"], 56 / 6)
            self.assertEqual((plan["points"][0]["row"], plan["points"][0]["column"], plan["points"][0]["x_local"], plan["points"][0]["y_local"]), (0, 0, 2.0, 2.0))
            self.assertEqual((plan["points"][5]["row"], plan["points"][5]["column"], plan["points"][5]["x_local"]), (0, 5, 58.0))
            self.assertEqual((plan["points"][6]["row"], plan["points"][6]["column"], plan["points"][6]["x_local"]), (1, 5, 58.0))
            self.assertEqual((plan["points"][11]["row"], plan["points"][11]["column"], plan["points"][11]["x_local"]), (1, 0, 2.0))

    def test_physical_mesh_supports_independent_retreats_and_exclusions(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository = JsonProjectRepository(Path(temp))
            project_service = ProjectService(repository)
            project = project_service.create_project(nombre="PCB", ancho_mm=60, alto_mm=60, espesor_mm=1.6)
            operation = project_service.add_operation(project_id=project.id, nombre="Taladros", tipo="taladrado", cara="superior", orden=0, tool_id="tool-drill", herramienta="Broca")
            project_service.upload_operation_gcode(project_id=project.id, operation_id=operation.id, filename="job.nc", content="G21\nG90\nG1 X0 Y0\n")
            project_service.analyze_operation(project.id, operation.id)
            service = PhysicalMapService(repository)
            plan = service.capture_reference_and_plan(
                project_id=project.id, operation_id=operation.id, machine_origin_x=0.0, machine_origin_y=0.0, reference_z=0.0,
                machine_position={"x_mm": 0.0, "y_mm": 0.0, "z_mm": 0.0}, homed_axes="xyz", machine_label="test", session_id="session",
                config=PhysicalMeshConfig(
                    rows=3, columns=3, edge_margin_left_mm=1.0, edge_margin_right_mm=3.0, edge_margin_bottom_mm=2.0, edge_margin_top_mm=4.0,
                    exclusions=(
                        PhysicalExclusion(id="rect", name="Pinza", shape="rectangle", x_min_mm=0.5, x_max_mm=2.0, y_min_mm=1.5, y_max_mm=3.0),
                        PhysicalExclusion(id="circle", name="Tornillo", shape="circle", center_x_mm=29.0, center_y_mm=29.0, radius_mm=2.0),
                    ),
                ),
            )
            self.assertEqual(plan["local_region"], {"min_x_mm": 1.0, "min_y_mm": 2.0, "max_x_mm": 57.0, "max_y_mm": 56.0})
            self.assertEqual(plan["excluded_count"], 2)
            self.assertEqual(plan["executable_point_count"], 7)
            excluded = [point for point in plan["points"] if point["status"] == "EXCLUDED"]
            self.assertEqual(len(excluded), 2)
            self.assertIn("Excluido", excluded[0]["error"])

    def test_invalid_edge_retreat_blocks_mesh(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository = JsonProjectRepository(Path(temp))
            project_service = ProjectService(repository)
            project = project_service.create_project(nombre="PCB", ancho_mm=10, alto_mm=10, espesor_mm=1.6)
            operation = project_service.add_operation(project_id=project.id, nombre="Aislamiento", tipo="aislamiento", cara="superior", orden=0)
            service = PhysicalMapService(repository)
            with self.assertRaises(Exception) as context:
                service.capture_reference_and_plan(
                    project_id=project.id, operation_id=operation.id, machine_origin_x=0.0, machine_origin_y=0.0, reference_z=0.0,
                    machine_position={"x_mm": 0.0, "y_mm": 0.0, "z_mm": 0.0}, homed_axes="xyz", machine_label="test", session_id="session",
                    config=PhysicalMeshConfig(rows=2, columns=2, edge_margin_left_mm=6.0, edge_margin_right_mm=5.0),
                )
            self.assertIn("retiro de los bordes", str(context.exception))


    def test_compensated_gcode_generation_preserves_xy_and_uses_relative_surface(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository = JsonProjectRepository(Path(temp))
            project_service = ProjectService(repository)
            project = project_service.create_project(nombre="PCB", ancho_mm=50, alto_mm=40, espesor_mm=1.6)
            operation = project_service.add_operation(project_id=project.id, nombre="Aislamiento", tipo="aislamiento", cara="superior", orden=0, tool_id="tool-v", herramienta="V-bit 0.2 mm")
            project_service.upload_operation_gcode(project_id=project.id, operation_id=operation.id, filename="job.nc", content="G21\nG90\nG1 X0 Y0 Z-0.10 F120\nG1 X10 Y0 Z-0.10 F120\nG1 X10 Y10 Z-0.10 F120\n")
            project_service.analyze_operation(project.id, operation.id)
            service = PhysicalMapService(repository)
            plan = service.capture_reference_and_plan(
                project_id=project.id,
                operation_id=operation.id,
                machine_origin_x=20.0,
                machine_origin_y=30.0,
                reference_z=1.0,
                machine_position={"x_mm": 20.0, "y_mm": 30.0, "z_mm": 1.0},
                homed_axes="xyz",
                machine_label="test",
                session_id="session",
                config=PhysicalMeshConfig(edge_margin_left_mm=0.0, edge_margin_right_mm=0.0, edge_margin_bottom_mm=0.0, edge_margin_top_mm=0.0),
            )
            for point in plan["points"]:
                z = 1.0 + 0.001 * float(point["x_local"]) + 0.002 * float(point["y_local"])
                plan = service.record_point(project_id=project.id, map_id=plan["map_id"], point_index=int(point["index"]), z_measured=z)
            self.assertEqual(plan["status"], "MESH_COMPLETE")
            generator = CompensatedGCodeService(repository, service)
            result = generator.generate(project.id, operation.id)
            generated = repository.read_project_file(project.id, result["relative_path"])
            self.assertIn("X10.00000 Y0.00000", generated)
            self.assertIn("Z-0.09000", generated)
            self.assertEqual(result["metadata"]["tool_id"], "tool-v")
            self.assertTrue(result["relative_path"].startswith("generated/compensated/"))


    def _physical_project(self, temp: str):
        repository = JsonProjectRepository(Path(temp))
        project_service = ProjectService(repository)
        project = project_service.create_project(nombre="PCB", ancho_mm=60, alto_mm=60, espesor_mm=1.6)
        operation = project_service.add_operation(project_id=project.id, nombre="Aislamiento", tipo="aislamiento", cara="superior", orden=0, tool_id="tool-v", herramienta="V-bit")
        project_service.upload_operation_gcode(project_id=project.id, operation_id=operation.id, filename="job.nc", content="G21\nG90\nG1 X0 Y0\nG1 X10 Y10\n")
        project_service.analyze_operation(project.id, operation.id)
        return repository, project_service, project, operation

    def test_manual_mesh_2x2_generates_exact_inner_vertices(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository, _project_service, project, operation = self._physical_project(temp)
            service = PhysicalMapService(repository)
            plan = service.capture_reference_and_plan(
                project_id=project.id, operation_id=operation.id, machine_origin_x=0.0, machine_origin_y=0.0, reference_z=0.0,
                machine_position={"x_mm": 0.0, "y_mm": 0.0, "z_mm": 0.0}, homed_axes="xyz", machine_label="test", session_id="session",
                config=PhysicalMeshConfig(grid_mode="manual", rows=2, columns=2, edge_margin_left_mm=2, edge_margin_right_mm=2, edge_margin_bottom_mm=2, edge_margin_top_mm=2),
            )
            self.assertEqual(plan["point_count"], 4)
            self.assertEqual(plan["grid"], {"rows": 2, "columns": 2, "dx_mm": 56.0, "dy_mm": 56.0})
            self.assertEqual([(p["x_local"], p["y_local"]) for p in plan["points"]], [(2.0, 2.0), (58.0, 2.0), (58.0, 58.0), (2.0, 58.0)])

    def test_manual_mesh_3x4_generates_exact_count_and_spacing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository, _project_service, project, operation = self._physical_project(temp)
            service = PhysicalMapService(repository)
            plan = service.capture_reference_and_plan(
                project_id=project.id, operation_id=operation.id, machine_origin_x=0.0, machine_origin_y=0.0, reference_z=0.0,
                machine_position={"x_mm": 0.0, "y_mm": 0.0, "z_mm": 0.0}, homed_axes="xyz", machine_label="test", session_id="session",
                config=PhysicalMeshConfig(grid_mode="manual", rows=3, columns=4, edge_margin_left_mm=2, edge_margin_right_mm=2, edge_margin_bottom_mm=2, edge_margin_top_mm=2),
            )
            self.assertEqual(plan["point_count"], 12)
            self.assertAlmostEqual(plan["grid"]["dx_mm"], 56 / 3)
            self.assertAlmostEqual(plan["grid"]["dy_mm"], 28.0)
            self.assertEqual(plan["points"][0]["x_local"], 2.0)
            self.assertEqual(plan["points"][-1]["x_local"], 58.0)

    def test_suggested_mesh_produces_concrete_rows_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository, _project_service, project, operation = self._physical_project(temp)
            service = PhysicalMapService(repository)
            suggestion = service.suggest_mesh_config(
                project_id=project.id,
                operation_id=operation.id,
                config=PhysicalMeshConfig(grid_mode="suggested", max_spacing_mm=20.0),
            )
            self.assertEqual(suggestion["grid_mode"], "suggested")
            self.assertEqual(suggestion["rows"], 4)
            self.assertEqual(suggestion["columns"], 4)
            plan = service.capture_reference_and_plan(
                project_id=project.id, operation_id=operation.id, machine_origin_x=0.0, machine_origin_y=0.0, reference_z=0.0,
                machine_position={"x_mm": 0.0, "y_mm": 0.0, "z_mm": 0.0}, homed_axes="xyz", machine_label="test", session_id="session",
                config=PhysicalMeshConfig(grid_mode="suggested", rows=9, columns=9, max_spacing_mm=20.0),
            )
            self.assertEqual(plan["grid"]["rows"], 4)
            self.assertEqual(plan["grid"]["columns"], 4)

    def test_changing_mesh_archives_partial_previous_map(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository, _project_service, project, operation = self._physical_project(temp)
            service = PhysicalMapService(repository)
            first = service.capture_reference_and_plan(
                project_id=project.id, operation_id=operation.id, machine_origin_x=0, machine_origin_y=0, reference_z=0,
                machine_position={"x_mm": 0, "y_mm": 0, "z_mm": 0}, homed_axes="xyz", machine_label="test", session_id="session",
                config=PhysicalMeshConfig(grid_mode="manual", rows=3, columns=3),
            )
            service.record_point(project_id=project.id, map_id=first["map_id"], point_index=0, z_measured=0.01)
            second = service.capture_reference_and_plan(
                project_id=project.id, operation_id=operation.id, machine_origin_x=0, machine_origin_y=0, reference_z=0,
                machine_position={"x_mm": 0, "y_mm": 0, "z_mm": 0}, homed_axes="xyz", machine_label="test", session_id="session",
                config=PhysicalMeshConfig(grid_mode="manual", rows=4, columns=4),
            )
            self.assertNotEqual(first["map_id"], second["map_id"])
            self.assertIn("medición parcial", second["configuration_change_warning"])
            archived = service.get_by_id(project.id, first["map_id"])
            self.assertIsNotNone(archived["archived_at"])

    def test_reset_map_and_preparation_preserve_operations_and_gcode(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository, project_service, project, operation = self._physical_project(temp)
            service = PhysicalMapService(repository)
            plan = service.capture_reference_and_plan(
                project_id=project.id, operation_id=operation.id, machine_origin_x=0, machine_origin_y=0, reference_z=0,
                machine_position={"x_mm": 0, "y_mm": 0, "z_mm": 0}, homed_axes="xyz", machine_label="test", session_id="session",
                config=PhysicalMeshConfig(grid_mode="manual", rows=2, columns=2),
            )
            setup_id = operation.setup_id
            service.reset_map(project_id=project.id, setup_id=setup_id)
            loaded = project_service.get_project(project.id)
            self.assertEqual(len(loaded.operaciones), 1)
            self.assertIsNotNone(loaded.operaciones[0].archivo_gcode)
            self.assertIsNone(loaded.get_setup(setup_id).active_map_id)
            result = service.reset_preparation(project_id=project.id, setup_id=setup_id)
            loaded = project_service.get_project(project.id)
            self.assertEqual(result["previous_placement_revision"], "placement-1")
            self.assertEqual(loaded.get_setup(setup_id).placement_revision, "placement-2")
            self.assertIsNone(loaded.get_setup(setup_id).preparacion.origen_trabajo)
            self.assertIsNone(loaded.get_setup(setup_id).preparacion.referencia_z)
            self.assertTrue(repository.load_height_map_payload(project.id, plan["map_id"])["archived_at"])


if __name__ == "__main__":
    unittest.main()
