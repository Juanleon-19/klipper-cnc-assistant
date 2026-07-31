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
