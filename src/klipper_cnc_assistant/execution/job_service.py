from __future__ import annotations

import json
import hashlib
import math
import threading
import time
import traceback
from copy import deepcopy
from collections import OrderedDict
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from klipper_cnc_assistant.application.compensated_gcode_service import CompensatedGCodeService
from klipper_cnc_assistant.application.errors import ApplicationError, NotFoundError
from klipper_cnc_assistant.application.physical_map_service import PhysicalMapService
from klipper_cnc_assistant.application.reference_service import ReferenceSessionService
from klipper_cnc_assistant.application.time_estimation_service import TimeEstimationService
from klipper_cnc_assistant.domain import BoardFace, OperacionPCB, ProjectValidationError
from klipper_cnc_assistant.heightmap.coverage import DOMAIN_TOLERANCE_MM, build_coverage_report
from klipper_cnc_assistant.moonraker.client import MoonrakerClient, MoonrakerError
from klipper_cnc_assistant.storage import JsonProjectRepository
from klipper_cnc_assistant.machine.physical_ownership import OwnerKind, OwnershipError
from klipper_cnc_assistant.machine.motion_authorization import MotionRequirements
from klipper_cnc_assistant.machine.physical_reference import PhysicalReferenceError, reference_context, require_current_reference
from klipper_cnc_assistant.storage.job_run_store import JobRunStore, JobRunConflict
from klipper_cnc_assistant.storage.safe_persistence import (
    FileRevisionGuard, atomic_json, atomic_write, storage_lock, patch_metadata,
    validate_generation, read_artifact_metadata, PersistenceConflict,
)
from .administrative_guard import administrative_mutation, execution_write
from .print_identity import PrintIdentity, PrintIdentityError
from .health import execution_health, preparation_retry_safe
from .readiness import PREPARABLE_STATES, ownership_compatible, evaluate_readiness, reference_candidate


@dataclass
class DispatchEvidence:
    """Explicit adapter evidence; unknown adapters remain conservative."""
    observed: bool = False
    transport_invoked: bool = False

    @property
    def failure_status(self):
        return 'not_sent' if self.observed and not self.transport_invoked else 'uncertain'


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
    "MOVING_TO_REFERENCE",
    "CALIBRATING_TOOL",
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


def _safe_operator_error(error: Exception) -> str:
    message = " ".join(str(error).strip().split())
    if not message or "traceback (most recent call last)" in message.lower():
        return "La transición segura no pudo completarse. Revise el estado de la máquina antes de reintentar."
    return message[:300]


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
        self.permit = None

    def runtime_snapshot(self) -> dict[str, Any]:
        return self.runtime.snapshot()

    def _client(self) -> MoonrakerClient:
        self.runtime.require_physical_access()
        client = self.runtime._client
        if client is None:
            raise ApplicationError('Runtime físico desconectado.')
        return client

    def _dispatch(self, action, send, *, before_send):
        evidence = getattr(before_send, 'dispatch_evidence', None)
        if evidence is not None:
            evidence.observed = True
        # Expensive artifact/reference IO precedes the fresh motion frame.
        # The callback still performs cheap CAS/session checks at dispatch.
        prepare = getattr(before_send, 'prepare', None)
        if prepare is not None:
            prepare()
        verify = getattr(before_send, 'verify', None)
        if verify is not None:
            verify()
        self.runtime._refresh_machine()
        authorizer = self.runtime._motion_authorizer()
        context = authorizer.require_context(self.permit, action, MotionRequirements())
        def authorized_send():
            before_send()
            authorizer.revalidate(context)
            if evidence is not None:
                evidence.transport_invoked = True
            return send()
        return authorizer.dispatch(context, authorized_send)

    def upload_file(self, *, local_path: Path, project_id: str, setup_id: str, face: str) -> dict[str, Any]:
        remote_dir = f"klipper-cnc-assistant/{project_id}/{setup_id}/{_safe_face(face)}"
        checksum = hashlib.sha256(local_path.read_bytes()).hexdigest()
        return self._client().upload_file(local_path=local_path, remote_dir=remote_dir, checksum=checksum, print_file=False)

    def start_file(self, remote_path: str, *, before_send) -> dict[str, Any]:
        transport = self._client().start_print
        return self._dispatch("job_start", lambda: transport(remote_path), before_send=before_send)

    def pause(self, *, before_send) -> dict[str, Any]:
        before_send()
        return self._client().pause_print()

    def resume(self, *, before_send) -> dict[str, Any]:
        return self._dispatch("job_resume", lambda: self._client().resume_print(), before_send=before_send)

    def cancel(self, *, before_send) -> dict[str, Any]:
        before_send()
        return self._client().cancel_print()

    def print_status(self) -> dict[str, Any]:
        status = self._client().query_objects(
            {
                "webhooks": ["state", "state_message"],
                "print_stats": ["state", "filename", "message", "print_duration", "total_duration"],
                "virtual_sdcard": ["progress", "file_position", "file_size", "file_path", "is_active"],
                "toolhead": ["position", "homed_axes"],
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
            "total_duration": print_stats.get("total_duration"),
            "active": bool(virtual_sdcard.get("is_active")),
            "is_active": bool(virtual_sdcard.get("is_active")),
            "is_active_observed": virtual_sdcard.get("is_active"),
            "homed_axes_observed": toolhead.get("homed_axes"),
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

    def move_to_tool_change_position(self, *, tool_change_profile: str = "standard") -> dict[str, Any]:
        return self.runtime.move_to_tool_change_position(tool_change_profile=tool_change_profile, permit=self.permit)

    def move_to_reference_point(
        self,
        *,
        x_mm: float,
        y_mm: float,
        tool_reference_profile: str = "standard",
    ) -> dict[str, Any]:
        return self.runtime.go_to_reference_point(
            permit=self.permit,
            reference_x=x_mm,
            reference_y=y_mm,
            tool_reference_profile=tool_reference_profile,
        )

    def move_from_tool_change_to_reference_point(
        self,
        *,
        x_mm: float,
        y_mm: float,
        tool_change_profile: str = "standard",
        progress_callback: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        return self.runtime.move_from_tool_change_to_reference_point(
            permit=self.permit,
            reference_x=x_mm,
            reference_y=y_mm,
            tool_change_profile=tool_change_profile,
            progress_callback=progress_callback,
        )

    def probe_tool_reference(self, *, x_mm: float, y_mm: float, probe_config: dict[str, Any] | None) -> dict[str, Any]:
        point = {
            "index": 0,
            "role": "REFERENCE",
            "x_machine": x_mm,
            "y_machine": y_mm,
        }
        return self.runtime.probe_mesh_point(point, probe_config=probe_config, permit=self.permit)


class JobService:
    def __init__(
        self,
        repository: JsonProjectRepository,
        physical_map_service: PhysicalMapService,
        reference_service: ReferenceSessionService,
        compensated_gcode_service: CompensatedGCodeService,
        runtime: Any,
        *,
        time_estimation_service: TimeEstimationService | None = None,
        adapter_factory: Callable[[Any], MoonrakerJobAdapter] = MoonrakerJobAdapter,
        mesh_execution_service: Any | None = None,
    ) -> None:
        self.repository = repository
        self.physical_map_service = physical_map_service
        self.reference_service = reference_service
        self.compensated_gcode_service = compensated_gcode_service
        self.runtime = runtime
        self.compensated_gcode_service.machine_runtime = runtime
        self.time_estimation_service = time_estimation_service
        self.adapter_factory = adapter_factory
        self.mesh_execution_service = mesh_execution_service
        self._lock = threading.RLock()
        self._threads: dict[tuple[str, str, str], threading.Thread] = {}
        self._physical_leases = {}
        self._standby_observations = {}
        self._plan_cache = OrderedDict()
        self._project_dependency_cache = OrderedDict()
        self.remote_standby_grace_s = 2.0
        self.run_store = JobRunStore()
        self.administrative_pending_path = self.repository.projects_dir.parent / 'administrative-reset.pending.json'
        if self.administrative_pending_path.exists():
            self.runtime.physical_ownership.administration_failed('Journal administrativo pendiente; no se reanuda automáticamente.')

    def assert_administration_available(self):
        if (self.administrative_pending_path.exists()
                or self.runtime.physical_ownership.snapshot().get('administrative_active')):
            raise ApplicationError('Administración activa o incompleta; conservar datos y revisar recuperación.')

    def assert_project_edit_safe(self, project_id):
        """Software-only writes must not change even prepared execution evidence."""
        self.repository.load_project(project_id)
        for path in self.repository.project_dir(project_id).glob('reports/jobs/*/*/current_run.json'):
            run = self.run_store.load(path)
            if not run:
                continue
            attempt = run.get('start_attempt') or {}
            unresolved_terminal = run.get('state') in {'JOB_ERROR', 'JOB_CANCELLED'} and (
                run.get('started_at') or attempt.get('may_have_been_sent')
                or any(op.get('remote_file') for op in run.get('operations', []))
                or run.get('cancel_remote_status') in {'pending_identity_check',
                    'identity_unverified', 'cancel_sent_requires_reconciliation',
                    'identity_or_transport_error_requires_reconciliation'})
            if (run.get('state') not in RUN_TERMINAL_STATES or unresolved_terminal
                    or attempt.get('status') in {'dispatching', 'sending', 'uncertain'}):
                raise ApplicationError('La ejecución actual conserva dependencias de este proyecto.')

    def administrative_mutation(self, action, *, allow_started_runs=False):
        return administrative_mutation(self, action, allow_started_runs=allow_started_runs)

    def _assert_administrative_print_identity(self, run, guard):
        if guard.remote is None:
            return
        operation = self.run_store._operation(run)
        expected = operation.get('remote_file')
        if expected:
            try:
                PrintIdentity(expected).require_match(guard.remote)
            except PrintIdentityError as error:
                raise ApplicationError('Identidad remota diferente; se conserva la ejecución.') from error
        if (run.get('start_attempt') or {}).get('status') in {'sending', 'dispatching', 'uncertain'}:
            raise ApplicationError('Intento de start ambiguo; no se permite reset administrativo.')

    def _assert_no_started_runs(self):
        for path in self.repository.projects_dir.glob('*/reports/jobs/*/*/current_run.json'):
            run = self.run_store.load(path)
            if run and run.get('state') not in RUN_TERMINAL_STATES and (
                    run.get('started_at') or run.get('state') not in PREPARABLE_STATES):
                raise ApplicationError('Una ejecución conserva dependencias físicas; no se pueden modificar referencias o mapas.')

    def _job_permit(self, context):
        self.assert_administration_available()
        key = (context.project_id, context.setup_id, context.face)
        with self._lock:
            permit = self._physical_leases.get(key)
            if permit is None:
                permit = self.runtime.physical_ownership.acquire(OwnerKind.JOB_EXECUTION, '/'.join(key))
                self._physical_leases[key] = permit
            else:
                self.runtime.physical_ownership.validate(permit)
            return permit

    def _motion_adapter(self, context):
        permit = self._job_permit(context)
        adapter = self.adapter_factory(self.runtime)
        adapter.permit = permit
        return adapter

    def get_plan(self, *, project_id: str, setup_id: str, face: str) -> dict[str, Any]:
        context = self._context(project_id, setup_id, face)
        return self._resolved_plan(context, publish=False)

    def create_plan(self, *, project_id: str, setup_id: str, face: str) -> dict[str, Any]:
        context = self._context(project_id, setup_id, face)
        plan = self._build_plan(context)
        self._save_plan(context, plan)
        return plan

    def assert_manual_compensation_allowed(self, project_id, operation_id):
        project = self.repository.load_project(project_id)
        operation = project.get_operation(operation_id)
        context = self._context(project_id, operation.setup_id, str(operation.cara))
        run = self.run_store.load(self._existing_run_file(context))
        if run and run.get('started_at') and run.get('state') not in RUN_TERMINAL_STATES:
            raise ApplicationError('Diagnóstico Legacy deshabilitado durante JobRun activo; la ejecución genera su propio JIT.')

    def generate_project_compensation(self, *, project_id: str, setup_id: str, face: str) -> dict[str, Any]:
        for operation in self.repository.load_project(project_id).operations_for_setup(setup_id):
            self.assert_manual_compensation_allowed(project_id, operation.id)
        context = self._context(project_id, setup_id, face)
        plan = self._build_plan(context)
        generated_results: dict[str, dict[str, Any]] = {}
        for item in plan["operations"]:
            if item["blocking"]:
                continue
            try:
                generated = self.compensated_gcode_service.generate(
                    project_id,
                    item["operation_id"],
                    mode="legacy",
                    require_tool_reference=item["reference_status"] == "LISTA",
                )
                if self.time_estimation_service is not None:
                    estimate = self.time_estimation_service.estimate_project_file(
                        project_id=project_id,
                        relative_path=str(generated["relative_path"]),
                    )
                    generated["time_estimate"] = estimate
                    self._merge_generated_metadata(project_id, str(generated["metadata_path"]), {"time_estimate": estimate})
                if bool(generated.get("executable", True)):
                    generated_results[item["operation_id"]] = generated
            except Exception as error:
                generated_results[item["operation_id"]] = {"error": str(error)}
        refreshed = self._build_plan(context, generated_results=generated_results)
        self._write_manifest(context, refreshed)
        refreshed["updated_at"] = _iso_now()
        self._save_plan(context, refreshed)
        return refreshed

    @execution_write
    def prepare_run(self, *, project_id: str, setup_id: str, face: str) -> dict[str, Any]:
        self.assert_administration_available()
        context = self._context(project_id, setup_id, face)
        current = self.run_store.load(self._existing_run_file(context))
        if current is not None and (current.get('state') not in PREPARABLE_STATES or current.get('started_at')):
            raise ApplicationError(f"No se puede preparar desde {current.get('state')}; el JobRun se conserva.")
        plan = self._load_or_build_plan(context)
        run = self._base_run(context, plan)
        readiness = self._execution_readiness(context, plan, run)
        ready = readiness['conditions_ready']
        run.update(checks=readiness['checks'], state='JOB_READY' if ready else 'JOB_VALIDATING',
                   ready=ready, next_action='Iniciar trabajo' if ready else 'Resolver bloqueos',
                   available_actions=['start'] if ready else [], updated_at=_iso_now())
        run['readiness'] = self._execution_readiness(context, plan, run)
        saved = self.run_store.create(self._run_file(context), run, previous=current)
        run.update(revision=saved['revision'], cancellation_epoch=saved['cancellation_epoch'])
        return run

    @execution_write
    def start_run(self, *, project_id: str, setup_id: str, face: str, spindle_prepared: bool = False) -> dict[str, Any]:
        self.assert_administration_available()
        context = self._context(project_id, setup_id, face)
        with self._lock:
            current = self._load_run(context)
        if current is None:
            raise ApplicationError("Prepare explícitamente un JobRun antes de iniciar.")
        if spindle_prepared is not True:
            raise ApplicationError("Confirme explícitamente que el spindle está preparado antes de iniciar.")
        recovered = self._recover_active_print_if_possible(context, current)
        if recovered is not None:
            self._start_worker(context)
            return recovered
        if current.get('state') not in {'JOB_VALIDATING', 'JOB_READY'} or current.get('started_at'):
            raise ApplicationError("JOB_ACTIVE_CONFLICT")
        readiness = self._execution_readiness(context, self._resolved_plan(context, publish=False), current)
        current.update(readiness=readiness, checks=readiness['checks'], ready=readiness['can_start'],
                       state='JOB_READY' if readiness['can_start'] else 'JOB_VALIDATING')
        self._save_run(context, current)
        if not readiness['can_start']:
            raise ApplicationError('El trabajo no está listo para iniciar. ' + '; '.join(item['message'] for item in readiness['blocking_reasons']))
        prepared = current
        if not prepared.get("ready"):
            raise ApplicationError("El trabajo no está listo para iniciar. Revise el preflight general.")
        run = prepared
        if run.get("state") not in {"JOB_READY", "JOB_PAUSED", "OPERATION_PAUSED", "TOOL_REFERENCE_READY", "READY_TO_RESUME", "NEXT_OPERATION_READY"}:
            raise ApplicationError(f"El trabajo no puede iniciar desde estado {run.get('state')}.")
        self._require_run_reference(context, run, next_pending=True)
        self._record_spindle_confirmation(context, run, "prepared", self._next_pending_operation_index(run))
        self._job_permit(context)
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

    def get_run(self, *, project_id: str, setup_id: str, face: str) -> dict[str, Any] | None:
        context = self._context(project_id, setup_id, face)
        self._validate_execution_target(context)
        return self._load_run(context)

    def live_execution(self, *, project_id: str, setup_id: str, face: str) -> dict[str, Any]:
        context = self._context(project_id, setup_id, face)
        self._validate_execution_target(context)
        run = self._load_run(context)
        if run is None:
            return self._no_active_execution_snapshot()
        self._validate_run_context(context, run)
        diagnosis = self._diagnose_run(context, run)
        status = diagnosis["moonraker"]
        self._reconcile_standby(context, run, status)
        diagnosis["run"]["status"] = run["state"]
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
        if str(run.get("state")) == "JOB_COMPLETE":
            overall_progress = 1.0
        else:
            weighted_total = 0.0
            weighted_done = 0.0
            weighted_available = bool(operations)
            for item in operations:
                raw_estimate = item.get("estimated_time_s")
                try:
                    weight = float(raw_estimate)
                except (TypeError, ValueError):
                    weighted_available = False
                    break
                if not math.isfinite(weight) or weight <= 0:
                    weighted_available = False
                    break
                execution_status = str(item.get("execution_status") or "")
                fraction = 1.0 if execution_status == "COMPLETED" else self._clamp_progress(item.get("progress"))
                weighted_total += weight
                weighted_done += weight * fraction
            if weighted_available and weighted_total > 0:
                overall_progress = self._clamp_progress(weighted_done / weighted_total)
            else:
                current_is_completed = str((operation or {}).get("execution_status") or "") == "COMPLETED"
                current_fraction = 0.0 if current_is_completed else progress
                overall_progress = min(1.0, (completed + current_fraction) / total) if total else 0.0
        next_index = index + 1
        next_operation = operations[next_index] if 0 <= next_index < len(operations) else None
        transition_states = {
            "SPINDLE_STOP_REQUIRED",
            "SPINDLE_STOP_CONFIRMED",
            "RETRACTING",
            "TOOL_CHANGE_REQUIRED",
            "TOOL_CHANGE_CONFIRMED",
            "RETURNING_TO_REFERENCE_SAFE_Z",
            "RETURNING_TO_REFERENCE_XY",
            "MOVING_TO_REFERENCE",
            "CALIBRATING_TOOL",
            "READY_TO_RESUME",
            "RECOVERY_REQUIRED",
        }
        transition_operation = next_operation if str(run.get("state")) in transition_states and next_operation is not None else operation
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
        eta = self._build_eta_snapshot(run, operation, status, expected, observed)
        if eta.get("available") and "eta_ratio_ema" in run:
            try:
                self.run_store.observe(self._run_file(context), run, {'eta_ratio_ema': run['eta_ratio_ema']})
            except JobRunConflict:
                latest = self._load_run(context)
                if latest is None:
                    return self._no_active_execution_snapshot()
                # Return current domain state; never a stale RUNNING after cancel.
                run = latest
        try:
            assessment = self.inspect_execution(project_id=project_id, setup_id=setup_id, face=face)['readiness']
        except Exception as error:
            # Domain inspection must not hide the persisted execution monitor.
            assessment = evaluate_readiness({'project_id': project_id, 'setup_id': setup_id, 'face': face},
                run, self.runtime.snapshot(), self.runtime.physical_ownership.snapshot(),
                self.runtime.current_physical_session_id())
            assessment['blocking_reasons'].insert(0, {'code': 'EXECUTION_INSPECTION_UNAVAILABLE',
                'category': 'execution', 'message': _safe_operator_error(error),
                'action': 'Reintente la inspección; el monitor conserva el estado del JobRun.'})
        # Fresh presentation of the persisted run; do not save derived checks.
        run = deepcopy(run)
        run['readiness'] = assessment
        run['checks'] = assessment['checks']
        run['ready'] = assessment['can_start']
        health = execution_health(run, status, assessment, worker_alive)
        run['available_actions'] = health['available_actions']
        recovery = health['recovery'] or (self._recovery_diagnostic(run, status, assessment, worker_alive)
            if run.get('state') in {'RECOVERY_REQUIRED', 'JOB_ERROR'} else None)
        return {
            "has_active_run": True,
            "start_readiness": assessment if health['phase'] == 'START' else None,
            "active_execution_health": health,
            "readiness": assessment,
            "recovery": recovery,
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
                "total_duration": status.get("total_duration"),
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
                "tool": (transition_operation or {}).get("tool_name"),
                "tool_reference_profile": (transition_operation or {}).get("tool_reference_profile", "standard"),
                "tool_change_profile": (transition_operation or {}).get("tool_reference_profile", "standard"),
                "tool_change_clearance_z_mm": (transition_operation or {}).get("tool_change_clearance_z_mm"),
                "z_clearance_feed_mm_min": (transition_operation or {}).get("z_clearance_feed_mm_min"),
                "reference_approach_z_feed_mm_min": (transition_operation or {}).get("reference_approach_z_feed_mm_min"),
                "reference_probe_feed_mm_min": (transition_operation or {}).get("reference_probe_feed_mm_min"),
                "stage": run.get("transition_stage"),
                "outgoing_tool": (operation or {}).get("tool_name"),
                "outgoing_tool_change_profile": (operation or {}).get("tool_reference_profile", "standard"),
                "outgoing_tool_change_clearance_z_mm": (operation or {}).get("tool_change_clearance_z_mm"),
                "reference_prep_z_mm": (transition_operation or {}).get("reference_prep_z_mm"),
                "last_error": run.get("last_transition_error") if str(run.get("state")) == "RECOVERY_REQUIRED" else None,
                "operator_confirmation_required": str(run.get("state")) in {"SPINDLE_STOP_REQUIRED", "TOOL_CHANGE_REQUIRED", "READY_TO_RESUME"},
            },
            "synchronization": {
                "ok": sync_reason is None,
                "reason": sync_reason,
            },
            "events": self._dedupe_events(run.get("events") or []),
            "job_run": run,
            "eta": eta,
        }

    def _recovery_diagnostic(self, run, status, assessment, worker_alive):
        reference = assessment['reference']
        if run.get('started_at') and not reference['valid']:
            return {'code': reference['code'], 'message': reference['message'],
                    'action': 'Revise la sesión y la referencia de la instalación actual.'}
        operation = self._diagnostic_operation(run)
        expected, observed = self._normalize_filename(operation.get('remote_file')), self._normalize_filename(status.get('filename'))
        if str(status.get('print_state')) in {'printing', 'paused'} and expected != observed:
            return {'code': 'PRINT_IDENTITY_MISMATCH', 'message': 'El archivo remoto no coincide con este trabajo.',
                    'action': 'Identifique la impresión remota antes de cualquier recuperación.'}
        if run.get('state') == 'RECOVERY_REQUIRED' and str(status.get('print_state')) == 'standby' and expected:
            return {'code': 'MOONRAKER_STANDBY', 'message': 'Moonraker en standby contradice la ejecución esperada.',
                    'action': 'Revise el archivo y el estado remoto; no reinicie automáticamente.'}
        if run.get('last_transition_error'):
            return {'code': 'TOOL_TRANSITION_FAILED', 'message': str(run['last_transition_error'].get('message', 'Falló la transición de herramienta.')),
                    'action': 'Revise la transición y la posición antes de recuperarla.'}
        if run.get('state') in RUN_ACTIVE_STATES and not worker_alive:
            return {'code': 'SUPERVISOR_MISSING', 'message': 'El supervisor de ejecución no está activo.',
                    'action': 'Revise la identidad y el estado de Moonraker antes de recuperar.'}
        if run.get('state') == 'RECOVERY_REQUIRED':
            return run.get('recovery_reason') or {'code': 'RECOVERY_REQUIRED',
                'message': run.get('next_action') or 'Se requiere recuperación explícita.',
                'action': 'Revise el diagnóstico de ejecución.'}
        return None

    def _no_active_execution_snapshot(self) -> dict[str, Any]:
        """Represent normal JobRun absence without creating or probing anything."""
        runtime_snapshot = self.runtime.snapshot()
        runtime_moonraker = runtime_snapshot.get("moonraker") or {}
        return {
            "has_active_run": False,
            "moonraker": {
                "connected": bool(runtime_moonraker.get("http_connected")),
                "klipper_state": runtime_moonraker.get("klippy_state"),
                "print_state": None,
                "filename": None,
                "progress": 0.0,
                "is_active": False,
                "file_position": None,
                "file_size": None,
                "print_duration": None,
                "total_duration": None,
                "message": "Sin ejecución activa; no se consultó el estado de impresión de Moonraker.",
                "updated_at": _iso_now(),
            },
            "run": {
                "run_id": None,
                "status": "NO_EXECUTION",
                "current_operation_index": 0,
                "total_operations": 0,
                "completed_operations": 0,
                "overall_progress": 0.0,
                "next_action": "Preparar trabajo",
                "available_actions": [],
                "worker_alive": False,
                "watcher_alive": False,
                "supervisor_registered": False,
                "movement_lock": None,
                "job_lock": False,
                "last_watcher_error": None,
                "recovery_state": None,
                "stale_candidate": False,
                "updated_at": None,
            },
            "operation": {
                "operation_id": None,
                "name": None,
                "tool": None,
                "execution_status": "NO_EXECUTION",
                "expected_remote_file": None,
                "observed_filename": None,
                "filename_match": False,
                "observed_printing": False,
                "progress": 0.0,
            },
            "operations": [],
            "transition": {
                "state": "NO_EXECUTION",
                "required_tool": None,
                "tool": None,
                "tool_reference_profile": "standard",
                "tool_change_profile": "standard",
                "tool_change_clearance_z_mm": None,
                "outgoing_tool": None,
                "outgoing_tool_change_profile": "standard",
                "outgoing_tool_change_clearance_z_mm": None,
                "reference_prep_z_mm": None,
                "last_error": None,
                "operator_confirmation_required": False,
            },
            "synchronization": {"ok": True, "reason": None},
            "events": [],
            "job_run": None,
            "eta": {"available": False, "reason": "no_active_run"},
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
            self._archive_ownership_clear()
            and self._moonraker_is_idle(diagnosis["moonraker"])
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
        with self.administrative_mutation('archive-stale', allow_started_runs=True) as guard:
            return self._archive_stale_run(project_id=project_id, setup_id=setup_id, face=face, guard=guard)

    def _archive_stale_run(self, *, project_id, setup_id, face, guard):
        context = self._context(project_id, setup_id, face)
        run = self._load_run(context)
        if run is None:
            raise NotFoundError("No existe una ejecución de trabajo para este montaje/cara.")
        diagnosis = self._diagnose_run(context, run)
        moonraker = diagnosis["moonraker"]
        if not self._moonraker_is_idle(moonraker):
            raise ApplicationError("No se puede archivar la ejecución obsoleta: Moonraker debe confirmar conexión, Klipper ready e impresión inactiva; un estado desconocido o shutdown no demuestra reposo.")
        if not self._is_stale_run(diagnosis["run"], str(run.get("state") or "JOB_DRAFT")):
            raise ApplicationError("La ejecución actual no cumple el criterio real de obsolescencia; primero revalide el plan o espere a que el estado se estabilice.")
        guard.check()
        self._assert_administrative_print_identity(run, guard)
        self.run_store.validate(self._run_file(context), run)
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
        guard.begin_mutation(run_id=run['run_id'], revision=run['revision'])
        archive_path = self._write_archived_run(context, archived, suffix="stale")
        guard.check()
        run_file = self._run_file(context)
        if run_file.exists():
            self.run_store.remove(run_file, run)
            released.append("job_run.current_run")
        return {
            "archived_run_id": archived.get("run_id"),
            "previous_status": previous_status,
            "archive_path": self._relative_to_project(context.project_id, archive_path),
            "locks_released": released,
            "can_start_new_run": not self._run_file(context).exists(),
        }

    def reset_runs_for_preparation(self, *, project_id: str, setup_id: str, reset_dependencies=None) -> dict[str, Any]:
        """Keep current runs until all dependency callbacks succeed; never fake rollback."""
        if not setup_id or setup_id in {'.', '..'} or '/' in setup_id or '\\' in setup_id:
            raise ApplicationError('Identificador de montaje inválido.')
        with self.administrative_mutation('reset-preparation', allow_started_runs=True) as guard:
            self.repository.load_project(project_id).get_setup(setup_id)
            entries = list(self._current_runs_for_setup(project_id, setup_id))
            for _context, path, run in entries:
                self._assert_administrative_print_identity(run, guard)
                self.run_store.validate(path, run, allow_cancelled=True)
            guard.begin_mutation(project_id=project_id, setup_id=setup_id,
                                 runs=[{'run_id': r['run_id'], 'revision': r['revision']} for _, _, r in entries])
            archived_runs = []
            for context, path, run in entries:
                guard.check()
                archived = deepcopy(run)
                archived.update(previous_status=run.get('state'), archive_reason='preparation_reset',
                                completed_at=run.get('completed_at') or _iso_now(), updated_at=_iso_now(),
                                available_actions=[], next_action='Ejecución archivada por reinicio de preparación')
                if run.get('state') not in RUN_TERMINAL_STATES:
                    archived.update(state='PREPARATION_RESET_ARCHIVED', recovery_state='PREPARATION_RESET_ARCHIVED')
                self._append_event(archived, "warning", "El operador reinició la preparación; se conserva el historial de ejecución.")
                self._prepare_history_snapshot(archived)
                archive_path = self._write_archived_run(context, archived, suffix='preparation-reset')
                archived_runs.append({'run_id': run['run_id'], 'previous_status': run.get('state'),
                    'archive_reason': 'preparation_reset', 'history_already_present': False,
                    'archive_path': self._relative_to_project(project_id, archive_path)})
            dependency_result = reset_dependencies(guard) if reset_dependencies else None
            guard.check()
            # No removal until every callback and every expected revision passed.
            current_paths = {str(path) for _, path, _ in self._current_runs_for_setup(project_id, setup_id)}
            if current_paths != {str(path) for _, path, _ in entries}:
                raise JobRunConflict('Cambió el conjunto de ejecuciones durante el reset.')
            for _, path, run in entries:
                self.run_store.validate(path, run, allow_cancelled=True)
            for _, path, run in entries:
                guard.check()
                self.run_store.remove(path, run)
            result = {'reason': 'preparation_reset', 'archived_runs': archived_runs,
                      'current_runs_cleared': len(entries), 'can_start_new_run': True}
            if dependency_result is not None:
                result['dependency_result'] = dependency_result
            return result

    def history(self, *, project_id: str, setup_id: str, face: str) -> list[dict[str, Any]]:
        history_dir = self._existing_run_file(self._context(project_id, setup_id, face)).parent / "history"
        if not history_dir.exists():
            return []
        items: list[dict[str, Any]] = []
        for file in sorted(history_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
            payload = json.loads(file.read_text(encoding="utf-8"))
            history_summary = payload.get("history_summary") or {}
            items.append(
                {
                    "run_id": payload.get("run_id"),
                    "state": payload.get("state"),
                    "started_at": payload.get("started_at"),
                    "completed_at": payload.get("completed_at"),
                    "tool_changes_completed": payload.get("summary", {}).get("tool_changes_completed", 0),
                    "operations_completed": payload.get("summary", {}).get("operations_completed", 0),
                    "manifest_path": payload.get("manifest_path"),
                    "original_estimated_time_s": history_summary.get("original_estimated_time_s"),
                    "compensated_estimated_time_s": history_summary.get("compensated_estimated_time_s"),
                    "actual_print_duration_s": history_summary.get("actual_print_duration_s"),
                    "estimation_error_pct": history_summary.get("estimation_error_pct"),
                    "calibration_factor": history_summary.get("calibration_factor"),
                    "entries": payload.get("history_entries", []),
                }
            )
        return items

    @execution_write
    def run_action(self, *, project_id: str, setup_id: str, face: str, action: str) -> dict[str, Any]:
        self.assert_administration_available()
        context = self._context(project_id, setup_id, face)
        run = self._load_run(context)
        if run is None:
            raise NotFoundError("No existe una ejecución de trabajo para este montaje/cara.")
        if action == "cancel":
            return self._cancel_run(context, run)
        self.run_store.validate(self._run_file(context), run)
        if action in {'retry-operation-preparation', 'update-tool-reference'}:
            return self._recover_preparation(context, run, measure=action == 'update-tool-reference')
        adapter = self._motion_adapter(context)
        if action == "pause":
            operation = self.run_store._operation(run)
            if operation.get("remote_file"):
                if not self._existing_print_action(context, run, adapter, "pause", {'printing', 'paused'}):
                    return run
                operation["execution_status"] = "PAUSED"
                run["state"] = "OPERATION_PAUSED"
            else:
                run["state"] = "JOB_PAUSED"
            run["next_action"] = "Reanudar trabajo"
            run["available_actions"] = ["resume", "cancel"]
            self._append_event(run, "warning", "Trabajo pausado por el operador.")
        elif action == "resume":
            if run["state"] not in {"JOB_PAUSED", "OPERATION_PAUSED", "TOOL_REFERENCE_READY", "READY_TO_RESUME", "NEXT_OPERATION_READY"}:
                raise ApplicationError(f"No se puede reanudar desde {run['state']}.")
            operation = self.run_store._operation(run)
            if run["state"] in {"TOOL_REFERENCE_READY", "READY_TO_RESUME", "NEXT_OPERATION_READY"}:
                self._require_run_reference(context, run, next_pending=True)
            if (run["state"] == "OPERATION_PAUSED" or
                    (run["state"] == "JOB_PAUSED" and operation.get('remote_file')
                     and operation.get('execution_status') != 'COMPLETED')):
                if not self._existing_print_action(context, run, adapter, "resume", {'paused'}):
                    return run
                run["state"] = "OPERATION_RUNNING"
            else:
                self._require_run_reference(context, run, next_pending=True)
                self._record_spindle_confirmation(context, run, "prepared", self._next_pending_operation_index(run))
                run["state"] = "JOB_STARTING"
            run["available_actions"] = ["pause", "cancel"]
            run["next_action"] = "Reanudando trabajo"
            self._append_event(run, "info", "Trabajo reanudado por el operador.")
            self._save_run(context, run)
            self._start_worker(context)
            return run
        elif action == "retry-tool-change-transition":
            live = self.live_execution(project_id=project_id, setup_id=setup_id, face=face)
            if action not in live['run']['available_actions']:
                raise ApplicationError("No es seguro reintentar esta transición: revise sesión, ownership e identidad remota.")
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
            self._record_spindle_confirmation(context, run, "stopped", next_index)
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
            self._require_spindle_confirmation(context, run, "stopped", next_index)
            plan = self._load_or_build_plan(context)
            active_map = plan["active_map"]
            self.physical_map_service.invalidate_tool_reference(
                project_id=context.project_id,
                map_id=active_map["map_id"],
                operation_id=next_operation["operation_id"],
            )
            next_operation["reference_status"] = "REQUIERE_REFERENCIA"
            from uuid import uuid4
            next_operation["installation_revision"] = uuid4().hex
            run["state"] = "TOOL_CHANGE_CONFIRMED"
            run["next_action"] = "Moviendo al punto de referencia para calibrar la nueva herramienta"
            run["available_actions"] = ["cancel"]
            self._append_event(run, "info", f"Herramienta confirmada para {next_operation['tool_name']}; la CNC irá automáticamente al punto de referencia.")
        elif action == "measure-reference":
            self._measure_tool_reference(context, run)
        elif action == "continue":
            if run["state"] not in {"TOOL_REFERENCE_READY", "READY_TO_RESUME", "NEXT_OPERATION_READY"}:
                raise ApplicationError("Continuar solo aplica cuando ya existe referencia Z y hay una siguiente operación preparada.")
            self._require_run_reference(context, run, next_pending=True)
            self._record_spindle_confirmation(context, run, "prepared", self._next_pending_operation_index(run))
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

    def _recover_preparation(self, context, run, *, measure=False):
        """Explicit action only. Never adopt an upload or replay a start attempt."""
        thread = self._supervisor_thread(context)
        if thread and thread.is_alive():
            raise ApplicationError('El supervisor sigue activo.')
        diagnosis = self._diagnose_run(context, run)
        if not preparation_retry_safe(run, diagnosis['moonraker']):
            raise ApplicationError('Preparación ambigua o upload iniciado; requiere recuperación de identidad remota.')
        plan = self._resolved_plan(context, publish=False)
        assessment = self._execution_readiness(context, plan, run)
        blocked = [r for r in assessment['blocking_reasons'] if r['category'] in {'geometry', 'runtime'}]
        if blocked:
            raise ApplicationError('; '.join(r['message'] for r in blocked))
        index = int(run.get('current_operation_index') or 0)
        operation = run['operations'][index]
        self.run_store.validate(self._run_file(context), run)
        if measure:
            # Deliberate physical action, protected by the existing motion adapter.
            # No historical token is promoted; create a new installation evidence.
            from uuid import uuid4
            self._validate_spindle_stop_confirmation(self.adapter_factory(self.runtime))
            operation['installation_revision'] = uuid4().hex
            operation['physical_reference_token'] = None
            self._save_run(context, run)
            try:
                self._measure_tool_reference(context, run)
                if not run.get('started_at'):
                    run.update(state='JOB_READY', available_actions=['start'], next_action='Confirme spindle preparado e inicie el trabajo.')
                    self._save_run(context, run)
            except Exception as error:
                self.runtime.physical_ownership.enter_recovery('Medición de referencia interrumpida; verifique el estado físico.')
                self._identity_failure(context, run, error)
                raise
            return run
        self._require_run_reference(context, run, next_pending=True)
        self._require_spindle_confirmation(context, run, 'prepared', index)
        for key in ('generated_file', 'generated_file_name', 'generated_metadata_path', 'generated_metadata', 'remote_file'):
            operation.pop(key, None)
        operation['execution_status'] = 'PENDING'
        run.update(state='JOB_STARTING', next_action='Reintentando preparación desde cero', available_actions=['cancel'],
                   completed_at=None, last_watcher_error=None, recovery_state=None)
        self._append_event(run, 'info', 'Reintento explícito de preparación: se descarta el upload anterior, se regenera JIT y se conserva el historial de start.')
        self._save_run(context, run)
        self._start_worker(context)
        return run

    def _identity_failure(self, context, run, error):
        reference_failure = isinstance(error, ApplicationError) and not isinstance(error, PrintIdentityError)
        run.update(state='RECOVERY_REQUIRED', recovery_state='PHYSICAL_REFERENCE_REQUIRED' if reference_failure else 'PRINT_IDENTITY_REQUIRED',
                   next_action='Verificar el archivo Moonraker antes de continuar',
                   available_actions=['cancel'], last_watcher_error=str(error))
        self._append_event(run, 'error', str(error))
        self._save_run(context, run)

    def _reference_dispatch_guard(self, context, operation, generated=None):
        """Validate bytes before refresh; retain mutation guards for final checks.

        The second digest/context check is prepare-time IO. final_check uses only
        memory and never rebuilds reference_context or reads files.
        """
        root = self.repository.project_dir(context.project_id)
        def digest(path):
            with path.open('rb') as stream:
                return hashlib.file_digest(stream, 'sha256').hexdigest()
        project_path = root / 'project.json'
        project_guard = FileRevisionGuard([project_path])
        project_hash = digest(project_path)
        project = self.repository.load_project(context.project_id)
        current = project.get_operation(operation['operation_id'])
        physical_map = self.physical_map_service.get_active(context.project_id, current.id)
        paths = [self.repository._height_map_file(context.project_id, physical_map['map_id']),
                 self.repository._resolve_project_file(context.project_id, current.archivo_gcode)]
        artifact = generated or {'relative_path': operation.get('generated_file'),
                                 'metadata_path': operation.get('generated_metadata_path') or operation.get('generated_metadata')}
        for key in ('relative_path', 'metadata_path'):
            if artifact.get(key):
                paths.append(self.repository._resolve_project_file(context.project_id, artifact[key]))
        files_guard = FileRevisionGuard(paths)
        hashes = {path: digest(path) for path in paths}
        hashes[project_path] = project_hash
        physical_context = reference_context(self.repository, self.runtime, context.project_id,
            current.id, physical_map, operation.get('installation_revision'), project=project)
        config = deepcopy(vars(self.runtime.config))
        binding = deepcopy({key: operation.get(key) for key in
                            ('operation_id', 'installation_revision', 'physical_reference_token', 'remote_file')})
        ownership_session = self.runtime.physical_ownership.session
        def validate():
            if any(digest(path) != value for path, value in hashes.items()):
                raise ApplicationError('Los datos de ejecución cambiaron después de validar; revalide la operación.')
            if reference_context(self.repository, self.runtime, context.project_id, current.id,
                    physical_map, operation.get('installation_revision'), project=project) != physical_context:
                raise ApplicationError('El contexto físico cambió durante la observación previa al envío.')
        def final_check():
            project_guard.validate()
            files_guard.validate()
            if (vars(self.runtime.config) != config
                    or self.runtime.physical_ownership.session != ownership_session
                    or any(operation.get(key) != value for key, value in binding.items())):
                raise ApplicationError('La configuración o identidad física cambió antes del envío.')
        validate.final_check = final_check
        return validate

    def _existing_print_action(self, context, run, adapter, action, states):
        operation = self.run_store._operation(run)
        try:
            identity = PrintIdentity(operation.get('remote_file'))
            prechecked = False
            reference_guard = None
            run_guard = None
            session = self.runtime.current_physical_session_id()
            def prepare_dispatch():
                nonlocal prechecked, reference_guard, run_guard
                if action == 'resume':
                    reference_guard = self._reference_dispatch_guard(context, operation)
                run_guard = FileRevisionGuard([self._run_file(context)], error=JobRunConflict)
                self.run_store.validate(self._run_file(context), run)
                identity.require_match(adapter.print_status(), states=states)
                if action == 'resume':
                    self._require_run_reference(context, run)
                    self._require_spindle_confirmation(context, run, "prepared", int(run["current_operation_index"]))
                prechecked = True
            def verify_dispatch():
                if reference_guard is not None:
                    reference_guard()
                identity.require_match(adapter.print_status(), states=states)
                self.run_store.validate(self._run_file(context), run)
            def before_send():
                if not prechecked:
                    prepare_dispatch()
                    verify_dispatch()
                if reference_guard is not None:
                    reference_guard.final_check()
                run_guard.validate()
                if self.runtime.current_physical_session_id() != session:
                    raise ApplicationError('La sesión física cambió antes del envío.')
                self.runtime.physical_ownership.validate(adapter.permit)
            before_send.prepare = prepare_dispatch
            before_send.verify = verify_dispatch
            getattr(adapter, action)(before_send=before_send)
            return True
        except JobRunConflict:
            raise
        except Exception as error:
            self._identity_failure(context, run, error)
            return False

    def _cancel_run(self, context, run):
        # Persist cancellation before any network IO. A stale caller can cancel
        # this run, but never a replacement run at the same path.
        path = self._run_file(context)
        cancelled = self.run_store.cancel(path, run['run_id'])
        key = (context.project_id, context.setup_id, context.face)
        permit = self._physical_leases.get(key)
        if permit is not None:
            self.runtime.physical_ownership.request_cancel(permit)
        adapter = self.adapter_factory(self.runtime)
        result = 'identity_unverified'
        try:
            operation = self.run_store._operation(cancelled)
            expected = operation.get('remote_file')
            if expected:
                identity = PrintIdentity(expected)
                def before_send():
                    identity.require_match(adapter.print_status())
                    self.run_store.validate(path, cancelled, allow_cancelled=True)
                adapter.cancel(before_send=before_send)
                result = 'cancel_sent_requires_reconciliation'
            else:
                result = 'no_expected_print_no_remote_action'
        except JobRunConflict:
            raise
        except Exception:
            # Unknown/mismatched identity never authorizes a global cancel.
            result = 'identity_or_transport_error_requires_reconciliation'
        cancelled = self.run_store.cancel_result(path, cancelled, result)
        self._archive_run(context, cancelled)
        with self._lock:
            thread = self._threads.get(key)
            producer_active = thread is not None and thread.is_alive()
        if permit is not None and not producer_active:
            self.runtime.physical_ownership.retire(permit)
        return cancelled

    def assert_machine_settings_update_allowed(self) -> None:
        """Reject global physical-setting edits while a started JobRun is active."""
        ownership = self.runtime.physical_ownership.snapshot()
        if ownership["active"] or ownership["recovery_pending"]:
            raise ApplicationError("No se puede modificar la configuración física mientras existe una ejecución activa o recuperación pendiente.")
        if self.mesh_execution_service is not None and self.mesh_execution_service.active_execution_snapshot()["active"]:
            raise ApplicationError("No se puede modificar la configuración física mientras existe un worker/probe de malla activo.")
        with self._lock:
            if any(thread.is_alive() for thread in self._threads.values()):
                raise ApplicationError("No se puede modificar la configuración física mientras existe un supervisor activo.")
        for run_file in self.repository.projects_dir.glob(
            "*/reports/jobs/*/*/current_run.json"
        ):
            try:
                run = self.run_store.load(run_file)
            except Exception as error:
                raise ApplicationError(
                    "No se pudo comprobar si existe un JobRun activo; la configuración física permanece bloqueada."
                ) from error
            state = str(run.get("state") or "JOB_DRAFT")
            if run.get("started_at") and state not in RUN_TERMINAL_STATES:
                raise ApplicationError(
                    "No se puede modificar la configuración física mientras existe una ejecución activa. "
                    f"Cancele o finalice el trabajo antes de cambiarla. Estado actual: {state}."
                )

    def _measure_tool_reference(self, context: JobContext, run: dict[str, Any]) -> None:
        plan = self._load_or_build_plan(context)
        active_map = plan["active_map"]
        if active_map is None:
            raise ApplicationError("No existe mapa físico activo para medir la referencia de herramienta.")
        returning_from_tool_change = run["state"] == "TOOL_CHANGE_CONFIRMED"
        operation_index = int(run["current_operation_index"]) + 1 if returning_from_tool_change else int(run.get("current_operation_index", 0) or 0)
        operation_payload = run["operations"][operation_index]
        if not operation_payload.get('installation_revision'):
            from uuid import uuid4
            operation_payload['installation_revision'] = uuid4().hex
        measurement_context = reference_context(
            self.repository, self.runtime, context.project_id, operation_payload["operation_id"],
            active_map, operation_payload.get("installation_revision"),
        )
        adapter = self._motion_adapter(context)
        reference_x = float(active_map["machine_origin_x"])
        reference_y = float(active_map["machine_origin_y"])
        tool_reference_profile = str(operation_payload.get("tool_reference_profile") or "standard")
        reference_prep_z = self._reference_preparation_z(tool_reference_profile)
        tool_change_clearance_z = self._tool_change_clearance_z(tool_reference_profile)
        z_clearance_feed = self._z_clearance_feed()
        reference_approach_feed = self._reference_approach_z_feed()
        probe_config = active_map.get("probe_config")
        reference_probe_feed = self._reference_probe_feed(probe_config)
        run["state"] = "MOVING_TO_REFERENCE"
        run["available_actions"] = ["cancel"]
        if returning_from_tool_change:
            run["next_action"] = (
                f"Saliendo del cambio con perfil {tool_reference_profile}: despeje Z "
                f"{tool_change_clearance_z:.3f} mm, X/Y de referencia y aproximación Z "
                f"{reference_prep_z:.3f} mm"
            )
        else:
            run["next_action"] = (
                f"Moviendo primero Z a {reference_prep_z:.3f} mm y después al punto de referencia "
                f"CNC X={reference_x:.3f}, Y={reference_y:.3f}"
            )
        operation_payload["reference_prep_z_mm"] = reference_prep_z
        operation_payload["tool_change_clearance_z_mm"] = tool_change_clearance_z
        operation_payload["z_clearance_feed_mm_min"] = z_clearance_feed
        operation_payload["reference_approach_z_feed_mm_min"] = reference_approach_feed
        operation_payload["reference_probe_feed_mm_min"] = reference_probe_feed
        self._append_event(run, "info", run["next_action"])
        self._save_run(context, run)
        if returning_from_tool_change:
            stage_messages = {
                "RETURNING_TO_REFERENCE_SAFE_Z": "Subiendo a clearance de la herramienta nueva",
                "RETURNING_TO_REFERENCE_XY": "Moviendo X/Y al punto de referencia",
                "MOVING_TO_REFERENCE": "Aproximando Z a la altura previa al sondeo",
                "REFERENCE_APPROACH_CONFIRMED": "Z de aproximación confirmada; preparando sondeo",
            }

            def transition_progress(stage: str, payload: dict[str, Any]) -> None:
                run["transition_stage"] = stage
                run["state"] = stage if stage in RUN_ACTIVE_STATES else "MOVING_TO_REFERENCE"
                run["next_action"] = stage_messages.get(stage, "Moviendo a la referencia de herramienta")
                run["transition_motion"] = payload
                run["updated_at"] = _iso_now()
                self._save_run(context, run)

            adapter.move_from_tool_change_to_reference_point(
                x_mm=reference_x,
                y_mm=reference_y,
                tool_change_profile=tool_reference_profile,
                progress_callback=transition_progress,
            )
        else:
            adapter.move_to_reference_point(x_mm=reference_x, y_mm=reference_y, tool_reference_profile=tool_reference_profile)
        run["state"] = "CALIBRATING_TOOL"
        run["transition_stage"] = "CALIBRATING_TOOL"
        run["next_action"] = "Sondeando referencia Z de la nueva herramienta"
        self._append_event(run, "info", run["next_action"])
        self._save_run(context, run)
        probe = adapter.probe_tool_reference(
            x_mm=reference_x,
            y_mm=reference_y,
            probe_config=probe_config,
        )
        snapshot = adapter.runtime_snapshot()
        self.run_store.validate(self._run_file(context), run)
        current_map = self.physical_map_service.get_active(context.project_id, operation_payload["operation_id"])
        if reference_context(self.repository, self.runtime, context.project_id,
                             operation_payload["operation_id"], current_map,
                             operation_payload.get("installation_revision")) != measurement_context:
            raise ApplicationError("La sesión o contexto físico cambió durante probe; repita la medición.")
        position = probe.get("probe") or self.runtime.last_probe_position()
        self.reference_service.capture_physical_z_reference(
            context.project_id,
            operation_payload["operation_id"],
            position=position,
            machine_label=str(snapshot["moonraker"].get("url") or "physical"),
            homed_axes=snapshot["klipper"].get("homed_axes"),
            session_id=measurement_context['physical_session_id'],
        )
        measured_map = self.physical_map_service.record_tool_reference(
            project_id=context.project_id,
            map_id=active_map["map_id"],
            operation_id=operation_payload["operation_id"],
            position=position,
            machine_label=str(snapshot["moonraker"].get("url") or "physical"),
            homed_axes=snapshot["klipper"].get("homed_axes"),
            session_id=measurement_context['physical_session_id'],
            installation_id=operation_payload.get("installation_revision"),
            runtime=self.runtime,
            measurement_context=measurement_context,
        )
        token = self._require_operation_reference(
            context, operation_payload, physical_map=measured_map,
            expected_token=measured_map['tool_references'][operation_payload['tool_key']]['physical_reference_token'],
        )
        if any(token.get(key) != measurement_context[key] for key in
               ("physical_session_id", "ownership_session_id", "physical_config_fingerprint")):
            self.physical_map_service.invalidate_tool_reference(
                project_id=context.project_id, map_id=active_map["map_id"],
                operation_id=operation_payload["operation_id"],
            )
            raise ApplicationError("La sesión física cambió al publicar la medición.")
        operation_payload["physical_reference_token"] = token
        operation_payload["installation_revision"] = token['installation_id']
        operation_payload["reference_status"] = "LISTA"
        run["current_tool_key"] = operation_payload["tool_key"]
        if returning_from_tool_change:
            run["summary"]["tool_changes_completed"] = int(run["summary"].get("tool_changes_completed", 0)) + 1
        run["state"] = "READY_TO_RESUME"
        run["next_action"] = "Referencia Z lista; continuar para generar Legacy y ejecutar la siguiente operación"
        run["available_actions"] = ["continue", "cancel"]
        self._append_event(
            run,
            "info",
            f"Referencia Z medida para {operation_payload['tool_name']}; la compensación Legacy se generará al continuar.",
        )
        self._save_run(context, run)

    def _start_worker(self, context: JobContext) -> None:
        self._job_permit(context)
        key = (context.project_id, context.setup_id, context.face)
        with self._lock:
            thread = self._threads.get(key)
            if thread is not None and thread.is_alive():
                return
            run = self._load_run(context)
            if run is None or run.get("state") in RUN_TERMINAL_STATES:
                return
            worker = threading.Thread(target=self._run_worker, args=(context, run["run_id"]), name=f"job-{context.setup_id}-{context.face}", daemon=True)
            self._threads[key] = worker
            worker.start()

    def _run_worker(self, context: JobContext, run_id: str | None = None) -> None:
        key = (context.project_id, context.setup_id, context.face)
        initial = self._load_run(context)
        run_id = run_id or (initial or {}).get('run_id')
        permit = self._physical_leases.get(key)
        try:
            try:
                while True:
                    run = self._load_run(context)
                    if run is None or run.get("run_id") != run_id or run.get("state") in RUN_TERMINAL_STATES | RUN_WAITING_STATES:
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
            except JobRunConflict:
                return
            except Exception as error:
                self.runtime.physical_ownership.enter_recovery("Fallo del supervisor JOB; estado físico incierto.")
                current = run
                if current is not None and current.get('run_id') == run_id:
                    current["state"] = "JOB_ERROR"
                    current["completed_at"] = current.get("completed_at") or _iso_now()
                    current["updated_at"] = _iso_now()
                    current["available_actions"] = ["cancel"]
                    current["next_action"] = "Revisar error del supervisor"
                    current["last_watcher_error"] = traceback.format_exc()
                    self._append_event(current, "error", f"Fallo del supervisor: {error}")
                    try:
                        self._save_run(context, current)
                    except JobRunConflict:
                        pass
        finally:
            current = self._load_run(context)
            if current is None or (current.get('run_id') == run_id and current.get('state') in RUN_TERMINAL_STATES):
                if (permit is not None and current is not None and current.get('state') == 'JOB_COMPLETE'
                        and self.runtime.complete_physical_workflow(permit)):
                    self._physical_leases.pop(key, None)
                else:
                    if permit is not None:
                        self.runtime.physical_ownership.retire(permit)
                    self.runtime.physical_ownership.enter_recovery('JOB terminado sin cierre físico confirmado.')
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
        candidate = reference_candidate(run, index)
        try:
            token = self._require_operation_reference(context, candidate)
        except ApplicationError as error:
            operation['reference_status'] = 'REQUIERE_REFERENCIA'
            run['state'] = 'RECOVERY_REQUIRED' if run.get('started_at') else 'JOB_VALIDATING'
            run['recovery_reason'] = {'code': 'REFERENCE_INVALID', 'message': str(error),
                                      'action': 'Verifique la instalación y su referencia antes de continuar.'}
            run['next_action'] = str(error)
            run['available_actions'] = ['cancel']
            self._save_run(context, run)
            return
        operation.update(installation_revision=token['installation_id'], physical_reference_token=token,
                         reference_status='LISTA')
        adapter = self._motion_adapter(context)
        self._require_spindle_confirmation(context, run, "prepared", index)
        reference_token = self._require_operation_reference(context, operation)
        operation["physical_reference_token"] = reference_token
        operation["execution_status"] = "PREFLIGHT"
        run["current_operation_index"] = index
        run["current_operation_id"] = operation["operation_id"]
        run["current_tool_key"] = operation["tool_key"]
        run["state"] = "OPERATION_PREFLIGHT"
        run["next_action"] = f"Generando compensación Legacy para {operation['name']}"
        run["available_actions"] = ["pause", "cancel"]
        self._append_event(run, "info", f"Generando compensación Legacy JIT para {operation['name']}.")
        self._save_run(context, run)
        generated = self.compensated_gcode_service.generate(
            context.project_id,
            operation["operation_id"],
            mode="legacy",
            require_tool_reference=True,
        )
        self._require_operation_reference(context, operation, expected_token=reference_token)
        if (generated.get('metadata', {}).get('operation_id') != operation['operation_id']
                or generated.get('metadata', {}).get('physical_reference_token') != reference_token):
            raise ApplicationError('El artefacto JIT no corresponde a la operación/instalación actual.')
        if not generated.get('executable'):
            raise ApplicationError('El artefacto JIT no autoriza ejecución.')
        self._require_spindle_confirmation(context, run, "prepared", index)
        self._validate_generated_pair(context, generated)
        operation["generated_file"] = generated["relative_path"]
        operation["generated_file_name"] = Path(str(generated["relative_path"])).name
        operation["generated_metadata_path"] = generated.get("metadata_path")
        operation["compensation_mode"] = "legacy"
        expected_remote_file = self._expected_remote_file(context, str(generated["relative_path"]))
        recovered = self._recover_active_print_if_possible(
            context,
            run,
            operation_index=index,
            expected_remote_file=expected_remote_file,
        )
        if recovered is not None or run.get("state") == "RECOVERY_REQUIRED":
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
        if self.time_estimation_service is not None:
            estimate = self.time_estimation_service.estimate_project_file(
                project_id=context.project_id,
                relative_path=str(generated["relative_path"]),
                remote_filename=remote_file,
            )
            operation["time_estimate"] = estimate
            operation["estimated_time_s"] = estimate.get("estimated_time_s")
            self._merge_generated_metadata(
                context.project_id,
                str(generated["metadata_path"]),
                {"time_estimate": estimate},
            )
        operation["execution_status"] = "WAITING_FOR_KLIPPER"
        operation["observed_printing"] = False
        operation["progress"] = 0.0
        run["recovery_state"] = None
        self._append_event(run, "info", f"Archivo subido a Moonraker: {remote_file}.")
        if upload.get("print_started") or upload.get("print_queued"):
            raise PrintIdentityError('El upload inició o encoló una impresión inesperadamente.')
        identity = PrintIdentity(expected_remote_file)
        if PrintIdentity(remote_file) != identity or item.get('root') != 'gcodes':
            raise PrintIdentityError('El archivo subido no coincide con el archivo JIT esperado.')
        run["state"] = "WAITING_FOR_KLIPPER"
        run["next_action"] = f"Esperando confirmación de Klipper para {operation['name']}"
        self._save_run(context, run)
        attempt_id = None
        dispatch_evidence = DispatchEvidence()
        prechecked = False
        reference_guard = None
        run_guard = None
        def prepare_dispatch():
            nonlocal prechecked, reference_guard
            reference_guard = self._reference_dispatch_guard(context, operation, generated)
            self.run_store.validate(self._run_file(context), run)
            PrintIdentity.require_idle(adapter.print_status())
            self._require_operation_reference(context, operation, expected_token=reference_token)
            self._validate_generated_pair(context, generated)
            self._require_spindle_confirmation(context, run, "prepared", index)
            prechecked = True
        def verify_dispatch():
            nonlocal attempt_id, run_guard
            self.run_store.validate(self._run_file(context), run)
            reference_guard()
            PrintIdentity.require_idle(adapter.print_status())
            if self.runtime.current_physical_session_id() != reference_token['physical_session_id']:
                raise ApplicationError('La sesión física cambió antes del envío.')
            self.runtime.physical_ownership.validate(adapter.permit)
            authorized = self.run_store.begin_start(self._run_file(context), run,
                                                    operation['operation_id'], identity.filename)
            run.update(revision=authorized['revision'], start_attempt=authorized['start_attempt'])
            attempt_id = authorized['start_attempt']['id']
            sending = self.run_store.mark_start_sending(self._run_file(context), run, attempt_id)
            run.update(revision=sending['revision'], start_attempt=sending['start_attempt'])
            run_guard = FileRevisionGuard([self._run_file(context)], error=JobRunConflict)
            self.run_store.validate(self._run_file(context), run)
        def before_send():
            if not prechecked:
                prepare_dispatch()
                verify_dispatch()
            reference_guard.final_check()
            run_guard.validate()
            if self.runtime.current_physical_session_id() != reference_token['physical_session_id']:
                raise ApplicationError('La sesión física cambió antes del envío.')
            self.runtime.physical_ownership.validate(adapter.permit)
        before_send.prepare = prepare_dispatch
        before_send.verify = verify_dispatch
        before_send.dispatch_evidence = dispatch_evidence
        try:
            adapter.start_file(identity.filename, before_send=before_send)
        except BaseException:
            if attempt_id is not None:
                self.run_store.finish_start(self._run_file(context), run['run_id'], attempt_id, status=dispatch_evidence.failure_status)
            raise
        else:
            self.run_store.finish_start(self._run_file(context), run['run_id'], attempt_id, status='sent')

    def _reconcile_standby(self, context, run, status):
        if run.get('state') not in {'OPERATION_RUNNING', 'WAITING_FOR_KLIPPER', 'OPERATION_STARTING', 'PRINT_QUEUED'}:
            with self._lock:
                for key in list(self._standby_observations):
                    if key[0] == run['run_id']:
                        self._standby_observations.pop(key)
            return False
        operation = self.run_store._operation(run)
        key = (run['run_id'], operation.get('operation_id'))
        state = str(status.get('state') or status.get('print_state') or '').lower()
        expected = self._normalize_filename(operation.get('remote_file'))
        observed = self._normalize_filename(status.get('filename'))
        contradiction = (run.get('state') in {'OPERATION_RUNNING', 'WAITING_FOR_KLIPPER', 'OPERATION_STARTING', 'PRINT_QUEUED'}
                         and state == 'standby' and expected and observed in {'', expected}
                         and not status.get('is_active', status.get('active', False)))
        with self._lock:
            if not contradiction:
                self._standby_observations.pop(key, None)
                return False
            now = time.monotonic()
            since = self._standby_observations.setdefault(key, now)
            while len(self._standby_observations) > 128:
                self._standby_observations.pop(next(iter(self._standby_observations)))
        if now - since >= self.remote_standby_grace_s:
            self._identity_failure(context, run, PrintIdentityError('Moonraker standby persistente contradice la ejecución; requiere recovery.'))
            self.runtime.physical_ownership.enter_recovery('Impresión esperada no activa en Moonraker.')
        return True

    def _watch_operation(self, context: JobContext, run: dict[str, Any]) -> None:
        adapter = self.adapter_factory(self.runtime)
        operation = run["operations"][int(run["current_operation_index"])]
        while True:
            current = self._load_run(context)
            if (current is None or current.get('run_id') != run['run_id']
                    or current.get('current_operation_id') != run.get('current_operation_id')):
                return
            if current.get("state") in RUN_TERMINAL_STATES | RUN_WAITING_STATES | {"JOB_PAUSED"}:
                return
            operation = current["operations"][int(current["current_operation_index"])]
            try:
                status = adapter.print_status()
                if self._reconcile_standby(context, current, status):
                    if current.get("state") == "RECOVERY_REQUIRED":
                        return
                    time.sleep(0.5)
                    continue
                PrintIdentity(operation.get('remote_file')).require_match(status)
            except Exception as error:
                self._identity_failure(context, current, error)
                return
            transition = False
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
                    transition = True
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
                operation["actual_print_duration_s"] = None if status.get("print_duration") is None else float(status.get("print_duration") or 0.0)
                operation["actual_total_duration_s"] = None if status.get("total_duration") is None else float(status.get("total_duration") or 0.0)
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
            if transition:
                self._save_run(context, current)
            else:
                self.run_store.observe(self._run_file(context), current, {},
                    operation_id=operation['operation_id'],
                    operation_fields={key: operation.get(key) for key in JobRunStore.OPERATION_OBSERVATIONS})
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
        refresh_observed_state = getattr(self.runtime, "refresh_observed_state", None)
        if not callable(refresh_observed_state):
            raise ApplicationError(
                "El runtime no permite realizar la observación HTTP activa requerida antes de confirmar el spindle detenido."
            )
        try:
            snapshot = refresh_observed_state()
        except Exception as error:
            raise ApplicationError(
                f"No fue posible actualizar el estado de Moonraker antes de confirmar el spindle detenido: {error}"
            ) from error
        position = snapshot.get("klipper", {}).get("position") or {}
        velocity = position.get("velocity")
        velocity_age = position.get("velocity_age_s")
        if (not isinstance(velocity, (int, float)) or not math.isfinite(velocity)
                or abs(velocity) > self.runtime.config.velocity_tolerance_mm_s
                or not isinstance(velocity_age, (int, float)) or not math.isfinite(velocity_age)
                or not 0 <= velocity_age <= getattr(self.runtime.config, "telemetry_fresh_timeout_s", 2.0)):
            raise ApplicationError("Se requiere evidencia vigente de velocidad cero antes del cambio de herramienta.")
        if str(snapshot.get("moonraker", {}).get("telemetry_state") or "") != "LIVE":
            raise ApplicationError(
                "La telemetría Moonraker sigue sin estar fresca después de una observación HTTP activa; "
                "no se autoriza el movimiento de cambio de herramienta."
            )
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
            run["last_transition_error"] = None
            self._append_event(run, "warning", "Reintento de transición listo; apague manualmente el spindle antes de continuar.")
        else:
            self._append_event(run, "warning", f"Apague manualmente el spindle antes de continuar con {next_operation['name']}.")
        self._save_run(context, run)

    def _perform_tool_change_transition(self, context: JobContext, run: dict[str, Any]) -> None:
        adapter = self._motion_adapter(context)
        current_index = int(run.get("current_operation_index", 0) or 0)
        next_index = current_index + 1
        if next_index >= len(run.get("operations") or []):
            raise ApplicationError("No existe una siguiente operación para completar el cambio de herramienta.")
        current_operation = run["operations"][current_index]
        next_operation = run["operations"][next_index]
        self._require_spindle_confirmation(context, run, "stopped", next_index)
        outgoing_profile = str(current_operation.get("tool_reference_profile") or "standard")
        outgoing_clearance_z = self._tool_change_clearance_z(outgoing_profile)
        try:
            run["state"] = "RETRACTING"
            run["next_action"] = "Subiendo a Z segura para cambio de herramienta"
            run["available_actions"] = ["cancel"]
            run["outgoing_tool_change_profile"] = outgoing_profile
            run["outgoing_tool_change_clearance_z_mm"] = outgoing_clearance_z
            self._append_event(
                run,
                "info",
                "Spindle detenido confirmado. Subiendo la herramienta instalada "
                f"con perfil {outgoing_profile} a despeje Z {outgoing_clearance_z:.3f} mm.",
            )
            self._save_run(context, run)
            adapter.move_to_tool_change_position(tool_change_profile=outgoing_profile)
            run["last_watcher_error"] = None
            run["last_transition_error"] = None
            run["state"] = "TOOL_CHANGE_REQUIRED"
            run["next_action"] = "Cambie la herramienta y pulse Herramienta cambiada"
            run["available_actions"] = ["confirm-tool-change", "cancel"]
            self._append_event(run, "warning", f"Cambie a {next_operation['tool_name']} y confirme cuando esté instalada.")
            self._save_run(context, run)
        except Exception as error:
            safe_error = _safe_operator_error(error)
            run["state"] = "RECOVERY_REQUIRED"
            run["next_action"] = "Revise la transición y pulse Reintentar transición de herramienta"
            run["available_actions"] = ["retry-tool-change-transition", "cancel"]
            run["updated_at"] = _iso_now()
            run["last_watcher_error"] = traceback.format_exc()
            run["last_transition_error"] = {
                "code": "TOOL_CHANGE_TRANSITION_FAILED",
                "message": safe_error,
                "occurred_at": _iso_now(),
            }
            run["recovery_state"] = "RECOVERY_REQUIRED"
            self._append_event(run, "error", f"Falló la transición segura de herramienta: {safe_error}")
            self._save_run(context, run)

    def _execution_readiness(self, context, plan, run=None):
        snapshot = self.runtime.snapshot()
        ownership = self.runtime.physical_ownership.snapshot()
        observation_failed = False
        if ((snapshot.get('safety') or {}).get('telemetry_recent') is not True
                and snapshot.get('mode') == 'PHYSICAL'
                and (snapshot.get('moonraker') or {}).get('http_connected')
                and snapshot.get('state') not in {'DISCONNECTED', 'ERROR', 'STOPPED'}
                and not snapshot.get('recovery_pending')
                and not ownership.get('recovery_pending')
                and ownership_compatible(plan, ownership)
                and not snapshot.get('active_operation')):
            try:
                snapshot = self.runtime.refresh_observed_state()
            except Exception:
                # Preserve current Klipper/recovery evidence; a concurrent WS
                # update must not hide a failed HTTP observation.
                snapshot = self.runtime.snapshot()
                observation_failed = True
        return evaluate_readiness(plan, run, snapshot,
                                  self.runtime.physical_ownership.snapshot(),
                                  self.runtime.current_physical_session_id(),
                                  observation_failed=observation_failed)

    def inspect_execution(self, *, project_id, setup_id, face):
        """No workflow, store writes, physical commands or ownership acquisition.

        Puede realizar observación HTTP read-only del estado físico.
        """
        context = self._context(project_id, setup_id, face)
        plan = self._resolved_plan(context, publish=False)
        run = self.run_store.load(self._existing_run_file(context))
        readiness = self._execution_readiness(context, plan, run)
        # Presentation only; the cached/published plan remains immutable.
        current_id = readiness['current_operation']['operation_id']
        seen_current = False
        after_change = False
        previous_key = None
        for row in plan['operations']:
            if row['operation_id'] == current_id:
                seen_current = True
                row['reference_display'] = ('Lista — herramienta instalada' if readiness['reference']['valid'] else readiness['reference']['message'])
            elif seen_current:
                after_change = after_change or row['tool_key'] != previous_key
                row['reference_display'] = ('Se medirá después del cambio' if after_change
                                            else 'Reutiliza instalación actual')
            else:
                row['reference_display'] = row['reference_diagnostic']['status']
            previous_key = row['tool_key']
        return {'plan': plan, 'readiness': readiness}

    def _build_run_checks(self, context, plan):
        return self._execution_readiness(context, plan)['checks']

    def _build_plan(self, context: JobContext, generated_results: dict[str, dict[str, Any]] | None = None, *, project=None) -> dict[str, Any]:
        project = project if project is not None else self._load_project(context.project_id)
        setup = project.get_setup(context.setup_id)
        operations = sorted(
            [item for item in project.operations_for_setup(context.setup_id) if str(item.cara) == str(context.face)],
            key=lambda item: item.orden,
        )
        if not operations:
            raise ApplicationError("No existen operaciones activas para este montaje/cara.")
        from klipper_cnc_assistant.gcode import analyze_gcode_text
        from klipper_cnc_assistant.gcode.analysis_cache import analysis_is_current
        missing_sources = set()
        for index, operation in enumerate(operations):
            if operation.archivo_gcode:
                try:
                    original, source_sha256 = self.repository.read_gcode_source(context.project_id, operation.archivo_gcode)
                except OSError:
                    missing_sources.add(operation.id)
                    operations[index] = replace(operation, analisis=None)
                    continue
                if operation.analisis is not None and not analysis_is_current(operation, original, source_sha256=source_sha256):
                    operations[index] = replace(operation, analisis=analyze_gcode_text(original, material=project.material))
        active_map = self._load_active_map(context.project_id, operations[0].id, project=project)
        coverage_by_operation = self._coverage_by_operation(active_map, operations)
        generated_by_operation = self._latest_generated_by_operation(context.project_id, operations, active_map, project=project)
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
            generated_metadata = generated.get("metadata") if isinstance(generated, dict) else None
            coverage = coverage_by_operation.get(operation.id)
            reference_diagnostic = self._reference_diagnostic(active_map, operation, binding, project=project)
            reference_status = 'LISTA' if reference_diagnostic['valid'] else 'REQUIERE_REFERENCIA'
            calibration = self._tool_installation_calibration(active_map, operation, binding)
            blocking_reasons: list[str] = []
            original_time_estimate: dict[str, Any] | None = None
            if operation.archivo_gcode is None or operation.id in missing_sources:
                blocking_reasons.append("Falta G-code original.")
            if operation.analisis is None:
                blocking_reasons.append("Falta análisis G-code.")
            if operation.analisis is not None:
                try:
                    self.compensated_gcode_service._validate_legacy_analysis(operation)
                except ApplicationError as error:
                    blocking_reasons.append(str(error))
            if active_map is None:
                blocking_reasons.append("Falta mapa físico activo.")
            else:
                try:
                    self.compensated_gcode_service._validate_map_for_operation(active_map, operation, require_tool_reference=False)
                except ApplicationError as error:
                    blocking_reasons.append(str(error))
                if (active_map.get('validation') or {}).get('status') != 'VALID':
                    blocking_reasons.append('Mapa sin validación vigente.')
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
                    "tool_reference_profile": str(operation.tool_reference_profile),
                    "tool_change_clearance_z_mm": self._tool_change_clearance_z(str(operation.tool_reference_profile)),
                    "reference_prep_z_mm": self._reference_preparation_z(str(operation.tool_reference_profile)),
                    "z_clearance_feed_mm_min": self._z_clearance_feed(),
                    "reference_approach_z_feed_mm_min": self._reference_approach_z_feed(),
                    "reference_probe_feed_mm_min": self._reference_probe_feed(
                        None if active_map is None else active_map.get("probe_config")
                    ),
                    "tool_changed": tool_changed,
                    "map_status": "LISTO" if active_map and active_map.get("status") == "MESH_COMPLETE" and (active_map.get("validation") or {}).get("status") == "VALID" else "PENDIENTE",
                    "coverage_status": "VALIDA" if coverage is None or coverage["sufficient"] else "FUERA_DE_DOMINIO",
                    "coverage_detail": None if coverage is None or coverage["sufficient"] else blocking_reasons[-1],
                    "reference_status": reference_status,
                    "reference_error": None if reference_diagnostic["valid"] else reference_diagnostic["message"],
                    "reference_diagnostic": reference_diagnostic,
                    "tool_installation_calibration": calibration,
                    "generated_file": None if generated is None else generated["relative_path"],
                    "generated_file_name": None if generated is None else Path(str(generated["relative_path"])).name,
                    "generated_metadata_path": None if generated is None else generated.get("metadata_path"),
                    "time_estimate": None if generated is None else generated.get("time_estimate"),
                    "estimated_time_s": None if generated is None else (generated.get("time_estimate") or {}).get("estimated_time_s"),
                    "original_time_estimate": original_time_estimate,
                    "original_estimated_time_s": None if original_time_estimate is None else original_time_estimate.get("estimated_time_s"),
                    "source_file_hash": None if generated_metadata is None else generated_metadata.get("original_hash"),
                    "generated_file_hash": None if generated_metadata is None else generated_metadata.get("generated_hash"),
                    "compensation_mode": None if generated_metadata is None else generated_metadata.get("compensation_mode", "legacy"),
                    "algorithm_version": None if generated_metadata is None else generated_metadata.get("algorithm_version"),
                    "max_z_error_mm": operation.max_z_error_mm,
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
                "original_estimated_time_s": sum(float(item.get("original_estimated_time_s") or 0.0) for item in operation_rows),
                "estimated_time_s": sum(float(item.get("estimated_time_s") or 0.0) for item in operation_rows),
            },
            # An in-memory inspection is not a published plan/manifest generation.
            "manifest_path": None,
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
                    "tool_reference_profile": item["tool_reference_profile"],
                    "tool_change_clearance_z_mm": item["tool_change_clearance_z_mm"],
                    "reference_prep_z_mm": item["reference_prep_z_mm"],
                    "z_clearance_feed_mm_min": item["z_clearance_feed_mm_min"],
                    "reference_approach_z_feed_mm_min": item["reference_approach_z_feed_mm_min"],
                    "reference_probe_feed_mm_min": item["reference_probe_feed_mm_min"],
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
        run_id = f"job-run/{context.setup_id}/{_safe_face(context.face)}/{_utc_now().strftime('%Y%m%d-%H%M%S-%f')}"
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
                    "tool_reference_profile": item["tool_reference_profile"],
                    "tool_change_clearance_z_mm": item["tool_change_clearance_z_mm"],
                    "reference_prep_z_mm": item["reference_prep_z_mm"],
                    "z_clearance_feed_mm_min": item["z_clearance_feed_mm_min"],
                    "reference_approach_z_feed_mm_min": item["reference_approach_z_feed_mm_min"],
                    "reference_probe_feed_mm_min": item["reference_probe_feed_mm_min"],
                    "tool_changed": item["tool_changed"],
                    "reference_status": item["reference_status"],
                    "generated_file": item["generated_file"],
                    "generated_file_name": item["generated_file_name"],
                    "generated_metadata_path": item.get("generated_metadata_path"),
                    "time_estimate": item.get("time_estimate"),
                    "estimated_time_s": item.get("estimated_time_s"),
                    "original_time_estimate": item.get("original_time_estimate"),
                    "original_estimated_time_s": item.get("original_estimated_time_s"),
                    "source_file_hash": item.get("source_file_hash"),
                    "generated_file_hash": item.get("generated_file_hash"),
                    "compensation_mode": item.get("compensation_mode"),
                    "algorithm_version": item.get("algorithm_version"),
                    "max_z_error_mm": item.get("max_z_error_mm"),
                    "execution_status": "PENDING",
                    "started_at": None,
                    "completed_at": None,
                    "error": None,
                    "progress": 0.0,
                    "installation_revision": None if not item.get("tool_installation_calibration") else item["tool_installation_calibration"].get("installation_id"),
                    "physical_reference_token": None if not item.get("tool_installation_calibration") else item["tool_installation_calibration"].get("physical_reference_token"),
                }
                for item in plan["operations"]
            ],
            "summary": {
                "operations_total": plan["summary"]["operations_total"],
                "operations_completed": 0,
                "tool_changes_required": plan["summary"]["tool_changes"],
                "tool_changes_completed": 0,
                "original_estimated_time_s": sum(float(item.get("original_estimated_time_s") or 0.0) for item in plan["operations"]),
                "estimated_time_s": sum(float(item.get("estimated_time_s") or 0.0) for item in plan["operations"]),
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
            "last_transition_error": None,
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

    def _reference_diagnostic(self, active_map, operation, initial_reference_binding=None, *, project=None):
        try:
            if active_map is None:
                raise PhysicalReferenceError('Falta un mapa físico activo.', 'REFERENCE_MEASUREMENT_REQUIRED')
            entry = self._reference_entry(active_map, operation, initial_reference_binding)
            token = None if entry is None else entry.get('physical_reference_token')
            if not token:
                raise PhysicalReferenceError('Se requiere una medición con evidencia vigente para esta herramienta.', 'REFERENCE_MEASUREMENT_REQUIRED')
            require_current_reference(self.repository, self.runtime, active_map['project_id'], operation.id,
                                      active_map, project=project, expected_token=token)
        except PhysicalReferenceError as error:
            status = {'REFERENCE_SESSION_CHANGED': 'SESIÓN ANTERIOR',
                      'REFERENCE_MEASUREMENT_REQUIRED': 'REQUIERE MEDICIÓN'}.get(error.code, 'CONTEXTO CAMBIÓ')
            return {'valid': False, 'code': error.code, 'status': status, 'message': str(error)}
        return {'valid': True, 'code': 'REFERENCE_VALID', 'status': 'LISTA', 'message': 'Referencia vigente de la herramienta actual.'}

    def _reference_error(self, active_map, operation, initial_reference_binding=None, *, project=None):
        result = self._reference_diagnostic(active_map, operation, initial_reference_binding, project=project)
        return None if result['valid'] else result['message']

    def _reference_status(self, active_map, operation, initial_reference_binding=None, *, project=None):
        return "LISTA" if self._reference_error(active_map, operation, initial_reference_binding, project=project) is None else "REQUIERE_REFERENCIA"

    def _require_operation_reference(self, context, operation, *, physical_map=None, expected_token=None):
        if operation.get('remote_file') and not operation.get('physical_reference_token'):
            raise ApplicationError('La impresión existente carece de evidencia de referencia de herramienta.')
        project_operation = self.repository.load_project(context.project_id).get_operation(operation['operation_id'])
        if project_operation.setup_id != context.setup_id or str(project_operation.cara) != context.face:
            raise ApplicationError('El montaje/cara de la referencia no coincide con el JobRun.')
        try:
            return require_current_reference(
                self.repository, self.runtime, context.project_id, operation['operation_id'],
                physical_map if physical_map is not None else self.physical_map_service.get_active(context.project_id, operation['operation_id']),
                installation_id=operation.get('installation_revision'),
                expected_token=expected_token if expected_token is not None else operation.get('physical_reference_token'),
            )
        except PhysicalReferenceError as error:
            raise ApplicationError(str(error)) from error

    def _require_run_reference(self, context, run, *, next_pending=False):
        index = self._next_pending_operation_index(run) if next_pending else int(run['current_operation_index'])
        if index is None:
            raise ApplicationError('No existe una operación pendiente con referencia ejecutable.')
        return self._require_operation_reference(context, reference_candidate(run, index))

    def _spindle_confirmation_context(self, context, operation):
        physical_map = self.physical_map_service.get_active(context.project_id, operation['operation_id'])
        return reference_context(self.repository, self.runtime, context.project_id,
                                 operation['operation_id'], physical_map, operation.get('installation_revision'))

    def _record_spindle_confirmation(self, context, run, kind, index):
        operation = run['operations'][index]
        run.setdefault('spindle_confirmations', {})[kind] = {
            'source': 'human', 'kind': kind, 'run_id': run['run_id'],
            'operation_id': operation['operation_id'], 'operation_index': index,
            'confirmed_at': _iso_now(),
            'context': self._spindle_confirmation_context(context, operation),
        }

    def _require_spindle_confirmation(self, context, run, kind, index):
        proof = (run.get('spindle_confirmations') or {}).get(kind)
        if not proof or proof.get('source') != 'human' or proof.get('run_id') != run['run_id']:
            raise ApplicationError('Se requiere una confirmación humana de spindle vigente.')
        source = proof.get('operation_index')
        if not isinstance(source, int) or source < 0 or source >= len(run['operations']):
            raise ApplicationError('La confirmación de spindle no identifica una operación vigente.')
        if proof.get('operation_id') != run['operations'][source]['operation_id']:
            raise ApplicationError('La operación confirmada de spindle cambió.')
        if source != index:
            # Continuous same-tool operations may retain the original human proof.
            if kind != 'prepared' or source > index or any(
                op['tool_key'] != run['operations'][index]['tool_key'] or op['execution_status'] != 'COMPLETED'
                for op in run['operations'][source:index]
            ):
                raise ApplicationError('La confirmación humana no corresponde a esta operación/herramienta.')
        if proof.get('context') != self._spindle_confirmation_context(context, run['operations'][index]):
            raise ApplicationError('La sesión o contexto cambió; confirme nuevamente el spindle.')

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
            "physical_reference_token": reference.get("physical_reference_token"),
        }

    def _reference_entry(self, active_map, operation, initial_reference_binding=None):
        if active_map is None:
            return None
        key = _tool_key(operation)
        entry = (active_map.get("tool_references") or {}).get(key)
        if initial_reference_binding is not None and initial_reference_binding.get("tool_key") == key:
            # A binding can select an identical canonical entry, never retarget it.
            if (initial_reference_binding.get("source_tool_key") != key
                    or initial_reference_binding.get("reference") != entry):
                return None
        return entry if isinstance(entry, dict) else None

    def _initial_reference_binding(self, active_map, setup, operations):
        if active_map is None or not operations:
            return None
        key = _tool_key(operations[0])
        entry = (active_map.get("tool_references") or {}).get(key)
        if not isinstance(entry, dict) or not entry.get("valid"):
            return None
        token = entry.get("physical_reference_token")
        if not isinstance(token, dict) or token.get("tool_id") != key:
            return None
        if setup.active_reference_id not in (None, entry.get("installation_id")):
            return None
        return {"tool_key": key, "source_tool_key": key, "reference": entry,
                "reference_revision": token.get("reference_revision")}

    def _generated_payload_for_operation(self, plan: dict[str, Any], operation_id: str) -> dict[str, Any] | None:
        row = next((item for item in plan["operations"] if item["operation_id"] == operation_id), None)
        if row is None or not row.get("generated_file"):
            return None
        return {
            "relative_path": row["generated_file"],
            "metadata_path": row.get("generated_metadata_path"),
            "time_estimate": row.get("time_estimate"),
        }

    def _latest_generated_by_operation(
        self,
        project_id: str,
        operations: list[OperacionPCB],
        active_map: dict[str, Any] | None,
        *, project=None,
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
                if metadata.get("executable") is False:
                    continue
                if not self._generated_artifact_is_current(project_id, operation, active_map, file_path, metadata, project=project):
                    continue
                results[operation.id] = {
                    "relative_path": self._relative_to_project(project_id, file_path),
                    "metadata_path": self._relative_to_project(project_id, metadata_path),
                    "plan_hash": metadata.get("generated_hash"),
                    "time_estimate": metadata.get("time_estimate"),
                    "metadata": metadata,
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
        *, project=None,
    ) -> bool:
        if active_map is None or metadata.get("operation_id") != operation.id:
            return False
        try:
            require_current_reference(self.repository, self.runtime, project_id, operation.id, active_map,
                                      expected_token=metadata.get('physical_reference_token'), project=project)
            if not metadata.get('physical_reference_token'):
                return False
        except PhysicalReferenceError:
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
        if metadata.get("executable") is False:
            return False
        if metadata.get("tool_id") != _tool_key(operation):
            return False
        metadata_mode = str(metadata.get("compensation_mode") or "legacy")
        if metadata_mode != "legacy":
            return False
        reference = (active_map.get("tool_references") or {}).get(_tool_key(operation))
        if isinstance(reference, dict) and reference.get("valid"):
            actual_z = (metadata.get("reference_frame") or {}).get("surface_reference_z_mm")
            if actual_z is None or abs(float(actual_z) - float(reference["reference_z"])) > 1e-9:
                return False
        setup = (project if project is not None else self.repository.load_project(project_id)).get_setup(operation.setup_id)
        current_fingerprint = self.compensated_gcode_service._build_artifact_fingerprint(
            operation_id=operation.id,
            original_hash=hashlib.sha256(original.encode("utf-8")).hexdigest(),
            map_hash=active_map_hash,
            reference_frame=self.compensated_gcode_service._reference_frame(active_map, operation),
            compensation_mode=metadata_mode,
            max_z_error_mm=float(operation.max_z_error_mm),
            algorithm_version=str(metadata.get("algorithm_version") or ""),
            placement_revision=setup.placement_revision,
        )
        if metadata.get("audit_fingerprint") != current_fingerprint:
            return False
        return True

    def _load_active_map(self, project_id: str, operation_id: str, *, project=None) -> dict[str, Any] | None:
        try:
            return self.physical_map_service.get_active(project_id, operation_id, project=project)
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
        if run.get('state') in JobRunStore.CANCELLED:
            return None
        try:
            status = self.adapter_factory(self.runtime).print_status()
            PrintIdentity.require_status(status)
        except Exception as error:
            self._identity_failure(context, run, error)
            raise PrintIdentityError('No se pudo identificar el archivo Moonraker.') from error
        if str(status.get("state") or "").lower() not in {'printing', 'paused'}:
            PrintIdentity.require_idle(status)
            return None
        operations = list(run.get("operations") or [])
        candidate_index = operation_index if operation_index is not None else run.get('current_operation_index')
        if isinstance(candidate_index, int) and 0 <= candidate_index < len(operations):
            operation = operations[candidate_index]
            candidate_remote = expected_remote_file or operation.get("remote_file")
            if not candidate_remote and operation.get("generated_file"):
                candidate_remote = self._expected_remote_file(context, str(operation["generated_file"]))
            try:
                PrintIdentity(candidate_remote).require_match(status)
                self._require_operation_reference(context, operation)
            except (PrintIdentityError, ApplicationError) as error:
                self._identity_failure(context, run, error)
                return None
            operation["remote_file"] = candidate_remote
            operation["execution_status"] = "PAUSED" if status['state'] == 'paused' else "RUNNING"
            operation["observed_printing"] = status['state'] == 'printing' or bool(operation.get('observed_printing'))
            operation["progress"] = self._clamp_progress(status.get("progress"))
            operation["moonraker_filename"] = status.get("filename")
            operation["moonraker_state"] = status.get("state")
            operation["machine_status"] = status
            operation["started_at"] = operation.get("started_at") or _iso_now()
            run["current_operation_index"] = candidate_index
            run["current_operation_id"] = operation["operation_id"]
            run["current_tool_key"] = operation["tool_key"]
            run["state"] = "OPERATION_PAUSED" if status['state'] == 'paused' else "OPERATION_RUNNING"
            run["next_action"] = f"RECOVERED_ACTIVE_PRINT · Ejecutando {operation['name']}"
            run["available_actions"] = ["resume", "cancel"] if status['state'] == 'paused' else ["pause", "cancel"]
            run["updated_at"] = _iso_now()
            run["recovery_state"] = "RECOVERED_ACTIVE_PRINT"
            self._append_event(run, "warning", f"RECOVER_ACTIVE_PRINT: se recupero la impresion activa de {operation['name']} sin re-subir el archivo.")
            self._save_run(context, run)
            return run
        if str(status.get('state')) in {'printing', 'paused'}:
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
        if not setup_id or setup_id in {".", ".."} or "/" in setup_id or "\\" in setup_id:
            raise ApplicationError("Identificador de montaje inválido.")
        try:
            normalized_face = BoardFace(face).value
        except ValueError as error:
            raise ApplicationError("Cara de montaje inválida.") from error
        return JobContext(project_id=project_id, setup_id=setup_id, face=normalized_face)

    def _validate_execution_target(self, context: JobContext) -> None:
        try:
            _, payload = self._project_dependency_metadata(context.project_id)
        except FileNotFoundError as error:
            raise NotFoundError(f"El proyecto '{context.project_id}' no existe.") from error
        setups = payload.get("montajes") if isinstance(payload, dict) else None
        if not isinstance(setups, list):
            raise ValueError("project.json no contiene una lista válida de montajes.")
        if not any(isinstance(item, dict) and item.get("id") == context.setup_id for item in setups):
            raise NotFoundError(f"El montaje '{context.setup_id}' no existe en el proyecto.")

    @staticmethod
    def _validate_run_context(context: JobContext, run: dict[str, Any]) -> None:
        expected = {
            "project_id": context.project_id,
            "setup_id": context.setup_id,
            "face": context.face,
        }
        mismatches = [
            field
            for field, value in expected.items()
            if str(run.get(field) or "") != value
        ]
        if mismatches:
            raise ApplicationError(
                "current_run.json no corresponde a la ruta solicitada: "
                + ", ".join(sorted(mismatches))
                + "."
            )

    def _reference_preparation_z(self, profile="standard") -> float:
        resolver = getattr(self.runtime, "reference_preparation_z", None)
        if callable(resolver):
            return float(resolver(profile))
        config = getattr(self.runtime, "config", None)
        return float(getattr(config, "reference_prep_z_mm", 115.0))

    def _tool_change_clearance_z(self, tool_change_profile: str) -> float:
        resolver = getattr(self.runtime, "tool_change_clearance_z", None)
        if callable(resolver):
            return float(resolver(tool_change_profile))
        config = getattr(self.runtime, "config", None)
        if tool_change_profile == "long_tool":
            return float(
                getattr(
                    config,
                    "long_tool_change_clearance_z_mm",
                    getattr(config, "tool_change_clearance_z_mm", 115.0),
                )
            )
        return float(getattr(config, "tool_change_clearance_z_mm", 115.0))

    def _z_clearance_feed(self) -> float:
        config = getattr(self.runtime, "config", None)
        return float(getattr(config, "z_clearance_feed_mm_min", 180.0))

    def _reference_approach_z_feed(self) -> float:
        config = getattr(self.runtime, "config", None)
        return float(getattr(config, "reference_approach_z_feed_mm_min", 180.0))

    def _reference_probe_feed(self, probe_config: dict[str, Any] | None) -> float:
        resolver = getattr(self.runtime, "effective_probe_profile_payload", None)
        if callable(resolver):
            profile = resolver(probe_config)
            value = profile.get("effective_probe_feed_mm_min")
            if value is not None:
                return float(value)
        config = getattr(self.runtime, "config", None)
        return float(getattr(config, "probe_lower_speed_mm_s", 1.0)) * 60.0

    def _setup_run_files(self, project_id: str, setup_id: str) -> list[tuple[JobContext, Path]]:
        base = self._project_dir(project_id) / "reports" / "jobs" / setup_id
        faces = {BoardFace.SUPERIOR.value, BoardFace.INFERIOR.value}
        if base.exists():
            faces.update(path.name for path in base.iterdir() if path.is_dir())
        return [
            (self._context(project_id, setup_id, face), base / _safe_face(face) / "current_run.json")
            for face in sorted(faces)
        ]

    def _current_runs_for_setup(self, project_id: str, setup_id: str) -> list[tuple[JobContext, Path, dict[str, Any]]]:
        current: list[tuple[JobContext, Path, dict[str, Any]]] = []
        for context, run_file in self._setup_run_files(project_id, setup_id):
            if not run_file.exists():
                continue
            current.append((context, run_file, self.run_store.load(run_file)))
        return current

    def _history_contains_run(self, context: JobContext, run_id: str) -> bool:
        if not run_id:
            return False
        history_dir = self._project_dir(context.project_id) / "reports" / "jobs" / context.setup_id / _safe_face(context.face) / "history"
        if not history_dir.exists():
            return False
        for history_file in history_dir.glob("*.json"):
            try:
                payload = json.loads(history_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if str(payload.get("run_id") or "") == run_id:
                return True
        return False

    def _assert_preparation_reset_safe(self) -> dict[str, Any] | None:
        if not self._archive_ownership_clear():
            raise ApplicationError('No se puede reiniciar: ownership, productor, recuperación o movement_lock no están libres y observables.')
        snapshot = self.runtime.snapshot()
        if snapshot.get('recovery_pending') or snapshot.get('active_operation') is not None:
            raise ApplicationError('Operación física o recuperación pendiente.')
        if str(snapshot.get('state') or '').upper() in {'HOMING', 'WAITING_SAFE_Z', 'MOVING_TO_SAFE_Z', 'MOVING_TO_CENTER', 'PROBING_REFERENCE', 'MESH_PROBING', 'STOPPING'}:
            raise ApplicationError('Estado físico transitorio; reinicio rechazado.')
        with self._lock:
            if any(thread.is_alive() for thread in self._threads.values()):
                raise ApplicationError('Supervisor activo; reinicio rechazado.')
        if self.mesh_execution_service is not None and self.mesh_execution_service.active_execution_snapshot().get('active'):
            raise ApplicationError('Worker de malla activo; reinicio rechazado.')
        if str(snapshot.get('mode', '')).upper() == 'SIMULATED':
            return
        try:
            remote = self.adapter_factory(self.runtime).print_status()
        except Exception as error:
            raise ApplicationError('No se pudo observar Moonraker; no se modifica la preparación.') from error
        if not self._moonraker_is_idle({
                'connected': remote.get('connected'), 'klipper_state': remote.get('klipper_state'),
                'print_state': remote.get('state'),
                'is_active': remote.get('is_active_observed')}):
            raise ApplicationError('Se requiere Moonraker conectado, Klipper ready e is_active observado false; shutdown/estado desconocido bloquean.')
        if not set('xyz').issubset(str(remote.get('homed_axes_observed') or '')):
            raise ApplicationError('Falta homing XYZ observado; reset rechazado.')
        speed = self._observed_speed(remote.get('live_velocity'))
        tolerance = float(self.runtime.config.velocity_tolerance_mm_s)
        if speed is None or not math.isfinite(speed) or speed > tolerance:
            raise ApplicationError('Velocidad remota ausente, inválida o movimiento activo; reset rechazado.')
        return remote

    def _observed_speed(self, value: Any) -> float | None:
        if isinstance(value, dict):
            components = value.values()
        elif isinstance(value, (list, tuple)):
            components = value
        else:
            components = (value,)
        numeric: list[float] = []
        for component in components:
            try:
                parsed = float(component)
            except (TypeError, ValueError):
                return None
            if not math.isfinite(parsed):
                return None
            numeric.append(parsed)
        return math.sqrt(sum(component * component for component in numeric)) if numeric else None

    def _load_or_build_plan(self, context: JobContext) -> dict[str, Any]:
        return self._resolved_plan(context, publish=True)

    def _project_dependency_metadata(self, project_id):
        """Hash fresh bytes on every read; reuse only compact, verified metadata.

        Avoid rehydrating a large analysis merely to verify setup existence or
        plan dependencies. No mtime trust and no retained G-code/analysis graph.
        """
        path = self._project_dir(project_id) / "project.json"
        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        with self._lock:
            cached = self._project_dependency_cache.get(project_id)
            if cached is not None and cached[0] == digest:
                self._project_dependency_cache.move_to_end(project_id)
                return digest, deepcopy(cached[1])
        raw = json.loads(content)
        metadata = {"storage_revision": raw.get("storage_revision", 0),
                    "montajes": raw.get("montajes"),
                    "operaciones": [{k: op.get(k) for k in ("id", "setup_id", "cara", "archivo_gcode")}
                                    for op in raw.get("operaciones", [])]}
        with self._lock:
            self._project_dependency_cache[project_id] = (digest, metadata)
            self._project_dependency_cache.move_to_end(project_id)
            while len(self._project_dependency_cache) > 8:
                self._project_dependency_cache.popitem(last=False)
        return digest, deepcopy(metadata)

    def _plan_dependencies(self, context):
        """Content identity, never mtime: includes source, map, evidence and artifacts."""
        from klipper_cnc_assistant.gcode import CURRENT_ANALYSIS_VERSION
        root = self._project_dir(context.project_id)
        project_digest, raw = self._project_dependency_metadata(context.project_id)
        setup = next((s for s in raw["montajes"] if s["id"] == context.setup_id), None)
        if setup is None:
            raise NotFoundError("El montaje ya no existe.")
        dependencies = {"version": 2, "project_content": project_digest,
                        "storage_revision": raw.get("storage_revision", 0), "face": context.face,
                        "setup": setup, "analysis_version": CURRENT_ANALYSIS_VERSION,
                        "algorithms": [CompensatedGCodeService.LEGACY_ALGORITHM_VERSION,
                                       CompensatedGCodeService.ADAPTIVE_ALGORITHM_VERSION], "files": {}}
        for op in raw["operaciones"]:
            if op.get("setup_id") != context.setup_id or str(op["cara"]) != context.face:
                continue
            path = op.get("archivo_gcode")
            if path:
                try:
                    _, dependencies["files"][path] = self.repository.read_gcode_source(context.project_id, path)
                except OSError:
                    dependencies["files"][path] = None
        map_id = setup.get("active_map_id")
        if map_id:
            try:
                payload = self.repository.load_height_map_payload(context.project_id, map_id)
                dependencies["map"] = payload
            except FileNotFoundError:
                dependencies["map"] = None
        else:
            # Legacy fallback discovery participates in identity as well.
            dependencies["maps"] = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                                    for p in sorted((root / "maps").glob("**/height_map.json"))}
        generated = root / "generated" / "compensated"
        dependencies["artifacts"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                     for p in sorted(generated.glob("*")) if p.is_file() and p.suffix in {".gcode", ".json"}}
        config = self.runtime.config
        dependencies["config"] = {k: getattr(config, k) for k in dir(config)
                                  if not k.startswith("_") and not callable(getattr(config, k))}
        snapshot = self.runtime.snapshot()
        dependencies["machine"] = {k: snapshot.get("klipper", {}).get(k)
                                    for k in ("limits", "max_velocity", "max_z_velocity", "max_accel")}
        session = getattr(self.runtime, "current_physical_session_id", None)
        dependencies["physical_session"] = session() if callable(session) else None
        dependencies["ownership_session"] = self.runtime.physical_ownership.session
        dependencies["reference_recovery_pending"] = self.runtime.physical_ownership.snapshot()["recovery_pending"]
        return hashlib.sha256(json.dumps(dependencies, sort_keys=True, default=str, allow_nan=False).encode()).hexdigest()

    @staticmethod
    def _plan_digest(plan):
        return hashlib.sha256(json.dumps({k: v for k, v in plan.items()
            if k not in {"plan_content_hash", "generation_id", "manifest_path"}},
            sort_keys=True, default=str, allow_nan=False).encode()).hexdigest()

    def _resolved_plan(self, context, *, publish):
        fingerprint = self._plan_dependencies(context)
        key = (context.project_id, context.setup_id, context.face)
        with self._lock:
            cached = deepcopy(self._plan_cache.get(key))
        if cached is None or cached.get("dependency_fingerprint") != fingerprint:
            cached = self._load_plan(context)
        if (cached is not None and cached.get("dependency_fingerprint") == fingerprint
                and cached.get("plan_content_hash") == self._plan_digest(cached)):
            plan = cached
        else:
            plan = self._build_plan(context)
            if fingerprint != self._plan_dependencies(context):
                raise ApplicationError("El contexto cambió durante la preparación del plan; repita la solicitud.")
            plan["dependency_fingerprint"] = fingerprint
            plan["plan_content_hash"] = self._plan_digest(plan)
        if publish and not plan.get("generation_id"):
            self._save_plan(context, plan)
        with self._lock:
            self._plan_cache[key] = deepcopy(plan)
            self._plan_cache.move_to_end(key)
            while len(self._plan_cache) > 8:
                self._plan_cache.popitem(last=False)
        return plan

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

    def _plan_file(self, context: JobContext) -> Path:
        return self._plan_dir(context) / "job_plan.json"

    def _run_file(self, context: JobContext) -> Path:
        return self._plan_dir(context) / "current_run.json"

    def _existing_run_file(self, context: JobContext) -> Path:
        """Return the read path without creating report directories."""
        return (
            self._project_dir(context.project_id)
            / "reports"
            / "jobs"
            / context.setup_id
            / _safe_face(context.face)
            / "current_run.json"
        )

    def _load_plan(self, context: JobContext) -> dict[str, Any] | None:
        path = self._project_dir(context.project_id) / "reports" / "jobs" / context.setup_id / _safe_face(context.face) / "job_plan.json"
        if not path.exists():
            return None
        with storage_lock(path):
            if not path.exists():
                return None
            plan = json.loads(path.read_text(encoding="utf-8"))
            manifest_path = self._plan_dir(context) / "job_manifest.json"
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                validate_generation(plan, manifest)
            except (FileNotFoundError, PersistenceConflict):
                return None  # Rebuild legacy/incomplete pairs instead of combining them.
            return plan

    def _save_plan(self, context: JobContext, plan: dict[str, Any]) -> None:
        plan["generation_id"] = uuid4().hex
        manifest_path = self._plan_dir(context) / "job_manifest.json"
        plan["manifest_path"] = self._relative_to_project(context.project_id, manifest_path)
        manifest = self._build_manifest(plan)
        manifest["generation_id"] = plan["generation_id"]
        validate_generation(plan, manifest)
        manifest_content = json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True)
        plan_content = json.dumps(plan, ensure_ascii=True, indent=2, sort_keys=True)
        # Build outside the lock; publish both under the same reader/writer lock.
        with storage_lock(self._plan_file(context)):
            atomic_write(manifest_path, manifest_content)
            atomic_write(self._plan_file(context), plan_content)

    def _write_manifest(self, context: JobContext, plan: dict[str, Any]) -> None:
        # Publication belongs exclusively to _save_plan, including its generation.
        plan["manifest_path"] = self._relative_to_project(context.project_id, self._plan_dir(context) / "job_manifest.json")

    def _load_run(self, context: JobContext) -> dict[str, Any] | None:
        return self.run_store.load(self._existing_run_file(context))

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
        try:
            return PrintIdentity(value).filename
        except PrintIdentityError:
            return ""

    def _clamp_progress(self, value: Any) -> float:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(1.0, numeric))

    def _save_run(self, context: JobContext, run: dict[str, Any]) -> None:
        path = self._run_file(context)
        if "revision" not in run:
            saved = self.run_store.create(path, run)
        else:
            saved = self.run_store.save(path, run)
        run.update(revision=saved['revision'], cancellation_epoch=saved['cancellation_epoch'])

    def _archive_run(self, context: JobContext, run: dict[str, Any]) -> None:
        self.run_store.validate(self._run_file(context), run, allow_cancelled=True)
        archived = json.loads(json.dumps(run))
        self._prepare_history_snapshot(archived)
        self._write_archived_run(context, archived)

    def _write_archived_run(self, context: JobContext, run: dict[str, Any], *, suffix: str | None = None) -> Path:
        stamp = _utc_now().strftime("%Y%m%d-%H%M%S")
        base = str(run["run_id"]).replace("/", "_")
        filename = f"{base}__{suffix}_{stamp}.json" if suffix else f"{base}__{stamp}.json"
        history_file = self._history_dir(context) / filename
        atomic_json(history_file, run, durable=True)
        return history_file

    def _prepare_history_snapshot(self, run: dict[str, Any]) -> None:
        operations = list(run.get("operations") or [])
        entries: list[dict[str, Any]] = []
        original_total = 0.0
        compensated_total = 0.0
        actual_total = 0.0
        actual_present = False
        for operation in operations:
            original_estimated = operation.get("original_estimated_time_s")
            compensated_estimated = operation.get("estimated_time_s")
            actual_duration = operation.get("actual_print_duration_s")
            if original_estimated is not None:
                original_total += float(original_estimated)
            if compensated_estimated is not None:
                compensated_total += float(compensated_estimated)
            if actual_duration is not None:
                actual_total += float(actual_duration)
                actual_present = True
            estimation_error_pct = None
            if actual_duration is not None and compensated_estimated not in {None, 0}:
                estimation_error_pct = ((float(actual_duration) - float(compensated_estimated)) / float(compensated_estimated)) * 100.0
            entries.append(
                {
                    "project_id": run.get("project_id"),
                    "operation_id": operation.get("operation_id"),
                    "map_id": run.get("active_map_id"),
                    "source_file_hash": operation.get("source_file_hash"),
                    "generated_file_hash": operation.get("generated_file_hash"),
                    "compensation_mode": operation.get("compensation_mode") or "legacy",
                    "algorithm_version": operation.get("algorithm_version"),
                    "max_z_error_mm": operation.get("max_z_error_mm"),
                    "original_estimated_time_s": original_estimated,
                    "compensated_estimated_time_s": compensated_estimated,
                    "actual_print_duration_s": actual_duration,
                    "estimation_error_pct": estimation_error_pct,
                    "calibration_factor": None,
                    "status": operation.get("execution_status"),
                    "created_at": operation.get("started_at") or run.get("started_at") or run.get("updated_at"),
                    "completed_at": operation.get("completed_at") or run.get("completed_at"),
                }
            )
        calibration_factor = run.get("eta_ratio_ema")
        if calibration_factor is None and actual_present and compensated_total > 0:
            calibration_factor = actual_total / compensated_total
        run["history_entries"] = entries
        run["history_summary"] = {
            "original_estimated_time_s": original_total,
            "compensated_estimated_time_s": compensated_total,
            "actual_print_duration_s": actual_total if actual_present else None,
            "estimation_error_pct": None if not actual_present or compensated_total <= 0 else ((actual_total - compensated_total) / compensated_total) * 100.0,
            "calibration_factor": calibration_factor,
            "status": run.get("state"),
            "created_at": run.get("started_at") or run.get("updated_at"),
            "completed_at": run.get("completed_at"),
        }
        if calibration_factor is not None:
            for entry in run["history_entries"]:
                entry["calibration_factor"] = calibration_factor

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
            "total_duration": None,
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
                "connected": status.get("connected") is True,
                "webhooks_state": status.get("klipper_state"),
                "klipper_state": status.get("klipper_state"),
                "print_state": status.get("state"),
                "filename": status.get("filename"),
                "progress": self._clamp_progress(status.get("progress")),
                "is_active": status.get("is_active_observed", status.get("is_active", status.get("active"))),
                "file_position": status.get("file_position"),
                "file_size": status.get("file_size"),
                "print_duration": status.get("print_duration"),
                "total_duration": status.get("total_duration"),
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
            state = lock.locked()
            return state if isinstance(state, bool) else None
        except Exception:
            return None

    def _archive_ownership_clear(self) -> bool:
        # Absence of a supervisor is not proof that the workflow retired its
        # producer. Archiving must never orphan a retained physical lease.
        try:
            owner = self.runtime.physical_ownership.snapshot()
        except Exception:
            return False
        if not isinstance(owner, dict):
            return False
        return (owner.get("kind") == "IDLE" and owner.get("active") is False and owner.get("recovery_pending") is False
                and owner.get("producer_active") is False and owner.get("job_updates", 0) == 0
                and owner.get("children") == 0 and owner.get("dispatching") == 0
                and self._movement_lock_state() is False
                and getattr(self.runtime, "_active_operation", None) is None)

    def _moonraker_is_idle(self, status: dict[str, Any]) -> bool:
        return (status.get("connected") is True and status.get("klipper_state") == "ready"
                and status.get("print_state") in {"standby", "complete", "cancelled", "error"}
                and status.get("is_active") is False)

    def _available_recovery_actions(self, diagnosis: dict[str, Any], run: dict[str, Any]) -> list[str]:
        actions = ["open"]
        if diagnosis["moonraker"]["print_state"] == "printing" or diagnosis["moonraker"]["is_active"]:
            actions.append("recover")
        if self._archive_ownership_clear() and self._moonraker_is_idle(diagnosis["moonraker"]) and self._is_stale_run(diagnosis["run"], str(run.get("state") or "")):
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

    def _merge_generated_metadata(self, project_id: str, metadata_path: str, updates: dict[str, Any]) -> None:
        absolute = self.repository._resolve_project_file(project_id, metadata_path)
        patch_metadata(absolute, updates)

    def _validate_generated_pair(self, context: JobContext, generated: dict[str, Any]) -> None:
        metadata = generated['metadata']
        operation = self.repository.load_project(context.project_id).get_operation(metadata['operation_id'])
        _, source_hash = self.repository.read_gcode_source(context.project_id, operation.archivo_gcode)
        active_map = self.physical_map_service.get_active(context.project_id, operation.id)
        if (metadata.get('source_sha256') != source_hash
                or metadata.get('map_id') != active_map.get('map_id')
                or metadata.get('map_hash') != hashlib.sha256(json.dumps(active_map, sort_keys=True).encode('utf-8')).hexdigest()):
            raise ApplicationError('El origen G-code o mapa cambió después de generar el JIT.')
        read_artifact_metadata(
            self.repository._resolve_project_file(context.project_id, str(generated['relative_path'])),
            self.repository._resolve_project_file(context.project_id, str(generated['metadata_path'])),
            expected=generated['metadata'],
        )

    def _build_eta_snapshot(
        self,
        run: dict[str, Any],
        operation: dict[str, Any] | None,
        status: dict[str, Any],
        expected_filename: str,
        observed_filename: str,
    ) -> dict[str, Any]:
        estimate = (operation or {}).get("time_estimate") or {}
        offset_table = list(estimate.get("offset_table") or [])
        predicted_total = estimate.get("estimated_time_s")
        if (
            operation is None
            or not expected_filename
            or expected_filename != observed_filename
            or not offset_table
            or predicted_total in {None, 0}
        ):
            return {
                "available": False,
                "reason": "filename_mismatch_or_missing_estimate",
                "method": None,
                "confidence": None,
            }
        file_position = status.get("file_position")
        print_duration = status.get("print_duration")
        try:
            predicted_elapsed = self._interpolate_cumulative_seconds(offset_table, float(file_position or 0.0))
            predicted_total_s = float(predicted_total)
            actual_elapsed = float(print_duration or 0.0)
        except (TypeError, ValueError):
            return {
                "available": False,
                "reason": "invalid_eta_inputs",
                "method": None,
                "confidence": None,
            }
        ratio = float(run.get("eta_ratio_ema") or 1.0)
        print_state = str(status.get("state") or status.get("print_state") or "").lower()
        if predicted_elapsed > 0.1 and actual_elapsed >= 0.0 and print_state not in {"paused", "pausing"}:
            measured_ratio = actual_elapsed / predicted_elapsed
            ratio = max(0.25, min(4.0, ratio * 0.7 + measured_ratio * 0.3))
            run["eta_ratio_ema"] = ratio
            run["updated_at"] = _iso_now()
        remaining = max(0.0, predicted_total_s - predicted_elapsed) * ratio
        completion = _utc_now().timestamp() + remaining
        method = str(estimate.get("method") or "internal")
        if ratio != 1.0 and method != "moonraker_analysis":
            method = "calibrated"
        return {
            "available": True,
            "method": method,
            "confidence": estimate.get("confidence", "medium"),
            "detail": estimate.get("distribution_detail") or estimate.get("distribution_method"),
            "progress": self._clamp_progress(status.get("progress")),
            "elapsed_s": actual_elapsed,
            "predicted_elapsed_s": predicted_elapsed,
            "predicted_total_s": predicted_total_s,
            "remaining_s": remaining,
            "completion_at": datetime.fromtimestamp(completion, tz=timezone.utc).isoformat(),
            "calibration_factor": ratio,
            "filename": observed_filename,
            "state": status.get("print_state"),
        }

    def _interpolate_cumulative_seconds(self, offset_table: list[dict[str, Any]], file_position: float) -> float:
        previous_offset = 0.0
        previous_seconds = 0.0
        for row in offset_table:
            current_offset = float(row.get("file_byte_offset") or 0.0)
            current_seconds = float(row.get("predicted_cumulative_seconds") or 0.0)
            if file_position <= current_offset:
                if current_offset <= previous_offset:
                    return current_seconds
                progress = (file_position - previous_offset) / (current_offset - previous_offset)
                return previous_seconds + (current_seconds - previous_seconds) * max(0.0, min(1.0, progress))
            previous_offset = current_offset
            previous_seconds = current_seconds
        return previous_seconds

    def _load_project(self, project_id: str):
        try:
            return self.repository.load_project(project_id)
        except FileNotFoundError as error:
            raise NotFoundError(str(error)) from error
        except ProjectValidationError as error:
            raise ApplicationError(str(error)) from error
