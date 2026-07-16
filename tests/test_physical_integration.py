from __future__ import annotations

from dataclasses import replace
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from klipper_cnc_assistant.application import ApplicationError, HeightMapService, MachineSessionService, MeshExecutionService, ReferenceSessionService
from klipper_cnc_assistant.application.compensated_gcode_service import CompensatedGCodeService
from klipper_cnc_assistant.application.physical_map_service import PhysicalExclusion, PhysicalMapService, PhysicalMeshConfig
from klipper_cnc_assistant.input.serial_driver import HEADER, SerialDriver
from klipper_cnc_assistant.machine.discovery import discover_machine
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


class FakeMeshRuntime:
    def __init__(self, *, fail_first: bool = False) -> None:
        self.calls: list[int] = []
        self.probe_configs: list[dict | None] = []
        self.fail_first = fail_first
        self.failed_once = False

    def probe_mesh_point(self, point: dict, probe_config: dict | None = None) -> dict:
        self.calls.append(int(point["index"]))
        self.probe_configs.append(probe_config)
        if self.fail_first and not self.failed_once:
            self.failed_once = True
            raise RuntimeError("timeout HTTP reconciliable")
        return {"z_measured": 1.0 + 0.001 * float(point["x_local"]) + 0.002 * float(point["y_local"]), "duration_s": 0.001}

    def snapshot(self) -> dict:
        return {
            "state": "MESH_PROBING",
            "position": {"x": 0.0, "y": 0.0, "z": 10.0, "velocity": 0.0},
            "homed_axes": "xyz",
            "last_command_text": "probe_mesh_point",
            "telemetry_age_s": 0.01,
            "serial_age_s": 0.01,
        }


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

    def test_discovery_reads_max_z_velocity_from_klipper_config(self) -> None:
        class Client:
            def query_objects(self, objects):
                self.objects = objects
                return {
                    "toolhead": {
                        "position": [0, 0, 0],
                        "homed_axes": "xyz",
                        "axis_minimum": [0, 0, 0],
                        "axis_maximum": [100, 100, 200],
                        "max_velocity": 100,
                        "max_accel": 500,
                    },
                    "configfile": {
                        "settings": {
                            "printer": {"max_z_velocity": 2.5}
                        }
                    },
                }

        client = Client()
        machine = discover_machine(client)

        self.assertEqual(machine.max_z_velocity, 2.5)
        self.assertIn("configfile", client.objects)

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
            self.assertEqual(plan["points"][0]["role"], "REFERENCE")
            self.assertEqual(plan["points"][0]["status"], "MEASURED")
            self.assertAlmostEqual(float(plan["probe_config"]["reference_z_mm"]), 1.23)
            self.assertAlmostEqual(float(plan["points"][0]["delta_z"]), 0.0)
            self.assertAlmostEqual(float(plan["points"][0]["z_measured"]), 1.23)
            self.assertEqual(service.next_pending_point(project.id, plan["map_id"])["index"], 1)
            updated = service.record_point(project_id=project.id, map_id=plan["map_id"], point_index=1, z_measured=1.17)
            self.assertAlmostEqual(updated["points"][1]["delta_z"], -0.06)
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
            self.assertEqual((plan["points"][0]["role"], plan["points"][0]["x_local"], plan["points"][0]["y_local"]), ("REFERENCE", 0.0, 0.0))
            grid_points = [point for point in plan["points"] if point.get("role") != "REFERENCE"]
            self.assertEqual((grid_points[0]["row"], grid_points[0]["column"], grid_points[0]["x_local"], grid_points[0]["y_local"]), (0, 0, 2.0, 2.0))
            self.assertEqual((grid_points[5]["row"], grid_points[5]["column"], grid_points[5]["x_local"]), (0, 5, 58.0))
            self.assertEqual((grid_points[6]["row"], grid_points[6]["column"], grid_points[6]["x_local"]), (1, 5, 58.0))
            self.assertEqual((grid_points[11]["row"], grid_points[11]["column"], grid_points[11]["x_local"]), (1, 0, 2.0))

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


    def test_compensation_allows_initial_travel_outside_domain_when_cutting_path_is_covered(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository = JsonProjectRepository(Path(temp))
            project_service = ProjectService(repository)
            project = project_service.create_project(nombre="PCB", ancho_mm=60, alto_mm=60, espesor_mm=1.6)
            operation = project_service.add_operation(project_id=project.id, nombre="Aislamiento", tipo="aislamiento", cara="superior", orden=0, tool_id="tool-v", herramienta="V-bit")
            original = "G21\nG90\nG0 Z5.000\nG0 X0 Y0\nG0 X20 Y20\nG1 X20 Y20 Z-0.100 F120\nG1 X30 Y20 Z-0.100 F120\n"
            project_service.upload_operation_gcode(project_id=project.id, operation_id=operation.id, filename="job.nc", content=original)
            project_service.analyze_operation(project.id, operation.id)
            service = PhysicalMapService(repository)
            plan = service.capture_reference_and_plan(
                project_id=project.id,
                operation_id=operation.id,
                machine_origin_x=100.0,
                machine_origin_y=200.0,
                reference_z=1.0,
                machine_position={"x_mm": 100.0, "y_mm": 200.0, "z_mm": 1.0},
                homed_axes="xyz",
                machine_label="test",
                session_id="session",
                config=PhysicalMeshConfig(grid_mode="manual", rows=3, columns=3, edge_margin_left_mm=10.0, edge_margin_right_mm=10.0, edge_margin_bottom_mm=10.0, edge_margin_top_mm=10.0),
            )
            for point in plan["points"]:
                z = 1.0 + 0.001 * float(point["x_local"]) + 0.002 * float(point["y_local"])
                plan = service.record_point(project_id=project.id, map_id=plan["map_id"], point_index=int(point["index"]), z_measured=z)

            generator = CompensatedGCodeService(repository, service)
            result = generator.generate(project.id, operation.id)
            generated = repository.read_project_file(project.id, result["relative_path"])
            self.assertIn("G1 X0.00000 Y0.00000 Z5.00000", generated)
            self.assertIn("X20.00000 Y20.00000 Z-0.04000", generated)

    def test_completed_physical_mesh_feeds_compensation_without_simulated_blockers(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository = JsonProjectRepository(Path(temp))
            project_service = ProjectService(repository)
            project = project_service.create_project(nombre="PCB", ancho_mm=60, alto_mm=60, espesor_mm=1.6)
            operation = project_service.add_operation(project_id=project.id, nombre="Aislamiento", tipo="aislamiento", cara="superior", orden=0, tool_id="tool-v", herramienta="V-bit")
            original = "G21\nG90\nG1 X0 Y0 Z-0.100 F120\nG1 X20 Y0 Z-0.100 F120\nG2 X30 Y0 I5 J0 Z-0.100 F120\n"
            project_service.upload_operation_gcode(project_id=project.id, operation_id=operation.id, filename="job.nc", content=original)
            project_service.analyze_operation(project.id, operation.id)
            service = PhysicalMapService(repository)
            plan = service.capture_reference_and_plan(
                project_id=project.id,
                operation_id=operation.id,
                machine_origin_x=100.0,
                machine_origin_y=200.0,
                reference_z=1.0,
                machine_position={"x_mm": 100.0, "y_mm": 200.0, "z_mm": 1.0},
                homed_axes="xyz",
                machine_label="test",
                session_id="session",
                config=PhysicalMeshConfig(grid_mode="manual", rows=3, columns=3, edge_margin_left_mm=0.0, edge_margin_right_mm=0.0, edge_margin_bottom_mm=0.0, edge_margin_top_mm=0.0),
            )
            for point in plan["points"]:
                z = 1.0 + 0.001 * float(point["x_local"]) + 0.002 * float(point["y_local"])
                plan = service.record_point(project_id=project.id, map_id=plan["map_id"], point_index=int(point["index"]), z_measured=z)

            self.assertEqual(plan["status"], "MESH_COMPLETE")
            self.assertEqual(plan["source"], "MEASURED")
            self.assertEqual(plan["map_ready_state"], "MAP_READY")
            self.assertEqual(plan["validation"]["status"], "VALID")
            loaded = project_service.get_project(project.id)
            self.assertEqual(loaded.get_setup(operation.setup_id).active_map_id, plan["map_id"])

            machine_session = MachineSessionService()
            machine_session.machine_mode = "fisico"
            reference = ReferenceSessionService(repository, HeightMapService(repository), machine_session, service)
            session = reference.get_session(project.id, operation.id)
            self.assertTrue(session["lista_para_compensacion"])
            self.assertEqual(session["bloqueos_compensacion"], [])
            step_details = "\n".join(str(step["detalle"]) for step in session["pasos"])
            self.assertIn("Homing válido", step_details)
            self.assertIn("Origen X/Y medido", step_details)
            self.assertIn("Referencia Z medida", step_details)
            self.assertIn("Región configurada", step_details)
            self.assertIn("Mapa medido y activo", step_details)
            self.assertIn("Cobertura validada", step_details)

            preview_payload = reference.build_compensation_preview(project.id, operation.id)
            self.assertEqual(preview_payload["session"]["bloqueos_compensacion"], [])
            generator = CompensatedGCodeService(repository, service)
            result = generator.generate(project.id, operation.id, max_segment_mm=5.0)
            generated_path = generator.resolve_generated_file(project.id, result["relative_path"])
            generated = generated_path.read_text(encoding="utf-8")
            persisted_operation = project_service.get_project(project.id).get_operation(operation.id)
            self.assertEqual(repository.read_project_file(project.id, persisted_operation.archivo_gcode), original)
            self.assertIn("X20.00000 Y0.00000 Z-0.08000", generated)
            self.assertIn("X30.00000 Y0.00000", generated)
            self.assertFalse(any(line.startswith(("G2 ", "G3 ")) for line in generated.splitlines()))
            self.assertGreater(generated.count("G1 "), 8)
            self.assertTrue(generated_path.exists())
            self.assertIn("original_hash", result["metadata"])

            narrow = service.capture_reference_and_plan(
                project_id=project.id,
                operation_id=operation.id,
                machine_origin_x=100.0,
                machine_origin_y=200.0,
                reference_z=1.0,
                machine_position={"x_mm": 100.0, "y_mm": 200.0, "z_mm": 1.0},
                homed_axes="xyz",
                machine_label="test",
                session_id="session-2",
                config=PhysicalMeshConfig(grid_mode="manual", rows=2, columns=2, edge_margin_left_mm=10.0, edge_margin_right_mm=10.0, edge_margin_bottom_mm=10.0, edge_margin_top_mm=10.0),
            )
            for point in narrow["points"]:
                narrow = service.record_point(project_id=project.id, map_id=narrow["map_id"], point_index=int(point["index"]), z_measured=1.0)
            with self.assertRaises(ApplicationError):
                generator.generate(project.id, operation.id)

    def test_completed_existing_physical_map_is_finalized_on_read_without_reprobing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository, project_service, project, operation = self._physical_project(temp)
            service = PhysicalMapService(repository)
            plan = service.capture_reference_and_plan(
                project_id=project.id,
                operation_id=operation.id,
                machine_origin_x=0.0,
                machine_origin_y=0.0,
                reference_z=1.0,
                machine_position={"x_mm": 0.0, "y_mm": 0.0, "z_mm": 1.0},
                homed_axes="xyz",
                machine_label="test",
                session_id="session",
                config=PhysicalMeshConfig(grid_mode="manual", rows=2, columns=2, edge_margin_left_mm=0.0, edge_margin_right_mm=0.0, edge_margin_bottom_mm=0.0, edge_margin_top_mm=0.0, safe_z_mm=12.0, probe_step_mm=0.05, probe_feed_mm_min=30.0, retract_mm=0.8),
            )
            for point in plan["points"]:
                plan = service.record_point(project_id=project.id, map_id=plan["map_id"], point_index=int(point["index"]), z_measured=1.0)
            legacy_payload = dict(plan)
            legacy_payload.pop("validation", None)
            legacy_payload.pop("map_ready_state", None)
            repository.save_height_map_payload(project.id, plan["map_id"], legacy_payload)
            loaded_project = repository.load_project(project.id)
            setup = loaded_project.get_setup(operation.setup_id)
            repository.save_project(loaded_project.replace_setup(replace(setup, active_map_id=None)))

            finalized = service.get_active(project.id, operation.id)
            self.assertEqual(finalized["map_id"], plan["map_id"])
            self.assertEqual(finalized["map_ready_state"], "MAP_READY")
            self.assertEqual(finalized["validation"]["status"], "VALID")
            refreshed = project_service.get_project(project.id)
            self.assertEqual(refreshed.get_setup(operation.setup_id).active_map_id, plan["map_id"])
            machine_session = MachineSessionService()
            machine_session.machine_mode = "fisico"
            session = ReferenceSessionService(repository, HeightMapService(repository), machine_session, service).get_session(project.id, operation.id)
            self.assertTrue(session["lista_para_compensacion"])
            self.assertEqual(session["bloqueos_compensacion"], [])

    def test_mesh_execution_worker_completes_2x2_without_per_point_frontend_continue(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository, _project_service, project, operation = self._physical_project(temp)
            service = PhysicalMapService(repository)
            plan = service.capture_reference_and_plan(
                project_id=project.id,
                operation_id=operation.id,
                machine_origin_x=0.0,
                machine_origin_y=0.0,
                reference_z=1.0,
                machine_position={"x_mm": 0.0, "y_mm": 0.0, "z_mm": 1.0},
                homed_axes="xyz",
                machine_label="test",
                session_id="session",
                config=PhysicalMeshConfig(grid_mode="manual", rows=2, columns=2, edge_margin_left_mm=0.0, edge_margin_right_mm=0.0, edge_margin_bottom_mm=0.0, edge_margin_top_mm=0.0, safe_z_mm=12.0, probe_step_mm=0.05, probe_feed_mm_min=30.0, retract_mm=0.8),
            )
            worker = MeshExecutionService(service, max_point_retries=2)
            runtime = FakeMeshRuntime(fail_first=True)
            started = worker.start_all(project_id=project.id, map_id=plan["map_id"], runtime=runtime)
            self.assertEqual(started["status"], "MESH_PROBING")
            deadline = time.monotonic() + 3.0
            completed = service.get_by_id(project.id, plan["map_id"])
            while time.monotonic() < deadline:
                completed = service.get_by_id(project.id, plan["map_id"])
                if completed["status"] == "MESH_COMPLETE":
                    break
                time.sleep(0.01)
            self.assertEqual(completed["status"], "MESH_COMPLETE")
            self.assertEqual(sum(1 for point in completed["points"] if point["status"] == "MEASURED"), 4)
            self.assertEqual(sorted(set(runtime.calls)), [1, 2, 3])
            self.assertEqual(runtime.calls.count(1), 2)
            self.assertEqual(runtime.calls.count(0), 0)
            self.assertTrue(runtime.probe_configs)
            self.assertTrue(all(config and float(config["probe_feed_mm_min"]) == 30.0 for config in runtime.probe_configs))
            self.assertTrue(all(config and float(config["safe_z_mm"]) == 12.0 for config in runtime.probe_configs))
            self.assertTrue(all(config and float(config["reference_z_mm"]) == 1.0 for config in runtime.probe_configs))
            log = service.execution_log(project_id=project.id, map_id=plan["map_id"])
            self.assertEqual(log["execution"]["worker_active"], False)
            self.assertIn("POINT_RETRY", {event.get("next_state") for event in log["events"]})
            self.assertIn("POINT_COMPLETE", {event.get("next_state") for event in log["events"]})
            self.assertTrue(worker.wait_until_idle(timeout_s=1.0))

    def test_reference_session_accepts_map_ready_active_map_without_reasking_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository, _project_service, project, operation = self._physical_project(temp)
            service = PhysicalMapService(repository)
            plan = service.capture_reference_and_plan(
                project_id=project.id,
                operation_id=operation.id,
                machine_origin_x=100.0,
                machine_origin_y=200.0,
                reference_z=1.0,
                machine_position={"x_mm": 100.0, "y_mm": 200.0, "z_mm": 1.0},
                homed_axes="xyz",
                machine_label="test",
                session_id="session",
                config=PhysicalMeshConfig(grid_mode="manual", rows=2, columns=2, edge_margin_left_mm=0.0, edge_margin_right_mm=0.0, edge_margin_bottom_mm=0.0, edge_margin_top_mm=0.0),
            )
            for point in plan["points"]:
                plan = service.record_point(project_id=project.id, map_id=plan["map_id"], point_index=int(point["index"]), z_measured=1.0)

            payload = service.get_by_id(project.id, plan["map_id"])
            payload["status"] = "MAP_READY"
            repository.save_height_map_payload(project.id, plan["map_id"], payload)

            machine_session = MachineSessionService()
            machine_session.machine_mode = "fisico"
            session = ReferenceSessionService(repository, HeightMapService(repository), machine_session, service).get_session(project.id, operation.id)
            self.assertTrue(session["lista_para_compensacion"])
            self.assertEqual(session["bloqueos_compensacion"], [])
            self.assertIn("Cobertura validada", "\n".join(str(step["detalle"]) for step in session["pasos"]))

    def test_reference_session_reports_exact_invalid_coverage_reason_for_physical_map(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository = JsonProjectRepository(Path(temp))
            project_service = ProjectService(repository)
            project = project_service.create_project(nombre="PCB", ancho_mm=60, alto_mm=60, espesor_mm=1.6)
            operation = project_service.add_operation(project_id=project.id, nombre="Aislamiento", tipo="aislamiento", cara="superior", orden=0, tool_id="tool-v", herramienta="V-bit")
            project_service.upload_operation_gcode(project_id=project.id, operation_id=operation.id, filename="job.nc", content="G21\nG90\nG1 X2.859 Y4.905 Z-0.100 F120\nG1 X20 Y20 Z-0.100 F120\n")
            project_service.analyze_operation(project.id, operation.id)
            service = PhysicalMapService(repository)
            plan = service.capture_reference_and_plan(
                project_id=project.id,
                operation_id=operation.id,
                machine_origin_x=100.0,
                machine_origin_y=200.0,
                reference_z=1.0,
                machine_position={"x_mm": 100.0, "y_mm": 200.0, "z_mm": 1.0},
                homed_axes="xyz",
                machine_label="test",
                session_id="session",
                config=PhysicalMeshConfig(grid_mode="manual", rows=2, columns=2, edge_margin_left_mm=10.0, edge_margin_right_mm=10.0, edge_margin_bottom_mm=10.0, edge_margin_top_mm=10.0),
            )
            for point in plan["points"]:
                plan = service.record_point(project_id=project.id, map_id=plan["map_id"], point_index=int(point["index"]), z_measured=1.0)

            machine_session = MachineSessionService()
            machine_session.machine_mode = "fisico"
            session = ReferenceSessionService(repository, HeightMapService(repository), machine_session, service).get_session(project.id, operation.id)
            self.assertFalse(session["lista_para_compensacion"])
            joined = " ".join(session["bloqueos_compensacion"])
            self.assertIn("La cobertura del mapa físico es insuficiente", joined)
            self.assertIn("Primer punto fuera", joined)
            self.assertIn("X=0.000", joined)
            self.assertIn("distancia=14.142 mm", joined)
            self.assertNotIn("Falta validación de cobertura del mapa físico.", joined)

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
            self.assertEqual(plan["acquisition_point_count"], 5)
            self.assertEqual(plan["points"][0]["role"], "REFERENCE")
            self.assertEqual((plan["points"][0]["x_local"], plan["points"][0]["y_local"]), (0.0, 0.0))
            self.assertEqual(plan["grid"], {"rows": 2, "columns": 2, "dx_mm": 56.0, "dy_mm": 56.0})
            grid_points = [point for point in plan["points"] if point.get("role") != "REFERENCE"]
            self.assertEqual([(p["x_local"], p["y_local"]) for p in grid_points], [(2.0, 2.0), (58.0, 2.0), (58.0, 58.0), (2.0, 58.0)])

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
            self.assertEqual(plan["acquisition_point_count"], 13)
            self.assertAlmostEqual(plan["grid"]["dx_mm"], 56 / 3)
            self.assertAlmostEqual(plan["grid"]["dy_mm"], 28.0)
            grid_points = [point for point in plan["points"] if point.get("role") != "REFERENCE"]
            self.assertEqual(grid_points[0]["x_local"], 2.0)
            self.assertEqual(grid_points[-1]["x_local"], 58.0)

    def test_preview_local_2x2_without_reference_returns_four_grid_points(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository, _project_service, project, operation = self._physical_project(temp)
            service = PhysicalMapService(repository)
            preview = service.preview_mesh(
                project_id=project.id,
                operation_id=operation.id,
                config=PhysicalMeshConfig(grid_mode="manual", rows=2, columns=2, edge_margin_left_mm=2, edge_margin_right_mm=2, edge_margin_bottom_mm=2, edge_margin_top_mm=2),
            )
            self.assertEqual(preview["status"], "MESH_PREVIEW")
            self.assertEqual(preview["point_count"], 4)
            self.assertEqual(len(preview["points"]), 4)
            self.assertIsNone(preview["points"][0]["x_machine"])
            self.assertEqual(preview["reference_point"]["role"], "REFERENCE")
            self.assertFalse(preview["valid_for_execution"])

    def test_repeat_measurement_archives_previous_and_creates_empty_version_with_reference_first(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository, _project_service, project, operation = self._physical_project(temp)
            service = PhysicalMapService(repository)
            plan = service.capture_reference_and_plan(
                project_id=project.id, operation_id=operation.id, machine_origin_x=5.0, machine_origin_y=6.0, reference_z=1.0,
                machine_position={"x_mm": 5.0, "y_mm": 6.0, "z_mm": 1.0}, homed_axes="xyz", machine_label="test", session_id="session",
                config=PhysicalMeshConfig(grid_mode="manual", rows=2, columns=2),
            )
            repeated = service.repeat_measurement(project_id=project.id, map_id=plan["map_id"])
            self.assertNotEqual(repeated["map_id"], plan["map_id"])
            self.assertEqual(repeated["status"], "REPROBE_CONFIRMATION")
            self.assertEqual(repeated["points"][0]["role"], "REFERENCE")
            self.assertTrue(all(point["status"] in {"PENDING", "EXCLUDED"} for point in repeated["points"]))
            history = service.history(project_id=project.id, operation_id=operation.id)
            self.assertGreaterEqual(len(history), 2)
            self.assertTrue(any(item["map_id"] == plan["map_id"] and item["archived_at"] for item in history))

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
