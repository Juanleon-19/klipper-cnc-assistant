from __future__ import annotations

from math import exp, pi, sin
from random import Random

from .analysis import compute_height_map
from .models import ExclusionZone, HeightGrid, HeightSample, ProbeRegion, SampleQuality


SIMULATION_SURFACES = {
    "plana",
    "inclinada",
    "deformacion_suave",
    "elevacion_localizada",
    "ruido_pequeno",
    "punto_faltante",
    "punto_atipico",
    "inclinacion_y_deformacion",
}


def generate_simulated_height_map(
    *,
    proyecto_id: str,
    operacion_id: str,
    version: int,
    probe_region: ProbeRegion,
    exclusion_zones: tuple[ExclusionZone, ...],
    filas: int,
    columnas: int,
    superficie_simulada: str,
    repeticion_simulacion: int,
) -> object:
    if superficie_simulada not in SIMULATION_SURFACES:
        raise ValueError("Superficie simulada no soportada.")
    if filas < 2 or columnas < 2:
        raise ValueError("La malla simulada requiere al menos 2 filas y 2 columnas.")

    randomizer = Random(repeticion_simulacion)
    paso_x = probe_region.ancho_mm / (columnas - 1)
    paso_y = probe_region.alto_mm / (filas - 1)
    grid = HeightGrid(
        filas=filas,
        columnas=columnas,
        ancho_mm=probe_region.ancho_mm,
        alto_mm=probe_region.alto_mm,
        paso_x_mm=paso_x,
        paso_y_mm=paso_y,
    )
    center_x = probe_region.min_x_mm + probe_region.ancho_mm / 2
    center_y = probe_region.min_y_mm + probe_region.alto_mm / 2
    missing_cell = (filas // 2, columnas // 2)
    outlier_cell = (max(0, filas // 3), min(columnas - 1, columnas // 2))

    samples: list[HeightSample] = []
    for fila in range(filas):
        for columna in range(columnas):
            x_mm = probe_region.min_x_mm + columna * paso_x
            y_mm = probe_region.min_y_mm + fila * paso_y
            z_mm = _surface_height(
                superficie_simulada=superficie_simulada,
                x_mm=x_mm,
                y_mm=y_mm,
                probe_region=probe_region,
                center_x=center_x,
                center_y=center_y,
                randomizer=randomizer,
            )
            if superficie_simulada == "punto_faltante" and (fila, columna) == missing_cell:
                z_value = None
                quality = SampleQuality.FALTANTE
                observation = "Punto faltante simulado."
            else:
                z_value = z_mm
                quality = SampleQuality.VALIDA
                observation = "DATOS SIMULADOS"
            if superficie_simulada == "punto_atipico" and (fila, columna) == outlier_cell and z_value is not None:
                z_value += 0.16
                observation = "DATOS SIMULADOS · punto atipico"
            samples.append(
                HeightSample(
                    id=f"hm_{fila}_{columna}",
                    x_mm=x_mm,
                    y_mm=y_mm,
                    z_mm=z_value,
                    fila=fila,
                    columna=columna,
                    origen_datos="simulado",
                    estado_calidad=quality,
                    observacion=observation,
                )
            )

    return compute_height_map(
        proyecto_id=proyecto_id,
        operacion_id=operacion_id,
        version=version,
        fuente_datos="simulado",
        superficie_simulada=superficie_simulada,
        repeticion_simulacion=repeticion_simulacion,
        etiqueta_simulada=True,
        grid=grid,
        probe_region=probe_region,
        exclusion_zones=exclusion_zones,
        muestras=samples,
        estado="datos simulados",
    )


def _surface_height(
    *,
    superficie_simulada: str,
    x_mm: float,
    y_mm: float,
    probe_region: ProbeRegion,
    center_x: float,
    center_y: float,
    randomizer: Random,
) -> float:
    local_x = x_mm - probe_region.min_x_mm
    local_y = y_mm - probe_region.min_y_mm
    width = max(probe_region.ancho_mm, 1.0)
    height = max(probe_region.alto_mm, 1.0)
    tilt_x = 0.05 * (local_x / width)
    tilt_y = -0.035 * (local_y / height)
    smooth = 0.032 * sin(pi * local_x / width) * sin(pi * local_y / height)
    bump = 0.082 * exp(
        -(
            ((x_mm - center_x) ** 2) / max((probe_region.ancho_mm * 0.18) ** 2, 1.0)
            + ((y_mm - center_y) ** 2) / max((probe_region.alto_mm * 0.18) ** 2, 1.0)
        )
    )
    noise = randomizer.uniform(-0.006, 0.006)

    if superficie_simulada == "plana":
        return 0.0
    if superficie_simulada == "inclinada":
        return tilt_x + tilt_y
    if superficie_simulada == "deformacion_suave":
        return smooth
    if superficie_simulada == "elevacion_localizada":
        return bump
    if superficie_simulada == "ruido_pequeno":
        return noise
    if superficie_simulada == "punto_faltante":
        return tilt_x + smooth * 0.45
    if superficie_simulada == "punto_atipico":
        return smooth * 0.3
    if superficie_simulada == "inclinacion_y_deformacion":
        return tilt_x + tilt_y + smooth + bump * 0.35 + noise
    raise ValueError("Superficie simulada no soportada.")
