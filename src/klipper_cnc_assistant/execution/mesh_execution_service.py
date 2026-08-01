from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any

from klipper_cnc_assistant.application.errors import ApplicationError
from klipper_cnc_assistant.application.physical_map_service import PhysicalMapService


logger = logging.getLogger(__name__)


POINT_STATES = (
    "POINT_PRECHECK",
    "POINT_MOVE_SAFE_Z",
    "POINT_CONFIRM_SAFE_Z",
    "POINT_MOVE_XY",
    "POINT_CONFIRM_XY",
    "POINT_SETTLE",
    "POINT_VERIFY_PROBE_OPEN",
    "POINT_LOWER_STEP",
    "POINT_CONFIRM_STEP",
    "POINT_CONTACT_DETECTED",
    "POINT_CAPTURE_Z",
    "POINT_RETRACT",
    "POINT_CONFIRM_RETRACT",
    "POINT_PERSIST",
    "POINT_COMPLETE",
    "POINT_RETRY",
    "POINT_FAILED",
    "MESH_PAUSING",
    "MESH_CANCELING",
)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MeshExecutionService:
    """Runs physical mesh probing outside the HTTP request lifecycle."""

    def __init__(
        self,
        physical_map_service: PhysicalMapService,
        *,
        max_point_retries: int = 2,
        point_watchdog_timeout_s: float | None = None,
        point_watchdog_poll_s: float = 0.05,
        point_watchdog_grace_s: float = 0.2,
    ) -> None:
        self.physical_map_service = physical_map_service
        self.max_point_retries = 0
        self.point_watchdog_timeout_s = point_watchdog_timeout_s
        self.point_watchdog_poll_s = point_watchdog_poll_s
        self.point_watchdog_grace_s = point_watchdog_grace_s
        self._lock = threading.Lock()
        self._threads: dict[tuple[str, str], threading.Thread] = {}
        self._cancel_requests: dict[tuple[str, str], threading.Event] = {}

    def start_all(self, *, project_id: str, map_id: str, runtime: Any) -> dict[str, Any]:
        payload = self.physical_map_service.validate_resume_context(project_id=project_id, map_id=map_id)
        if payload.get("status") in {"CANCELLED", "MESH_COMPLETE"}:
            raise ApplicationError("La malla no está en un estado ejecutable.")
        key = (project_id, map_id)
        with self._lock:
            self._prune_dead_threads_locked()
            live_thread = self._threads.get(key)
            if live_thread is not None and live_thread.is_alive():
                raise ApplicationError("La malla ya tiene un worker activo y no puede iniciarse dos veces.")
            if any(thread.is_alive() for other_key, thread in self._threads.items() if other_key != key):
                raise ApplicationError("Ya hay una operación física de malla en curso.")
            cancel_request = self._cancel_requests.get(key)
            if cancel_request is None:
                cancel_request = threading.Event()
                self._cancel_requests[key] = cancel_request
            cancel_request.clear()
            generation = int((payload.get("execution") or {}).get("worker_generation", 0)) + 1
            resumed = payload.get("status") == "MESH_PAUSED"
            updated = self.physical_map_service.mark_status(
                project_id=project_id,
                map_id=map_id,
                status="MESH_PROBING",
                worker_active=True,
                point_state="POINT_PRECHECK",
                last_event="Reanudando sondeo automático." if resumed else "Sondeo automático iniciado; el backend continuará aunque se cierre el navegador.",
                metadata={
                    "worker_generation": generation,
                    "pause_requested": False,
                    "pause_reason": None,
                    "cancel_requested": False,
                    "cancel_reason": None,
                    "phase": "precheck",
                    "last_progress_at": _iso_now(),
                },
            )
            thread = threading.Thread(target=self._run, args=(project_id, map_id, runtime), name=f"mesh-{map_id}", daemon=True)
            self._threads[key] = thread
            thread.start()
        self._log_transition("MESH_WORKER_START", project_id, map_id, execution=updated.get("execution") or {})
        return updated

    def pause(self, *, project_id: str, map_id: str) -> dict[str, Any]:
        key = (project_id, map_id)
        worker_alive = False
        with self._lock:
            self._prune_dead_threads_locked()
            thread = self._threads.get(key)
            worker_alive = bool(thread and thread.is_alive())
        payload = self.physical_map_service.get_by_id(project_id, map_id)
        if payload.get("status") in {"CANCELLED", "MESH_COMPLETE"}:
            return payload
        return self.physical_map_service.mark_status(
            project_id=project_id,
            map_id=map_id,
            status="MESH_PAUSED",
            worker_active=worker_alive,
            point_state="MESH_PAUSING" if worker_alive else "MESH_PAUSED",
            last_event="Pausa solicitada; el punto actual terminará antes de detenerse." if worker_alive else "Malla pausada; puede reanudarse desde el siguiente punto pendiente.",
            metadata={
                "pause_requested": True,
                "pause_reason": "Solicitada por el operador.",
                "phase": "pausing" if worker_alive else "paused",
                "last_progress_at": (payload.get("execution") or {}).get("last_progress_at") or _iso_now(),
            },
        )

    def resume(self, *, project_id: str, map_id: str, runtime: Any) -> dict[str, Any]:
        self.physical_map_service.validate_resume_context(project_id=project_id, map_id=map_id)
        key = (project_id, map_id)
        with self._lock:
            self._prune_dead_threads_locked()
            thread = self._threads.get(key)
            if thread is not None and thread.is_alive():
                raise ApplicationError("La malla ya tiene un worker activo y no puede reanudarse dos veces.")
        return self.start_all(project_id=project_id, map_id=map_id, runtime=runtime)

    def cancel(self, *, project_id: str, map_id: str, runtime: Any) -> dict[str, Any]:
        key = (project_id, map_id)
        with self._lock:
            self._prune_dead_threads_locked()
            cancel_request = self._cancel_requests.get(key)
            if cancel_request is None:
                cancel_request = threading.Event()
                self._cancel_requests[key] = cancel_request
            cancel_request.set()
            thread = self._threads.get(key)
            worker_alive = bool(thread and thread.is_alive())
        payload = self.physical_map_service.get_by_id(project_id, map_id)
        if payload.get("status") == "CANCELLED":
            return payload
        if not worker_alive:
            return self.physical_map_service.mark_status(
                project_id=project_id,
                map_id=map_id,
                status="CANCELLED",
                worker_active=False,
                point_state="CANCELLED",
                last_event="Malla cancelada por el operador; no se iniciará ningún punto nuevo.",
                metadata={
                    "cancel_requested": False,
                    "cancel_reason": "Solicitada por el operador.",
                    "pause_requested": False,
                    "phase": "cancelled",
                    "last_progress_at": (payload.get("execution") or {}).get("last_progress_at") or _iso_now(),
                },
            )
        updated = self.physical_map_service.update_execution_state(
            project_id=project_id,
            map_id=map_id,
            worker_active=True,
            point_state="MESH_CANCELING",
            last_event="Cancelación solicitada; el worker cerrará la malla antes de iniciar otro punto.",
            metadata={
                "cancel_requested": True,
                "cancel_reason": "Solicitada por el operador.",
                "pause_requested": False,
                "phase": "canceling",
                "last_progress_at": (payload.get("execution") or {}).get("last_progress_at") or _iso_now(),
            },
        )
        try:
            runtime.cancel_operation()
        except Exception:
            pass
        return updated

    def reconcile_map_state(self, *, project_id: str, map_id: str) -> dict[str, Any]:
        key = (project_id, map_id)
        with self._lock:
            self._prune_dead_threads_locked()
            thread = self._threads.get(key)
            worker_alive = bool(thread and thread.is_alive())
        payload = self.physical_map_service.get_by_id(project_id, map_id)
        execution = payload.get("execution") or {}
        if worker_alive:
            if not execution.get("worker_active"):
                return self.physical_map_service.update_execution_state(
                    project_id=project_id,
                    map_id=map_id,
                    worker_active=True,
                    point_state=str(execution.get("point_state") or payload.get("status") or "POINT_PRECHECK"),
                    metadata={
                        "phase": execution.get("phase") or "running",
                        "last_progress_at": execution.get("last_progress_at") or _iso_now(),
                    },
                )
            return payload
        if payload.get("status") == "MESH_PROBING" or execution.get("worker_active"):
            return self.physical_map_service.mark_status(
                project_id=project_id,
                map_id=map_id,
                status="MESH_PAUSED",
                worker_active=False,
                point_state="MESH_PAUSED",
                last_event="El worker dejó de existir sin cerrar la malla; el estado quedó pausado y es recuperable.",
                metadata={
                    "pause_requested": True,
                    "pause_reason": "Worker desaparecido sin estado terminal.",
                    "phase": "paused",
                    "cancel_requested": False,
                    "last_progress_at": execution.get("last_progress_at") or _iso_now(),
                    "last_error": execution.get("last_error") or "El worker terminó sin un estado terminal persistido.",
                },
            )
        return payload

    def wait_until_idle(self, *, timeout_s: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout_s
        while True:
            with self._lock:
                threads = list(self._threads.values())
            live = [thread for thread in threads if thread.is_alive()]
            if not live:
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            for thread in live:
                thread.join(min(0.05, remaining))

    def _run(self, project_id: str, map_id: str, runtime: Any) -> None:
        key = (project_id, map_id)
        with self._lock:
            self._cancel_requests.setdefault(key, threading.Event())
        try:
            while True:
                with self._lock:
                    cancel_requested = self._cancel_requests.get(key)
                if cancel_requested is not None and cancel_requested.is_set():
                    self.physical_map_service.mark_status(
                        project_id=project_id,
                        map_id=map_id,
                        status="CANCELLED",
                        worker_active=False,
                        point_state="CANCELLED",
                        last_event="Malla cancelada por el operador; no se iniciará otro paso.",
                        metadata={
                            "cancel_requested": False,
                            "cancel_reason": "Solicitada por el operador.",
                            "pause_requested": False,
                            "phase": "cancelled",
                            "last_progress_at": _iso_now(),
                        },
                    )
                    return
                payload = self.physical_map_service.get_by_id(project_id, map_id)
                status = payload.get("status")
                execution = payload.get("execution") or {}
                if status in {"CANCELLED", "MESH_COMPLETE"}:
                    self.physical_map_service.update_execution_state(
                        project_id=project_id,
                        map_id=map_id,
                        worker_active=False,
                        point_state=str(status),
                        last_event=f"Ejecución de malla terminada en estado {status}.",
                        metadata={
                            "phase": "complete" if status == "MESH_COMPLETE" else "cancelled",
                            "last_progress_at": execution.get("last_progress_at") or _iso_now(),
                        },
                    )
                    return
                if status == "MESH_PAUSED" or execution.get("pause_requested"):
                    self.physical_map_service.mark_status(
                        project_id=project_id,
                        map_id=map_id,
                        status="MESH_PAUSED",
                        worker_active=False,
                        point_state="MESH_PAUSED",
                        last_event="Pausa solicitada; no se iniciará otro punto.",
                        metadata={
                            "pause_requested": True,
                            "pause_reason": execution.get("pause_reason") or "Solicitada por el operador.",
                            "cancel_requested": False,
                            "phase": "paused",
                            "last_progress_at": execution.get("last_progress_at") or _iso_now(),
                        },
                    )
                    return
                try:
                    snapshot = self._require_current_machine_state(runtime)
                except Exception as error:
                    self.physical_map_service.mark_status(
                        project_id=project_id,
                        map_id=map_id,
                        status="MESH_PAUSED",
                        worker_active=False,
                        point_state="MESH_PAUSED",
                        last_event="Malla pausada: Moonraker no respondió con estado reciente; reconecte y reanude explícitamente.",
                        metadata={
                            "pause_requested": True,
                            "pause_reason": "Estado de máquina no verificable.",
                            "phase": "paused",
                            "last_error": f"No se pudo confirmar estado reciente de Moonraker: {error}",
                            "last_progress_at": execution.get("last_progress_at") or _iso_now(),
                        },
                    )
                    self._log_transition("POINT_FAILED", project_id, map_id, execution=execution, error=str(error))
                    return
                try:
                    point = self.physical_map_service.next_pending_point(project_id, map_id)
                except ApplicationError:
                    final_payload = self.physical_map_service.get_by_id(project_id, map_id)
                    final_status = str(final_payload.get("status") or "MESH_READY")
                    self.physical_map_service.update_execution_state(
                        project_id=project_id,
                        map_id=map_id,
                        worker_active=False,
                        point_state="MESH_COMPLETE" if final_status == "MESH_COMPLETE" else "MESH_READY",
                        last_event="No quedan puntos pendientes ejecutables.",
                        metadata={
                            "phase": "complete" if final_status == "MESH_COMPLETE" else "ready",
                            "last_progress_at": _iso_now(),
                        },
                    )
                    self._log_transition("MESH_COMPLETE", project_id, map_id, execution=final_payload.get("execution") or {})
                    return
                self._log_transition("POINT_START", project_id, map_id, point_index=int(point["index"]), target=point, execution=execution)
                self._probe_one_point(project_id, map_id, runtime, point, probe_config=payload.get("probe_config"))
        finally:
            with self._lock:
                thread = self._threads.get(key)
                if thread is threading.current_thread():
                    self._threads.pop(key, None)
                    self._cancel_requests.pop(key, None)
            try:
                payload = self.physical_map_service.get_by_id(project_id, map_id)
            except Exception:
                payload = None
            execution = (payload or {}).get("execution") or {}
            if payload and (payload.get("status") == "MESH_PROBING" or execution.get("worker_active")):
                self.physical_map_service.mark_status(
                    project_id=project_id,
                    map_id=map_id,
                    status="MESH_PAUSED",
                    worker_active=False,
                    point_state="MESH_PAUSED",
                    last_event="El worker terminó sin completar ni cancelar la malla; quedó pausada y es recuperable.",
                    metadata={
                        "pause_requested": True,
                        "pause_reason": "Worker terminado sin estado final persistido.",
                        "cancel_requested": False,
                        "phase": "paused",
                        "last_error": execution.get("last_error") or "El worker dejó de existir sin publicar un estado terminal.",
                        "last_progress_at": execution.get("last_progress_at") or _iso_now(),
                    },
                )
            self._log_transition("MESH_WORKER_END", project_id, map_id, execution=execution)

    def _probe_one_point(self, project_id: str, map_id: str, runtime: Any, point: dict[str, Any], *, probe_config: dict[str, Any] | None = None) -> None:
        point_index = int(point["index"])
        attempts = int(point.get("attempts", 0))
        target = {"x_mm": point.get("x_machine"), "y_mm": point.get("y_machine"), "point_index": point_index}
        payload = self.physical_map_service.get_by_id(project_id, map_id)
        total_points = int((payload.get("execution") or {}).get("total_count") or payload.get("total_count") or 0)
        worker_generation = int((payload.get("execution") or {}).get("worker_generation", 0))
        while attempts < int(point.get("attempts", 0)) + 1:
            attempts += 1
            self.physical_map_service.update_execution_state(
                project_id=project_id,
                map_id=map_id,
                worker_active=True,
                point_state="POINT_PRECHECK",
                point_index=point_index,
                retry_count=attempts - 1,
                target=target,
                last_event=f"Punto {point_index + 1}: verificando condiciones antes de mover.",
                metadata={
                    "phase": "precheck",
                    "worker_generation": worker_generation,
                    "last_progress_at": _iso_now(),
                },
            )
            started = time.monotonic()
            progress_ticks = {"monotonic": time.monotonic(), "iso": _iso_now()}
            try:
                self.physical_map_service.update_execution_state(
                    project_id=project_id,
                    map_id=map_id,
                    worker_active=True,
                    point_state="POINT_MOVE_SAFE_Z",
                    point_index=point_index,
                    retry_count=attempts - 1,
                    command="probe_mesh_point",
                    target=target,
                    last_event=f"Punto {point_index + 1}: operación física exclusiva iniciada.",
                    metadata={
                        "phase": "move_safe_z",
                        "worker_generation": worker_generation,
                        "last_progress_at": progress_ticks["iso"],
                    },
                )

                def progress(state: str, detail: dict[str, Any]) -> None:
                    progress_ticks["monotonic"] = time.monotonic()
                    progress_ticks["iso"] = _iso_now()
                    observed_now = self._observed_from_runtime(runtime)
                    phase = self._phase_for_point_state(state)
                    self.physical_map_service.update_execution_state(
                        project_id=project_id,
                        map_id=map_id,
                        worker_active=True,
                        point_state=state,
                        point_index=point_index,
                        retry_count=attempts - 1,
                        command=str(detail.get("command") or state),
                        target={**target, **detail},
                        observed=observed_now,
                        last_event=f"Punto {point_index + 1}: {state}.",
                        metadata={
                            "phase": phase,
                            "worker_generation": worker_generation,
                            "last_progress_at": progress_ticks["iso"],
                        },
                    )
                    transition = {
                        "POINT_MOVE_SAFE_Z": "MOVE_SAFE_Z",
                        "POINT_CONFIRM_SAFE_Z": "MOVE_SAFE_Z_DONE",
                        "POINT_VERIFY_PROBE_OPEN": "VERIFY_PROBE_OPEN",
                        "POINT_MOVE_XY": "MOVE_XY",
                        "POINT_CONFIRM_XY": "MOVE_XY_DONE",
                        "POINT_LOWER_STEP": "LOWER_STEP",
                        "POINT_CONTACT_DETECTED": "CONTACT",
                        "POINT_RETRACT": "RETRACT",
                    }.get(state)
                    if transition:
                        self._log_transition(
                            transition,
                            project_id,
                            map_id,
                            point_index=point_index,
                            target={**target, **detail},
                            observed=observed_now,
                            started=started,
                            execution={
                                "phase": phase,
                                "worker_generation": worker_generation,
                                "last_progress_at": progress_ticks["iso"],
                                "point_state": state,
                                "total_count": total_points,
                            },
                        )

                result = self._probe_with_watchdog(
                    runtime,
                    point,
                    probe_config=probe_config,
                    progress_callback=progress,
                    project_id=project_id,
                    map_id=map_id,
                    point_index=point_index,
                    total_points=total_points,
                    worker_generation=worker_generation,
                    progress_ticks=progress_ticks,
                )
                observed = self._observed_from_runtime(runtime)
                self.physical_map_service.update_execution_state(
                    project_id=project_id,
                    map_id=map_id,
                    worker_active=True,
                    point_state="POINT_CAPTURE_Z",
                    point_index=point_index,
                    retry_count=attempts - 1,
                    target=target,
                    observed=observed,
                    last_event=f"Punto {point_index + 1}: contacto capturado; persistiendo Z.",
                    metadata={
                        "phase": "capture_z",
                        "worker_generation": worker_generation,
                        "last_progress_at": _iso_now(),
                    },
                )
                updated = self.physical_map_service.record_point(
                    project_id=project_id,
                    map_id=map_id,
                    point_index=point_index,
                    z_measured=float(result["z_measured"]),
                    status="MEASURED",
                    attempts=attempts,
                    duration_s=float(result.get("duration_s", time.monotonic() - started)),
                    error=None,
                )
                next_phase = "complete" if updated.get("status") == "MESH_COMPLETE" else "paused" if updated.get("status") == "MESH_PAUSED" else "cancelled" if updated.get("status") == "CANCELLED" else "persist"
                next_state = "POINT_COMPLETE" if updated.get("status") == "MESH_PROBING" else str(updated.get("status"))
                last_event = (
                    f"Punto {point_index + 1}: completado; avanzando automáticamente."
                    if updated.get("status") == "MESH_PROBING"
                    else f"Punto {point_index + 1}: completado con estado {updated.get('status')}."
                )
                self.physical_map_service.update_execution_state(
                    project_id=project_id,
                    map_id=map_id,
                    worker_active=updated.get("status") == "MESH_PROBING",
                    point_state=next_state,
                    point_index=point_index,
                    retry_count=attempts - 1,
                    target=target,
                    observed=observed,
                    last_event=last_event,
                    metadata={
                        "phase": next_phase,
                        "worker_generation": worker_generation,
                        "last_progress_at": updated.get("updated_at") or _iso_now(),
                    },
                )
                self._log_transition(
                    "POINT_COMPLETE",
                    project_id,
                    map_id,
                    point_index=point_index,
                    target=target,
                    observed=observed,
                    started=started,
                    execution={
                        "phase": next_phase,
                        "worker_generation": worker_generation,
                        "last_progress_at": updated.get("updated_at") or _iso_now(),
                        "point_state": next_state,
                        "total_count": total_points,
                    },
                )
                return
            except Exception as error:
                observed = self._observed_from_runtime(runtime)
                with self._lock:
                    cancelled = bool(self._cancel_requests.get((project_id, map_id)) and self._cancel_requests[(project_id, map_id)].is_set())
                persisted_point = None
                try:
                    persisted_payload = self.physical_map_service.get_by_id(project_id, map_id)
                    persisted_point = dict((persisted_payload.get("points") or [])[point_index])
                except Exception:
                    persisted_payload = None
                if persisted_point and persisted_point.get("status") == "MEASURED":
                    self._log_transition(
                        "POINT_POST_PERSIST_ERROR",
                        project_id,
                        map_id,
                        point_index=point_index,
                        target=target,
                        observed=observed,
                        started=started,
                        error=str(error),
                        execution={
                            "phase": "persist",
                            "worker_generation": worker_generation,
                            "last_progress_at": (persisted_payload or {}).get("updated_at") or _iso_now(),
                            "point_state": "POINT_COMPLETE",
                            "total_count": total_points,
                        },
                    )
                    return
                if cancelled:
                    self.physical_map_service.mark_status(
                        project_id=project_id,
                        map_id=map_id,
                        status="CANCELLED",
                        worker_active=False,
                        point_state="CANCELLED",
                        last_event="Malla cancelada por el operador; no se iniciará otro paso.",
                        metadata={
                            "cancel_requested": False,
                            "cancel_reason": "Solicitada por el operador.",
                            "pause_requested": False,
                            "phase": "cancelled",
                            "last_error": str(error),
                            "last_progress_at": progress_ticks["iso"],
                        },
                    )
                    return
                if attempts <= self.max_point_retries:
                    self.physical_map_service.update_execution_state(
                        project_id=project_id,
                        map_id=map_id,
                        worker_active=True,
                        point_state="POINT_RETRY",
                        point_index=point_index,
                        retry_count=attempts,
                        error=str(error),
                        target=target,
                        observed=observed,
                        last_event=f"Punto {point_index + 1}: error recuperable; reintento {attempts}/{self.max_point_retries} tras reconciliar estado.",
                        metadata={
                            "phase": "retry",
                            "worker_generation": worker_generation,
                            "last_progress_at": progress_ticks["iso"],
                        },
                    )
                    continue
                self.physical_map_service.mark_point_failed(project_id=project_id, map_id=map_id, point_index=point_index, error=str(error))
                self.physical_map_service.update_execution_state(
                    project_id=project_id,
                    map_id=map_id,
                    worker_active=False,
                    point_state="POINT_FAILED",
                    point_index=point_index,
                    retry_count=attempts,
                    error=str(error),
                    target=target,
                    observed=observed,
                    last_event=f"Punto {point_index + 1}: falló después de {attempts} intentos; la malla queda pausada.",
                    metadata={
                        "phase": "failed",
                        "worker_generation": worker_generation,
                        "pause_requested": True,
                        "pause_reason": "Punto fallido; requiere decisión explícita del operador.",
                        "last_progress_at": progress_ticks["iso"],
                    },
                )
                self._log_transition(
                    "POINT_FAILED",
                    project_id,
                    map_id,
                    point_index=point_index,
                    target=target,
                    observed=observed,
                    error=str(error),
                    started=started,
                    execution={
                        "phase": "failed",
                        "worker_generation": worker_generation,
                        "last_progress_at": progress_ticks["iso"],
                        "point_state": "POINT_FAILED",
                        "total_count": total_points,
                    },
                )
                return

    def _probe_with_watchdog(
        self,
        runtime: Any,
        point: dict[str, Any],
        *,
        probe_config: dict[str, Any] | None,
        progress_callback,
        project_id: str,
        map_id: str,
        point_index: int,
        total_points: int,
        worker_generation: int,
        progress_ticks: dict[str, Any],
    ) -> dict[str, Any]:
        result_holder: dict[str, Any] = {}
        error_holder: dict[str, BaseException] = {}
        finished = threading.Event()

        def run_probe() -> None:
            try:
                try:
                    result_holder["result"] = runtime.probe_mesh_point(point, probe_config=probe_config, progress_callback=progress_callback)
                except TypeError as error:
                    if "progress_callback" not in str(error):
                        raise
                    result_holder["result"] = runtime.probe_mesh_point(point, probe_config=probe_config)
            except BaseException as error:  # pragma: no cover - exercised in tests through holders
                error_holder["error"] = error
            finally:
                finished.set()

        thread = threading.Thread(target=run_probe, name=f"mesh-point-{map_id}-{point_index}", daemon=True)
        thread.start()
        timeout_s = self.point_watchdog_timeout_s
        if timeout_s is None:
            timeout_s = float(getattr(getattr(runtime, "config", None), "no_progress_timeout_s", 60.0) or 60.0)
        while not finished.wait(self.point_watchdog_poll_s):
            with self._lock:
                cancel_requested = self._cancel_requests.get((project_id, map_id))
                cancelled = bool(cancel_requested and cancel_requested.is_set())
            if cancelled:
                try:
                    runtime.cancel_operation()
                except Exception:
                    pass
            elapsed_without_progress = time.monotonic() - float(progress_ticks["monotonic"])
            if elapsed_without_progress <= timeout_s:
                continue
            try:
                runtime.cancel_operation()
            except Exception:
                pass
            self.physical_map_service.update_execution_state(
                project_id=project_id,
                map_id=map_id,
                worker_active=True,
                point_state="POINT_FAILED",
                point_index=point_index,
                error=f"Timeout sin progreso durante {elapsed_without_progress:.3f} s en el punto {point_index + 1}.",
                last_event="Watchdog: el punto excedió el timeout lógico sin progreso visible.",
                metadata={
                    "phase": "watchdog_timeout",
                    "worker_generation": worker_generation,
                    "last_progress_at": progress_ticks["iso"],
                },
            )
            finished.wait(self.point_watchdog_grace_s)
            raise TimeoutError(f"Timeout sin progreso durante {elapsed_without_progress:.3f} s en el punto {point_index + 1}/{total_points}.")
        if "error" in error_holder:
            raise error_holder["error"]
        return dict(result_holder["result"])

    def _require_current_machine_state(self, runtime: Any) -> dict[str, Any]:
        """Require a fresh HTTP observation before a physical point.

        A quiet WebSocket alone is not proof that Moonraker is unavailable: the
        discovery response contains the toolhead state used by movement guards.
        A failed HTTP refresh remains a hard stop.
        """
        refresh_state = getattr(runtime, "refresh_observed_state", None)
        if refresh_state is not None:
            snapshot = refresh_state()
        else:
            snapshot_fn = getattr(runtime, "snapshot", None)
            snapshot = snapshot_fn() if snapshot_fn is not None else {}
        safety = snapshot.get("safety") if isinstance(snapshot, dict) else {}
        if isinstance(safety, dict) and safety.get("serial_recent") is False:
            raise ApplicationError("Arduino obsoleto; no se inicia el sondeo.")
        return snapshot if isinstance(snapshot, dict) else {}

    def _prune_dead_threads_locked(self) -> None:
        for other_key, thread in list(self._threads.items()):
            if not thread.is_alive():
                self._threads.pop(other_key, None)

    def _log_transition(
        self,
        event: str,
        project_id: str,
        map_id: str,
        *,
        point_index: int | None = None,
        target: dict[str, Any] | None = None,
        observed: dict[str, Any] | None = None,
        error: str | None = None,
        started: float | None = None,
        execution: dict[str, Any] | None = None,
    ) -> None:
        observed = observed or {}
        execution = execution or {}
        logger.info(
            "%s project_id=%s map_id=%s point_index=%s total_points=%s worker_generation=%s operation_state=%s phase=%s last_progress_at=%s elapsed_s=%.3f error=%s target=%s observed=%s probe=%s telemetry_age_s=%s",
            event,
            project_id,
            map_id,
            point_index,
            execution.get("total_count"),
            execution.get("worker_generation"),
            execution.get("point_state") or execution.get("operation_state"),
            execution.get("phase"),
            execution.get("last_progress_at"),
            0.0 if started is None else time.monotonic() - started,
            error or execution.get("last_error"),
            target or {},
            observed.get("position"),
            observed.get("probe") or observed.get("probe_filtered"),
            observed.get("telemetry_age_s"),
        )

    @staticmethod
    def _phase_for_point_state(state: str) -> str:
        return {
            "POINT_PRECHECK": "precheck",
            "POINT_MOVE_SAFE_Z": "move_safe_z",
            "POINT_CONFIRM_SAFE_Z": "confirm_safe_z",
            "POINT_MOVE_XY": "move_xy",
            "POINT_CONFIRM_XY": "confirm_xy",
            "POINT_SETTLE": "settle",
            "POINT_VERIFY_PROBE_OPEN": "waiting_probe",
            "POINT_LOWER_STEP": "waiting_probe",
            "POINT_CONFIRM_STEP": "waiting_probe",
            "POINT_CONTACT_DETECTED": "contact",
            "POINT_CAPTURE_Z": "capture_z",
            "POINT_RETRACT": "retract",
            "POINT_CONFIRM_RETRACT": "retract",
            "POINT_PERSIST": "persist",
            "POINT_COMPLETE": "persist",
            "POINT_RETRY": "retry",
            "POINT_FAILED": "failed",
            "MESH_PAUSING": "pausing",
            "MESH_CANCELING": "canceling",
        }.get(state, state.lower())

    @staticmethod
    def _observed_from_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
        return {
            "state": snapshot.get("state"),
            "position": snapshot.get("position") or snapshot.get("machine_position"),
            "telemetry_age_s": snapshot.get("telemetry_age_s"),
            "serial_age_s": snapshot.get("serial_age_s"),
        }

    def _observed_from_runtime(self, runtime: Any) -> dict[str, Any] | None:
        snapshot_fn = getattr(runtime, "snapshot", None)
        if snapshot_fn is None:
            return None
        try:
            snapshot = snapshot_fn()
        except Exception:
            return None
        return {
            "state": snapshot.get("state"),
            "position": snapshot.get("position") or snapshot.get("machine_position"),
            "homed_axes": snapshot.get("homed_axes"),
            "last_command": snapshot.get("last_command") or snapshot.get("last_command_text"),
            "probe": snapshot.get("probe") or snapshot.get("last_packet"),
            "telemetry_age_s": snapshot.get("telemetry_age_s"),
            "serial_age_s": snapshot.get("serial_age_s"),
        }
