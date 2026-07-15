from __future__ import annotations

import re
import tempfile
import threading
import time
import unittest
from dataclasses import replace

from klipper_cnc_assistant.input.command_mapper import CommandMapper
from klipper_cnc_assistant.input.serial_driver import ControllerPacket
from klipper_cnc_assistant.machine.config import MachineMode, MachineRuntimeConfig
from klipper_cnc_assistant.machine.runtime import MachineRuntime, MachineRuntimeError
import klipper_cnc_assistant.machine.runtime as runtime_module
from klipper_cnc_assistant.machine.state import AxisLimits, MachinePosition, MachineState
from klipper_cnc_assistant.moonraker.client import MoonrakerError, MoonrakerTimeout


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
        reference_prep_z_feed_mm_min=120.0,
        tool_change_z_mm=115.0,
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
        self.machine.update_motion(live_position=(x, y, z), live_velocity=0)
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
            return {"result": "ok"}
        if "Z115" in script:
            self.z_command_sent = True
            self.machine.update_toolhead(position=(0, 0, 115))
            self.machine.update_motion(live_position=(0, 0, 0), live_velocity=0)
            return {"result": "ok"}
        return super().send_gcode(script, timeout=timeout)


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


def physical_runtime_with_machine(machine: MachineState, cfg: MachineRuntimeConfig | None = None) -> tuple[MachineRuntime, MotionClient]:
    cfg = cfg or config(MachineMode.PHYSICAL)
    runtime = MachineRuntime(cfg, discovery=lambda _client: machine)
    client = MotionClient(machine)
    runtime._client = client
    runtime._machine = machine
    runtime._driver = type("Driver", (), {"diagnostics": FakeDiagnostics()})()
    runtime._serial_thread = FakeThread()
    runtime._last_packet_at = time.monotonic()
    runtime._last_telemetry_at = time.monotonic()
    return runtime, client


class MachineRuntimeTest(unittest.TestCase):
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
        self.assertIn("F120.000", client.scripts[1])
        self.assertIn("X50.000000", client.scripts[2])
        self.assertIn("F600.000", client.scripts[2])
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
        self.assertIn("velocidad configurada 120.000 mm/min", z_step["detail"])
        self.assertIn("velocidad efectiva 2.000 mm/s", z_step["detail"])
        self.assertIn("estimado 57.500 s", z_step["detail"])
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
        self.assertGreaterEqual(fake_clock.now, 57.0)
        z_step = next(step for step in snapshot["initialization_steps"] if step["name"] == "z_preparacion_referencia")
        self.assertIn("estimado 57.500 s", z_step["detail"])
        self.assertIn("timeout 180.000 s", z_step["detail"])
        self.assertIn("Z115.000000", client.scripts[1])
        self.assertIn("F120.000", client.scripts[1])
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
                machine.update_motion(live_position=(0, 0, 115), live_velocity=0)
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
                "reference_prep_z_feed_mm_min": 90,
                "move_total_timeout_s": 240,
                "no_progress_timeout_s": 70,
                "position_tolerance_mm": 0.04,
                "velocity_tolerance_mm_s": 0.015,
            })

            self.assertEqual(saved["reference_prep_z_mm"], 110)
            self.assertEqual(saved["reference_prep_z_feed_mm_min"], 90)
            reloaded = MachineRuntime(config(MachineMode.PHYSICAL), settings_path=runtime_module.Path(settings_path))
            self.assertEqual(reloaded.config.reference_prep_z_mm, 110)
            self.assertEqual(reloaded.config.reference_prep_z_feed_mm_min, 90)
            self.assertEqual(reloaded.config.move_timeout_s, 240)
            self.assertEqual(reloaded.config.move_minimum_timeout_s, 240)
            self.assertEqual(reloaded.config.no_progress_timeout_s, 70)
            self.assertEqual(reloaded.config.settle_tolerance_mm, 0.04)
            self.assertEqual(reloaded.config.velocity_tolerance_mm_s, 0.015)

    def test_reference_z_wrong_direction_is_blocked_before_center(self) -> None:
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
            with self.assertRaisesRegex(MachineRuntimeError, "se aleja del objetivo"):
                runtime.initialize()
        finally:
            runtime_module.time = original_time

        self.assertEqual(len(client.scripts), 2)
        self.assertIn("Z115.000000", client.scripts[1])

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
