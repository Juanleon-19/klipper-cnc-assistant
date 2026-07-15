from __future__ import annotations

import threading
import time
from typing import Any

from klipper_cnc_assistant.application.errors import ApplicationError
from klipper_cnc_assistant.application.physical_map_service import PhysicalMapService


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
)


class MeshExecutionService:
    """Runs physical mesh probing outside the HTTP request lifecycle."""

    def __init__(self, physical_map_service: PhysicalMapService, *, max_point_retries: int = 2) -> None:
        self.physical_map_service = physical_map_service
        self.max_point_retries = max_point_retries
        self._lock = threading.Lock()
        self._threads: dict[tuple[str, str], threading.Thread] = {}

    def start_all(self, *, project_id: str, map_id: str, runtime: Any) -> dict[str, Any]:
        payload = self.physical_map_service.get_by_id(project_id, map_id)
        if payload.get("status") in {"CANCELLED", "MESH_COMPLETE"}:
            raise ApplicationError("La malla no está en un estado ejecutable.")
        key = (project_id, map_id)
        with self._lock:
            for other_key, thread in list(self._threads.items()):
                if not thread.is_alive():
                    self._threads.pop(other_key, None)
            if self._threads:
                raise ApplicationError("Ya hay una operación física de malla en curso.")
            self.physical_map_service.mark_status(project_id=project_id, map_id=map_id, status="MESH_PROBING")
            self.physical_map_service.update_execution_state(
                project_id=project_id,
                map_id=map_id,
                worker_active=True,
                point_state="POINT_PRECHECK",
                last_event="Sondeo automático iniciado; el backend continuará aunque se cierre el navegador.",
            )
            thread = threading.Thread(target=self._run, args=(project_id, map_id, runtime), name=f"mesh-{map_id}", daemon=True)
            self._threads[key] = thread
            thread.start()
        return self.physical_map_service.get_by_id(project_id, map_id)

    def resume(self, *, project_id: str, map_id: str, runtime: Any) -> dict[str, Any]:
        self.physical_map_service.mark_status(project_id=project_id, map_id=map_id, status="MESH_READY")
        return self.start_all(project_id=project_id, map_id=map_id, runtime=runtime)

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
        try:
            while True:
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
                    )
                    return
                if status == "MESH_PAUSED" or execution.get("pause_requested"):
                    self.physical_map_service.mark_status(project_id=project_id, map_id=map_id, status="MESH_PAUSED")
                    self.physical_map_service.update_execution_state(
                        project_id=project_id,
                        map_id=map_id,
                        worker_active=False,
                        point_state="MESH_PAUSED",
                        last_event="Pausa solicitada; no se iniciará otro punto.",
                    )
                    return
                try:
                    point = self.physical_map_service.next_pending_point(project_id, map_id)
                except ApplicationError:
                    self.physical_map_service.update_execution_state(
                        project_id=project_id,
                        map_id=map_id,
                        worker_active=False,
                        point_state="MESH_COMPLETE" if payload.get("status") == "MESH_COMPLETE" else "MESH_READY",
                        last_event="No quedan puntos pendientes ejecutables.",
                    )
                    return
                self._probe_one_point(project_id, map_id, runtime, point)
        finally:
            with self._lock:
                thread = self._threads.get(key)
                if thread is threading.current_thread():
                    self._threads.pop(key, None)

    def _probe_one_point(self, project_id: str, map_id: str, runtime: Any, point: dict[str, Any]) -> None:
        point_index = int(point["index"])
        attempts = int(point.get("attempts", 0))
        target = {"x_mm": point.get("x_machine"), "y_mm": point.get("y_machine"), "point_index": point_index}
        while attempts <= self.max_point_retries:
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
            )
            started = time.monotonic()
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
                )
                result = runtime.probe_mesh_point(point)
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
                self.physical_map_service.update_execution_state(
                    project_id=project_id,
                    map_id=map_id,
                    worker_active=updated.get("status") != "MESH_COMPLETE",
                    point_state="POINT_COMPLETE",
                    point_index=point_index,
                    retry_count=attempts - 1,
                    target=target,
                    observed=observed,
                    last_event=f"Punto {point_index + 1}: completado; avanzando automáticamente.",
                )
                return
            except Exception as error:
                observed = self._observed_from_runtime(runtime)
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
                )
                return

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
            "telemetry_age_s": snapshot.get("telemetry_age_s"),
            "serial_age_s": snapshot.get("serial_age_s"),
        }
