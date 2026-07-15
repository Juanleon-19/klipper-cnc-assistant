# Arquitectura actual

## Objetivo de esta fase

La arquitectura actual integra el flujo vertical del producto: proyecto, montaje, operaciones, preparación física, mapa medido, compensación, descarga de G-code compensado y preflight de ejecución. El modo simulado se conserva, pero el modo físico está conectado de extremo a extremo en software y requiere validación física supervisada.

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
- `heightmap/`: modelos, interpolación, simulación, mapa relativo medido y compensación matemática.
- `application/heightmap_service.py`: reglas de negocio del mapa y persistencia asociada.
- `application/reference_service.py`: referencias de montaje, origen X/Y y referencia Z simulada o medida.
- `application/services.py`: proyectos, sesión de máquina, estado general y compatibilidad de esquema.

## Flujo de datos actual

1. El usuario crea un proyecto; el dominio crea automáticamente “Montaje principal”.
2. Cada operación se asigna a un montaje mediante setup_id y conserva su propio archivo, herramienta, análisis y estado.
3. FastAPI valida el payload y lo entrega a `ProjectService`.
4. `JsonProjectRepository` persiste el proyecto en `data/projects/<id>/project.json`.
5. El usuario carga un archivo `.nc`, `.gcode` o `.tap`.
6. El analizador produce límites, incidencias, alturas de trayectoria, metadatos y `analysis_version`.
7. El frontend muestra la trayectoria y detecta si el análisis guardado quedó obsoleto respecto a la versión actual.
8. En modo simulado, la sesión confirma referencias manuales sin mover hardware. En modo físico, `MachineRuntime` captura posición y sonda, y `ReferenceSessionService` guarda origen X/Y y referencia Z medida.
9. `PhysicalMapService` planifica mapas medidos desde operaciones activas y `HeightMapService` valida mapas simulados/importados.
10. `heightmap/analysis.py` interpola solo dentro de la región medida y nunca compensa fuera del dominio.
11. `CompensatedGCodeService` genera archivos nuevos en `generated/compensated/` con metadatos y hash, subdividiendo segmentos cuando hace falta.

## Jerarquía de preparación

```text
ProyectoPCB
└── MontajePCB (preparación, referencias y mapa)
    └── OperacionPCB (archivo, herramienta, análisis, trayectoria y estado)
```

El esquema 1.4 acepta operaciones repetidas del mismo tipo dentro de un montaje. Los órdenes son únicos solo dentro de cada montaje. Al leer un proyecto antiguo sin `setup_id` se crea “Montaje principal”; los mapas legados bajo `maps/<operation_id>` se adoptan sin eliminar el original.

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

## Sesión de referencia

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
- origen X/Y, referencia Z, región sondeable, mapa y validación pertenecen al montaje y se comparten entre sus operaciones;
- si se pierde la sesión de máquina, la referencia vuelve a estado desconocido;
- el homing real solo se asume cuando `MachineRuntime` confirma `toolhead.homed_axes` y velocidad cero.

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
- se genera G-code compensado bajo confirmación explícita, pero no se envía ni ejecuta automáticamente durante desarrollo.

## Seguridad actual

- en modo simulado no se llama a Moonraker ni se abre hardware;
- en modo físico los movimientos solo se disparan por endpoints `POST` explícitos y estados autorizados;
- Sistema queda como diagnóstico técnico y emergencia;
- el workspace contiene las acciones productivas;
- la ejecución real de trabajos queda bloqueada por preflight y confirmación supervisada.


## integración física inicial: runtime físico

La aplicación añade `MachineRuntime` como singleton por proceso FastAPI. En modo `SIMULATED` no abre puerto serie, no inicia WebSocket y no envía comandos de movimiento. En modo `PHYSICAL`, la conexión es explícita y usa `MOONRAKER_URL`, `MOONRAKER_WS`, `SERIAL_PORT` y `SERIAL_BAUDRATE`.

`MachineRuntime` administra Moonraker HTTP, Moonraker WebSocket, `MachineState`, `SerialDriver`, `CommandMapper`, `ManualJogController`, `JogController`, estado del joystick, botones, sonda, permisos de movimiento, eventos y parada.

Los comandos físicos son operaciones `POST`; las consultas de diagnóstico no tienen efectos laterales. La inicialización física se orquesta en backend y no depende de una macro `HOME_AND_CENTER`. El centro se calcula con límites descubiertos desde Klipper.

La compensación usa coordenadas locales del montaje/G-code para consultar el mapa. La posición física del montaje se guarda como referencia `MEASURED` y se usa para ubicar físicamente el montaje, no para extrapolar ni corregir fuera de dominio.


## Integración física corregida

El runtime productivo conserva el protocolo validado en los experimentos 007 y 008: `SerialDriver`, `CommandMapper`, `ManualJogController` y `JogController` son la frontera de entrada y movimiento. `MoonrakerTelemetry` suscribe `motion_report` y `toolhead` para mantener posición, velocidad, límites y `homed_axes` en el mismo `MachineState` compartido.

Moonraker separa aceptación HTTP, ejecución física y confirmación de estado. `G28` puede producir timeout de transporte sin declararse fallo físico si Klipper sigue conectado; el backend consulta `toolhead.homed_axes`, velocidad y límites hasta `MACHINE_HOME_TIMEOUT`. Los movimientos automáticos esperan posición objetivo y velocidad cero hasta `MACHINE_MOVE_TIMEOUT`.

El servicio `PhysicalMapService` planifica mapas `MEASURED` por montaje/cara/revisión de colocación/configuración, usa la unión de límites analizados de operaciones activas del montaje/cara y genera recorrido serpentino. La ejecución de malla es punto a punto: Z segura, XY, sondeo discreto, retracto y persistencia inmediata. Las referencias Z viven por herramienta en `tool_references`.


## Modelo físico de superficie y referencias

El modelo físico actual separa dos conceptos:

```text
Mapa de superficie = montaje físico + cara + placement_revision + región/configuración de malla
Referencia Z = montaje + herramienta instalada + instalación/revisión de esa herramienta
```

`PhysicalMapService` persiste mapas medidos bajo `maps/measured/<setup>/<face>/placement-1/<timestamp>/height_map.json`. Cada punto guarda Z absoluto (`z_measured_abs`) y `delta_z = z_measured_abs - acquisition_reference_z`. El `height_map` usado para compensación contiene la superficie relativa `delta_z`, por eso puede reutilizarse con otra herramienta si la PCB no se movió.

Los mapas legados por herramienta siguen leyéndose y se migran en memoria a `surface-map-v2`; no se eliminan ni sobrescriben. La identidad de herramienta se conserva en `tool_references` y en `acquisition_tool_id` para trazabilidad.

## Generación real de G-code compensado

`CompensatedGCodeService` genera archivos nuevos en `generated/compensated/` y un JSON de metadatos. La convención es:

```text
x_compensado = x_original
y_compensado = y_original
z_compensado = z_original + delta_superficie(x,y)
```

La generación bloquea si falta mapa medido completo, si el mapa pertenece a otro montaje/cara, si falta referencia Z vigente de la herramienta, si la operación no está cubierta o si un punto cae fuera del dominio. Los movimientos largos se subdividen usando un límite derivado de la separación de malla; los arcos usan los segmentos discretizados por el analizador G-code actual.

## Ejecución física

La web no controla motores directamente. El backend orquesta estados y Moonraker/Klipper ejecutan movimiento. El paso `Ejecución` solo prepara preflight y trazabilidad en esta fase; no arranca trabajos durante desarrollo.
