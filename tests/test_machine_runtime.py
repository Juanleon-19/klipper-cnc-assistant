from __future__ import annotations

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
