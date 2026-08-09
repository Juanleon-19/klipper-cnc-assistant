from __future__ import annotations

from queue import SimpleQueue
from types import SimpleNamespace
import threading
import time
import unittest

from klipper_cnc_assistant.application.errors import ApplicationError
from klipper_cnc_assistant.execution.recoverable_mesh_execution_service import RecoverableMeshExecutionService
from klipper_cnc_assistant.machine.config import MachineMode, MachineRuntimeConfig
from klipper_cnc_assistant.machine.recoverable_runtime import (
    RECOVERY_PENDING_MESSAGE,
    RecoverableMachineRuntime,
)
from klipper_cnc_assistant.machine.runtime import MachineRuntimeError, MachineRuntimeState, ProbeResult


def _config() -> MachineRuntimeConfig:
    return MachineRuntimeConfig(
        mode=MachineMode.PHYSICAL,
        auto_connect=False,
        moonraker_url="http://moonraker.invalid",
        moonraker_ws="ws://moonraker.invalid/websocket",
        serial_port="/dev/null",
        serial_baudrate=115200,
        safe_z_mm=10.0,
        reference_prep_z_mm=115.0,
        reference_prep_z_feed_mm_min=180.0,
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
        no_progress_timeout_s=0.02,
        settle_timeout_s=5.0,
        stable_samples=2,
        probe_step_mm=0.05,
        probe_open_stable_ms=50.0,
        probe_lower_speed_mm_s=1.0,
        probe_retract_mm=1.0,
        probe_retract_speed_mm_s=2.0,
    )


class _Machine:
    homed_axes = "xyz"

    def get_motion_snapshot(self):
        return {"x": 10.0, "y": 20.0, "z": 5.0}


class _PhysicalMapStub:
    def __init__(self) -> None:
        self.updates: list[dict] = []

    def update_execution_state(self, **kwargs):
        self.updates.append(dict(kwargs))
        return {"execution": dict(kwargs)}


class _BlockingProbeRuntime:
    def __init__(self) -> None:
        self.config = SimpleNamespace(no_progress_timeout_s=0.01)
        self.release = threading.Event()
        self.started = threading.Event()
        self.done = threading.Event()
        self.cancel_requested = False
        self.pending = False

    def probe_mesh_point(self, _point, **_kwargs):
        self.started.set()
        try:
            self.release.wait(1.0)
            return {"z_measured": 0.0}
        finally:
            self.done.set()

    def cancel_operation(self, **_kwargs):
        self.cancel_requested = True
        return self.motion_ownership_snapshot()

    def mark_motion_recovery_pending(self, _reason=None):
        self.pending = True
        return self.motion_ownership_snapshot()

    def clear_motion_recovery_pending(self):
        if not self.done.is_set():
            return False
        self.pending = False
        return True

    def motion_ownership_snapshot(self):
        alive = self.started.is_set() and not self.done.is_set()
        return {
            "state": "MESH_PROBING" if alive else "MESH_PAUSED",
            "active": alive or self.pending,
            "can_start_motion": not alive and not self.pending,
            "active_operation": {"operation_type": "mesh"} if alive else None,
            "movement_lock": alive,
            "recovery_pending": self.pending,
            "reason": RECOVERY_PENDING_MESSAGE if alive or self.pending else None,
        }


class MeshFailureRecoveryHotfixTest(unittest.TestCase):
    def _runtime(self) -> RecoverableMachineRuntime:
        runtime = RecoverableMachineRuntime(_config())
        runtime._require_physical_ready = lambda: None
        runtime._assert_safety_for_motion = lambda: None
        runtime._refresh_machine = lambda: None
        runtime._machine = _Machine()
        runtime._mesh_safe_z = lambda _machine, probe_config=None: 5.0
        runtime._move_absolute = lambda **_kwargs: None
        runtime._resolve_probe_profile = lambda _probe_config: object()
        runtime._packet_sequence = 1
        return runtime

    def test_successful_mesh_probe_returns_to_mesh_ready_and_releases_ownership(self) -> None:
        runtime = self._runtime()
        runtime._perform_probe_descent = lambda **_kwargs: ProbeResult(
            x_mm=10.0,
            y_mm=20.0,
            z_mm=0.125,
            captured_at="2026-08-09T00:00:00+00:00",
        )

        result = runtime.probe_mesh_point({"index": 0, "x_machine": 10.0, "y_machine": 20.0})
        ownership = runtime.motion_ownership_snapshot()

        self.assertEqual(result["z_measured"], 0.125)
        self.assertEqual(ownership["state"], "MESH_READY")
        self.assertFalse(ownership["active"])
        self.assertFalse(ownership["movement_lock"])
        self.assertIsNone(ownership["active_operation"])

    def test_recoverable_mesh_failure_pauses_and_releases_runtime_ownership(self) -> None:
        runtime = self._runtime()

        def fail_probe(**_kwargs):
            raise MachineRuntimeError("No se detectó contacto en el punto.")

        runtime._perform_probe_descent = fail_probe

        with self.assertRaisesRegex(MachineRuntimeError, "No se detectó contacto"):
            runtime.probe_mesh_point({"index": 3, "x_machine": 10.0, "y_machine": 20.0})

        ownership = runtime.motion_ownership_snapshot()
        self.assertEqual(ownership["state"], "MESH_PAUSED")
        self.assertFalse(ownership["active"])
        self.assertFalse(ownership["movement_lock"])
        self.assertIsNone(ownership["active_operation"])
        self.assertEqual(runtime._last_error, "No se detectó contacto en el punto.")

    def test_severe_runtime_state_is_not_downgraded_to_mesh_paused(self) -> None:
        runtime = self._runtime()

        def fail_degraded(**_kwargs):
            with runtime._lock:
                runtime._state = MachineRuntimeState.DEGRADED
            raise MachineRuntimeError("Telemetría perdida.")

        runtime._perform_probe_descent = fail_degraded

        with self.assertRaises(MachineRuntimeError):
            runtime.probe_mesh_point({"index": 0, "x_machine": 10.0, "y_machine": 20.0})

        ownership = runtime.motion_ownership_snapshot()
        self.assertEqual(ownership["state"], "DEGRADED")
        self.assertFalse(ownership["can_start_motion"])

    def test_stale_mesh_probing_without_ownership_reconciles_to_paused(self) -> None:
        runtime = self._runtime()
        with runtime._lock:
            runtime._state = MachineRuntimeState.MESH_PROBING
            runtime._active_operation = None
            runtime._motion_recovery_pending = False

        ownership = runtime.motion_ownership_snapshot()

        self.assertEqual(ownership["state"], "MESH_PAUSED")
        self.assertTrue(ownership["can_start_motion"])

    def test_recovery_pending_blocks_until_runtime_ownership_is_clear(self) -> None:
        runtime = self._runtime()
        with runtime._lock:
            runtime._state = MachineRuntimeState.MESH_PAUSED

        runtime.mark_motion_recovery_pending(RECOVERY_PENDING_MESSAGE)
        blocked = runtime.motion_ownership_snapshot()
        self.assertTrue(blocked["recovery_pending"])
        self.assertFalse(blocked["can_start_motion"])

        self.assertTrue(runtime.clear_motion_recovery_pending())
        ready = runtime.motion_ownership_snapshot()
        self.assertFalse(ready["recovery_pending"])
        self.assertTrue(ready["can_start_motion"])

    def test_watchdog_keeps_orphan_probe_blocked_until_inner_thread_finishes(self) -> None:
        physical = _PhysicalMapStub()
        service = RecoverableMeshExecutionService(
            physical,
            point_watchdog_timeout_s=0.01,
            point_watchdog_poll_s=0.001,
            point_watchdog_grace_s=0.001,
        )
        runtime = _BlockingProbeRuntime()
        progress_state = {
            "monotonic": time.monotonic(),
            "iso": "2026-08-09T00:00:00+00:00",
            "phase": "probe",
            "step_counter": 0,
            "persistence_count": 0,
            "persistence_duration_s": 0.0,
        }

        with self.assertRaises(TimeoutError):
            service._probe_with_watchdog(
                runtime,
                {"index": 0, "x_machine": 10.0, "y_machine": 20.0},
                probe_config=None,
                progress_callback=lambda *_args, **_kwargs: None,
                progress_updates=SimpleQueue(),
                project_id="project-1",
                map_id="map-1",
                point_index=0,
                total_points=1,
                worker_generation=1,
                progress_state=progress_state,
            )

        self.assertTrue(runtime.cancel_requested)
        blocked = service.motion_ownership_snapshot(
            runtime=runtime,
            project_id="project-1",
            map_id="map-1",
        )
        self.assertTrue(blocked["recovery_pending"])
        self.assertFalse(blocked["can_start_motion"])
        self.assertTrue(blocked["probe_thread_active"])

        runtime.release.set()
        self.assertTrue(runtime.done.wait(1.0))

        ready = service.motion_ownership_snapshot(
            runtime=runtime,
            project_id="project-1",
            map_id="map-1",
        )
        self.assertFalse(ready["recovery_pending"])
        self.assertFalse(ready["probe_thread_active"])
        self.assertTrue(ready["can_start_motion"])

    def test_missing_public_runtime_ownership_fails_closed(self) -> None:
        service = RecoverableMeshExecutionService(_PhysicalMapStub())

        class RuntimeWithoutOwnership:
            pass

        guard = service.motion_ownership_snapshot(
            runtime=RuntimeWithoutOwnership(),
            project_id="project-1",
            map_id="map-1",
        )

        self.assertFalse(guard["can_start_motion"])
        self.assertTrue(guard["recovery_pending"])
        self.assertIn("ownership físico", guard["reason"])

    def test_automatic_point_retries_are_disabled_by_default(self) -> None:
        service = RecoverableMeshExecutionService(_PhysicalMapStub())
        self.assertEqual(service.max_point_retries, 0)

    def test_start_all_refuses_motion_when_runtime_ownership_is_unknown(self) -> None:
        service = RecoverableMeshExecutionService(_PhysicalMapStub())

        class RuntimeWithoutOwnership:
            pass

        with self.assertRaises(ApplicationError):
            service.start_all(
                project_id="project-1",
                map_id="map-1",
                runtime=RuntimeWithoutOwnership(),
            )


if __name__ == "__main__":
    unittest.main()
