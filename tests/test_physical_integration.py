from __future__ import annotations

from dataclasses import replace
import threading
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from klipper_cnc_assistant.application import ApplicationError, HeightMapService, MachineSessionService, ReferenceSessionService
from klipper_cnc_assistant.application.compensated_gcode_service import CompensatedGCodeService
from klipper_cnc_assistant.application.physical_map_service import (
    PhysicalExclusion,
    PhysicalMapService,
    PhysicalMeshConfig,
    canonical_mesh_geometry,
    mesh_configuration_fingerprint,
    mesh_geometry_fingerprint,
)
from klipper_cnc_assistant.domain import CoordinateReference
from klipper_cnc_assistant.execution import MeshExecutionService
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

    def probe_mesh_point(self, point: dict, probe_config: dict | None = None, progress_callback=None) -> dict:
        if progress_callback is not None:
            progress_callback("POINT_MOVE_XY", {"x_mm": point.get("x_machine"), "y_mm": point.get("y_machine")})
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


class StatefulMeshRuntime:
    """Stateful mesh runtime used to exercise worker lifecycle without hardware."""

    def __init__(self, *, refresh_fails: bool = False, serial_stale: bool = False, unexpected: bool = False, block: bool = False) -> None:
        self.position = {"x": 0.0, "y": 0.0, "z": 10.0}
        self.refresh_fails = refresh_fails
        self.serial_stale = serial_stale
        self.unexpected = unexpected
        self.block = block
        self.entered = threading.Event()
        self.release = threading.Event()
        self.refreshes = 0
        self.calls: list[int] = []
        self.transitions: list[str] = []
        self.movement_lock = False

    def snapshot(self) -> dict:
        return {
            "state": "MESH_PROBING", "position": dict(self.position), "homed_axes": "xyz",
            "last_command_text": self.transitions[-1] if self.transitions else None,
            "telemetry_age_s": 999.0, "serial_age_s": 0.01,
            "safety": {"telemetry_recent": False, "serial_recent": not self.serial_stale},
        }

    def refresh_observed_state(self) -> dict:
        self.refreshes += 1
        if self.refresh_fails:
            raise RuntimeError("Moonraker HTTP timeout")
        current = self.snapshot()
        current["telemetry_age_s"] = 0.0
        current["safety"] = {"telemetry_recent": True, "serial_recent": not self.serial_stale}
        return current

    def probe_mesh_point(self, point: dict, probe_config: dict | None = None, progress_callback=None) -> dict:
        if self.movement_lock:
            raise RuntimeError("movement lock leaked")
        self.movement_lock = True
        try:
            self.entered.set()
            if self.block:
                self.release.wait(1.0)
            if self.unexpected:
                raise RuntimeError("unexpected probe failure")
            for state, detail in (
                ("POINT_MOVE_SAFE_Z", {"safe_z_mm": 12.0}),
                ("POINT_CONFIRM_SAFE_Z", {"observed_z_mm": 12.0}),
                ("POINT_VERIFY_PROBE_OPEN", {"probe_raw": False, "probe_filtered": False, "last_packet_age_s": 0.01}),
                ("POINT_MOVE_XY", {"x_mm": point["x_machine"], "y_mm": point["y_machine"]}),
                ("POINT_CONFIRM_XY", {}),
                ("POINT_LOWER_STEP", {"step_mm": 0.1, "feed_mm_min": 120.0}),
                ("POINT_CONTACT_DETECTED", {"z_mm": 9.9}),
                ("POINT_RETRACT", {"retract_mm": 1.0}),
            ):
                self.transitions.append(state)
                if progress_callback:
                    progress_callback(state, detail)
            self.position.update({"x": float(point["x_machine"]), "y": float(point["y_machine"]), "z": 10.9})
            self.calls.append(int(point["index"]))
            return {"z_measured": 9.9, "duration_s": 0.001}
        finally:
            self.movement_lock = False


class BlockingMeshRuntime(StatefulMeshRuntime):
    def __init__(self, *, block_point_index: int = 0, fail_on_cancel: bool = False) -> None:
        super().__init__()
        self.block_point_index = block_point_index
        self.fail_on_cancel = fail_on_cancel
        self.cancel_calls = 0
        self.cancelled = threading.Event()

    def cancel_operation(self) -> None:
        self.cancel_calls += 1
        self.cancelled.set()
        self.release.set()

    def probe_mesh_point(self, point: dict, probe_config: dict | None = None, progress_callback=None) -> dict:
        point_index = int(point["index"])
        if point_index != self.block_point_index:
            return super().probe_mesh_point(point, probe_config=probe_config, progress_callback=progress_callback)
        if self.movement_lock:
            raise RuntimeError("movement lock leaked")
        self.movement_lock = True
        try:
            self.entered.set()
            if progress_callback:
                progress_callback("POINT_MOVE_XY", {"x_mm": point["x_machine"], "y_mm": point["y_machine"]})
            while not self.release.wait(0.01):
                if self.cancelled.is_set():
                    break
            if self.cancelled.is_set() and self.fail_on_cancel:
                raise RuntimeError("cancelled by operator")
            self.transitions.append("POINT_CONTACT_DETECTED")
            if progress_callback:
                progress_callback("POINT_CONTACT_DETECTED", {"z_mm": 9.9})
            self.position.update({"x": float(point["x_machine"]), "y": float(point["y_machine"]), "z": 10.9})
            self.calls.append(point_index)
            return {"z_measured": 9.9, "duration_s": 0.001}
        finally:
            self.movement_lock = False


class WatchdogMeshRuntime(StatefulMeshRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.cancel_calls = 0
        self.cancelled = threading.Event()
        self.hang_started = threading.Event()

    def cancel_operation(self) -> None:
        self.cancel_calls += 1
        self.cancelled.set()

    def probe_mesh_point(self, point: dict, probe_config: dict | None = None, progress_callback=None) -> dict:
        point_index = int(point["index"])
        if point_index == 1:
            return super().probe_mesh_point(point, probe_config=probe_config, progress_callback=progress_callback)
        self.hang_started.set()
        while not self.cancelled.wait(0.01):
            pass
        raise RuntimeError("watchdog cancelled probe")


class CadenceMeshRuntime(StatefulMeshRuntime):
    def __init__(self, *, lower_steps: int = 100) -> None:
        super().__init__()
        self.lower_steps = lower_steps
        self.step_started_at: list[float] = []
        self.step_completed_at: list[float] = []

    def probe_mesh_point(self, point: dict, probe_config: dict | None = None, progress_callback=None) -> dict:
        if self.movement_lock:
            raise RuntimeError("movement lock leaked")
        self.movement_lock = True
        try:
            self.entered.set()
            states = (
                ("POINT_MOVE_SAFE_Z", {"safe_z_mm": 12.0}),
                ("POINT_CONFIRM_SAFE_Z", {"observed_z_mm": 12.0}),
                ("POINT_VERIFY_PROBE_OPEN", {"probe_raw": False, "probe_filtered": False, "last_packet_age_s": 0.01}),
                ("POINT_MOVE_XY", {"x_mm": point["x_machine"], "y_mm": point["y_machine"]}),
                ("POINT_CONFIRM_XY", {"x_mm": point["x_machine"], "y_mm": point["y_machine"]}),
                ("POINT_DESCENT_STARTED", {"probe_step_mm": 0.1, "probe_feed_mm_min": 120.0, "retract_mm": 1.0}),
            )
            for state, detail in states:
                self.transitions.append(state)
                if progress_callback:
                    progress_callback(state, detail)
            for step_index in range(self.lower_steps):
                command_started_at = time.monotonic()
                self.step_started_at.append(command_started_at)
                if progress_callback:
                    progress_callback("POINT_LOWER_STEP", {"step_mm": 0.1, "feed_mm_min": 120.0, "command_started_at": command_started_at})
                command_completed_at = time.monotonic()
                self.step_completed_at.append(command_completed_at)
                if progress_callback:
                    progress_callback(
                        "POINT_CONFIRM_STEP",
                        {
                            "step_mm": 0.1,
                            "z_mm": 10.0 - ((step_index + 1) * 0.1),
                            "command_started_at": command_started_at,
                            "command_completed_at": command_completed_at,
                            "command_duration_s": command_completed_at - command_started_at,
                        },
                    )
            if progress_callback:
                progress_callback("POINT_CONTACT_DETECTED", {"z_mm": 0.0})
                progress_callback("POINT_RETRACT", {"retract_mm": 1.0, "feed_mm_min": 120.0})
                retract_started_at = time.monotonic()
                retract_completed_at = time.monotonic()
                progress_callback(
                    "POINT_CONFIRM_RETRACT",
                    {
                        "retract_mm": 1.0,
                        "z_mm": 1.0,
                        "command_started_at": retract_started_at,
                        "command_completed_at": retract_completed_at,
                        "command_duration_s": retract_completed_at - retract_started_at,
                    },
                )
                progress_callback("POINT_VERIFY_PROBE_OPEN_AFTER_RETRACT", {"probe_raw": False, "probe_filtered": False, "last_packet_age_s": 0.01})
            self.calls.append(int(point["index"]))
            self.position.update({"x": float(point["x_machine"]), "y": float(point["y_machine"]), "z": 1.0})
            return {"z_measured": 0.0, "duration_s": 0.001}
        finally:
            self.movement_lock = False


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

    def test_telemetry_reports_live_and_disconnected_states(self) -> None:
        machine = MachineState(position=MachinePosition(0, 0, 0), x_limits=AxisLimits(0, 100), y_limits=AxisLimits(0, 100), z_limits=AxisLimits(0, 50), homed_axes="xyz", max_velocity=100, max_accel=500)
        telemetry = MoonrakerTelemetry("ws://example", machine)
        states: list[str] = []
        telemetry.set_state_callback(states.append)
        telemetry._process_message({"method": "notify_status_update", "params": [{"motion_report": {"live_position": [1, 2, 3], "live_velocity": 0}}]})
        telemetry.stop()
        self.assertEqual(states, ["LIVE", "DISCONNECTED"])
        self.assertAlmostEqual(machine.get_motion_snapshot()["live_position"]["z"], 3.0)

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
            self.assertIn("X30.00000 Y30.00000", generated)
            self.assertIn("Z0.91000", generated)
            self.assertNotIn("X10.00000 Y0.00000 Z-0.09000", generated)
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
            self.assertIn("G1 X100.00000 Y200.00000 Z6.00000", generated)
            self.assertIn("X120.00000 Y220.00000 Z0.96000", generated)

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
            self.assertIn("X120.00000 Y200.00000 Z0.92000", generated)
            self.assertIn("X130.00000 Y200.00000", generated)
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

    def test_legacy_compensation_preserves_flatcam_modal_feeds_when_subdividing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository = JsonProjectRepository(Path(temp))
            project_service = ProjectService(repository)
            project = project_service.create_project(
                nombre="Feeds FlatCAM",
                ancho_mm=40,
                alto_mm=20,
                espesor_mm=1.6,
            )
            operation = project_service.add_operation(
                project_id=project.id,
                nombre="Aislamiento",
                tipo="aislamiento",
                cara="superior",
                orden=0,
                herramienta="V-bit",
            )
            original = (
                "G21\nG90\n"
                "G1 X10 Y0 Z-0.100 F120\n"
                "G1 X20 Y0 Z-0.100 F300\n"
                "G1 X30 Y0 Z-0.100\n"
            )
            project_service.upload_operation_gcode(
                project_id=project.id,
                operation_id=operation.id,
                filename="flatcam-feeds.nc",
                content=original,
            )
            project_service.analyze_operation(project.id, operation.id)
            map_service = PhysicalMapService(repository)
            plan = map_service.capture_reference_and_plan(
                project_id=project.id,
                operation_id=operation.id,
                machine_origin_x=100.0,
                machine_origin_y=200.0,
                reference_z=1.0,
                machine_position={"x_mm": 100.0, "y_mm": 200.0, "z_mm": 1.0},
                homed_axes="xyz",
                machine_label="test",
                session_id="session",
                config=PhysicalMeshConfig(
                    grid_mode="manual",
                    rows=2,
                    columns=2,
                    edge_margin_left_mm=0.0,
                    edge_margin_right_mm=0.0,
                    edge_margin_bottom_mm=0.0,
                    edge_margin_top_mm=0.0,
                ),
            )
            for point in plan["points"]:
                plan = map_service.record_point(
                    project_id=project.id,
                    map_id=plan["map_id"],
                    point_index=int(point["index"]),
                    z_measured=1.0,
                )

            result = CompensatedGCodeService(repository, map_service).generate(
                project.id,
                operation.id,
                max_segment_mm=2.0,
            )
            generated = repository.read_project_file(project.id, result["relative_path"])
            feeds_by_line: dict[int, set[float]] = {}
            for entry in result["metadata"]["movement_trace"]:
                feeds_by_line.setdefault(int(entry["line_number"]), set()).add(float(entry["feed_mm_min"]))

            self.assertEqual(feeds_by_line[3], {120.0})
            self.assertEqual(feeds_by_line[4], {300.0})
            self.assertEqual(feeds_by_line[5], {300.0})
            self.assertGreater(generated.count("F300.000"), 2)
            self.assertNotIn("F600", generated)
            self.assertNotIn("F1800", generated)

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
            self.assertTrue(worker.wait_until_idle(timeout_s=3.0))
            paused = service.get_by_id(project.id, plan["map_id"])
            self.assertEqual(paused["status"], "MESH_PAUSED")
            self.assertEqual(paused["points"][1]["status"], "FAILED")
            self.assertEqual(runtime.calls, [1])
            retried = service.retry_failed_point(project_id=project.id, map_id=plan["map_id"], point_index=1)
            self.assertEqual(retried["points"][1]["status"], "RETRY_REQUIRED")
            self.assertIsNone(retried["points"][1]["error"])
            self.assertTrue(retried["points"][1]["last_error"])
            worker.resume(project_id=project.id, map_id=plan["map_id"], runtime=runtime)
            self.assertTrue(worker.wait_until_idle(timeout_s=3.0))
            completed = service.get_by_id(project.id, plan["map_id"])
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
            self.assertIn("POINT_FAILED", {event.get("next_state") for event in log["events"]})
            self.assertIn("POINT_RETRY", {event.get("next_state") for event in log["events"]})
            self.assertIn("POINT_COMPLETE", {event.get("next_state") for event in log["events"]})
            self.assertTrue(worker.wait_until_idle(timeout_s=1.0))

    def test_mesh_worker_refreshes_http_then_completes_stateful_2x2(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository, _project_service, project, operation = self._physical_project(temp)
            service = PhysicalMapService(repository)
            plan = service.capture_reference_and_plan(
                project_id=project.id, operation_id=operation.id, machine_origin_x=0.0, machine_origin_y=0.0,
                reference_z=10.0, machine_position={"x_mm": 0.0, "y_mm": 0.0, "z_mm": 10.0},
                homed_axes="xyz", machine_label="test", session_id="session",
                config=PhysicalMeshConfig(grid_mode="manual", rows=2, columns=2, edge_margin_left_mm=0.0, edge_margin_right_mm=0.0, edge_margin_bottom_mm=0.0, edge_margin_top_mm=0.0, probe_step_mm=0.1, probe_feed_mm_min=120.0, retract_mm=1.0),
            )
            worker = MeshExecutionService(service)
            runtime = StatefulMeshRuntime()
            worker.start_all(project_id=project.id, map_id=plan["map_id"], runtime=runtime)
            self.assertTrue(worker.wait_until_idle(timeout_s=3.0))
            completed = service.get_by_id(project.id, plan["map_id"])
            self.assertEqual(completed["status"], "MESH_COMPLETE")
            self.assertEqual(runtime.calls, [1, 2, 3])
            self.assertGreaterEqual(runtime.refreshes, 3)
            self.assertIn("POINT_LOWER_STEP", runtime.transitions)
            self.assertFalse(runtime.movement_lock)
            self.assertFalse(completed["execution"]["worker_active"])

    def test_mesh_worker_pauses_when_http_observation_fails_and_releases_worker(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository, _project_service, project, operation = self._physical_project(temp)
            service = PhysicalMapService(repository)
            plan = service.capture_reference_and_plan(
                project_id=project.id, operation_id=operation.id, machine_origin_x=0.0, machine_origin_y=0.0,
                reference_z=10.0, machine_position={"x_mm": 0.0, "y_mm": 0.0, "z_mm": 10.0},
                homed_axes="xyz", machine_label="test", session_id="session",
                config=PhysicalMeshConfig(grid_mode="manual", rows=2, columns=2, edge_margin_left_mm=0.0, edge_margin_right_mm=0.0, edge_margin_bottom_mm=0.0, edge_margin_top_mm=0.0),
            )
            worker = MeshExecutionService(service)
            runtime = StatefulMeshRuntime(refresh_fails=True)
            worker.start_all(project_id=project.id, map_id=plan["map_id"], runtime=runtime)
            self.assertTrue(worker.wait_until_idle(timeout_s=3.0))
            paused = service.get_by_id(project.id, plan["map_id"])
            self.assertEqual(paused["status"], "MESH_PAUSED")
            self.assertFalse(paused["execution"]["worker_active"])
            self.assertIn("Moonraker HTTP timeout", str(paused["execution"]))

    def test_mesh_worker_rejects_stale_arduino_before_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository, _project_service, project, operation = self._physical_project(temp)
            service = PhysicalMapService(repository)
            plan = service.capture_reference_and_plan(project_id=project.id, operation_id=operation.id, machine_origin_x=0.0, machine_origin_y=0.0, reference_z=10.0, machine_position={"x_mm": 0.0, "y_mm": 0.0, "z_mm": 10.0}, homed_axes="xyz", machine_label="test", session_id="session", config=PhysicalMeshConfig(grid_mode="manual", rows=2, columns=2, edge_margin_left_mm=0.0, edge_margin_right_mm=0.0, edge_margin_bottom_mm=0.0, edge_margin_top_mm=0.0))
            worker = MeshExecutionService(service)
            runtime = StatefulMeshRuntime(serial_stale=True)
            worker.start_all(project_id=project.id, map_id=plan["map_id"], runtime=runtime)
            self.assertTrue(worker.wait_until_idle(timeout_s=3.0))
            paused = service.get_by_id(project.id, plan["map_id"])
            self.assertEqual(paused["status"], "MESH_PAUSED")
            self.assertEqual(runtime.calls, [])
            self.assertIn("Arduino obsoleto", str(paused["execution"]))

    def test_mesh_worker_prevents_double_start_and_releases_after_unexpected_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository, _project_service, project, operation = self._physical_project(temp)
            service = PhysicalMapService(repository)
            plan = service.capture_reference_and_plan(project_id=project.id, operation_id=operation.id, machine_origin_x=0.0, machine_origin_y=0.0, reference_z=10.0, machine_position={"x_mm": 0.0, "y_mm": 0.0, "z_mm": 10.0}, homed_axes="xyz", machine_label="test", session_id="session", config=PhysicalMeshConfig(grid_mode="manual", rows=2, columns=2, edge_margin_left_mm=0.0, edge_margin_right_mm=0.0, edge_margin_bottom_mm=0.0, edge_margin_top_mm=0.0))
            worker = MeshExecutionService(service)
            runtime = StatefulMeshRuntime(block=True)
            worker.start_all(project_id=project.id, map_id=plan["map_id"], runtime=runtime)
            self.assertTrue(runtime.entered.wait(1.0))
            with self.assertRaises(ApplicationError):
                worker.start_all(project_id=project.id, map_id=plan["map_id"], runtime=runtime)
            runtime.release.set()
            self.assertTrue(worker.wait_until_idle(timeout_s=3.0))
            plan2 = service.repeat_measurement(project_id=project.id, map_id=plan["map_id"])
            bad = StatefulMeshRuntime(unexpected=True)
            worker.start_all(project_id=project.id, map_id=plan2["map_id"], runtime=bad)
            self.assertTrue(worker.wait_until_idle(timeout_s=3.0))
            failed = service.get_by_id(project.id, plan2["map_id"])
            self.assertEqual(failed["status"], "MESH_PAUSED")
            self.assertFalse(failed["execution"]["worker_active"])
            self.assertFalse(bad.movement_lock)

    def test_mesh_worker_watchdog_cancels_hung_point_and_preserves_measured_progress(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository, _project_service, project, operation = self._physical_project(temp)
            service = PhysicalMapService(repository)
            plan = service.capture_reference_and_plan(
                project_id=project.id,
                operation_id=operation.id,
                machine_origin_x=0.0,
                machine_origin_y=0.0,
                reference_z=10.0,
                machine_position={"x_mm": 0.0, "y_mm": 0.0, "z_mm": 10.0},
                homed_axes="xyz",
                machine_label="test",
                session_id="session",
                config=PhysicalMeshConfig(grid_mode="manual", rows=2, columns=2, edge_margin_left_mm=0.0, edge_margin_right_mm=0.0, edge_margin_bottom_mm=0.0, edge_margin_top_mm=0.0),
            )
            worker = MeshExecutionService(service, point_watchdog_timeout_s=0.05, point_watchdog_poll_s=0.01, point_watchdog_grace_s=0.01)
            runtime = WatchdogMeshRuntime()
            worker.start_all(project_id=project.id, map_id=plan["map_id"], runtime=runtime)
            self.assertTrue(runtime.hang_started.wait(1.0))
            self.assertTrue(worker.wait_until_idle(timeout_s=3.0))
            paused = service.get_by_id(project.id, plan["map_id"])
            self.assertEqual(paused["status"], "MESH_PAUSED")
            self.assertFalse(paused["execution"]["worker_active"])
            self.assertGreaterEqual(runtime.cancel_calls, 1)
            self.assertEqual(paused["points"][1]["status"], "MEASURED")
            self.assertEqual(runtime.calls, [1])
            self.assertIn("Timeout sin progreso", str(paused["execution"].get("last_error")))

    def test_mesh_worker_limits_persistence_writes_during_100_lower_steps(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository, _project_service, project, operation = self._physical_project(temp)
            service = PhysicalMapService(repository)
            plan = service.capture_reference_and_plan(
                project_id=project.id,
                operation_id=operation.id,
                machine_origin_x=0.0,
                machine_origin_y=0.0,
                reference_z=10.0,
                machine_position={"x_mm": 0.0, "y_mm": 0.0, "z_mm": 10.0},
                homed_axes="xyz",
                machine_label="test",
                session_id="session",
                config=PhysicalMeshConfig(grid_mode="manual", rows=2, columns=2, edge_margin_left_mm=0.0, edge_margin_right_mm=0.0, edge_margin_bottom_mm=0.0, edge_margin_top_mm=0.0),
            )
            worker = MeshExecutionService(service)
            runtime = CadenceMeshRuntime(lower_steps=100)
            point = service.next_pending_point(project.id, plan["map_id"])
            save_count = 0
            original_save = repository.save_height_map_payload

            def counting_save(project_id: str, operation_id: str, payload: dict):
                nonlocal save_count
                save_count += 1
                return original_save(project_id, operation_id, payload)

            with patch.object(repository, "save_height_map_payload", side_effect=counting_save):
                worker._probe_one_point(project.id, plan["map_id"], runtime, point, probe_config=plan.get("probe_config"))

            updated = service.get_by_id(project.id, plan["map_id"])
            self.assertEqual(runtime.calls, [1])
            self.assertEqual(updated["points"][1]["status"], "MEASURED")
            self.assertEqual(updated["execution"]["step_counter"], 100)
            self.assertLessEqual(save_count, 12)

    def test_mesh_worker_heartbeat_ignores_slow_persistence_during_lower_steps(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository, _project_service, project, operation = self._physical_project(temp)
            service = PhysicalMapService(repository)
            plan = service.capture_reference_and_plan(
                project_id=project.id,
                operation_id=operation.id,
                machine_origin_x=0.0,
                machine_origin_y=0.0,
                reference_z=10.0,
                machine_position={"x_mm": 0.0, "y_mm": 0.0, "z_mm": 10.0},
                homed_axes="xyz",
                machine_label="test",
                session_id="session",
                config=PhysicalMeshConfig(grid_mode="manual", rows=2, columns=2, edge_margin_left_mm=0.0, edge_margin_right_mm=0.0, edge_margin_bottom_mm=0.0, edge_margin_top_mm=0.0),
            )
            worker = MeshExecutionService(service, point_watchdog_timeout_s=0.05, point_watchdog_poll_s=0.005, point_watchdog_grace_s=0.005)
            runtime = CadenceMeshRuntime(lower_steps=5)
            point = service.next_pending_point(project.id, plan["map_id"])
            original_update = service.update_execution_state

            def slow_update(**kwargs):
                time.sleep(0.2)
                return original_update(**kwargs)

            with patch.object(service, "update_execution_state", side_effect=slow_update):
                worker._probe_one_point(project.id, plan["map_id"], runtime, point, probe_config=plan.get("probe_config"))

            updated = service.get_by_id(project.id, plan["map_id"])
            self.assertEqual(updated["points"][1]["status"], "MEASURED")
            self.assertEqual(updated["execution"]["step_counter"], 5)
            self.assertEqual(runtime.calls, [1])
            self.assertGreaterEqual(len(runtime.step_started_at), 5)
            self.assertTrue(
                all((later - earlier) < 0.05 for earlier, later in zip(runtime.step_started_at, runtime.step_started_at[1:])),
                runtime.step_started_at,
            )

    def test_pause_before_start_is_idempotent_and_keeps_first_pending_point(self) -> None:
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
                config=PhysicalMeshConfig(grid_mode="manual", rows=2, columns=2),
            )
            worker = MeshExecutionService(service)
            first = worker.pause(project_id=project.id, map_id=plan["map_id"])
            second = worker.pause(project_id=project.id, map_id=plan["map_id"])
            self.assertEqual(first["status"], "MESH_PAUSED")
            self.assertEqual(second["status"], "MESH_PAUSED")
            self.assertEqual(second["execution"]["next_point_index"], 1)
            self.assertTrue(second["execution"]["pause_requested"])

    def test_pause_requested_during_point_stops_before_next_point(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository, _project_service, project, operation = self._physical_project(temp)
            service = PhysicalMapService(repository)
            plan = service.capture_reference_and_plan(
                project_id=project.id,
                operation_id=operation.id,
                machine_origin_x=0.0,
                machine_origin_y=0.0,
                reference_z=10.0,
                machine_position={"x_mm": 0.0, "y_mm": 0.0, "z_mm": 10.0},
                homed_axes="xyz",
                machine_label="test",
                session_id="session",
                config=PhysicalMeshConfig(grid_mode="manual", rows=2, columns=2),
            )
            worker = MeshExecutionService(service)
            runtime = BlockingMeshRuntime(block_point_index=1)
            worker.start_all(project_id=project.id, map_id=plan["map_id"], runtime=runtime)
            self.assertTrue(runtime.entered.wait(1.0))
            pausing = worker.pause(project_id=project.id, map_id=plan["map_id"])
            self.assertEqual(pausing["execution"]["point_state"], "MESH_PAUSING")
            runtime.release.set()
            self.assertTrue(worker.wait_until_idle(timeout_s=3.0))
            paused = service.get_by_id(project.id, plan["map_id"])
            self.assertEqual(paused["status"], "MESH_PAUSED")
            self.assertEqual(paused["execution"]["next_point_index"], 2)
            self.assertEqual(runtime.calls, [1])
            self.assertFalse(paused["execution"]["worker_active"])

    def test_pause_requested_between_points_stops_before_next_probe(self) -> None:
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
                config=PhysicalMeshConfig(grid_mode="manual", rows=2, columns=2),
            )
            worker = MeshExecutionService(service)
            runtime = FakeMeshRuntime()
            original_record = service.record_point
            persisted_first = threading.Event()
            release = threading.Event()

            def blocking_record(*, project_id: str, map_id: str, point_index: int, **kwargs):
                updated = original_record(project_id=project_id, map_id=map_id, point_index=point_index, **kwargs)
                if point_index == 1 and not persisted_first.is_set():
                    persisted_first.set()
                    self.assertTrue(release.wait(1.0))
                return updated

            with patch.object(service, "record_point", side_effect=blocking_record):
                worker.start_all(project_id=project.id, map_id=plan["map_id"], runtime=runtime)
                self.assertTrue(persisted_first.wait(1.0))
                pausing = worker.pause(project_id=project.id, map_id=plan["map_id"])
                self.assertTrue(pausing["execution"]["pause_requested"])
                release.set()
                self.assertTrue(worker.wait_until_idle(timeout_s=3.0))

            paused = service.get_by_id(project.id, plan["map_id"])
            self.assertEqual(paused["status"], "MESH_PAUSED")
            self.assertEqual(runtime.calls, [1])
            self.assertEqual(paused["execution"]["next_point_index"], 2)

    def test_cancel_before_start_and_after_repeat_is_idempotent(self) -> None:
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
                config=PhysicalMeshConfig(grid_mode="manual", rows=2, columns=2),
            )
            worker = MeshExecutionService(service)
            runtime = FakeMeshRuntime()
            first = worker.cancel(project_id=project.id, map_id=plan["map_id"], runtime=runtime)
            second = worker.cancel(project_id=project.id, map_id=plan["map_id"], runtime=runtime)
            self.assertEqual(first["status"], "CANCELLED")
            self.assertEqual(second["status"], "CANCELLED")
            self.assertEqual(second["execution"]["next_point_index"], 1)
            self.assertEqual(sum(1 for point in second["points"] if point.get("role") != "REFERENCE" and point["status"] == "MEASURED"), 0)

    def test_cancel_requested_during_point_finishes_worker_without_next_probe(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository, _project_service, project, operation = self._physical_project(temp)
            service = PhysicalMapService(repository)
            plan = service.capture_reference_and_plan(
                project_id=project.id,
                operation_id=operation.id,
                machine_origin_x=0.0,
                machine_origin_y=0.0,
                reference_z=10.0,
                machine_position={"x_mm": 0.0, "y_mm": 0.0, "z_mm": 10.0},
                homed_axes="xyz",
                machine_label="test",
                session_id="session",
                config=PhysicalMeshConfig(grid_mode="manual", rows=2, columns=2),
            )
            worker = MeshExecutionService(service)
            runtime = BlockingMeshRuntime(block_point_index=1, fail_on_cancel=True)
            worker.start_all(project_id=project.id, map_id=plan["map_id"], runtime=runtime)
            self.assertTrue(runtime.entered.wait(1.0))
            cancelling = worker.cancel(project_id=project.id, map_id=plan["map_id"], runtime=runtime)
            self.assertEqual(cancelling["execution"]["point_state"], "MESH_CANCELING")
            self.assertTrue(worker.wait_until_idle(timeout_s=3.0))
            cancelled = service.get_by_id(project.id, plan["map_id"])
            self.assertEqual(cancelled["status"], "CANCELLED")
            self.assertEqual(runtime.calls, [])
            self.assertGreaterEqual(runtime.cancel_calls, 1)
            self.assertEqual(cancelled["execution"]["next_point_index"], 1)
            again = worker.cancel(project_id=project.id, map_id=plan["map_id"], runtime=runtime)
            self.assertEqual(again["status"], "CANCELLED")

    def test_cancel_requested_between_points_stops_before_next_probe(self) -> None:
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
                config=PhysicalMeshConfig(grid_mode="manual", rows=2, columns=2),
            )
            worker = MeshExecutionService(service)
            runtime = FakeMeshRuntime()
            original_record = service.record_point
            persisted_first = threading.Event()
            release = threading.Event()

            def blocking_record(*, project_id: str, map_id: str, point_index: int, **kwargs):
                updated = original_record(project_id=project_id, map_id=map_id, point_index=point_index, **kwargs)
                if point_index == 1 and not persisted_first.is_set():
                    persisted_first.set()
                    self.assertTrue(release.wait(1.0))
                return updated

            with patch.object(service, "record_point", side_effect=blocking_record):
                worker.start_all(project_id=project.id, map_id=plan["map_id"], runtime=runtime)
                self.assertTrue(persisted_first.wait(1.0))
                cancelling = worker.cancel(project_id=project.id, map_id=plan["map_id"], runtime=runtime)
                self.assertTrue(cancelling["execution"]["cancel_requested"])
                release.set()
                self.assertTrue(worker.wait_until_idle(timeout_s=3.0))

            cancelled = service.get_by_id(project.id, plan["map_id"])
            self.assertEqual(cancelled["status"], "CANCELLED")
            self.assertEqual(runtime.calls, [1])
            self.assertEqual(cancelled["execution"]["next_point_index"], 2)

    def test_post_persist_error_does_not_repeat_confirmed_point(self) -> None:
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
                config=PhysicalMeshConfig(grid_mode="manual", rows=2, columns=2),
            )
            worker = MeshExecutionService(service)
            runtime = FakeMeshRuntime()
            original_update = service.update_execution_state
            failed_once = {"value": False}

            def flaky_update(*, project_id: str, map_id: str, **kwargs):
                if not failed_once["value"] and kwargs.get("point_state") == "POINT_COMPLETE" and kwargs.get("point_index") == 1:
                    failed_once["value"] = True
                    raise RuntimeError("post-persist failure")
                return original_update(project_id=project_id, map_id=map_id, **kwargs)

            with patch.object(service, "update_execution_state", side_effect=flaky_update):
                worker.start_all(project_id=project.id, map_id=plan["map_id"], runtime=runtime)
                self.assertTrue(worker.wait_until_idle(timeout_s=3.0))

            completed = service.get_by_id(project.id, plan["map_id"])
            self.assertEqual(completed["status"], "MESH_COMPLETE")
            self.assertEqual(runtime.calls.count(1), 1)
            self.assertEqual(sum(1 for point in completed["points"] if point.get("role") != "REFERENCE" and point["status"] == "MEASURED"), 4)

    def test_reconcile_missing_worker_pauses_map_and_allows_resume(self) -> None:
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
                config=PhysicalMeshConfig(grid_mode="manual", rows=2, columns=2),
            )
            worker = MeshExecutionService(service)
            service.mark_status(
                project_id=project.id,
                map_id=plan["map_id"],
                status="MESH_PROBING",
                worker_active=True,
                point_state="POINT_MOVE_XY",
                last_event="Worker perdido durante la ejecución.",
                metadata={
                    "phase": "move_xy",
                    "last_error": "Worker desaparecido durante el sondeo.",
                    "last_progress_at": plan["execution"]["last_progress_at"],
                },
            )
            reconciled = worker.reconcile_map_state(project_id=project.id, map_id=plan["map_id"])
            self.assertEqual(reconciled["status"], "MESH_PAUSED")
            self.assertFalse(reconciled["execution"]["worker_active"])
            self.assertIn("worker", str(reconciled["execution"]["last_error"]).lower())
            resumed = worker.resume(project_id=project.id, map_id=plan["map_id"], runtime=FakeMeshRuntime())
            self.assertEqual(resumed["status"], "MESH_PROBING")
            self.assertTrue(worker.wait_until_idle(timeout_s=3.0))
            completed = service.get_by_id(project.id, plan["map_id"])
            self.assertEqual(completed["status"], "MESH_COMPLETE")

    def test_resume_rejects_changed_context_and_active_worker(self) -> None:
        def make_plan(temp: str):
            repository, project_service, project, operation = self._physical_project(temp)
            service = PhysicalMapService(repository)
            plan = service.capture_reference_and_plan(
                project_id=project.id,
                operation_id=operation.id,
                machine_origin_x=5.0,
                machine_origin_y=6.0,
                reference_z=1.5,
                machine_position={"x_mm": 5.0, "y_mm": 6.0, "z_mm": 1.5},
                homed_axes="xyz",
                machine_label="test",
                session_id="session",
                config=PhysicalMeshConfig(grid_mode="manual", rows=2, columns=2),
            )
            return repository, project_service, project, operation, service, plan

        with tempfile.TemporaryDirectory() as temp:
            repository, project_service, project, operation, service, plan = make_plan(temp)
            other_project = project_service.create_project(nombre="PCB-2", ancho_mm=60, alto_mm=60, espesor_mm=1.6)
            with self.assertRaises(Exception):
                service.validate_resume_context(project_id=other_project.id, map_id=plan["map_id"])

        for label in ("setup", "face", "grid", "placement", "origin", "reference", "active-map"):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as temp:
                repository, _project_service, project, operation, service, plan = make_plan(temp)
                if label in {"setup", "face", "grid"}:
                    payload = service.get_by_id(project.id, plan["map_id"])
                    if label == "setup":
                        payload["setup_id"] = "setup-mutated"
                    elif label == "face":
                        payload["face"] = "inferior"
                    else:
                        payload["mesh_config"] = dict(payload.get("mesh_config") or {}, rows=3)
                    repository.save_height_map_payload(project.id, plan["map_id"], payload)
                else:
                    loaded = repository.load_project(project.id)
                    setup = loaded.get_setup(operation.setup_id)
                    if label == "placement":
                        updated_setup = replace(setup, placement_revision="placement-mutated")
                    elif label == "origin":
                        updated_setup = replace(setup, preparacion=replace(setup.preparacion, origen_trabajo=CoordinateReference(x_mm=99.0, y_mm=99.0)))
                    elif label == "reference":
                        updated_setup = replace(setup, preparacion=replace(setup.preparacion, referencia_z=CoordinateReference(x_mm=99.0, y_mm=99.0, z_mm=2.0)))
                    else:
                        replanned = service.capture_reference_and_plan(
                            project_id=project.id,
                            operation_id=operation.id,
                            machine_origin_x=5.0,
                            machine_origin_y=6.0,
                            reference_z=1.5,
                            machine_position={"x_mm": 5.0, "y_mm": 6.0, "z_mm": 1.5},
                            homed_axes="xyz",
                            machine_label="test",
                            session_id="session",
                            config=PhysicalMeshConfig(grid_mode="manual", rows=3, columns=3),
                        )
                        self.assertNotEqual(replanned["map_id"], plan["map_id"])
                        with self.assertRaises(ApplicationError):
                            service.validate_resume_context(project_id=project.id, map_id=plan["map_id"])
                        continue
                    repository.save_project(loaded.replace_setup(updated_setup))
                with self.assertRaises(ApplicationError):
                    service.validate_resume_context(project_id=project.id, map_id=plan["map_id"])

        with tempfile.TemporaryDirectory() as temp:
            repository, _project_service, project, operation, service, plan = make_plan(temp)
            worker = MeshExecutionService(service)
            runtime = BlockingMeshRuntime(block_point_index=1)
            worker.start_all(project_id=project.id, map_id=plan["map_id"], runtime=runtime)
            self.assertTrue(runtime.entered.wait(1.0))
            with self.assertRaises(ApplicationError):
                worker.resume(project_id=project.id, map_id=plan["map_id"], runtime=runtime)
            runtime.release.set()
            self.assertTrue(worker.wait_until_idle(timeout_s=3.0))

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
            self.assertTrue(session["lista_para_compensacion"])
            self.assertEqual(session["bloqueos_compensacion"], [])
            generator = CompensatedGCodeService(repository, service)
            with self.assertRaises(ApplicationError) as error:
                generator.generate(project.id, operation.id)
            message = str(error.exception)
            self.assertIn("Mapa insuficiente", message)
            self.assertIn("Primer punto fuera", message)
            self.assertIn("X=0.000", message)
            self.assertIn("distancia=14.142 mm", message)

    def _physical_project(self, temp: str):
        repository = JsonProjectRepository(Path(temp))
        project_service = ProjectService(repository)
        project = project_service.create_project(nombre="PCB", ancho_mm=60, alto_mm=60, espesor_mm=1.6)
        operation = project_service.add_operation(project_id=project.id, nombre="Aislamiento", tipo="aislamiento", cara="superior", orden=0, tool_id="tool-v", herramienta="V-bit")
        project_service.upload_operation_gcode(project_id=project.id, operation_id=operation.id, filename="job.nc", content="G21\nG90\nG1 X0 Y0\nG1 X10 Y10\n")
        project_service.analyze_operation(project.id, operation.id)
        return repository, project_service, project, operation

    def _persist_saved_reference(self, repository: JsonProjectRepository, project_id: str, setup_id: str) -> None:
        project = repository.load_project(project_id)
        setup = project.get_setup(setup_id)
        updated_setup = replace(
            setup,
            preparacion=replace(
                setup.preparacion,
                origen_trabajo=CoordinateReference(
                    x_mm=5.0,
                    y_mm=6.0,
                    fuente="MEASURED",
                    maquina="klipper",
                    homed_axes="xyz",
                    sesion="physical-session",
                ),
                referencia_z=CoordinateReference(
                    x_mm=5.0,
                    y_mm=6.0,
                    z_mm=1.5,
                    fuente="MEASURED",
                    maquina="klipper",
                    homed_axes="xyz",
                    sesion="physical-session",
                ),
            ),
        )
        repository.save_project(project.replace_setup(updated_setup))

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

    def test_preview_from_saved_reference_is_pure_and_matches_equivalent_planned_map(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository, _project_service, project, operation = self._physical_project(temp)
            self._persist_saved_reference(repository, project.id, operation.setup_id)
            service = PhysicalMapService(repository)
            config = PhysicalMeshConfig(
                grid_mode="manual",
                rows=2,
                columns=2,
                edge_margin_left_mm=2.0,
                edge_margin_right_mm=2.0,
                edge_margin_bottom_mm=2.0,
                edge_margin_top_mm=2.0,
                safe_z_mm=10.0,
            )
            save_counts = {"project": 0, "map": 0}
            original_save_project = repository.save_project
            original_save_map = repository.save_height_map_payload

            def counting_save_project(project_payload):
                save_counts["project"] += 1
                return original_save_project(project_payload)

            def counting_save_map(project_id: str, map_id: str, payload: dict):
                save_counts["map"] += 1
                return original_save_map(project_id, map_id, payload)

            with patch.object(repository, "save_project", side_effect=counting_save_project), patch.object(repository, "save_height_map_payload", side_effect=counting_save_map):
                preview = service.preview_mesh_from_saved_reference(
                    project_id=project.id,
                    operation_id=operation.id,
                    config=config,
                )

            self.assertEqual(preview["status"], "MESH_PREVIEW")
            self.assertEqual(preview["source"], "PREVIEW")
            self.assertEqual(save_counts["project"], 0)
            self.assertEqual(save_counts["map"], 0)
            self.assertEqual(service.history(project_id=project.id, operation_id=operation.id), [])
            self.assertIsNone(repository.load_project(project.id).get_setup(operation.setup_id).active_map_id)

            planned = service.plan_from_saved_reference(
                project_id=project.id,
                operation_id=operation.id,
                config=config,
            )

            self.assertEqual(planned["status"], "MESH_PLANNED")
            self.assertEqual(preview["point_count"], planned["point_count"])
            self.assertEqual(preview["grid"], planned["grid"])
            self.assertEqual(preview["local_region"], planned["local_region"])
            self.assertEqual(preview["machine_region"], planned["machine_region"])
            self.assertEqual(
                [(point["x_local"], point["y_local"], point["x_machine"], point["y_machine"]) for point in preview["points"]],
                [(point["x_local"], point["y_local"], point["x_machine"], point["y_machine"]) for point in planned["points"] if point.get("role") != "REFERENCE"],
            )
            self.assertEqual(repository.load_project(project.id).get_setup(operation.setup_id).active_map_id, planned["map_id"])
            self.assertEqual(len(service.history(project_id=project.id, operation_id=operation.id)), 1)

    def test_mesh_fingerprint_accepts_origin_grid_promoted_to_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository, _project_service, project, operation = self._physical_project(temp)
            self._persist_saved_reference(repository, project.id, operation.setup_id)
            service = PhysicalMapService(repository)
            config = PhysicalMeshConfig(
                grid_mode="manual",
                rows=2,
                columns=2,
                edge_margin_left_mm=0.0,
                edge_margin_right_mm=0.0,
                edge_margin_bottom_mm=0.0,
                edge_margin_top_mm=0.0,
                safe_z_mm=10.0,
            )

            preview = service.preview_mesh_from_saved_reference(project_id=project.id, operation_id=operation.id, config=config)
            planned = service.plan_from_saved_reference(project_id=project.id, operation_id=operation.id, config=config)

            self.assertEqual(preview["points"][0]["role"], "GRID")
            self.assertEqual((preview["points"][0]["x_local"], preview["points"][0]["y_local"]), (0.0, 0.0))
            self.assertEqual(planned["points"][0]["role"], "REFERENCE")
            self.assertEqual((planned["points"][0]["x_local"], planned["points"][0]["y_local"]), (0.0, 0.0))
            self.assertEqual(mesh_configuration_fingerprint(preview), mesh_configuration_fingerprint(planned))
            self.assertEqual(mesh_geometry_fingerprint(preview), mesh_geometry_fingerprint(planned))
            self.assertEqual(canonical_mesh_geometry(preview)["point_count"], 4)
            self.assertEqual(canonical_mesh_geometry(planned)["point_count"], 4)

    def test_mesh_fingerprint_accepts_same_geometry_without_origin_coincidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository, _project_service, project, operation = self._physical_project(temp)
            self._persist_saved_reference(repository, project.id, operation.setup_id)
            service = PhysicalMapService(repository)
            config = PhysicalMeshConfig(
                grid_mode="manual",
                rows=2,
                columns=2,
                edge_margin_left_mm=2.0,
                edge_margin_right_mm=2.0,
                edge_margin_bottom_mm=2.0,
                edge_margin_top_mm=2.0,
                safe_z_mm=10.0,
            )

            preview = service.preview_mesh_from_saved_reference(project_id=project.id, operation_id=operation.id, config=config)
            planned = service.plan_from_saved_reference(project_id=project.id, operation_id=operation.id, config=config)

            self.assertTrue(all(point.get("role") == "GRID" for point in preview["points"]))
            self.assertEqual(planned["points"][0]["role"], "REFERENCE")
            self.assertLess(int(planned["points"][0]["row"]), 0)
            self.assertLess(int(planned["points"][0]["column"]), 0)
            self.assertEqual(mesh_configuration_fingerprint(preview), mesh_configuration_fingerprint(planned))
            self.assertEqual(mesh_geometry_fingerprint(preview), mesh_geometry_fingerprint(planned))

    def test_mesh_fingerprint_rejects_real_rows_margin_exclusion_and_profile_differences(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository, _project_service, project, operation = self._physical_project(temp)
            self._persist_saved_reference(repository, project.id, operation.setup_id)
            service = PhysicalMapService(repository)
            base = service.preview_mesh_from_saved_reference(
                project_id=project.id,
                operation_id=operation.id,
                config=PhysicalMeshConfig(
                    grid_mode="manual",
                    rows=2,
                    columns=2,
                    edge_margin_left_mm=2.0,
                    edge_margin_right_mm=2.0,
                    edge_margin_bottom_mm=2.0,
                    edge_margin_top_mm=2.0,
                    safe_z_mm=10.0,
                ),
            )
            variants = {
                "rows": service.preview_mesh_from_saved_reference(
                    project_id=project.id,
                    operation_id=operation.id,
                    config=PhysicalMeshConfig(
                        grid_mode="manual",
                        rows=3,
                        columns=2,
                        edge_margin_left_mm=2.0,
                        edge_margin_right_mm=2.0,
                        edge_margin_bottom_mm=2.0,
                        edge_margin_top_mm=2.0,
                        safe_z_mm=10.0,
                    ),
                ),
                "margins": service.preview_mesh_from_saved_reference(
                    project_id=project.id,
                    operation_id=operation.id,
                    config=PhysicalMeshConfig(
                        grid_mode="manual",
                        rows=2,
                        columns=2,
                        edge_margin_left_mm=4.0,
                        edge_margin_right_mm=2.0,
                        edge_margin_bottom_mm=2.0,
                        edge_margin_top_mm=2.0,
                        safe_z_mm=10.0,
                    ),
                ),
                "exclusions": service.preview_mesh_from_saved_reference(
                    project_id=project.id,
                    operation_id=operation.id,
                    config=PhysicalMeshConfig(
                        grid_mode="manual",
                        rows=2,
                        columns=2,
                        edge_margin_left_mm=2.0,
                        edge_margin_right_mm=2.0,
                        edge_margin_bottom_mm=2.0,
                        edge_margin_top_mm=2.0,
                        exclusions=(PhysicalExclusion(id="keepout", name="Pinza", shape="rectangle", enabled=True, x_min_mm=8.0, x_max_mm=12.0, y_min_mm=6.0, y_max_mm=9.0),),
                        safe_z_mm=10.0,
                    ),
                ),
                "profile": service.preview_mesh_from_saved_reference(
                    project_id=project.id,
                    operation_id=operation.id,
                    config=PhysicalMeshConfig(
                        grid_mode="manual",
                        rows=2,
                        columns=2,
                        edge_margin_left_mm=2.0,
                        edge_margin_right_mm=2.0,
                        edge_margin_bottom_mm=2.0,
                        edge_margin_top_mm=2.0,
                        safe_z_mm=12.0,
                        probe_profile_source="map_override",
                        probe_step_mm=0.03,
                        probe_feed_mm_min=75.0,
                        retract_mm=0.8,
                    ),
                ),
            }

            for label, variant in variants.items():
                with self.subTest(label=label):
                    self.assertNotEqual(mesh_configuration_fingerprint(base), mesh_configuration_fingerprint(variant))
                    self.assertNotEqual(mesh_geometry_fingerprint(base), mesh_geometry_fingerprint(variant))

    def test_mesh_fingerprint_ignores_representational_only_noise(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository, _project_service, project, operation = self._physical_project(temp)
            self._persist_saved_reference(repository, project.id, operation.setup_id)
            service = PhysicalMapService(repository)
            planned = service.plan_from_saved_reference(
                project_id=project.id,
                operation_id=operation.id,
                config=PhysicalMeshConfig(
                    grid_mode="manual",
                    rows=2,
                    columns=2,
                    edge_margin_left_mm=0.0,
                    edge_margin_right_mm=0.0,
                    edge_margin_bottom_mm=0.0,
                    edge_margin_top_mm=0.0,
                    safe_z_mm=10.0,
                ),
            )
            noisy = dict(planned)
            noisy["map_id"] = "measured/noisy-map"
            noisy["created_at"] = "2026-08-01T00:00:00+00:00"
            noisy["updated_at"] = "2026-08-01T01:00:00+00:00"
            noisy["preview_id"] = "preview/noisy"
            noisy_points = []
            for reverse_index, point in enumerate(reversed(planned["points"])):
                updated = dict(point)
                updated["index"] = reverse_index + 40
                if updated.get("x_local") == 0.0 and updated.get("y_local") == 0.0:
                    updated["role"] = "GRID"
                    updated["status"] = "MEASURED"
                noisy_points.append(updated)
            noisy["points"] = noisy_points

            self.assertEqual(mesh_configuration_fingerprint(planned), mesh_configuration_fingerprint(noisy))
            self.assertEqual(mesh_geometry_fingerprint(planned), mesh_geometry_fingerprint(noisy))

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

    def test_preview_parameters_regenerate_spacing_total_and_serpentine_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository, _project_service, project, operation = self._physical_project(temp)
            service = PhysicalMapService(repository)
            first = service.preview_mesh(
                project_id=project.id,
                operation_id=operation.id,
                config=PhysicalMeshConfig(grid_mode="manual", rows=3, columns=3, edge_margin_left_mm=2.0, edge_margin_right_mm=2.0, edge_margin_bottom_mm=2.0, edge_margin_top_mm=2.0),
            )
            second = service.preview_mesh(
                project_id=project.id,
                operation_id=operation.id,
                config=PhysicalMeshConfig(grid_mode="manual", rows=2, columns=4, edge_margin_left_mm=4.0, edge_margin_right_mm=4.0, edge_margin_bottom_mm=2.0, edge_margin_top_mm=2.0),
            )
            first_points = [(point["x_local"], point["y_local"]) for point in first["points"]]
            self.assertEqual(first["point_count"], 9)
            self.assertAlmostEqual(first["grid"]["dx_mm"], 28.0)
            self.assertAlmostEqual(first["grid"]["dy_mm"], 28.0)
            self.assertEqual(first_points, [(2.0, 2.0), (30.0, 2.0), (58.0, 2.0), (58.0, 30.0), (30.0, 30.0), (2.0, 30.0), (2.0, 58.0), (30.0, 58.0), (58.0, 58.0)])
            self.assertEqual(second["point_count"], 8)
            self.assertAlmostEqual(second["grid"]["dx_mm"], (60.0 - 4.0 - 4.0) / 3.0)
            self.assertAlmostEqual(second["grid"]["dy_mm"], 56.0)
            self.assertNotEqual(first_points, [(point["x_local"], point["y_local"]) for point in second["points"]])

    def test_cancelled_map_does_not_block_new_plan_from_saved_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            repository, _project_service, project, operation = self._physical_project(temp)
            self._persist_saved_reference(repository, project.id, operation.setup_id)
            service = PhysicalMapService(repository)
            config = PhysicalMeshConfig(
                grid_mode="manual",
                rows=2,
                columns=2,
                edge_margin_left_mm=2.0,
                edge_margin_right_mm=2.0,
                edge_margin_bottom_mm=2.0,
                edge_margin_top_mm=2.0,
            )
            first = service.plan_from_saved_reference(
                project_id=project.id,
                operation_id=operation.id,
                config=config,
            )
            cancelled = service.mark_status(
                project_id=project.id,
                map_id=first["map_id"],
                status="CANCELLED",
                worker_active=False,
                point_state="CANCELLED",
                last_event="Cancelado por el operador.",
                metadata={"cancel_requested": True},
            )

            second = service.plan_from_saved_reference(
                project_id=project.id,
                operation_id=operation.id,
                config=config,
            )

            self.assertEqual(cancelled["status"], "CANCELLED")
            self.assertNotEqual(first["map_id"], second["map_id"])
            self.assertEqual(second["status"], "MESH_PLANNED")
            self.assertTrue(all(point["status"] in {"PENDING", "EXCLUDED"} for point in second["points"] if point.get("role") != "REFERENCE"))
            self.assertEqual(second["execution"].get("step_counter", 0), 0)
            self.assertFalse(second["execution"]["worker_active"])
            archived_first = service.get_by_id(project.id, first["map_id"])
            self.assertIsNotNone(archived_first["archived_at"])
            self.assertEqual(repository.load_project(project.id).get_setup(operation.setup_id).active_map_id, second["map_id"])
            history = service.history(project_id=project.id, operation_id=operation.id)
            self.assertGreaterEqual(len(history), 2)
            self.assertTrue(any(item["map_id"] == first["map_id"] and item["archived_at"] for item in history))
            self.assertTrue(any(item["map_id"] == second["map_id"] and item["active"] for item in history))

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
