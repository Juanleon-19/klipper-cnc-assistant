from __future__ import annotations

from dataclasses import dataclass
from math import hypot

from klipper_cnc_assistant.domain import OperationAnalysis, PreviewPoint

from .models import ExclusionZone, HeightMap, ProbeRegion


DOMAIN_TOLERANCE_MM = 1e-6


@dataclass(frozen=True)
class DomainCheck:
    x_mm: float
    y_mm: float
    inside: bool
    distance_mm: float
    reason: str | None


@dataclass(frozen=True)
class CoveragePointIssue:
    operation_id: str
    operation_name: str
    segment_index: int
    point_index: int
    x_mm: float
    y_mm: float
    distance_mm: float
    reason: str
    numerical_only: bool


@dataclass(frozen=True)
class CoverageReport:
    points_inside: int
    points_outside: int
    points_numerically_outside: int
    max_distance_outside_mm: float
    tolerance_mm: float
    issues: tuple[CoveragePointIssue, ...]

    @property
    def blocking_outside_points(self) -> int:
        return self.points_outside - self.points_numerically_outside

    @property
    def sufficient(self) -> bool:
        return self.blocking_outside_points == 0


def check_domain(height_map: HeightMap, x_mm: float, y_mm: float, *, tolerance_mm: float = DOMAIN_TOLERANCE_MM) -> DomainCheck:
    region = height_map.probe_region
    rectangle_distance = _distance_to_region(region, x_mm, y_mm)
    if rectangle_distance > tolerance_mm:
        return DomainCheck(
            x_mm=x_mm,
            y_mm=y_mm,
            inside=False,
            distance_mm=rectangle_distance,
            reason="fuera de la region sondeable",
        )

    for zone in height_map.exclusion_zones:
        if _inside_zone(zone, x_mm, y_mm, tolerance_mm):
            return DomainCheck(
                x_mm=x_mm,
                y_mm=y_mm,
                inside=False,
                distance_mm=0.0,
                reason=f"dentro de la zona excluida {zone.nombre}",
            )

    return DomainCheck(x_mm=x_mm, y_mm=y_mm, inside=True, distance_mm=0.0, reason=None)


def segment_uses_surface_map(segment: object) -> bool:
    movement = str(getattr(segment, "tipo_movimiento", "") or "")
    z_mm = getattr(segment, "z_mm", None)
    if movement == "desplazamiento_rapido":
        return False
    if z_mm is None:
        return False
    return float(z_mm) < 0.0


def build_coverage_report(
    *,
    height_map: HeightMap,
    operations: tuple[tuple[str, str, OperationAnalysis], ...],
    tolerance_mm: float = DOMAIN_TOLERANCE_MM,
    include_all_segments: bool = False,
) -> CoverageReport:
    inside = 0
    outside = 0
    numerical = 0
    issues: list[CoveragePointIssue] = []
    max_distance = 0.0

    unsupported = unsupported_cells(height_map)
    for operation_id, operation_name, analysis in operations:
        for segment_index, segment in enumerate(analysis.segmentos_vista_previa):
            if not include_all_segments and not segment_uses_surface_map(segment):
                continue
            points = segment.puntos or (segment.desde, segment.hasta)
            # A rectangle is convex, but exclusions/support holes are not.
            # Test complete polyline edges, not just the stored vertices.
            for start, end in zip(points, points[1:]):
                for zone in (*height_map.exclusion_zones, *unsupported):
                    hit = segment_rectangle_hit(start, end, zone, tolerance_mm)
                    if hit is None:
                        continue
                    outside += 1
                    if len(issues) < 200:
                        issues.append(CoveragePointIssue(operation_id, operation_name,
                            segment_index, -1, hit[0], hit[1], 0.0,
                            f"segmento cruza zona sin soporte: {zone.nombre}", False))
            for point_index, point in enumerate(points):
                check = check_domain(height_map, point.x_mm, point.y_mm, tolerance_mm=tolerance_mm)
                if check.inside:
                    inside += 1
                    continue
                outside += 1
                max_distance = max(max_distance, check.distance_mm)
                is_numerical = check.distance_mm <= tolerance_mm and check.reason == "fuera de la region sondeable"
                if is_numerical:
                    numerical += 1
                if len(issues) < 200:
                    issues.append(
                        CoveragePointIssue(
                            operation_id=operation_id,
                            operation_name=operation_name,
                            segment_index=segment_index,
                            point_index=point_index,
                            x_mm=point.x_mm,
                            y_mm=point.y_mm,
                            distance_mm=check.distance_mm,
                            reason=check.reason or "fuera de dominio",
                            numerical_only=is_numerical,
                        )
                    )

    return CoverageReport(
        points_inside=inside,
        points_outside=outside,
        points_numerically_outside=numerical,
        max_distance_outside_mm=max_distance,
        tolerance_mm=tolerance_mm,
        issues=tuple(issues),
    )


def _distance_to_region(region: ProbeRegion, x_mm: float, y_mm: float) -> float:
    dx = 0.0
    if x_mm < region.min_x_mm:
        dx = region.min_x_mm - x_mm
    elif x_mm > region.max_x_mm:
        dx = x_mm - region.max_x_mm

    dy = 0.0
    if y_mm < region.min_y_mm:
        dy = region.min_y_mm - y_mm
    elif y_mm > region.max_y_mm:
        dy = y_mm - region.max_y_mm

    return hypot(dx, dy)


def _inside_zone(zone: ExclusionZone, x_mm: float, y_mm: float, tolerance_mm: float) -> bool:
    if zone.shape == "circle":
        return hypot(x_mm - zone.center_x_mm, y_mm - zone.center_y_mm) <= zone.radius_mm + tolerance_mm
    return (
        zone.min_x_mm - tolerance_mm <= x_mm <= zone.max_x_mm + tolerance_mm
        and zone.min_y_mm - tolerance_mm <= y_mm <= zone.max_y_mm + tolerance_mm
    )


def segment_rectangle_hit(start, end, zone, tolerance=DOMAIN_TOLERANCE_MM):
    """Closed segment/slab intersection, including tangencies and zero length."""
    if zone.shape == "circle":
        dx, dy = end.x_mm - start.x_mm, end.y_mm - start.y_mm
        length2 = dx * dx + dy * dy
        t = max(0, min(1, ((zone.center_x_mm - start.x_mm) * dx + (zone.center_y_mm - start.y_mm) * dy) / length2)) if length2 else 0
        x, y = start.x_mm + t * dx, start.y_mm + t * dy
        return (x, y) if _inside_zone(zone, x, y, tolerance) else None
    lower, upper = 0.0, 1.0
    for origin, delta, minimum, maximum in (
        (start.x_mm, end.x_mm - start.x_mm, zone.min_x_mm, zone.max_x_mm),
        (start.y_mm, end.y_mm - start.y_mm, zone.min_y_mm, zone.max_y_mm),
    ):
        if abs(delta) < 1e-15:
            if origin < minimum - tolerance or origin > maximum + tolerance:
                return None
            continue
        a, b = (minimum - tolerance - origin) / delta, (maximum + tolerance - origin) / delta
        lower, upper = max(lower, min(a, b)), min(upper, max(a, b))
        if lower > upper:
            return None
    t = (lower + upper) / 2
    return start.x_mm + t * (end.x_mm - start.x_mm), start.y_mm + t * (end.y_mm - start.y_mm)


def unsupported_cells(height_map):
    """Bilinear interpolation requires all four corners of a traversed cell.

    Only cells adjacent to absent/excluded samples are expanded. Normal complete
    maps add no segment work. Preview uses hypothetical included samples with Z=0
    for geometry only; these are never saved as measurements.
    """
    grid, region = height_map.grid, height_map.probe_region
    present = {(s.fila, s.columna) for s in height_map.muestras}
    bad = {(s.fila, s.columna) for s in height_map.muestras if not s.incluida or s.z_mm is None}
    bad.update((r, c) for r in range(grid.filas) for c in range(grid.columnas) if (r, c) not in present)
    cells = set()
    for row, col in bad:
        for r in (row - 1, row):
            for c in (col - 1, col):
                if 0 <= r < grid.filas - 1 and 0 <= c < grid.columnas - 1:
                    cells.add((r, c))
    return tuple(ExclusionZone(f"support-{r}-{c}", "celda con esquina ausente/excluida",
        region.min_x_mm + c * grid.paso_x_mm, region.min_y_mm + r * grid.paso_y_mm,
        region.min_x_mm + (c + 1) * grid.paso_x_mm, region.min_y_mm + (r + 1) * grid.paso_y_mm)
        for r, c in sorted(cells))
