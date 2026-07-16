from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from klipper_cnc_assistant.application.errors import ApplicationError, NotFoundError
from klipper_cnc_assistant.domain import OperacionPCB, ProjectValidationError
from klipper_cnc_assistant.heightmap import HeightGrid, HeightMap, HeightSample, ProbeRegion, SampleQuality, interpolate_height
from klipper_cnc_assistant.heightmap.coverage import DOMAIN_TOLERANCE_MM, build_coverage_report, segment_uses_surface_map
from klipper_cnc_assistant.storage import JsonProjectRepository


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp() -> str:
    return _now().strftime("%Y%m%d-%H%M%S")


def _tool_key(operation: OperacionPCB) -> str:
    return operation.tool_id or (operation.herramienta or "sin-herramienta").strip().lower().replace(" ", "-")


@dataclass(frozen=True)
class GeneratedGCodeResult:
    relative_path: str
    metadata_path: str
    metadata: dict[str, Any]
    preview: dict[str, Any]


class CompensatedGCodeService:
    ALGORITHM_VERSION = "compensated-gcode-v1"

    def __init__(self, repository: JsonProjectRepository, physical_map_service) -> None:
        self.repository = repository
        self.physical_map_service = physical_map_service

    def generate(self, project_id: str, operation_id: str, *, max_segment_mm: float | None = None) -> dict[str, Any]:
        project = self._load_project(project_id)
        operation = project.get_operation(operation_id)
        if operation.analisis is None:
            raise ApplicationError("La operación requiere análisis G-code antes de generar compensación.")
        if not operation.archivo_gcode:
            raise ApplicationError("La operación no tiene archivo G-code original asociado.")

        physical_map = self.physical_map_service.get_active(project_id, operation_id)
        self._validate_map_for_operation(physical_map, operation)
        height_map = self._height_map_from_payload(physical_map["height_map"])
        coverage = build_coverage_report(
            height_map=height_map,
            operations=((operation.id, operation.nombre, operation.analisis),),
            tolerance_mm=DOMAIN_TOLERANCE_MM,
        )
        if not coverage.sufficient:
            first = coverage.issues[0] if coverage.issues else None
            detail = ""
            if first:
                detail = f" Primer punto fuera: línea/segmento {first.segment_index}, X={first.x_mm:.3f}, Y={first.y_mm:.3f}, distancia={first.distance_mm:.3f} mm."
            raise ApplicationError(
                "Mapa insuficiente: hay puntos de trayectoria fuera del dominio medido. "
                "Amplíe la región medida antes de compensar." + detail
            )

        original = self.repository.read_project_file(project_id, operation.archivo_gcode)
        original_hash = hashlib.sha256(original.encode("utf-8")).hexdigest()
        sample_spacing = self._sample_spacing_mm(height_map)
        segment_limit = max_segment_mm or max(0.25, sample_spacing / 2.0)
        lines, preview = self._build_compensated_lines(operation, height_map, segment_limit)
        output = "\n".join(lines) + "\n"
        map_hash = hashlib.sha256(json.dumps(physical_map, sort_keys=True).encode("utf-8")).hexdigest()

        relative_dir = Path("generated") / "compensated"
        safe_name = f"{operation.id}_{_stamp()}_compensated.gcode"
        metadata_name = f"{operation.id}_{_stamp()}_compensated.json"
        project_dir = self.repository.project_dir(project_id)
        (project_dir / relative_dir).mkdir(parents=True, exist_ok=True)
        relative_path = relative_dir / safe_name
        metadata_path = relative_dir / metadata_name
        metadata = {
            "project_id": project_id,
            "setup_id": operation.setup_id,
            "operation_id": operation.id,
            "operation_name": operation.nombre,
            "tool_id": _tool_key(operation),
            "tool_name": operation.herramienta,
            "map_id": physical_map["map_id"],
            "map_hash": map_hash,
            "reference_required": physical_map.get("tool_references", {}).get(_tool_key(operation)),
            "created_at": _now().isoformat(),
            "original_path": operation.archivo_gcode,
            "original_hash": original_hash,
            "generated_hash": hashlib.sha256(output.encode("utf-8")).hexdigest(),
            "algorithm_version": self.ALGORITHM_VERSION,
            "max_segment_mm": segment_limit,
            "warnings": preview["warnings"],
            "convention": "x_compensado=x_original, y_compensado=y_original, z_compensado=z_original+delta_superficie(x,y)",
        }
        (project_dir / relative_path).write_text(output, encoding="utf-8")
        (project_dir / metadata_path).write_text(json.dumps(metadata, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
        return GeneratedGCodeResult(
            relative_path=relative_path.as_posix(),
            metadata_path=metadata_path.as_posix(),
            metadata=metadata,
            preview=preview,
        ).__dict__

    def resolve_generated_file(self, project_id: str, relative_path: str) -> Path:
        if not relative_path.startswith("generated/compensated/"):
            raise ApplicationError("Solo se permite descargar archivos compensados generados.")
        project_dir = self.repository.project_dir(project_id)
        target = (project_dir / relative_path).resolve()
        root = project_dir.resolve()
        if target != root and root not in target.parents:
            raise ApplicationError("La ruta solicitada sale del directorio del proyecto.")
        if not target.exists():
            raise NotFoundError("El archivo compensado solicitado no existe.")
        return target

    def _validate_map_for_operation(self, physical_map: dict[str, Any], operation: OperacionPCB) -> None:
        if physical_map.get("schema_version") != "surface-map-v2":
            raise ApplicationError("El mapa medido usa un modelo anterior. Abra el mapa físico para migrarlo antes de compensar.")
        if physical_map.get("setup_id") != operation.setup_id or physical_map.get("face") != operation.cara:
            raise ApplicationError("Mapa incorrecto: pertenece a otro montaje o cara de PCB.")
        if physical_map.get("status") != "MESH_COMPLETE":
            raise ApplicationError("Mapa incompleto: termine o recupere el sondeo antes de compensar.")
        reference = (physical_map.get("tool_references") or {}).get(_tool_key(operation))
        if not reference or not reference.get("valid"):
            raise ApplicationError("Referencia Z inválida o ausente para la herramienta requerida por esta operación.")
        if operation.id not in set(physical_map.get("operation_ids") or []):
            raise ApplicationError("La operación no está cubierta por el mapa medido activo.")

    def _build_compensated_lines(self, operation: OperacionPCB, height_map: HeightMap, max_segment_mm: float) -> tuple[list[str], dict[str, Any]]:
        lines = [
            "; Klipper CNC Assistant - G-code compensado",
            f"; Operacion: {operation.nombre} ({operation.id})",
            f"; Algoritmo: {self.ALGORITHM_VERSION}",
            "; Convencion: X/Y se conservan, Z += delta_superficie(x,y)",
            "G21",
            "G90",
        ]
        warnings: list[str] = []
        compensated_z: list[float] = []
        emitted_points = 0
        for segment in operation.analisis.segmentos_vista_previa:
            points = segment.puntos or (segment.desde, segment.hasta)
            sampled = self._sample_points(points, max_segment_mm)
            uses_surface = segment_uses_surface_map(segment)
            for point in sampled[1:] if len(sampled) > 1 else sampled:
                if segment.z_mm is None:
                    lines.append(self._format_move(point.x_mm, point.y_mm, None, segment.avance_mm_min))
                    continue
                if not uses_surface:
                    lines.append(self._format_move(point.x_mm, point.y_mm, segment.z_mm, segment.avance_mm_min))
                    continue
                interpolation = interpolate_height(height_map, x_mm=point.x_mm, y_mm=point.y_mm, mode="bruto")
                if interpolation.valor_mm is None:
                    raise ApplicationError(
                        f"No se puede compensar línea {segment.numero_linea}: X={point.x_mm:.3f}, Y={point.y_mm:.3f}. {interpolation.observacion or interpolation.estado}"
                    )
                z_comp = segment.z_mm + interpolation.valor_mm
                compensated_z.append(z_comp)
                emitted_points += 1
                lines.append(self._format_move(point.x_mm, point.y_mm, z_comp, segment.avance_mm_min))
        if not compensated_z:
            warnings.append("No se encontraron movimientos con Z explícita para compensar.")
        return lines, {
            "emitted_points": emitted_points,
            "warnings": warnings,
            "z_compensated_min_mm": min(compensated_z) if compensated_z else None,
            "z_compensated_max_mm": max(compensated_z) if compensated_z else None,
        }

    def _format_move(self, x_mm: float, y_mm: float, z_mm: float | None, feed_mm_min: float | None) -> str:
        parts = ["G1", f"X{x_mm:.5f}", f"Y{y_mm:.5f}"]
        if z_mm is not None:
            parts.append(f"Z{z_mm:.5f}")
        if feed_mm_min is not None:
            parts.append(f"F{feed_mm_min:.3f}")
        return " ".join(parts)

    def _sample_points(self, points, spacing_mm: float):
        if len(points) <= 1:
            return tuple(points)
        sampled = [points[0]]
        point_type = type(points[0])
        for start, end in zip(points, points[1:]):
            distance = ((end.x_mm - start.x_mm) ** 2 + (end.y_mm - start.y_mm) ** 2) ** 0.5
            subdivisions = max(1, math.ceil(distance / spacing_mm))
            for index in range(1, subdivisions + 1):
                progress = index / subdivisions
                sampled.append(point_type(x_mm=start.x_mm + (end.x_mm - start.x_mm) * progress, y_mm=start.y_mm + (end.y_mm - start.y_mm) * progress))
        return tuple(sampled)

    def _sample_spacing_mm(self, height_map: HeightMap) -> float:
        candidates = [value for value in (height_map.grid.paso_x_mm, height_map.grid.paso_y_mm) if value > 0]
        return min(candidates) if candidates else 1.0

    def _height_map_from_payload(self, payload: dict[str, Any]) -> HeightMap:
        grid = HeightGrid(**payload["grid"])
        region = ProbeRegion(**payload["probe_region"])
        samples = tuple(
            HeightSample(
                id=item["id"],
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
        from klipper_cnc_assistant.heightmap import compute_height_map

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
            exclusion_zones=(),
            muestras=list(samples),
            estado=str(payload.get("estado", "medido relativo")),
        )

    def _load_project(self, project_id: str):
        try:
            return self.repository.load_project(project_id)
        except FileNotFoundError as error:
            raise NotFoundError(str(error)) from error
        except ProjectValidationError as error:
            raise ApplicationError(str(error)) from error
