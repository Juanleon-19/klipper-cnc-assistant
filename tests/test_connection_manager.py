from __future__ import annotations

import errno
import os
import threading
import time
import unittest
from dataclasses import dataclass
from unittest.mock import patch

import serial

from klipper_cnc_assistant.input.connection_manager import (
    ArduinoConnectionErrorCode,
    ArduinoConnectionManager,
    ArduinoConnectionState,
    ArduinoConnectionStopError,
    UsbIdentity,
)
from klipper_cnc_assistant.input.serial_driver import ControllerPacket, SerialReadCancelled


PACKET = ControllerPacket(
    direction="CENTER",
    joystick_button=False,
    external_button=False,
    probe=False,
    x=512,
    y=512,
)


@dataclass
class FakePortInfo:
    device: str
    vid: int | None = None
    pid: int | None = None
    serial_number: str | None = None
    product: str | None = None
    manufacturer: str | None = None
    location: str | None = None


@dataclass(frozen=True)
class WaitForPacket:
    ready: threading.Event


class FakeDiagnostics:
    def __init__(self, port: str, baudrate: int) -> None:
        self.port = port
        self.baudrate = baudrate
        self.open = False
        self.thread_active = False


class FakeDriver:
    def __init__(
        self,
        port: str,
        baudrate: int,
        behavior: list[object],
        *,
        open_gate: threading.Event | None = None,
        ignore_cancel: bool = False,
        close_error: Exception | None = None,
    ) -> None:
        self.port = port
        self.baudrate = baudrate
        self._behavior = list(behavior)
        self._open_gate = open_gate
        self._ignore_cancel = ignore_cancel
        self._close_error = close_error
        self._cancelled = threading.Event()
        self.open_entered = threading.Event()
        self.close_calls = 0
        self.open_calls = 0
        self.open_thread_ids: list[int] = []
        self.read_thread_ids: list[int] = []
        self.close_thread_ids: list[int] = []
        self.read_active = False
        self.close_while_read = False
        self.diagnostics = FakeDiagnostics(port, baudrate)

    def open(self) -> None:
        self.open_calls += 1
        self.open_thread_ids.append(threading.get_ident())
        self.diagnostics.open = True
        self.open_entered.set()
        if self._open_gate is not None:
            while not self._open_gate.wait(timeout=0.01):
                if self._cancelled.is_set() and not self._ignore_cancel:
                    raise SerialReadCancelled("open cancelled")

    def cancel_read(self) -> bool:
        if not self._ignore_cancel:
            self._cancelled.set()
        return not self._ignore_cancel

    def close(self) -> None:
        self.close_calls += 1
        self.close_thread_ids.append(threading.get_ident())
        self.close_while_read = self.close_while_read or self.read_active
        self.diagnostics.open = False
        self.diagnostics.thread_active = False
        self._cancelled.set()
        if self._close_error is not None:
            raise self._close_error

    def read_packet(self) -> ControllerPacket:
        self.diagnostics.thread_active = True
        self.read_thread_ids.append(threading.get_ident())
        self.read_active = True
        try:
            if self._cancelled.is_set():
                raise SerialReadCancelled("read cancelled")
            action = self._behavior.pop(0) if self._behavior else "block"
            if isinstance(action, BaseException):
                raise action
            if isinstance(action, WaitForPacket):
                while not action.ready.wait(timeout=0.01):
                    if self._cancelled.is_set():
                        raise SerialReadCancelled("read cancelled")
                return PACKET
            if action == "block":
                self._cancelled.wait()
                raise SerialReadCancelled("read cancelled")
            return action  # type: ignore[return-value]
        finally:
            self.read_active = False


class ScriptedFactory:
    def __init__(self, sessions: list[dict[str, object] | list[object]]) -> None:
        self._sessions = list(sessions)
        self.instances: list[FakeDriver] = []

    def __call__(self, *, port: str, baudrate: int, startup_delay: float, require_exclusive: bool = True) -> FakeDriver:
        del startup_delay
        raw = self._sessions.pop(0) if self._sessions else [PACKET]
        options = raw if isinstance(raw, dict) else {"behavior": raw}
        driver = FakeDriver(
            port,
            baudrate,
            list(options.get("behavior", [PACKET])),
            open_gate=options.get("open_gate"),  # type: ignore[arg-type]
            ignore_cancel=bool(options.get("ignore_cancel", False)),
            close_error=options.get("close_error"),  # type: ignore[arg-type]
        )
        self.instances.append(driver)
        driver.diagnostics.exclusive_requested = require_exclusive
        driver.diagnostics.exclusive_supported = True
        return driver


class ConnectionManagerTest(unittest.TestCase):
    def _wait_for(self, predicate, *, timeout: float = 2.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            threading.Event().wait(0.005)
        self.fail("timeout waiting for condition")

    def _manager(self, factory: ScriptedFactory, **callbacks) -> ArduinoConnectionManager:
        return ArduinoConnectionManager(
            configured_port="/dev/serial/by-path/controller",
            baudrate=115200,
            startup_delay=0.0,
            manager_epoch=41,
            physical_mode=False,
            driver_factory=factory,
            **callbacks,
        )

    @staticmethod
    def _port(device: str = "/dev/ttyUSB1", *, vid: int = 0x067B, pid: int = 0x2303, location: str = "1-3:1.0") -> FakePortInfo:
        return FakePortInfo(device=device, vid=vid, pid=pid, serial_number=None, location=location)

    def test_startup_without_port_waits_without_crashing(self) -> None:
        manager = self._manager(ScriptedFactory([]))
        with patch("klipper_cnc_assistant.input.connection_manager.os.path.exists", return_value=False):
            manager.start()
            self._wait_for(lambda: manager.snapshot()["state"] == ArduinoConnectionState.RETRY_WAIT)
            snapshot = manager.snapshot()
            self.assertTrue(snapshot["thread_alive"])
            self.assertEqual(snapshot["last_session_error_class"], ArduinoConnectionErrorCode.PORT_ABSENT)
            manager.stop()

    def test_port_appears_later_and_connects_automatically(self) -> None:
        available = threading.Event()
        factory = ScriptedFactory([[PACKET]])
        manager = self._manager(factory)
        with patch(
            "klipper_cnc_assistant.input.connection_manager.os.path.exists",
            side_effect=lambda _path: available.is_set(),
        ), patch(
            "klipper_cnc_assistant.input.connection_manager.os.path.realpath",
            return_value="/dev/ttyUSB1",
        ), patch(
            "klipper_cnc_assistant.input.connection_manager.list_ports.comports",
            return_value=[self._port()],
        ):
            manager.start()
            self._wait_for(lambda: manager.snapshot()["state"] == ArduinoConnectionState.RETRY_WAIT)
            available.set()
            self._wait_for(lambda: manager.snapshot()["state"] == ArduinoConnectionState.CONNECTED)
            self.assertEqual(manager.snapshot()["connected_port"], "/dev/ttyUSB1")
            manager.stop()

    def test_connected_requires_first_valid_packet(self) -> None:
        packet_ready = threading.Event()
        factory = ScriptedFactory([[WaitForPacket(packet_ready)]])
        manager = self._manager(factory)
        with patch("klipper_cnc_assistant.input.connection_manager.os.path.exists", return_value=True):
            manager.start()
            self._wait_for(lambda: bool(factory.instances) and factory.instances[0].diagnostics.open)
            self.assertEqual(manager.snapshot()["state"], ArduinoConnectionState.CONNECTING)
            self.assertEqual(manager.snapshot()["generation"], 0)
            packet_ready.set()
            self._wait_for(lambda: manager.snapshot()["state"] == ArduinoConnectionState.CONNECTED)
            self.assertEqual(manager.snapshot()["generation"], 1)
            manager.stop()

    def test_unplug_during_read_is_recoverable_and_replug_uses_new_driver(self) -> None:
        readiness_error = serial.SerialException(
            "device reports readiness to read but returned no data (device disconnected or multiple access on port?)"
        )
        factory = ScriptedFactory([[PACKET, readiness_error], [PACKET]])
        started: list[tuple[int, int]] = []
        lost: list[dict[str, object]] = []
        manager = self._manager(
            factory,
            on_session_started=lambda epoch, generation, _identity: started.append((epoch, generation)),
            on_session_lost=lambda _epoch, _generation, _message, failure: lost.append(failure),
        )
        with patch("klipper_cnc_assistant.input.connection_manager.os.path.exists", return_value=True):
            manager.start()
            self._wait_for(lambda: started == [(41, 1), (41, 2)])
            self.assertEqual(len(factory.instances), 2)
            self.assertIsNot(factory.instances[0], factory.instances[1])
            self.assertEqual(factory.instances[0].close_calls, 1)
            self.assertEqual(lost[0]["code"], ArduinoConnectionErrorCode.READ_HANGUP_OR_CONCURRENT_ACCESS)
            manager.stop()
            self.assertTrue(all(driver.close_calls == 1 for driver in factory.instances))

    def test_multiple_reconnects_keep_one_owner_and_close_every_driver_once(self) -> None:
        factory = ScriptedFactory([[PACKET], [PACKET], [PACKET]])
        started: list[int] = []
        manager = self._manager(
            factory,
            on_session_started=lambda _epoch, generation, _identity: started.append(generation),
        )
        with patch("klipper_cnc_assistant.input.connection_manager.os.path.exists", return_value=True):
            manager.start()
            self._wait_for(lambda: started == [1])
            owner = manager.thread
            manager.request_reconnect()
            self._wait_for(lambda: started == [1, 2])
            manager.request_reconnect()
            self._wait_for(lambda: started == [1, 2, 3])
            self.assertIs(manager.thread, owner)
            self.assertEqual(manager.snapshot()["generation"], 3)
            self.assertEqual(manager.snapshot()["reconnects"], 2)
            self.assertEqual(sum(driver.diagnostics.open for driver in factory.instances), 1)
            self.assertTrue(all(driver.close_calls == 1 for driver in factory.instances[:-1]))
            self.assertTrue(all(not driver.close_while_read for driver in factory.instances))
            manager.stop()
            self.assertTrue(all(driver.open_calls == 1 and driver.close_calls == 1 for driver in factory.instances))
            owner_id = manager.snapshot()["thread_id"]
            for driver in factory.instances:
                self.assertEqual(set(driver.open_thread_ids + driver.read_thread_ids + driver.close_thread_ids), {owner_id})

    def test_ebadf_read_and_close_are_diagnostic_and_manager_recovers(self) -> None:
        read_error = serial.SerialException("read failed: [Errno 9] Bad file descriptor")
        close_error = OSError(errno.EBADF, "Bad file descriptor")
        factory = ScriptedFactory([
            {"behavior": [PACKET, read_error], "close_error": close_error},
            [PACKET],
        ])
        started: list[int] = []
        lost: list[dict[str, object]] = []
        manager = self._manager(
            factory,
            on_session_started=lambda _epoch, generation, _identity: started.append(generation),
            on_session_lost=lambda _epoch, _generation, _message, failure: lost.append(failure),
        )

        with patch("klipper_cnc_assistant.input.connection_manager.os.path.exists", return_value=True):
            manager.start()
            self._wait_for(lambda: started == [1, 2])
            self.assertEqual(lost[0]["code"], ArduinoConnectionErrorCode.READ_HANGUP_OR_CONCURRENT_ACCESS)
            self.assertEqual(lost[0]["phase"], "read")
            self.assertEqual(lost[0]["errno"], errno.EBADF)
            self.assertEqual(factory.instances[0].close_calls, 1)
            self.assertTrue(manager.snapshot()["thread_alive"])
            self.assertEqual(manager.snapshot()["state"], ArduinoConnectionState.CONNECTED)
            manager.stop()

        self.assertEqual([driver.close_calls for driver in factory.instances], [1, 1])
        self.assertTrue(all(not driver.close_while_read for driver in factory.instances))

    def test_stop_during_open_cancels_and_closes_published_driver(self) -> None:
        gate = threading.Event()
        factory = ScriptedFactory([{"behavior": [PACKET], "open_gate": gate}])
        manager = self._manager(factory)
        with patch("klipper_cnc_assistant.input.connection_manager.os.path.exists", return_value=True):
            manager.start()
            self._wait_for(lambda: bool(factory.instances) and factory.instances[0].open_entered.is_set())
            self.assertIs(manager.driver, factory.instances[0])
            manager.stop()
            self.assertEqual(factory.instances[0].close_calls, 1)
            self.assertIsNone(manager.driver)
            self.assertEqual(manager.snapshot()["state"], ArduinoConnectionState.STOPPED)

    def test_reconnect_during_connecting_is_not_lost(self) -> None:
        gate = threading.Event()
        factory = ScriptedFactory([
            {"behavior": [PACKET], "open_gate": gate},
            [PACKET],
        ])
        started: list[int] = []
        manager = self._manager(
            factory,
            on_session_started=lambda _epoch, generation, _identity: started.append(generation),
        )
        with patch("klipper_cnc_assistant.input.connection_manager.os.path.exists", return_value=True):
            manager.start()
            self._wait_for(lambda: bool(factory.instances) and factory.instances[0].open_entered.is_set())
            manager.request_reconnect()
            self._wait_for(lambda: started == [1])
            self.assertEqual(len(factory.instances), 2)
            self.assertEqual(factory.instances[0].close_calls, 1)
            manager.stop()

    def test_manual_reconnect_wakes_retry_wait_immediately(self) -> None:
        available = threading.Event()
        factory = ScriptedFactory([[PACKET]])
        manager = self._manager(factory)
        with patch(
            "klipper_cnc_assistant.input.connection_manager.os.path.exists",
            side_effect=lambda _path: available.is_set(),
        ):
            manager.start()
            self._wait_for(lambda: manager.snapshot()["state"] == ArduinoConnectionState.RETRY_WAIT)
            available.set()
            started_at = time.monotonic()
            manager.request_reconnect()
            self._wait_for(lambda: manager.snapshot()["state"] == ArduinoConnectionState.CONNECTED)
            self.assertLess(time.monotonic() - started_at, 0.4)
            manager.stop()

    def test_stop_does_not_claim_stopped_when_owner_thread_is_stuck(self) -> None:
        gate = threading.Event()
        factory = ScriptedFactory([
            {"behavior": [PACKET], "open_gate": gate, "ignore_cancel": True},
        ])
        manager = ArduinoConnectionManager(
            configured_port="/dev/serial/by-path/controller",
            baudrate=115200,
            startup_delay=0.0,
            manager_epoch=41,
            driver_factory=factory,
            stop_timeout=0.03,
            physical_mode=False,
        )
        with patch("klipper_cnc_assistant.input.connection_manager.os.path.exists", return_value=True):
            manager.start()
            self._wait_for(lambda: bool(factory.instances) and factory.instances[0].open_entered.is_set())
            with self.assertRaises(ArduinoConnectionStopError):
                manager.stop()
            self.assertIsNotNone(manager.thread)
            self.assertTrue(manager.thread.is_alive())  # type: ignore[union-attr]
            self.assertIs(manager.driver, factory.instances[0])
            self.assertNotEqual(manager.snapshot()["state"], ArduinoConnectionState.STOPPED)
            gate.set()
            self._wait_for(lambda: manager.snapshot()["state"] == ArduinoConnectionState.STOPPED)
            manager.stop()

    def test_state_callback_does_not_hold_manager_lock(self) -> None:
        factory = ScriptedFactory([[PACKET]])
        external_lock = threading.Lock()
        callback_waiting = threading.Event()
        snapshot_done = threading.Event()

        def on_state_change(_epoch: int, _snapshot: dict[str, object]) -> None:
            callback_waiting.set()
            with external_lock:
                pass

        manager = self._manager(factory, on_state_change=on_state_change)
        with patch("klipper_cnc_assistant.input.connection_manager.os.path.exists", return_value=True):
            with external_lock:
                starter = threading.Thread(target=manager.start)
                starter.start()
                self._wait_for(callback_waiting.is_set)
                snap_thread = threading.Thread(target=lambda: (manager.snapshot(), snapshot_done.set()))
                snap_thread.start()
                self._wait_for(snapshot_done.is_set, timeout=0.3)
            starter.join(timeout=1.0)
            snap_thread.join(timeout=1.0)
            manager.stop()

    def test_distractor_serial_device_is_never_selected(self) -> None:
        manager = self._manager(ScriptedFactory([]))
        with patch("klipper_cnc_assistant.input.connection_manager.os.path.exists", return_value=False), patch(
            "klipper_cnc_assistant.input.connection_manager.list_ports.comports",
            return_value=[self._port("/dev/ttyUSB9")],
        ):
            with self.assertRaisesRegex(RuntimeError, "No se seleccionará otro dispositivo"):
                manager._resolve_target()

    def test_by_path_identity_matches_canonical_device(self) -> None:
        manager = self._manager(ScriptedFactory([]))

        def canonical(path: str) -> str:
            if path == "/dev/serial/by-path/controller":
                return "/dev/ttyUSB1"
            return path

        with patch("klipper_cnc_assistant.input.connection_manager.os.path.realpath", side_effect=canonical), patch(
            "klipper_cnc_assistant.input.connection_manager.list_ports.comports",
            return_value=[self._port()],
        ):
            identity = manager._port_identity("/dev/serial/by-path/controller")
        self.assertIsNotNone(identity)
        self.assertEqual(identity.port, "/dev/ttyUSB1")  # type: ignore[union-attr]
        self.assertEqual(identity.vid, 0x067B)  # type: ignore[union-attr]
        self.assertFalse(identity.exact)  # type: ignore[union-attr]

    def test_vid_pid_or_location_mismatch_is_fail_closed(self) -> None:
        known = UsbIdentity(
            port="/dev/ttyUSB1",
            vid=0x067B,
            pid=0x2303,
            serial_number=None,
            location="1-3:1.0",
        )
        cases = (
            (self._port(vid=0x1234), "VID"),
            (self._port(pid=0x9999), "PID"),
            (self._port(location="1-4:1.0"), "ubicación USB"),
        )
        for observed, message in cases:
            with self.subTest(message=message):
                manager = ArduinoConnectionManager(
                    configured_port="/dev/serial/by-path/controller",
                    baudrate=115200,
                    startup_delay=0.0,
                    known_identity=known,
                )
                with patch("klipper_cnc_assistant.input.connection_manager.os.path.exists", return_value=True), patch(
                    "klipper_cnc_assistant.input.connection_manager.os.path.realpath",
                    side_effect=lambda path: "/dev/ttyUSB1" if path == "/dev/serial/by-path/controller" else path,
                ), patch(
                    "klipper_cnc_assistant.input.connection_manager.list_ports.comports",
                    return_value=[observed],
                ):
                    with self.assertRaisesRegex(RuntimeError, message):
                        manager._resolve_target()
                self.assertEqual(manager.snapshot()["rejected_devices"], 1)

    def test_readiness_no_data_error_has_normalized_classification(self) -> None:
        error = serial.SerialException(
            "device reports readiness to read but returned no data (device disconnected or multiple access on port?)"
        )
        code = ArduinoConnectionManager._classify_error(error, "read")
        self.assertEqual(code, ArduinoConnectionErrorCode.READ_HANGUP_OR_CONCURRENT_ACCESS)

    def test_exclusive_busy_open_has_normalized_classification(self) -> None:
        error = serial.SerialException(16, "Could not exclusively lock port: Resource busy")
        code = ArduinoConnectionManager._classify_error(error, "open")
        self.assertEqual(code, ArduinoConnectionErrorCode.OPEN_BUSY)

    def test_usb_serial_zero_is_not_an_exact_identity(self) -> None:
        identity = UsbIdentity(port="/dev/ttyUSB1", vid=0x067B, pid=0x2303, serial_number="0")
        self.assertFalse(identity.exact)


if __name__ == "__main__":
    unittest.main()
