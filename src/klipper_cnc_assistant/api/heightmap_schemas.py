from __future__ import annotations

from pydantic import BaseModel, Field

from klipper_cnc_assistant.heightmap import HeightMap


class HeightMapConfigRequest(BaseModel):
    filas: int = Field(ge=2)
    columnas: int = Field(ge=2)


class HeightMapSimulationRequest(HeightMapConfigRequest):
    escenario: str
    semilla: int = 1


class HeightMapImportRequest(BaseModel):
    contenido: str = Field(min_length=1)


class HeightMapSampleUpdateRequest(BaseModel):
    z_mm: float | None = None
    incluida: bool | None = None
    observacion: str | None = None


class HeightGridResponse(BaseModel):
    filas: int
    columnas: int
    ancho_mm: float
    alto_mm: float
    paso_x_mm: float
    paso_y_mm: float


class HeightSampleResponse(BaseModel):
    id: str
    x_mm: float
    y_mm: float
    z_mm: float | None
    fila: int
    columna: int
    origen_datos: str
    estado_calidad: str
    observacion: str | None
    incluida: bool
    residuo_plano_mm: float | None


class PlaneFitResponse(BaseModel):
    a: float
    b: float
    c: float
    inclinacion_x_mm_por_mm: float
    inclinacion_y_mm_por_mm: float
    rms_residuos_mm: float
    residuo_maximo_mm: float
    residuo_minimo_mm: float


class HeightMapStatisticsResponse(BaseModel):
    cantidad_puntos: int
    cantidad_puntos_incluidos: int
    cantidad_puntos_faltantes: int
    cantidad_puntos_atipicos: int
    altura_min_mm: float | None
    altura_max_mm: float | None
    rango_alturas_mm: float | None
    rms_residuos_mm: float | None
    residuo_maximo_mm: float | None
    ancho_cubierto_mm: float | None
    alto_cubierto_mm: float | None


class SurfacePointResponse(BaseModel):
    fila: int
    columna: int
    x_mm: float
    y_mm: float
    z_mm: float | None
    estado: str
    observacion: str | None


class SurfaceResponse(BaseModel):
    filas: int
    columnas: int
    modo: str
    puntos: list[SurfacePointResponse]


class HeightMapResponse(BaseModel):
    proyecto_id: str
    operacion_id: str
    version: int
    version_algoritmo: str
    estado: str
    fuente_datos: str
    escenario: str | None
    semilla: int | None
    etiqueta_simulada: bool
    grid: HeightGridResponse
    muestras: list[HeightSampleResponse]
    estadisticas: HeightMapStatisticsResponse
    plano: PlaneFitResponse | None
    superficies: dict[str, SurfaceResponse]
    creado_en: str
    actualizado_en: str


def height_map_to_response(height_map: HeightMap, surfaces: dict[str, dict[str, object]]) -> HeightMapResponse:
    return HeightMapResponse(
        proyecto_id=height_map.proyecto_id,
        operacion_id=height_map.operacion_id,
        version=height_map.version,
        version_algoritmo=height_map.version_algoritmo,
        estado=height_map.estado,
        fuente_datos=height_map.fuente_datos,
        escenario=height_map.escenario,
        semilla=height_map.semilla,
        etiqueta_simulada=height_map.etiqueta_simulada,
        grid=HeightGridResponse(
            filas=height_map.grid.filas,
            columnas=height_map.grid.columnas,
            ancho_mm=height_map.grid.ancho_mm,
            alto_mm=height_map.grid.alto_mm,
            paso_x_mm=height_map.grid.paso_x_mm,
            paso_y_mm=height_map.grid.paso_y_mm,
        ),
        muestras=[
            HeightSampleResponse(
                id=sample.id,
                x_mm=sample.x_mm,
                y_mm=sample.y_mm,
                z_mm=sample.z_mm,
                fila=sample.fila,
                columna=sample.columna,
                origen_datos=sample.origen_datos,
                estado_calidad=sample.estado_calidad,
                observacion=sample.observacion,
                incluida=sample.incluida,
                residuo_plano_mm=sample.residuo_plano_mm,
            )
            for sample in height_map.muestras
        ],
        estadisticas=HeightMapStatisticsResponse(
            cantidad_puntos=height_map.estadisticas.cantidad_puntos,
            cantidad_puntos_incluidos=height_map.estadisticas.cantidad_puntos_incluidos,
            cantidad_puntos_faltantes=height_map.estadisticas.cantidad_puntos_faltantes,
            cantidad_puntos_atipicos=height_map.estadisticas.cantidad_puntos_atipicos,
            altura_min_mm=height_map.estadisticas.altura_min_mm,
            altura_max_mm=height_map.estadisticas.altura_max_mm,
            rango_alturas_mm=height_map.estadisticas.rango_alturas_mm,
            rms_residuos_mm=height_map.estadisticas.rms_residuos_mm,
            residuo_maximo_mm=height_map.estadisticas.residuo_maximo_mm,
            ancho_cubierto_mm=height_map.estadisticas.ancho_cubierto_mm,
            alto_cubierto_mm=height_map.estadisticas.alto_cubierto_mm,
        ),
        plano=None if height_map.plano is None else PlaneFitResponse(
            a=height_map.plano.a,
            b=height_map.plano.b,
            c=height_map.plano.c,
            inclinacion_x_mm_por_mm=height_map.plano.inclinacion_x_mm_por_mm,
            inclinacion_y_mm_por_mm=height_map.plano.inclinacion_y_mm_por_mm,
            rms_residuos_mm=height_map.plano.rms_residuos_mm,
            residuo_maximo_mm=height_map.plano.residuo_maximo_mm,
            residuo_minimo_mm=height_map.plano.residuo_minimo_mm,
        ),
        superficies={
            key: SurfaceResponse(
                filas=int(value["filas"]),
                columnas=int(value["columnas"]),
                modo=str(value["modo"]),
                puntos=[
                    SurfacePointResponse(
                        fila=int(point["fila"]),
                        columna=int(point["columna"]),
                        x_mm=float(point["x_mm"]),
                        y_mm=float(point["y_mm"]),
                        z_mm=None if point["z_mm"] is None else float(point["z_mm"]),
                        estado=str(point["estado"]),
                        observacion=str(point["observacion"]) if point.get("observacion") is not None else None,
                    )
                    for point in value["puntos"]
                ],
            )
            for key, value in surfaces.items()
        },
        creado_en=height_map.creado_en.isoformat(),
        actualizado_en=height_map.actualizado_en.isoformat(),
    )

