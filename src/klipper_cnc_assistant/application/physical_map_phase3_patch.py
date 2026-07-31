from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from klipper_cnc_assistant.application.errors import ApplicationError


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return utc_now().isoformat()


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return None
    return parsed


def _same_number(left: Any, right: Any, *, tolerance: float = 0.001) -> bool:
    left_value = _as_float(left)
    right_value = _as_float(right)
    if left_value is None or right_value is None:
        return False
    return abs(left_value - right_value) <= tolerance


def _grid_points(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [point for point in points if point.get("role") != "REFERENCE"]


def _pending_points(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [point for point in points if point.get("status") in {"PENDING", "RETRY_REQUIRED"}]


def _progress_points(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [point for point in points if point.get("role") != "REFERENCE" and point.get("status") != "EXCLUDED"]


def _tool_reference_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    references = payload.get("tool_references") or {}
    tool_key = payload.get("acquisition_tool_id") or payload.get("tool_id")
    if isinstance(references, dict) and isinstance(tool_key, str):
        reference = references.get(tool_key)
        if isinstance(reference, dict):
            return reference
    if isinstance(references, dict):
        for reference in references.values():
            if isinstance(reference, dict) and reference.get("valid", True):
                return reference
    return {}


def _decorate_execution_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
    decorated = dict(payload)
    points = [dict(point) for point in decorated.get("points") or []]
    execution = dict(decorated.get("execution") or {})
    progress_points = _progress_points(points)
    pending_points = _pending_points(points)
    measured_count = sum(1 for point in progress_points if point.get("status") == "MEASURED")
    failed_count = sum(1 for point in progress_points if point.get("status") == "FAILED")
    excluded_count = sum(1 for point in points if point.get("status") == "EXCLUDED")
    total_count = len(progress_points)
    current_point_index = execution.get("point_index")
    next_point_index = pending_points[0].get("index") if pending_points else None
    progress_percent = 100.0 if total_count == 0 else round((measured_count / total_count) * 100.0, 3)
    last_progress_at = execution.get("last_progress_at") or execution.get("last_transition_at")
    last_progress_age_s = None
    parsed_last_progress = _parse_datetime(last_progress_at)
    if parsed_last_progress is not None:
        last_progress_age_s = max(0.0, (utc_now() - parsed_last_progress).total_seconds())
    execution["measured_count"] = measured_count
    execution["pending_count"] = sum(1 for point in progress_points if point.get("status") in {"PENDING", "RETRY_REQUIRED"})
    execution["excluded_count"] = excluded_count
    execution["failed_count"] = failed_count
    execution["progress_total"] = total_count
    execution["total_count"] = total_count
    execution["current_point_index"] = current_point_index
    execution["next_point_index"] = next_point_index
    execution["progress_percent"] = progress_percent
    execution["worker_alive"] = bool(execution.get("worker_active"))
    execution["pause_requested"] = bool(execution.get("pause_requested"))
    execution["cancel_requested"] = bool(execution.get("cancel_requested"))
    execution["last_error"] = execution.get("last_error") or execution.get("error")
    execution["last_progress_age_s"] = last_progress_age_s
    execution["operation_state"] = execution.get("point_state") or decorated.get("status")
    decorated["execution"] = execution
    decorated["measured_count"] = measured_count
    decorated["total_count"] = total_count
    decorated["current_point_index"] = current_point_index
    decorated["next_point_index"] = next_point_index
    decorated["progress_percent"] = progress_percent
    decorated["worker_alive"] = execution["worker_alive"]
    decorated["pause_requested"] = execution["pause_requested"]
    decorated["cancel_requested"] = execution["cancel_requested"]
    decorated["last_progress_age_s"] = last_progress_age_s
    decorated["last_error"] = execution.get("last_error")
    decorated["operation_state"] = execution["operation_state"]
    return decorated


def apply_phase3_patch(cls):
    if getattr(cls, "_phase3_patch_applied", False):
        return cls

    original_get_active = cls.get_active
    original_get_by_id = cls.get_by_id
    original_record_point = cls.record_point
    original_set_execution_state = cls._set_execution_state
    original_payload = cls._payload
    original_compatible_surface_map = cls._compatible_surface_map

    def get_active(self, project_id: str, operation_id: str) -> dict[str, Any]:
        return _decorate_execution_payload(self, original_get_active(self, project_id, operation_id))

    def get_by_id(self, project_id: str, map_id: str) -> dict[str, Any]:
        return _decorate_execution_payload(self, original_get_by_id(self, project_id, map_id))

    def _set_execution_state(
        self,
        payload: dict[str, Any],
        *,
        worker_active: bool | None = None,
        point_state: str | None = None,
        point_index: int | None = None,
        retry_count: int | None = None,
        error: str | None = None,
        last_event: str | None = None,
        command: str | None = None,
        target: dict[str, Any] | None = None,
        observed: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        updated = original_set_execution_state(
            self,
            payload,
            worker_active=worker_active,
            point_state=point_state,
            point_index=point_index,
            retry_count=retry_count,
            error=error,
            last_event=last_event,
            command=command,
            target=target,
            observed=observed,
        )
        execution = dict(updated.get("execution") or {})
        metadata = dict(metadata or {})
        if point_state is not None or last_event is not None:
            execution["last_progress_at"] = metadata.pop("last_progress_at", _iso_now())
        if error is not None:
            execution["last_error"] = error
        elif point_state not in {None, "POINT_FAILED"} and "last_error" not in metadata:
            execution["last_error"] = None
        execution.update(metadata)
        execution["operation_state"] = execution.get("point_state") or updated.get("status")
        updated["execution"] = execution
        return _decorate_execution_payload(self, updated)

    def update_execution_state(
        self,
        *,
        project_id: str,
        map_id: str,
        worker_active: bool | None = None,
        point_state: str | None = None,
        point_index: int | None = None,
        retry_count: int | None = None,
        error: str | None = None,
        last_event: str | None = None,
        command: str | None = None,
        target: dict[str, Any] | None = None,
        observed: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = original_get_by_id(self, project_id, map_id)
        payload = self._set_execution_state(
            payload,
            worker_active=worker_active,
            point_state=point_state,
            point_index=point_index,
            retry_count=retry_count,
            error=error,
            last_event=last_event,
            command=command,
            target=target,
            observed=observed,
            metadata=metadata,
        )
        payload["updated_at"] = _iso_now()
        self._save(project_id, map_id, payload)
        return payload

    def mark_status(
        self,
        *,
        project_id: str,
        map_id: str,
        status: str,
        worker_active: bool | None = None,
        point_state: str | None = None,
        last_event: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = original_get_by_id(self, project_id, map_id)
        payload["status"] = status
        payload = self._set_execution_state(
            payload,
            worker_active=(status == "MESH_PROBING" if worker_active is None else worker_active),
            point_state=point_state or ("MESH_PAUSED" if status == "MESH_PAUSED" else status),
            last_event=last_event or f"Estado de malla actualizado a {status}.",
            metadata=metadata,
        )
        execution = dict(payload.get("execution") or {})
        metadata = dict(metadata or {})
        if status == "MESH_PAUSED" and "pause_requested" not in metadata:
            execution["pause_requested"] = True
        elif status in {"MESH_READY", "MESH_PROBING", "CANCELLED"} and "pause_requested" not in metadata:
            execution["pause_requested"] = False
        if status == "CANCELLED" and "cancel_requested" not in metadata:
            execution["cancel_requested"] = False
        payload["execution"] = execution
        payload = _decorate_execution_payload(self, payload)
        payload["updated_at"] = _iso_now()
        self._save(project_id, map_id, payload)
        return payload

    def record_point(
        self,
        *,
        project_id: str,
        map_id: str,
        point_index: int,
        z_measured: float,
        status: str = "MEASURED",
        attempts: int | None = None,
        duration_s: float | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        before = self.get_by_id(project_id, map_id)
        execution_before = dict(before.get("execution") or {})
        updated = original_record_point(
            self,
            project_id=project_id,
            map_id=map_id,
            point_index=point_index,
            z_measured=z_measured,
            status=status,
            attempts=attempts,
            duration_s=duration_s,
            error=error,
        )
        if updated.get("status") == "MESH_COMPLETE":
            final = self.update_execution_state(
                project_id=project_id,
                map_id=map_id,
                worker_active=False,
                point_state="MESH_COMPLETE",
                point_index=point_index,
                last_event=f"Punto {point_index + 1}/{len(updated.get('points', []))} persistido; malla completa.",
                metadata={
                    "phase": "complete",
                    "pause_requested": False,
                    "pause_reason": None,
                    "cancel_requested": False,
                    "cancel_reason": None,
                    "last_progress_at": updated.get("updated_at") or _iso_now(),
                },
            )
            return final
        if execution_before.get("cancel_requested"):
            return self.mark_status(
                project_id=project_id,
                map_id=map_id,
                status="CANCELLED",
                worker_active=False,
                point_state="CANCELLED",
                last_event=f"Punto {point_index + 1}/{len(updated.get('points', []))} persistido antes de cerrar la cancelación.",
                metadata={
                    "phase": "cancelled",
                    "pause_requested": False,
                    "pause_reason": None,
                    "cancel_requested": False,
                    "cancel_reason": execution_before.get("cancel_reason") or "Solicitada por el operador.",
                    "last_progress_at": updated.get("updated_at") or _iso_now(),
                },
            )
        if execution_before.get("pause_requested"):
            return self.mark_status(
                project_id=project_id,
                map_id=map_id,
                status="MESH_PAUSED",
                worker_active=False,
                point_state="MESH_PAUSED",
                last_event=f"Punto {point_index + 1}/{len(updated.get('points', []))} persistido; pausa confirmada antes del siguiente punto.",
                metadata={
                    "phase": "paused",
                    "pause_requested": True,
                    "pause_reason": execution_before.get("pause_reason") or "Solicitada por el operador.",
                    "cancel_requested": False,
                    "last_progress_at": updated.get("updated_at") or _iso_now(),
                },
            )
        return self.update_execution_state(
            project_id=project_id,
            map_id=map_id,
            worker_active=True,
            point_state="POINT_PERSIST",
            point_index=point_index,
            last_event=f"Punto {point_index + 1}/{len(updated.get('points', []))} persistido; buscando siguiente punto.",
            metadata={
                "phase": "persist",
                "last_progress_at": updated.get("updated_at") or _iso_now(),
            },
        )

    def _payload(self, **kwargs) -> dict[str, Any]:
        payload = original_payload(self, **kwargs)
        execution = dict(payload.get("execution") or {})
        execution.setdefault("worker_generation", 0)
        execution.setdefault("pause_requested", False)
        execution.setdefault("pause_reason", None)
        execution.setdefault("cancel_requested", False)
        execution.setdefault("cancel_reason", None)
        execution.setdefault("phase", "planned")
        execution.setdefault("last_progress_at", payload.get("updated_at") or _iso_now())
        execution.setdefault("last_error", None)
        execution.setdefault("operation_state", execution.get("point_state") or payload.get("status"))
        payload["execution"] = execution
        return _decorate_execution_payload(self, payload)

    def _compatible_surface_map(self, payload: dict[str, Any], operation, origin_x: float, origin_y: float, config) -> bool:
        if not original_compatible_surface_map(self, payload, operation, origin_x, origin_y, config):
            return False
        mesh_config = payload.get("mesh_config") or {}
        probe_config = payload.get("probe_config") or {}
        exclusions = payload.get("exclusions") or []
        expected_exclusions = [exclusion.to_payload() for exclusion in config.exclusions]
        checks = (
            _same_number(mesh_config.get("max_spacing_mm"), config.max_spacing_mm),
            _same_number(mesh_config.get("margin_mm"), config.margin_mm),
            _same_number(probe_config.get("safe_z_mm"), config.safe_z_mm),
            _same_number(probe_config.get("probe_step_mm"), config.probe_step_mm),
            _same_number(probe_config.get("probe_feed_mm_min"), config.probe_feed_mm_min),
            _same_number(probe_config.get("retract_mm"), config.retract_mm),
            exclusions == expected_exclusions,
        )
        return all(checks)

    def validate_resume_context(self, *, project_id: str, map_id: str) -> dict[str, Any]:
        payload = self.get_by_id(project_id, map_id)
        if payload.get("archived_at") is not None:
            raise ApplicationError("La malla archivada no puede reanudarse.")
        if payload.get("status") == "MESH_COMPLETE":
            raise ApplicationError("La malla ya está completa.")
        if payload.get("status") == "CANCELLED":
            raise ApplicationError("La malla fue cancelada y necesita una nueva planificación o repetición explícita.")
        project = self._load_project(project_id)
        setup = project.get_setup(str(payload.get("setup_id")))
        if setup.active_map_id != map_id:
            raise ApplicationError("La malla ya no coincide con el mapa activo del montaje.")
        if setup.placement_revision != payload.get("placement_revision"):
            raise ApplicationError("La revisión de colocación cambió; la malla ya no es reanudable.")
        if setup.preparacion.origen_trabajo is None:
            raise ApplicationError("Falta el origen X/Y activo del montaje.")
        if setup.preparacion.referencia_z is None or setup.preparacion.referencia_z.z_mm is None:
            raise ApplicationError("Falta la referencia Z activa del montaje.")
        if not _same_number(setup.preparacion.origen_trabajo.x_mm, payload.get("machine_origin_x")) or not _same_number(setup.preparacion.origen_trabajo.y_mm, payload.get("machine_origin_y")):
            raise ApplicationError("El origen X/Y vigente ya no coincide con la malla planificada.")
        reference = _tool_reference_from_payload(payload)
        expected_x = reference.get("reference_x", payload.get("machine_origin_x"))
        expected_y = reference.get("reference_y", payload.get("machine_origin_y"))
        expected_z = reference.get("reference_z", payload.get("reference_z"))
        current_z_reference = setup.preparacion.referencia_z
        if not _same_number(current_z_reference.x_mm, expected_x) or not _same_number(current_z_reference.y_mm, expected_y) or not _same_number(current_z_reference.z_mm, expected_z):
            raise ApplicationError("La referencia Z vigente ya no coincide con la malla planificada.")
        return payload

    cls.get_active = get_active
    cls.get_by_id = get_by_id
    cls._set_execution_state = _set_execution_state
    cls.update_execution_state = update_execution_state
    cls.mark_status = mark_status
    cls.record_point = record_point
    cls._payload = _payload
    cls._compatible_surface_map = _compatible_surface_map
    cls.validate_resume_context = validate_resume_context
    cls._decorate_execution = _decorate_execution_payload
    cls._phase3_patch_applied = True
    return cls
