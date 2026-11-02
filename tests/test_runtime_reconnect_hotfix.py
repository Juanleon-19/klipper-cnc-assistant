from __future__ import annotations

import unittest
import threading
from unittest.mock import patch
from klipper_cnc_assistant.machine.physical_ownership import PhysicalMachineCoordinator

from klipper_cnc_assistant.api.machine_routes import build_machine_router
from klipper_cnc_assistant.machine.config import MachineMode, MachineRuntimeConfig
from klipper_cnc_assistant.machine.recoverable_runtime import RecoverableMachineRuntime
from klipper_cnc_assistant.machine.runtime import MachineRuntimeError, MachineRuntimeState


def physical_config() -> MachineRuntimeConfig:
    return MachineRuntimeConfig(
        mode=MachineMode.PHYSICAL,
        auto_connect=False,
        moonraker_url="http://127.0.0.1:7126",
        moonraker_ws="ws://127.0.0.1:7126/websocket",
        serial_port="/dev/null",
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
        stable_samples=2,
        probe_step_mm=0.05,
        probe_open_stable_ms=50.0,
        probe_lower_speed_mm_s=1.0,
        probe_retract_mm=1.0,
        probe_retract_speed_mm_s=2.0,
    )


class FakeReconnectRuntime(RecoverableMachineRuntime):
    def __init__(self) -> None:
        super().__init__(physical_config(), coordinator=PhysicalMachineCoordinator())
        self.stop_calls = 0
        self.connect_calls = 0

    def stop(self) -> None:
        self.stop_calls += 1
        with self._lock:
            self._state = MachineRuntimeState.DISCONNECTED

    def connect(self) -> dict[str, object]:
        self.connect_calls += 1
        with self._lock:
            self._state = MachineRuntimeState.DIAGNOSTIC
        return self.snapshot()



class AdministrativeConnectExclusionTest(unittest.TestCase):
    """All transport startup IO is mocked; never talk to real machinery."""

    def test_connection_denied_during_administrative_reset_and_disconnect(self):
        coordinator = PhysicalMachineCoordinator()
        runtime = RecoverableMachineRuntime(physical_config(), coordinator=coordinator)
        token = coordinator.begin_administration(expected_revision=coordinator.snapshot()['revision'])
        try:
            with patch.object(runtime, '_connect_locked') as connect_impl:
                with self.assertRaisesRegex(MachineRuntimeError, 'administrativa activa'):
                    runtime.connect()
                connect_impl.assert_not_called()
            coordinator.administrative_disconnected(token)
            with patch.object(runtime, '_connect_locked') as connect_impl:
                with self.assertRaisesRegex(MachineRuntimeError, 'administrativa activa'):
                    runtime.connect()
                connect_impl.assert_not_called()
        finally:
            coordinator.end_administration(token)
        self.assertFalse(coordinator.snapshot()['connection_active'])

    def test_administration_denied_while_transport_startup_in_progress(self):
        from klipper_cnc_assistant.machine.physical_ownership import OwnershipError
        coordinator = PhysicalMachineCoordinator()
        runtime = RecoverableMachineRuntime(physical_config(), coordinator=coordinator)
        entered, release = threading.Event(), threading.Event()
        failures = []
        def fake_startup():
            entered.set()
            if not release.wait(5):
                raise AssertionError('Fake startup not released')
            return {'fake': True}
        def worker():
            try:
                runtime.connect()
            except Exception as error:
                failures.append(error)
        with patch.object(runtime, '_connect_locked', side_effect=fake_startup):
            thread = threading.Thread(target=worker)
            thread.start()
            try:
                self.assertTrue(entered.wait(5))
                self.assertTrue(coordinator.snapshot()['connection_active'])
                with self.assertRaisesRegex(OwnershipError, 'bloqueada'):
                    coordinator.begin_administration(expected_revision=coordinator.snapshot()['revision'])
                self.assertFalse(coordinator.snapshot()['administrative_active'])
            finally:
                release.set()
                thread.join(timeout=5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(failures, [])
        self.assertFalse(coordinator.snapshot()['connection_active'])
        token = coordinator.begin_administration(expected_revision=coordinator.snapshot()['revision'])
        coordinator.end_administration(token)

    def test_connection_possible_for_recovery_when_no_admin_is_active(self):
        coordinator = PhysicalMachineCoordinator(uncertain=True)
        runtime = RecoverableMachineRuntime(physical_config(), coordinator=coordinator)
        with patch.object(runtime, '_connect_locked', return_value={'fake': True}):
            self.assertEqual(runtime.connect(), {'fake': True})
        self.assertTrue(coordinator.snapshot()['recovery_pending'])
        self.assertFalse(coordinator.snapshot()['connection_active'])

    def test_redundant_connection_does_not_change_active_revision(self):
        coordinator = PhysicalMachineCoordinator()
        runtime = RecoverableMachineRuntime(physical_config(), coordinator=coordinator)
        runtime._client = object()  # Existing fake session, no hardware IO.
        before = coordinator.snapshot()['revision']
        with patch.object(runtime, '_connect_locked', return_value={'connected': True}) as startup:
            self.assertEqual(runtime.connect(), {'connected': True})
            startup.assert_called_once()
        self.assertEqual(coordinator.snapshot()['revision'], before)
        self.assertFalse(coordinator.snapshot()['connection_active'])

    def test_connection_lease_closed_after_transport_failure(self):
        coordinator = PhysicalMachineCoordinator()
        runtime = RecoverableMachineRuntime(physical_config(), coordinator=coordinator)
        with patch.object(runtime, '_connect_locked', side_effect=RuntimeError('fake IO failure')):
            with self.assertRaisesRegex(RuntimeError, 'fake IO failure'):
                runtime.connect()
        self.assertFalse(coordinator.snapshot()['connection_active'])


class RuntimeReconnectHotfixTest(unittest.TestCase):
    def test_reconnect_runtime_endpoint_is_registered_as_post(self) -> None:
        router = build_machine_router()
        matches = [
            route
            for route in router.routes
            if getattr(route, "path", None) == "/api/machine/reconnect-runtime"
        ]

        self.assertEqual(len(matches), 1)
        self.assertIn("POST", matches[0].methods)

    def test_reconnect_restarts_only_runtime_session_when_idle(self) -> None:
        runtime = FakeReconnectRuntime()
        with runtime._lock:
            runtime._state = MachineRuntimeState.ERROR
            runtime._last_error = "telemetría perdida"

        snapshot = runtime.reconnect_runtime()

        self.assertEqual(runtime.stop_calls, 1)
        self.assertEqual(runtime.connect_calls, 1)
        self.assertEqual(snapshot["state"], MachineRuntimeState.DIAGNOSTIC.value)

    def test_reconnect_refuses_active_operation(self) -> None:
        runtime = FakeReconnectRuntime()
        context = runtime._begin_operation_context("mesh")
        try:
            with self.assertRaisesRegex(MachineRuntimeError, "finalización segura|movimiento"):
                runtime.reconnect_runtime()
            self.assertEqual(runtime.stop_calls, 0)
            self.assertEqual(runtime.connect_calls, 0)
        finally:
            runtime._finish_operation_context(context)

    def test_reconnect_refuses_held_movement_lock(self) -> None:
        runtime = FakeReconnectRuntime()
        self.assertTrue(runtime._movement_lock.acquire(blocking=False))
        try:
            with self.assertRaisesRegex(MachineRuntimeError, "finalización segura|movimiento"):
                runtime.reconnect_runtime()
            self.assertEqual(runtime.stop_calls, 0)
            self.assertEqual(runtime.connect_calls, 0)
        finally:
            runtime._movement_lock.release()

    def test_reconnect_refuses_mesh_cleanup_pending(self) -> None:
        runtime = FakeReconnectRuntime()
        runtime.mark_motion_recovery_pending()

        with self.assertRaisesRegex(MachineRuntimeError, "finalización segura"):
            runtime.reconnect_runtime()

        self.assertEqual(runtime.stop_calls, 0)
        self.assertEqual(runtime.connect_calls, 0)

    def test_reconnect_refuses_motion_state_even_without_visible_ownership(self) -> None:
        runtime = FakeReconnectRuntime()
        with runtime._lock:
            runtime._state = MachineRuntimeState.HOMING

        with self.assertRaisesRegex(MachineRuntimeError, "HOMING"):
            runtime.reconnect_runtime()

        self.assertEqual(runtime.stop_calls, 0)
        self.assertEqual(runtime.connect_calls, 0)


if __name__ == "__main__":
    unittest.main()
