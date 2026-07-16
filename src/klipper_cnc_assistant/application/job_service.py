from __future__ import annotations

import json
import threading
import time
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
RUN_WAITING_STATES = {"WAITING_TOOL_CHANGE", "TOOL_CHANGE_CONFIRMED", "OPERATION_PAUSED", "JOB_PAUSED"}
RUN_ACTIVE_STATES = {
    "JOB_STARTING",
    "OPERATION_PREFLIGHT",
    "OPERATION_UPLOADING",
    "OPERATION_READY",
    "OPERATION_RUNNING",
    "MOVING_TO_TOOL_CHANGE_SAFE_Z",
    "MOVING_TO_TOOL_CHANGE_XY",
    "RETURNING_TO_REFERENCE_SAFE_Z",
    "RETURNING_TO_REFERENCE_XY",
    "PROBING_TOOL_REFERENCE",
    "COMPENSATING_NEXT_OPERATIONS",
    "NEXT_OPERATION_READY",
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _utc_now().isoformat()


def _tool_key(operation: OperacionPCB) -> str:
    return operation.tool_id or (operation.herramienta or "sin-herramienta").strip().lower().replace(" ", "-")


def _safe_face(face: str) -> str:
    return str(face).strip().lower().replace(" ", "-")


@dataclass(frozen=True)
class JobContext:
    project_id: str
    setup_id: str
    face: str


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
        return self._client().upload_file(local_path=local_path, remote_dir=remote_dir)

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
                "print_stats": ["state", "filename", "message"],
                "virtual_sdcard": ["progress", "file_position", "is_active"],
                "toolhead": ["position"],
                "motion_report": ["live_position", "live_velocity"],
            }
        )
        print_stats = status.get("print_stats") or {}
        virtual_sdcard = status.get("virtual_sdcard") or {}
        motion_report = status.get("motion_report") or {}
        toolhead = status.get("toolhead") or {}
        return {
            "state": print_stats.get("state"),
            "filename": print_stats.get("filename"),
            "message": print_stats.get("message"),
            "progress": virtual_sdcard.get("progress"),
            "file_position": virtual_sdcard.get("file_position"),
            "active": virtual_sdcard.get("is_active"),
            "live_position": motion_report.get("live_position"),
            "live_velocity": motion_report.get("live_velocity"),
            "toolhead_position": toolhead.get("position"),
        }

    def move_to_tool_change_position(self) -> dict[str, Any]:
        return self.runtime.move_to_tool_change_position()

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
        manifest = self._build_manifest(refreshed)
        manifest_path = self._plan_dir(context) / "job_manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
        refreshed["manifest_path"] = self._relative_to_project(context.project_id, manifest_path)
        refreshed["updated_at"] = _iso_now()
        self._save_plan(context, refreshed)
        return refreshed

    def prepare_run(self, *, project_id: str, setup_id: str, face: str) -> dict[str, Any]:
        context = self._context(project_id, setup_id, face)
        plan = self._load_or_build_plan(context)
        checks = self._build_run_checks(context, plan)
        ready = all(check["ok"] for check in checks)
        current = self._load_run(context)
        run = self._base_run(context, plan) if current is None or current.get("state") in RUN_TERMINAL_STATES else current
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
        prepared = self.prepare_run(project_id=project_id, setup_id=setup_id, face=face)
        if not prepared.get("ready"):
            raise ApplicationError("El trabajo no está listo para iniciar. Revise el preflight general.")
        run = prepared
        if run.get("state") not in {"JOB_READY", "JOB_PAUSED", "OPERATION_PAUSED", "TOOL_REFERENCE_READY", "NEXT_OPERATION_READY"}:
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

    def get_run(self, *, project_id: str, setup_id: str, face: str) -> dict[str, Any]:
        context = self._context(project_id, setup_id, face)
        run = self._load_run(context)
        if run is None:
            return self.prepare_run(project_id=project_id, setup_id=setup_id, face=face)
        return run

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
            if run["state"] not in {"JOB_PAUSED", "OPERATION_PAUSED", "TOOL_REFERENCE_READY", "NEXT_OPERATION_READY"}:
                raise ApplicationError(f"No se puede reanudar desde {run['state']}.")
            try:
                if run["state"] == "OPERATION_PAUSED":
                    adapter.resume()
            except Exception:
                pass
            run["state"] = "JOB_STARTING" if run["state"] in {"JOB_PAUSED", "TOOL_REFERENCE_READY", "NEXT_OPERATION_READY"} else "OPERATION_RUNNING"
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
        elif action == "confirm-tool-change":
            if run["state"] != "WAITING_TOOL_CHANGE":
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
            run["next_action"] = "Medir referencia Z de la nueva herramienta"
            run["available_actions"] = ["measure-reference", "cancel"]
            self._append_event(run, "info", f"Herramienta confirmada para {next_operation['tool_name']}. Falta medir la nueva referencia Z.")
        elif action == "measure-reference":
            self._measure_tool_reference(context, run)
        elif action == "continue":
            if run["state"] not in {"TOOL_REFERENCE_READY", "NEXT_OPERATION_READY"}:
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
        if action == "measure-reference":
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
        run["state"] = "PROBING_TOOL_REFERENCE"
        run["available_actions"] = ["cancel"]
        run["next_action"] = "Sondeando referencia Z de herramienta"
        self._append_event(run, "info", "Retornando al punto X0/Y0 para medir la nueva referencia Z.")
        self._save_run(context, run)
        probe = adapter.probe_tool_reference(
            x_mm=float(active_map["machine_origin_x"]),
            y_mm=float(active_map["machine_origin_y"]),
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
        self.generate_project_compensation(project_id=context.project_id, setup_id=context.setup_id, face=context.face)
        operation_payload["reference_status"] = "LISTA"
        run["current_tool_key"] = operation_payload["tool_key"]
        run["summary"]["tool_changes_completed"] = int(run["summary"].get("tool_changes_completed", 0)) + 1
        run["state"] = "JOB_STARTING"
        run["next_action"] = "Continuando con la siguiente operación tras la nueva referencia Z"
        run["available_actions"] = ["pause", "cancel"]
        self._append_event(run, "info", f"Referencia Z medida para {operation_payload['tool_name']}; continuando automáticamente.")

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
            while True:
                run = self._load_run(context)
                if run is None or run.get("state") in RUN_TERMINAL_STATES | RUN_WAITING_STATES:
                    return
                state = str(run.get("state"))
                if state in {"JOB_STARTING", "NEXT_OPERATION_READY", "TOOL_REFERENCE_READY"}:
                    self._execute_next_operation(context, run)
                    continue
                if state == "OPERATION_RUNNING":
                    self._watch_operation(context, run)
                    continue
                return
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
        operation["remote_file"] = upload.get("path") or upload.get("filename") or generated["relative_path"].split("/")[-1]
        operation["generated_file"] = generated["relative_path"]
        operation["generated_metadata"] = generated.get("metadata_path")
        operation["execution_status"] = "UPLOADED"
        run["state"] = "OPERATION_RUNNING"
        run["next_action"] = f"Ejecutando {operation['name']}"
        self._append_event(run, "info", f"Archivo subido a Moonraker: {operation['remote_file']}.")
        adapter.start_file(str(operation["remote_file"]))
        operation["execution_status"] = "RUNNING"
        operation["started_at"] = operation.get("started_at") or _iso_now()
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
            operation["progress"] = status.get("progress")
            operation["machine_status"] = status
            state = str(status.get("state") or "").lower()
            current["updated_at"] = _iso_now()
            if state in {"paused"}:
                operation["execution_status"] = "PAUSED"
                current["state"] = "OPERATION_PAUSED"
                current["next_action"] = "Reanudar operación"
                current["available_actions"] = ["resume", "cancel"]
                self._append_event(current, "warning", f"Operación {operation['name']} pausada.")
                self._save_run(context, current)
                return
            if state in {"complete", "completed", "standby"} and status.get("filename") == operation.get("remote_file"):
                operation["execution_status"] = "COMPLETED"
                operation["completed_at"] = _iso_now()
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

    def _handle_tool_change_required(self, context: JobContext, run: dict[str, Any], *, operation_index: int) -> None:
        adapter = self.adapter_factory(self.runtime)
        next_operation = run["operations"][operation_index]
        run["state"] = "MOVING_TO_TOOL_CHANGE_SAFE_Z"
        run["next_action"] = "Llevando la máquina a posición segura de cambio de herramienta"
        run["available_actions"] = ["cancel"]
        self._append_event(run, "info", f"Cambio de herramienta requerido antes de {next_operation['name']}.")
        self._save_run(context, run)
        adapter.move_to_tool_change_position()
        run["state"] = "WAITING_TOOL_CHANGE"
        run["next_action"] = "Confirmar cambio de herramienta"
        run["available_actions"] = ["confirm-tool-change", "cancel"]
        run["summary"]["tool_changes_required"] = max(run["summary"].get("tool_changes_required", 0), 1)
        self._append_event(run, "warning", f"Cambie a {next_operation['tool_name']} y confirme cuando esté instalada.")
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
        generated_by_operation = self._latest_generated_by_operation(context.project_id, operations)
        if generated_results:
            generated_by_operation.update({key: value for key, value in generated_results.items() if "relative_path" in value})
        operation_rows: list[dict[str, Any]] = []
        previous_tool_key: str | None = None
        tool_change_count = 0
        distinct_tools: list[str] = []
        for index, operation in enumerate(operations):
            tool_key = _tool_key(operation)
            if tool_key not in distinct_tools:
                distinct_tools.append(tool_key)
            tool_changed = previous_tool_key is not None and previous_tool_key != tool_key
            if tool_changed:
                tool_change_count += 1
            previous_tool_key = tool_key
            generated = generated_by_operation.get(operation.id)
            coverage = coverage_by_operation.get(operation.id)
            reference_status = self._reference_status(active_map, operation)
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
                    "installation_revision": None,
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

    def _reference_status(self, active_map: dict[str, Any] | None, operation: OperacionPCB) -> str:
        if active_map is None:
            return "PENDIENTE"
        reference = (active_map.get("tool_references") or {}).get(_tool_key(operation))
        return "LISTA" if isinstance(reference, dict) and reference.get("valid") else "REQUIERE_REFERENCIA"

    def _generated_payload_for_operation(self, plan: dict[str, Any], operation_id: str) -> dict[str, Any] | None:
        row = next((item for item in plan["operations"] if item["operation_id"] == operation_id), None)
        if row is None or not row.get("generated_file"):
            return None
        return {
            "relative_path": row["generated_file"],
            "metadata_path": row.get("generated_metadata_path"),
        }

    def _latest_generated_by_operation(self, project_id: str, operations: list[OperacionPCB]) -> dict[str, dict[str, Any]]:
        generated_dir = self.repository.project_dir(project_id) / "generated" / "compensated"
        if not generated_dir.exists():
            return {}
        results: dict[str, dict[str, Any]] = {}
        for operation in operations:
            candidates = sorted(generated_dir.glob(f"{operation.id}_*_compensated.gcode"), key=lambda item: item.stat().st_mtime, reverse=True)
            if not candidates:
                continue
            file_path = candidates[0]
            metadata_candidates = sorted(generated_dir.glob(f"{operation.id}_*_compensated.json"), key=lambda item: item.stat().st_mtime, reverse=True)
            metadata_path = metadata_candidates[0] if metadata_candidates else None
            results[operation.id] = {
                "relative_path": self._relative_to_project(project_id, file_path),
                "metadata_path": None if metadata_path is None else self._relative_to_project(project_id, metadata_path),
            }
        return results

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

    def _append_event(self, run: dict[str, Any], level: str, message: str) -> None:
        run.setdefault("events", []).append({"timestamp": _iso_now(), "level": level, "message": message})
        run["events"] = run["events"][-300:]

    def _context(self, project_id: str, setup_id: str, face: str) -> JobContext:
        normalized_face = BoardFace(face).value if face in {BoardFace.SUPERIOR.value, BoardFace.INFERIOR.value} else str(face)
        return JobContext(project_id=project_id, setup_id=setup_id, face=normalized_face)

    def _load_or_build_plan(self, context: JobContext) -> dict[str, Any]:
        plan = self._load_plan(context)
        if plan is not None:
            refreshed = self._build_plan(context)
            if plan.get("manifest_path"):
                refreshed["manifest_path"] = plan["manifest_path"]
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

    def _load_run(self, context: JobContext) -> dict[str, Any] | None:
        path = self._run_file(context)
        if not path.exists():
            return None
        with self._lock:
            return json.loads(path.read_text(encoding="utf-8"))

    def _save_run(self, context: JobContext, run: dict[str, Any]) -> None:
        path = self._run_file(context)
        payload = json.dumps(run, ensure_ascii=True, indent=2, sort_keys=True)
        with self._lock:
            tmp = path.with_suffix('.tmp')
            tmp.write_text(payload, encoding="utf-8")
            tmp.replace(path)

    def _archive_run(self, context: JobContext, run: dict[str, Any]) -> None:
        archived = dict(run)
        history_file = self._history_dir(context) / (str(run["run_id"]).replace("/", "_") + ".json")
        history_file.write_text(json.dumps(archived, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
        self._save_run(context, run)

    def _relative_to_project(self, project_id: str, path: Path) -> str:
        return path.relative_to(self._project_dir(project_id)).as_posix()

    def _load_project(self, project_id: str):
        try:
            return self.repository.load_project(project_id)
        except FileNotFoundError as error:
            raise NotFoundError(str(error)) from error
        except ProjectValidationError as error:
            raise ApplicationError(str(error)) from error
