from __future__ import annotations

import csv
import json
from io import StringIO

from .models import HeightGrid, HeightSample, SampleQuality


def parse_json_samples(content: str) -> tuple[HeightGrid, list[HeightSample]]:
    payload = json.loads(content)
    if isinstance(payload, dict):
        raw_samples = payload.get("muestras", [])
        raw_grid = payload.get("grid", {})
    elif isinstance(payload, list):
        raw_samples = payload
        raw_grid = {}
    else:
        raise ValueError("El archivo JSON del mapa no tiene un formato valido.")
    samples = [_sample_from_mapping(item, default_source="json") for item in raw_samples]
    grid = _resolve_grid(samples, raw_grid)
    return grid, samples


def parse_csv_samples(content: str) -> tuple[HeightGrid, list[HeightSample]]:
    reader = csv.DictReader(StringIO(content))
    samples = [
        _sample_from_mapping(row, default_source="csv")
        for row in reader
    ]
    if not samples:
        raise ValueError("El archivo CSV no contiene muestras.")
    grid = _resolve_grid(samples, {})
    return grid, samples


def _sample_from_mapping(payload: dict[str, object], *, default_source: str) -> HeightSample:
    row = int(payload.get("fila", payload.get("row", 0)))
    column = int(payload.get("columna", payload.get("column", payload.get("col", 0))))
    z_value = payload.get("z_mm")
    if z_value in ("", None):
        parsed_z = None
    else:
        parsed_z = float(z_value)
    quality = str(payload.get("estado_calidad", payload.get("quality", "valida"))).strip().lower()
    quality_map = {
        "valida": SampleQuality.VALIDA,
        "faltante": SampleQuality.FALTANTE,
        "atipica": SampleQuality.ATIPICA,
        "excluida": SampleQuality.EXCLUIDA,
        "revision": SampleQuality.REVISION,
    }
    return HeightSample(
        id=str(payload.get("id", f"hm_{row}_{column}")),
        x_mm=float(payload.get("x_mm", 0.0)),
        y_mm=float(payload.get("y_mm", 0.0)),
        z_mm=parsed_z,
        fila=row,
        columna=column,
        origen_datos=str(payload.get("origen_datos", payload.get("source", default_source))),
        estado_calidad=quality_map.get(quality, SampleQuality.VALIDA),
        observacion=str(payload["observacion"]) if payload.get("observacion") else None,
        incluida=_parse_bool(payload.get("incluida", payload.get("included", True))),
    )


def _resolve_grid(samples: list[HeightSample], payload: dict[str, object]) -> HeightGrid:
    if payload:
        return HeightGrid(
            filas=int(payload.get("filas", payload.get("rows", 0))),
            columnas=int(payload.get("columnas", payload.get("columns", 0))),
            ancho_mm=float(payload.get("ancho_mm", 0.0)),
            alto_mm=float(payload.get("alto_mm", 0.0)),
            paso_x_mm=float(payload.get("paso_x_mm", payload.get("spacing_x_mm", 0.0))),
            paso_y_mm=float(payload.get("paso_y_mm", payload.get("spacing_y_mm", 0.0))),
        )

    rows = max(sample.fila for sample in samples) + 1
    columns = max(sample.columna for sample in samples) + 1
    xs = sorted({sample.x_mm for sample in samples})
    ys = sorted({sample.y_mm for sample in samples})
    width = xs[-1] - xs[0] if len(xs) >= 2 else 0.0
    height = ys[-1] - ys[0] if len(ys) >= 2 else 0.0
    step_x = xs[1] - xs[0] if len(xs) >= 2 else 0.0
    step_y = ys[1] - ys[0] if len(ys) >= 2 else 0.0
    return HeightGrid(
        filas=rows,
        columnas=columns,
        ancho_mm=width,
        alto_mm=height,
        paso_x_mm=step_x,
        paso_y_mm=step_y,
    )


def _parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"0", "false", "no"}
    return bool(value)

