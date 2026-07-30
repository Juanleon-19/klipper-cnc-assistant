from __future__ import annotations

import json
import hashlib
import threading
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from klipper_cnc_assistant.application.compensated_gcode_service import CompensatedGCodeService
from klipper_cnc_assistant.application.errors import ApplicationError, NotFoundError
from klipper_cnc_assistant.application.physical_map_service import PhysicalMapService
from klipper_cnc_assistant.application.reference_service import ReferenceSessionService
from klipper_cnc_assistant.domain import BoardFace, OperacionPCB, ProjectValidationError
from klipper_cnc_assistant.heightmap.coverage import DOMAIN_TOLERANCE_MM, build_coverage_report
from klipper_cnc_assistant.moonraker.client import MoonrakerClient, MoonrakerError
from klipper_cnc_assistant.storage import JsonProjectRepository


JOB_PLAN_SCHEMA = "job-plan-v1"
JOB_RUN_SCHEMA = "job-run-v1"
RUN_TERMINAL_STATES = {"JOB_COMPLETE", "JOB_CANCELLED", "JOB_ERROR"}
RUN_WAITING_STATES = {"SPINDLE_STOP_REQUIRED", "TOOL_CHANGE_REQUIRED", "READY_TO_RESUME", "OPERATION_PAUSED", "JOB_PAUSED", "RECOVERY_REQUIRED"}
RUN_ACTIVE_STATES = {
    "JOB_STARTING",
    "OPERATION_PREFLIGHT",
    "OPERATION_UPLOADING",
    "OPERATION_READY",
    "WAITING_FOR_KLIPPER",
    "PRINT_QUEUED",
    "OPERATION_RUNNING",
    "SPINDLE_STOP_CONFIRMED",
    "RETRACTING",
    "MOVING_TO_TOOL_CHANGE_SAFE_Z",
    "MOVING_TO_TOOL_CHANGE_XY",
    "MOVING_TO_TOOL_CHANGE",
    "RETURNING_TO_REFERENCE_SAFE_Z",
    "RETURNING_TO_REFERENCE_XY",
    "PROBING_TOOL_REFERENCE",
    "COMPENSATING_NEXT_OPERATIONS",
    "NEXT_OPERATION_READY",
}
RUN_REFRESHABLE_IDLE_STATES = {"JOB_DRAFT", "JOB_VALIDATING"}
RUN_MARKED_ACTIVE_STATES = RUN_ACTIVE_STATES | RUN_WAITING_STATES | {"JOB_VALIDATING"}
STALE_RUN_IDLE_SECONDS = 300.0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _utc_now().isoformat()


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _tool_key(operation: OperacionPCB) -> str:
    return operation.tool_id or (operation.herramienta or "sin-herramienta").strip().lower().replace(" ", "-")


def _safe_face(face: str) -> str:
    return str(face).strip().lower().replace(" ", "-")


@dataclass(frozen=True)
class JobContext:
    project_id: str
    setup_id: str
    face: str


@dataclass(frozen=True)
class ToolInstallationCalibration:
    calibration_id: str
    tool_id: str
    installation_session_id: str
    reference_point_id: str
    reference_machine_x: float
    reference_machine_y: float
    tool_reference_z: float
    measured_at: str
    probe_method: str
    valid: bool
    invalidation_reason: str | None = None


class MoonrakerJobAdapter:
    def __init__(self, runtime: Any, client_factory: Callable[..., MoonrakerClient] = MoonrakerClient) -> None:
        self.runtime = runtime
        self.client_factory = client_factory

    def runtime_snapshot(self) -> dict[str, Any]:
        return self.runtime.snapshot()

    def _client(self) -> MoonrakerClient:
        config = self.runtime.config
        if not config.moonraker_url:
            raise ApplicationError("Moonraker no está configurado para ejecución del trabajo.")
        return self.client_factory(config.moonraker_url, timeout=config.moonraker_request_timeout_s)

    def upload_file(self, *, local_path: Path, project_id: str, setup_id: str, face: str) -> dict[str, Any]:
        remote_dir = f"klipper-cnc-assistant/{project_id}/{setup_id}/{_safe_face(face)}"
        checksum = hashlib.sha256(local_path.read_bytes()).hexdigest()
        return self._client().upload_file(local_path=local_path, remote_dir=remote_dir, checksum=checksum, print_file=True)

    def start_file(self, remote_path: str) -> dict[str, Any]:
        return self._client().start_print(remote_path)

    def pause(self) -> dict[str, Any]:
        return self._client().pause_print()

    def resume(self) -> dict[str, Any]:
        return self._client().resume_print()

    def cancel(self) -> dict[str, Any]:
        return self._client().cancel_print()

    def print_status(self) -> dict[str, Any]:
        status = self._client().query_objects(
            {
                "webhooks": ["state", "state_message"],
                "print_stats": ["state", "filename", "message", "print_duration", "total_duration"],
                "virtual_sdcard": ["progress", "file_position", "file_size", "file_path", "is_active"],
                "toolhead": ["position"],
                "motion_report": ["live_position", "live_velocity"],
            }
        )
        webhooks = status.get("webhooks") or {}
        print_stats = status.get("print_stats") or {}
        virtual_sdcard = status.get("virtual_sdcard") or {}
        motion_report = status.get("motion_report") or {}
        toolhead = status.get("toolhead") or {}
        return {
            "connected": True,
            "klipper_ready": webhooks.get("state") == "ready",
            "klipper_state": webhooks.get("state"),
            "state_message": webhooks.get("state_message"),
            "state": print_stats.get("state"),
            "filename": print_stats.get("filename"),
            "message": print_stats.get("message"),
            "progress": virtual_sdcard.get("progress"),
            "file_position": virtual_sdcard.get("file_position"),
            "file_size": virtual_sdcard.get("file_size"),
            "file_path": virtual_sdcard.get("file_path"),
            "print_duration": print_stats.get("print_duration"),
            "active": bool(virtual_sdcard.get("is_active")),
            "is_active": bool(virtual_sdcard.get("is_active")),
            "live_position": motion_report.get("live_position"),
            "live_velocity": motion_report.get("live_velocity"),
            "toolhead_position": toolhead.get("position"),
            "updated_at": _iso_now(),
        }

    def spindle_control_mode(self) -> str:
        return str(getattr(self.runtime.config, "spindle_control_mode", "manual") or "manual").strip().lower()

    def stop_spindle(self) -> dict[str, Any]:
        if self.spindle_control_mode() == "manual":
            return {"mode": "manual", "command_sent": False}
        return self._client().send_gcode("M5")

    def move_to_tool_change_position(self) -> dict[str, Any]:
        return self.runtime.move_to_tool_change_position()

    def move_to_reference_point(self, *, x_mm: float, y_mm: float) -> dict[str, Any]:
        return self.runtime.go_to_reference_point(reference_x=x_mm, reference_y=y_mm)

    def probe_tool_reference(self, *, x_mm: float, y_mm: float, probe_config: dict[str, Any] | None) -> dict[str, Any]:
        point = {
            "index": 0,
            "role": "REFERENCE",
            "x_machine": x_mm,
            "y_machine": y_mm,
        }
        return self.runtime.probe_mesh_point(point, probe_config=probe_config)


class JobService:
    def __init__(
        self,
        repository: JsonProjectRepository,
        physical_map_service: PhysicalMapService,
        reference_service: ReferenceSessionService,
        compensated_gcode_service: CompensatedGCodeService,
        runtime: Any,
        *,
        adapter_factory: Callable[[Any], MoonrakerJobAdapter] = MoonrakerJobAdapter,
    ) -> None:
        self.repository = repository
        self.physical_map_service = physical_map_service
        self.reference_service = reference_service
        self.compensated_gcode_service = compensated_gcode_service
        self.runtime = runtime
        self.adapter_factory = adapter_factory
        self._lock = threading.RLock()
        self._threads: dict[tuple[str, str, str], threading.Thread] = {}

    def get_plan(self, *, project_id: str, setup_id: str, face: str) -> dict[str, Any]:
        context = self._context(project_id, setup_id, face)
        plan = self._build_plan(context)
        self._write_manifest(context, plan)
        self._save_plan(context, plan)
        return plan

    def generate_project_compensation(self, *, project_id: str, setup_id: str, face: str) -> dict[str, Any]:
        context = self._context(project_id, setup_id, face)
        plan = self._build_plan(context)
        generated_results: dict[str, dict[str, Any]] = {}
        for item in plan["operations"]:
            if item["blocking"]:
                continue
            try:
                generated_results[item["operation_id"]] = self.compensated_gcode_service.generate(
                    project_id,
                    item["operation_id"],
                    require_tool_reference=False,
                )
            except Exception as error:
                generated_results[item["operation_id"]] = {"error": str(error)}
        refreshed = self._build_plan(context, generated_results=generated_results)
        self._write_manifest(context, refreshed)
        refreshed["updated_at"] = _iso_now()
        self._save_plan(context, refreshed)
        return refreshed

    def prepare_run(self, *, project_id: str, setup_id: str, face: str) -> dict[str, Any]:
        context = self._context(project_id, setup_id, face)
        plan = self._load_or_build_plan(context)
        checks = self._build_run_checks(context, plan)
        ready = all(check["ok"] for check in checks)
        current = self._load_run(context)
        run = self._base_run(context, plan) if current is None or current.get("state") in (RUN_TERMINAL_STATES | RUN_REFRESHABLE_IDLE_STATES) else current
        run["checks"] = checks
        run["state"] = "JOB_READY" if ready else "JOB_VALIDATING"
        run["ready"] = ready
        run["next_action"] = "Iniciar trabajo" if ready else "Resolver bloqueos"
        run["available_actions"] = ["start"] if ready else []
        run["updated_at"] = _iso_now()
        self._save_run(context, run)
        return run

    def start_run(self, *, project_id: str, setup_id: str, face: str) -> dict[str, Any]:
        context = self._context(project_id, setup_id, face)
        with self._lock:
            current = self._load_run(context)
        run: dict[str, Any] | None = current
        if current is not None:
            recovered = self._recover_active_print_if_possible(context, current)
            if recovered is not None:
                self._start_worker(context)
                return recovered
            state = str(current.get("state") or "")
            if state in RUN_TERMINAL_STATES:
                run = None
            elif state in RUN_REFRESHABLE_IDLE_STATES:
                run = self.prepare_run(project_id=project_id, setup_id=setup_id, face=face)
            elif state != "JOB_READY":
                raise ApplicationError("JOB_ACTIVE_CONFLICT")
        prepared = run if run is not None else self.prepare_run(project_id=project_id, setup_id=setup_id, face=face)
        if not prepared.get("ready"):
            raise ApplicationError("El trabajo no está listo para iniciar. Revise el preflight general.")
        run = prepared
        if run.get("state") not in {"JOB_READY", "JOB_PAUSED", "OPERATION_PAUSED", "TOOL_REFERENCE_READY", "READY_TO_RESUME", "NEXT_OPERATION_READY"}:
            raise ApplicationError(f"El trabajo no puede iniciar desde estado {run.get('state')}.")
        run["state"] = "JOB_STARTING"
        run["started_at"] = run.get("started_at") or _iso_now()
        run["updated_at"] = _iso_now()
        run["next_action"] = "Preparando primera operación"
        run["available_actions"] = ["pause", "cancel"]
        self._append_event(run, "info", "Trabajo iniciado; el backend continuará la secuencia.")
        self._save_run(context, run)
        self._start_worker(context)
        return run

    def dry_run(self, *, project_id: str, setup_id: str, face: str) -> dict[str, Any]:
        """Validate the immutable generated artifacts without runtime or motion access."""
        context = self._context(project_id, setup_id, face)
        plan = self._load_or_build_plan(context)
        operations: list[dict[str, Any]] = []
        for row in plan["operations"]:
            generated = self._generated_payload_for_operation(plan, row["operation_id"])
            if generated is None:
                operations.append({"operation_id": row["operation_id"], "ok": False, "error": "No hay plan compensado vigente."})
                continue
            metadata_path = self.repository.project_dir(context.project_id) / str(generated["metadata_path"])
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            trace = list(metadata.get("movement_trace") or [])
            xs = [item["machine_x_mm"] for item in trace]
            ys = [item["machine_y_mm"] for item in trace]
            zs = [item["final_z_mm"] for item in trace if item.get("final_z_mm") is not None]
            operations.append({
                "operation_id": row["operation_id"], "tool_id": row["tool_id"],
                "tool_reference_z": (metadata.get("reference_frame") or {}).get("surface_reference_z_mm"),
                "original_segments": metadata.get("segments_before"), "compensated_segments": metadata.get("segments_after"),
                "surface_delta_min_mm": metadata.get("compensation_delta_min_mm"),
                "surface_delta_max_mm": metadata.get("compensation_delta_max_mm"),
                "limits": {"x_min": min(xs) if xs else None, "x_max": max(xs) if xs else None, "y_min": min(ys) if ys else None, "y_max": max(ys) if ys else None, "z_min": min(zs) if zs else None, "z_max": max(zs) if zs else None},
                "first_movement": trace[0] if trace else None, "last_movement": trace[-1] if trace else None,
                "plan_hash": metadata.get("generated_hash"), "ok": True,
            })
        return {"mode": "DRY_RUN", "movement_lock_acquired": False, "moonraker_commands_sent": 0, "operations": operations, "ok": all(item["ok"] for item in operations)}

    def get_run(self, *, project_id: str, setup_id: str, face: str) -> dict[str, Any]:
        context = self._context(project_id, setup_id, face)
        run = self._load_run(context)
        if run is None:
            return self.prepare_run(project_id=project_id, setup_id=setup_id, face=face)
        return run

    def live_execution(self, *, project_id: str, setup_id: str, face: str) -> dict[str, Any]:
        context = self._context(project_id, setup_id, face)
        run = self._load_run(context)
        if run is None:
            run = self.prepare_run(project_id=project_id, setup_id=setup_id, face=face)
        diagnosis = self._diagnose_run(context, run)
        status = diagnosis["moonraker"]
        worker_alive = bool(diagnosis["run"].get("worker_alive"))
        operations = list(run.get("operations") or [])
        index = int(run.get("current_operation_index", 0) or 0)
        if operations:
            index = max(0, min(index, len(operations) - 1))
            operation = operations[index]
        else:
            operation = None
        expected = self._normalize_filename((operation or {}).get("remote_file"))
        observed = self._normalize_filename(status.get("filename"))
        total = int(run.get("summary", {}).get("operations_total", len(operations)) or 0)
        completed = int(run.get("summary", {}).get("operations_completed", 0) or 0)
        progress = self._clamp_progress((operation or {}).get("progress"))
        overall_progress = 1.0 if str(run.get("state")) == "JOB_COMPLETE" else (min(1.0, (completed + progress) / total) if total else 0.0)
        next_index = index + 1
        next_operation = operations[next_index] if 0 <= next_index < len(operations) else None
        sync_reason = None
        print_state = str(status.get("print_state") or "").lower()
        if print_state == "printing":
            if not expected:
                sync_reason = "remote_file_missing"
            elif expected != observed:
                sync_reason = "filename_mismatch"
            elif str((operation or {}).get("execution_status") or "") != "RUNNING":
                sync_reason = "jobrun_not_running"
            elif not bool((operation or {}).get("observed_printing")):
                sync_reason = "observed_printing_missing"
        elif worker_alive is False and str(run.get("state")) in RUN_ACTIVE_STATES:
            sync_reason = "watcher_inactive"
        return {
            "moonraker": {
                "connected": bool(status.get("connected", True)),
                "klipper_state": status.get("klipper_state"),
                "print_state": status.get("print_state"),
                "filename": status.get("filename"),
                "progress": self._clamp_progress(status.get("progress")),
                "is_active": bool(status.get("is_active", status.get("active"))),
                "file_position": status.get("file_position"),
                "file_size": status.get("file_size"),
                "print_duration": status.get("print_duration"),
                "message": status.get("message"),
                "updated_at": status.get("updated_at") or _iso_now(),
            },
            "run": {
                "run_id": run.get("run_id"),
                "status": run.get("state") or "JOB_DRAFT",
                "current_operation_index": index if operations else 0,
                "total_operations": total,
                "completed_operations": completed,
                "overall_progress": overall_progress,
                "next_action": run.get("next_action") or "Preparar trabajo",
                "available_actions": list(run.get("available_actions") or []),
                "worker_alive": worker_alive,
                "watcher_alive": worker_alive,
                "supervisor_registered": bool(diagnosis["run"].get("supervisor_registered")),
                "movement_lock": diagnosis["run"].get("movement_lock"),
                "job_lock": bool(diagnosis["run"].get("job_lock")),
                "last_watcher_error": run.get("last_watcher_error"),
                "recovery_state": run.get("recovery_state"),
                "stale_candidate": self._is_stale_run(diagnosis["run"], str(run.get("state") or "JOB_DRAFT")),
                "updated_at": run.get("updated_at"),
            },
            "operation": {
                "operation_id": (operation or {}).get("operation_id"),
                "name": (operation or {}).get("name"),
                "tool": (operation or {}).get("tool_name"),
                "execution_status": (operation or {}).get("execution_status") or "PENDING",
                "expected_remote_file": (operation or {}).get("remote_file"),
                "observed_filename": status.get("filename"),
                "filename_match": bool(expected and expected == observed),
                "observed_printing": bool((operation or {}).get("observed_printing")),
                "progress": progress,
            },
            "operations": operations,
            "transition": {
                "state": run.get("state") or "JOB_DRAFT",
                "required_tool": (next_operation or {}).get("tool_name"),
                "operator_confirmation_required": str(run.get("state")) in {"SPINDLE_STOP_REQUIRED", "TOOL_CHANGE_REQUIRED", "READY_TO_RESUME"},
            },
            "synchronization": {
                "ok": sync_reason is None,
                "reason": sync_reason,
            },
            "events": self._dedupe_events(run.get("events") or []),
            "job_run": run,
        }

    def describe_run_conflict(self, *, project_id: str, setup_id: str, face: str) -> dict[str, Any]:
        context = self._context(project_id, setup_id, face)
        run = self._load_run(context)
        if run is None:
            raise NotFoundError("No existe una ejecución de trabajo para este montaje/cara.")
        diagnosis = self._diagnose_run(context, run)
        state = str(run.get("state") or "JOB_DRAFT")
        conflict_condition = (
            f"current_run.state={state} no es terminal ni JOB_READY, "
            "por lo que start_run rechaza un segundo inicio para el mismo montaje/cara."
        )
        can_archive_stale = (
            self._moonraker_is_idle(diagnosis["moonraker"])
            and self._is_stale_run(diagnosis["run"], state)
        )
        return {
            "code": "JOB_ACTIVE_CONFLICT",
            "message": "Ya existe un trabajo activo para este montaje y cara.",
            "conflict_condition": conflict_condition,
            "existing_run": diagnosis["run"],
            "moonraker": diagnosis["moonraker"],
            "available_actions": self._available_recovery_actions(diagnosis, run),
            "can_archive_stale": can_archive_stale,
        }

    def archive_stale_run(self, *, project_id: str, setup_id: str, face: str) -> dict[str, Any]:
        context = self._context(project_id, setup_id, face)
        run = self._load_run(context)
        if run is None:
            raise NotFoundError("No existe una ejecución de trabajo para este montaje/cara.")
        diagnosis = self._diagnose_run(context, run)
        moonraker = diagnosis["moonraker"]
        if not self._moonraker_is_idle(moonraker):
            raise ApplicationError("No se puede archivar la ejecución obsoleta mientras Moonraker siga imprimiendo o virtual_sdcard esté activa.")
        if not self._is_stale_run(diagnosis["run"], str(run.get("state") or "JOB_DRAFT")):
            raise ApplicationError("La ejecución actual no cumple el criterio real de obsolescencia; primero revalide el plan o espere a que el estado se estabilice.")
        released = self._release_stale_supervisor(context, run, diagnosis)
        previous_status = str(run.get("state") or "JOB_DRAFT")
        archived = json.loads(json.dumps(run))
        archived["previous_status"] = previous_status
        archived["state"] = "STALE_RUN_ARCHIVED"
        archived["recovery_state"] = "RECOVERY_REQUIRED" if diagnosis["run"]["supervisor_registered"] else "STALE_RUN_ARCHIVED"
        archived["completed_at"] = archived.get("completed_at") or _iso_now()
        archived["updated_at"] = _iso_now()
        archived["available_actions"] = []
        archived["next_action"] = "Ejecución obsoleta archivada"
        archived["moonraker_snapshot"] = moonraker
        self._append_event(archived, "warning", "Ejecución obsoleta archivada manualmente tras confirmar que Moonraker estaba inactivo.")
        archive_path = self._write_archived_run(context, archived, suffix="stale")
        run_file = self._run_file(context)
        if run_file.exists():
            run_file.unlink()
            released.append("job_run.current_run")
        return {
            "archived_run_id": archived.get("run_id"),
            "previous_status": previous_status,
            "archive_path": self._relative_to_project(context.project_id, archive_path),
            "locks_released": released,
            "can_start_new_run": not self._run_file(context).exists(),
        }

    def history(self, *, project_id: str, setup_id: str, face: str) -> list[dict[str, Any]]:
        history_dir = self._history_dir(self._context(project_id, setup_id, face))
        if not history_dir.exists():
            return []
        items: list[dict[str, Any]] = []
        for file in sorted(history_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            payload = json.loads(file.read_text(encoding="utf-8"))
            items.append(
                {
                    "run_id": payload.get("run_id"),
                    "state": payload.get("state"),
                    "started_at": payload.get("started_at"),
                    "completed_at": payload.get("completed_at"),
                    "tool_changes_completed": payload.get("summary", {}).get("tool_changes_completed", 0),
                    "operations_completed": payload.get("summary", {}).get("operations_completed", 0),
                    "manifest_path": payload.get("manifest_path"),
                }
            )
        return items

    def run_action(self, *, project_id: str, setup_id: str, face: str, action: str) -> dict[str, Any]:
        context = self._context(project_id, setup_id, face)
        run = self._load_run(context)
        if run is None:
            raise NotFoundError("No existe una ejecución de trabajo para este montaje/cara.")
        adapter = self.adapter_factory(self.runtime)
        if action == "pause":
            try:
                adapter.pause()
            except Exception:
                pass
            run["state"] = "JOB_PAUSED"
            run["next_action"] = "Reanudar trabajo"
            run["available_actions"] = ["resume", "cancel"]
            self._append_event(run, "warning", "Trabajo pausado por el operador.")
        elif action == "resume":
            if run["state"] not in {"JOB_PAUSED", "OPERATION_PAUSED", "TOOL_REFERENCE_READY", "READY_TO_RESUME", "NEXT_OPERATION_READY"}:
                raise ApplicationError(f"No se puede reanudar desde {run['state']}.")
            try:
                if run["state"] == "OPERATION_PAUSED":
                    adapter.resume()
            except Exception:
                pass
            run["state"] = "JOB_STARTING" if run["state"] in {"JOB_PAUSED", "TOOL_REFERENCE_READY", "READY_TO_RESUME", "NEXT_OPERATION_READY"} else "OPERATION_RUNNING"
            run["available_actions"] = ["pause", "cancel"]
            run["next_action"] = "Reanudando trabajo"
            self._append_event(run, "info", "Trabajo reanudado por el operador.")
            self._start_worker(context)
        elif action == "cancel":
            try:
                adapter.cancel()
            except Exception:
                pass
            run["state"] = "JOB_CANCELLED"
            run["completed_at"] = _iso_now()
            run["available_actions"] = []
            run["next_action"] = "Trabajo cancelado"
            self._append_event(run, "warning", "Trabajo cancelado por el operador.")
            self._archive_run(context, run)
        elif action == "retry-tool-change-transition":
            if run["state"] != "RECOVERY_REQUIRED":
                raise ApplicationError("El reintento de transición solo aplica cuando la ejecución quedó en recuperación.")
            current_index = int(run.get("current_operation_index", 0) or 0)
            if current_index < 0 or current_index >= len(run["operations"]):
                raise ApplicationError("No existe operación completada para reintentar la transición.")
            if run["operations"][current_index].get("execution_status") != "COMPLETED":
                raise ApplicationError("La operación anterior debe estar COMPLETED antes de reintentar la transición.")
            next_index = current_index + 1
            if next_index >= len(run["operations"]):
                raise ApplicationError("No existe una siguiente operación que requiera cambio de herramienta.")
            if run["operations"][next_index].get("tool_key") == run["operations"][current_index].get("tool_key"):
                raise ApplicationError("La siguiente operación no requiere cambio de herramienta.")
            self._validate_spindle_stop_confirmation(adapter)
            self._handle_tool_change_required(context, run, operation_index=next_index, retry=True)
            return run
        elif action == "confirm-spindle-stopped":
            if run["state"] != "SPINDLE_STOP_REQUIRED":
                raise ApplicationError("La confirmación del spindle detenido solo aplica cuando el trabajo está esperando al operador.")
            next_index = int(run.get("current_operation_index", 0) or 0) + 1
            if next_index >= len(run["operations"]):
                raise ApplicationError("No existe una siguiente operación que requiera cambio de herramienta.")
            self._validate_spindle_stop_confirmation(adapter)
            next_operation = run["operations"][next_index]
            run["state"] = "SPINDLE_STOP_CONFIRMED"
            run["next_action"] = "Iniciando transición segura hacia el cambio de herramienta"
            run["available_actions"] = ["cancel"]
            run["updated_at"] = _iso_now()
            self._append_event(run, "info", f"El operador confirmó el spindle detenido; iniciando transición segura antes de {next_operation['name']}.")
            self._save_run(context, run)
            self._start_worker(context)
            return run
        elif action == "confirm-tool-change":
            if run["state"] != "TOOL_CHANGE_REQUIRED":
                raise ApplicationError("El cambio de herramienta solo puede confirmarse cuando el trabajo está esperando al operador.")
            next_index = int(run["current_operation_index"]) + 1
            next_operation = run["operations"][next_index]
            plan = self._load_or_build_plan(context)
            active_map = plan["active_map"]
            self.physical_map_service.invalidate_tool_reference(
                project_id=context.project_id,
                map_id=active_map["map_id"],
                operation_id=next_operation["operation_id"],
            )
            next_operation["reference_status"] = "REQUIERE_REFERENCIA"
            next_operation["installation_revision"] = _utc_now().strftime("%Y%m%d-%H%M%S")
            run["state"] = "TOOL_CHANGE_CONFIRMED"
            run["next_action"] = "Moviendo al punto de referencia para calibrar la nueva herramienta"
            run["available_actions"] = ["cancel"]
            self._append_event(run, "info", f"Herramienta confirmada para {next_operation['tool_name']}; la CNC irá automáticamente al punto de referencia.")
        elif action == "measure-reference":
            self._measure_tool_reference(context, run)
        elif action == "continue":
            if run["state"] not in {"TOOL_REFERENCE_READY", "READY_TO_RESUME", "NEXT_OPERATION_READY"}:
                raise ApplicationError("Continuar solo aplica cuando ya existe referencia Z y hay una siguiente operación preparada.")
            run["state"] = "JOB_STARTING"
            run["next_action"] = "Continuando secuencia"
            run["available_actions"] = ["pause", "cancel"]
            self._append_event(run, "info", "Continuación manual confirmada por el operador.")
            self._save_run(context, run)
            self._start_worker(context)
            return run
        else:
            raise ApplicationError(f"Acción de trabajo no soportada: {action}.")
        run["updated_at"] = _iso_now()
        self._save_run(context, run)
        if action in {"confirm-tool-change", "measure-reference"}:
            self._start_worker(context)
        return run

    def _measure_tool_reference(self, context: JobContext, run: dict[str, Any]) -> None:
        plan = self._load_or_build_plan(context)
        active_map = plan["active_map"]
        if active_map is None:
            raise ApplicationError("No existe mapa físico activo para medir la referencia de herramienta.")
        operation_index = int(run["current_operation_index"]) + 1 if run["state"] == "TOOL_CHANGE_CONFIRMED" else int(run.get("current_operation_index", 0) or 0)
        operation_payload = run["operations"][operation_index]
        adapter = self.adapter_factory(self.runtime)
        reference_x = float(active_map["machine_origin_x"])
        reference_y = float(active_map["machine_origin_y"])
        run["state"] = "MOVING_TO_REFERENCE"
        run["available_actions"] = ["cancel"]
        run["next_action"] = f"Moviendo al punto de referencia CNC X={reference_x:.3f}, Y={reference_y:.3f}"
        self._append_event(run, "info", run["next_action"])
        self._save_run(context, run)
        adapter.move_to_reference_point(x_mm=reference_x, y_mm=reference_y)
        run["state"] = "CALIBRATING_TOOL"
        run["next_action"] = "Sondeando referencia Z de la nueva herramienta"
        self._append_event(run, "info", run["next_action"])
        self._save_run(context, run)
        probe = adapter.probe_tool_reference(
            x_mm=reference_x,
            y_mm=reference_y,
            probe_config=active_map.get("probe_config"),
        )
        snapshot = adapter.runtime_snapshot()
        position = probe.get("probe") or self.runtime.last_probe_position()
        self.reference_service.capture_physical_z_reference(
            context.project_id,
            operation_payload["operation_id"],
            position=position,
            machine_label=str(snapshot["moonraker"].get("url") or "physical"),
            homed_axes=snapshot["klipper"].get("homed_axes"),
            session_id=snapshot.get("started_at"),
        )
        self.physical_map_service.record_tool_reference(
            project_id=context.project_id,
            map_id=active_map["map_id"],
            operation_id=operation_payload["operation_id"],
            position=position,
            machine_label=str(snapshot["moonraker"].get("url") or "physical"),
            homed_axes=snapshot["klipper"].get("homed_axes"),
            session_id=snapshot.get("started_at"),
            installation_id=operation_payload.get("installation_revision"),
        )
        run["state"] = "REGENERATING_COMPENSATION"
        run["next_action"] = "Regenerando compensación pendiente con la nueva referencia Z"
        self._save_run(context, run)
        self.generate_project_compensation(project_id=context.project_id, setup_id=context.setup_id, face=context.face)
        run["state"] = "VALIDATING_REGENERATED_PLAN"
        dry_run = self.dry_run(project_id=context.project_id, setup_id=context.setup_id, face=context.face)
        if not dry_run.get("ok"):
            raise ApplicationError("El dry-run del plan regenerado falló.")
        run["dry_run"] = dry_run
        operation_payload["reference_status"] = "LISTA"
        run["current_tool_key"] = operation_payload["tool_key"]
        run["summary"]["tool_changes_completed"] = int(run["summary"].get("tool_changes_completed", 0)) + 1
        run["state"] = "READY_TO_RESUME"
        run["next_action"] = "Revisar la nueva calibración y continuar trabajo"
        run["available_actions"] = ["continue", "cancel"]
        self._append_event(run, "info", f"Referencia Z medida para {operation_payload['tool_name']}; esperando confirmación explícita para continuar.")
        self._save_run(context, run)

    def _start_worker(self, context: JobContext) -> None:
        key = (context.project_id, context.setup_id, context.face)
        with self._lock:
            thread = self._threads.get(key)
            if thread is not None and thread.is_alive():
                return
            worker = threading.Thread(target=self._run_worker, args=(context,), name=f"job-{context.setup_id}-{context.face}", daemon=True)
            self._threads[key] = worker
            worker.start()

    def _run_worker(self, context: JobContext) -> None:
        key = (context.project_id, context.setup_id, context.face)
        try:
            try:
                while True:
                    run = self._load_run(context)
                    if run is None or run.get("state") in RUN_TERMINAL_STATES | RUN_WAITING_STATES:
                        return
                    state = str(run.get("state"))
                    if state in {"JOB_STARTING", "NEXT_OPERATION_READY", "TOOL_REFERENCE_READY"}:
                        self._execute_next_operation(context, run)
                        continue
                    if state == "SPINDLE_STOP_CONFIRMED":
                        self._perform_tool_change_transition(context, run)
                        continue
                    if state == "TOOL_CHANGE_CONFIRMED":
                        self._measure_tool_reference(context, run)
                        continue
                    if state in {"OPERATION_RUNNING", "OPERATION_STARTING", "WAITING_FOR_KLIPPER", "PRINT_QUEUED"}:
                        self._watch_operation(context, run)
                        continue
                    return
            except Exception as error:
                current = self._load_run(context)
                if current is not None:
                    current["state"] = "JOB_ERROR"
                    current["completed_at"] = current.get("completed_at") or _iso_now()
                    current["updated_at"] = _iso_now()
                    current["available_actions"] = ["cancel"]
                    current["next_action"] = "Revisar error del supervisor"
                    current["last_watcher_error"] = traceback.format_exc()
                    self._append_event(current, "error", f"Fallo del supervisor: {error}")
                    self._save_run(context, current)
        finally:
            with self._lock:
                existing = self._threads.get(key)
                if existing is threading.current_thread():
                    self._threads.pop(key, None)

    def _execute_next_operation(self, context: JobContext, run: dict[str, Any]) -> None:
        index = self._next_pending_operation_index(run)
        if index is None:
            run["state"] = "JOB_COMPLETE"
            run["completed_at"] = _iso_now()
            run["summary"]["operations_completed"] = len(run["operations"])
            run["available_actions"] = []
            run["next_action"] = "Trabajo completo"
            self._append_event(run, "info", "Todas las operaciones del montaje terminaron.")
            self._save_run(context, run)
            self._archive_run(context, run)
            return
        operation = run["operations"][index]
        previous = run["operations"][index - 1] if index > 0 else None
        current_tool_key = run.get("current_tool_key")
        if previous and previous["tool_key"] != operation["tool_key"] and previous["execution_status"] == "COMPLETED" and current_tool_key != operation["tool_key"]:
            self._handle_tool_change_required(context, run, operation_index=index)
            return
        if operation["reference_status"] != "LISTA":
            run["state"] = "TOOL_CHANGE_CONFIRMED" if index > 0 else "JOB_VALIDATING"
            run["current_operation_index"] = index - 1 if index > 0 else 0
            run["current_operation_id"] = previous["operation_id"] if previous else operation["operation_id"]
            run["next_action"] = "Medir referencia de herramienta" if index > 0 else "Mida la referencia Z inicial"
            run["available_actions"] = ["measure-reference", "cancel"]
            self._append_event(run, "warning", f"La operación {operation['name']} requiere una referencia Z vigente antes de ejecutar.")
            self._save_run(context, run)
            return
        adapter = self.adapter_factory(self.runtime)
        plan = self._load_or_build_plan(context)
        generated = self._generated_payload_for_operation(plan, operation["operation_id"])
        if generated is None:
            raise ApplicationError(f"No existe archivo compensado para la operación {operation['name']}.")
        operation["execution_status"] = "PREFLIGHT"
        run["current_operation_index"] = index
        run["current_operation_id"] = operation["operation_id"]
        run["current_tool_key"] = operation["tool_key"]
        expected_remote_file = self._expected_remote_file(context, str(generated["relative_path"]))
        recovered = self._recover_active_print_if_possible(
            context,
            run,
            operation_index=index,
            expected_remote_file=expected_remote_file,
        )
        if recovered is not None:
            return
        run["state"] = "OPERATION_UPLOADING"
        run["next_action"] = f"Subiendo {operation['generated_file_name']} a Moonraker"
        run["available_actions"] = ["pause", "cancel"]
        self._append_event(run, "info", f"Preparando operación {operation['order_label']} — {operation['name']}.")
        self._save_run(context, run)
        upload = adapter.upload_file(
            local_path=self.repository.project_dir(context.project_id) / generated["relative_path"],
            project_id=context.project_id,
            setup_id=context.setup_id,
            face=context.face,
        )
        item = upload.get("item") if isinstance(upload, dict) else None
        remote_file = item.get("path") if isinstance(item, dict) else None
        if not isinstance(remote_file, str) or not remote_file.strip():
            operation["execution_status"] = "UPLOAD_FAILED"
            raise ApplicationError("Moonraker no devolvió item.path para el archivo cargado.")
        operation["remote_file"] = remote_file
        operation["generated_file"] = generated["relative_path"]
        operation["generated_metadata"] = generated.get("metadata_path")
        operation["execution_status"] = "WAITING_FOR_KLIPPER"
        operation["observed_printing"] = False
        operation["progress"] = 0.0
        run["recovery_state"] = None
        self._append_event(run, "info", f"Archivo subido a Moonraker: {remote_file}.")
        if upload.get("print_started"):
            run["state"] = "WAITING_FOR_KLIPPER"
            run["next_action"] = f"Esperando confirmación de Klipper para {operation['name']}"
            self._save_run(context, run)
            return
        if upload.get("print_queued"):
            operation["execution_status"] = "PRINT_QUEUED"
            run["state"] = "PRINT_QUEUED"
            run["next_action"] = f"Moonraker dejó {operation['name']} en cola; esperando impresión"
            self._save_run(context, run)
            return
        operation["execution_status"] = "START_NOT_ACCEPTED"
        run["state"] = "JOB_ERROR"
        self._save_run(context, run)

    def _watch_operation(self, context: JobContext, run: dict[str, Any]) -> None:
        adapter = self.adapter_factory(self.runtime)
        operation = run["operations"][int(run["current_operation_index"])]
        while True:
            current = self._load_run(context)
            if current is None:
                return
            if current.get("state") in RUN_TERMINAL_STATES | RUN_WAITING_STATES | {"JOB_PAUSED"}:
                return
            status = adapter.print_status()
            operation = current["operations"][int(current["current_operation_index"])]
            operation["progress"] = max(0.0, min(1.0, float(status.get("progress") or 0.0)))
            operation["machine_status"] = status
            operation["moonraker_filename"] = status.get("filename")
            operation["moonraker_state"] = status.get("state")
            state = str(status.get("state") or "").lower()
            observed_filename = str(status.get("filename") or "").replace("\\", "/").lstrip("/")
            expected_filename = str(operation.get("remote_file") or "").replace("\\", "/").lstrip("/")
            current["updated_at"] = _iso_now()
            if observed_filename == expected_filename and state == "printing":
                first_printing = not bool(operation.get("observed_printing"))
                operation["observed_printing"] = True
                operation["execution_status"] = "RUNNING"
                current["state"] = "OPERATION_RUNNING"
                current["next_action"] = f"Ejecutando {operation['name']}"
                if first_printing:
                    self._append_event(current, "info", f"Klipper confirmó la ejecución de {operation['name']}.")
            if state in {"paused"}:
                operation["execution_status"] = "PAUSED"
                current["state"] = "OPERATION_PAUSED"
                current["next_action"] = "Reanudar operación"
                current["available_actions"] = ["resume", "cancel"]
                self._append_event(current, "warning", f"Operación {operation['name']} pausada.")
                self._save_run(context, current)
                return
            if state in {"complete", "completed"} and observed_filename == expected_filename and operation.get("observed_printing") is True:
                operation["execution_status"] = "COMPLETED"
                operation["progress"] = 1.0
                operation["finished_at"] = _iso_now()
                operation["completed_at"] = operation["finished_at"]
                current["summary"]["operations_completed"] = sum(1 for item in current["operations"] if item["execution_status"] == "COMPLETED")
                current["state"] = "NEXT_OPERATION_READY"
                current["next_action"] = "Preparando siguiente operación"
                current["available_actions"] = ["pause", "cancel"]
                self._append_event(current, "info", f"Operación {operation['name']} completada.")
                self._save_run(context, current)
                return
            if state in {"cancelled", "canceling"}:
                operation["execution_status"] = "CANCELLED"
                current["state"] = "JOB_CANCELLED"
                current["completed_at"] = _iso_now()
                current["available_actions"] = []
                current["next_action"] = "Trabajo cancelado"
                self._append_event(current, "warning", f"Moonraker canceló la operación {operation['name']}.")
                self._save_run(context, current)
                self._archive_run(context, current)
                return
            if state in {"error"}:
                operation["execution_status"] = "ERROR"
                operation["error"] = str(status.get("message") or "Moonraker reportó error.")
                current["state"] = "JOB_ERROR"
                current["completed_at"] = _iso_now()
                current["available_actions"] = ["cancel"]
                current["next_action"] = "Revisar error de ejecución"
                self._append_event(current, "error", f"Error en {operation['name']}: {operation['error']}")
                self._save_run(context, current)
                self._archive_run(context, current)
                return
            self._save_run(context, current)
            time.sleep(0.5)

    def _retry_tool_change_transition(self, context: JobContext, run: dict[str, Any]) -> None:
        current_index = int(run.get("current_operation_index", 0) or 0)
        next_index = current_index + 1
        if next_index >= len(run.get("operations") or []):
            raise ApplicationError("No existe una siguiente operación para reintentar el cambio de herramienta.")
        self._handle_tool_change_required(context, run, operation_index=next_index, retry=True)

    def _validate_spindle_stop_confirmation(self, adapter: MoonrakerJobAdapter) -> None:
        status = adapter.print_status()
        if str(status.get("state") or "").lower() == "printing":
            raise ApplicationError("No se puede continuar mientras Moonraker sigue imprimiendo.")
        if bool(status.get("is_active", status.get("active"))):
            raise ApplicationError("virtual_sdcard.is_active debe ser false antes de continuar con el cambio de herramienta.")
        snapshot = self.runtime.snapshot()
        if str(snapshot.get("moonraker", {}).get("telemetry_state") or "") != "LIVE":
            raise ApplicationError("La telemetría Moonraker debe estar LIVE para confirmar el spindle detenido.")
        homed_axes = str(snapshot.get("klipper", {}).get("homed_axes") or "")
        if not set("xyz").issubset(set(homed_axes)):
            raise ApplicationError("Falta homing XYZ para continuar con el cambio de herramienta.")

    def _handle_tool_change_required(self, context: JobContext, run: dict[str, Any], *, operation_index: int, retry: bool = False) -> None:
        next_operation = run["operations"][operation_index]
        run["state"] = "SPINDLE_STOP_REQUIRED"
        run["next_action"] = "Apague manualmente el spindle antes de continuar."
        run["available_actions"] = ["confirm-spindle-stopped", "cancel"]
        run["updated_at"] = _iso_now()
        run["recovery_state"] = None
        if retry:
            run["last_watcher_error"] = None
            self._append_event(run, "warning", "Reintento de transición listo; apague manualmente el spindle antes de continuar.")
        else:
            self._append_event(run, "warning", f"Apague manualmente el spindle antes de continuar con {next_operation['name']}.")
        self._save_run(context, run)

    def _perform_tool_change_transition(self, context: JobContext, run: dict[str, Any]) -> None:
        adapter = self.adapter_factory(self.runtime)
        current_index = int(run.get("current_operation_index", 0) or 0)
        next_index = current_index + 1
        if next_index >= len(run.get("operations") or []):
            raise ApplicationError("No existe una siguiente operación para completar el cambio de herramienta.")
        next_operation = run["operations"][next_index]
        try:
            run["state"] = "RETRACTING"
            run["next_action"] = "Subiendo a Z segura para cambio de herramienta"
            run["available_actions"] = ["cancel"]
            self._append_event(run, "info", "Spindle detenido confirmado. Subiendo a Z segura para cambio de herramienta.")
            self._save_run(context, run)
            adapter.move_to_tool_change_position()
            run["last_watcher_error"] = None
            run["state"] = "TOOL_CHANGE_REQUIRED"
            run["next_action"] = "Cambie la herramienta y pulse Herramienta cambiada"
            run["available_actions"] = ["confirm-tool-change", "cancel"]
            self._append_event(run, "warning", f"Cambie a {next_operation['tool_name']} y confirme cuando esté instalada.")
            self._save_run(context, run)
        except Exception as error:
            run["state"] = "RECOVERY_REQUIRED"
            run["next_action"] = "Revise la transición y pulse Reintentar transición de herramienta"
            run["available_actions"] = ["retry-tool-change-transition", "cancel"]
            run["updated_at"] = _iso_now()
            run["last_watcher_error"] = traceback.format_exc()
            run["recovery_state"] = "RECOVERY_REQUIRED"
            self._append_event(run, "error", f"Fallo la transición segura de herramienta: {error}")
            self._save_run(context, run)

    def _build_run_checks(self, context: JobContext, plan: dict[str, Any]) -> list[dict[str, Any]]:
        snapshot = self.runtime.snapshot()
        checks: list[dict[str, Any]] = []

        def add(name: str, ok: bool, detail: str) -> None:
            checks.append({"name": name, "ok": ok, "detail": detail})

        add("modo_fisico", snapshot.get("mode") == "PHYSICAL", "MACHINE_MODE=physical requerido para ejecutar.")
        add("runtime_conectado", bool(snapshot.get("moonraker", {}).get("http_connected")), "Moonraker HTTP conectado.")
        add("websocket", bool(snapshot.get("moonraker", {}).get("websocket_connected")), "Telemetría WebSocket conectada.")
        add("klipper_ready", bool(snapshot.get("klipper", {}).get("ready")), "Klipper listo para ejecución.")
        homed_axes = str(snapshot.get("klipper", {}).get("homed_axes") or "")
        add("homing", set("xyz").issubset(set(homed_axes)), f"Homing actual: {homed_axes or 'pendiente'}.")
        add("mapa_activo", bool(plan.get("active_map")), "Mapa físico activo del montaje.")
        add("plan_generado", len(plan.get("operations", [])) > 0, "Plan multioperación generado.")
        blocked_operations = [item for item in plan["operations"] if item["blocking"]]
        add("operaciones_bloqueadas", not blocked_operations, "Todas las operaciones activas están compensables y cubiertas por el mapa.")
        missing_generated = [item for item in plan["operations"] if not item.get("generated_file") and not item["blocking"]]
        add("archivos_compensados", not missing_generated, "Cada operación activa tiene archivo compensado generado.")
        initial = plan["operations"][0] if plan["operations"] else None
        add(
            "referencia_inicial",
            bool(initial and initial.get("reference_status") == "LISTA"),
            "La herramienta inicial tiene una referencia Z vigente." if initial and initial.get("reference_status") == "LISTA" else "Falta referencia Z de la herramienta inicial.",
        )
        return checks

    def _build_plan(self, context: JobContext, generated_results: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
        project = self._load_project(context.project_id)
        setup = project.get_setup(context.setup_id)
        operations = sorted(
            [item for item in project.operations_for_setup(context.setup_id) if str(item.cara) == str(context.face)],
            key=lambda item: item.orden,
        )
        if not operations:
            raise ApplicationError("No existen operaciones activas para este montaje/cara.")
        active_map = self._load_active_map(context.project_id, operations[0].id)
        coverage_by_operation = self._coverage_by_operation(active_map, operations)
        generated_by_operation = self._latest_generated_by_operation(context.project_id, operations, active_map)
        if generated_results:
            generated_by_operation.update({key: value for key, value in generated_results.items() if "relative_path" in value})
        operation_rows: list[dict[str, Any]] = []
        previous_tool_key: str | None = None
        tool_change_count = 0
        distinct_tools: list[str] = []
        initial_reference_binding = self._initial_reference_binding(active_map, setup, operations)
        for index, operation in enumerate(operations):
            tool_key = _tool_key(operation)
            if tool_key not in distinct_tools:
                distinct_tools.append(tool_key)
            tool_changed = previous_tool_key is not None and previous_tool_key != tool_key
            if tool_changed:
                tool_change_count += 1
            binding = initial_reference_binding
            previous_tool_key = tool_key
            generated = generated_by_operation.get(operation.id)
            coverage = coverage_by_operation.get(operation.id)
            reference_status = self._reference_status(active_map, operation, binding)
            calibration = self._tool_installation_calibration(active_map, operation, binding)
            blocking_reasons: list[str] = []
            if operation.archivo_gcode is None:
                blocking_reasons.append("Falta G-code original.")
            if operation.analisis is None:
                blocking_reasons.append("Falta análisis G-code.")
            if active_map is None:
                blocking_reasons.append("Falta mapa físico activo.")
            if coverage is not None and not coverage["sufficient"]:
                first = coverage["issues"][0] if coverage["issues"] else None
                blocking_reasons.append(
                    "Mapa insuficiente."
                    + (
                        f" Primer punto fuera: línea/segmento {first['segment_index']}, X={first['x_mm']:.3f}, Y={first['y_mm']:.3f}, distancia={first['distance_mm']:.3f} mm."
                        if first is not None
                        else ""
                    )
                )
            operation_rows.append(
                {
                    "operation_id": operation.id,
                    "order": operation.orden,
                    "order_label": f"{index + 1:03d}",
                    "name": operation.nombre,
                    "type": str(operation.tipo),
                    "tool_id": operation.tool_id,
                    "tool_name": operation.herramienta or operation.tool_id or "sin herramienta",
                    "tool_key": tool_key,
                    "tool_changed": tool_changed,
                    "map_status": "LISTO" if active_map is not None else "PENDIENTE",
                    "coverage_status": "VALIDA" if coverage is None or coverage["sufficient"] else "FUERA_DE_DOMINIO",
                    "coverage_detail": None if coverage is None or coverage["sufficient"] else blocking_reasons[-1],
                    "reference_status": reference_status,
                    "tool_installation_calibration": calibration,
                    "generated_file": None if generated is None else generated["relative_path"],
                    "generated_file_name": None if generated is None else Path(str(generated["relative_path"])).name,
                    "generated_metadata_path": None if generated is None else generated.get("metadata_path"),
                    "compensation_status": "COMPENSADO" if generated is not None else "PENDIENTE",
                    "preflight_status": "PENDIENTE",
                    "execution_status": "PENDING",
                    "blocking": bool(blocking_reasons),
                    "blocking_reasons": blocking_reasons,
                    "coverage": coverage,
                    "original_gcode": operation.archivo_gcode,
                }
            )
        plan = {
            "schema_version": JOB_PLAN_SCHEMA,
            "plan_id": f"job-plan/{context.setup_id}/{_safe_face(context.face)}",
            "project_id": context.project_id,
            "setup_id": context.setup_id,
            "face": context.face,
            "placement_revision": setup.placement_revision,
            "active_map_id": None if active_map is None else active_map.get("map_id"),
            "active_map": active_map,
            "operations": operation_rows,
            "summary": {
                "operations_total": len(operation_rows),
                "operations_ready": sum(1 for item in operation_rows if not item["blocking"]),
                "generated_files": sum(1 for item in operation_rows if item.get("generated_file")),
                "tool_changes": tool_change_count,
                "distinct_tools": len(distinct_tools),
                "blocked_operations": sum(1 for item in operation_rows if item["blocking"]),
            },
            "manifest_path": self._existing_manifest_path(context),
            "created_at": _iso_now(),
            "updated_at": _iso_now(),
        }
        return plan

    def _build_manifest(self, plan: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": "job-manifest-v1",
            "plan_id": plan["plan_id"],
            "project_id": plan["project_id"],
            "setup_id": plan["setup_id"],
            "face": plan["face"],
            "placement_revision": plan["placement_revision"],
            "active_map_id": plan["active_map_id"],
            "generated_at": _iso_now(),
            "operations": [
                {
                    "order": item["order"],
                    "label": item["order_label"],
                    "operation_id": item["operation_id"],
                    "name": item["name"],
                    "tool_id": item["tool_id"],
                    "tool_name": item["tool_name"],
                    "file": item["generated_file"],
                    "metadata_path": item["generated_metadata_path"],
                    "coverage_status": item["coverage_status"],
                    "reference_status": item["reference_status"],
                    "requires_tool_change": item["tool_changed"],
                    "blocking": item["blocking"],
                }
                for item in plan["operations"]
            ],
        }

    def _base_run(self, context: JobContext, plan: dict[str, Any]) -> dict[str, Any]:
        run_id = f"job-run/{context.setup_id}/{_safe_face(context.face)}/{_utc_now().strftime('%Y%m%d-%H%M%S')}"
        return {
            "schema_version": JOB_RUN_SCHEMA,
            "run_id": run_id,
            "plan_id": plan["plan_id"],
            "project_id": context.project_id,
            "setup_id": context.setup_id,
            "face": context.face,
            "placement_revision": plan["placement_revision"],
            "active_map_id": plan["active_map_id"],
            "state": "JOB_DRAFT",
            "ready": False,
            "checks": [],
            "started_at": None,
            "completed_at": None,
            "updated_at": _iso_now(),
            "current_operation_index": 0,
            "current_operation_id": None,
            "current_tool_key": None,
            "next_action": "Preparar trabajo",
            "available_actions": ["start"],
            "operations": [
                {
                    "operation_id": item["operation_id"],
                    "order": item["order"],
                    "order_label": item["order_label"],
                    "name": item["name"],
                    "type": item["type"],
                    "tool_id": item["tool_id"],
                    "tool_name": item["tool_name"],
                    "tool_key": item["tool_key"],
                    "tool_changed": item["tool_changed"],
                    "reference_status": item["reference_status"],
                    "generated_file": item["generated_file"],
                    "generated_file_name": item["generated_file_name"],
                    "execution_status": "PENDING",
                    "started_at": None,
                    "completed_at": None,
                    "error": None,
                    "progress": 0.0,
                    "installation_revision": None if not item.get("tool_installation_calibration") else item["tool_installation_calibration"].get("installation_id"),
                }
                for item in plan["operations"]
            ],
            "summary": {
                "operations_total": plan["summary"]["operations_total"],
                "operations_completed": 0,
                "tool_changes_required": plan["summary"]["tool_changes"],
                "tool_changes_completed": 0,
            },
            "timeline": [
                {
                    "kind": "operation",
                    "operation_id": item["operation_id"],
                    "name": item["name"],
                    "tool_name": item["tool_name"],
                    "state": "PENDING",
                    "requires_tool_change": item["tool_changed"],
                }
                for item in plan["operations"]
            ],
            "events": [],
            "manifest_path": plan.get("manifest_path"),
            "last_watcher_error": None,
            "recovery_state": None,
        }

    def _coverage_by_operation(self, active_map: dict[str, Any] | None, operations: list[OperacionPCB]) -> dict[str, dict[str, Any]]:
        if active_map is None:
            return {}
        height_map = self.physical_map_service.height_map_from_payload(active_map["height_map"])
        result: dict[str, dict[str, Any]] = {}
        for operation in operations:
            if operation.analisis is None:
                continue
            coverage = build_coverage_report(
                height_map=height_map,
                operations=((operation.id, operation.nombre, operation.analisis),),
                tolerance_mm=DOMAIN_TOLERANCE_MM,
            )
            result[operation.id] = {
                "sufficient": coverage.sufficient,
                "points_inside": coverage.points_inside,
                "points_outside": coverage.points_outside,
                "issues": [issue.__dict__ for issue in coverage.issues],
            }
        return result

    def _reference_status(self, active_map: dict[str, Any] | None, operation: OperacionPCB, initial_reference_binding: dict[str, Any] | None = None) -> str:
        reference = self._reference_entry(active_map, operation, initial_reference_binding)
        return "LISTA" if isinstance(reference, dict) and reference.get("valid") else "REQUIERE_REFERENCIA"

    def _tool_installation_calibration(self, active_map: dict[str, Any] | None, operation: OperacionPCB, initial_reference_binding: dict[str, Any] | None = None) -> dict[str, Any] | None:
        reference = self._reference_entry(active_map, operation, initial_reference_binding)
        if not isinstance(reference, dict):
            return None
        return {
            "calibration_id": str(reference.get("calibration_id") or reference.get("installation_id") or ""),
            "tool_id": _tool_key(operation),
            "installation_id": str(reference.get("installation_id") or reference.get("installation_session_id") or ""),
            "installation_session_id": str(reference.get("installation_session_id") or reference.get("installation_id") or ""),
            "reference_point_id": str(reference.get("reference_point_id") or "surface-map-origin"),
            "reference_machine_x": reference.get("reference_x"),
            "reference_machine_y": reference.get("reference_y"),
            "tool_reference_z": reference.get("tool_reference_z", reference.get("reference_z")),
            "measured_at": reference.get("measured_at"),
            "probe_method": reference.get("probe_method", reference.get("source", "MEASURED")),
            "valid": bool(reference.get("valid")),
            "invalidation_reason": reference.get("invalidation_reason"),
        }

    def _reference_entry(self, active_map: dict[str, Any] | None, operation: OperacionPCB, initial_reference_binding: dict[str, Any] | None = None) -> dict[str, Any] | None:
        if active_map is None:
            return None
        operation_tool_key = _tool_key(operation)
        references = active_map.get("tool_references") or {}
        reference = references.get(operation_tool_key)
        if initial_reference_binding is not None:
            source_tool_key = initial_reference_binding.get("source_tool_key")
            rebound_tool_key = initial_reference_binding.get("tool_key")
            if operation_tool_key == rebound_tool_key:
                fallback = initial_reference_binding.get("reference")
                if isinstance(fallback, dict) and fallback.get("valid"):
                    return fallback
            elif operation_tool_key == source_tool_key:
                return None
        if isinstance(reference, dict) and reference.get("valid"):
            return reference
        return reference if isinstance(reference, dict) else None

    def _initial_reference_binding(self, active_map: dict[str, Any] | None, setup: Any, operations: list[OperacionPCB]) -> dict[str, Any] | None:
        if active_map is None or not operations:
            return None
        first_tool_key = _tool_key(operations[0])
        references = active_map.get("tool_references") or {}
        direct = references.get(first_tool_key)
        if isinstance(direct, dict) and direct.get("valid"):
            return None
        valid_references = [reference for reference in references.values() if isinstance(reference, dict) and reference.get("valid")]
        if len(valid_references) != 1:
            return None
        active_reference_id = str(getattr(setup, "active_reference_id", "") or "")
        reference = valid_references[0]
        if active_reference_id:
            reference_ids = {
                str(reference.get("installation_id") or ""),
                str(reference.get("calibration_id") or ""),
                str(reference.get("installation_session_id") or ""),
            }
            if active_reference_id not in reference_ids:
                return None
        return {
            "tool_key": first_tool_key,
            "reference": reference,
            "source_tool_key": next((key for key, value in references.items() if value is reference), None),
            "source_tool_id": reference.get("tool_id"),
            "source_tool_name": reference.get("tool_name"),
        }

    def _generated_payload_for_operation(self, plan: dict[str, Any], operation_id: str) -> dict[str, Any] | None:
        row = next((item for item in plan["operations"] if item["operation_id"] == operation_id), None)
        if row is None or not row.get("generated_file"):
            return None
        return {
            "relative_path": row["generated_file"],
            "metadata_path": row.get("generated_metadata_path"),
        }

    def _latest_generated_by_operation(
        self,
        project_id: str,
        operations: list[OperacionPCB],
        active_map: dict[str, Any] | None,
    ) -> dict[str, dict[str, Any]]:
        """Return only artifacts bound to the current source, map and tool Z.

        A generated file is an execution artifact, not a cache. Selecting by
        mtime alone could execute a toolpath built before a board/map/reference
        change, so every candidate is verified against immutable metadata.
        """
        generated_dir = self.repository.project_dir(project_id) / "generated" / "compensated"
        if not generated_dir.exists():
            return {}
        results: dict[str, dict[str, Any]] = {}
        for operation in operations:
            candidates = sorted(
                generated_dir.glob(f"{operation.id}_*_compensated.gcode"),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
            for file_path in candidates:
                metadata_path = file_path.with_suffix(".json")
                if not metadata_path.exists():
                    continue
                try:
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not self._generated_artifact_is_current(project_id, operation, active_map, file_path, metadata):
                    continue
                results[operation.id] = {
                    "relative_path": self._relative_to_project(project_id, file_path),
                    "metadata_path": self._relative_to_project(project_id, metadata_path),
                    "plan_hash": metadata.get("generated_hash"),
                }
                break
        return results

    def _generated_artifact_is_current(
        self,
        project_id: str,
        operation: OperacionPCB,
        active_map: dict[str, Any] | None,
        file_path: Path,
        metadata: dict[str, Any],
    ) -> bool:
        if active_map is None or metadata.get("operation_id") != operation.id:
            return False
        if metadata.get("map_id") != active_map.get("map_id"):
            return False
        active_map_hash = hashlib.sha256(json.dumps(active_map, sort_keys=True).encode("utf-8")).hexdigest()
        if metadata.get("map_hash") != active_map_hash:
            return False
        try:
            original = self.repository.read_project_file(project_id, operation.archivo_gcode or "")
        except Exception:
            return False
        if metadata.get("original_hash") != hashlib.sha256(original.encode("utf-8")).hexdigest():
            return False
        if metadata.get("generated_hash") != hashlib.sha256(file_path.read_bytes()).hexdigest():
            return False
        if metadata.get("tool_id") != _tool_key(operation):
            return False
        reference = (active_map.get("tool_references") or {}).get(_tool_key(operation))
        if isinstance(reference, dict) and reference.get("valid"):
            actual_z = (metadata.get("reference_frame") or {}).get("surface_reference_z_mm")
            if actual_z is None or abs(float(actual_z) - float(reference["reference_z"])) > 1e-9:
                return False
        return True

    def _load_active_map(self, project_id: str, operation_id: str) -> dict[str, Any] | None:
        try:
            return self.physical_map_service.get_active(project_id, operation_id)
        except Exception:
            return None

    def _next_pending_operation_index(self, run: dict[str, Any]) -> int | None:
        for index, item in enumerate(run["operations"]):
            if item["execution_status"] not in {"COMPLETED", "CANCELLED"}:
                return index
        return None

    def _expected_remote_file(self, context: JobContext, generated_relative_path: str) -> str:
        return f"klipper-cnc-assistant/{context.project_id}/{context.setup_id}/{_safe_face(context.face)}/{Path(str(generated_relative_path)).name}"

    def _recover_active_print_if_possible(
        self,
        context: JobContext,
        run: dict[str, Any],
        *,
        operation_index: int | None = None,
        expected_remote_file: str | None = None,
    ) -> dict[str, Any] | None:
        try:
            status = self.adapter_factory(self.runtime).print_status()
        except Exception:
            return None
        if str(status.get("state") or "").lower() != "printing":
            return None
        observed_filename = self._normalize_filename(status.get("filename"))
        operations = list(run.get("operations") or [])
        candidate_indexes: list[int] = []
        if operation_index is not None:
            candidate_indexes.append(operation_index)
        current_index = run.get("current_operation_index")
        if isinstance(current_index, int):
            candidate_indexes.append(current_index)
        pending_index = self._next_pending_operation_index(run)
        if pending_index is not None:
            candidate_indexes.append(pending_index)
        seen_indexes: set[int] = set()
        for candidate_index in candidate_indexes:
            if candidate_index in seen_indexes or not (0 <= candidate_index < len(operations)):
                continue
            seen_indexes.add(candidate_index)
            operation = operations[candidate_index]
            candidate_remote = expected_remote_file or operation.get("remote_file")
            if not candidate_remote and operation.get("generated_file"):
                candidate_remote = self._expected_remote_file(context, str(operation["generated_file"]))
            normalized_candidate = self._normalize_filename(candidate_remote)
            if not normalized_candidate or normalized_candidate != observed_filename:
                continue
            operation["remote_file"] = candidate_remote
            operation["execution_status"] = "RUNNING"
            operation["observed_printing"] = True
            operation["progress"] = self._clamp_progress(status.get("progress"))
            operation["moonraker_filename"] = status.get("filename")
            operation["moonraker_state"] = status.get("state")
            operation["machine_status"] = status
            operation["started_at"] = operation.get("started_at") or _iso_now()
            run["current_operation_index"] = candidate_index
            run["current_operation_id"] = operation["operation_id"]
            run["current_tool_key"] = operation["tool_key"]
            run["state"] = "OPERATION_RUNNING"
            run["next_action"] = f"RECOVERED_ACTIVE_PRINT · Ejecutando {operation['name']}"
            run["available_actions"] = ["pause", "cancel"]
            run["updated_at"] = _iso_now()
            run["recovery_state"] = "RECOVERED_ACTIVE_PRINT"
            self._append_event(run, "warning", f"RECOVER_ACTIVE_PRINT: se recupero la impresion activa de {operation['name']} sin re-subir el archivo.")
            self._save_run(context, run)
            return run
        if str(run.get("state")) == "JOB_ERROR":
            run["state"] = "RECOVERY_REQUIRED"
            run["next_action"] = "Revision manual requerida: Moonraker imprime un archivo que no coincide con este JobRun"
            run["available_actions"] = ["cancel"]
            run["updated_at"] = _iso_now()
            run["recovery_state"] = "RECOVERY_REQUIRED"
            self._save_run(context, run)
        return None

    def _append_event(self, run: dict[str, Any], level: str, message: str) -> None:
        timestamp = _iso_now()
        run.setdefault("events", []).append({"event_id": hashlib.sha256(f"{run.get('run_id')}:{timestamp}:{message}".encode()).hexdigest()[:16], "run_id": run.get("run_id"), "operation_id": run.get("current_operation_id"), "timestamp": timestamp, "level": level, "stage": run.get("state"), "message": message})
        run["events"] = self._dedupe_events(run["events"])[-300:]

    def _context(self, project_id: str, setup_id: str, face: str) -> JobContext:
        normalized_face = BoardFace(face).value if face in {BoardFace.SUPERIOR.value, BoardFace.INFERIOR.value} else str(face)
        return JobContext(project_id=project_id, setup_id=setup_id, face=normalized_face)

    def _load_or_build_plan(self, context: JobContext) -> dict[str, Any]:
        plan = self._load_plan(context)
        if plan is not None:
            refreshed = self._build_plan(context)
            self._write_manifest(context, refreshed)
            self._save_plan(context, refreshed)
            return refreshed
        return self.get_plan(project_id=context.project_id, setup_id=context.setup_id, face=context.face)

    def _project_dir(self, project_id: str) -> Path:
        return self.repository.project_dir(project_id)

    def _plan_dir(self, context: JobContext) -> Path:
        target = self._project_dir(context.project_id) / "reports" / "jobs" / context.setup_id / _safe_face(context.face)
        target.mkdir(parents=True, exist_ok=True)
        return target

    def _history_dir(self, context: JobContext) -> Path:
        target = self._plan_dir(context) / "history"
        target.mkdir(parents=True, exist_ok=True)
        return target

    def _existing_manifest_path(self, context: JobContext) -> str | None:
        path = self._plan_dir(context) / "job_manifest.json"
        return self._relative_to_project(context.project_id, path) if path.exists() else None

    def _plan_file(self, context: JobContext) -> Path:
        return self._plan_dir(context) / "job_plan.json"

    def _run_file(self, context: JobContext) -> Path:
        return self._plan_dir(context) / "current_run.json"

    def _load_plan(self, context: JobContext) -> dict[str, Any] | None:
        path = self._plan_file(context)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _save_plan(self, context: JobContext, plan: dict[str, Any]) -> None:
        self._plan_file(context).write_text(json.dumps(plan, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")

    def _write_manifest(self, context: JobContext, plan: dict[str, Any]) -> None:
        manifest = self._build_manifest(plan)
        manifest_path = self._plan_dir(context) / "job_manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
        plan["manifest_path"] = self._relative_to_project(context.project_id, manifest_path)

    def _load_run(self, context: JobContext) -> dict[str, Any] | None:
        path = self._run_file(context)
        if not path.exists():
            return None
        with self._lock:
            return json.loads(path.read_text(encoding="utf-8"))

    def _is_stale_run(self, run: dict[str, Any], state: str | None = None) -> bool:
        run_state = str(state or run.get("status") or run.get("state") or "")
        if run_state not in RUN_MARKED_ACTIVE_STATES:
            return False
        if bool(run.get("worker_alive")) or bool(run.get("watcher_alive")) or bool(run.get("supervisor_registered")):
            return False
        updated_at = _parse_iso_datetime(run.get("updated_at"))
        if updated_at is None:
            return False
        return (_utc_now() - updated_at).total_seconds() >= STALE_RUN_IDLE_SECONDS

    def _dedupe_events(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        unique: list[dict[str, Any]] = []
        seen: set[str] = set()
        for event in events:
            key = str(event.get("event_id") or f"{event.get('timestamp')}:{event.get('stage')}:{event.get('message')}")
            if key in seen:
                continue
            seen.add(key)
            unique.append(event)
        return unique

    def _normalize_filename(self, value: Any) -> str:
        return str(value or "").replace("\\", "/").lstrip("/")

    def _clamp_progress(self, value: Any) -> float:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, numeric))

    def _save_run(self, context: JobContext, run: dict[str, Any]) -> None:
        path = self._run_file(context)
        payload = json.dumps(run, ensure_ascii=True, indent=2, sort_keys=True)
        with self._lock:
            tmp = path.with_suffix('.tmp')
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(path)

    def _archive_run(self, context: JobContext, run: dict[str, Any]) -> None:
        archived = dict(run)
        self._write_archived_run(context, archived)
        self._save_run(context, run)

    def _write_archived_run(self, context: JobContext, run: dict[str, Any], *, suffix: str | None = None) -> Path:
        stamp = _utc_now().strftime("%Y%m%d-%H%M%S")
        base = str(run["run_id"]).replace("/", "_")
        filename = f"{base}__{suffix}_{stamp}.json" if suffix else f"{base}__{stamp}.json"
        history_file = self._history_dir(context) / filename
        history_file.write_text(json.dumps(run, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
        return history_file

    def _supervisor_thread(self, context: JobContext) -> threading.Thread | None:
        return self._threads.get((context.project_id, context.setup_id, context.face))

    def _status_fallback(self, error: Exception) -> dict[str, Any]:
        return {
            "connected": False,
            "klipper_ready": False,
            "klipper_state": None,
            "state": None,
            "filename": None,
            "message": str(error),
            "progress": 0.0,
            "file_position": None,
            "file_size": None,
            "file_path": None,
            "print_duration": None,
            "active": False,
            "is_active": False,
            "updated_at": _iso_now(),
        }

    def _diagnose_run(self, context: JobContext, run: dict[str, Any]) -> dict[str, Any]:
        thread = self._supervisor_thread(context)
        worker_alive = bool(thread and thread.is_alive())
        try:
            status = self.adapter_factory(self.runtime).print_status()
        except Exception as error:
            status = self._status_fallback(error)
        current_operation = self._diagnostic_operation(run)
        return {
            "moonraker": {
                "connected": bool(status.get("connected", True)),
                "webhooks_state": status.get("klipper_state"),
                "klipper_state": status.get("klipper_state"),
                "print_state": status.get("state"),
                "filename": status.get("filename"),
                "progress": self._clamp_progress(status.get("progress")),
                "is_active": bool(status.get("is_active", status.get("active"))),
                "file_position": status.get("file_position"),
                "file_size": status.get("file_size"),
                "print_duration": status.get("print_duration"),
                "message": status.get("message"),
                "updated_at": status.get("updated_at") or _iso_now(),
            },
            "run": {
                "run_id": run.get("run_id"),
                "project_id": context.project_id,
                "setup": context.setup_id,
                "side": context.face,
                "placement_revision": run.get("placement_revision"),
                "status": run.get("state") or "JOB_DRAFT",
                "current_operation": current_operation,
                "remote_file": current_operation.get("remote_file"),
                "worker_alive": worker_alive,
                "watcher_alive": worker_alive,
                "supervisor_registered": thread is not None,
                "movement_lock": self._movement_lock_state(),
                "job_lock": self._run_file(context).exists(),
                "updated_at": run.get("updated_at"),
                "last_error": run.get("last_watcher_error"),
                "available_actions": list(run.get("available_actions") or []),
            },
        }

    def _diagnostic_operation(self, run: dict[str, Any]) -> dict[str, Any]:
        operations = list(run.get("operations") or [])
        if not operations:
            return {"operation_id": None, "name": None, "execution_status": None, "remote_file": None}
        index = int(run.get("current_operation_index", 0) or 0)
        index = max(0, min(index, len(operations) - 1))
        operation = operations[index]
        return {
            "operation_id": operation.get("operation_id"),
            "name": operation.get("name"),
            "execution_status": operation.get("execution_status"),
            "remote_file": operation.get("remote_file"),
        }

    def _movement_lock_state(self) -> bool | None:
        lock = getattr(self.runtime, "_movement_lock", None)
        if lock is None or not hasattr(lock, "locked"):
            return None
        try:
            return bool(lock.locked())
        except Exception:
            return None

    def _moonraker_is_idle(self, status: dict[str, Any]) -> bool:
        state = str(status.get("print_state") or "").lower()
        return state not in {"printing", "paused"} and not bool(status.get("is_active"))

    def _available_recovery_actions(self, diagnosis: dict[str, Any], run: dict[str, Any]) -> list[str]:
        actions = ["open"]
        if diagnosis["moonraker"]["print_state"] == "printing" or diagnosis["moonraker"]["is_active"]:
            actions.append("recover")
        if self._moonraker_is_idle(diagnosis["moonraker"]) and self._is_stale_run(diagnosis["run"], str(run.get("state") or "")):
            actions.append("archive-stale")
        return actions

    def _release_stale_supervisor(self, context: JobContext, run: dict[str, Any], diagnosis: dict[str, Any]) -> list[str]:
        released: list[str] = []
        key = (context.project_id, context.setup_id, context.face)
        thread = self._supervisor_thread(context)
        if thread is not None and not thread.is_alive():
            with self._lock:
                if self._threads.get(key) is thread:
                    self._threads.pop(key, None)
            released.extend(["job_supervisor.registry", "job_supervisor.dead_worker"])
            return released
        if thread is None:
            return released
        run["state"] = "RECOVERY_REQUIRED"
        run["recovery_state"] = "RECOVERY_REQUIRED"
        run["next_action"] = "Supervisor detenido por recuperación"
        run["available_actions"] = []
        run["updated_at"] = _iso_now()
        self._append_event(run, "warning", "Se detuvo el supervisor interno porque Moonraker no tenía impresión física activa.")
        self._save_run(context, run)
        thread.join(timeout=2.0)
        if thread.is_alive():
            raise ApplicationError("El supervisor interno sigue activo; no es seguro archivar todavía esta ejecución.")
        with self._lock:
            if self._threads.get(key) is thread:
                self._threads.pop(key, None)
        released.extend(["job_supervisor.registry", "job_supervisor.ghost_worker"])
        if diagnosis["run"].get("movement_lock") is False:
            released.append("machine_runtime.movement_lock_clear")
        return released

    def _relative_to_project(self, project_id: str, path: Path) -> str:
        return path.relative_to(self._project_dir(project_id)).as_posix()

    def _load_project(self, project_id: str):
        try:
            return self.repository.load_project(project_id)
        except FileNotFoundError as error:
            raise NotFoundError(str(error)) from error
        except ProjectValidationError as error:
            raise ApplicationError(str(error)) from error
