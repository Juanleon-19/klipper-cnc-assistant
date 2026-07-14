from __future__ import annotations

import math

from klipper_cnc_assistant.domain import OperationAnalysis, PreviewPoint

from .analysis import interpolate_height
from .models import HeightMap


def build_compensation_preview(
    *,
    analysis: OperationAnalysis,
    height_map: HeightMap,
    reference_z_mm: float,
) -> dict[str, object]:
    sample_spacing_mm = _sample_spacing_mm(height_map)
    segments: list[dict[str, object]] = []
    outside_points = 0
    virtual_points = 0
    original_z_values: list[float] = []
    compensated_z_values: list[float] = []

    for segment in analysis.segmentos_vista_previa:
        sampled_points = _sample_segment_points(segment.puntos or (segment.desde, segment.hasta), sample_spacing_mm)
        virtual_points += max(0, len(sampled_points) - len(segment.puntos or (segment.desde, segment.hasta)))
        preview_points: list[dict[str, object]] = []
        segment_outside = False
        for point in sampled_points:
            result = interpolate_height(height_map, x_mm=point.x_mm, y_mm=point.y_mm, mode="bruto")
            if result.estado == "fuera de dominio":
                outside_points += 1
                segment_outside = True
            if segment.z_mm is not None:
                original_z_values.append(segment.z_mm)
            correction_mm = None if result.valor_mm is None else result.valor_mm - reference_z_mm
            compensated_z_mm = None if correction_mm is None or segment.z_mm is None else segment.z_mm + correction_mm
            if compensated_z_mm is not None:
                compensated_z_values.append(compensated_z_mm)
            preview_points.append(
                {
                    "x_mm": point.x_mm,
                    "y_mm": point.y_mm,
                    "z_original_mm": segment.z_mm,
                    "z_superficie_mm": result.valor_mm,
                    "correccion_mm": correction_mm,
                    "z_compensada_mm": compensated_z_mm,
                    "estado": result.estado if segment.z_mm is not None else "sin_z_original",
                    "observacion": result.observacion,
                }
            )
        segments.append(
            {
                "tipo": segment.tipo,
                "tipo_movimiento": segment.tipo_movimiento,
                "numero_linea": segment.numero_linea,
                "estado": "fuera de dominio" if segment_outside else "ok" if segment.z_mm is not None else "sin_z_original",
                "distancia_mm": segment.distancia_mm,
                "puntos": preview_points,
            }
        )

    return {
        "convencion_matematica": (
            "z_compensada = z_original + (superficie_xy - z_referencia). "
            "La compensacion usa la altura interpolada del mapa en cada punto X/Y y conserva los valores reales en las etiquetas."
        ),
        "z_referencia_mm": reference_z_mm,
        "paso_muestreo_virtual_mm": sample_spacing_mm,
        "puntos_fuera_dominio": outside_points,
        "puntos_virtuales_agregados": virtual_points,
        "resumen_z_original": _z_summary(original_z_values),
        "resumen_z_compensada": _z_summary(compensated_z_values),
        "segmentos": segments,
    }


def _sample_spacing_mm(height_map: HeightMap) -> float:
    candidates = [value for value in (height_map.grid.paso_x_mm, height_map.grid.paso_y_mm) if value > 0]
    if not candidates:
        return 1.0
    return max(0.5, min(candidates) / 2)


def _sample_segment_points(points: tuple[PreviewPoint, ...], spacing_mm: float) -> tuple[PreviewPoint, ...]:
    if len(points) <= 1:
        return points
    sampled: list[PreviewPoint] = [points[0]]
    for start, end in zip(points, points[1:]):
        distance = math.dist((start.x_mm, start.y_mm), (end.x_mm, end.y_mm))
        subdivisions = max(1, math.ceil(distance / spacing_mm))
        for index in range(1, subdivisions + 1):
            progress = index / subdivisions
            sampled.append(
                PreviewPoint(
                    x_mm=start.x_mm + (end.x_mm - start.x_mm) * progress,
                    y_mm=start.y_mm + (end.y_mm - start.y_mm) * progress,
                )
            )
    return tuple(sampled)


def _z_summary(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"min_mm": None, "max_mm": None}
    return {"min_mm": min(values), "max_mm": max(values)}
