from __future__ import annotations

from math import exp, pi, sin
from random import Random

from .analysis import compute_height_map
from .models import HeightGrid, HeightSample, SampleQuality


SIMULATION_SCENARIOS = {
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
    ancho_mm: float,
    alto_mm: float,
    filas: int,
    columnas: int,
    escenario: str,
    semilla: int,
) -> object:
    if escenario not in SIMULATION_SCENARIOS:
        raise ValueError("Escenario simulado no soportado.")
    if filas < 2 or columnas < 2:
        raise ValueError("La malla simulada requiere al menos 2 filas y 2 columnas.")

    randomizer = Random(semilla)
    paso_x = ancho_mm / (columnas - 1)
    paso_y = alto_mm / (filas - 1)
    grid = HeightGrid(
        filas=filas,
        columnas=columnas,
        ancho_mm=ancho_mm,
        alto_mm=alto_mm,
        paso_x_mm=paso_x,
        paso_y_mm=paso_y,
    )
    center_x = ancho_mm / 2
    center_y = alto_mm / 2
    missing_cell = (filas // 2, columnas // 2)
    outlier_cell = (max(0, filas // 3), min(columnas - 1, columnas // 2))

    samples: list[HeightSample] = []
    for fila in range(filas):
        for columna in range(columnas):
            x_mm = columna * paso_x
            y_mm = fila * paso_y
            z_mm = _scenario_height(
                escenario=escenario,
                x_mm=x_mm,
                y_mm=y_mm,
                ancho_mm=ancho_mm,
                alto_mm=alto_mm,
                center_x=center_x,
                center_y=center_y,
                randomizer=randomizer,
            )
            if escenario == "punto_faltante" and (fila, columna) == missing_cell:
                z_value = None
                quality = SampleQuality.FALTANTE
                observation = "Punto faltante simulado."
            else:
                z_value = z_mm
                quality = SampleQuality.VALIDA
                observation = "DATOS SIMULADOS"
            if escenario == "punto_atipico" and (fila, columna) == outlier_cell and z_value is not None:
                z_value += 0.16
                observation = "DATOS SIMULADOS · punto atípico"
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
        escenario=escenario,
        semilla=semilla,
        etiqueta_simulada=True,
        grid=grid,
        muestras=samples,
        estado="datos simulados",
    )


def _scenario_height(
    *,
    escenario: str,
    x_mm: float,
    y_mm: float,
    ancho_mm: float,
    alto_mm: float,
    center_x: float,
    center_y: float,
    randomizer: Random,
) -> float:
    tilt_x = 0.05 * (x_mm / max(ancho_mm, 1.0))
    tilt_y = -0.035 * (y_mm / max(alto_mm, 1.0))
    smooth = 0.032 * sin(pi * x_mm / max(ancho_mm, 1.0)) * sin(pi * y_mm / max(alto_mm, 1.0))
    bump = 0.082 * exp(
        -(
            ((x_mm - center_x) ** 2) / max((ancho_mm * 0.18) ** 2, 1.0)
            + ((y_mm - center_y) ** 2) / max((alto_mm * 0.18) ** 2, 1.0)
        )
    )
    noise = randomizer.uniform(-0.006, 0.006)

    if escenario == "plana":
        return 0.0
    if escenario == "inclinada":
        return tilt_x + tilt_y
    if escenario == "deformacion_suave":
        return smooth
    if escenario == "elevacion_localizada":
        return bump
    if escenario == "ruido_pequeno":
        return noise
    if escenario == "punto_faltante":
        return tilt_x + smooth * 0.45
    if escenario == "punto_atipico":
        return smooth * 0.3
    if escenario == "inclinacion_y_deformacion":
        return tilt_x + tilt_y + smooth + bump * 0.35 + noise
    raise ValueError("Escenario simulado no soportado.")

