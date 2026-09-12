from __future__ import annotations

import asyncio
import time
import unittest
from unittest.mock import Mock, patch

from klipper_cnc_assistant.machine.state import AxisLimits, MachinePosition, MachineState
from klipper_cnc_assistant.moonraker.telemetry import MoonrakerTelemetry


class QuietWebSocket:
    def __init__(self) -> None:
        self.pings = 0
        self.sent: list[str] = []
        self._recv_waiters: list[asyncio.Future[str]] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str:
        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[str] = loop.create_future()
        self._recv_waiters.append(waiter)
        try:
            return await waiter
        finally:
            if waiter in self._recv_waiters:
                self._recv_waiters.remove(waiter)

    def ping(self):
        self.pings += 1
        loop = asyncio.get_running_loop()
        waiter = loop.create_future()
        waiter.set_result(None)
        return waiter

    async def close(self) -> None:
        for waiter in list(self._recv_waiters):
            if not waiter.done():
                waiter.cancel()


class ConnectContext:
    def __init__(self, websocket: QuietWebSocket) -> None:
        self.websocket = websocket

    async def __aenter__(self) -> QuietWebSocket:
        return self.websocket

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class MoonrakerTelemetryTest(unittest.IsolatedAsyncioTestCase):
    async def _wait_for(self, predicate, *, timeout: float = 0.5) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            await asyncio.sleep(0.01)
        self.fail("timeout waiting for condition")

    def _machine(self) -> MachineState:
        return MachineState(
            position=MachinePosition(0, 0, 0),
            x_limits=AxisLimits(0, 100),
            y_limits=AxisLimits(0, 100),
            z_limits=AxisLimits(0, 50),
            homed_axes="xyz",
            max_velocity=100,
            max_accel=500,
        )

    async def test_quiet_connection_stays_connected_after_successful_ping(self) -> None:
        websocket = QuietWebSocket()
        telemetry = MoonrakerTelemetry(
            "ws://example",
            self._machine(),
            idle_ping_interval_s=0.01,
            ping_timeout_s=0.01,
            reconnect_delay_s=0.01,
        )
        states: list[dict[str, object]] = []
        telemetry.set_snapshot_callback(states.append)
        connect_mock = Mock(return_value=ConnectContext(websocket))

        with patch("klipper_cnc_assistant.moonraker.telemetry.websockets.connect", connect_mock):
            task = asyncio.create_task(telemetry.run())
            await self._wait_for(lambda: telemetry.snapshot()["state"] == "CONNECTED")
            await asyncio.sleep(0.06)
            telemetry.stop()
            await asyncio.wait_for(task, timeout=0.3)

        self.assertEqual(connect_mock.call_count, 1)
        self.assertGreaterEqual(websocket.pings, 2)
        self.assertEqual(telemetry.snapshot()["reconnects"], 0)
        self.assertNotIn("RECONNECTING", [str(item["state"]) for item in states if item["state"] != "CONNECTING"])

    async def test_stop_interrupts_waiting_recv_without_waiting_for_ping_timeouts(self) -> None:
        websocket = QuietWebSocket()
        telemetry = MoonrakerTelemetry(
            "ws://example",
            self._machine(),
            idle_ping_interval_s=1.0,
            ping_timeout_s=1.0,
            reconnect_delay_s=0.01,
        )

        with patch("klipper_cnc_assistant.moonraker.telemetry.websockets.connect", return_value=ConnectContext(websocket)):
            task = asyncio.create_task(telemetry.run())
            await self._wait_for(lambda: telemetry.snapshot()["state"] == "CONNECTED")
            started = time.monotonic()
            telemetry.stop()
            await asyncio.wait_for(task, timeout=0.2)

        self.assertLess(time.monotonic() - started, 0.2)
        self.assertEqual(telemetry.snapshot()["state"], "STOPPED")


if __name__ == "__main__":
    unittest.main()


class TelemetryFrameFreshnessTest(unittest.TestCase):
    def test_partial_notifications_only_refresh_their_exact_frame(self):
        machine = MachineState(MachinePosition(0, 0, 0), AxisLimits(0, 100),
                               AxisLimits(0, 100), AxisLimits(0, 100), 'xyz', 100, 500)
        telemetry = MoonrakerTelemetry('ws://unused.invalid', machine)
        machine.update_motion((1, 2, 3), 0)
        machine.update_gcode_move(gcode_position=(4, 5, 6))
        live_stamp = machine.live_position_updated_at
        gcode_stamp = machine.gcode_position_updated_at
        telemetry._process_message({'method': 'notify_status_update', 'params': [
            {'toolhead': {'position': [7, 8, 9]}, 'gcode_move': {'position': [10, 11, 12]}}]})
        self.assertEqual(machine.live_position_updated_at, live_stamp)
        self.assertEqual(machine.gcode_position_updated_at, gcode_stamp)
        self.assertIsNotNone(machine.commanded_position_updated_at)
        self.assertIsNotNone(machine.gcode_move_position_updated_at)
        self.assertEqual(machine.authorization_snapshot('live_position').position, (1, 2, 3))

    def test_toolhead_fallback_is_not_a_live_frame(self):
        machine = MachineState(MachinePosition(0, 0, 0), AxisLimits(0, 100),
                               AxisLimits(0, 100), AxisLimits(0, 100), 'xyz', 100, 500)
        machine.update_toolhead(position=(7, 8, 9))
        self.assertIsNone(machine.authorization_snapshot('live_position').position)
        self.assertIsNone(machine.get_motion_snapshot()['live_position_age_s'])
        self.assertEqual(machine.authorization_snapshot('commanded_position').position, (7, 8, 9))
