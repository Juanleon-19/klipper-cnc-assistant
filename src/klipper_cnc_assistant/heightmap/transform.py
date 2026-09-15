"""Pure compensation in machine Z and sampling of programmed linear ramps.

Map values are deltas from the acquisition reference, never absolute probe Z.
Rapid/auxiliary moves retain the existing no-surface-correction policy.
"""
import math
from dataclasses import dataclass
from klipper_cnc_assistant.domain import PreviewSegment


def compensated_z(*, reference_z_mm: float, map_delta_mm: float | None,
                  programmed_z_mm: float | None, uses_surface_map: bool) -> float | None:
    if programmed_z_mm is None or (uses_surface_map and map_delta_mm is None):
        return None
    return reference_z_mm + (map_delta_mm if uses_surface_map else 0.0) + programmed_z_mm


@dataclass(frozen=True)
class ProgrammedPoint:
    x_mm: float
    y_mm: float
    z_mm: float | None
    t: float


def sample_programmed_segment(segment: PreviewSegment, spacing_mm: float) -> tuple[ProgrammedPoint, ...]:
    if not math.isfinite(spacing_mm) or spacing_mm <= 0:
        raise ValueError("El paso de compensación debe ser finito y positivo.")
    points = segment.puntos or (segment.desde, segment.hasta)
    lengths = [math.hypot(b.x_mm-a.x_mm, b.y_mm-a.y_mm) for a,b in zip(points, points[1:])]
    total = sum(lengths)
    start_z = segment.inicio_z_mm
    if start_z is None:
        raise ValueError("El análisis no contiene Z inicial; vuelva a analizar el G-code.")
    def point(x, y, t):
        z = segment.z_mm if t == 1 else (None if segment.z_mm is None else start_z + t*(segment.z_mm-start_z))
        return ProgrammedPoint(x, y, z, t)
    sampled = [point(points[0].x_mm, points[0].y_mm, 0.0)]
    traversed = 0.0
    for start, end, length in zip(points, points[1:], lengths):
        count = max(1, math.ceil(length/spacing_mm))
        for index in range(1, count+1):
            fraction = index/count
            t = (traversed + fraction*length)/total if total else 1.0
            sampled.append(point(end.x_mm if index == count else start.x_mm + fraction*(end.x_mm-start.x_mm),
                                 end.y_mm if index == count else start.y_mm + fraction*(end.y_mm-start.y_mm), t))
        traversed += length
    # Exact programmed endpoint, including the small chord rounding of planar arcs.
    sampled[-1] = point(segment.fin_x_mm, segment.fin_y_mm, 1.0)
    return tuple(sampled)


def compensation_tool_key(tool_id, label):
    return tool_id or (label or "sin-herramienta").strip().lower().replace(" ", "-")


def measured_reference_z(physical_map, tool_key):
    reference = (physical_map.get("tool_references") or {}).get(tool_key)
    value = physical_map.get("reference_z") if not isinstance(reference, dict) or not reference.get("valid") else reference.get("reference_z")
    if value is None or not math.isfinite(float(value)):
        raise ValueError("Referencia Z inválida o ausente para compensar.")
    return float(value)
