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
) -> CoverageReport:
    inside = 0
    outside = 0
    numerical = 0
    issues: list[CoveragePointIssue] = []
    max_distance = 0.0

    for operation_id, operation_name, analysis in operations:
        for segment_index, segment in enumerate(analysis.segmentos_vista_previa):
            if not segment_uses_surface_map(segment):
                continue
            points = segment.puntos or (segment.desde, segment.hasta)
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
    return (
        zone.min_x_mm - tolerance_mm <= x_mm <= zone.max_x_mm + tolerance_mm
        and zone.min_y_mm - tolerance_mm <= y_mm <= zone.max_y_mm + tolerance_mm
    )
