from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one match, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")
    print(f"patched {path}")


replace_once(
    "src/klipper_cnc_assistant/machine/runtime.py",
    '''        context = self._begin_operation_context("reference_move")
        preparation_z = float(self.config.reference_prep_z_mm)
        try:
''',
    '''        with self._lock:
            preserve_reference_captured = self._state is MachineRuntimeState.REFERENCE_CAPTURED
        context = self._begin_operation_context("reference_move")
        preparation_z = float(self.config.reference_prep_z_mm)
        try:
''',
)

replace_once(
    "src/klipper_cnc_assistant/machine/runtime.py",
    '''            with self._lock:
                self._state = MachineRuntimeState.WAITING_FOR_XY_REFERENCE
                self._event("info", "REFERENCE_MOVE_COMPLETE: máquina ubicada en el punto de referencia.")
            return {"accepted": True, "reference_x": float(reference_x), "reference_y": float(reference_y), "preparation_z": preparation_z, "final_state": "REFERENCE_MOVE_COMPLETE", "message": "Máquina ubicada en el punto de referencia."}
''',
    '''            with self._lock:
                self._state = (
                    MachineRuntimeState.REFERENCE_CAPTURED
                    if preserve_reference_captured
                    else MachineRuntimeState.WAITING_FOR_XY_REFERENCE
                )
                self._event("info", "REFERENCE_MOVE_COMPLETE: máquina ubicada en el punto de referencia.")
            return {"accepted": True, "reference_x": float(reference_x), "reference_y": float(reference_y), "preparation_z": preparation_z, "final_state": "REFERENCE_MOVE_COMPLETE", "message": "Máquina ubicada en el punto de referencia."}
''',
)

replace_once(
    "src/klipper_cnc_assistant/execution/mesh_execution_service.py",
    '''        if blocked_by_state:
            reason = str(runtime_ownership.get("reason") or f"El runtime físico no está listo: {state}.")
        elif recovery_pending or runtime_ownership.get("movement_lock") or runtime_ownership.get("active_operation") is not None:
            reason = RECOVERY_PENDING_MESSAGE
        elif worker_same:
            reason = "La malla ya tiene un worker activo y no puede iniciarse dos veces."
''',
    '''        if blocked_by_state:
            reason = str(runtime_ownership.get("reason") or f"El runtime físico no está listo: {state}.")
        elif recovery_pending or runtime_ownership.get("movement_lock") or runtime_ownership.get("active_operation") is not None:
            reason = str(runtime_ownership.get("reason") or RECOVERY_PENDING_MESSAGE)
        elif runtime_active:
            reason = str(runtime_ownership.get("reason") or f"El runtime físico no está listo para iniciar el sondeo: {state or 'UNKNOWN'}.")
        elif worker_same:
            reason = "La malla ya tiene un worker activo y no puede iniciarse dos veces."
''',
)

Path("tests/test_reference_go_to_state_hotfix.py").write_text(
    '''from __future__ import annotations

import unittest

from klipper_cnc_assistant.execution import MeshExecutionService
from klipper_cnc_assistant.machine.runtime import MachineRuntimeState
from tests.test_machine_runtime import ReferencePointMoveTest


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
''',
    encoding="utf-8",
)
print("created tests/test_reference_go_to_state_hotfix.py")
print("Hotfix applied. No hardware commands were executed.")
