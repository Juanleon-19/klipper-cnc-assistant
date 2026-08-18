from __future__ import annotations

import unittest

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
        super().__init__(physical_config())
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
