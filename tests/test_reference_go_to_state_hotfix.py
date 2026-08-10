from __future__ import annotations

import unittest

from tests.test_machine_runtime import ReferencePointMoveTest
from klipper_cnc_assistant.execution import MeshExecutionService
from klipper_cnc_assistant.machine.runtime import MachineRuntimeState


class _BlockedRuntime:
    def motion_ownership_snapshot(self):
        return {
            "state": "WAITING_FOR_XY_REFERENCE",
            "active": True,
            "can_start_motion": False,
            "active_operation": None,
            "movement_lock": False,
            "recovery_pending": False,
            "reason": "El runtime físico no está listo para iniciar el sondeo: WAITING_FOR_XY_REFERENCE.",
        }


class ReferenceGoToStateHotfixTest(unittest.TestCase):
    def test_go_to_reference_preserves_reference_captured_state(self) -> None:
        helper = ReferencePointMoveTest(methodName="test_moves_preparation_z_before_saved_cnc_xy_without_probing")
        runtime, _client = helper._runtime()
        with runtime._lock:
            runtime._state = MachineRuntimeState.REFERENCE_CAPTURED

        result = runtime.go_to_reference_point(reference_x=42.5, reference_y=67.25)

        self.assertTrue(result["accepted"])
        self.assertEqual(runtime.snapshot()["state"], "REFERENCE_CAPTURED")

    def test_go_to_reference_does_not_promote_uncaptured_reference(self) -> None:
        helper = ReferencePointMoveTest(methodName="test_moves_preparation_z_before_saved_cnc_xy_without_probing")
        runtime, _client = helper._runtime()
        with runtime._lock:
            runtime._state = MachineRuntimeState.WAITING_FOR_XY_REFERENCE

        runtime.go_to_reference_point(reference_x=42.5, reference_y=67.25)

        self.assertEqual(runtime.snapshot()["state"], "WAITING_FOR_XY_REFERENCE")

    def test_mesh_guard_preserves_runtime_block_reason_instead_of_none(self) -> None:
        service = MeshExecutionService(object())

        snapshot = service.motion_ownership_snapshot(
            runtime=_BlockedRuntime(),
            project_id="project",
            map_id="map",
        )

        self.assertFalse(snapshot["can_start_motion"])
        self.assertEqual(
            snapshot["reason"],
            "El runtime físico no está listo para iniciar el sondeo: WAITING_FOR_XY_REFERENCE.",
        )


if __name__ == "__main__":
    unittest.main()
