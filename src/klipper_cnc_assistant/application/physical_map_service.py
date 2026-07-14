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
    raw = (value or "sin-herramienta").strip().lower()
    return re.sub(r"[^a-z0-9._-]+", "-", raw).strip("-") or "sin-herramienta"


def _tool_key(operation: OperacionPCB) -> str:
    return operation.tool_id or _slug(operation.herramienta)


def _tool_diameter(operation: OperacionPCB) -> float | None:
    values = re.findall(r"\d+(?:[\.,]\d+)?", operation.herramienta or "")
    if not values:
        return None
    return float(values[0].replace(",", "."))


@dataclass(frozen=True)
class PhysicalMeshConfig:
    max_spacing_mm: float = 10.0
    margin_mm: float = 1.0

    def __post_init__(self) -> None:
        if self.max_spacing_mm <= 0:
            raise ApplicationError("La separación máxima de malla debe ser positiva.")
        if self.margin_mm < 0:
            raise ApplicationError("El margen de malla no puede ser negativo.")


class PhysicalMapService:
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
        operations = self._operations_for_same_tool(project, operation)
        if not operations:
            raise ApplicationError("No hay operaciones válidas para la herramienta seleccionada.")
        local_region = self._union_region(project, operations, selected_config)
        grid = self._grid_for_region(local_region, selected_config.max_spacing_mm)
        samples = self._blank_samples(grid, local_region)
        height_map = compute_height_map(
            proyecto_id=project_id,
            operacion_id=operation_id,
            version=1,
            fuente_datos="measured",
            superficie_simulada=None,
            repeticion_simulacion=None,
            etiqueta_simulada=False,
            grid=grid,
            probe_region=local_region,
            exclusion_zones=(),
            muestras=samples,
            estado="malla planificada",
        )
        map_id = self._map_id(operation.setup_id, operation, machine_origin_x, machine_origin_y, reference_z, selected_config)
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
        prefix = self._map_prefix(operation.setup_id, operation)
        maps_dir = self.repository.project_dir(project_id) / "maps" / "measured" / prefix
        if not maps_dir.exists():
            raise NotFoundError("No existe mapa físico medido para esta herramienta.")
        files = sorted(maps_dir.glob("*/height_map.json"), key=lambda item: item.stat().st_mtime, reverse=True)
        if not files:
            raise NotFoundError("No existe mapa físico medido para esta herramienta.")
        return self._load_file(files[0])

    def get_by_id(self, project_id: str, map_id: str) -> dict[str, Any]:
        return self._load(project_id, map_id)

    def next_pending_point(self, project_id: str, map_id: str) -> dict[str, Any]:
        payload = self._load(project_id, map_id)
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
        payload = self._load(project_id, map_id)
        points = payload["points"]
        if point_index < 0 or point_index >= len(points):
            raise ApplicationError("Índice de punto fuera de rango.")
        point = dict(points[point_index])
        point.update({
            "z_measured": z_measured,
            "timestamp": _iso_now(),
            "status": status,
            "attempts": attempts if attempts is not None else int(point.get("attempts", 0)) + 1,
            "duration_s": duration_s,
            "error": error,
        })
        points[point_index] = point
        payload["points"] = points
        payload["status"] = "MESH_COMPLETE" if all(item.get("status") == "MEASURED" for item in points) else "MESH_PROBING"
        payload["updated_at"] = _iso_now()
        payload["height_map"] = self._height_map_payload_from_points(payload)
        self._save(project_id, map_id, payload)
        return payload

    def mark_status(self, *, project_id: str, map_id: str, status: str) -> dict[str, Any]:
        payload = self._load(project_id, map_id)
        payload["status"] = status
        payload["updated_at"] = _iso_now()
        self._save(project_id, map_id, payload)
        return payload

    def _operations_for_same_tool(self, project, operation: OperacionPCB) -> tuple[OperacionPCB, ...]:
        key = _tool_key(operation)
        return tuple(
            item
            for item in project.operations_for_setup(operation.setup_id)
            if _tool_key(item) == key and item.analisis is not None and item.analisis.limites is not None
        )

    def _union_region(self, project, operations: tuple[OperacionPCB, ...], config: PhysicalMeshConfig) -> ProbeRegion:
        min_x = min(item.analisis.limites.min_x_mm for item in operations if item.analisis and item.analisis.limites) - config.margin_mm
        max_x = max(item.analisis.limites.max_x_mm for item in operations if item.analisis and item.analisis.limites) + config.margin_mm
        min_y = min(item.analisis.limites.min_y_mm for item in operations if item.analisis and item.analisis.limites) - config.margin_mm
        max_y = max(item.analisis.limites.max_y_mm for item in operations if item.analisis and item.analisis.limites) + config.margin_mm
        min_x = max(0.0, min_x)
        min_y = max(0.0, min_y)
        max_x = min(project.material.ancho_mm, max_x)
        max_y = min(project.material.alto_mm, max_y)
        if max_x < min_x or max_y < min_y:
            raise ApplicationError("La región de malla calculada no es válida.")
        return ProbeRegion(min_x_mm=min_x, min_y_mm=min_y, max_x_mm=max_x, max_y_mm=max_y)

    def _grid_for_region(self, region: ProbeRegion, max_spacing: float) -> HeightGrid:
        columns = max(1, math.ceil(region.ancho_mm / max_spacing) + 1)
        rows = max(1, math.ceil(region.alto_mm / max_spacing) + 1)
        step_x = 0.0 if columns == 1 else region.ancho_mm / (columns - 1)
        step_y = 0.0 if rows == 1 else region.alto_mm / (rows - 1)
        return HeightGrid(filas=rows, columnas=columns, ancho_mm=region.ancho_mm, alto_mm=region.alto_mm, paso_x_mm=step_x, paso_y_mm=step_y)

    def _blank_samples(self, grid: HeightGrid, region: ProbeRegion) -> list[HeightSample]:
        samples: list[HeightSample] = []
        for row in range(grid.filas):
            columns = range(grid.columnas) if row % 2 == 0 else range(grid.columnas - 1, -1, -1)
            for column in columns:
                samples.append(HeightSample(
                    id=f"measured_{row}_{column}",
                    x_mm=region.min_x_mm + (0.0 if grid.columnas == 1 else column * grid.paso_x_mm),
                    y_mm=region.min_y_mm + (0.0 if grid.filas == 1 else row * grid.paso_y_mm),
                    z_mm=None,
                    fila=row,
                    columna=column,
                    origen_datos="measured",
                    estado_calidad=SampleQuality.FALTANTE,
                    observacion="Pendiente de sondeo físico.",
                ))
        return samples

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
                "timestamp": None,
                "status": "PENDING",
                "attempts": 0,
                "duration_s": None,
                "error": None,
                "tool_id": _tool_key(operation),
                "setup_id": operation.setup_id,
            })
        return {
            "map_id": kwargs["map_id"],
            "project_id": kwargs["project_id"],
            "setup_id": operation.setup_id,
            "tool_id": _tool_key(operation),
            "tool_name": operation.herramienta,
            "tool_diameter": _tool_diameter(operation),
            "operation_ids": [item.id for item in operations],
            "source": "MEASURED",
            "status": kwargs["status"],
            "created_at": _iso_now(),
            "updated_at": _iso_now(),
            "machine_origin_x": origin_x,
            "machine_origin_y": origin_y,
            "reference_z": float(kwargs["reference_z"]),
            "machine_position": kwargs["machine_position"],
            "homed_axes": kwargs["homed_axes"],
            "machine_label": kwargs["machine_label"],
            "session_id": kwargs["session_id"],
            "mesh_config": {"max_spacing_mm": config.max_spacing_mm, "margin_mm": config.margin_mm},
            "probe_config": {},
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
            "estimated_distance_mm": total_distance,
            "estimated_time_s": None,
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
        samples = [
            HeightSample(
                id=f"measured_{point['row']}_{point['column']}",
                x_mm=point["x_local"],
                y_mm=point["y_local"],
                z_mm=point.get("z_measured"),
                fila=point["row"],
                columna=point["column"],
                origen_datos="measured",
                estado_calidad=SampleQuality.FALTANTE if point.get("z_measured") is None else SampleQuality.VALIDA,
                observacion=point.get("error"),
            )
            for point in payload["points"]
        ]
        height_map = compute_height_map(
            proyecto_id=payload["project_id"],
            operacion_id=payload["operation_ids"][0],
            version=int(payload.get("height_map", {}).get("version", 0)) + 1,
            fuente_datos="measured",
            superficie_simulada=None,
            repeticion_simulacion=None,
            etiqueta_simulada=False,
            grid=grid,
            probe_region=region,
            exclusion_zones=(),
            muestras=samples,
            estado="medido" if payload["status"] == "MESH_COMPLETE" else "medicion parcial",
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
        return f"{_slug(setup_id)}/{_slug(_tool_key(operation))}"

    def _map_id(self, setup_id: str, operation: OperacionPCB, origin_x: float, origin_y: float, reference_z: float, config: PhysicalMeshConfig) -> str:
        stamp = utc_now().strftime("%Y%m%d-%H%M%S")
        return f"measured/{self._map_prefix(setup_id, operation)}/{stamp}_x{origin_x:.3f}_y{origin_y:.3f}_z{reference_z:.3f}_s{config.max_spacing_mm:.3f}"

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
