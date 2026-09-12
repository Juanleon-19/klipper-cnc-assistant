from __future__ import annotations

import threading
import unittest
from klipper_cnc_assistant.machine.physical_ownership import PhysicalMachineCoordinator
from unittest.mock import patch

from klipper_cnc_assistant.input.connection_manager import ArduinoConnectionStopError, UsbIdentity
from klipper_cnc_assistant.input.serial_driver import ControllerPacket
from klipper_cnc_assistant.machine.recoverable_runtime import RecoverableMachineRuntime
from klipper_cnc_assistant.machine.runtime import MachineRuntime, MachineRuntimeError, MachineRuntimeState

from tests.test_runtime_reconnect_hotfix import physical_config


PACKET = ControllerPacket(
    direction="CENTER",
    joystick_button=False,
    external_button=False,
    probe=False,
    x=512,
    y=512,
)


class FakeManager:
    def __init__(self, epoch: int, *, stop_error: Exception | None = None) -> None:
        self.manager_epoch = epoch
        self.driver = None
        self.thread = None
        self.stop_error = stop_error
        self.reconnect_requests = 0

    def request_reconnect(self) -> bool:
        self.reconnect_requests += 1
        return True

    def stop(self) -> None:
        if self.stop_error is not None:
            raise self.stop_error

    def snapshot(self) -> dict[str, object]:
        return {
            "state": "RETRY_WAIT",
            "manager_epoch": self.manager_epoch,
            "generation": 0,
            "configured_port": "/dev/serial/by-path/controller",
            "resolved_port": None,
            "connected_port": None,
            "usb_identity": None,
            "known_identity": None,
            "reconnects": 0,
            "rejected_devices": 0,
            "retry_wait_s": 5.0,
            "last_error": None,
            "last_session_error": None,
            "last_session_error_class": None,
            "last_session_error_phase": None,
            "last_session_failure": None,
            "session_received_packet": False,
            "thread_alive": False,
            "thread_id": None,
            "open": False,
        }


class RuntimeSerialLifecycleTest(unittest.TestCase):
    def _runtime_with_manager(
        self,
        *,
        recoverable: bool = False,
        epoch: int = 2,
        stop_error: Exception | None = None,
    ) -> tuple[MachineRuntime, FakeManager]:
        runtime_type = RecoverableMachineRuntime if recoverable else MachineRuntime
        runtime = runtime_type(physical_config(), coordinator=PhysicalMachineCoordinator())
        manager = FakeManager(epoch, stop_error=stop_error)
        with runtime._lock:
            runtime._client = object()  # type: ignore[assignment]
            runtime._connection_manager = manager  # type: ignore[assignment]
            runtime._connection_manager_epoch = epoch
            runtime._state = MachineRuntimeState.DEGRADED
        return runtime, manager

    def test_current_serial_loss_keeps_job_owner_blocked_in_recovery(self):
        from klipper_cnc_assistant.machine.physical_ownership import OwnerKind, OwnershipError
        runtime, _manager = self._runtime_with_manager(epoch=2)
        lease = runtime.physical_ownership.acquire(OwnerKind.JOB_EXECUTION, 'job')
        runtime._on_serial_session_lost(2, 0, 'unplug', {})
        self.assertEqual(runtime.physical_ownership.snapshot()['kind'], 'RECOVERY')
        self.assertEqual(runtime.physical_ownership.snapshot()['owner_id'], 'job')
        with self.assertRaises(OwnershipError):
            runtime.physical_ownership.validate(lease)

    def test_packet_motion_does_not_retain_runtime_mutex(self):
        from klipper_cnc_assistant.input.command_mapper import ControllerCommand
        runtime, _manager = self._runtime_with_manager(epoch=2)
        runtime._manual_enabled = True
        runtime._diagnostic_input_only = False
        runtime._ready_for_jog = True
        acquired = []
        def move(_command, **_kwargs):
            def check_lock():
                ok = runtime._lock.acquire(timeout=0.2)
                acquired.append(ok)
                if ok:
                    runtime._lock.release()
            checker = threading.Thread(target=check_lock)
            checker.start()
            checker.join(0.5)
        with patch.object(runtime._mapper, 'map', return_value=ControllerCommand(jog_x=1)), patch.object(runtime, '_manual_move', side_effect=move):
            runtime._handle_controller_packet_from_manager(2, PACKET, 0)
        self.assertEqual(acquired, [True])

    def test_late_callbacks_and_packets_from_old_manager_epoch_are_ignored(self) -> None:
        runtime, _manager = self._runtime_with_manager(epoch=2)
        runtime._last_error = "current"
        runtime._serial_generation = 7
        runtime._session_valid_packets = 3

        runtime._on_connection_state(1, {"state": "CONNECTED", "last_error": "old"})
        runtime._on_serial_session_started(1, 1, None)
        runtime._handle_controller_packet_from_manager(1, PACKET, 1)
        runtime._on_serial_session_lost(1, 1, "old loss", {"code": "old"})

        self.assertEqual(runtime._state, MachineRuntimeState.DEGRADED)
        self.assertEqual(runtime._last_error, "current")
        self.assertEqual(runtime._serial_generation, 7)
        self.assertEqual(runtime._session_valid_packets, 3)
        self.assertIsNone(runtime._last_packet)

    def test_late_state_and_duplicate_session_callback_cannot_rewind_current_generation(self) -> None:
        runtime, _manager = self._runtime_with_manager(epoch=2)
        runtime._on_serial_session_started(2, 1, None)
        runtime._on_serial_session_started(2, 2, None)
        self.assertEqual(runtime._serial_generation, 2)
        self.assertEqual(runtime._manager_session_generation, 2)

        runtime._on_connection_state(
            2,
            {"state": "DEGRADED", "generation": 1, "last_error": "old generation"},
        )
        runtime._on_serial_session_started(2, 2, None)

        self.assertEqual(runtime._state, MachineRuntimeState.DIAGNOSTIC)
        self.assertIsNone(runtime._last_error)
        self.assertEqual(runtime._serial_generation, 2)
        self.assertEqual(runtime._manager_session_generation, 2)

    def test_session_generation_is_global_when_managers_restart_at_one(self) -> None:
        runtime, first = self._runtime_with_manager(epoch=1)
        runtime._on_serial_session_started(1, 1, None)
        self.assertEqual(runtime._serial_generation, 1)

        second = FakeManager(2)
        with runtime._lock:
            runtime._connection_manager = second  # type: ignore[assignment]
            runtime._connection_manager_epoch = 2
            runtime._manager_session_generation = 0
        runtime._on_serial_session_started(2, 1, None)

        self.assertEqual(runtime._serial_generation, 2)
        self.assertEqual(runtime._manager_session_generation, 1)
        self.assertIsNot(first, second)

    def test_session_and_lifetime_packet_counters_have_distinct_semantics(self) -> None:
        runtime, _manager = self._runtime_with_manager(epoch=2)
        identity = UsbIdentity(port="/dev/ttyUSB1", vid=0x067B, pid=0x2303, location="1-3:1.0")

        runtime._on_serial_session_started(2, 1, identity)
        runtime._handle_controller_packet_from_manager(2, PACKET, 1)
        self.assertEqual(runtime._session_valid_packets, 1)
        self.assertEqual(runtime._counters.valid_packets, 1)

        runtime._on_serial_session_lost(2, 1, "unplug", {"code": "READ_HANGUP_OR_CONCURRENT_ACCESS"})
        self.assertEqual(runtime._session_valid_packets, 0)
        self.assertIsNone(runtime._last_packet_at)
        runtime._on_serial_session_started(2, 2, identity)
        runtime._handle_controller_packet_from_manager(2, PACKET, 2)

        self.assertEqual(runtime._session_valid_packets, 1)
        self.assertEqual(runtime._counters.valid_packets, 2)
        self.assertEqual(runtime._state, MachineRuntimeState.DIAGNOSTIC)
        self.assertFalse(runtime._manual_enabled)
        self.assertFalse(runtime._ready_for_jog)

    def test_manual_reconnect_is_blocked_by_active_operation(self) -> None:
        runtime, manager = self._runtime_with_manager()
        context = runtime._begin_operation_context("probe")
        try:
            with self.assertRaisesRegex(MachineRuntimeError, "operación física activa"):
                runtime.reconnect_arduino()
            self.assertEqual(manager.reconnect_requests, 0)
        finally:
            runtime._finish_operation_context(context)

    def test_manual_reconnect_is_blocked_by_movement_lock(self) -> None:
        runtime, manager = self._runtime_with_manager()
        self.assertTrue(runtime._movement_lock.acquire(blocking=False))
        try:
            with self.assertRaisesRegex(MachineRuntimeError, "movement_lock"):
                runtime.reconnect_arduino()
            self.assertEqual(manager.reconnect_requests, 0)
        finally:
            runtime._movement_lock.release()

    def test_manual_reconnect_is_blocked_by_recovery_pending(self) -> None:
        runtime, manager = self._runtime_with_manager(recoverable=True)
        runtime.mark_motion_recovery_pending()
        with self.assertRaisesRegex(MachineRuntimeError, "recuperación física pendiente"):
            runtime.reconnect_arduino()
        self.assertEqual(manager.reconnect_requests, 0)

    def test_manual_reconnect_clears_current_freshness_and_never_touches_moonraker(self) -> None:
        runtime, manager = self._runtime_with_manager()
        runtime._last_packet = PACKET
        runtime._last_packet_at = 123.0
        runtime._session_valid_packets = 12
        client = runtime._client

        snapshot = runtime.reconnect_arduino()

        self.assertEqual(manager.reconnect_requests, 1)
        self.assertIs(runtime._client, client)
        self.assertIsNone(runtime._last_packet_at)
        self.assertEqual(runtime._session_valid_packets, 0)
        self.assertEqual(snapshot["state"], MachineRuntimeState.DEGRADED)
        self.assertFalse(runtime._manual_enabled)

    def test_failed_stop_retains_manager_and_prevents_replacement(self) -> None:
        error = ArduinoConnectionStopError("owner still alive")
        runtime, manager = self._runtime_with_manager(stop_error=error)

        with self.assertRaisesRegex(MachineRuntimeError, "conserva ownership serial"):
            runtime.stop()

        self.assertIs(runtime._connection_manager, manager)
        self.assertEqual(runtime._connection_manager_epoch, manager.manager_epoch)
        with patch.object(runtime, "_build_connection_manager") as build:
            runtime.connect()
        build.assert_not_called()


if __name__ == "__main__":
    unittest.main()
