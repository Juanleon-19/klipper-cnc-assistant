from __future__ import annotations

import threading
import time
import unittest
from dataclasses import dataclass
from unittest.mock import patch

from klipper_cnc_assistant.input.connection_manager import ArduinoConnectionManager, ArduinoConnectionState, UsbIdentity
from klipper_cnc_assistant.input.serial_driver import ControllerPacket


PACKET = ControllerPacket(direction="CENTER", joystick_button=False, external_button=False, probe=False, x=512, y=512)


@dataclass
class FakePortInfo:
    device: str
    vid: int | None = None
    pid: int | None = None
    serial_number: str | None = None
    product: str | None = None
    manufacturer: str | None = None


class FakeDiagnostics:
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


class FakeDriver:
    def __init__(self, port: str, baudrate: int, behavior: list[object]) -> None:
        self.port = port
        self.baudrate = baudrate
        self._behavior = list(behavior)
        self._index = 0
        self.closed = False
        self.diagnostics = FakeDiagnostics(port, baudrate)

    def open(self) -> None:
        self.diagnostics.open = True

    def close(self) -> None:
        self.closed = True
        self.diagnostics.open = False

    def read_packet(self) -> ControllerPacket:
        self.diagnostics.thread_active = True
        if self.closed:
            raise RuntimeError("driver closed")
        if self._index < len(self._behavior):
            action = self._behavior[self._index]
            self._index += 1
        else:
            action = "wait"
        if isinstance(action, BaseException):
            self.diagnostics.last_exception = str(action)
            raise action
        if action == "wait":
            time.sleep(0.02)
            if self.closed:
                raise RuntimeError("driver closed")
            return PACKET
        return action  # type: ignore[return-value]


class ScriptedFactory:
    def __init__(self, sessions: list[list[object]]) -> None:
        self._sessions = list(sessions)
        self.instances: list[FakeDriver] = []

    def __call__(self, *, port: str, baudrate: int, startup_delay: float) -> FakeDriver:
        behavior = self._sessions.pop(0) if self._sessions else ["wait"]
        driver = FakeDriver(port, baudrate, behavior)
        self.instances.append(driver)
        return driver


class ConnectionManagerTest(unittest.TestCase):
    def _wait_for(self, predicate, *, timeout: float = 2.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.02)
        self.fail("timeout waiting for condition")

    def test_reconnects_after_disconnect_and_increments_generation(self) -> None:
        factory = ScriptedFactory([[PACKET, OSError("usb disconnected")], [PACKET, "wait"]])
        packets: list[tuple[int, ControllerPacket]] = []
        started: list[int] = []
        lost: list[str] = []
        states: list[str] = []
        manager = ArduinoConnectionManager(
            configured_port="/dev/ttyUSB0",
            baudrate=115200,
            startup_delay=0.0,
            driver_factory=factory,
            on_packet=lambda packet, generation: packets.append((generation, packet)),
            on_session_started=lambda generation, _identity: started.append(generation),
            on_session_lost=lost.append,
            on_state_change=lambda snapshot: states.append(str(snapshot["state"])),
        )
        with patch("klipper_cnc_assistant.input.connection_manager.os.path.exists", return_value=True), patch(
            "klipper_cnc_assistant.input.connection_manager.list_ports.comports",
            return_value=[FakePortInfo(device="/dev/ttyUSB0", vid=0x2341, pid=0x0043, serial_number="arduino-1")],
        ):
            manager.start()
            self._wait_for(lambda: started == [1, 2] and len(packets) >= 2)
            snapshot = manager.snapshot()
            self.assertEqual(snapshot["generation"], 2)
            self.assertEqual(snapshot["state"], ArduinoConnectionState.CONNECTED)
            self.assertEqual(snapshot["reconnects"], 1)
            self.assertTrue(factory.instances[0].closed)
            self.assertIn("usb disconnected", lost[-1])
            self.assertIn(ArduinoConnectionState.RETRY_WAIT, states)
            self.assertEqual({generation for generation, _packet in packets[:2]}, {1, 2})
            manager.stop()
            self.assertEqual(manager.snapshot()["state"], ArduinoConnectionState.STOPPED)

    def test_start_is_idempotent_and_does_not_spawn_two_threads(self) -> None:
        factory = ScriptedFactory([["wait"]])
        manager = ArduinoConnectionManager(
            configured_port="/dev/ttyUSB0",
            baudrate=115200,
            startup_delay=0.0,
            driver_factory=factory,
        )
        with patch("klipper_cnc_assistant.input.connection_manager.os.path.exists", return_value=True):
            manager.start()
            first_thread = manager.thread
            manager.start()
            self._wait_for(lambda: manager.thread is not None and manager.thread.is_alive() and len(factory.instances) == 1)
            self.assertIs(manager.thread, first_thread)
            self.assertEqual(len(factory.instances), 1)
            manager.stop()

    def test_manual_reconnect_reuses_same_manager_thread(self) -> None:
        factory = ScriptedFactory([[PACKET, "wait"], [PACKET, "wait"]])
        started: list[int] = []
        manager = ArduinoConnectionManager(
            configured_port="/dev/ttyUSB0",
            baudrate=115200,
            startup_delay=0.0,
            driver_factory=factory,
            on_session_started=lambda generation, _identity: started.append(generation),
        )
        with patch("klipper_cnc_assistant.input.connection_manager.os.path.exists", return_value=True):
            manager.start()
            self._wait_for(lambda: started == [1])
            thread = manager.thread
            manager.request_reconnect()
            self._wait_for(lambda: started == [1, 2])
            self.assertIs(manager.thread, thread)
            self.assertEqual(manager.snapshot()["generation"], 2)
            manager.stop()

    def test_rejects_other_device_when_exact_identity_is_known(self) -> None:
        manager = ArduinoConnectionManager(
            configured_port="/dev/ttyUSB0",
            baudrate=115200,
            startup_delay=0.0,
        )
        manager._known_identity = UsbIdentity(port="/dev/ttyUSB0", vid=0x2341, pid=0x0043, serial_number="arduino-1")
        with patch(
            "klipper_cnc_assistant.input.connection_manager.list_ports.comports",
            return_value=[FakePortInfo(device="/dev/ttyUSB1", vid=0x1111, pid=0x2222, serial_number="other")],
        ):
            with self.assertRaisesRegex(RuntimeError, "VID/PID"):
                manager._resolve_target()
        self.assertEqual(manager.snapshot()["rejected_devices"], 1)

    def test_snapshot_does_not_block_behind_state_callback_waiting_on_external_lock(self) -> None:
        factory = ScriptedFactory([["wait"]])
        runtime_lock = threading.Lock()
        callback_waiting = threading.Event()
        callback_finished = threading.Event()
        snapshot_done = threading.Event()

        def on_state_change(_snapshot: dict[str, object]) -> None:
            callback_waiting.set()
            with runtime_lock:
                callback_finished.set()

        manager = ArduinoConnectionManager(
            configured_port="/dev/ttyUSB0",
            baudrate=115200,
            startup_delay=0.0,
            driver_factory=factory,
            on_state_change=on_state_change,
        )
        with patch("klipper_cnc_assistant.input.connection_manager.os.path.exists", return_value=True):
            with runtime_lock:
                starter = threading.Thread(target=manager.start)
                starter.start()
                self._wait_for(callback_waiting.is_set)
                snap_thread = threading.Thread(target=lambda: (manager.snapshot(), snapshot_done.set()))
                snap_thread.start()
                self._wait_for(snapshot_done.is_set, timeout=0.5)
            self._wait_for(callback_finished.is_set)
            starter.join(timeout=1.0)
            snap_thread.join(timeout=1.0)
            manager.stop()


if __name__ == "__main__":
    unittest.main()
