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
        # Los reintentos requieren decisión explícita del operador; nunca son implícitos.
        self.max_point_retries = 0
        self._lock = threading.Lock()
        self._threads: dict[tuple[str, str], threading.Thread] = {}
        self._cancel_requests: dict[tuple[str, str], threading.Event] = {}

    def start_all(self, *, project_id: str, map_id: str, runtime: Any) -> dict[str, Any]:
        payload = self.physical_map_service.get_by_id(project_id, map_id)
        if payload.get("status") in {"CANCELLED", "MESH_COMPLETE"}:
            raise ApplicationError("La malla no está en un estado ejecutable.")
        key = (project_id, map_id)
        with self._lock:
            self._cancel_requests[key] = threading.Event()
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

    def cancel(self, *, project_id: str, map_id: str, runtime: Any) -> dict[str, Any]:
        key = (project_id, map_id)
        with self._lock:
            self._cancel_requests[key] = threading.Event()
        with self._lock:
            cancel_request = self._cancel_requests.get(key)
            if cancel_request is not None:
                cancel_request.set()
        try:
            runtime.cancel_operation()
        except Exception:
            pass
        return self.physical_map_service.mark_status(project_id=project_id, map_id=map_id, status="CANCELLED")

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
                    self.physical_map_service.mark_status(project_id=project_id, map_id=map_id, status="CANCELLED")
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
                if not self._ensure_motion_sample_ready(project_id=project_id, map_id=map_id, runtime=runtime, point=point):
                    return
                self._probe_one_point(project_id, map_id, runtime, point, probe_config=payload.get("probe_config"))
        finally:
            with self._lock:
                thread = self._threads.get(key)
                if thread is threading.current_thread():
                    self._threads.pop(key, None)
                    self._cancel_requests.pop(key, None)

    def _runtime_snapshot(self, runtime: Any) -> dict[str, Any]:
        snapshot_fn = getattr(runtime, "snapshot", None)
        if snapshot_fn is None:
            return {}
        try:
            snapshot = snapshot_fn()
        except Exception:
            return {}
        return snapshot if isinstance(snapshot, dict) else {}

    def _position_sample_recent(self, safety: dict[str, Any]) -> bool:
        if not isinstance(safety, dict):
            return False
        if "position_sample_recent" in safety:
            return bool(safety.get("position_sample_recent"))
        return bool(safety.get("telemetry_recent"))

    def _ensure_motion_sample_ready(self, *, project_id: str, map_id: str, runtime: Any, point: dict[str, Any]) -> bool:
        snapshot = self._runtime_snapshot(runtime)
        safety = snapshot.get("safety") if isinstance(snapshot, dict) else {}
        if self._position_sample_recent(safety):
            return True
        refresh_error: Exception | None = None
        refresh_fn = getattr(runtime, "refresh_motion_snapshot_http", None)
        if callable(refresh_fn):
            try:
                refresh_fn(timeout_s=0.25)
            except Exception as error:
                refresh_error = error
            snapshot = self._runtime_snapshot(runtime)
            safety = snapshot.get("safety") if isinstance(snapshot, dict) else {}
            if self._position_sample_recent(safety):
                return True
        self._pause_for_stale_telemetry(
            project_id=project_id,
            map_id=map_id,
            point=point,
            snapshot=snapshot,
            safety=safety if isinstance(safety, dict) else {},
            refresh_error=refresh_error,
        )
        return False

    def _pause_for_stale_telemetry(
        self,
        *,
        project_id: str,
        map_id: str,
        point: dict[str, Any],
        snapshot: dict[str, Any],
        safety: dict[str, Any],
        refresh_error: Exception | None,
    ) -> None:
        point_index = int(point["index"])
        target = {"x_mm": point.get("x_machine"), "y_mm": point.get("y_machine"), "point_index": point_index}
        observed = self._observed_from_snapshot(snapshot)
        websocket_age = snapshot.get("websocket_age_s")
        position_age = snapshot.get("position_sample_age_s")
        gcode_age = None
        telemetry = snapshot.get("telemetry") if isinstance(snapshot, dict) else None
        if isinstance(telemetry, dict):
            gcode_age = telemetry.get("gcode_position_age_s")
        if gcode_age is None:
            gcode_age = snapshot.get("gcode_position_age_s")
        serial_age = snapshot.get("serial_age_s")
        reason = (
            "La última muestra live_position de Moonraker está obsoleta; "
            f"websocket_age_s={websocket_age}, position_sample_age_s={position_age}, "
            f"gcode_position_age_s={gcode_age}, arduino_age_s={serial_age}."
        )
        if refresh_error is not None:
            reason += f" El refresco HTTP acotado falló: {refresh_error}."
        elif bool(safety.get("websocket_recent")):
            reason += " Moonraker sigue conectado, pero no llegó una muestra nueva de live_position."
        else:
            reason += " No hay confirmación reciente de posición desde Moonraker."
        self.physical_map_service.mark_status(project_id=project_id, map_id=map_id, status="MESH_PAUSED")
        self.physical_map_service.update_execution_state(
            project_id=project_id,
            map_id=map_id,
            worker_active=False,
            point_state="MESH_PAUSED",
            point_index=point_index,
            target=target,
            observed=observed,
            error=reason,
            last_event="Malla pausada por telemetría stale; el mismo punto permanece pendiente para reintento explícito.",
        )

    def _probe_one_point(self, project_id: str, map_id: str, runtime: Any, point: dict[str, Any], *, probe_config: dict[str, Any] | None = None) -> None:
        point_index = int(point["index"])
        attempts = int(point.get("attempts", 0))
        target = {"x_mm": point.get("x_machine"), "y_mm": point.get("y_machine"), "point_index": point_index}
        # Cada inicio o reintento explícito ejecuta exactamente un intento.
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
                def progress(state: str, detail: dict[str, Any]) -> None:
                    observed_now = self._observed_from_runtime(runtime)
                    self.physical_map_service.update_execution_state(
                        project_id=project_id, map_id=map_id, worker_active=True, point_state=state,
                        point_index=point_index, retry_count=attempts - 1, command=str(detail.get("command") or state),
                        target={**target, **detail}, observed=observed_now,
                        last_event=f"Punto {point_index + 1}: {state}.",
                    )
                try:
                    result = runtime.probe_mesh_point(point, probe_config=probe_config, progress_callback=progress)
                except TypeError as error:
                    if "progress_callback" not in str(error):
                        raise
                    result = runtime.probe_mesh_point(point, probe_config=probe_config)
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
                with self._lock:
                    cancelled = bool(self._cancel_requests.get((project_id, map_id)) and self._cancel_requests[(project_id, map_id)].is_set())
                if cancelled:
                    self.physical_map_service.mark_status(project_id=project_id, map_id=map_id, status="CANCELLED")
                    self.physical_map_service.update_execution_state(
                        project_id=project_id, map_id=map_id, worker_active=False, point_state="CANCELLED",
                        point_index=point_index, error=str(error), target=target, observed=observed,
                        last_event="Malla cancelada por el operador; no se iniciará otro paso.",
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
        snapshot = self._runtime_snapshot(runtime)
        if not snapshot:
            return None
        return self._observed_from_snapshot(snapshot)

    def _observed_from_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        safety = snapshot.get("safety") if isinstance(snapshot, dict) else {}
        klipper = snapshot.get("klipper") if isinstance(snapshot, dict) else {}
        telemetry = snapshot.get("telemetry") if isinstance(snapshot, dict) else {}
        return {
            "state": snapshot.get("state"),
            "position": snapshot.get("position") or snapshot.get("machine_position") or (klipper.get("position") if isinstance(klipper, dict) else None),
            "homed_axes": snapshot.get("homed_axes") or (klipper.get("homed_axes") if isinstance(klipper, dict) else None),
            "last_command": snapshot.get("last_command") or snapshot.get("last_command_text"),
            "telemetry_age_s": snapshot.get("telemetry_age_s"),
            "websocket_age_s": snapshot.get("websocket_age_s"),
            "position_sample_age_s": snapshot.get("position_sample_age_s") or (telemetry.get("position_sample_age_s") if isinstance(telemetry, dict) else None),
            "position_changed_age_s": snapshot.get("position_changed_age_s") or (telemetry.get("position_changed_age_s") if isinstance(telemetry, dict) else None),
            "gcode_position_age_s": snapshot.get("gcode_position_age_s") or (telemetry.get("gcode_position_age_s") if isinstance(telemetry, dict) else None),
            "serial_age_s": snapshot.get("serial_age_s"),
            "safety": {
                "telemetry_recent": safety.get("telemetry_recent") if isinstance(safety, dict) else None,
                "websocket_recent": safety.get("websocket_recent") if isinstance(safety, dict) else None,
                "position_sample_recent": safety.get("position_sample_recent") if isinstance(safety, dict) else None,
                "position_changed_recent": safety.get("position_changed_recent") if isinstance(safety, dict) else None,
                "blocked_reason": safety.get("blocked_reason") if isinstance(safety, dict) else None,
            },
        }
