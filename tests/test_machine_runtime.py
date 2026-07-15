from __future__ import annotations

import re
import time
import unittest

from klipper_cnc_assistant.input.command_mapper import CommandMapper
from klipper_cnc_assistant.input.serial_driver import ControllerPacket
from klipper_cnc_assistant.machine.config import MachineMode, MachineRuntimeConfig
from klipper_cnc_assistant.machine.runtime import MachineRuntime, MachineRuntimeError
from klipper_cnc_assistant.machine.state import AxisLimits, MachinePosition, MachineState
from klipper_cnc_assistant.moonraker.client import MoonrakerTimeout


def config(mode: MachineMode = MachineMode.SIMULATED) -> MachineRuntimeConfig:
    return MachineRuntimeConfig(
        mode=mode,
        auto_connect=False,
        moonraker_url=None,
        moonraker_ws=None,
        serial_port=None,
        serial_baudrate=115200,
        safe_z_mm=10.0,
        reference_prep_z_mm=115.0,
        tool_change_z_mm=115.0,
        tool_change_x_mm=0.0,
        tool_change_y_mm=0.0,
        moonraker_request_timeout_s=0.1,
        home_timeout_s=1.0,
        telemetry_fresh_timeout_s=2.0,
        serial_fresh_timeout_s=2.0,
        serial_startup_delay_s=0.0,
        settle_tolerance_mm=0.02,
        velocity_tolerance_mm_s=0.05,
        move_timeout_s=1.0,
        probe_step_mm=0.05,
        probe_lower_speed_mm_s=1.0,
        probe_retract_mm=1.0,
        probe_retract_speed_mm_s=2.0,
    )


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


def physical_runtime_with_machine(machine: MachineState) -> tuple[MachineRuntime, MotionClient]:
    cfg = config(MachineMode.PHYSICAL)
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
        self.assertIn("X50.000000", client.scripts[2])
        self.assertIn("Y30.000000", client.scripts[2])
        self.assertLess(client.scripts[1].find("Z115.000000"), len(client.scripts[1]))
        self.assertEqual(machine.get_motion_snapshot()["z"], 115.0)
        self.assertEqual(machine.get_motion_snapshot()["x"], 50.0)
        self.assertEqual(machine.get_motion_snapshot()["y"], 30.0)
        self.assertTrue(any(step["name"] == "centro_confirmado" for step in snapshot["initialization_steps"]))

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
