from __future__ import annotations

import math
import re
import threading
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from klipper_cnc_assistant.application.errors import ApplicationError, NotFoundError
from klipper_cnc_assistant.domain import CapturedPosition, CoordinateReference, OperationPreparation, OperacionPCB, PreparationState, ProjectValidationError
from klipper_cnc_assistant.heightmap import (
    ExclusionZone,
    HeightGrid,
    HeightMap,
    HeightSample,
    ProbeRegion,
    SampleQuality,
    compute_height_map,
)
from klipper_cnc_assistant.heightmap.coverage import DOMAIN_TOLERANCE_MM, build_coverage_report
from klipper_cnc_assistant.storage import JsonProjectRepository


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return utc_now().isoformat()


def _slug(value: str | None) -> str:
    raw = (value or "sin-valor").strip().lower()
    return re.sub(r"[^a-z0-9._-]+", "-", raw).strip("-") or "sin-valor"


def _tool_key(operation: OperacionPCB) -> str:
    return operation.tool_id or _slug(operation.herramienta) or "sin-herramienta"


def _tool_diameter(operation: OperacionPCB) -> float | None:
    values = re.findall(r"\d+(?:[\.,]\d+)?", operation.herramienta or "")
    if not values:
        return None
    return float(values[0].replace(",", "."))


@dataclass(frozen=True)
class PhysicalExclusion:
    id: str
    name: str
    shape: str
    enabled: bool = True
    x_min_mm: float | None = None
    x_max_mm: float | None = None
    y_min_mm: float | None = None
    y_max_mm: float | None = None
    center_x_mm: float | None = None
    center_y_mm: float | None = None
    radius_mm: float | None = None

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ApplicationError("La exclusión necesita un identificador.")
        if self.shape not in {"rectangle", "circle"}:
            raise ApplicationError("La exclusión debe ser rectangular o circular.")
        if self.shape == "rectangle":
            values = (self.x_min_mm, self.x_max_mm, self.y_min_mm, self.y_max_mm)
            if any(value is None for value in values):
                raise ApplicationError("La exclusión rectangular necesita x_min, x_max, y_min e y_max.")
            if float(self.x_min_mm) >= float(self.x_max_mm) or float(self.y_min_mm) >= float(self.y_max_mm):
                raise ApplicationError("La exclusión rectangular tiene límites inválidos.")
        if self.shape == "circle":
            values = (self.center_x_mm, self.center_y_mm, self.radius_mm)
            if any(value is None for value in values):
                raise ApplicationError("La exclusión circular necesita centro y radio.")
            if float(self.radius_mm) <= 0:
                raise ApplicationError("La exclusión circular necesita un radio positivo.")

    def contains(self, x_mm: float, y_mm: float) -> bool:
        if not self.enabled:
            return False
        if self.shape == "rectangle":
            return float(self.x_min_mm) <= x_mm <= float(self.x_max_mm) and float(self.y_min_mm) <= y_mm <= float(self.y_max_mm)
        dx = x_mm - float(self.center_x_mm)
        dy = y_mm - float(self.center_y_mm)
        return math.hypot(dx, dy) <= float(self.radius_mm)

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "shape": self.shape,
            "enabled": self.enabled,
            "x_min_mm": self.x_min_mm,
            "x_max_mm": self.x_max_mm,
            "y_min_mm": self.y_min_mm,
            "y_max_mm": self.y_max_mm,
            "center_x_mm": self.center_x_mm,
            "center_y_mm": self.center_y_mm,
            "radius_mm": self.radius_mm,
        }


@dataclass(frozen=True)
class PhysicalMeshConfig:
    grid_mode: str = "manual"
    rows: int = 7
    columns: int = 6
    edge_margin_left_mm: float = 2.0
    edge_margin_right_mm: float = 2.0
    edge_margin_bottom_mm: float = 2.0
    edge_margin_top_mm: float = 2.0
    exclusions: tuple[PhysicalExclusion, ...] = ()
    max_spacing_mm: float = 10.0
    margin_mm: float = 0.0
    safe_z_mm: float | None = None
    probe_step_mm: float | None = None
    probe_feed_mm_min: float | None = None
    retract_mm: float | None = None

    def __post_init__(self) -> None:
        if self.grid_mode not in {"manual", "suggested"}:
            raise ApplicationError("El modo de malla debe ser manual o suggested.")
        if self.rows < 2 or self.columns < 2:
            raise ApplicationError("La malla física necesita al menos 2 filas y 2 columnas.")
        if self.max_spacing_mm <= 0:
            raise ApplicationError("La separación máxima de malla debe ser positiva.")
        if self.margin_mm < 0:
            raise ApplicationError("El margen avanzado de malla no puede ser negativo.")
        for field, value in (
            ("retiro izquierdo", self.edge_margin_left_mm),
            ("retiro derecho", self.edge_margin_right_mm),
            ("retiro inferior", self.edge_margin_bottom_mm),
            ("retiro superior", self.edge_margin_top_mm),
        ):
            if value < 0:
                raise ApplicationError(f"El {field} no puede ser negativo.")
        for field, value in (
            ("Z segura", self.safe_z_mm),
            ("paso de sonda", self.probe_step_mm),
            ("velocidad de sonda", self.probe_feed_mm_min),
            ("retracto", self.retract_mm),
        ):
            if value is not None and value <= 0:
                raise ApplicationError(f"{field} debe ser positivo.")


class PhysicalMapService:
    """Planifica y persiste mapas físicos medidos.

    Modelo v2: el mapa de superficie pertenece al montaje/cara/revisión de colocación.
    Las referencias Z pertenecen a la herramienta instalada. Los puntos conservan Z
    absoluto y además delta_z normalizado contra la referencia Z usada al adquirir.
    """

    def __init__(self, repository: JsonProjectRepository) -> None:
        self.repository = repository
        self._io_lock = threading.RLock()

    def suggest_mesh_config(
        self,
        *,
        project_id: str,
        operation_id: str,
        config: PhysicalMeshConfig | None = None,
    ) -> dict[str, Any]:
        project = self._load_project(project_id)
        operation = project.get_operation(operation_id)
        selected = config or PhysicalMeshConfig(grid_mode="suggested")
        region = self._material_inner_region(project, selected)
        target = selected.max_spacing_mm
        columns = max(2, math.ceil(region.ancho_mm / target) + 1)
        rows = max(2, math.ceil(region.alto_mm / target) + 1)
        grid = self._grid_for_region(region, rows, columns)
        samples = self._blank_samples(grid, region, selected.exclusions)
        points = [sample for sample in samples if sample.incluida]
        distance = self._estimate_serpentine_distance(points, 0.0, 0.0)
        reason = (
            f"Se eligieron {rows} filas y {columns} columnas para cubrir la región sondeable "
            f"{region.ancho_mm:.3f} x {region.alto_mm:.3f} mm con separación objetivo {target:.3f} mm, "
            "respetando retiro de borde y exclusiones."
        )
        return {
            "grid_mode": "suggested",
            "rows": rows,
            "columns": columns,
            "point_count": rows * columns,
            "excluded_count": len(samples) - len(points),
            "executable_point_count": len(points),
            "dx_mm": grid.paso_x_mm,
            "dy_mm": grid.paso_y_mm,
            "estimated_distance_mm": distance,
            "estimated_time_s": self._estimate_time_s([], distance, replace(selected, rows=rows, columns=columns)),
            "reason": reason,
            "local_region": {"min_x_mm": region.min_x_mm, "min_y_mm": region.min_y_mm, "max_x_mm": region.max_x_mm, "max_y_mm": region.max_y_mm},
        }


    def preview_mesh(
        self,
        *,
        project_id: str,
        operation_id: str,
        config: PhysicalMeshConfig | None = None,
        machine_origin_x: float | None = None,
        machine_origin_y: float | None = None,
    ) -> dict[str, Any]:
        project = self._load_project(project_id)
        operation = project.get_operation(operation_id)
        selected_config = config or PhysicalMeshConfig()
        if selected_config.grid_mode == "suggested":
            suggestion = self.suggest_mesh_config(project_id=project_id, operation_id=operation_id, config=selected_config)
            selected_config = replace(selected_config, rows=int(suggestion["rows"]), columns=int(suggestion["columns"]))
        region = self._material_inner_region(project, selected_config)
        grid = self._grid_for_region(region, selected_config.rows, selected_config.columns)
        samples = self._blank_samples(grid, region, selected_config.exclusions)
        points = self._points_from_samples(
            samples=samples,
            operation=operation,
            config=selected_config,
            origin_x=machine_origin_x,
            origin_y=machine_origin_y,
            include_reference=False,
        )
        total_distance = self._distance_for_points(points)
        now = _iso_now()
        reference_point = {
            "index": 0,
            "row": None,
            "column": None,
            "role": "REFERENCE",
            "x_local": 0.0,
            "y_local": 0.0,
            "x_machine": machine_origin_x,
            "y_machine": machine_origin_y,
            "status": "REFERENCE_PENDING" if machine_origin_x is None or machine_origin_y is None else "PENDING",
        }
        payload = {
            "schema_version": "surface-map-preview-v1",
            "preview_id": f"preview/{operation.setup_id}/{operation.id}/{utc_now().strftime('%Y%m%d-%H%M%S-%f')}",
            "preview_version": now,
            "map_id": None,
            "project_id": project_id,
            "setup_id": operation.setup_id,
            "operation_id": operation.id,
            "face": operation.cara,
            "placement_revision": project.get_setup(operation.setup_id).placement_revision,
            "source": "PREVIEW",
            "status": "MESH_PREVIEW",
            "grid_mode": selected_config.grid_mode,
            "rows": grid.filas,
            "columns": grid.columnas,
            "point_count": grid.filas * grid.columnas,
            "excluded_count": sum(1 for point in points if point.get("status") == "EXCLUDED"),
            "executable_point_count": sum(1 for point in points if point.get("role") != "REFERENCE" and point.get("status") != "EXCLUDED"),
            "dx": grid.paso_x_mm,
            "dy": grid.paso_y_mm,
            "grid": {"rows": grid.filas, "columns": grid.columnas, "dx_mm": grid.paso_x_mm, "dy_mm": grid.paso_y_mm},
            "material_bounds": {"min_x_mm": 0.0, "min_y_mm": 0.0, "max_x_mm": float(project.material.ancho_mm), "max_y_mm": float(project.material.alto_mm)},
            "probe_region": {"min_x_mm": region.min_x_mm, "min_y_mm": region.min_y_mm, "max_x_mm": region.max_x_mm, "max_y_mm": region.max_y_mm},
            "local_region": {"min_x_mm": region.min_x_mm, "min_y_mm": region.min_y_mm, "max_x_mm": region.max_x_mm, "max_y_mm": region.max_y_mm},
            "machine_region": None if machine_origin_x is None or machine_origin_y is None else {"min_x_mm": machine_origin_x + region.min_x_mm, "min_y_mm": machine_origin_y + region.min_y_mm, "max_x_mm": machine_origin_x + region.max_x_mm, "max_y_mm": machine_origin_y + region.max_y_mm},
            "edge_margins": {"left_mm": selected_config.edge_margin_left_mm, "right_mm": selected_config.edge_margin_right_mm, "bottom_mm": selected_config.edge_margin_bottom_mm, "top_mm": selected_config.edge_margin_top_mm},
            "exclusions": [exclusion.to_payload() for exclusion in selected_config.exclusions],
            "mesh_config": {"grid_mode": selected_config.grid_mode, "rows": selected_config.rows, "columns": selected_config.columns, "edge_margin_left_mm": selected_config.edge_margin_left_mm, "edge_margin_right_mm": selected_config.edge_margin_right_mm, "edge_margin_bottom_mm": selected_config.edge_margin_bottom_mm, "edge_margin_top_mm": selected_config.edge_margin_top_mm, "max_spacing_mm": selected_config.max_spacing_mm, "margin_mm": selected_config.margin_mm},
            "probe_config": {"safe_z_mm": selected_config.safe_z_mm, "probe_step_mm": selected_config.probe_step_mm, "probe_feed_mm_min": selected_config.probe_feed_mm_min, "retract_mm": selected_config.retract_mm},
            "local_points": points,
            "machine_points": None if machine_origin_x is None or machine_origin_y is None else points,
            "points": points,
            "serpentine_path": [{"index": point["index"], "x_local": point["x_local"], "y_local": point["y_local"], "x_machine": point.get("x_machine"), "y_machine": point.get("y_machine"), "status": point.get("status")} for point in points],
            "reference_point": reference_point,
            "warnings": [] if machine_origin_x is not None and machine_origin_y is not None else ["Vista previa en coordenadas PCB. Complete la referencia para calcular las coordenadas CNC."],
            "valid_for_execution": machine_origin_x is not None and machine_origin_y is not None,
            "estimated_distance_mm": total_distance,
            "estimated_time_s": self._estimate_time_s(points, total_distance, selected_config),
            "created_at": now,
            "updated_at": now,
        }
        return payload

    def capture_reference_and_plan(
        self,
        *,
        project_id: str,
        operation_id: str,
        machine_origin_x: float,
        machine_origin_y: float,
        reference_z: float,
        machine_position: dict[str, float],
        homed_axes: str | None,
        machine_label: str | None,
        session_id: str | None,
        config: PhysicalMeshConfig | None = None,
    ) -> dict[str, Any]:
        project = self._load_project(project_id)
        operation = project.get_operation(operation_id)
        selected_config = config or PhysicalMeshConfig()
        if selected_config.grid_mode == "suggested":
            suggestion = self.suggest_mesh_config(project_id=project_id, operation_id=operation_id, config=selected_config)
            selected_config = replace(selected_config, rows=int(suggestion["rows"]), columns=int(suggestion["columns"]))

        existing = self._latest_surface_map(project_id, operation)
        if existing and self._compatible_surface_map(existing, operation, machine_origin_x, machine_origin_y, selected_config):
            payload = self._with_tool_reference(
                existing,
                operation=operation,
                reference_z=reference_z,
                machine_position=machine_position,
                homed_axes=homed_axes,
                machine_label=machine_label,
                session_id=session_id,
            )
            payload["updated_at"] = _iso_now()
            self._save(project_id, str(payload["map_id"]), payload)
            return payload

        previous_partial_message = None
        if existing and not self._compatible_surface_map(existing, operation, machine_origin_x, machine_origin_y, selected_config):
            measured = sum(1 for point in existing.get("points", []) if point.get("status") == "MEASURED")
            if measured:
                previous_partial_message = "Existe una medición parcial. Cambiar la cuadrícula creará una nueva versión de malla. Los puntos medidos anteriores se conservarán en el historial, pero no pertenecerán a la nueva cuadrícula."
            self._archive_payload(project_id, existing, replaced_by=None)

        operations = self._operations_for_setup_face(project, operation) or (operation,)
        local_region = self._material_inner_region(project, selected_config)
        grid = self._grid_for_region(local_region, selected_config.rows, selected_config.columns)
        samples = self._blank_samples(grid, local_region, selected_config.exclusions)
        height_map = compute_height_map(
            proyecto_id=project_id,
            operacion_id=operation.setup_id,
            version=1,
            fuente_datos="measured",
            superficie_simulada=None,
            repeticion_simulacion=None,
            etiqueta_simulada=False,
            grid=grid,
            probe_region=local_region,
            exclusion_zones=self._rectangular_exclusion_zones(selected_config.exclusions),
            muestras=samples,
            estado="malla planificada",
        )
        map_id = self._map_id(operation.setup_id, operation, machine_origin_x, machine_origin_y, selected_config)
        payload = self._payload(
            project_id=project_id,
            setup_id=operation.setup_id,
            operation=operation,
            operations=operations,
            map_id=map_id,
            machine_origin_x=machine_origin_x,
            machine_origin_y=machine_origin_y,
            reference_z=reference_z,
            machine_position=machine_position,
            homed_axes=homed_axes,
            machine_label=machine_label,
            session_id=session_id,
            config=selected_config,
            height_map=height_map,
            status="MESH_PLANNED",
            reference_already_measured=True,
            placement_revision=project.get_setup(operation.setup_id).placement_revision,
        )
        if previous_partial_message:
            payload["configuration_change_warning"] = previous_partial_message
        self._save(project_id, map_id, payload)
        self._mark_setup_map_active(project_id, operation.setup_id, map_id)
        return payload

    def get_active(self, project_id: str, operation_id: str) -> dict[str, Any]:
        project = self._load_project(project_id)
        operation = project.get_operation(operation_id)
        setup = project.get_setup(operation.setup_id)
        if setup.active_map_id:
            try:
                active = self.get_by_id(project_id, setup.active_map_id)
                if active.get("setup_id") == operation.setup_id and active.get("face") == operation.cara and active.get("archived_at") is None:
                    return self._ensure_completed_map_finalized(project_id, active)
            except Exception:
                pass
        payload = self._latest_surface_map(project_id, operation)
        if payload is not None:
            return self._ensure_completed_map_finalized(project_id, payload)
        legacy = self._latest_legacy_tool_map(project_id, operation)
        if legacy is not None:
            return self._migrate_legacy_payload(legacy, operation)
        raise NotFoundError("No existe mapa físico medido para este montaje y cara.")

    def get_by_id(self, project_id: str, map_id: str) -> dict[str, Any]:
        payload = self._load(project_id, map_id)
        if payload.get("schema_version") != "surface-map-v2":
            project = self._load_project(project_id)
            operation_id = str((payload.get("operation_ids") or [""])[0])
            if operation_id:
                payload = self._migrate_legacy_payload(payload, project.get_operation(operation_id))
        return self._ensure_completed_map_finalized(project_id, payload)

    def next_pending_point(self, project_id: str, map_id: str) -> dict[str, Any]:
        payload = self.get_by_id(project_id, map_id)
        for point in payload["points"]:
            if point.get("status") in {"PENDING", "RETRY_REQUIRED", "FAILED"}:
                return point
        raise ApplicationError("La malla no tiene puntos pendientes.")

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
        payload = self.get_by_id(project_id, map_id)
        points = payload["points"]
        if point_index < 0 or point_index >= len(points):
            raise ApplicationError("Índice de punto fuera de rango.")
        measured_at = _iso_now()
        reference_z = float(payload.get("acquisition_reference_z", payload.get("reference_z", 0.0)) or 0.0)
        point = dict(points[point_index])
        if point.get("role") == "REFERENCE":
            reference_z = float(z_measured)
            payload["reference_z"] = reference_z
            payload["acquisition_reference_z"] = reference_z
            tool_key = str(point.get("tool_id") or payload.get("acquisition_tool_id") or "sin-herramienta")
            references = dict(payload.get("tool_references") or {})
            existing_reference = dict(references.get(tool_key) or {})
            existing_reference.update({"reference_z": reference_z, "measured_at": measured_at, "source": "MEASURED"})
            references[tool_key] = existing_reference
            payload["tool_references"] = references
        point.update({
            "z_measured": z_measured,
            "z_measured_abs": z_measured,
            "delta_z": 0.0 if point.get("role") == "REFERENCE" else z_measured - reference_z,
            "timestamp": measured_at,
            "measured_at": measured_at,
            "status": status,
            "attempts": attempts if attempts is not None else int(point.get("attempts", 0)) + 1,
            "duration_s": duration_s,
            "duration": duration_s,
            "error": error,
            "last_error": error,
        })
        points[point_index] = point
        payload["points"] = points
        payload["status"] = "MESH_COMPLETE" if all(item.get("status") in {"MEASURED", "EXCLUDED", "SKIPPED"} for item in points) else "MESH_PROBING"
        if payload["status"] == "MESH_COMPLETE":
            payload["completed_at"] = measured_at
            payload = self._set_execution_state(
                payload,
                worker_active=False,
                point_state="POINT_COMPLETE",
                point_index=point_index,
                last_event=f"Punto {point_index + 1}/{len(points)} persistido; malla completa.",
            )
        else:
            payload = self._set_execution_state(
                payload,
                point_state="POINT_PERSIST",
                point_index=point_index,
                last_event=f"Punto {point_index + 1}/{len(points)} persistido; buscando siguiente punto.",
            )
        payload["updated_at"] = measured_at
        payload["height_map"] = self._height_map_payload_from_points(payload)
        if payload["status"] == "MESH_COMPLETE":
            payload = self._finalize_completed_map(project_id, payload)
        self._save(project_id, map_id, payload)
        return payload

    def mark_status(self, *, project_id: str, map_id: str, status: str) -> dict[str, Any]:
        payload = self.get_by_id(project_id, map_id)
        payload["status"] = status
        payload["updated_at"] = _iso_now()
        payload = self._set_execution_state(
            payload,
            worker_active=status == "MESH_PROBING",
            point_state="MESH_PAUSED" if status == "MESH_PAUSED" else status,
            last_event=f"Estado de malla actualizado a {status}.",
        )
        execution = dict(payload.get("execution") or {})
        if status == "MESH_PAUSED":
            execution["pause_requested"] = True
        elif status in {"MESH_READY", "MESH_PROBING", "CANCELLED"}:
            execution["pause_requested"] = False
        payload["execution"] = execution
        self._save(project_id, map_id, payload)
        return payload

    def mark_point_failed(self, *, project_id: str, map_id: str, point_index: int, error: str) -> dict[str, Any]:
        payload = self.get_by_id(project_id, map_id)
        points = payload["points"]
        if point_index < 0 or point_index >= len(points):
            raise ApplicationError("Índice de punto fuera de rango.")
        point = dict(points[point_index])
        point["status"] = "FAILED"
        point["error"] = error
        point["last_error"] = error
        point["attempts"] = int(point.get("attempts", 0)) + 1
        points[point_index] = point
        payload["points"] = points
        payload["status"] = "MESH_PAUSED"
        payload["updated_at"] = _iso_now()
        payload = self._set_execution_state(
            payload,
            worker_active=False,
            point_state="POINT_FAILED",
            point_index=point_index,
            retry_count=int(point.get("attempts", 0)),
            error=error,
            last_event=f"Punto {point_index + 1}/{len(points)} falló y la malla quedó pausada.",
        )
        payload["height_map"] = self._height_map_payload_from_points(payload)
        self._save(project_id, map_id, payload)
        return payload

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
    ) -> dict[str, Any]:
        payload = self.get_by_id(project_id, map_id)
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
        )
        payload["updated_at"] = _iso_now()
        self._save(project_id, map_id, payload)
        return payload

    def execution_log(self, *, project_id: str, map_id: str) -> dict[str, Any]:
        payload = self.get_by_id(project_id, map_id)
        return {
            "map_id": map_id,
            "status": payload.get("status"),
            "execution": payload.get("execution", {}),
            "events": list(payload.get("events", [])),
        }

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
    ) -> dict[str, Any]:
        now = _iso_now()
        points = list(payload.get("points", []))
        execution = dict(payload.get("execution") or {})
        previous_state = execution.get("point_state")
        if worker_active is not None:
            execution["worker_active"] = worker_active
        if point_state is not None:
            execution["point_state"] = point_state
            if point_state != previous_state:
                execution["state_entered_at"] = now
        if point_index is not None:
            execution["point_index"] = point_index
        if retry_count is not None:
            execution["retry_count"] = retry_count
        if error is not None or point_state == "POINT_FAILED":
            execution["error"] = error
        elif point_state not in {None, "POINT_FAILED"}:
            execution["error"] = None
        if last_event is not None:
            execution["last_event"] = last_event
        if command is not None:
            execution["last_command"] = command
        if target is not None or observed is not None:
            execution["last_result"] = {"target": target, "observed": observed, "at": now}
        execution["last_transition_at"] = now
        execution["measured_count"] = sum(1 for point in points if point.get("status") == "MEASURED")
        execution["pending_count"] = sum(1 for point in points if point.get("status") in {"PENDING", "RETRY_REQUIRED"})
        execution["excluded_count"] = sum(1 for point in points if point.get("status") == "EXCLUDED")
        execution["failed_count"] = sum(1 for point in points if point.get("status") == "FAILED")
        execution["progress_total"] = sum(1 for point in points if point.get("status") != "EXCLUDED")
        payload["execution"] = execution
        event = {
            "timestamp": now,
            "mesh_id": payload.get("map_id"),
            "point_index": point_index if point_index is not None else execution.get("point_index"),
            "previous_state": previous_state,
            "next_state": execution.get("point_state"),
            "command": command,
            "target": target,
            "observed": observed,
            "result": last_event,
            "retry_count": execution.get("retry_count", 0),
            "error": error,
            "progress": {
                "measured": execution["measured_count"],
                "pending": execution["pending_count"],
                "failed": execution["failed_count"],
                "total": execution["progress_total"],
            },
        }
        events = list(payload.get("events") or [])
        if last_event is not None or point_state is not None:
            events.append(event)
        payload["events"] = events[-500:]
        return payload

    def _operations_for_setup_face(self, project, operation: OperacionPCB) -> tuple[OperacionPCB, ...]:
        return tuple(
            item
            for item in project.operations_for_setup(operation.setup_id)
            if item.cara == operation.cara and item.analisis is not None and item.analisis.limites is not None
        )

    def _material_inner_region(self, project, config: PhysicalMeshConfig) -> ProbeRegion:
        material = getattr(project, "material", None)
        if material is None:
            raise ApplicationError("El proyecto no tiene dimensiones de material configuradas.")
        material_width = float(material.ancho_mm)
        material_height = float(material.alto_mm)
        min_x = config.edge_margin_left_mm
        max_x = material_width - config.edge_margin_right_mm
        min_y = config.edge_margin_bottom_mm
        max_y = material_height - config.edge_margin_top_mm
        if min_x >= max_x or min_y >= max_y:
            raise ApplicationError("El retiro de los bordes deja una región de sondeo inválida. Reduzca los valores o revise las dimensiones del material.")
        return ProbeRegion(min_x_mm=min_x, min_y_mm=min_y, max_x_mm=max_x, max_y_mm=max_y)

    def _grid_for_region(self, region: ProbeRegion, rows: int, columns: int) -> HeightGrid:
        step_x = region.ancho_mm / (columns - 1)
        step_y = region.alto_mm / (rows - 1)
        return HeightGrid(filas=rows, columnas=columns, ancho_mm=region.ancho_mm, alto_mm=region.alto_mm, paso_x_mm=step_x, paso_y_mm=step_y)

    def _blank_samples(self, grid: HeightGrid, region: ProbeRegion, exclusions: tuple[PhysicalExclusion, ...]) -> list[HeightSample]:
        samples: list[HeightSample] = []
        for row in range(grid.filas):
            columns = range(grid.columnas) if row % 2 == 0 else range(grid.columnas - 1, -1, -1)
            for column in columns:
                x_mm = region.min_x_mm + column * grid.paso_x_mm
                y_mm = region.min_y_mm + row * grid.paso_y_mm
                exclusion = self._exclusion_for_point(x_mm, y_mm, exclusions)
                samples.append(HeightSample(
                    id=f"measured_{row}_{column}",
                    x_mm=x_mm,
                    y_mm=y_mm,
                    z_mm=None,
                    fila=row,
                    columna=column,
                    origen_datos="measured",
                    estado_calidad=SampleQuality.EXCLUIDA if exclusion else SampleQuality.FALTANTE,
                    observacion=f"Excluido: {exclusion.name}" if exclusion else "Pendiente de sondeo físico.",
                    incluida=False if exclusion else True,
                ))
        return samples


    def _exclusion_for_point(self, x_mm: float, y_mm: float, exclusions: tuple[PhysicalExclusion, ...]) -> PhysicalExclusion | None:
        for exclusion in exclusions:
            if exclusion.contains(x_mm, y_mm):
                return exclusion
        return None

    def _rectangular_exclusion_zones(self, exclusions: tuple[PhysicalExclusion, ...]) -> tuple[ExclusionZone, ...]:
        return tuple(
            ExclusionZone(
                id=exclusion.id,
                nombre=exclusion.name,
                min_x_mm=float(exclusion.x_min_mm),
                min_y_mm=float(exclusion.y_min_mm),
                max_x_mm=float(exclusion.x_max_mm),
                max_y_mm=float(exclusion.y_max_mm),
            )
            for exclusion in exclusions
            if exclusion.enabled and exclusion.shape == "rectangle"
        )

    def _rectangular_exclusion_zones_from_payload(self, exclusions: list[dict[str, Any]]) -> tuple[ExclusionZone, ...]:
        zones: list[ExclusionZone] = []
        for exclusion in exclusions:
            if not exclusion.get("enabled", True) or exclusion.get("shape") != "rectangle":
                continue
            zones.append(ExclusionZone(
                id=str(exclusion.get("id") or "exclusion"),
                nombre=str(exclusion.get("name") or exclusion.get("nombre") or "Exclusión"),
                min_x_mm=float(exclusion.get("x_min_mm")),
                min_y_mm=float(exclusion.get("y_min_mm")),
                max_x_mm=float(exclusion.get("x_max_mm")),
                max_y_mm=float(exclusion.get("y_max_mm")),
            ))
        return tuple(zones)

    def _estimate_serpentine_distance(self, samples: list[HeightSample], origin_x: float, origin_y: float) -> float:
        total = 0.0
        previous: tuple[float, float] | None = None
        for sample in samples:
            current = (origin_x + sample.x_mm, origin_y + sample.y_mm)
            if previous is not None:
                total += math.dist(previous, current)
            previous = current
        return total

    def _estimate_time_s(self, points: list[dict[str, Any]], distance_mm: float, config: PhysicalMeshConfig) -> float | None:
        executable = sum(1 for point in points if point.get("status") != "EXCLUDED") if points else config.rows * config.columns
        if executable <= 0:
            return 0.0
        travel_feed_mm_min = 600.0
        travel_s = distance_mm / travel_feed_mm_min * 60.0
        probe_feed = config.probe_feed_mm_min or 60.0
        retract = config.retract_mm or 1.0
        probe_step = config.probe_step_mm or 0.05
        probe_s = executable * max(2.0, (retract + probe_step * 4.0) / probe_feed * 60.0)
        return travel_s + probe_s

    def _payload(self, **kwargs) -> dict[str, Any]:
        height_map: HeightMap = kwargs["height_map"]
        operation: OperacionPCB = kwargs["operation"]
        operations: tuple[OperacionPCB, ...] = kwargs["operations"]
        config: PhysicalMeshConfig = kwargs["config"]
        origin_x = float(kwargs["machine_origin_x"])
        origin_y = float(kwargs["machine_origin_y"])
        points = self._points_from_samples(
            samples=list(height_map.muestras),
            operation=operation,
            config=config,
            origin_x=origin_x,
            origin_y=origin_y,
            include_reference=True,
        )
        if kwargs.get("reference_already_measured"):
            points = self._apply_captured_reference_point(points, reference_z=float(kwargs["reference_z"]))
        total_distance = self._distance_for_points(points)
        tool_reference = self._tool_reference(
            operation=operation,
            reference_z=float(kwargs["reference_z"]),
            machine_position=kwargs["machine_position"],
            homed_axes=kwargs["homed_axes"],
            machine_label=kwargs["machine_label"],
            session_id=kwargs["session_id"],
        )
        return {
            "schema_version": "surface-map-v2",
            "map_model": "SURFACE_BY_SETUP_FACE_PLACEMENT",
            "map_id": kwargs["map_id"],
            "project_id": kwargs["project_id"],
            "setup_id": operation.setup_id,
            "face": operation.cara,
            "placement_revision": kwargs.get("placement_revision", "placement-1"),
            "version": 1,
            "archived_at": None,
            "replaced_by": None,
            "tool_id": _tool_key(operation),
            "tool_name": operation.herramienta,
            "tool_diameter": _tool_diameter(operation),
            "operation_ids": [item.id for item in operations],
            "source": "MEASURED",
            "status": kwargs["status"],
            "created_at": _iso_now(),
            "completed_at": None,
            "updated_at": _iso_now(),
            "machine_origin_x": origin_x,
            "machine_origin_y": origin_y,
            "reference_z": float(kwargs["reference_z"]),
            "acquisition_tool_id": _tool_key(operation),
            "acquisition_tool_name": operation.herramienta,
            "acquisition_tool_diameter": _tool_diameter(operation),
            "acquisition_reference_z": float(kwargs["reference_z"]),
            "tool_references": {_tool_key(operation): tool_reference},
            "machine_position": kwargs["machine_position"],
            "homed_axes": kwargs["homed_axes"],
            "machine_label": kwargs["machine_label"],
            "session_id": kwargs["session_id"],
            "mesh_config": {
                "grid_mode": config.grid_mode,
                "rows": config.rows,
                "columns": config.columns,
                "edge_margin_left_mm": config.edge_margin_left_mm,
                "edge_margin_right_mm": config.edge_margin_right_mm,
                "edge_margin_bottom_mm": config.edge_margin_bottom_mm,
                "edge_margin_top_mm": config.edge_margin_top_mm,
                "max_spacing_mm": config.max_spacing_mm,
                "margin_mm": config.margin_mm,
            },
            "edge_margins": {
                "left_mm": config.edge_margin_left_mm,
                "right_mm": config.edge_margin_right_mm,
                "bottom_mm": config.edge_margin_bottom_mm,
                "top_mm": config.edge_margin_top_mm,
            },
            "exclusions": [exclusion.to_payload() for exclusion in config.exclusions],
            "probe_config": {
                "safe_z_mm": config.safe_z_mm,
                "reference_z_mm": float(kwargs["reference_z"]),
                "probe_step_mm": config.probe_step_mm,
                "probe_feed_mm_min": config.probe_feed_mm_min,
                "retract_mm": config.retract_mm,
            },
            "local_region": {
                "min_x_mm": height_map.probe_region.min_x_mm,
                "min_y_mm": height_map.probe_region.min_y_mm,
                "max_x_mm": height_map.probe_region.max_x_mm,
                "max_y_mm": height_map.probe_region.max_y_mm,
            },
            "machine_region": {
                "min_x_mm": origin_x + height_map.probe_region.min_x_mm,
                "min_y_mm": origin_y + height_map.probe_region.min_y_mm,
                "max_x_mm": origin_x + height_map.probe_region.max_x_mm,
                "max_y_mm": origin_y + height_map.probe_region.max_y_mm,
            },
            "grid_mode": config.grid_mode,
            "rows": height_map.grid.filas,
            "columns": height_map.grid.columnas,
            "dx": height_map.grid.paso_x_mm,
            "dy": height_map.grid.paso_y_mm,
            "grid": {"rows": height_map.grid.filas, "columns": height_map.grid.columnas, "dx_mm": height_map.grid.paso_x_mm, "dy_mm": height_map.grid.paso_y_mm},
            "point_count": sum(1 for point in points if point.get("role") != "REFERENCE"),
            "acquisition_point_count": len(points),
            "excluded_count": sum(1 for point in points if point.get("status") == "EXCLUDED"),
            "executable_point_count": sum(1 for point in points if point.get("role") != "REFERENCE" and point.get("status") != "EXCLUDED"),
            "estimated_distance_mm": total_distance,
            "estimated_time_s": self._estimate_time_s(points, total_distance, config),
            "invalid_points": [],
            "execution": {
                "worker_active": False,
                "point_state": "MESH_PLANNED",
                "point_index": None,
                "state_entered_at": _iso_now(),
                "last_transition_at": _iso_now(),
                "last_event": "Malla planificada; ejecución física pendiente.",
                "last_command": None,
                "last_result": None,
                "retry_count": 0,
                "error": None,
                "measured_count": sum(1 for point in points if point.get("status") == "MEASURED"),
                "pending_count": sum(1 for point in points if point.get("status") in {"PENDING", "RETRY_REQUIRED", "FAILED"}),
                "excluded_count": sum(1 for point in points if point.get("status") == "EXCLUDED"),
                "failed_count": 0,
                "progress_total": sum(1 for point in points if point.get("status") != "EXCLUDED"),
            },
            "events": [],
            "points": points,
            "height_map": self._serialize_height_map(height_map),
        }


    def _apply_captured_reference_point(self, points: list[dict[str, Any]], *, reference_z: float) -> list[dict[str, Any]]:
        measured_at = _iso_now()
        updated_points: list[dict[str, Any]] = []
        for point in points:
            if point.get("role") != "REFERENCE":
                updated_points.append(point)
                continue
            reference_point = dict(point)
            reference_point.update({
                "z_measured": reference_z,
                "z_measured_abs": reference_z,
                "delta_z": 0.0,
                "timestamp": measured_at,
                "started_at": measured_at,
                "measured_at": measured_at,
                "status": "MEASURED",
                "attempts": max(1, int(reference_point.get("attempts", 0))),
                "duration": 0.0,
                "duration_s": 0.0,
                "error": None,
                "last_error": None,
            })
            updated_points.append(reference_point)
        return updated_points

    def _points_from_samples(
        self,
        *,
        samples: list[HeightSample],
        operation: OperacionPCB,
        config: PhysicalMeshConfig,
        origin_x: float | None,
        origin_y: float | None,
        include_reference: bool,
    ) -> list[dict[str, Any]]:
        points: list[dict[str, Any]] = []
        for sample in samples:
            machine_x = None if origin_x is None else origin_x + sample.x_mm
            machine_y = None if origin_y is None else origin_y + sample.y_mm
            exclusion = self._exclusion_for_point(sample.x_mm, sample.y_mm, config.exclusions)
            is_excluded = exclusion is not None
            points.append({
                "index": len(points),
                "row": sample.fila,
                "column": sample.columna,
                "role": "GRID",
                "x_local": sample.x_mm,
                "y_local": sample.y_mm,
                "x_machine": machine_x,
                "y_machine": machine_y,
                "z_measured": None,
                "z_measured_abs": None,
                "delta_z": None,
                "timestamp": None,
                "started_at": None,
                "measured_at": None,
                "status": "EXCLUDED" if is_excluded else "PENDING",
                "attempts": 0,
                "duration": None,
                "duration_s": None,
                "last_error": None,
                "error": f"Excluido: {exclusion.name}" if exclusion else None,
                "exclusion_id": exclusion.id if exclusion else None,
                "tool_id": _tool_key(operation),
                "setup_id": operation.setup_id,
            })
        if include_reference:
            points = self._with_reference_point(points, operation=operation, origin_x=origin_x, origin_y=origin_y)
        return points

    def _with_reference_point(self, points: list[dict[str, Any]], *, operation: OperacionPCB, origin_x: float | None, origin_y: float | None) -> list[dict[str, Any]]:
        tolerance = 1e-6
        coincident_index = next((index for index, point in enumerate(points) if abs(float(point.get("x_local", 0.0))) <= tolerance and abs(float(point.get("y_local", 0.0))) <= tolerance), None)
        if coincident_index is not None:
            reference = dict(points.pop(coincident_index))
            reference["role"] = "REFERENCE"
            reference["x_machine"] = origin_x
            reference["y_machine"] = origin_y
            ordered = [reference, *points]
        else:
            reference = {
                "index": 0,
                "row": -1,
                "column": -1,
                "role": "REFERENCE",
                "x_local": 0.0,
                "y_local": 0.0,
                "x_machine": origin_x,
                "y_machine": origin_y,
                "z_measured": None,
                "z_measured_abs": None,
                "delta_z": None,
                "timestamp": None,
                "started_at": None,
                "measured_at": None,
                "status": "PENDING",
                "attempts": 0,
                "duration": None,
                "duration_s": None,
                "last_error": None,
                "error": None,
                "tool_id": _tool_key(operation),
                "setup_id": operation.setup_id,
            }
            ordered = [reference, *points]
        for index, point in enumerate(ordered):
            point["index"] = index
        return ordered

    def _distance_for_points(self, points: list[dict[str, Any]]) -> float:
        total = 0.0
        previous: tuple[float, float] | None = None
        for point in points:
            if point.get("status") == "EXCLUDED":
                continue
            x_value = point.get("x_machine") if point.get("x_machine") is not None else point.get("x_local")
            y_value = point.get("y_machine") if point.get("y_machine") is not None else point.get("y_local")
            current = (float(x_value), float(y_value))
            if previous is not None:
                total += math.dist(previous, current)
            previous = current
        return total

    def _height_map_payload_from_points(self, payload: dict[str, Any]) -> dict[str, Any]:
        grid_payload = payload["grid"]
        region_payload = payload["local_region"]
        grid = HeightGrid(
            filas=grid_payload["rows"],
            columnas=grid_payload["columns"],
            ancho_mm=region_payload["max_x_mm"] - region_payload["min_x_mm"],
            alto_mm=region_payload["max_y_mm"] - region_payload["min_y_mm"],
            paso_x_mm=grid_payload["dx_mm"],
            paso_y_mm=grid_payload["dy_mm"],
        )
        region = ProbeRegion(**region_payload)
        samples = []
        for point in payload["points"]:
            if point.get("role") == "REFERENCE" and (int(point.get("row", -1)) < 0 or int(point.get("column", -1)) < 0):
                continue
            value = point.get("delta_z", point.get("z_measured"))
            status = point.get("status")
            samples.append(HeightSample(
                id=f"measured_{point['row']}_{point['column']}",
                x_mm=point["x_local"],
                y_mm=point["y_local"],
                z_mm=None if status == "EXCLUDED" else value,
                fila=point["row"],
                columna=point["column"],
                origen_datos="measured",
                estado_calidad=SampleQuality.EXCLUIDA if status == "EXCLUDED" else (SampleQuality.FALTANTE if value is None else SampleQuality.VALIDA),
                observacion=point.get("last_error") or point.get("error"),
                incluida=status != "EXCLUDED",
            ))
        height_map = compute_height_map(
            proyecto_id=payload["project_id"],
            operacion_id=payload["setup_id"],
            version=int(payload.get("height_map", {}).get("version", 0)) + 1,
            fuente_datos="measured",
            superficie_simulada=None,
            repeticion_simulacion=None,
            etiqueta_simulada=False,
            grid=grid,
            probe_region=region,
            exclusion_zones=self._rectangular_exclusion_zones_from_payload(payload.get("exclusions") or []),
            muestras=samples,
            estado="medido relativo" if payload["status"] == "MESH_COMPLETE" else "medicion parcial",
        )
        return self._serialize_height_map(height_map)

    def _ensure_completed_map_finalized(self, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if payload.get("source") != "MEASURED" or payload.get("status") != "MESH_COMPLETE":
            return payload
        validation = payload.get("validation") or {}
        if payload.get("map_ready_state") == "MAP_READY" and validation.get("status") in {"VALID", "INVALID"}:
            return payload
        finalized = self._finalize_completed_map(project_id, payload)
        finalized["updated_at"] = _iso_now()
        self._save(project_id, str(finalized["map_id"]), finalized)
        return finalized

    def _height_map_from_payload(self, payload: dict[str, Any]) -> HeightMap:
        grid = HeightGrid(**payload["grid"])
        region = ProbeRegion(**payload["probe_region"])
        samples = tuple(
            HeightSample(
                id=str(item["id"]),
                x_mm=float(item["x_mm"]),
                y_mm=float(item["y_mm"]),
                z_mm=None if item.get("z_mm") is None else float(item["z_mm"]),
                fila=int(item["fila"]),
                columna=int(item["columna"]),
                origen_datos=str(item.get("origen_datos", "measured")),
                estado_calidad=SampleQuality(str(item.get("estado_calidad", "valida"))),
                observacion=item.get("observacion"),
                incluida=bool(item.get("incluida", True)),
                residuo_plano_mm=item.get("residuo_plano_mm"),
            )
            for item in payload["muestras"]
        )
        return compute_height_map(
            proyecto_id=str(payload["proyecto_id"]),
            operacion_id=str(payload["operacion_id"]),
            version=int(payload.get("version", 1)),
            fuente_datos=str(payload.get("fuente_datos", "measured")),
            superficie_simulada=payload.get("superficie_simulada"),
            repeticion_simulacion=payload.get("repeticion_simulacion"),
            etiqueta_simulada=bool(payload.get("etiqueta_simulada", False)),
            grid=grid,
            probe_region=region,
            exclusion_zones=tuple(
                ExclusionZone(
                    id=str(zone.get("id") or "exclusion"),
                    nombre=str(zone.get("nombre") or zone.get("name") or "Exclusión"),
                    min_x_mm=float(zone.get("min_x_mm")),
                    min_y_mm=float(zone.get("min_y_mm")),
                    max_x_mm=float(zone.get("max_x_mm")),
                    max_y_mm=float(zone.get("max_y_mm")),
                )
                for zone in payload.get("exclusion_zones") or []
            ),
            muestras=list(samples),
            estado=str(payload.get("estado", "medido relativo")),
        )

    def _coverage_payload(self, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        project = self._load_project(project_id)
        height_map = self._height_map_from_payload(payload["height_map"])
        operation_ids = set(payload.get("operation_ids") or [])
        operations = tuple(
            (operation.id, operation.nombre, operation.analisis)
            for operation in project.operaciones
            if operation.id in operation_ids and operation.analisis is not None
        )
        coverage = build_coverage_report(height_map=height_map, operations=operations, tolerance_mm=DOMAIN_TOLERANCE_MM)
        return {
            "validated_at": _iso_now(),
            "status": "VALID" if coverage.sufficient else "INVALID",
            "sufficient": coverage.sufficient,
            "points_inside": coverage.points_inside,
            "points_outside": coverage.points_outside,
            "points_numerically_outside": coverage.points_numerically_outside,
            "blocking_outside_points": coverage.blocking_outside_points,
            "max_distance_outside_mm": coverage.max_distance_outside_mm,
            "tolerance_mm": coverage.tolerance_mm,
            "issues": [issue.__dict__ for issue in coverage.issues],
        }

    def _parse_datetime(self, value: Any) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            return None

    def _first_valid_tool_reference(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        for reference in (payload.get("tool_references") or {}).values():
            if isinstance(reference, dict) and reference.get("valid"):
                return reference
        return None

    def _finalize_completed_map(self, project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        finalized = dict(payload)
        finalized["source"] = "MEASURED"
        finalized["map_ready_state"] = "MAP_READY"
        finalized["validation"] = self._coverage_payload(project_id, finalized)
        self._sync_setup_preparation_from_completed_map(project_id, finalized)
        return finalized

    def _sync_setup_preparation_from_completed_map(self, project_id: str, payload: dict[str, Any]) -> None:
        project = self._load_project(project_id)
        setup = project.get_setup(str(payload["setup_id"]))
        timestamp = self._parse_datetime(payload.get("completed_at")) or self._parse_datetime(payload.get("updated_at")) or utc_now()
        reference = self._first_valid_tool_reference(payload)
        origin = CoordinateReference(
            x_mm=float(payload.get("machine_origin_x", 0.0)),
            y_mm=float(payload.get("machine_origin_y", 0.0)),
            z_mm=None,
            confirmado_en=timestamp,
            fuente="MEASURED",
            maquina=payload.get("machine_label"),
            homed_axes=payload.get("homed_axes"),
            posicion_captura=CapturedPosition(
                x_mm=float(payload.get("machine_origin_x", 0.0)),
                y_mm=float(payload.get("machine_origin_y", 0.0)),
                z_mm=None,
            ),
            sesion=payload.get("session_id"),
        )
        z_reference = None
        if reference is not None:
            z_reference = CoordinateReference(
                x_mm=float(reference.get("reference_x", payload.get("machine_origin_x", 0.0))),
                y_mm=float(reference.get("reference_y", payload.get("machine_origin_y", 0.0))),
                z_mm=float(reference.get("reference_z", payload.get("reference_z", 0.0))),
                confirmado_en=self._parse_datetime(reference.get("measured_at")) or timestamp,
                fuente=str(reference.get("source") or "MEASURED"),
                maquina=reference.get("machine_label") or payload.get("machine_label"),
                homed_axes=reference.get("homed_axes") or payload.get("homed_axes"),
                posicion_captura=CapturedPosition(
                    x_mm=float(reference.get("reference_x", payload.get("machine_origin_x", 0.0))),
                    y_mm=float(reference.get("reference_y", payload.get("machine_origin_y", 0.0))),
                    z_mm=float(reference.get("reference_z", payload.get("reference_z", 0.0))),
                ),
                sesion=reference.get("session_id") or payload.get("session_id"),
            )
        validation = payload.get("validation") or {}
        validation_ok = bool(validation.get("sufficient")) and validation.get("status") == "VALID"
        updated_setup = replace(
            setup,
            active_reference_id=None if reference is None else str(reference.get("installation_id") or reference.get("tool_id") or "measured"),
            active_map_id=str(payload["map_id"]),
            preparation_status=PreparationState.MAPA_VALIDADO if validation_ok else PreparationState.MAPA_DISPONIBLE,
            last_prepared_at=timestamp,
            preparacion=replace(
                setup.preparacion,
                origen_trabajo=origin,
                referencia_z=z_reference or setup.preparacion.referencia_z,
                region_sondeable_configurada_en=timestamp,
                mapa_disponible_en=timestamp,
                mapa_validado_en=timestamp if validation_ok else None,
                compensacion_previsualizada_en=None,
                motivo_invalidacion=None if validation_ok else "La cobertura del mapa físico medido no cubre todas las trayectorias.",
            ),
        )
        self.repository.save_project(project.replace_setup(updated_setup))

    def _serialize_height_map(self, height_map: HeightMap) -> dict[str, Any]:
        return {
            "proyecto_id": height_map.proyecto_id,
            "operacion_id": height_map.operacion_id,
            "version": height_map.version,
            "version_algoritmo": height_map.version_algoritmo,
            "estado": height_map.estado,
            "fuente_datos": height_map.fuente_datos,
            "superficie_simulada": height_map.superficie_simulada,
            "repeticion_simulacion": height_map.repeticion_simulacion,
            "etiqueta_simulada": height_map.etiqueta_simulada,
            "grid": height_map.grid.__dict__,
            "probe_region": height_map.probe_region.__dict__,
            "exclusion_zones": [zone.__dict__ for zone in height_map.exclusion_zones],
            "muestras": [sample.__dict__ | {"estado_calidad": sample.estado_calidad.value} for sample in height_map.muestras],
            "estadisticas": height_map.estadisticas.__dict__,
            "plano": None if height_map.plano is None else height_map.plano.__dict__,
            "creado_en": height_map.creado_en.isoformat(),
            "actualizado_en": height_map.actualizado_en.isoformat(),
        }

    def _map_prefix(self, setup_id: str, operation: OperacionPCB) -> str:
        return f"{_slug(setup_id)}/{_slug(operation.cara)}/placement-1"

    def _legacy_map_prefix(self, setup_id: str, operation: OperacionPCB) -> str:
        return f"{_slug(setup_id)}/{_slug(_tool_key(operation))}"

    def _map_id(self, setup_id: str, operation: OperacionPCB, origin_x: float, origin_y: float, config: PhysicalMeshConfig) -> str:
        stamp = utc_now().strftime("%Y%m%d-%H%M%S")
        return f"measured/{self._map_prefix(setup_id, operation)}/{stamp}_x{origin_x:.3f}_y{origin_y:.3f}_r{config.rows}_c{config.columns}_e{config.edge_margin_left_mm:.3f}-{config.edge_margin_right_mm:.3f}-{config.edge_margin_bottom_mm:.3f}-{config.edge_margin_top_mm:.3f}"

    def _latest_surface_map(self, project_id: str, operation: OperacionPCB) -> dict[str, Any] | None:
        maps_dir = self.repository.project_dir(project_id) / "maps" / "measured" / self._map_prefix(operation.setup_id, operation)
        if not maps_dir.exists():
            return None
        files = sorted(maps_dir.glob("*/height_map.json"), key=lambda item: item.stat().st_mtime, reverse=True)
        for file in files:
            payload = self._load_file(file)
            if payload.get("archived_at") is None and payload.get("status") != "ARCHIVED":
                return payload
        return None

    def _latest_legacy_tool_map(self, project_id: str, operation: OperacionPCB) -> dict[str, Any] | None:
        maps_dir = self.repository.project_dir(project_id) / "maps" / "measured" / self._legacy_map_prefix(operation.setup_id, operation)
        if not maps_dir.exists():
            return None
        files = sorted(maps_dir.glob("*/height_map.json"), key=lambda item: item.stat().st_mtime, reverse=True)
        for file in files:
            payload = self._load_file(file)
            if payload.get("archived_at") is None and payload.get("status") != "ARCHIVED":
                return payload
        return None

    def _compatible_surface_map(self, payload: dict[str, Any], operation: OperacionPCB, origin_x: float, origin_y: float, config: PhysicalMeshConfig) -> bool:
        if payload.get("schema_version") != "surface-map-v2":
            return False
        if payload.get("setup_id") != operation.setup_id or payload.get("face") != operation.cara:
            return False
        if abs(float(payload.get("machine_origin_x", origin_x)) - origin_x) > 0.001:
            return False
        if abs(float(payload.get("machine_origin_y", origin_y)) - origin_y) > 0.001:
            return False
        mesh_config = payload.get("mesh_config", {})
        checks = (
            int(mesh_config.get("rows", config.rows)) == config.rows,
            int(mesh_config.get("columns", config.columns)) == config.columns,
            str(mesh_config.get("grid_mode", "manual")) == config.grid_mode,
            abs(float(mesh_config.get("edge_margin_left_mm", config.edge_margin_left_mm)) - config.edge_margin_left_mm) <= 0.001,
            abs(float(mesh_config.get("edge_margin_right_mm", config.edge_margin_right_mm)) - config.edge_margin_right_mm) <= 0.001,
            abs(float(mesh_config.get("edge_margin_bottom_mm", config.edge_margin_bottom_mm)) - config.edge_margin_bottom_mm) <= 0.001,
            abs(float(mesh_config.get("edge_margin_top_mm", config.edge_margin_top_mm)) - config.edge_margin_top_mm) <= 0.001,
        )
        return all(checks)

    def _tool_reference(
        self,
        *,
        operation: OperacionPCB,
        reference_z: float,
        machine_position: dict[str, float],
        homed_axes: str | None,
        machine_label: str | None,
        session_id: str | None,
    ) -> dict[str, Any]:
        return {
            "setup_id": operation.setup_id,
            "tool_id": _tool_key(operation),
            "tool_name": operation.herramienta,
            "tool_diameter": _tool_diameter(operation),
            "installation_id": utc_now().strftime("%Y%m%d-%H%M%S"),
            "reference_x": float(machine_position["x_mm"]),
            "reference_y": float(machine_position["y_mm"]),
            "reference_z": reference_z,
            "measured_at": _iso_now(),
            "source": "MEASURED",
            "valid": True,
            "homed_axes": homed_axes,
            "machine_label": machine_label,
            "session_id": session_id,
        }

    def _with_tool_reference(self, payload: dict[str, Any], **kwargs) -> dict[str, Any]:
        updated = dict(payload)
        refs = dict(updated.get("tool_references") or {})
        operation: OperacionPCB = kwargs["operation"]
        refs[_tool_key(operation)] = self._tool_reference(**kwargs)
        updated["tool_references"] = refs
        return updated

    def _migrate_legacy_payload(self, payload: dict[str, Any], operation: OperacionPCB) -> dict[str, Any]:
        if payload.get("schema_version") == "surface-map-v2":
            return payload
        migrated = dict(payload)
        migrated["schema_version"] = "surface-map-v2"
        migrated["map_model"] = "SURFACE_BY_SETUP_FACE_PLACEMENT"
        migrated["face"] = operation.cara
        migrated["placement_revision"] = "placement-1"
        migrated["acquisition_tool_id"] = migrated.get("tool_id")
        migrated["acquisition_tool_name"] = migrated.get("tool_name")
        migrated["acquisition_tool_diameter"] = migrated.get("tool_diameter")
        migrated["acquisition_reference_z"] = migrated.get("reference_z")
        if "tool_references" not in migrated:
            migrated["tool_references"] = {
                str(migrated.get("tool_id") or _tool_key(operation)): {
                    "setup_id": operation.setup_id,
                    "tool_id": str(migrated.get("tool_id") or _tool_key(operation)),
                    "tool_name": migrated.get("tool_name") or operation.herramienta,
                    "tool_diameter": migrated.get("tool_diameter"),
                    "installation_id": "legacy",
                    "reference_x": migrated.get("machine_origin_x"),
                    "reference_y": migrated.get("machine_origin_y"),
                    "reference_z": migrated.get("reference_z"),
                    "measured_at": migrated.get("created_at"),
                    "source": "MEASURED",
                    "valid": True,
                }
            }
        reference_z = float(migrated.get("acquisition_reference_z") or 0.0)
        migrated_points = []
        for point in migrated.get("points", []):
            next_point = dict(point)
            measured = next_point.get("z_measured")
            next_point.setdefault("z_measured_abs", measured)
            next_point.setdefault("delta_z", None if measured is None else float(measured) - reference_z)
            next_point.setdefault("started_at", None)
            next_point.setdefault("measured_at", next_point.get("timestamp"))
            next_point.setdefault("last_error", next_point.get("error"))
            next_point.setdefault("duration", next_point.get("duration_s"))
            migrated_points.append(next_point)
        migrated["points"] = migrated_points
        mesh_config = migrated.setdefault("mesh_config", {})
        grid = migrated.get("grid", {})
        mesh_config.setdefault("rows", grid.get("rows", 2))
        mesh_config.setdefault("columns", grid.get("columns", 2))
        mesh_config.setdefault("edge_margin_left_mm", mesh_config.get("margin_mm", 2.0))
        mesh_config.setdefault("edge_margin_right_mm", mesh_config.get("margin_mm", 2.0))
        mesh_config.setdefault("edge_margin_bottom_mm", mesh_config.get("margin_mm", 2.0))
        mesh_config.setdefault("edge_margin_top_mm", mesh_config.get("margin_mm", 2.0))
        migrated.setdefault("edge_margins", {
            "left_mm": mesh_config.get("edge_margin_left_mm", 2.0),
            "right_mm": mesh_config.get("edge_margin_right_mm", 2.0),
            "bottom_mm": mesh_config.get("edge_margin_bottom_mm", 2.0),
            "top_mm": mesh_config.get("edge_margin_top_mm", 2.0),
        })
        migrated.setdefault("exclusions", [])
        migrated["excluded_count"] = sum(1 for point in migrated_points if point.get("status") == "EXCLUDED")
        migrated["executable_point_count"] = sum(1 for point in migrated_points if point.get("status") != "EXCLUDED")
        migrated["height_map"] = self._height_map_payload_from_points(migrated)
        return migrated


    def repeat_measurement(self, *, project_id: str, map_id: str) -> dict[str, Any]:
        active = self.get_by_id(project_id, map_id)
        if active.get("archived_at") is not None:
            raise ApplicationError("No se puede repetir una medición archivada.")
        project = self._load_project(project_id)
        operation_id = str((active.get("operation_ids") or [""])[0])
        operation = project.get_operation(operation_id)
        config_payload = active.get("mesh_config") or {}
        probe_payload = active.get("probe_config") or {}
        config = PhysicalMeshConfig(
            grid_mode=str(config_payload.get("grid_mode") or active.get("grid_mode") or "manual"),
            rows=int(config_payload.get("rows") or active.get("rows") or 2),
            columns=int(config_payload.get("columns") or active.get("columns") or 2),
            edge_margin_left_mm=float(config_payload.get("edge_margin_left_mm") or active.get("edge_margins", {}).get("left_mm") or 2.0),
            edge_margin_right_mm=float(config_payload.get("edge_margin_right_mm") or active.get("edge_margins", {}).get("right_mm") or 2.0),
            edge_margin_bottom_mm=float(config_payload.get("edge_margin_bottom_mm") or active.get("edge_margins", {}).get("bottom_mm") or 2.0),
            edge_margin_top_mm=float(config_payload.get("edge_margin_top_mm") or active.get("edge_margins", {}).get("top_mm") or 2.0),
            exclusions=tuple(PhysicalExclusion(**item) for item in active.get("exclusions", [])),
            max_spacing_mm=float(config_payload.get("max_spacing_mm") or 10.0),
            margin_mm=float(config_payload.get("margin_mm") or 0.0),
            safe_z_mm=probe_payload.get("safe_z_mm"),
            probe_step_mm=probe_payload.get("probe_step_mm"),
            probe_feed_mm_min=probe_payload.get("probe_feed_mm_min"),
            retract_mm=probe_payload.get("retract_mm"),
        )
        region = self._material_inner_region(project, config)
        grid = self._grid_for_region(region, config.rows, config.columns)
        samples = self._blank_samples(grid, region, config.exclusions)
        height_map = compute_height_map(
            proyecto_id=project_id,
            operacion_id=operation.setup_id,
            version=int(active.get("version") or 1) + 1,
            fuente_datos="measured",
            superficie_simulada=None,
            repeticion_simulacion=None,
            etiqueta_simulada=False,
            grid=grid,
            probe_region=region,
            exclusion_zones=self._rectangular_exclusion_zones(config.exclusions),
            muestras=samples,
            estado="repeticion planificada",
        )
        next_version = int(active.get("version") or 1) + 1
        new_map_id = f"{str(active['map_id']).rstrip('/')}/repeat-{next_version}-{utc_now().strftime('%Y%m%d-%H%M%S-%f')}"
        payload = self._payload(
            project_id=project_id,
            setup_id=operation.setup_id,
            operation=operation,
            operations=self._operations_for_setup_face(project, operation) or (operation,),
            map_id=new_map_id,
            machine_origin_x=float(active.get("machine_origin_x") or 0.0),
            machine_origin_y=float(active.get("machine_origin_y") or 0.0),
            reference_z=float(active.get("reference_z") or active.get("acquisition_reference_z") or 0.0),
            machine_position=active.get("machine_position") or {},
            homed_axes=active.get("homed_axes"),
            machine_label=active.get("machine_label"),
            session_id=active.get("session_id"),
            config=config,
            height_map=height_map,
            status="REPROBE_CONFIRMATION",
            placement_revision=active.get("placement_revision") or project.get_setup(operation.setup_id).placement_revision,
        )
        payload["version"] = next_version
        payload["replaces"] = active.get("map_id")
        payload["map_ready_state"] = None
        payload["validation"] = None
        payload["message"] = "El mapa cambió. Debe volver a validar la cobertura y regenerar el G-code compensado."
        self._archive_payload(project_id, active, replaced_by=new_map_id)
        self._save(project_id, new_map_id, payload)
        self._mark_setup_map_active(project_id, operation.setup_id, new_map_id)
        return payload

    def history(self, *, project_id: str, operation_id: str) -> list[dict[str, Any]]:
        project = self._load_project(project_id)
        operation = project.get_operation(operation_id)
        maps_dir = self.repository.project_dir(project_id) / "maps" / "measured" / self._map_prefix(operation.setup_id, operation)
        if not maps_dir.exists():
            return []
        active_map_id = project.get_setup(operation.setup_id).active_map_id
        entries: list[dict[str, Any]] = []
        files = sorted(maps_dir.glob("**/height_map.json"), key=lambda item: item.stat().st_mtime, reverse=True)
        for file in files:
            payload = self._load_file(file)
            if payload.get("setup_id") != operation.setup_id or payload.get("face") != operation.cara:
                continue
            stats = (payload.get("height_map") or {}).get("estadisticas") or {}
            points = payload.get("points") or []
            entries.append({
                "map_id": payload.get("map_id"),
                "version": payload.get("version"),
                "date": payload.get("completed_at") or payload.get("updated_at") or payload.get("created_at"),
                "placement_revision": payload.get("placement_revision"),
                "rows": payload.get("rows"),
                "columns": payload.get("columns"),
                "points_measured": sum(1 for point in points if point.get("status") == "MEASURED"),
                "points_failed": sum(1 for point in points if point.get("status") in {"FAILED", "RETRY_REQUIRED"}),
                "min": stats.get("altura_min_mm"),
                "max": stats.get("altura_max_mm"),
                "range": stats.get("rango_alturas_mm"),
                "rms": stats.get("desviacion_rms_respecto_plano_mm"),
                "tool": payload.get("acquisition_tool_name") or payload.get("tool_name"),
                "status": payload.get("status"),
                "active": payload.get("map_id") == active_map_id and payload.get("archived_at") is None,
                "archived_at": payload.get("archived_at"),
            })
        return entries

    def reset_map(self, *, project_id: str, setup_id: str, reason: str | None = None, user_session: str | None = None) -> dict[str, Any]:
        project = self._load_project(project_id)
        setup = project.get_setup(setup_id)
        if setup.active_map_id:
            active = self.get_by_id(project_id, setup.active_map_id)
            self._archive_payload(project_id, active, replaced_by=None)
        updated_setup = replace(
            setup,
            active_map_id=None,
            preparation_status=PreparationState.REFERENCIA_Z_CONFIRMADA,
            preparacion=replace(
                setup.preparacion,
                region_sondeable_configurada_en=None,
                mapa_disponible_en=None,
                mapa_validado_en=None,
                compensacion_previsualizada_en=None,
                motivo_invalidacion=reason or "Mapa activo reiniciado. Debe configurar y confirmar una nueva malla.",
            ),
        )
        self.repository.save_project(project.replace_setup(updated_setup))
        return {"status": "map_reset", "setup_id": setup_id, "placement_revision": setup.placement_revision, "reason": reason, "user_session": user_session}

    def reset_reference(self, *, project_id: str, setup_id: str, reason: str | None = None, user_session: str | None = None) -> dict[str, Any]:
        project = self._load_project(project_id)
        setup = project.get_setup(setup_id)
        if setup.active_map_id:
            active = self.get_by_id(project_id, setup.active_map_id)
            self._archive_payload(project_id, active, replaced_by=None)
        next_revision = self._next_revision(setup.placement_revision)
        updated_setup = replace(
            setup,
            placement_revision=next_revision,
            active_reference_id=None,
            active_map_id=None,
            preparation_status=PreparationState.SIN_INICIAR,
            last_prepared_at=None,
            preparacion=OperationPreparation(motivo_invalidacion=reason or "Referencia reiniciada. Debe repetir origen X/Y, referencia Z y malla."),
        )
        self.repository.save_project(project.replace_setup(updated_setup))
        return {"status": "reference_reset", "setup_id": setup_id, "previous_placement_revision": setup.placement_revision, "placement_revision": next_revision, "reason": reason, "user_session": user_session}

    def reset_preparation(self, *, project_id: str, setup_id: str, reason: str | None = None, user_session: str | None = None) -> dict[str, Any]:
        return self.reset_reference(project_id=project_id, setup_id=setup_id, reason=reason or "Preparación completa reiniciada.", user_session=user_session)

    def _next_revision(self, current: str) -> str:
        try:
            number = int(str(current).rsplit("-", 1)[-1])
        except ValueError:
            number = 1
        return f"placement-{number + 1}"

    def _archive_payload(self, project_id: str, payload: dict[str, Any], replaced_by: str | None) -> None:
        archived = dict(payload)
        archived["archived_at"] = archived.get("archived_at") or _iso_now()
        archived["replaced_by"] = replaced_by
        archived["status"] = "ARCHIVED" if archived.get("status") not in {"MESH_COMPLETE"} else archived.get("status")
        self._save(project_id, str(archived["map_id"]), archived)

    def _mark_setup_map_active(self, project_id: str, setup_id: str, map_id: str) -> None:
        project = self._load_project(project_id)
        setup = project.get_setup(setup_id)
        now = utc_now()
        updated_setup = replace(
            setup,
            active_map_id=map_id,
            preparation_status=PreparationState.MAPA_DISPONIBLE,
            last_prepared_at=now,
            preparacion=replace(
                setup.preparacion,
                region_sondeable_configurada_en=now,
                mapa_disponible_en=now,
                mapa_validado_en=None,
                compensacion_previsualizada_en=None,
                motivo_invalidacion=None,
            ),
        )
        self.repository.save_project(project.replace_setup(updated_setup))

    def _save(self, project_id: str, map_id: str, payload: dict[str, Any]) -> None:
        with self._io_lock:
            self.repository.save_height_map_payload(project_id, map_id, payload)

    def _load(self, project_id: str, map_id: str) -> dict[str, Any]:
        try:
            with self._io_lock:
                return self.repository.load_height_map_payload(project_id, map_id)
        except FileNotFoundError as error:
            raise NotFoundError(str(error)) from error

    def _load_file(self, path) -> dict[str, Any]:
        import json
        return json.loads(path.read_text(encoding="utf-8"))

    def _load_project(self, project_id: str):
        try:
            return self.repository.load_project(project_id)
        except FileNotFoundError as error:
            raise NotFoundError(str(error)) from error
        except ProjectValidationError as error:
            raise ApplicationError(str(error)) from error
