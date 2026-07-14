from __future__ import annotations

from dataclasses import replace
from math import sqrt

import numpy

from .coverage import DOMAIN_TOLERANCE_MM, check_domain
from .models import (
    ExclusionZone,
    HeightGrid,
    HeightMap,
    HeightMapStatistics,
    HeightSample,
    InterpolationResult,
    PlaneFit,
    ProbeRegion,
    SampleQuality,
)


ALGORITHM_VERSION = "heightmap-v2"


def plane_height(plane: PlaneFit, x_mm: float, y_mm: float) -> float:
    return plane.a * x_mm + plane.b * y_mm + plane.c


def point_in_exclusion_zone(x_mm: float, y_mm: float, exclusion_zones: tuple[ExclusionZone, ...]) -> bool:
    return any(zone.contains(x_mm, y_mm) for zone in exclusion_zones)


def compute_height_map(
    *,
    proyecto_id: str,
    operacion_id: str,
    version: int,
    fuente_datos: str,
    superficie_simulada: str | None,
    repeticion_simulacion: int | None,
    etiqueta_simulada: bool,
    grid: HeightGrid,
    probe_region: ProbeRegion,
    exclusion_zones: tuple[ExclusionZone, ...],
    muestras: list[HeightSample],
    estado: str = "configurado",
) -> HeightMap:
    plane = fit_plane(muestras)
    samples_with_residuals = _with_residuals(muestras, plane)
    statistics = build_statistics(samples_with_residuals, plane)
    return HeightMap(
        proyecto_id=proyecto_id,
        operacion_id=operacion_id,
        version=version,
        version_algoritmo=ALGORITHM_VERSION,
        estado=estado,
        fuente_datos=fuente_datos,
        superficie_simulada=superficie_simulada,
        repeticion_simulacion=repeticion_simulacion,
        etiqueta_simulada=etiqueta_simulada,
        grid=grid,
        probe_region=probe_region,
        exclusion_zones=exclusion_zones,
        muestras=tuple(samples_with_residuals),
        estadisticas=statistics,
        plano=plane,
    )


def fit_plane(samples: list[HeightSample]) -> PlaneFit | None:
    valid = [sample for sample in samples if sample.incluida and sample.z_mm is not None]
    if len(valid) < 3:
        return None

    matrix = numpy.array([[sample.x_mm, sample.y_mm, 1.0] for sample in valid], dtype=float)
    vector = numpy.array([sample.z_mm for sample in valid], dtype=float)
    coefficients, *_ = numpy.linalg.lstsq(matrix, vector, rcond=None)
    a, b, c = (float(value) for value in coefficients.tolist())
    residuals = vector - matrix.dot(coefficients)
    abs_residuals = numpy.abs(residuals)
    return PlaneFit(
        a=a,
        b=b,
        c=c,
        inclinacion_x_mm_por_mm=a,
        inclinacion_y_mm_por_mm=b,
        rms_residuos_mm=float(sqrt(float(numpy.mean(residuals**2)))),
        residuo_maximo_mm=float(numpy.max(abs_residuals)),
        residuo_minimo_mm=float(numpy.min(residuals)),
    )


def build_statistics(samples: list[HeightSample], plane: PlaneFit | None) -> HeightMapStatistics:
    included_samples = [sample for sample in samples if sample.incluida and sample.z_mm is not None]
    heights = [sample.z_mm for sample in included_samples]
    xs = [sample.x_mm for sample in included_samples]
    ys = [sample.y_mm for sample in included_samples]
    reference_sample = next(
        (
            sample
            for sample in sorted(included_samples, key=lambda item: (item.fila, item.columna, item.y_mm, item.x_mm))
            if sample.z_mm is not None
        ),
        None,
    )
    return HeightMapStatistics(
        cantidad_puntos=len(samples),
        cantidad_puntos_incluidos=len(included_samples),
        cantidad_puntos_faltantes=sum(1 for sample in samples if sample.z_mm is None),
        cantidad_puntos_atipicos=sum(1 for sample in samples if sample.estado_calidad == SampleQuality.ATIPICA),
        altura_min_mm=min(heights) if heights else None,
        altura_max_mm=max(heights) if heights else None,
        rango_alturas_mm=(max(heights) - min(heights)) if len(heights) >= 2 else 0.0 if heights else None,
        valor_referencia_mm=reference_sample.z_mm if reference_sample else None,
        desviacion_rms_respecto_plano_mm=plane.rms_residuos_mm if plane else None,
        residuo_maximo_mm=plane.residuo_maximo_mm if plane else None,
        ancho_cubierto_mm=(max(xs) - min(xs)) if len(xs) >= 2 else 0.0 if xs else None,
        alto_cubierto_mm=(max(ys) - min(ys)) if len(ys) >= 2 else 0.0 if ys else None,
    )


def interpolate_height(
    height_map: HeightMap,
    *,
    x_mm: float,
    y_mm: float,
    mode: str = "bruto",
) -> InterpolationResult:
    grid = height_map.grid
    region = height_map.probe_region
    domain_check = check_domain(height_map, x_mm, y_mm, tolerance_mm=DOMAIN_TOLERANCE_MM)
    if not domain_check.inside:
        return InterpolationResult(
            estado="fuera de dominio",
            valor_mm=None,
            observacion=(
                f"La coordenada solicitada cae {domain_check.reason}; "
                f"distancia al dominio {domain_check.distance_mm:.6f} mm."
            ),
        )
    local_x = x_mm - region.min_x_mm
    local_y = y_mm - region.min_y_mm
    column_position = (local_x / grid.paso_x_mm) if grid.paso_x_mm > 0 else 0.0
    row_position = (local_y / grid.paso_y_mm) if grid.paso_y_mm > 0 else 0.0
    sample_index = {(sample.fila, sample.columna): sample for sample in height_map.muestras}

    if grid.columnas == 1 and grid.filas == 1:
        sample = sample_index.get((0, 0))
        value = None if sample is None else _value_for_mode(sample, height_map.plano, mode)
        if sample is None or value is None or not sample.incluida:
            return InterpolationResult(estado="insuficiente", valor_mm=None, observacion="El único punto de la malla no tiene medición válida.")
        return InterpolationResult(estado="ok", valor_mm=float(value))

    if grid.filas == 1:
        left_column = min(grid.columnas - 2, max(0, int(column_position)))
        tx = min(1.0, max(0.0, column_position - left_column))
        samples = [sample_index.get((0, left_column)), sample_index.get((0, left_column + 1))]
        values = _interpolation_values(samples, height_map.plano, mode)
        if values is None:
            return InterpolationResult(estado="insuficiente", valor_mm=None, observacion="La fila contiene puntos faltantes o excluidos.")
        return InterpolationResult(estado="ok", valor_mm=float(values[0] * (1.0 - tx) + values[1] * tx))

    if grid.columnas == 1:
        top_row = min(grid.filas - 2, max(0, int(row_position)))
        ty = min(1.0, max(0.0, row_position - top_row))
        samples = [sample_index.get((top_row, 0)), sample_index.get((top_row + 1, 0))]
        values = _interpolation_values(samples, height_map.plano, mode)
        if values is None:
            return InterpolationResult(estado="insuficiente", valor_mm=None, observacion="La columna contiene puntos faltantes o excluidos.")
        return InterpolationResult(estado="ok", valor_mm=float(values[0] * (1.0 - ty) + values[1] * ty))

    left_column = min(grid.columnas - 2, max(0, int(column_position)))
    top_row = min(grid.filas - 2, max(0, int(row_position)))
    tx = min(1.0, max(0.0, column_position - left_column))
    ty = min(1.0, max(0.0, row_position - top_row))

    corners = [
        sample_index.get((top_row, left_column)),
        sample_index.get((top_row, left_column + 1)),
        sample_index.get((top_row + 1, left_column)),
        sample_index.get((top_row + 1, left_column + 1)),
    ]
    values = _interpolation_values(corners, height_map.plano, mode)
    if values is None:
        return InterpolationResult(
            estado="insuficiente",
            valor_mm=None,
            observacion="La celda contiene puntos faltantes o excluidos.",
        )

    top = values[0] * (1.0 - tx) + values[1] * tx
    bottom = values[2] * (1.0 - tx) + values[3] * tx
    value = top * (1.0 - ty) + bottom * ty
    return InterpolationResult(estado="ok", valor_mm=float(value))



def _interpolation_values(samples, plane: PlaneFit | None, mode: str) -> list[float] | None:
    values: list[float] = []
    for sample in samples:
        if sample is None:
            return None
        value = _value_for_mode(sample, plane, mode)
        if value is None or not sample.incluida:
            return None
        values.append(value)
    return values

def build_dense_surface(
    height_map: HeightMap,
    *,
    mode: str = "bruto",
    resolution_x: int | None = None,
    resolution_y: int | None = None,
) -> dict[str, object]:
    columns = resolution_x or max(12, min(48, height_map.grid.columnas * 3))
    rows = resolution_y or max(12, min(48, height_map.grid.filas * 3))
    points: list[dict[str, object]] = []
    region = height_map.probe_region
    for row in range(rows):
        y_mm = region.min_y_mm if rows == 1 else region.min_y_mm + region.alto_mm * row / (rows - 1)
        for column in range(columns):
            x_mm = region.min_x_mm if columns == 1 else region.min_x_mm + region.ancho_mm * column / (columns - 1)
            result = interpolate_height(height_map, x_mm=x_mm, y_mm=y_mm, mode=mode)
            points.append(
                {
                    "fila": row,
                    "columna": column,
                    "x_mm": x_mm,
                    "y_mm": y_mm,
                    "z_mm": result.valor_mm,
                    "estado": result.estado,
                    "observacion": result.observacion,
                }
            )
    return {
        "filas": rows,
        "columnas": columns,
        "modo": mode,
        "puntos": points,
    }


def _with_residuals(samples: list[HeightSample], plane: PlaneFit | None) -> list[HeightSample]:
    if plane is None:
        return [
            replace(
                sample,
                estado_calidad=(
                    SampleQuality.FALTANTE
                    if sample.z_mm is None
                    else SampleQuality.EXCLUIDA
                    if not sample.incluida
                    else sample.estado_calidad
                ),
                residuo_plano_mm=None,
            )
            for sample in samples
        ]

    provisional: list[HeightSample] = []
    for sample in samples:
        if sample.z_mm is None:
            provisional.append(replace(sample, estado_calidad=SampleQuality.FALTANTE, residuo_plano_mm=None))
            continue
        if not sample.incluida:
            provisional.append(replace(sample, estado_calidad=SampleQuality.EXCLUIDA, residuo_plano_mm=None))
            continue
        residual = sample.z_mm - plane_height(plane, sample.x_mm, sample.y_mm)
        provisional.append(replace(sample, residuo_plano_mm=float(residual)))

    threshold = max(0.03, plane.rms_residuos_mm * 2.5)
    result: list[HeightSample] = []
    for sample in provisional:
        if sample.z_mm is None or not sample.incluida:
            result.append(sample)
            continue
        quality = SampleQuality.ATIPICA if sample.residuo_plano_mm is not None and abs(sample.residuo_plano_mm) > threshold else SampleQuality.VALIDA
        result.append(replace(sample, estado_calidad=quality))
    return result


def _value_for_mode(sample: HeightSample, plane: PlaneFit | None, mode: str) -> float | None:
    if mode == "bruto":
        return sample.z_mm
    if mode == "plano":
        if plane is None:
            return None
        return plane_height(plane, sample.x_mm, sample.y_mm)
    if mode == "residuo":
        return sample.residuo_plano_mm
    raise ValueError(f"Modo de superficie no soportado: {mode}")
