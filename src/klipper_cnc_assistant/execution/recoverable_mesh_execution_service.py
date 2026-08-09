from __future__ import annotations

from typing import Any

from .mesh_execution_service import MeshExecutionService, RECOVERY_PENDING_MESSAGE


class RecoverableMeshExecutionService(MeshExecutionService):
    """Fail-closed adapter for mesh recovery ownership checks.

    Production mesh execution must receive the public ownership contract from
    MachineRuntime.  Missing or malformed ownership information is treated as
    unsafe instead of falling back to an optimistic movement_lock=False value.
    """

    def _runtime_motion_ownership(self, runtime: Any) -> dict[str, Any]:
        snapshot_fn = getattr(runtime, "motion_ownership_snapshot", None)
        if snapshot_fn is None:
            return self._blocked_unknown_ownership("El runtime no expone ownership físico verificable.")

        try:
            snapshot = snapshot_fn()
        except Exception as error:
            return self._blocked_unknown_ownership(
                f"No fue posible verificar ownership físico: {error}"
            )

        if not isinstance(snapshot, dict):
            return self._blocked_unknown_ownership("El runtime devolvió ownership físico inválido.")

        required = {
            "state",
            "active",
            "can_start_motion",
            "active_operation",
            "movement_lock",
            "recovery_pending",
            "reason",
        }
        missing = sorted(required.difference(snapshot))
        if missing:
            return self._blocked_unknown_ownership(
                "Ownership físico incompleto; faltan: " + ", ".join(missing) + "."
            )

        return dict(snapshot)

    @staticmethod
    def _blocked_unknown_ownership(detail: str) -> dict[str, Any]:
        return {
            "state": "UNKNOWN",
            "active": True,
            "can_start_motion": False,
            "active_operation": None,
            "movement_lock": True,
            "recovery_pending": True,
            "reason": f"{RECOVERY_PENDING_MESSAGE} {detail}",
        }
