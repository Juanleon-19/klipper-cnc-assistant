from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from klipper_cnc_assistant.application.adaptive_compensation import (
    ReferenceFrame as AdaptiveReferenceFrame,
    generate_adaptive_gcode,
)
from klipper_cnc_assistant.application.errors import ApplicationError, NotFoundError
from klipper_cnc_assistant.domain import CompensationMode, OperacionPCB, ProjectValidationError
from klipper_cnc_assistant.gcode import analyze_gcode_text
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


@dataclass(frozen=True)
class PcbCoordinates:
    x_mm: float
    y_mm: float


@dataclass(frozen=True)
class MachineCoordinates:
    x_mm: float
    y_mm: float
    z_mm: float | None


@dataclass(frozen=True)
class ReferenceFrame:
    machine_origin_x_mm: float
    machine_origin_y_mm: float
    surface_reference_z_mm: float

    def pcb_to_machine(self, point: PcbCoordinates) -> MachineCoordinates:
        return MachineCoordinates(self.machine_origin_x_mm + point.x_mm, self.machine_origin_y_mm + point.y_mm, None)


@dataclass(frozen=True)
class CompensatedMachineCoordinates:
    machine: MachineCoordinates
    surface_z_machine_mm: float | None
    delta_z_mm: float | None


def compensate_cut_point(*, pcb: PcbCoordinates, programmed_z_mm: float | None, surface_map: HeightMap, reference_frame: ReferenceFrame, uses_surface_map: bool) -> CompensatedMachineCoordinates:
    machine = reference_frame.pcb_to_machine(pcb)
    if programmed_z_mm is None:
        return CompensatedMachineCoordinates(machine, None, None)
    if not uses_surface_map:
        return CompensatedMachineCoordinates(MachineCoordinates(machine.x_mm, machine.y_mm, reference_frame.surface_reference_z_mm + programmed_z_mm), reference_frame.surface_reference_z_mm, 0.0)
    interpolation = interpolate_height(surface_map, x_mm=pcb.x_mm, y_mm=pcb.y_mm, mode="bruto")
    if interpolation.valor_mm is None:
        raise ApplicationError(f"No se puede compensar X={pcb.x_mm:.3f}, Y={pcb.y_mm:.3f}. {interpolation.observacion or interpolation.estado}")
    delta_z = float(interpolation.valor_mm)
    surface_z = reference_frame.surface_reference_z_mm + delta_z
    return CompensatedMachineCoordinates(MachineCoordinates(machine.x_mm, machine.y_mm, surface_z + programmed_z_mm), surface_z, delta_z)


class CompensatedGCodeService:
    LEGACY_ALGORITHM_VERSION = "compensated-gcode-v1"
    ADAPTIVE_ALGORITHM_VERSION = "adaptive-fast-v1"

    def __init__(self, repository: JsonProjectRepository, physical_map_service) -> None:
        self.repository = repository
        self.physical_map_service = physical_map_service

    def generate(
        self,
        project_id: str,
        operation_id: str,
        *,
        mode: str | None = None,
        max_segment_mm: float | None = None,
        require_tool_reference: bool = True,
    ) -> dict[str, Any]:
        project = self._load_project(project_id)
        operation = project.get_operation(operation_id)
        if operation.analisis is None:
            raise ApplicationError("La operación requiere análisis G-code antes de generar compensación.")
        if not operation.archivo_gcode:
            raise ApplicationError("La operación no tiene archivo G-code original asociado.")

        physical_map = self.physical_map_service.get_active(project_id, operation_id)
        self._validate_map_for_operation(physical_map, operation, require_tool_reference=require_tool_reference)
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

        sample_spacing = self._sample_spacing_mm(height_map)
        segment_limit = max_segment_mm or max(0.25, sample_spacing / 2.0)
        reference_frame = self._reference_frame(physical_map, operation)
        selected_mode = CompensationMode(mode or str(operation.compensation_mode))
        original = self.repository.read_project_file(project_id, operation.archivo_gcode)
        original_hash = hashlib.sha256(original.encode("utf-8")).hexdigest()
        map_hash = hashlib.sha256(json.dumps(physical_map, sort_keys=True).encode("utf-8")).hexdigest()
        if selected_mode == CompensationMode.ADAPTIVE_FAST:
            adaptive = generate_adaptive_gcode(
                original_text=original,
                height_map=height_map,
                reference_frame=AdaptiveReferenceFrame(**reference_frame.__dict__),
                max_z_error_mm=operation.max_z_error_mm,
                operation_id=operation.id,
                operation_name=operation.nombre,
                min_segment_length_mm=max(0.05, min(segment_limit / 4.0, operation.max_z_error_mm)),
            )
            preview = adaptive["preview"]
            output = adaptive["output"]
            algorithm_version = self.ADAPTIVE_ALGORITHM_VERSION
        else:
            lines, preview = self._build_compensated_lines(operation, height_map, segment_limit, reference_frame)
            output = "\n".join(lines) + "\n"
            algorithm_version = self.LEGACY_ALGORITHM_VERSION

        relative_dir = Path("generated") / "compensated"
        stamp = _stamp()
        safe_name = f"{operation.id}_{stamp}_{selected_mode.value}_compensated.gcode"
        metadata_name = f"{operation.id}_{stamp}_{selected_mode.value}_compensated.json"
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
            "algorithm_version": algorithm_version,
            "compensation_mode": selected_mode.value,
            "max_z_error_mm": operation.max_z_error_mm,
            "max_segment_mm": segment_limit,
            "reference_frame": reference_frame.__dict__,
            "movement_trace": preview["trace"],
            "compensation_delta_min_mm": preview["delta_z_min_mm"],
            "compensation_delta_max_mm": preview["delta_z_max_mm"],
            "compensation_delta_rms_mm": preview.get("delta_z_rms_mm"),
            "segments_before": len(operation.analisis.segmentos_vista_previa),
            "segments_after": preview["emitted_points"],
            "segments_subdivided": preview.get("segments_subdivided", 0),
            "segments_fused": preview.get("segments_fused", 0),
            "max_approximation_error_mm": preview.get("max_approximation_error_mm"),
            "unsupported_commands": preview.get("unsupported_commands", []),
            "outside_points": preview.get("outside_points", []),
            "warnings": preview["warnings"],
            "convention": "machine_xy=pcb_xy+machine_origin; cutting_z=reference_z+surface_delta+programmed_z; safe_z=reference_z+programmed_z",
        }
        (project_dir / relative_path).write_text(output, encoding="utf-8")
        (project_dir / metadata_path).write_text(json.dumps(metadata, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")
        return GeneratedGCodeResult(
            relative_path=relative_path.as_posix(),
            metadata_path=metadata_path.as_posix(),
            metadata=metadata,
            preview=preview,
        ).__dict__ | {
            "selected_mode": selected_mode.value,
            "effective_mode": selected_mode.value,
        }

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

    def build_comparison_report(self, project_id: str, operation_id: str) -> dict[str, Any]:
        project = self._load_project(project_id)
        operation = project.get_operation(operation_id)
        if operation.analisis is None or not operation.archivo_gcode:
            raise ApplicationError("La operación requiere análisis y archivo original para auditar compensación.")
        physical_map = self.physical_map_service.get_active(project_id, operation_id)
        self._validate_map_for_operation(physical_map, operation, require_tool_reference=False)
        height_map = self._height_map_from_payload(physical_map["height_map"])
        reference_frame = self._reference_frame(physical_map, operation)
        original = self.repository.read_project_file(project_id, operation.archivo_gcode)
        sample_spacing = self._sample_spacing_mm(height_map)
        segment_limit = max(0.25, sample_spacing / 2.0)
        original_summary = self._summarize_artifact(
            label="original",
            text=original,
            operation=operation,
            preview={"trace": [], "segments_subdivided": 0, "segments_fused": 0, "max_approximation_error_mm": 0.0, "outside_points": [], "unsupported_commands": []},
            analysis=operation.analisis,
        )
        legacy_lines, legacy_preview = self._build_compensated_lines(operation, height_map, segment_limit, reference_frame)
        legacy_text = "\n".join(legacy_lines) + "\n"
        legacy_summary = self._summarize_artifact(
            label="legacy",
            text=legacy_text,
            operation=operation,
            preview=legacy_preview,
        )
        adaptive_summary: dict[str, Any]
        adaptive_error: str | None = None
        try:
            adaptive = generate_adaptive_gcode(
                original_text=original,
                height_map=height_map,
                reference_frame=AdaptiveReferenceFrame(**reference_frame.__dict__),
                max_z_error_mm=operation.max_z_error_mm,
                operation_id=operation.id,
                operation_name=operation.nombre,
                min_segment_length_mm=max(0.05, min(segment_limit / 4.0, operation.max_z_error_mm)),
            )
            adaptive_summary = self._summarize_artifact(
                label="adaptive_fast",
                text=adaptive["output"],
                operation=operation,
                preview=adaptive["preview"],
            )
            adaptive_error = None
        except Exception as error:
            adaptive_summary = {
                "mode": "adaptive_fast",
                "eligible": False,
                "error": str(error),
                "unsupported_commands": [],
                "error_z_max_approximation_mm": None,
            }
            adaptive_error = str(error)
        adaptive_within_tolerance = adaptive_error is None and float(adaptive_summary.get("error_z_max_approximation_mm") or 0.0) <= float(operation.max_z_error_mm)
        adaptive_supported = adaptive_error is None and not adaptive_summary.get("unsupported_commands")
        adaptive_eligible = bool(adaptive_within_tolerance and adaptive_supported)
        if adaptive_error is None:
            adaptive_summary["eligible"] = adaptive_eligible
        selected_mode = str(operation.compensation_mode)
        recommended_mode = "adaptive_fast" if adaptive_eligible else "legacy"
        return {
            "selected_mode": selected_mode,
            "recommended_mode": recommended_mode,
            "max_z_error_mm": operation.max_z_error_mm,
            "original": original_summary,
            "legacy": legacy_summary,
            "adaptive_fast": adaptive_summary,
            "warnings": [message for message in [adaptive_error] if message],
            "_artifacts": {
                "original": original,
                "legacy": legacy_text,
                "adaptive_fast": None if adaptive_error is not None else adaptive["output"],
            },
        }

    def _validate_map_for_operation(self, physical_map: dict[str, Any], operation: OperacionPCB, *, require_tool_reference: bool = True) -> None:
        if physical_map.get("schema_version") != "surface-map-v2":
            raise ApplicationError("El mapa medido usa un modelo anterior. Abra el mapa físico para migrarlo antes de compensar.")
        if physical_map.get("setup_id") != operation.setup_id or physical_map.get("face") != operation.cara:
            raise ApplicationError("Mapa incorrecto: pertenece a otro montaje o cara de PCB.")
        if physical_map.get("status") != "MESH_COMPLETE":
            raise ApplicationError("Mapa incompleto: termine o recupere el sondeo antes de compensar.")
        reference = (physical_map.get("tool_references") or {}).get(_tool_key(operation))
        if require_tool_reference and (not reference or not reference.get("valid")):
            raise ApplicationError("Referencia Z inválida o ausente para la herramienta requerida por esta operación.")
        if operation.id not in set(physical_map.get("operation_ids") or []):
            raise ApplicationError("La operación no está cubierta por el mapa medido activo.")

    def _reference_frame(self, physical_map: dict[str, Any], operation: OperacionPCB) -> ReferenceFrame:
        reference = (physical_map.get("tool_references") or {}).get(_tool_key(operation))
        surface_reference_z = physical_map.get("reference_z") if not isinstance(reference, dict) or not reference.get("valid") else reference.get("reference_z")
        if surface_reference_z is None:
            raise ApplicationError("Referencia Z inválida o ausente para generar el plan compensado.")
        return ReferenceFrame(
            machine_origin_x_mm=float(physical_map["machine_origin_x"]),
            machine_origin_y_mm=float(physical_map["machine_origin_y"]),
            surface_reference_z_mm=float(surface_reference_z),
        )

    def _summarize_artifact(
        self,
        *,
        label: str,
        text: str,
        operation: OperacionPCB,
        preview: dict[str, Any],
        analysis=None,
    ) -> dict[str, Any]:
        resolved_analysis = operation.analisis if analysis is None else analysis
        if label != "original":
            resolved_analysis = analyze_gcode_text(text)
        movement_counts = {"G0": 0, "G1": 0, "G2": 0, "G3": 0}
        distance_cut_mm = 0.0
        distance_rapid_mm = 0.0
        distance_xyz_mm = 0.0
        for segment in resolved_analysis.segmentos_vista_previa:
            movement_counts[segment.tipo] = movement_counts.get(segment.tipo, 0) + 1
            distance_xyz_mm += float(segment.distancia_mm)
            if segment.tipo == "G0":
                distance_rapid_mm += float(segment.distancia_mm)
            else:
                distance_cut_mm += float(segment.distancia_mm)
        trace = list(preview.get("trace") or [])
        corrections = [float(item.get("delta_z_mm") or 0.0) for item in trace if item.get("delta_z_mm") is not None]
        original_movements = len(operation.analisis.segmentos_vista_previa)
        movements = sum(movement_counts.values())
        return {
            "mode": label,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "total_lines": len(text.splitlines()),
            "movement_counts": movement_counts,
            "movements_total": movements,
            "movements_added": max(0, movements - original_movements),
            "movements_removed": max(0, original_movements - movements),
            "segments_subdivided": int(preview.get("segments_subdivided", 0)),
            "segments_fused": int(preview.get("segments_fused", 0)),
            "distance_cut_mm": distance_cut_mm,
            "distance_rapid_mm": distance_rapid_mm,
            "distance_xyz_mm": distance_xyz_mm,
            "correction_z_min_mm": min(corrections) if corrections else None,
            "correction_z_max_mm": max(corrections) if corrections else None,
            "correction_rms_mm": math.sqrt(sum(value * value for value in corrections) / len(corrections)) if corrections else None,
            "error_z_max_approximation_mm": preview.get("max_approximation_error_mm", 0.0),
            "points_outside_map": len(preview.get("outside_points") or []),
            "extrapolations": int(preview.get("extrapolations", 0)),
            "unsupported_commands": list(preview.get("unsupported_commands") or []),
            "estimated_time_s": None,
            "estimation_method": None,
            "estimation_confidence": None,
            "estimation_detail": None,
        }

    def _build_compensated_lines(self, operation: OperacionPCB, height_map: HeightMap, max_segment_mm: float, reference_frame: ReferenceFrame) -> tuple[list[str], dict[str, Any]]:
        lines = [
            "; Klipper CNC Assistant - plan compensado inmutable",
            f"; Operacion: {operation.nombre} ({operation.id})",
            f"; Algoritmo: {self.LEGACY_ALGORITHM_VERSION}",
            f"; PCB -> CNC: X+{reference_frame.machine_origin_x_mm:.5f} Y+{reference_frame.machine_origin_y_mm:.5f}; Zref={reference_frame.surface_reference_z_mm:.5f}",
            "; Corte: Z_maquina=Zref+delta_superficie+Z_programada",
            "; Viaje seguro: Z_maquina=Zref+Z_programada",
            "G21",
            "G90",
        ]
        warnings: list[str] = []
        z_values: list[float] = []
        deltas: list[float] = []
        trace: list[dict[str, Any]] = []
        for segment_index, segment in enumerate(operation.analisis.segmentos_vista_previa):
            points = segment.puntos or (segment.desde, segment.hasta)
            sampled = self._sample_points(points, max_segment_mm)
            uses_surface = segment_uses_surface_map(segment)
            selected = sampled[1:] if len(sampled) > 1 else sampled
            for point in selected:
                compensated = compensate_cut_point(
                    pcb=PcbCoordinates(point.x_mm, point.y_mm),
                    programmed_z_mm=segment.z_mm,
                    surface_map=height_map,
                    reference_frame=reference_frame,
                    uses_surface_map=uses_surface,
                )
                final = compensated.machine
                lines.append(self._format_move(final.x_mm, final.y_mm, final.z_mm, segment.avance_mm_min))
                if final.z_mm is not None:
                    z_values.append(final.z_mm)
                if compensated.delta_z_mm is not None:
                    deltas.append(compensated.delta_z_mm)
                trace.append({
                    "plan_index": len(trace),
                    "line_number": segment.numero_linea,
                    "motion": segment.tipo,
                    "movement_type": segment.tipo_movimiento,
                    "pcb_x_mm": point.x_mm,
                    "pcb_y_mm": point.y_mm,
                    "machine_x_mm": final.x_mm,
                    "machine_y_mm": final.y_mm,
                    "programmed_z_mm": segment.z_mm,
                    "surface_z_machine_mm": compensated.surface_z_machine_mm,
                    "delta_z_mm": compensated.delta_z_mm,
                    "final_z_mm": final.z_mm,
                    "feed_mm_min": segment.avance_mm_min,
                    "uses_surface_map": uses_surface,
                })
        if not z_values:
            warnings.append("No se encontraron movimientos con Z explícita.")
        return lines, {
            "emitted_points": len(trace),
            "warnings": warnings,
            "z_compensated_min_mm": min(z_values) if z_values else None,
            "z_compensated_max_mm": max(z_values) if z_values else None,
            "delta_z_min_mm": min(deltas) if deltas else None,
            "delta_z_max_mm": max(deltas) if deltas else None,
            "trace": trace,
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
