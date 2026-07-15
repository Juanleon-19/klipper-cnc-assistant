from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from klipper_cnc_assistant.application.errors import ApplicationError, NotFoundError
from klipper_cnc_assistant.domain import OperacionPCB, ProjectValidationError
from klipper_cnc_assistant.heightmap import (
    ExclusionZone,
    HeightGrid,
    HeightMap,
    HeightSample,
    ProbeRegion,
    SampleQuality,
    compute_height_map,
)
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
        )
        self._save(project_id, map_id, payload)
        return payload

    def get_active(self, project_id: str, operation_id: str) -> dict[str, Any]:
        project = self._load_project(project_id)
        operation = project.get_operation(operation_id)
        payload = self._latest_surface_map(project_id, operation)
        if payload is not None:
            return payload
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
        return payload

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
        point.update({
            "z_measured": z_measured,
            "z_measured_abs": z_measured,
            "delta_z": z_measured - reference_z,
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
        payload["updated_at"] = measured_at
        payload["height_map"] = self._height_map_payload_from_points(payload)
        self._save(project_id, map_id, payload)
        return payload

    def mark_status(self, *, project_id: str, map_id: str, status: str) -> dict[str, Any]:
        payload = self.get_by_id(project_id, map_id)
        payload["status"] = status
        payload["updated_at"] = _iso_now()
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
        payload["height_map"] = self._height_map_payload_from_points(payload)
        self._save(project_id, map_id, payload)
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

    def _estimate_time_s(self, points: list[dict[str, Any]], distance_mm: float, config: PhysicalMeshConfig) -> float | None:
        executable = sum(1 for point in points if point.get("status") != "EXCLUDED")
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
        points = []
        total_distance = 0.0
        previous: tuple[float, float] | None = None
        for index, sample in enumerate(height_map.muestras):
            machine_x = origin_x + sample.x_mm
            machine_y = origin_y + sample.y_mm
            exclusion = self._exclusion_for_point(sample.x_mm, sample.y_mm, config.exclusions)
            is_excluded = exclusion is not None
            if not is_excluded:
                if previous is not None:
                    total_distance += math.dist(previous, (machine_x, machine_y))
                previous = (machine_x, machine_y)
            points.append({
                "index": index,
                "row": sample.fila,
                "column": sample.columna,
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
            "placement_revision": "placement-1",
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
            "grid": {"rows": height_map.grid.filas, "columns": height_map.grid.columnas, "dx_mm": height_map.grid.paso_x_mm, "dy_mm": height_map.grid.paso_y_mm},
            "point_count": len(points),
            "excluded_count": sum(1 for point in points if point.get("status") == "EXCLUDED"),
            "executable_point_count": sum(1 for point in points if point.get("status") != "EXCLUDED"),
            "estimated_distance_mm": total_distance,
            "estimated_time_s": self._estimate_time_s(points, total_distance, config),
            "invalid_points": [],
            "points": points,
            "height_map": self._serialize_height_map(height_map),
        }

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
        return None if not files else self._load_file(files[0])

    def _latest_legacy_tool_map(self, project_id: str, operation: OperacionPCB) -> dict[str, Any] | None:
        maps_dir = self.repository.project_dir(project_id) / "maps" / "measured" / self._legacy_map_prefix(operation.setup_id, operation)
        if not maps_dir.exists():
            return None
        files = sorted(maps_dir.glob("*/height_map.json"), key=lambda item: item.stat().st_mtime, reverse=True)
        return None if not files else self._load_file(files[0])

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

    def _save(self, project_id: str, map_id: str, payload: dict[str, Any]) -> None:
        self.repository.save_height_map_payload(project_id, map_id, payload)

    def _load(self, project_id: str, map_id: str) -> dict[str, Any]:
        try:
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
