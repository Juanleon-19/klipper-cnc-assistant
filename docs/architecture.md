# Arquitectura actual

## Objetivo de esta fase

La Fase 4.2 reorganiza el workspace, corrige el modelo del mapa de alturas y añade un flujo de referencia completamente simulado, manteniendo todo el sistema en **modo seguro y sin control físico**.

## Capas productivas

```text
frontend/ React + TypeScript + Vite
        |
        | fetch / mismo origen en producción
        v
src/klipper_cnc_assistant/api
        |
        v
src/klipper_cnc_assistant/application
        |
        v
src/klipper_cnc_assistant/domain
        |
        v
src/klipper_cnc_assistant/storage
```

Módulos funcionales principales:

- `gcode/`: análisis sintáctico y geométrico del archivo cargado.
- `heightmap/`: modelos, interpolación, simulación y previsualización matemática de compensación.
- `application/heightmap_service.py`: reglas de negocio del mapa y persistencia asociada.
- `application/reference_service.py`: sesión de preparación simulada por operación.
- `application/services.py`: proyectos, sesión de máquina simulada y estado general.

## Flujo de datos actual

1. El usuario crea un proyecto y una operación desde la interfaz web.
2. FastAPI valida el payload y lo entrega a `ProjectService`.
3. `JsonProjectRepository` persiste el proyecto en `data/projects/<id>/project.json`.
4. El usuario carga un archivo `.nc`, `.gcode` o `.tap`.
5. El analizador produce límites, incidencias, alturas de trayectoria, metadatos y `analysis_version`.
6. El frontend muestra la trayectoria y detecta si el análisis guardado quedó obsoleto respecto a la versión actual.
7. La sesión de referencia simulada confirma estado de máquina, origen X/Y y referencia Z sin mover hardware.
8. `HeightMapService` valida `probe_region`, exclusiones, muestras y dominio interpolable.
9. `heightmap/analysis.py` interpola solo dentro de la región medida y nunca compensa fuera del dominio.
10. `heightmap/compensation.py` genera una vista previa matemática sobre la trayectoria, subdividiendo segmentos cuando hace falta.

## Modelo de mapa de alturas

El mapa separa explícitamente:

- material bruto;
- región sondeable interior;
- región ocupada por la trayectoria;
- zonas excluidas;
- dominio interpolable.

Tipos principales:

- `ProbeRegion`
- `ExclusionZone`
- `HeightMap`
- `HeightMapStatistics`

Restricciones de negocio:

- `probe_region` debe quedar dentro del material;
- filas y columnas deben ser válidas;
- ningún punto de muestreo puede caer en una zona excluida;
- la interpolación y la compensación quedan bloqueadas fuera del dominio medido.

## Sesión de referencia simulada

`OperationPreparation` y `PreparationState` modelan el flujo:

- `sin_iniciar`
- `referencia_maquina_pendiente`
- `referencia_maquina_confirmada`
- `origen_xy_pendiente`
- `origen_xy_confirmado`
- `referencia_z_pendiente`
- `referencia_z_confirmada`
- `region_sondeable_configurada`
- `mapa_disponible`
- `mapa_validado`
- `compensacion_previsualizada`

Separación de sesión:

- la referencia de máquina pertenece a la sesión de máquina;
- origen X/Y, referencia Z, mapa y validación pertenecen a la operación;
- si se pierde la sesión de máquina, la referencia vuelve a estado desconocido;
- no se asume homing real en ningún punto.

## Convención matemática de compensación

La vista previa usa la convención:

```text
z_compensada = z_original + (superficie_xy - z_referencia)
```

Donde:

- `z_original` es la Z programada en la trayectoria;
- `superficie_xy` es la corrección interpolada en el punto X/Y;
- `z_referencia` es el valor de referencia del plano del mapa.

Detalles de implementación:

- cada segmento puede subdividirse virtualmente usando medio paso mínimo de la rejilla;
- los valores fuera del dominio se marcan y bloquean la vista previa utilizable;
- no se genera G-code, ni archivos, ni comandos hacia la máquina.

## Seguridad actual

- no se llama a Moonraker para home, jog, probe, spindle o ejecución;
- no se envía G-code a Klipper;
- no existe movimiento real desde la aplicación web;
- la sesión de máquina reportada es simulada;
- la compensación actual es solo analítica y visual.
