from __future__ import annotations

import time
from typing import Any

from .runtime import MachineRuntime, MachineRuntimeError, MachineRuntimeState


RECOVERY_PENDING_MESSAGE = (
    "Esperando finalización segura del sondeo anterior. "
    "No se iniciará un nuevo movimiento."
)

_BLOCKING_STATES = {
    MachineRuntimeState.DISCONNECTED,
    MachineRuntimeState.CONNECTING,
    MachineRuntimeState.DIAGNOSTIC,
    MachineRuntimeState.READY_FOR_HOME,
    MachineRuntimeState.HOMING,
    MachineRuntimeState.WAITING_SAFE_Z,
    MachineRuntimeState.MOVING_TO_SAFE_Z,
    MachineRuntimeState.MOVING_TO_CENTER,
    MachineRuntimeState.WAITING_FOR_XY_REFERENCE,
    MachineRuntimeState.REFERENCE_ARMED,
    MachineRuntimeState.PROBING_REFERENCE,
    MachineRuntimeState.DEGRADED,
    MachineRuntimeState.ERROR,
    MachineRuntimeState.CANCELLED,
    MachineRuntimeState.STOPPING,
}

_SEVERE_STATES = {
    MachineRuntimeState.DEGRADED,
    MachineRuntimeState.ERROR,
    MachineRuntimeState.DISCONNECTED,
    MachineRuntimeState.STOPPING,
    MachineRuntimeState.CANCELLED,
}


class RecoverableMachineRuntime(MachineRuntime):
    """MachineRuntime with explicit, fail-closed ownership for mesh recovery.

    The normal runtime owns the physical movement lock and operation context.
    This subclass only makes that ownership public and gives mesh probing a
    recoverable failure state. It does not relax any motion or safety guard.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._motion_recovery_pending = False
        self._motion_recovery_reason: str | None = None

    def snapshot(self) -> dict[str, Any]:
        """Expose cleanup as active probing until physical ownership is clear.

        Legacy route guards still inspect ``snapshot()['state']``. During a
        watchdog cleanup the inner probe may already have moved the internal
        state to MESH_PAUSED while its ownership is still being reconciled.
        Publishing MESH_PROBING for that short interval keeps those existing
        guards fail-closed without weakening their behaviour.
        """
        payload = super().snapshot()
        with self._lock:
            recovery_pending = bool(self._motion_recovery_pending)
            recovery_reason = self._motion_recovery_reason
        if recovery_pending:
            payload["state"] = MachineRuntimeState.MESH_PROBING.value
        payload["recovery_pending"] = recovery_pending
        payload["recovery_reason"] = recovery_reason
        return payload

    def motion_ownership_snapshot(self) -> dict[str, Any]:
        """Return an atomic view of physical ownership used by mesh guards."""
        with self._lock:
            context = self._active_operation
            movement_lock = self._movement_lock.locked()
            recovery_pending = bool(self._motion_recovery_pending)

            # A mesh state without any remaining ownership is stale, not proof
            # that a movement is still running. Reconcile it here while all
            # runtime ownership signals are observed under the runtime lock.
            if (
                self._state is MachineRuntimeState.MESH_PROBING
                and context is None
                and not movement_lock
                and not recovery_pending
            ):
                self._state = MachineRuntimeState.MESH_PAUSED

            state = self._state
            active_operation = None
            if context is not None:
                active_operation = {
                    "operation_id": context.operation_id,
                    "operation_type": context.operation_type,
                    "generation": context.generation,
                    "cancel_event_is_set": context.cancel_event.is_set(),
                }

            blocked_by_state = state in _BLOCKING_STATES
            active = bool(active_operation is not None or movement_lock or recovery_pending or blocked_by_state)

            if recovery_pending or active_operation is not None or movement_lock:
                reason = self._motion_recovery_reason or RECOVERY_PENDING_MESSAGE
            elif blocked_by_state:
                reason = f"El runtime físico no está listo para iniciar el sondeo: {state.value}."
            else:
                reason = None

            return {
                "state": state.value,
                "active": active,
                "can_start_motion": not active,
                "active_operation": active_operation,
                "movement_lock": movement_lock,
                "recovery_pending": recovery_pending,
                "reason": reason,
            }

    def mark_motion_recovery_pending(self, reason: str | None = None) -> dict[str, Any]:
        with self._lock:
            self._motion_recovery_pending = True
            self._motion_recovery_reason = reason or RECOVERY_PENDING_MESSAGE
        return self.motion_ownership_snapshot()

    def clear_motion_recovery_pending(self) -> bool:
        """Clear cleanup state only after real runtime ownership disappeared."""
        with self._lock:
            if self._active_operation is not None or self._movement_lock.locked():
                return False
            self._motion_recovery_pending = False
            self._motion_recovery_reason = None
            if self._state is MachineRuntimeState.MESH_PROBING:
                self._state = MachineRuntimeState.MESH_PAUSED
            return True

    def cancel_operation(
        self,
        *,
        reason: str | None = None,
        preserve_mesh_pause: bool = False,
    ) -> dict[str, Any]:
        """Request cancellation without falsely declaring a timed-out mesh idle.

        Operator cancellation keeps the original MachineRuntime behaviour.
        The watchdog path uses ``preserve_mesh_pause``: it sets the operation's
        cancel event but leaves MESH_PROBING in place until the probe thread
        actually exits and releases the movement lock.
        """
        if not preserve_mesh_pause:
            return super().cancel_operation()

        with self._lock:
            context = self._active_operation
            if context is not None:
                context.cancel_event.set()
            self._probe_requested = False
            self._manual_enabled = False
            self._motion_recovery_pending = True
            self._motion_recovery_reason = reason or RECOVERY_PENDING_MESSAGE
            self._event("warning", self._motion_recovery_reason)
        return self.snapshot()

    def probe_mesh_point(
        self,
        point: dict[str, Any],
        probe_config: dict[str, Any] | None = None,
        progress_callback=None,
    ) -> dict[str, Any]:
        """Probe one mesh point and leave recoverable failures in MESH_PAUSED."""
        self._require_physical_ready()
        if not self._movement_lock.acquire(blocking=False):
            raise MachineRuntimeError("Ya hay un movimiento u operación física activa.")

        context = self._begin_operation_context("mesh")
        started = time.monotonic()
        try:
            with self._lock:
                self._state = MachineRuntimeState.MESH_PROBING
                self._manual_enabled = False
                self._diagnostic_input_only = True

            self._assert_safety_for_motion()
            self._refresh_machine()
            machine = self._machine
            if machine is None:
                raise MachineRuntimeError("No hay estado de máquina descubierto.")

            start_snapshot = machine.get_motion_snapshot()
            safe_z = self._mesh_safe_z(machine, probe_config=probe_config)
            self._notify_probe_progress(
                progress_callback,
                "POINT_MOVE_SAFE_Z",
                safe_z_mm=safe_z,
                initial_z_mm=float(start_snapshot["z"]),
            )
            self._move_absolute(z=safe_z, label="mesh_z_segura")
            safe_observed = machine.get_motion_snapshot()
            self._notify_probe_progress(
                progress_callback,
                "POINT_CONFIRM_SAFE_Z",
                safe_z_mm=safe_z,
                observed_z_mm=float(safe_observed["z"]),
            )

            with self._lock:
                xy_sequence = self._packet_sequence

            self._notify_probe_progress(
                progress_callback,
                "POINT_MOVE_XY",
                x_mm=float(point["x_machine"]),
                y_mm=float(point["y_machine"]),
                safe_z_mm=safe_z,
                observed_z_mm=float(safe_observed["z"]),
            )
            self._move_absolute(
                x=float(point["x_machine"]),
                y=float(point["y_machine"]),
                label=f"mesh_xy_{point['index']}",
            )
            xy_observed = machine.get_motion_snapshot()
            self._notify_probe_progress(
                progress_callback,
                "POINT_CONFIRM_XY",
                x_mm=float(xy_observed["x"]),
                y_mm=float(xy_observed["y"]),
                observed_z_mm=float(xy_observed["z"]),
            )

            probe = self._perform_probe_descent(
                label=f"mesh_probe_{point['index']}",
                profile=self._resolve_probe_profile(probe_config),
                open_after_sequence=xy_sequence,
                progress_callback=progress_callback,
            )

            with self._lock:
                self._state = MachineRuntimeState.MESH_READY
                self._motion_recovery_pending = False
                self._motion_recovery_reason = None

            return {
                "index": point["index"],
                "z_measured": probe.z_mm,
                "duration_s": time.monotonic() - started,
                "probe": probe.__dict__,
            }
        except Exception as error:
            with self._lock:
                self._last_error = str(error)
                if self._state not in _SEVERE_STATES:
                    self._state = MachineRuntimeState.MESH_PAUSED
                level = "error" if self._state in _SEVERE_STATES else "warning"
                self._event(level, f"Punto de malla fallido: {error}")
            raise
        finally:
            self._finish_operation_context(context)
            self._movement_lock.release()
