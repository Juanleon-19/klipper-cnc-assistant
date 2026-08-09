from __future__ import annotations

from types import SimpleNamespace
import unittest

from klipper_cnc_assistant.api.routes import _reject_active_motion
from klipper_cnc_assistant.application.errors import ApplicationError
from klipper_cnc_assistant.execution import MeshExecutionService
from klipper_cnc_assistant.machine.recoverable_runtime import (
    RECOVERY_PENDING_MESSAGE,
    RecoverableMachineRuntime,
)
from klipper_cnc_assistant.machine.runtime import MachineRuntimeState

from tests.test_mesh_failure_recovery_hotfix import _PhysicalMapStub, _config


class MeshFailureRecoveryGuardTest(unittest.TestCase):
    def test_legacy_route_guard_stays_closed_while_mesh_cleanup_is_pending(self) -> None:
        runtime = RecoverableMachineRuntime(_config())
        with runtime._lock:
            runtime._state = MachineRuntimeState.MESH_PAUSED

        runtime.mark_motion_recovery_pending(RECOVERY_PENDING_MESSAGE)
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(machine_runtime=runtime)))

        self.assertEqual(runtime.snapshot()["state"], "MESH_PROBING")
        with self.assertRaises(ApplicationError):
            _reject_active_motion(request)

        self.assertTrue(runtime.clear_motion_recovery_pending())
        self.assertEqual(runtime.snapshot()["state"], "MESH_PAUSED")
        _reject_active_motion(request)

    def test_recovery_pending_does_not_hide_a_severe_runtime_state(self) -> None:
        runtime = RecoverableMachineRuntime(_config())
        with runtime._lock:
            runtime._state = MachineRuntimeState.DEGRADED

        runtime.mark_motion_recovery_pending(RECOVERY_PENDING_MESSAGE)

        self.assertEqual(runtime.snapshot()["state"], "DEGRADED")
        ownership = runtime.motion_ownership_snapshot()
        self.assertEqual(ownership["state"], "DEGRADED")
        self.assertFalse(ownership["can_start_motion"])

    def test_legacy_nonzero_retry_argument_cannot_enable_automatic_physical_retries(self) -> None:
        service = MeshExecutionService(_PhysicalMapStub(), max_point_retries=2)
        self.assertEqual(service.max_point_retries, 0)


if __name__ == "__main__":
    unittest.main()
