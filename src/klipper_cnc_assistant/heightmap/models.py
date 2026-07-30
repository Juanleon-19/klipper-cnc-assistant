from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SampleQuality(StrEnum):
    VALIDA = "valida"
    FALTANTE = "faltante"
    ATIPICA = "atipica"
    EXCLUIDA = "excluida"
    REVISION = "revision"


@dataclass(frozen=True)
class ProbeRegion:
    min_x_mm: float
    min_y_mm: float
    max_x_mm: float
    max_y_mm: float

    @property
    def ancho_mm(self) -> float:
        return self.max_x_mm - self.min_x_mm

    @property
    def alto_mm(self) -> float:
        return self.max_y_mm - self.min_y_mm

    def contains(self, x_mm: float, y_mm: float) -> bool:
        return self.min_x_mm <= x_mm <= self.max_x_mm and self.min_y_mm <= y_mm <= self.max_y_mm


@dataclass(frozen=True)
class ExclusionZone:
    id: str
    nombre: str
    min_x_mm: float
    min_y_mm: float
    max_x_mm: float
    max_y_mm: float

    def contains(self, x_mm: float, y_mm: float) -> bool:
        return self.min_x_mm <= x_mm <= self.max_x_mm and self.min_y_mm <= y_mm <= self.max_y_mm


@dataclass(frozen=True)
class HeightSample:
    id: str
    x_mm: float
    y_mm: float
    z_mm: float | None
    fila: int
    columna: int
    origen_datos: str
    estado_calidad: SampleQuality
    observacion: str | None = None
    incluida: bool = True
    residuo_plano_mm: float | None = None


@dataclass(frozen=True)
class HeightGrid:
    filas: int
    columnas: int
    ancho_mm: float
    alto_mm: float
    paso_x_mm: float
    paso_y_mm: float


@dataclass(frozen=True)
class PlaneFit:
    a: float
    b: float
    c: float
    inclinacion_x_mm_por_mm: float
    inclinacion_y_mm_por_mm: float
    rms_residuos_mm: float
    residuo_maximo_mm: float
    residuo_minimo_mm: float


@dataclass(frozen=True)
class HeightMapStatistics:
    cantidad_puntos: int
    cantidad_puntos_incluidos: int
    cantidad_puntos_faltantes: int
    cantidad_puntos_atipicos: int
    altura_min_mm: float | None
    altura_max_mm: float | None
    rango_alturas_mm: float | None
    valor_referencia_mm: float | None
    desviacion_rms_respecto_plano_mm: float | None
    residuo_maximo_mm: float | None
    ancho_cubierto_mm: float | None
    alto_cubierto_mm: float | None


@dataclass(frozen=True)
class InterpolationResult:
    estado: str
    valor_mm: float | None
    observacion: str | None = None


@dataclass(frozen=True)
class HeightMap:
    proyecto_id: str
    operacion_id: str
    version: int
    version_algoritmo: str
    estado: str
    fuente_datos: str
    superficie_simulada: str | None
    repeticion_simulacion: int | None
    etiqueta_simulada: bool
    grid: HeightGrid
    probe_region: ProbeRegion
    exclusion_zones: tuple[ExclusionZone, ...]
    muestras: tuple[HeightSample, ...]
    estadisticas: HeightMapStatistics
    plano: PlaneFit | None
    creado_en: datetime = field(default_factory=utc_now)
    actualizado_en: datetime = field(default_factory=utc_now)
