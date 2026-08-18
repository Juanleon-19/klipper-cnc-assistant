from __future__ import annotations

import asyncio
import json
import re
import tempfile
from pathlib import Path
import threading
import time
import unittest
from dataclasses import replace
from unittest.mock import patch

from klipper_cnc_assistant.application.physical_map_service import PhysicalMapService, PhysicalMeshConfig
from klipper_cnc_assistant.execution import MeshExecutionService
from klipper_cnc_assistant.application.services import ProjectService
from klipper_cnc_assistant.input.command_mapper import CommandMapper, ControllerCommand
from klipper_cnc_assistant.input.serial_driver import ControllerPacket
from klipper_cnc_assistant.machine.config import MachineMode, MachineRuntimeConfig
from klipper_cnc_assistant.machine.runtime import MachineRuntime, MachineRuntimeError
import klipper_cnc_assistant.machine.runtime as runtime_module
from klipper_cnc_assistant.machine.state import AxisLimits, MachinePosition, MachineState
from klipper_cnc_assistant.moonraker.client import MoonrakerError, MoonrakerTimeout
from klipper_cnc_assistant.storage import JsonProjectRepository


def config(mode: MachineMode = MachineMode.SIMULATED, **overrides) -> MachineRuntimeConfig:
    cfg = MachineRuntimeConfig(
        mode=mode,
        auto_connect=False,
        moonraker_url=None,
        moonraker_ws=None,
        serial_port=None,
        serial_baudrate=115200,
        safe_z_mm=10.0,
        reference_prep_z_mm=115.0,
        z_clearance_feed_mm_min=180.0,
        reference_approach_z_feed_mm_min=180.0,
        tool_change_z_mm=115.0,
        tool_change_clearance_z_mm=115.0,
        tool_change_work_z_mm=115.0,
        tool_change_z_positive_up=True,
        tool_change_z_feed_mm_min=180.0,
        tool_change_x_mm=0.0,
        tool_change_y_mm=0.0,
        moonraker_request_timeout_s=0.1,
        home_timeout_s=120.0,
        telemetry_fresh_timeout_s=2.0,
        serial_fresh_timeout_s=2.0,
        serial_startup_delay_s=0.0,
        settle_tolerance_mm=0.05,
        velocity_tolerance_mm_s=0.02,
        move_timeout_s=180.0,
        move_minimum_timeout_s=180.0,
        move_timeout_factor=1.5,
        move_settle_margin_s=10.0,
        no_progress_timeout_s=60.0,
        settle_timeout_s=5.0,
        probe_open_stable_ms=50.0,
        stable_samples=2,
        probe_step_mm=0.05,
        probe_lower_speed_mm_s=1.0,
        probe_retract_mm=1.0,
        probe_retract_speed_mm_s=2.0,
    )
    return replace(cfg, **overrides) if overrides else cfg


class FakeDiagnostics:
    thread_active = True

    def snapshot(self, *, now: float) -> dict[str, object]:
        return {
            "port": "/dev/null",
            "baudrate": 115200,
            "open": True,
            "thread_active": True,
            "bytes_received": 1,
            "packets_complete": 1,
            "valid_packets": 1,
            "invalid_packets": 0,
            "checksum_errors": 0,
            "sync_drops": 0,
            "partial_packets": 0,
            "reconnects": 0,
            "last_byte_age_s": 0,
            "last_valid_packet_age_s": 0,
            "last_invalid_packet_age_s": None,
            "last_exception": None,
        }


class FakeThread:
    def is_alive(self) -> bool:
        return True


class MotionClient:
    def __init__(self, machine: MachineState) -> None:
        self.machine = machine
        self.scripts: list[str] = []

    def send_gcode(self, script: str, *, timeout: float | None = None) -> dict[str, object]:
        self.scripts.append(script)
        if "G28" in script:
            self.machine.update_toolhead(position=(0, 0, 0), homed_axes="xyz")
            self.machine.update_motion(live_position=(0, 0, 0), live_velocity=0)
            self.machine.update_gcode_move(gcode_position=(0, 0, 0), position=(0, 0, 0), absolute_coordinates=True, homing_origin=(0, 0, 0))
            return {"result": "ok"}
        snapshot = self.machine.get_motion_snapshot()
        x = float(snapshot["x"])
        y = float(snapshot["y"])
        z = float(snapshot["z"])
        match_x = re.search(r"\bX(-?\d+(?:\.\d+)?)", script)
        match_y = re.search(r"\bY(-?\d+(?:\.\d+)?)", script)
        match_z = re.search(r"\bZ(-?\d+(?:\.\d+)?)", script)
        if match_x:
            x = float(match_x.group(1))
        if match_y:
            y = float(match_y.group(1))
        if match_z:
            z = float(match_z.group(1))
        self.machine.update_toolhead(position=(x, y, z), homed_axes=self.machine.homed_axes)
        self.machine.update_gcode_move(gcode_position=(x, y, z), position=(x, y, z), absolute_coordinates=True, homing_origin=(0.0, 0.0, 0.0))
        self.machine.update_motion(live_position=(x, y, z), live_velocity=0, source="websocket")
        return {"result": "ok"}


class TimeoutAfterCompletedMoveClient(MotionClient):
    def send_gcode(self, script: str, *, timeout: float | None = None) -> dict[str, object]:
        if "G28" in script:
            return super().send_gcode(script, timeout=timeout)
        super().send_gcode(script, timeout=timeout)
        raise MoonrakerTimeout("G-code request timed out: movimiento terminado")


class IncompleteMoveClient(MotionClient):
    def send_gcode(self, script: str, *, timeout: float | None = None) -> dict[str, object]:
        self.scripts.append(script)
        if "G28" in script:
            self.machine.update_toolhead(position=(0, 0, 0), homed_axes="xyz")
            self.machine.update_motion(live_position=(0, 0, 0), live_velocity=0)
        return {"result": "ok"}


class DelayedMoveClient(MotionClient):
    def send_gcode(self, script: str, *, timeout: float | None = None) -> dict[str, object]:
        self.scripts.append(script)
        if "G28" in script:
            self.machine.update_toolhead(position=(0, 0, 0), homed_axes="xyz")
            self.machine.update_motion(live_position=(0, 0, 0), live_velocity=0)
            self.machine.update_gcode_move(gcode_position=(0, 0, 0), position=(0, 0, 0), absolute_coordinates=True, homing_origin=(0, 0, 0))
            return {"result": "ok"}
        snapshot = self.machine.get_motion_snapshot()
        x = float(snapshot["x"])
        y = float(snapshot["y"])
        z = float(snapshot["z"])
        match_x = re.search(r"\bX(-?\d+(?:\.\d+)?)", script)
        match_y = re.search(r"\bY(-?\d+(?:\.\d+)?)", script)
        match_z = re.search(r"\bZ(-?\d+(?:\.\d+)?)", script)
        if match_x:
            x = float(match_x.group(1))
        if match_y:
            y = float(match_y.group(1))
        if match_z:
            z = float(match_z.group(1))
        self.machine.update_toolhead(position=(x, y, z), homed_axes=self.machine.homed_axes)
        self.machine.update_gcode_move(gcode_position=(x, y, z), position=(x, y, z), absolute_coordinates=True, homing_origin=(0, 0, 0))
        self.machine.update_motion(live_velocity=10)

        def arrive() -> None:
            self.machine.update_motion(live_position=(x, y, z), live_velocity=0)

        threading.Timer(0.12, arrive).start()
        return {"result": "ok"}


class SlowZClient(MotionClient):
    def __init__(self, machine: MachineState, *, speed_mm_s: float, wrong_direction: bool = False) -> None:
        super().__init__(machine)
        self.speed_mm_s = speed_mm_s
        self.wrong_direction = wrong_direction
        self.z_target: float | None = None
        self.xy_target: tuple[float, float] | None = None

    def send_gcode(self, script: str, *, timeout: float | None = None) -> dict[str, object]:
        self.scripts.append(script)
        if "G28" in script:
            self.machine.update_toolhead(position=(0, 0, 0), homed_axes="xyz")
            self.machine.update_motion(live_position=(0, 0, 0), live_velocity=0)
            self.machine.update_gcode_move(gcode_position=(0, 0, 0), position=(0, 0, 0), absolute_coordinates=True, homing_origin=(0, 0, 0))
            return {"result": "ok"}
        snapshot = self.machine.get_motion_snapshot()
        match_z = re.search(r"\bZ(-?\d+(?:\.\d+)?)", script)
        match_x = re.search(r"\bX(-?\d+(?:\.\d+)?)", script)
        match_y = re.search(r"\bY(-?\d+(?:\.\d+)?)", script)
        if match_z and not match_x and not match_y:
            self.z_target = float(match_z.group(1))
            self.machine.update_motion(live_velocity=-abs(self.speed_mm_s))
            return {"result": "ok"}
        if match_x and match_y:
            x = float(match_x.group(1))
            y = float(match_y.group(1))
            self.xy_target = (x, y)
            self.machine.update_motion(live_position=(x, y, float(snapshot["z"])), live_velocity=0)
            return {"result": "ok"}
        return super().send_gcode(script, timeout=timeout)

    def advance(self, seconds: float) -> None:
        if self.z_target is None:
            return
        snapshot = self.machine.get_motion_snapshot()
        current_z = float(snapshot["z"])
        direction = -1 if self.wrong_direction else (1 if self.z_target >= current_z else -1)
        next_z = current_z + direction * self.speed_mm_s * seconds
        if not self.wrong_direction:
            if direction > 0:
                next_z = min(next_z, self.z_target)
            else:
                next_z = max(next_z, self.z_target)
            velocity = 0 if abs(next_z - self.z_target) <= 1e-9 else -abs(self.speed_mm_s)
        else:
            velocity = -abs(self.speed_mm_s)
        self.machine.update_motion(live_position=(float(snapshot["x"]), float(snapshot["y"]), next_z), live_velocity=velocity)


class CommandedTargetSlowLiveZClient(SlowZClient):
    def send_gcode(self, script: str, *, timeout: float | None = None) -> dict[str, object]:
        result = super().send_gcode(script, timeout=timeout)
        match_z = re.search(r"\bZ(-?\d+(?:\.\d+)?)", script)
        match_x = re.search(r"\bX(-?\d+(?:\.\d+)?)", script)
        match_y = re.search(r"\bY(-?\d+(?:\.\d+)?)", script)
        if match_z and not match_x and not match_y:
            # Klipper can report the commanded destination while motion_report.live_position is still moving.
            self.machine.update_toolhead(position=(0, 0, float(match_z.group(1))))
        return result


class RejectedZClient(MotionClient):
    def send_gcode(self, script: str, *, timeout: float | None = None) -> dict[str, object]:
        self.scripts.append(script)
        if "G28" in script:
            self.machine.update_toolhead(position=(0, 0, 0), homed_axes="xyz")
            self.machine.update_motion(live_position=(0, 0, 0), live_velocity=0)
            self.machine.update_gcode_move(gcode_position=(0, 0, 0), position=(0, 0, 0), absolute_coordinates=True, homing_origin=(0, 0, 0))
            return {"result": "ok"}
        if "Z115" in script:
            raise MoonrakerError("Move rejected by Klipper")
        return super().send_gcode(script, timeout=timeout)


class QueryFallbackClient(MotionClient):
    def __init__(self, machine: MachineState) -> None:
        super().__init__(machine)
        self.z_command_sent = False

    def send_gcode(self, script: str, *, timeout: float | None = None) -> dict[str, object]:
        self.scripts.append(script)
        if "G28" in script:
            self.machine.update_toolhead(position=(0, 0, 0), homed_axes="xyz")
            self.machine.update_motion(live_position=(0, 0, 0), live_velocity=0)
            self.machine.update_gcode_move(gcode_position=(0, 0, 0), position=(0, 0, 0), absolute_coordinates=True, homing_origin=(0, 0, 0))
            return {"result": "ok"}
        if "Z115" in script:
            self.z_command_sent = True
            self.machine.update_toolhead(position=(0, 0, 115))
            self.machine.update_motion(live_position=(0, 0, 0), live_velocity=0)
            return {"result": "ok"}
        return super().send_gcode(script, timeout=timeout)


class SampleSequenceZClient(MotionClient):
    def __init__(self, machine: MachineState, z_samples: list[float], *, sources: list[str] | None = None, commanded_z: float | None = None) -> None:
        super().__init__(machine)
        self.z_samples = z_samples
        self.sources = sources or ["websocket"] * len(z_samples)
        self.commanded_z = commanded_z
        self.sample_index = 0
        self.z_active = False

    def send_gcode(self, script: str, *, timeout: float | None = None) -> dict[str, object]:
        self.scripts.append(script)
        if "G28" in script:
            self.machine.update_toolhead(position=(0, 0, 0), homed_axes="xyz")
            self.machine.update_motion(live_position=(0, 0, 0), live_velocity=0, source="websocket")
            return {"result": "ok"}
        snapshot = self.machine.get_motion_snapshot()
        match_z = re.search(r"\bZ(-?\d+(?:\.\d+)?)", script)
        match_x = re.search(r"\bX(-?\d+(?:\.\d+)?)", script)
        match_y = re.search(r"\bY(-?\d+(?:\.\d+)?)", script)
        if match_z and not match_x and not match_y:
            self.z_active = True
            self.sample_index = 0
            commanded_z = float(match_z.group(1)) if self.commanded_z is None else self.commanded_z
            self.machine.update_toolhead(position=(0, 0, commanded_z))
            self.machine.update_motion(
                live_position=(float(snapshot["x"]), float(snapshot["y"]), self.z_samples[0]),
                live_velocity=0 if len(self.z_samples) == 1 else 1.0,
                source=self.sources[0],
            )
            return {"result": "ok"}
        if match_x and match_y:
            x = float(match_x.group(1))
            y = float(match_y.group(1))
            self.machine.update_motion(live_position=(x, y, float(snapshot["z"])), live_velocity=0, source=self.sources[min(self.sample_index, len(self.sources) - 1)])
            return {"result": "ok"}
        return super().send_gcode(script, timeout=timeout)

    def advance(self, _seconds: float) -> None:
        if not self.z_active or self.sample_index >= len(self.z_samples) - 1:
            return
        self.sample_index += 1
        snapshot = self.machine.get_motion_snapshot()
        z_value = self.z_samples[self.sample_index]
        velocity = 0 if self.sample_index >= len(self.z_samples) - 1 else 1.0
        self.machine.update_motion(
            live_position=(float(snapshot["x"]), float(snapshot["y"]), z_value),
            live_velocity=velocity,
            source=self.sources[self.sample_index],
        )


class ProbeJogSpy:
    def __init__(self, runtime, machine: MachineState) -> None:
        self.runtime = runtime
        self.machine = machine
        self.calls: list[dict[str, float | str]] = []

    def move_relative(self, axis, distance, speed):
        snapshot = self.machine.get_motion_snapshot()
        target = float(snapshot[axis]) + float(distance)
        self.calls.append({"axis": axis, "distance": float(distance), "speed": float(speed), "target": target})
        self.machine.update_motion(live_position=(float(snapshot["x"]), float(snapshot["y"]), target), live_velocity=0, source="websocket")
        triggered = distance < 0
        with self.runtime._lock:
            self.runtime._last_command = ControllerCommand(probe_triggered=triggered)
            self.runtime._probe_raw = triggered
            self.runtime._probe_filtered = triggered
            self.runtime._probe_filtered_since = time.monotonic() - 0.1
            self.runtime._last_packet_at = time.monotonic()
            self.runtime._packet_sequence += 1
        return {"axis": axis, "current_position": float(snapshot[axis]), "target": target, "speed": float(speed)}


class FakeClock:
    def __init__(self, updater=None) -> None:
        self.now = 0.0
        self.updater = updater

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds
        if self.updater is not None:
            self.updater(seconds)


class ReadyInfoClient(MotionClient):
    def get_server_info(self) -> dict[str, str]:
        return {"klippy_state": "ready"}


class PassiveTelemetry:
    def __init__(self, *_args, **_kwargs) -> None:
        self._stop = threading.Event()
        self._snapshot_callback = None

    def set_snapshot_callback(self, callback) -> None:
        self._snapshot_callback = callback

    async def run(self) -> None:
        if self._snapshot_callback is not None:
            self._snapshot_callback({
                "state": "CONNECTED",
                "last_message_at": time.monotonic(),
                "last_error": None,
                "reconnects": 0,
            })
        while not self._stop.is_set():
            await asyncio.sleep(0.01)

    def stop(self) -> None:
        self._stop.set()


class ReconnectableDiagnostics:
    def __init__(self, port: str, baudrate: int) -> None:
        self.port = port
        self.baudrate = baudrate
        self.open = False
        self.thread_active = False
        self.last_exception = None

    def snapshot(self, *, now: float) -> dict[str, object]:
        return {
            "port": self.port,
            "baudrate": self.baudrate,
            "open": self.open,
            "thread_active": self.thread_active,
            "bytes_received": 0,
            "packets_complete": 0,
            "valid_packets": 0,
            "invalid_packets": 0,
            "checksum_errors": 0,
            "sync_drops": 0,
            "partial_packets": 0,
            "reconnects": 0,
            "last_byte_age_s": None,
            "last_valid_packet_age_s": None,
            "last_invalid_packet_age_s": None,
            "last_exception": self.last_exception,
        }


class ReconnectableDriver:
    def __init__(self, port: str, baudrate: int, *_args, **_kwargs) -> None:
        self.port = port
        self.baudrate = baudrate
        self.closed = False
        self.diagnostics = ReconnectableDiagnostics(port, baudrate)

    def open(self) -> None:
        self.diagnostics.open = True

    def close(self) -> None:
        self.closed = True
        self.diagnostics.open = False

    def read_packet(self) -> ControllerPacket:
        self.diagnostics.thread_active = True
        if self.closed:
            raise RuntimeError("driver closed")
        time.sleep(0.05)
        if self.closed:
            raise RuntimeError("driver closed")
        return ControllerPacket(direction="CENTER", joystick_button=False, external_button=False, probe=False, x=512, y=512)


def physical_runtime_with_machine(machine: MachineState, cfg: MachineRuntimeConfig | None = None) -> tuple[MachineRuntime, MotionClient]:
    cfg = cfg or config(MachineMode.PHYSICAL)
    runtime = MachineRuntime(cfg, discovery=lambda _client: machine)
    client = MotionClient(machine)
    runtime._client = client
    runtime._machine = machine
    runtime._driver = type("Driver", (), {"diagnostics": FakeDiagnostics()})()
    runtime._serial_thread = FakeThread()
    runtime._last_packet_at = time.monotonic()
    runtime._telemetry_state = "LIVE"
    runtime._last_websocket_message_at = time.monotonic()
    machine.update_motion(live_position=machine.position.as_tuple(), live_velocity=0.0, source="websocket")
    machine.update_gcode_move(gcode_position=machine.position.as_tuple(), position=machine.position.as_tuple(), absolute_coordinates=True, homing_origin=(0.0, 0.0, 0.0))
    runtime._probe_raw_since = time.monotonic() - 0.1
    runtime._probe_filtered_since = time.monotonic() - 0.1
    runtime._last_telemetry_at = time.monotonic()
    return runtime, client


class MachineRuntimeTest(unittest.TestCase):
    def test_live_probe_open_is_separate_from_historical_trigger_failure(self) -> None:
        machine = MachineState(position=MachinePosition(0, 0, 10), x_limits=AxisLimits(0, 100), y_limits=AxisLimits(0, 100), z_limits=AxisLimits(0, 200), homed_axes="xyz", max_velocity=100, max_accel=500, live_velocity=0)
        runtime, _client = physical_runtime_with_machine(machine)
        runtime._last_error = "Sonda no OPEN fresca y estable: raw=True"
        runtime._last_probe_failure = {"raw_value_at_failure": True, "filtered_at_failure": True}
        packet = ControllerPacket(direction="CENTER", joystick_button=False, external_button=False, probe=False, x=512, y=512)
        runtime._handle_controller_packet(packet, CommandMapper().map(packet))

        probe = runtime.get_live_probe_state(require_fresh=True, require_stable=False)
        snapshot = runtime.snapshot()

        self.assertEqual(probe["display_state"], "OPEN")
        self.assertFalse(probe["raw_value"])
        self.assertFalse(probe["filtered_triggered"])
        self.assertIsNone(snapshot["last_error"])
        self.assertTrue(snapshot["last_probe_failure"]["filtered_at_failure"])

    def test_live_probe_stability_uses_logical_transition_not_last_packet(self) -> None:
        machine = MachineState(position=MachinePosition(0, 0, 10), x_limits=AxisLimits(0, 100), y_limits=AxisLimits(0, 100), z_limits=AxisLimits(0, 200), homed_axes="xyz", max_velocity=100, max_accel=500, live_velocity=0)
        runtime, _client = physical_runtime_with_machine(machine)
        clock = FakeClock()
        runtime._reset_live_probe_stability()
        original_time = runtime_module.time
        runtime_module.time = clock
        try:
            def receive(probe: bool) -> dict[str, object]:
                packet = ControllerPacket(direction="CENTER", joystick_button=False, external_button=False, probe=probe, x=512, y=512)
                runtime._handle_controller_packet(packet, CommandMapper().map(packet))
                return runtime.get_live_probe_state()

            first_open = receive(False)
            self.assertEqual(first_open["stable_for_ms"], 0.0)
            open_changed_at = first_open["changed_at_monotonic"]
            for _ in range(10):
                clock.now += 0.02
                open_state = receive(False)
            self.assertAlmostEqual(float(open_state["stable_for_ms"]), 200.0)
            self.assertEqual(open_state["changed_at_monotonic"], open_changed_at)

            clock.now += 0.01
            triggered = receive(True)
            self.assertEqual(triggered["stable_for_ms"], 0.0)
            for _ in range(10):
                clock.now += 0.02
                triggered = receive(True)
            self.assertAlmostEqual(float(triggered["stable_for_ms"]), 200.0)

            clock.now += 0.01
            released = receive(False)
            self.assertEqual(released["stable_for_ms"], 0.0)
            for _ in range(10):
                clock.now += 0.02
                released = receive(False)
            self.assertAlmostEqual(float(released["stable_for_ms"]), 200.0)
            runtime._last_probe_failure = {"filtered_at_failure": True}
            self.assertAlmostEqual(float(runtime.get_live_probe_state()["stable_for_ms"]), 200.0)
            runtime._reset_live_probe_stability()
            reset = receive(False)
            self.assertEqual(reset["stable_for_ms"], 0.0)
            clock.now += 0.2
            after_reconnect = receive(False)
            self.assertAlmostEqual(float(after_reconnect["stable_for_ms"]), 200.0)
            self.assertAlmostEqual(float(after_reconnect["changed_at_monotonic"]), clock.now - 0.2)
        finally:
            runtime_module.time = original_time

    def test_probe_precheck_reports_each_condition_and_accepts_stable_open(self) -> None:
        machine = MachineState(position=MachinePosition(0, 0, 10), x_limits=AxisLimits(0, 100), y_limits=AxisLimits(0, 100), z_limits=AxisLimits(0, 200), homed_axes="xyz", max_velocity=100, max_accel=500, live_velocity=0)
        runtime, _client = physical_runtime_with_machine(machine, cfg=config(MachineMode.PHYSICAL, probe_open_stable_ms=2000.0))
        runtime._reset_live_probe_stability()
        clock = FakeClock()
        original_time = runtime_module.time
        runtime_module.time = clock
        try:
            packet = ControllerPacket(direction="CENTER", joystick_button=False, external_button=False, probe=False, x=512, y=512)
            runtime._handle_controller_packet(packet, CommandMapper().map(packet))
            with self.assertRaisesRegex(MachineRuntimeError, "stable_ok=False"):
                runtime._require_fresh_open_probe(after_sequence=0, stage="POINT_VERIFY_PROBE_OPEN")
            clock.now += 0.2
            runtime.config = replace(runtime.config, probe_open_stable_ms=50.0)
            detail = runtime._require_fresh_open_probe(after_sequence=0, stage="POINT_VERIFY_PROBE_OPEN_AFTER_RETRACT")
        finally:
            runtime_module.time = original_time
        self.assertTrue(detail["open_ok"])
        self.assertTrue(detail["fresh_ok"])
        self.assertTrue(detail["stable_ok"])
        self.assertEqual(detail["required_stable_ms"], 50.0)

    def test_stale_live_position_is_never_classified_live(self) -> None:
        machine = MachineState(position=MachinePosition(0, 0, 10), x_limits=AxisLimits(0, 100), y_limits=AxisLimits(0, 100), z_limits=AxisLimits(0, 200), homed_axes="xyz", max_velocity=100, max_accel=500, live_velocity=0)
        runtime, _client = physical_runtime_with_machine(machine)
        machine.update_motion(live_position=(0, 0, 10), live_velocity=0, source="websocket")
        machine.live_position_updated_at = time.monotonic() - 52.63
        runtime._telemetry_state = "LIVE"
        runtime._last_websocket_message_at = time.monotonic()
        self.assertEqual(runtime._telemetry_status(), "STALE")
        self.assertTrue(runtime._telemetry_is_stale(time.monotonic()))

    def test_mesh_retry_readiness_uses_live_open_probe_and_rejects_stale_position(self) -> None:
        machine = MachineState(position=MachinePosition(0, 0, 10), x_limits=AxisLimits(0, 100), y_limits=AxisLimits(0, 100), z_limits=AxisLimits(0, 200), homed_axes="xyz", max_velocity=100, max_accel=500, live_velocity=0)
        runtime, _client = physical_runtime_with_machine(machine)
        packet = ControllerPacket(direction="CENTER", joystick_button=False, external_button=False, probe=False, x=512, y=512)
        runtime._handle_controller_packet(packet, CommandMapper().map(packet))
        runtime._probe_filtered_since = time.monotonic() - 0.1
        machine.live_position = None
        machine.live_position_updated_at = time.monotonic() - 772
        with self.assertRaisesRegex(MachineRuntimeError, "posición Moonraker obsoleta"):
            runtime.mesh_retry_readiness()
        machine.update_motion(live_position=(0, 0, 10), live_velocity=0, source="websocket")
        readiness = runtime.mesh_retry_readiness()
        self.assertEqual(readiness["probe_state"], "OPEN")
        self.assertLess(readiness["position_age_ms"], 1000)

    def test_simulated_mode_never_constructs_physical_clients(self) -> None:
        def fail_client(_url: str):
            raise AssertionError("MoonrakerClient no debe construirse en modo simulado")

        runtime = MachineRuntime(config(), client_factory=fail_client)
        runtime.start()
        snapshot = runtime.snapshot()
        self.assertEqual(snapshot["mode"], "SIMULATED")
        self.assertEqual(snapshot["state"], "READY_FOR_HOME")
        with self.assertRaises(MachineRuntimeError):
            runtime.initialize(0.0)

    def test_physical_mode_requires_explicit_connection_settings(self) -> None:
        runtime = MachineRuntime(config(MachineMode.PHYSICAL))
        with self.assertRaisesRegex(MachineRuntimeError, "MOONRAKER_URL"):
            runtime.connect()

    def test_connect_cleans_up_after_early_http_failure(self) -> None:
        class FailingClient:
            def get_server_info(self) -> dict[str, str]:
                raise RuntimeError("Moonraker HTTP timeout")

        runtime = MachineRuntime(
            config(MachineMode.PHYSICAL, moonraker_url="http://moonraker.local", moonraker_ws="ws://moonraker.local/websocket", serial_port="/dev/ttyUSB0"),
            client_factory=lambda _url, timeout=None: FailingClient(),
        )

        with self.assertRaisesRegex(RuntimeError, "Moonraker HTTP timeout"):
            runtime.connect()

        snapshot = runtime.snapshot()
        self.assertEqual(snapshot["state"], "ERROR")
        self.assertFalse(snapshot["moonraker"]["http_connected"])
        self.assertEqual(snapshot["arduino"]["connection_state"], "DISCONNECTED")
        self.assertEqual(snapshot["moonraker"]["last_http_error"], "Moonraker HTTP timeout")

    def test_connect_keeps_moonraker_active_while_arduino_retries_and_recovers_later(self) -> None:
        machine = MachineState(
            position=MachinePosition(0, 0, 10),
            x_limits=AxisLimits(0, 100),
            y_limits=AxisLimits(0, 100),
            z_limits=AxisLimits(0, 200),
            homed_axes="xyz",
            max_velocity=100,
            max_accel=500,
            live_velocity=0,
        )
        client = ReadyInfoClient(machine)
        serial_available = threading.Event()
        runtime = MachineRuntime(
            config(MachineMode.PHYSICAL, moonraker_url="http://moonraker.local", moonraker_ws="ws://moonraker.local/websocket", serial_port="/dev/ttyUSB0"),
            client_factory=lambda _url, timeout=None: client,
            telemetry_factory=PassiveTelemetry,
            serial_factory=ReconnectableDriver,
            discovery=lambda _client: machine,
        )

        with patch("klipper_cnc_assistant.input.connection_manager.os.path.exists", side_effect=lambda _path: serial_available.is_set()), patch(
            "klipper_cnc_assistant.input.connection_manager.list_ports.comports",
            return_value=[],
        ):
            try:
                initial = runtime.connect()
                self.assertEqual(initial["state"], "DEGRADED")
                self.assertTrue(initial["moonraker"]["http_connected"])
                self.assertNotEqual(initial["arduino"]["connection_state"], "CONNECTED")

                serial_available.set()
                deadline = time.monotonic() + 2.0
                recovered = runtime.snapshot()
                while time.monotonic() < deadline:
                    recovered = runtime.snapshot()
                    if recovered["arduino"]["connection_state"] == "CONNECTED":
                        break
                    time.sleep(0.05)

                self.assertEqual(recovered["arduino"]["connection_state"], "CONNECTED")
                self.assertEqual(recovered["state"], "DIAGNOSTIC")
                self.assertTrue(recovered["moonraker"]["http_connected"])
                with runtime._lock:
                    self.assertFalse(runtime._manual_enabled)
                    self.assertFalse(runtime._ready_for_jog)
            finally:
                runtime.stop()

    def test_capture_reference_observation_rejects_stale_position(self) -> None:
        machine = MachineState(position=MachinePosition(0, 0, 10), x_limits=AxisLimits(0, 100), y_limits=AxisLimits(0, 100), z_limits=AxisLimits(0, 200), homed_axes="xyz", max_velocity=100, max_accel=500, live_velocity=0)
        runtime, _client = physical_runtime_with_machine(machine)
        runtime._state = runtime_module.MachineRuntimeState.DIAGNOSTIC
        runtime._last_klippy_state = "ready"
        runtime._refresh_machine = lambda: None
        stale_at = time.monotonic() - 10.0
        machine.live_position_updated_at = stale_at
        machine.commanded_position_updated_at = stale_at
        machine.gcode_position_updated_at = stale_at

        with self.assertRaisesRegex(MachineRuntimeError, "posición observada está obsoleta"):
            runtime.capture_reference_observation()

    def test_capture_reference_observation_rejects_session_change_during_refresh(self) -> None:
        machine = MachineState(position=MachinePosition(0, 0, 10), x_limits=AxisLimits(0, 100), y_limits=AxisLimits(0, 100), z_limits=AxisLimits(0, 200), homed_axes="xyz", max_velocity=100, max_accel=500, live_velocity=0)
        runtime, _client = physical_runtime_with_machine(machine)
        runtime._state = runtime_module.MachineRuntimeState.DIAGNOSTIC
        runtime._last_klippy_state = "ready"

        def change_session() -> None:
            with runtime._lock:
                runtime._serial_generation += 1

        runtime._refresh_machine = change_session

        with self.assertRaisesRegex(MachineRuntimeError, "La sesión física cambió durante la observación activa"):
            runtime.capture_reference_observation()


    def test_transport_timeout_is_cleared_when_homing_is_confirmed_by_state(self) -> None:
        class TimeoutClient:
            def send_gcode(self, _script: str, *, timeout: float | None = None) -> dict[str, object]:
                raise MoonrakerTimeout("G-code request timed out: prueba")

        machine = MachineState(
            position=MachinePosition(0, 0, 10),
            x_limits=AxisLimits(0, 100),
            y_limits=AxisLimits(0, 100),
            z_limits=AxisLimits(0, 50),
            homed_axes="xyz",
            max_velocity=100,
            max_accel=500,
            live_velocity=0,
        )
        runtime = MachineRuntime(config(MachineMode.PHYSICAL), discovery=lambda _client: machine)
        runtime._client = TimeoutClient()
        runtime._machine = machine

        runtime._send_script("G28", label="homing")
        self.assertIn("G-code request timed out", runtime.snapshot()["last_error"])

        runtime._wait_for_homing({"x", "y", "z"})

        snapshot = runtime.snapshot()
        self.assertIsNone(snapshot["last_error"])
        self.assertTrue(any("Timeout HTTP de homing resuelto" in event["message"] for event in snapshot["events"]))

    def test_cancelled_mesh_context_then_reset_does_not_cancel_preparation(self) -> None:
        machine = MachineState(
            position=MachinePosition(5, 5, 3),
            x_limits=AxisLimits(-10, 110),
            y_limits=AxisLimits(-20, 80),
            z_limits=AxisLimits(0, 200),
            homed_axes="",
            max_velocity=100,
            max_accel=500,
            live_velocity=0,
        )
        runtime, client = physical_runtime_with_machine(machine)

        mesh_context = runtime._begin_operation_context("mesh")
        runtime.cancel_operation()
        self.assertTrue(mesh_context.cancel_event.is_set())
        runtime._finish_operation_context(mesh_context)
        # The reset closes the simulated transport; reattach a fresh simulated one
        # exactly as a real reconnect would before starting the next operation.
        runtime._driver = None
        runtime._serial_thread = None
        runtime.reset_physical_session()
        runtime._client = client
        runtime._machine = machine
        runtime._driver = type("Driver", (), {"diagnostics": FakeDiagnostics()})()
        runtime._serial_thread = FakeThread()
        runtime._last_packet_at = time.monotonic()
        runtime._last_telemetry_at = time.monotonic()

        snapshot = runtime.initialize()

        self.assertEqual(snapshot["state"], "WAITING_FOR_XY_REFERENCE")
        self.assertEqual(client.scripts[0], "G28")
        self.assertIn("Z115.000000", client.scripts[1])
        self.assertIn("X50.000000", client.scripts[2])
        self.assertTrue(all("Sondeo cancelado" not in step["detail"] for step in snapshot["initialization_steps"]))
        self.assertEqual(snapshot["active_operation"]["operation_type"], "preparation")
        self.assertIsNone(runtime.snapshot()["active_operation"])

    def test_cancelled_preparation_can_retry_with_a_new_context(self) -> None:
        class CancelAfterHomeClient(MotionClient):
            def __init__(self, machine: MachineState, runtime: MachineRuntime) -> None:
                super().__init__(machine)
                self.runtime = runtime
                self.cancel_once = True

            def send_gcode(self, script: str, *, timeout: float | None = None) -> dict[str, object]:
                result = super().send_gcode(script, timeout=timeout)
                if self.cancel_once and "G28" in script:
                    self.cancel_once = False
                    self.runtime.cancel_operation()
                return result

        machine = MachineState(
            position=MachinePosition(0, 0, 0),
            x_limits=AxisLimits(0, 100),
            y_limits=AxisLimits(0, 100),
            z_limits=AxisLimits(0, 200),
            homed_axes="",
            max_velocity=100,
            max_accel=500,
            live_velocity=0,
        )
        runtime, _client = physical_runtime_with_machine(machine)
        cancelling_client = CancelAfterHomeClient(machine, runtime)
        runtime._client = cancelling_client

        with self.assertRaisesRegex(MachineRuntimeError, "Preparación cancelada por el operador"):
            runtime.initialize()

        failed = runtime.snapshot()
        self.assertEqual(failed["state"], "CANCELLED")
        self.assertTrue(any(step["name"] == "PREPARATION_CANCELLED" for step in failed["initialization_steps"]))
        self.assertTrue(all("Sondeo cancelado" not in step["detail"] for step in failed["initialization_steps"]))
        self.assertTrue(runtime._movement_lock.acquire(blocking=False))
        runtime._movement_lock.release()

        runtime._client = MotionClient(machine)
        retry = runtime.initialize()

        self.assertEqual(retry["state"], "WAITING_FOR_XY_REFERENCE")
        self.assertEqual(retry["active_operation"]["operation_type"], "preparation")
        self.assertIsNone(runtime.snapshot()["active_operation"])
        self.assertTrue(any(step["name"] == "PREPARATION_CENTER_DONE" for step in retry["initialization_steps"]))

    def test_cancelled_reference_context_cannot_cancel_new_preparation_context(self) -> None:
        runtime = MachineRuntime(config(MachineMode.PHYSICAL))
        reference_context = runtime._begin_operation_context("reference_z")
        runtime.cancel_operation()
        preparation_context = runtime._begin_operation_context("preparation")

        self.assertTrue(reference_context.cancel_event.is_set())
        self.assertFalse(preparation_context.cancel_event.is_set())
        self.assertNotEqual(reference_context.operation_id, preparation_context.operation_id)
        runtime._raise_if_cancelled()
        self.assertEqual(runtime.snapshot()["active_operation"]["operation_type"], "preparation")
        runtime._finish_operation_context(preparation_context)

    def test_initialize_runs_g28_then_reference_z_then_machine_center(self) -> None:
        machine = MachineState(
            position=MachinePosition(5, 5, 3),
            x_limits=AxisLimits(-10, 110),
            y_limits=AxisLimits(-20, 80),
            z_limits=AxisLimits(0, 200),
            homed_axes="",
            max_velocity=100,
            max_accel=500,
            live_velocity=0,
        )
        runtime, client = physical_runtime_with_machine(machine)

        snapshot = runtime.initialize()

        self.assertEqual(snapshot["state"], "WAITING_FOR_XY_REFERENCE")
        self.assertEqual(client.scripts[0], "G28")
        self.assertIn("Z115.000000", client.scripts[1])
        self.assertIn("F180.000", client.scripts[1])
        self.assertIn("X50.000000", client.scripts[2])
        self.assertIn("F1800.000", client.scripts[2])
        self.assertIn("Y30.000000", client.scripts[2])
        self.assertLess(client.scripts[1].find("Z115.000000"), len(client.scripts[1]))
        self.assertEqual(machine.get_motion_snapshot()["z"], 115.0)
        self.assertEqual(machine.get_motion_snapshot()["x"], 50.0)
        self.assertEqual(machine.get_motion_snapshot()["y"], 30.0)
        self.assertTrue(any(step["name"] == "centro_confirmado" for step in snapshot["initialization_steps"]))

    def test_reference_z_115_uses_dynamic_timeout_above_travel_time(self) -> None:
        machine = MachineState(
            position=MachinePosition(0, 0, 0),
            x_limits=AxisLimits(0, 100),
            y_limits=AxisLimits(0, 100),
            z_limits=AxisLimits(0, 200),
            homed_axes="",
            max_velocity=100,
            max_accel=500,
            live_velocity=0,
        )
        runtime, client = physical_runtime_with_machine(machine)

        snapshot = runtime.initialize()

        z_step = next(step for step in snapshot["initialization_steps"] if step["name"] == "z_preparacion_referencia")
        self.assertIn("distancia 115.000 mm", z_step["detail"])
        self.assertIn("velocidad configurada 180.000 mm/min", z_step["detail"])
        self.assertIn("velocidad efectiva 3.000 mm/s", z_step["detail"])
        self.assertIn("estimado 38.333 s", z_step["detail"])
        self.assertIn("timeout 180.000 s", z_step["detail"])
        self.assertIn("X50.000000", client.scripts[2])
        self.assertEqual(snapshot["state"], "WAITING_FOR_XY_REFERENCE")

    def test_reference_z_progresses_for_more_than_57_seconds_without_abort_and_then_centers(self) -> None:
        machine = MachineState(
            position=MachinePosition(0, 0, 0),
            x_limits=AxisLimits(0, 100),
            y_limits=AxisLimits(0, 100),
            z_limits=AxisLimits(0, 200),
            homed_axes="",
            max_velocity=100,
            max_accel=500,
            max_z_velocity=10,
            live_velocity=0,
        )
        runtime, _client = physical_runtime_with_machine(machine)
        client = SlowZClient(machine, speed_mm_s=2)
        runtime._client = client
        fake_clock = FakeClock(client.advance)
        original_time = runtime_module.time
        runtime_module.time = fake_clock
        try:
            snapshot = runtime.initialize()
        finally:
            runtime_module.time = original_time

        self.assertEqual(snapshot["state"], "WAITING_FOR_XY_REFERENCE")
        self.assertGreaterEqual(fake_clock.now, 38.0)
        z_step = next(step for step in snapshot["initialization_steps"] if step["name"] == "z_preparacion_referencia")
        self.assertIn("estimado 38.333 s", z_step["detail"])
        self.assertIn("timeout 180.000 s", z_step["detail"])
        self.assertIn("Z115.000000", client.scripts[1])
        self.assertIn("F180.000", client.scripts[1])
        self.assertIn("X50.000000", client.scripts[2])
        self.assertEqual(len(client.scripts), 3)

    def test_reference_z_timeout_uses_max_z_velocity_when_lower_than_requested_feed(self) -> None:
        machine = MachineState(
            position=MachinePosition(0, 0, 0),
            x_limits=AxisLimits(0, 100),
            y_limits=AxisLimits(0, 100),
            z_limits=AxisLimits(0, 200),
            homed_axes="",
            max_velocity=100,
            max_accel=500,
            max_z_velocity=1,
            live_velocity=0,
        )
        runtime, client = physical_runtime_with_machine(machine)

        snapshot = runtime.initialize()

        self.assertIn("F60.000", client.scripts[1])
        z_step = next(step for step in snapshot["initialization_steps"] if step["name"] == "z_preparacion_referencia")
        self.assertIn("velocidad efectiva 1.000 mm/s", z_step["detail"])
        self.assertIn("estimado 115.000 s", z_step["detail"])
        self.assertIn("timeout 182.500 s", z_step["detail"])
        self.assertIn("X50.000000", client.scripts[2])

    def test_reference_z_uses_live_position_not_commanded_position_for_progress(self) -> None:
        machine = MachineState(
            position=MachinePosition(0, 0, 0),
            x_limits=AxisLimits(0, 100),
            y_limits=AxisLimits(0, 100),
            z_limits=AxisLimits(0, 200),
            homed_axes="",
            max_velocity=100,
            max_accel=500,
            max_z_velocity=10,
            live_velocity=0,
        )
        runtime, _client = physical_runtime_with_machine(machine)
        client = CommandedTargetSlowLiveZClient(machine, speed_mm_s=2)
        runtime._client = client
        fake_clock = FakeClock(client.advance)
        original_time = runtime_module.time
        runtime_module.time = fake_clock
        try:
            snapshot = runtime.initialize()
        finally:
            runtime_module.time = original_time

        self.assertEqual(snapshot["state"], "WAITING_FOR_XY_REFERENCE")
        self.assertGreaterEqual(fake_clock.now, 57.0)
        self.assertEqual(machine.get_motion_snapshot()["source"], "motion_report.live_position")
        self.assertEqual(machine.get_motion_snapshot()["commanded_position"]["z"], 115.0)
        self.assertIn("X50.000000", client.scripts[2])

    def test_reference_z_websocket_stale_uses_http_query_fallback(self) -> None:
        machine = MachineState(
            position=MachinePosition(0, 0, 0),
            x_limits=AxisLimits(0, 100),
            y_limits=AxisLimits(0, 100),
            z_limits=AxisLimits(0, 200),
            homed_axes="",
            max_velocity=100,
            max_accel=500,
            live_velocity=0,
        )
        client = QueryFallbackClient(machine)

        def discovery(_client):
            if client.z_command_sent:
                observed = machine.get_motion_snapshot()
                machine.update_motion(live_position=(float(observed["x"]), float(observed["y"]), 115), live_velocity=0)
            return machine

        runtime = MachineRuntime(config(MachineMode.PHYSICAL), discovery=discovery)
        runtime._client = client
        runtime._machine = machine
        runtime._driver = type("Driver", (), {"diagnostics": FakeDiagnostics()})()
        runtime._serial_thread = FakeThread()
        runtime._last_packet_at = time.monotonic()
        runtime._last_telemetry_at = time.monotonic() - 10

        snapshot = runtime.initialize()

        self.assertEqual(snapshot["state"], "WAITING_FOR_XY_REFERENCE")
        self.assertIn("Z115.000000", client.scripts[1])
        self.assertIn("X50.000000", client.scripts[2])
        self.assertAlmostEqual(machine.get_motion_snapshot()["z"], 115.0, places=3)

    def test_reference_z_command_rejection_stops_before_center(self) -> None:
        machine = MachineState(
            position=MachinePosition(0, 0, 0),
            x_limits=AxisLimits(0, 100),
            y_limits=AxisLimits(0, 100),
            z_limits=AxisLimits(0, 200),
            homed_axes="",
            max_velocity=100,
            max_accel=500,
            live_velocity=0,
        )
        runtime, _client = physical_runtime_with_machine(machine)
        client = RejectedZClient(machine)
        runtime._client = client

        with self.assertRaisesRegex(MoonrakerError, "Move rejected"):
            runtime.initialize()

        self.assertEqual(len(client.scripts), 2)
        self.assertIn("Z115.000000", client.scripts[1])

    def test_machine_settings_are_editable_and_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings_path = f"{directory}/machine_runtime_settings.json"
            runtime = MachineRuntime(config(MachineMode.PHYSICAL), settings_path=runtime_module.Path(settings_path))

            saved = runtime.update_machine_settings({
                "reference_prep_z_mm": 110,
                "long_tool_change_clearance_z_mm": 130,
                "z_clearance_feed_mm_min": 90,
                "reference_approach_z_feed_mm_min": 45,
                "move_total_timeout_s": 240,
                "no_progress_timeout_s": 70,
                "position_tolerance_mm": 0.04,
                "velocity_tolerance_mm_s": 0.015,
                "reference_probe_step_mm": 0.10,
                "reference_probe_feed_mm_min": 120,
                "reference_probe_retract_mm": 1.25,
            })
            self.assertEqual(saved["reference_probe_step_mm"], 0.10)
            self.assertEqual(saved["reference_probe_feed_mm_min"], 120)
            self.assertEqual(saved["reference_probe_retract_mm"], 1.25)

            self.assertEqual(saved["reference_prep_z_mm"], 110)
            self.assertEqual(saved["long_tool_change_clearance_z_mm"], 130)
            self.assertEqual(saved["z_clearance_feed_mm_min"], 90)
            self.assertEqual(saved["reference_approach_z_feed_mm_min"], 45)
            reloaded = MachineRuntime(config(MachineMode.PHYSICAL), settings_path=runtime_module.Path(settings_path))
            self.assertEqual(reloaded.config.reference_prep_z_mm, 110)
            self.assertEqual(reloaded.config.long_tool_change_clearance_z_mm, 130)
            self.assertEqual(reloaded.config.z_clearance_feed_mm_min, 90)
            self.assertEqual(reloaded.config.reference_approach_z_feed_mm_min, 45)
            self.assertEqual(reloaded.config.move_timeout_s, 240)
            self.assertEqual(reloaded.config.probe_step_mm, 0.10)
            self.assertEqual(reloaded.config.probe_lower_speed_mm_s, 2.0)
            self.assertEqual(reloaded.config.probe_retract_mm, 1.25)
            self.assertEqual(reloaded.config.move_minimum_timeout_s, 240)
            self.assertEqual(reloaded.config.no_progress_timeout_s, 70)
            self.assertEqual(reloaded.config.settle_tolerance_mm, 0.04)
            self.assertEqual(reloaded.config.velocity_tolerance_mm_s, 0.015)

    def test_legacy_long_tool_reference_setting_migrates_to_canonical_clearance(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings_path = Path(directory) / "machine_runtime_settings.json"
            settings_path.write_text(json.dumps({
                "reference_prep_z_mm": 105,
                "long_tool_reference_prep_z_mm": 130,
            }))

            runtime = MachineRuntime(config(MachineMode.PHYSICAL), settings_path=settings_path)

            self.assertEqual(runtime.config.reference_prep_z_mm, 105)
            self.assertEqual(runtime.config.long_tool_change_clearance_z_mm, 130)
            self.assertNotIn("long_tool_reference_prep_z_mm", runtime.machine_settings())
            runtime.update_machine_settings({"long_tool_change_clearance_z_mm": 130})
            persisted = json.loads(settings_path.read_text())
            self.assertEqual(persisted["long_tool_change_clearance_z_mm"], 130)
            self.assertNotIn("long_tool_reference_prep_z_mm", persisted)

    def test_legacy_reference_z_feed_migrates_to_both_canonical_feeds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings_path = Path(directory) / "machine_runtime_settings.json"
            settings_path.write_text(json.dumps({"reference_prep_z_feed_mm_min": 72.0}))

            runtime = MachineRuntime(config(MachineMode.PHYSICAL), settings_path=settings_path)

            self.assertEqual(runtime.config.z_clearance_feed_mm_min, 72.0)
            self.assertEqual(runtime.config.reference_approach_z_feed_mm_min, 72.0)
            runtime.update_machine_settings({"reference_approach_z_feed_mm_min": 36.0})
            persisted = json.loads(settings_path.read_text())
            self.assertEqual(persisted["z_clearance_feed_mm_min"], 72.0)
            self.assertEqual(persisted["reference_approach_z_feed_mm_min"], 36.0)
            self.assertNotIn("reference_prep_z_feed_mm_min", persisted)

    def test_canonical_reference_approach_feed_load_update_and_reload_use_exact_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            settings_path = Path(directory) / "machine_runtime_settings.json"
            settings_path.write_text(json.dumps({"reference_approach_z_feed_mm_min": 45.0}))

            runtime = MachineRuntime(config(MachineMode.PHYSICAL), settings_path=settings_path)

            self.assertEqual(runtime.config.reference_approach_z_feed_mm_min, 45.0)
            updated = runtime.update_machine_settings({"reference_approach_z_feed_mm_min": 36.0})
            self.assertEqual(updated["reference_approach_z_feed_mm_min"], 36.0)
            self.assertEqual(runtime.config.reference_approach_z_feed_mm_min, 36.0)
            persisted = json.loads(settings_path.read_text())
            self.assertEqual(persisted["reference_approach_z_feed_mm_min"], 36.0)

            reloaded = MachineRuntime(config(MachineMode.PHYSICAL), settings_path=settings_path)
            self.assertEqual(reloaded.config.reference_approach_z_feed_mm_min, 36.0)

    def test_tool_change_profiles_follow_z_orientation_and_klipper_limits(self) -> None:
        machine = MachineState(
            position=MachinePosition(0, 0, 30),
            x_limits=AxisLimits(0, 100),
            y_limits=AxisLimits(0, 100),
            z_limits=AxisLimits(0, 140),
            homed_axes="xyz",
            max_velocity=100,
            max_accel=500,
            live_velocity=0,
        )
        runtime, _client = physical_runtime_with_machine(
            machine,
            cfg=config(
                MachineMode.PHYSICAL,
                reference_prep_z_mm=105.0,
                tool_change_clearance_z_mm=115.0,
                long_tool_change_clearance_z_mm=130.0,
            ),
        )

        self.assertEqual(runtime.reference_preparation_z("standard"), 105.0)
        self.assertEqual(runtime.reference_preparation_z("long_tool"), 105.0)
        self.assertEqual(runtime.tool_change_clearance_z("standard"), 115.0)
        self.assertEqual(runtime.tool_change_clearance_z("long_tool"), 130.0)
        with self.assertRaisesRegex(MachineRuntimeError, "fuera de límites Klipper"):
            runtime.update_machine_settings({"long_tool_change_clearance_z_mm": 145.0})
        with self.assertRaisesRegex(MachineRuntimeError, "aumentar Z aleja"):
            runtime.update_machine_settings({"long_tool_change_clearance_z_mm": 110.0})

        inverted, _client = physical_runtime_with_machine(
            machine,
            cfg=config(
                MachineMode.PHYSICAL,
                reference_prep_z_mm=105.0,
                tool_change_clearance_z_mm=100.0,
                long_tool_change_clearance_z_mm=90.0,
                tool_change_z_positive_up=False,
            ),
        )
        self.assertEqual(inverted.reference_preparation_z("long_tool"), 105.0)
        self.assertEqual(inverted.tool_change_clearance_z("standard"), 100.0)
        self.assertEqual(inverted.tool_change_clearance_z("long_tool"), 90.0)
        with self.assertRaisesRegex(MachineRuntimeError, "disminuir Z aleja"):
            inverted.update_machine_settings({"long_tool_change_clearance_z_mm": 110.0})

    def test_reference_z_feed_direction_respects_positive_and_inverted_orientation(self) -> None:
        positive = MachineRuntime(config(
            MachineMode.SIMULATED,
            tool_change_z_positive_up=True,
            z_clearance_feed_mm_min=240.0,
            reference_approach_z_feed_mm_min=30.0,
        ))
        inverted = MachineRuntime(config(
            MachineMode.SIMULATED,
            tool_change_z_positive_up=False,
            z_clearance_feed_mm_min=240.0,
            reference_approach_z_feed_mm_min=30.0,
        ))

        self.assertEqual(positive._reference_target_z_feed(current_z=105.0, target_z=130.0), 240.0)
        self.assertEqual(positive._reference_target_z_feed(current_z=130.0, target_z=105.0), 30.0)
        self.assertEqual(inverted._reference_target_z_feed(current_z=105.0, target_z=90.0), 240.0)
        self.assertEqual(inverted._reference_target_z_feed(current_z=90.0, target_z=105.0), 30.0)

    def test_reset_physical_session_rejects_active_operation_without_cancelling_it(self) -> None:
        runtime = MachineRuntime(config(MachineMode.SIMULATED))
        context = runtime._begin_operation_context("reference_z")

        with self.assertRaisesRegex(MachineRuntimeError, "operación o movimiento"):
            runtime.reset_physical_session()

        self.assertFalse(context.cancel_event.is_set())
        self.assertIs(runtime._active_operation, context)
        runtime._finish_operation_context(context)

    def test_reference_z_sequence_0_5_17_783_50_100_115_continues_to_center(self) -> None:
        machine = MachineState(
            position=MachinePosition(0, 0, 0),
            x_limits=AxisLimits(0, 100),
            y_limits=AxisLimits(0, 100),
            z_limits=AxisLimits(0, 200),
            homed_axes="",
            max_velocity=100,
            max_accel=500,
            max_z_velocity=10,
            live_velocity=0,
        )
        runtime, _client = physical_runtime_with_machine(machine)
        client = SampleSequenceZClient(machine, [0.0, 5.0, 17.783, 50.0, 100.0, 115.0, 115.0])
        runtime._client = client
        fake_clock = FakeClock(client.advance)
        original_time = runtime_module.time
        runtime_module.time = fake_clock
        try:
            snapshot = runtime.initialize()
        finally:
            runtime_module.time = original_time

        self.assertEqual(snapshot["state"], "WAITING_FOR_XY_REFERENCE")
        self.assertIn("Z115.000000", client.scripts[1])
        self.assertIn("X50.000000", client.scripts[2])
        self.assertEqual(sum(1 for script in client.scripts if "Z115.000000" in script), 1)

    def test_reference_z_descending_sequence_115_100_60_17_0_is_progress(self) -> None:
        machine = MachineState(
            position=MachinePosition(0, 0, 115),
            x_limits=AxisLimits(0, 100),
            y_limits=AxisLimits(0, 100),
            z_limits=AxisLimits(0, 200),
            homed_axes="xyz",
            max_velocity=100,
            max_accel=500,
            max_z_velocity=10,
            live_velocity=0,
        )
        runtime, _client = physical_runtime_with_machine(machine)
        client = SampleSequenceZClient(machine, [115.0, 100.0, 60.0, 17.0, 0.0, 0.0], commanded_z=0.0)
        runtime._client = client
        fake_clock = FakeClock(client.advance)
        original_time = runtime_module.time
        runtime_module.time = fake_clock
        try:
            runtime._move_absolute(z=0.0, label="z_descenso_prueba", feed_mm_min=180.0)
        finally:
            runtime_module.time = original_time

        snapshot = runtime.snapshot()
        self.assertEqual(snapshot["last_movement"]["result"], "confirmado")
        self.assertEqual(sum(1 for script in client.scripts if "Z0.000000" in script), 1)

    def test_reference_z_small_noise_does_not_trigger_away_detection(self) -> None:
        machine = MachineState(
            position=MachinePosition(0, 0, 0),
            x_limits=AxisLimits(0, 100),
            y_limits=AxisLimits(0, 100),
            z_limits=AxisLimits(0, 200),
            homed_axes="xyz",
            max_velocity=100,
            max_accel=500,
            max_z_velocity=10,
            live_velocity=0,
        )
        runtime, _client = physical_runtime_with_machine(machine)
        client = SampleSequenceZClient(machine, [0.0, 5.0, 4.995, 5.004, 20.0, 115.0, 115.0])
        runtime._client = client
        fake_clock = FakeClock(client.advance)
        original_time = runtime_module.time
        runtime_module.time = fake_clock
        try:
            runtime._move_absolute(z=115.0, label="z_ruido_prueba", feed_mm_min=180.0)
        finally:
            runtime_module.time = original_time

        snapshot = runtime.snapshot()
        self.assertEqual(snapshot["last_movement"]["result"], "confirmado")

    def test_reference_z_single_away_sample_is_not_enough_to_abort(self) -> None:
        machine = MachineState(
            position=MachinePosition(0, 0, 0),
            x_limits=AxisLimits(0, 100),
            y_limits=AxisLimits(0, 100),
            z_limits=AxisLimits(0, 200),
            homed_axes="xyz",
            max_velocity=100,
            max_accel=500,
            max_z_velocity=10,
            live_velocity=0,
        )
        runtime, _client = physical_runtime_with_machine(machine)
        client = SampleSequenceZClient(machine, [0.0, 20.0, 19.9, 21.0, 40.0, 115.0, 115.0])
        runtime._client = client
        fake_clock = FakeClock(client.advance)
        original_time = runtime_module.time
        runtime_module.time = fake_clock
        try:
            runtime._move_absolute(z=115.0, label="z_away_aislado", feed_mm_min=180.0)
        finally:
            runtime_module.time = original_time

        snapshot = runtime.snapshot()
        self.assertEqual(snapshot["last_movement"]["result"], "confirmado")

    def test_reference_z_five_consecutive_away_samples_abort(self) -> None:
        machine = MachineState(
            position=MachinePosition(0, 0, 0),
            x_limits=AxisLimits(0, 100),
            y_limits=AxisLimits(0, 100),
            z_limits=AxisLimits(0, 200),
            homed_axes="xyz",
            max_velocity=100,
            max_accel=500,
            max_z_velocity=10,
            live_velocity=0,
        )
        runtime, _client = physical_runtime_with_machine(machine)
        client = SampleSequenceZClient(machine, [0.0, 20.0, 19.9, 19.8, 19.7, 19.6, 19.5])
        runtime._client = client
        fake_clock = FakeClock(client.advance)
        original_time = runtime_module.time
        runtime_module.time = fake_clock
        try:
            with self.assertRaisesRegex(MachineRuntimeError, "se aleja del objetivo"):
                runtime._move_absolute(z=115.0, label="z_away_cinco", feed_mm_min=180.0)
        finally:
            runtime_module.time = original_time

    def test_reference_z_source_change_resets_temporal_comparison(self) -> None:
        machine = MachineState(
            position=MachinePosition(0, 0, 0),
            x_limits=AxisLimits(0, 100),
            y_limits=AxisLimits(0, 100),
            z_limits=AxisLimits(0, 200),
            homed_axes="",
            max_velocity=100,
            max_accel=500,
            max_z_velocity=10,
            live_velocity=0,
        )
        runtime, _client = physical_runtime_with_machine(machine)
        client = SampleSequenceZClient(
            machine,
            [0.0, 5.0, 17.783, 40.0, 80.0, 115.0, 115.0],
            sources=["websocket", "websocket", "http", "http", "http", "http", "http"],
        )
        runtime._client = client
        fake_clock = FakeClock(client.advance)
        original_time = runtime_module.time
        runtime_module.time = fake_clock
        try:
            snapshot = runtime.initialize()
        finally:
            runtime_module.time = original_time

        self.assertEqual(snapshot["state"], "WAITING_FOR_XY_REFERENCE")
        self.assertEqual(snapshot["last_movement"]["live_position_source"], "http")
        self.assertIn("X50.000000", client.scripts[2])

    def test_reference_z_wrong_direction_times_out_without_away_hard_stop(self) -> None:
        cfg = config(MachineMode.PHYSICAL, no_progress_timeout_s=15.0)
        machine = MachineState(
            position=MachinePosition(0, 0, 0),
            x_limits=AxisLimits(-200, 200),
            y_limits=AxisLimits(0, 100),
            z_limits=AxisLimits(-200, 200),
            homed_axes="",
            max_velocity=100,
            max_accel=500,
            max_z_velocity=10,
            live_velocity=0,
        )
        runtime, _client = physical_runtime_with_machine(machine, cfg=cfg)
        client = SlowZClient(machine, speed_mm_s=3, wrong_direction=True)
        runtime._client = client
        fake_clock = FakeClock(client.advance)
        original_time = runtime_module.time
        runtime_module.time = fake_clock
        try:
            with self.assertRaisesRegex(MachineRuntimeError, "sin progreso durante 15.000 s"):
                runtime.initialize()
        finally:
            runtime_module.time = original_time

        self.assertEqual(len(client.scripts), 2)
        self.assertIn("Z115.000000", client.scripts[1])
        self.assertFalse(any("X50.000000" in script for script in client.scripts))

    def test_http_timeout_after_completed_reference_z_continues_to_center(self) -> None:
        machine = MachineState(
            position=MachinePosition(0, 0, 0),
            x_limits=AxisLimits(0, 100),
            y_limits=AxisLimits(0, 100),
            z_limits=AxisLimits(0, 200),
            homed_axes="",
            max_velocity=100,
            max_accel=500,
            live_velocity=0,
        )
        runtime, _client = physical_runtime_with_machine(machine)
        client = TimeoutAfterCompletedMoveClient(machine)
        runtime._client = client

        snapshot = runtime.initialize()

        self.assertEqual(snapshot["state"], "WAITING_FOR_XY_REFERENCE")
        self.assertIsNone(snapshot["last_error"])
        self.assertIn("Z115.000000", client.scripts[1])
        self.assertIn("X50.000000", client.scripts[2])
        self.assertTrue(any("Timeout HTTP de z_preparacion_referencia resuelto" in event["message"] for event in snapshot["events"]))

    def test_delayed_telemetry_for_reference_z_is_reconciled_before_center(self) -> None:
        machine = MachineState(
            position=MachinePosition(0, 0, 0),
            x_limits=AxisLimits(0, 100),
            y_limits=AxisLimits(0, 100),
            z_limits=AxisLimits(0, 200),
            homed_axes="",
            max_velocity=100,
            max_accel=500,
            live_velocity=0,
        )
        runtime, _client = physical_runtime_with_machine(machine)
        client = DelayedMoveClient(machine)
        runtime._client = client

        snapshot = runtime.initialize()

        self.assertEqual(snapshot["state"], "WAITING_FOR_XY_REFERENCE")
        self.assertIn("Z115.000000", client.scripts[1])
        self.assertIn("X50.000000", client.scripts[2])
        self.assertAlmostEqual(machine.get_motion_snapshot()["z"], 115.0, places=3)

    def test_initialize_uses_absolute_preparation_z_after_high_home_not_mesh_safe_z(self) -> None:
        class HomeAtHighZClient(MotionClient):
            def send_gcode(self, script: str, *, timeout: float | None = None) -> dict[str, object]:
                if "G28" in script:
                    self.scripts.append(script)
                    self.machine.update_toolhead(position=(0, 0, 130), homed_axes="xyz")
                    self.machine.update_motion(live_position=(0, 0, 130), live_velocity=0)
                    return {"result": "ok"}
                return super().send_gcode(script, timeout=timeout)

        machine = MachineState(
            position=MachinePosition(0, 0, 10), x_limits=AxisLimits(0, 100), y_limits=AxisLimits(0, 80),
            z_limits=AxisLimits(0, 200), homed_axes="", max_velocity=100, max_accel=500, live_velocity=0,
        )
        runtime, _client = physical_runtime_with_machine(machine, cfg=config(MachineMode.PHYSICAL, safe_z_mm=4.0, reference_prep_z_mm=115.0))
        client = HomeAtHighZClient(machine)
        runtime._client = client

        snapshot = runtime.initialize()

        self.assertEqual(snapshot["state"], "WAITING_FOR_XY_REFERENCE")
        self.assertEqual(len(client.scripts), 3)
        self.assertEqual(client.scripts[0], "G28")
        self.assertIn("G1 Z115.000000 F180.000", client.scripts[1])
        self.assertNotIn("Z4.000000", client.scripts[1])
        self.assertIn("G1 X50.000000 Y40.000000", client.scripts[2])
        self.assertEqual(machine.get_motion_snapshot()["z"], 115.0)
        steps = [step["name"] for step in snapshot["initialization_steps"]]
        self.assertLess(steps.index("PREPARATION_Z_DONE"), steps.index("PREPARATION_CENTER_START"))
        self.assertIn("PREPARATION_HOME_DONE", steps)
        self.assertIn("PREPARATION_CENTER_DONE", steps)

    def test_incomplete_reference_z_times_out_with_observed_position(self) -> None:
        cfg = config(
            MachineMode.PHYSICAL,
            move_timeout_s=0.1,
            move_minimum_timeout_s=0.1,
            move_timeout_factor=0.0,
            move_settle_margin_s=0.0,
            stable_samples=1,
        )
        machine = MachineState(
            position=MachinePosition(0, 0, 0),
            x_limits=AxisLimits(0, 100),
            y_limits=AxisLimits(0, 100),
            z_limits=AxisLimits(0, 200),
            homed_axes="",
            max_velocity=100,
            max_accel=500,
            live_velocity=0,
        )
        runtime, _client = physical_runtime_with_machine(machine, cfg=cfg)
        client = IncompleteMoveClient(machine)
        runtime._client = client

        with self.assertRaisesRegex(MachineRuntimeError, "Posición observada: X=0.000, Y=0.000, Z=0.000"):
            runtime.initialize()

        self.assertEqual(len(client.scripts), 2)
        self.assertIn("Z115.000000", client.scripts[1])
        self.assertTrue(runtime._movement_lock.acquire(blocking=False))
        runtime._movement_lock.release()

    def test_initialize_rejects_reference_z_outside_klipper_limits(self) -> None:
        machine = MachineState(
            position=MachinePosition(0, 0, 0),
            x_limits=AxisLimits(0, 100),
            y_limits=AxisLimits(0, 100),
            z_limits=AxisLimits(0, 60),
            homed_axes="",
            max_velocity=100,
            max_accel=500,
            live_velocity=0,
        )
        runtime, client = physical_runtime_with_machine(machine)

        with self.assertRaisesRegex(MachineRuntimeError, "fuera de límites"):
            runtime.initialize()

        self.assertEqual(client.scripts, ["G28"])

    def test_confirm_probe_auto_arms_from_waiting_state(self) -> None:
        machine = MachineState(
            position=MachinePosition(10, 8, 5),
            x_limits=AxisLimits(0, 100),
            y_limits=AxisLimits(0, 100),
            z_limits=AxisLimits(0, 200),
            homed_axes="xyz",
            max_velocity=100,
            max_accel=500,
            live_velocity=0,
        )
        runtime, _client = physical_runtime_with_machine(machine, cfg=config(MachineMode.PHYSICAL, probe_lower_speed_mm_s=1.25, probe_retract_speed_mm_s=1.25))
        runtime._state = runtime_module.MachineRuntimeState.WAITING_FOR_XY_REFERENCE
        runtime._jog = ProbeJogSpy(runtime, machine)
        runtime._last_command = ControllerCommand()
        runtime._wait_for_axis = lambda *args, **kwargs: None

        snapshot = runtime.confirm_probe()

        self.assertEqual(snapshot["state"], "REFERENCE_CAPTURED")
        self.assertEqual(len(runtime._jog.calls), 2)
        self.assertTrue(any("sondeo iniciado desde la pantalla" in event["message"] for event in snapshot["events"]))

    def test_probe_step_accepts_probe_trigger_before_exact_target(self) -> None:
        machine = MachineState(
            position=MachinePosition(10, 8, 115),
            x_limits=AxisLimits(0, 100),
            y_limits=AxisLimits(0, 100),
            z_limits=AxisLimits(0, 200),
            homed_axes="xyz",
            max_velocity=100,
            max_accel=500,
            live_velocity=0,
        )
        runtime, _client = physical_runtime_with_machine(machine)

        def update_probe(seconds: float) -> None:
            if seconds <= 0:
                return
            machine.update_motion(live_position=(10, 8, 114.97), live_velocity=0, source="websocket")
            runtime._last_command = ControllerCommand(probe_triggered=True)

        fake_clock = FakeClock(update_probe)
        original_time = runtime_module.time
        runtime_module.time = fake_clock
        try:
            runtime._wait_for_axis("z", 114.95, "paso de sonda", start_position=115.0)
        finally:
            runtime_module.time = original_time

        self.assertTrue(runtime._last_command.probe_triggered)

    def test_reference_probe_retract_uses_configured_retract_speed(self) -> None:
        machine = MachineState(
            position=MachinePosition(10, 8, 5),
            x_limits=AxisLimits(0, 100),
            y_limits=AxisLimits(0, 100),
            z_limits=AxisLimits(0, 200),
            homed_axes="xyz",
            max_velocity=100,
            max_accel=500,
            live_velocity=0,
        )
        runtime, _client = physical_runtime_with_machine(machine, cfg=config(MachineMode.PHYSICAL, probe_lower_speed_mm_s=1.25, probe_retract_speed_mm_s=9.0))
        runtime._state = runtime_module.MachineRuntimeState.REFERENCE_ARMED
        runtime._probe_requested = True
        runtime._jog = ProbeJogSpy(runtime, machine)
        runtime._last_command = ControllerCommand()
        runtime._wait_for_axis = lambda *args, **kwargs: None

        snapshot = runtime.confirm_probe()

        self.assertEqual(snapshot["state"], "REFERENCE_CAPTURED")
        self.assertEqual(len(runtime._jog.calls), 2)
        self.assertEqual(runtime._jog.calls[0]["speed"], 1.25)
        self.assertEqual(runtime._jog.calls[1]["speed"], 9.0)
        self.assertGreater(float(runtime._jog.calls[1]["distance"]), 0.0)

    def test_reference_and_mesh_share_identical_probe_contact_contract(self) -> None:
        def runtime_at(x: float, y: float) -> MachineRuntime:
            machine = MachineState(
                position=MachinePosition(x, y, 5.0),
                x_limits=AxisLimits(0, 100), y_limits=AxisLimits(0, 100), z_limits=AxisLimits(0, 200),
                homed_axes="xyz", max_velocity=100, max_accel=500, live_velocity=0,
            )
            runtime, _client = physical_runtime_with_machine(
                machine,
                cfg=config(MachineMode.PHYSICAL, probe_step_mm=0.1, probe_lower_speed_mm_s=1.25, probe_retract_mm=0.4, probe_retract_speed_mm_s=2.5),
            )
            runtime._jog = ProbeJogSpy(runtime, machine)
            runtime._wait_for_axis = lambda *args, **kwargs: None
            return runtime

        reference_runtime = runtime_at(10.0, 8.0)
        reference_runtime._state = runtime_module.MachineRuntimeState.WAITING_FOR_XY_REFERENCE
        reference = reference_runtime.confirm_probe()["last_probe_result"]

        mesh_runtime = runtime_at(10.0, 8.0)
        mesh = mesh_runtime.probe_mesh_point(
            {"index": 1, "x_machine": 10.0, "y_machine": 8.0},
            probe_config={"reference_z_mm": 0.1, "safe_z_mm": 4.9, "probe_step_mm": 0.1, "probe_feed_mm_min": 75.0, "retract_mm": 0.4},
        )

        self.assertAlmostEqual(float(reference["z_mm"]), float(mesh["z_measured"]), places=6)
        self.assertEqual(
            [(call["distance"], call["speed"]) for call in reference_runtime._jog.calls],
            [(call["distance"], call["speed"]) for call in mesh_runtime._jog.calls],
        )
        self.assertEqual(reference_runtime.get_live_probe_state()["display_state"], "OPEN")
        self.assertEqual(mesh_runtime.get_live_probe_state()["display_state"], "OPEN")

    def test_reference_then_actual_runtime_mesh_worker_measures_all_2x2_points(self) -> None:
        machine = MachineState(
            position=MachinePosition(0, 0, 10.0),
            x_limits=AxisLimits(0, 100), y_limits=AxisLimits(0, 100), z_limits=AxisLimits(0, 200),
            homed_axes="xyz", max_velocity=100, max_accel=500, live_velocity=0,
        )
        runtime, _client = physical_runtime_with_machine(
            machine,
            cfg=config(MachineMode.PHYSICAL, probe_step_mm=0.1, probe_lower_speed_mm_s=2.0, probe_retract_mm=0.4, probe_retract_speed_mm_s=3.0),
        )
        runtime._jog = ProbeJogSpy(runtime, machine)
        runtime._wait_for_axis = lambda *args, **kwargs: None
        calls: list[str] = []
        common_probe = runtime._perform_probe_descent

        def record_common_probe(**kwargs):
            calls.append(str(kwargs["label"]))
            return common_probe(**kwargs)

        runtime._perform_probe_descent = record_common_probe
        runtime._state = runtime_module.MachineRuntimeState.WAITING_FOR_XY_REFERENCE
        reference = runtime.confirm_probe()["last_probe_result"]

        with tempfile.TemporaryDirectory() as temp:
            repository = JsonProjectRepository(Path(temp))
            project_service = ProjectService(repository)
            project = project_service.create_project(nombre="PCB", ancho_mm=60, alto_mm=60, espesor_mm=1.6)
            operation = project_service.add_operation(project_id=project.id, nombre="Aislamiento", tipo="aislamiento", cara="superior", orden=0, tool_id="tool", herramienta="V-bit")
            service = PhysicalMapService(repository)
            plan = service.capture_reference_and_plan(
                project_id=project.id, operation_id=operation.id,
                machine_origin_x=0.0, machine_origin_y=0.0, reference_z=float(reference["z_mm"]),
                machine_position={"x_mm": 0.0, "y_mm": 0.0, "z_mm": float(reference["z_mm"])},
                homed_axes="xyz", machine_label="simulated-live-runtime", session_id="test",
                config=PhysicalMeshConfig(grid_mode="manual", rows=2, columns=2, edge_margin_left_mm=1.0, edge_margin_right_mm=1.0, edge_margin_bottom_mm=1.0, edge_margin_top_mm=1.0, safe_z_mm=0.1, probe_step_mm=0.1, probe_feed_mm_min=120.0, retract_mm=0.4),
            )
            worker = MeshExecutionService(service)
            worker.start_all(project_id=project.id, map_id=plan["map_id"], runtime=runtime)
            self.assertTrue(worker.wait_until_idle(timeout_s=3.0))
            completed = service.get_by_id(project.id, plan["map_id"])

        self.assertAlmostEqual(float(reference["z_mm"]), 9.9, places=6)
        self.assertEqual(calls[0], "reference_probe")
        self.assertEqual(calls[1:], ["mesh_probe_1", "mesh_probe_2", "mesh_probe_3", "mesh_probe_4"])
        self.assertEqual(completed["status"], "MESH_COMPLETE")
        self.assertEqual(sum(point["status"] == "MEASURED" for point in completed["points"]), 5)
        self.assertTrue(all(point["status"] == "MEASURED" for point in completed["points"][1:]))

    def test_mesh_probe_uses_saved_probe_recipe(self) -> None:
        machine = MachineState(
            position=MachinePosition(10, 8, 5),
            x_limits=AxisLimits(0, 100),
            y_limits=AxisLimits(0, 100),
            z_limits=AxisLimits(0, 200),
            homed_axes="xyz",
            max_velocity=100,
            max_accel=500,
            live_velocity=0,
        )
        runtime, client = physical_runtime_with_machine(
            machine,
            cfg=config(MachineMode.PHYSICAL, safe_z_mm=9.0, probe_step_mm=0.25, probe_lower_speed_mm_s=4.0, probe_retract_mm=0.4),
        )
        runtime._jog = ProbeJogSpy(runtime, machine)
        runtime._last_command = ControllerCommand()
        runtime._wait_for_axis = lambda *args, **kwargs: None

        result = runtime.probe_mesh_point(
            {"index": 3, "x_machine": 25.0, "y_machine": 35.0},
            probe_config={"safe_z_mm": 10.0, "reference_z_mm": 1.23, "probe_step_mm": 0.05, "probe_feed_mm_min": 30.0, "retract_mm": 0.8},
        )

        self.assertEqual(result["index"], 3)
        self.assertIn("Z11.230000", client.scripts[0])
        self.assertIn("X25.000000", client.scripts[1])
        self.assertIn("Y35.000000", client.scripts[1])
        self.assertEqual(len(runtime._jog.calls), 2)
        self.assertEqual(runtime._jog.calls[0]["speed"], 0.5)
        self.assertEqual(runtime._jog.calls[1]["speed"], 2.0)
        self.assertAlmostEqual(float(runtime._jog.calls[0]["distance"]), -0.05)
        self.assertAlmostEqual(float(runtime._jog.calls[1]["distance"]), 0.8)

    def test_probe_profile_payload_marks_inherited_and_override_values(self) -> None:
        machine = MachineState(
            position=MachinePosition(10, 8, 5),
            x_limits=AxisLimits(0, 100),
            y_limits=AxisLimits(0, 100),
            z_limits=AxisLimits(0, 200),
            homed_axes="xyz",
            max_velocity=100,
            max_accel=500,
            live_velocity=0,
        )
        runtime, _client = physical_runtime_with_machine(
            machine,
            cfg=config(MachineMode.PHYSICAL, probe_step_mm=0.05, probe_lower_speed_mm_s=1.25, probe_retract_mm=0.4, probe_retract_speed_mm_s=2.5),
        )

        inherited = runtime.effective_probe_profile_payload({"source": "machine_reference_profile", "safe_z_mm": 10.0})
        override = runtime.effective_probe_profile_payload(
            {"source": "map_override", "safe_z_mm": 10.0, "probe_step_mm": 0.2, "probe_feed_mm_min": 90.0, "retract_mm": 0.7}
        )

        self.assertEqual(inherited["source"], "machine_reference_profile")
        self.assertAlmostEqual(float(inherited["effective_probe_step_mm"]), 0.05)
        self.assertAlmostEqual(float(inherited["effective_probe_feed_mm_min"]), 75.0)
        self.assertAlmostEqual(float(inherited["effective_retract_mm"]), 0.4)
        self.assertEqual(override["source"], "map_override")
        self.assertAlmostEqual(float(override["effective_probe_step_mm"]), 0.2)
        self.assertAlmostEqual(float(override["effective_probe_feed_mm_min"]), 90.0)
        self.assertAlmostEqual(float(override["effective_retract_mm"]), 0.7)
        with self.assertRaises(MachineRuntimeError):
            runtime.effective_probe_profile_payload({"source": "map_override", "probe_step_mm": 0.2})

    def test_reference_and_mesh_profiles_report_identical_probe_sequence(self) -> None:
        def sequence_for(profile_factory) -> list[str]:
            machine = MachineState(
                position=MachinePosition(10, 8, 5.0),
                x_limits=AxisLimits(0, 100),
                y_limits=AxisLimits(0, 100),
                z_limits=AxisLimits(0, 200),
                homed_axes="xyz",
                max_velocity=100,
                max_accel=500,
                live_velocity=0,
            )
            runtime, _client = physical_runtime_with_machine(
                machine,
                cfg=config(MachineMode.PHYSICAL, probe_step_mm=0.1, probe_lower_speed_mm_s=1.25, probe_retract_mm=0.4, probe_retract_speed_mm_s=2.5),
            )
            runtime._jog = ProbeJogSpy(runtime, machine)
            runtime._last_command = ControllerCommand()
            runtime._wait_for_axis = lambda *args, **kwargs: None
            states: list[str] = []
            runtime._perform_probe_descent(label="probe", profile=profile_factory(runtime), progress_callback=lambda state, _detail: states.append(state))
            return states

        expected = [
            "POINT_VERIFY_PROBE_OPEN",
            "POINT_DESCENT_STARTED",
            "POINT_LOWER_STEP",
            "POINT_CONFIRM_STEP",
            "POINT_CONTACT_DETECTED",
            "POINT_RETRACT",
            "POINT_CONFIRM_RETRACT",
            "POINT_VERIFY_PROBE_OPEN_AFTER_RETRACT",
        ]
        reference_states = sequence_for(lambda runtime: runtime._reference_probe_profile())
        mesh_states = sequence_for(
            lambda runtime: runtime._resolve_probe_profile({"source": "map_override", "probe_step_mm": 0.1, "probe_feed_mm_min": 75.0, "retract_mm": 0.4})
        )

        self.assertEqual(reference_states, expected)
        self.assertEqual(mesh_states, expected)

    def test_tool_change_skips_clearance_descent_when_current_gcode_z_is_already_higher(self) -> None:
        machine = MachineState(
            position=MachinePosition(40, 30, 119.127),
            x_limits=AxisLimits(0, 200),
            y_limits=AxisLimits(0, 200),
            z_limits=AxisLimits(0, 200),
            homed_axes="xyz",
            max_velocity=100,
            max_accel=500,
            live_velocity=0,
        )
        runtime, client = physical_runtime_with_machine(
            machine,
            cfg=config(MachineMode.PHYSICAL, tool_change_clearance_z_mm=115.0, tool_change_work_z_mm=119.127),
        )

        snapshot = runtime.move_to_tool_change_position()

        self.assertEqual(snapshot["state"], "WAITING_FOR_XY_REFERENCE")
        self.assertTrue(any("X0.000000" in script and "Y0.000000" in script for script in client.scripts))
        self.assertFalse(any("Z115.000000" in script for script in client.scripts))
        self.assertEqual(machine.get_motion_snapshot()["x"], 0.0)
        self.assertEqual(machine.get_motion_snapshot()["y"], 0.0)

    def test_tool_change_moves_clearance_z_before_xy_when_below_clearance(self) -> None:
        machine = MachineState(
            position=MachinePosition(40, 30, 110),
            x_limits=AxisLimits(0, 200),
            y_limits=AxisLimits(0, 200),
            z_limits=AxisLimits(0, 200),
            homed_axes="xyz",
            max_velocity=100,
            max_accel=500,
            live_velocity=0,
        )
        runtime, client = physical_runtime_with_machine(
            machine,
            cfg=config(MachineMode.PHYSICAL, tool_change_clearance_z_mm=115.0, tool_change_work_z_mm=115.0),
        )

        runtime.move_to_tool_change_position()

        self.assertIn("Z115.000000", client.scripts[0])
        self.assertIn("X0.000000", client.scripts[1])
        self.assertIn("Y0.000000", client.scripts[1])

    def test_tool_change_uses_installed_long_tool_clearance_before_xy(self) -> None:
        machine = MachineState(
            position=MachinePosition(40, 30, 110),
            x_limits=AxisLimits(0, 200),
            y_limits=AxisLimits(0, 200),
            z_limits=AxisLimits(0, 200),
            homed_axes="xyz",
            max_velocity=100,
            max_accel=500,
            live_velocity=0,
        )
        runtime, client = physical_runtime_with_machine(
            machine,
            cfg=config(
                MachineMode.PHYSICAL,
                tool_change_clearance_z_mm=115.0,
                long_tool_change_clearance_z_mm=130.0,
                tool_change_work_z_mm=115.0,
                z_clearance_feed_mm_min=240.0,
                tool_change_z_feed_mm_min=75.0,
            ),
        )

        snapshot = runtime.move_to_tool_change_position(tool_change_profile="long_tool")

        self.assertEqual(snapshot["tool_change_move"]["profile"], "long_tool")
        self.assertEqual(snapshot["tool_change_move"]["configured_clearance_z_mm"], 130.0)
        self.assertIn("Z130.000000", client.scripts[0])
        self.assertIn("F240.000", client.scripts[0])
        self.assertIn("X0.000000", client.scripts[1])
        self.assertIn("Y0.000000", client.scripts[1])

    def test_move_absolute_confirms_g1_targets_in_gcode_frame(self) -> None:
        machine = MachineState(
            position=MachinePosition(40, 30, 110),
            x_limits=AxisLimits(0, 200),
            y_limits=AxisLimits(0, 200),
            z_limits=AxisLimits(0, 200),
            homed_axes="xyz",
            max_velocity=100,
            max_accel=500,
            live_velocity=0,
        )
        runtime, _client = physical_runtime_with_machine(machine)

        runtime._move_absolute(z=115.0, label="frame_check", coordinate_frame="gcode_position")

        self.assertEqual(runtime._last_movement["coordinate_frame"], "gcode_position")
        self.assertEqual(runtime._last_movement["position_source"], "gcode_position")
        self.assertAlmostEqual(runtime._last_movement["observed_position"]["z"], 115.0, places=3)

    def test_wait_for_targets_does_not_fail_when_live_and_gcode_frames_differ_by_offset(self) -> None:
        machine = MachineState(
            position=MachinePosition(97.4, 153.2, 119.127),
            x_limits=AxisLimits(0, 300),
            y_limits=AxisLimits(0, 300),
            z_limits=AxisLimits(0, 300),
            homed_axes="xyz",
            max_velocity=100,
            max_accel=500,
            live_velocity=0,
        )
        runtime, _client = physical_runtime_with_machine(machine)
        machine.update_motion(live_position=(97.4, 153.2, 119.127), live_velocity=0, source="websocket")
        machine.update_gcode_move(gcode_position=(97.4, 153.2, 115.0), position=(97.4, 153.2, 119.127), absolute_coordinates=True, homing_origin=(0.0, 0.0, 4.127))

        result = runtime._wait_for_targets({"z": 115.0}, "tool_change_clearance_z", operation_timeout_s=0.2, coordinate_frame="gcode_position")

        self.assertIn(result["result"], {"confirmado", "reconciliado"})
        self.assertEqual(result["position_source"], "gcode_position")

    def test_tool_change_can_adjust_optional_work_z_after_xy(self) -> None:
        machine = MachineState(
            position=MachinePosition(40, 30, 110),
            x_limits=AxisLimits(0, 200),
            y_limits=AxisLimits(0, 200),
            z_limits=AxisLimits(0, 200),
            homed_axes="xyz",
            max_velocity=100,
            max_accel=500,
            live_velocity=0,
        )
        runtime, client = physical_runtime_with_machine(
            machine,
            cfg=config(
                MachineMode.PHYSICAL,
                tool_change_clearance_z_mm=130.0,
                long_tool_change_clearance_z_mm=130.0,
                tool_change_work_z_mm=115.0,
            ),
        )

        runtime.move_to_tool_change_position()

        self.assertIn("Z130.000000", client.scripts[0])
        self.assertIn("X0.000000", client.scripts[1])
        self.assertIn("Y0.000000", client.scripts[1])
        self.assertIn("Z115.000000", client.scripts[2])

    def test_tool_change_position_moves_z_before_xy(self) -> None:
        machine = MachineState(
            position=MachinePosition(40, 30, 10),
            x_limits=AxisLimits(0, 100),
            y_limits=AxisLimits(0, 100),
            z_limits=AxisLimits(0, 200),
            homed_axes="xyz",
            max_velocity=100,
            max_accel=500,
            live_velocity=0,
        )
        runtime, client = physical_runtime_with_machine(machine)

        snapshot = runtime.move_to_tool_change_position()

        self.assertEqual(snapshot["state"], "WAITING_FOR_XY_REFERENCE")
        self.assertIn("Z115.000000", client.scripts[0])
        self.assertIn("F180.000", client.scripts[0])
        self.assertIn("X0.000000", client.scripts[1])
        self.assertIn("Y0.000000", client.scripts[1])
        self.assertEqual(machine.get_motion_snapshot()["z"], 115.0)
        self.assertEqual(machine.get_motion_snapshot()["x"], 0.0)
        self.assertEqual(machine.get_motion_snapshot()["y"], 0.0)

    def test_command_mapper_discards_diagonal_jog(self) -> None:
        mapper = CommandMapper()
        diagonal = mapper.map(
            ControllerPacket(
                direction="UP_RIGHT",
                joystick_button=False,
                external_button=False,
                probe=False,
                x=900,
                y=900,
            )
        )
        right = mapper.map(
            ControllerPacket(
                direction="RIGHT",
                joystick_button=False,
                external_button=False,
                probe=False,
                x=900,
                y=512,
            )
        )
        self.assertEqual((diagonal.jog_x, diagonal.jog_y), (0, 0))
        self.assertEqual((right.jog_x, right.jog_y), (1, 0))


if __name__ == "__main__":
    unittest.main()


class ReferencePointMoveTest(unittest.TestCase):
    def _runtime(self, *, homed_axes: str = "xyz", runtime_config: MachineRuntimeConfig | None = None) -> tuple[MachineRuntime, MotionClient]:
        machine = MachineState(
            position=MachinePosition(4, 5, 30),
            x_limits=AxisLimits(0, 100),
            y_limits=AxisLimits(0, 100),
            z_limits=AxisLimits(0, 200),
            homed_axes=homed_axes,
            max_velocity=100,
            max_accel=500,
            live_velocity=0,
        )
        machine.update_motion(live_position=(4, 5, 30), live_velocity=0, source="websocket")
        runtime, client = physical_runtime_with_machine(machine, cfg=runtime_config)
        runtime._telemetry_state = "LIVE"
        runtime._last_websocket_message_at = time.monotonic()
        return runtime, client

    def test_moves_preparation_z_before_saved_cnc_xy_without_probing(self) -> None:
        runtime, client = self._runtime()

        result = runtime.go_to_reference_point(reference_x=42.5, reference_y=67.25)

        self.assertTrue(result["accepted"])
        self.assertEqual(result["final_state"], "REFERENCE_MOVE_COMPLETE")
        self.assertEqual(result["preparation_z"], 115.0)
        self.assertEqual(len(client.scripts), 2)
        self.assertIn("Z115.000000", client.scripts[0])
        self.assertNotIn("X42.500000", client.scripts[0])
        self.assertIn("X42.500000", client.scripts[1])
        self.assertIn("Y67.250000", client.scripts[1])
        self.assertNotIn("PROBE", "\n".join(client.scripts).upper())
        self.assertIsNone(runtime._active_operation)
        self.assertFalse(runtime._movement_lock.locked())

    def test_generic_reference_move_uses_normal_prep_z_for_long_tool_profile(self) -> None:
        runtime, client = self._runtime(
            runtime_config=config(
                MachineMode.PHYSICAL,
                reference_prep_z_mm=115.0,
                long_tool_change_clearance_z_mm=135.0,
            )
        )

        result = runtime.go_to_reference_point(
            reference_x=42.5,
            reference_y=67.25,
            tool_reference_profile="long_tool",
        )

        self.assertEqual(result["tool_reference_profile"], "long_tool")
        self.assertEqual(result["preparation_z"], 115.0)
        self.assertIn("Z115.000000", client.scripts[0])
        self.assertNotIn("X42.500000", client.scripts[0])
        self.assertIn("X42.500000", client.scripts[1])
        self.assertIn("Y67.250000", client.scripts[1])
        self.assertNotIn("PROBE", "\n".join(client.scripts).upper())

    def test_long_tool_leaves_change_station_at_long_clearance_before_xy_and_normal_prep(self) -> None:
        stages: list[str] = []
        runtime, client = self._runtime(
            runtime_config=config(
                MachineMode.PHYSICAL,
                reference_prep_z_mm=105.0,
                tool_change_clearance_z_mm=115.0,
                long_tool_change_clearance_z_mm=130.0,
                tool_change_work_z_mm=100.0,
                z_clearance_feed_mm_min=240.0,
                reference_approach_z_feed_mm_min=30.0,
            )
        )

        result = runtime.move_from_tool_change_to_reference_point(
            reference_x=42.5,
            reference_y=67.25,
            tool_change_profile="long_tool",
            progress_callback=lambda stage, _payload: stages.append(stage),
        )

        self.assertEqual(result["tool_change_clearance_z_mm"], 130.0)
        self.assertEqual(result["preparation_z"], 105.0)
        self.assertEqual(len(client.scripts), 3)
        self.assertIn("Z130.000000", client.scripts[0])
        self.assertIn("F240.000", client.scripts[0])
        self.assertIn("X42.500000", client.scripts[1])
        self.assertIn("Y67.250000", client.scripts[1])
        self.assertIn("Z105.000000", client.scripts[2])
        self.assertIn("F30.000", client.scripts[2])
        self.assertEqual(
            stages,
            [
                "RETURNING_TO_REFERENCE_SAFE_Z",
                "RETURNING_TO_REFERENCE_XY",
                "MOVING_TO_REFERENCE",
                "REFERENCE_APPROACH_CONFIRMED",
            ],
        )
        self.assertNotIn("PROBE", "\n".join(client.scripts).upper())

    def test_standard_tool_leaves_change_station_at_standard_clearance_before_xy_and_normal_prep(self) -> None:
        runtime, client = self._runtime(
            runtime_config=config(
                MachineMode.PHYSICAL,
                reference_prep_z_mm=105.0,
                tool_change_clearance_z_mm=115.0,
                long_tool_change_clearance_z_mm=130.0,
                tool_change_work_z_mm=100.0,
            )
        )

        result = runtime.move_from_tool_change_to_reference_point(
            reference_x=42.5,
            reference_y=67.25,
            tool_change_profile="standard",
        )

        self.assertEqual(result["tool_change_clearance_z_mm"], 115.0)
        self.assertEqual(len(client.scripts), 3)
        self.assertIn("Z115.000000", client.scripts[0])
        self.assertIn("X42.500000", client.scripts[1])
        self.assertIn("Y67.250000", client.scripts[1])
        self.assertIn("Z105.000000", client.scripts[2])

    def test_rejects_stale_telemetry_before_any_move(self) -> None:
        runtime, client = self._runtime()
        runtime._telemetry_state = "STALE"

        with self.assertRaisesRegex(MachineRuntimeError, "LIVE"):
            runtime.go_to_reference_point(reference_x=42.5, reference_y=67.25)

        self.assertEqual(client.scripts, [])
        self.assertFalse(runtime._movement_lock.locked())

    def test_rejects_missing_home_and_triggered_probe_before_xy(self) -> None:
        runtime, client = self._runtime(homed_axes="xy")
        with self.assertRaisesRegex(MachineRuntimeError, "homing"):
            runtime.go_to_reference_point(reference_x=42.5, reference_y=67.25)
        self.assertEqual(client.scripts, [])

        runtime, client = self._runtime()
        with runtime._lock:
            runtime._probe_filtered = True
            runtime._probe_raw = True
        with self.assertRaisesRegex(MachineRuntimeError, "TRIGGERED"):
            runtime.go_to_reference_point(reference_x=42.5, reference_y=67.25)
        self.assertEqual(client.scripts, [])

    def test_rejects_another_physical_operation_without_replacing_context(self) -> None:
        runtime, _client = self._runtime()
        self.assertTrue(runtime._movement_lock.acquire(blocking=False))
        try:
            with self.assertRaisesRegex(MachineRuntimeError, "operación física activa"):
                runtime.go_to_reference_point(reference_x=42.5, reference_y=67.25)
        finally:
            runtime._movement_lock.release()
