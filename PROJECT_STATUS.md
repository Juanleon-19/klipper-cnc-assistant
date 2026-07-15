# Estado del proyecto: Klipper CNC Assistant

**Última actualización:** 2026-07-14
**Rama local:** `fix/phase-43-stability-workflow`
**Estado global:** Producto integrado de extremo a extremo en software; pendiente prueba integral supervisada con PCB de descarte

Este documento resume el estado local real del repositorio. La rama contiene cambios no publicados de integración física inicial sobre la base `3911214`.

## Resumen ejecutivo

La aplicación conserva el modo simulado como predeterminado e integra el flujo físico supervisado dentro del montaje: referencia por herramienta, mapa de superficie por montaje/cara, sondeo punto a punto y generación de G-code compensado. El producto sigue separando preparación digital, simulación y control físico.

Implementado en esta fase:

- runtime físico singleton por proceso FastAPI;
- modo `SIMULATED` predeterminado y modo `PHYSICAL` mediante `MACHINE_MODE=physical`;
- diagnóstico consolidado de aplicación, Moonraker, Klipper, Arduino, controlador y seguridad;
- flujo físico guiado de conexión, diagnóstico, homing confirmado por `toolhead.homed_axes`, Z segura indicada por usuario y XY al centro real;
- joystick discreto cardinal usando `ManualJogController` y `JogController`;
- solicitud y confirmación de sonda de un punto;
- captura física de origen X/Y y referencia Z de montaje, con fuente `MEASURED`;
- separación de referencias `SIMULATED` y `MEASURED`;
- bloqueo de sobrescritura silenciosa de referencias medidas con simuladas;
- dominio de compensación con tolerancia explícita, detalle de puntos fuera, distancia al dominio y bloqueo de validación si la cobertura es insuficiente;
- `MachineContext` como fuente única de estado visible: sidebar, Sistema y workspace muestran el mismo modo;
- Sistema queda como diagnóstico técnico, conexión, cancelación y emergencia;
- el flujo productivo vive dentro del proyecto/montaje;
- documentación de variables y procedimiento manual.

No se implementó todavía:

- arranque real de mecanizado sin confirmación física supervisada;
- control automático de spindle;
- jog continuo;
- rotación automática de PCB;
- extrapolación fuera del dominio;
- cambios automáticos de herramienta.

## Arquitectura actual

```text
Frontend React/Vite
        |
        v
FastAPI /api + SPA estática
        |
        +-- ProjectService / HeightMapService / ReferenceSessionService
        |       +-- Dominio, persistencia JSON, analizador G-code, mapas y compensación
        |
        +-- MachineRuntime singleton
                +-- MoonrakerClient HTTP
                +-- MoonrakerTelemetry WebSocket
                +-- MachineState
                +-- SerialDriver + CommandMapper
                +-- ManualJogController -> JogController -> Moonraker
```

El runtime físico existe una sola vez durante la vida de la aplicación. No abre puerto serie ni WebSocket en modo simulado. En modo físico requiere conexión explícita o `MACHINE_AUTO_CONNECT=true`.

## Configuración física

Variables principales:

- `MACHINE_MODE=simulated|physical`
- `MACHINE_AUTO_CONNECT=false|true`
- `MOONRAKER_URL=http://host:puerto`
- `MOONRAKER_WS=ws://host:puerto/websocket`
- `SERIAL_PORT=/dev/ttyUSB0`
- `SERIAL_BAUDRATE=115200`
- `MACHINE_SAFE_Z=10.0`
- `MOONRAKER_REQUEST_TIMEOUT=2.0`
- `MACHINE_HOME_TIMEOUT=90.0`
- `MACHINE_TELEMETRY_FRESH_TIMEOUT=2.0` o `TELEMETRY_STALE_TIMEOUT=2.0`
- `MACHINE_SERIAL_FRESH_TIMEOUT=2.0`
- `MACHINE_SETTLE_TOLERANCE=0.02`
- `MACHINE_VELOCITY_TOLERANCE=0.05`
- `MACHINE_MOVE_TIMEOUT=8.0`
- `SERIAL_STARTUP_DELAY=2.0`
- `PROBE_STEP_DISTANCE=0.05`
- `PROBE_LOWER_SPEED=1.0`
- `PROBE_RETRACT_DISTANCE=1.0`
- `PROBE_RETRACT_SPEED=2.0`

En esta máquina se detectaron dos instancias Moonraker activas: puerto 7125 y puerto 7126. Por seguridad el producto no elige una automáticamente; `MOONRAKER_URL` y `MOONRAKER_WS` deben declararse explícitamente en modo físico.

## Klipper local

Se inspeccionaron:

- `/home/impresora/printer_kp3s1_data/config/printer.cfg`
- `/home/impresora/printer_kp3s2_data/config/printer.cfg`

No se modificó configuración Klipper, no se reinició Klipper y no se creó macro `HOME_AND_CENTER`. El cálculo de centro, Z segura de traslado pertenece al backend. Existe un riesgo preexistente: ambos `printer.cfg` incluyen `mainsail.cfg` dos veces. Queda documentado, pero no se tocó porque no era necesario para integración física inicial.

## Modelo de coordenadas y dominio

Convención usada por la compensación:

- el archivo G-code se analiza en coordenadas propias del archivo;
- para PCB, esas coordenadas se tratan como coordenadas locales del montaje/material;
- el mapa se define en coordenadas locales del montaje/material mediante `probe_region`;
- las referencias físicas guardan posición de máquina para ubicar ese montaje en la CNC;
- la compensación matemática consulta el mapa en X/Y local del montaje;
- la transformación a máquina se reserva para movimientos físicos; la generación de G-code compensado conserva coordenadas locales X/Y y solo modifica Z con la superficie relativa.

Un punto está dentro del mapa rectangular si:

```text
map_x_min - 0.000001 <= x <= map_x_max + 0.000001
map_y_min - 0.000001 <= y <= map_y_max + 0.000001
```

La tolerancia de `0.000001 mm` solo absorbe error numérico en bordes. Las zonas excluidas siguen bloqueando interpolación.

El caso observado de muchos puntos fuera de dominio era real: el mapa activo `setup-main` cubría `X=10..45`, `Y=10..45`, mientras la trayectoria local usaba puntos hasta `Y=51.474` y también puntos con `X/Y=0`. Ahora el sistema reporta cantidad, operación, punto, causa y distancia al dominio, y bloquea la validación si la cobertura es insuficiente.

## Seguridad física

Reglas implementadas:

- modo simulado predeterminado;
- consultas de estado sin efectos laterales;
- comandos físicos solo mediante `POST` explícitos;
- inicialización bloqueada si falta modo físico, conexión, Klipper ready, homing, límites o si existe otra operación activa;
- joystick bloqueado hasta inicialización y habilitación manual;
- movimiento manual discreto, cardinal y por transición `CENTER -> dirección`;
- diagonales descartadas;
- botón externo inicia la referencia cuando el estado está `REFERENCE_ARMED`;
- la sonda baja solo en referencia armada o ejecución explícita de punto de malla;
- emergencia `M112` separada de cancelar operación y requiere confirmación API;
- no existe jog continuo de producto.

## Pruebas ejecutadas

| Verificación | Resultado |
| --- | --- |
| `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v` | 64 pruebas correctas |
| `.venv/bin/python -m pip check` | Sin dependencias rotas |
| `npm run lint` | Correcto |
| `npm run test` | 37 pruebas correctas en 10 archivos |
| `npm run build` | Correcto, con advertencia no bloqueante por tamaño de Plotly |

## Riesgos pendientes

- La integración física aún no fue validada manualmente desde la web.
- La sonda de un punto depende de pasos discretos; no cancela un paso ya enviado a Klipper, igual que el experimento 008.
- La repetibilidad y rebote de sonda requieren validación física supervisada.
- La persistencia JSON sigue siendo local y simple; no hay bloqueo multiusuario robusto.
- La UI de diagnóstico usa polling moderado, no streaming dedicado.
- El bundle de Plotly sigue siendo grande.
- Firefox headless no tuvo WebGL para la captura 3D; validar superficie 3D en navegador normal con WebGL durante la prueba física.

## Pendiente para Fase 2

- Validación física supervisada completa.
- Endurecer reconexión y recuperación de Moonraker/WebSocket/serie.
- Medir repetibilidad de sonda y definir debounce si la evidencia lo exige.
- Validar físicamente la malla completa con PCB de descarte antes de usar trabajos reales.
- Diseñar generación revisable de G-code compensado, todavía sin ejecución automática.
- Separar carga diferida de Plotly si afecta operación real.


## Revisión de experimentos físicos

Se revisaron los experimentos `001` a `008`. La migración productiva reutiliza la ruta validada `Arduino -> SerialDriver -> CommandMapper -> ManualJogController -> JogController -> Moonraker -> Klipper`. El experimento 007 validó joystick cardinal discreto, sin diagonales y con un movimiento por transición `CENTER -> dirección`. El experimento 008 mantuvo ese comportamiento y validó sonda por pasos discretos con captura `X/Y/Z` y retracto.

La causa del timeout observado era el uso del timeout HTTP fijo de 5 s como si fuera confirmación de ejecución física. Ahora el comando se trata como aceptación de transporte y la finalización se confirma por estado Klipper. La causa del homing no reconocido era que la WebSocket solo suscribía `motion_report`, no `toolhead.homed_axes`. La causa de “puerto abierto, paquetes válidos = 0” queda diagnosticable: el runtime distingue bytes, paquetes completos, checksums, drops de sincronía, hilo serial, edad del último paquete, última excepción y espera de reinicio Arduino al abrir el puerto.

## Mapas medidos por montaje/cara

Un mapa físico medido se identifica por montaje, cara, revisión de colocación, región y configuración de malla. Se guarda bajo `maps/measured/<setup>/<face>/placement-1/<timestamp...>/height_map.json`, conserva `source=MEASURED`, no reemplaza mapas simulados y persiste cada punto inmediatamente. Las herramientas no comparten referencia Z: cada una conserva su propia entrada en `tool_references`, pero reutilizan la superficie relativa si la PCB no se movió.


## Configuración local verificada el 2026-07-14

`systemctl show klipper-cnc-assistant.service -p Environment` reporta `MACHINE_MODE=physical`, `MOONRAKER_URL=http://127.0.0.1:7126`, `MOONRAKER_WS=ws://127.0.0.1:7126/websocket`, `SERIAL_PORT=/dev/ttyUSB0`, `SERIAL_BAUDRATE=115200` y `MACHINE_SAFE_Z=10`.

Se leyeron `printer_kp3s1_data/config/printer.cfg` y `printer_kp3s2_data/config/printer.cfg`. No se modificaron límites, pasos, dirección de motores, macros ni firmware. Ambos archivos mantienen doble `[include mainsail.cfg]`, observado como condición preexistente.


## Estado actualizado 2026-07-14: flujo físico dentro del montaje

Implementado en software sobre los commits locales existentes, sin push y sin ejecutar movimientos físicos:

- El mapa físico medido cambió al modelo correcto: superficie del montaje/cara/revisión de colocación, no exclusiva de herramienta.
- `PhysicalMapService` guarda `schema_version=surface-map-v2`, `map_model=SURFACE_BY_SETUP_FACE_PLACEMENT`, origen físico X/Y, región local, región de máquina, puntos absolutos y `delta_z` relativo.
- Las referencias Z quedan en `tool_references` por herramienta/instalación. Cambiar herramienta permite guardar nueva referencia Z y reutilizar el mapa relativo del montaje si la PCB no cambió.
- Se mantiene migración compatible en lectura para mapas legados `maps/measured/<setup>/<tool>/...`; no se destruyen datos existentes.
- La pestaña `Mapa de alturas` ahora muestra selector `SIMULADO` / `MEDIDO FÍSICAMENTE`, acciones `Preparar mapa físico`, `Iniciar sondeo de malla`, `Pausar`, `Reanudar` y `Cancelar`.
- La pestaña `Compensación` genera un archivo real nuevo en `generated/compensated/` y no sobrescribe el original.
- La pestaña `Ejecución` agrega preflight visual para mapa medido, archivo compensado y referencia Z; no inicia ejecución física durante desarrollo.
- La interpolación bilineal ahora admite bordes, una fila, una columna y un solo punto sin extrapolar fuera del dominio.
- La generación de G-code compensado conserva X/Y, usa el mapa relativo `delta_z`, subdivide movimientos según la separación de malla, bloquea cobertura incompleta y registra metadatos/hash.

Validación local:

- `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v`: 64 pruebas OK.
- `.venv/bin/python -m pip check`: sin dependencias rotas.
- `npm run lint`: OK.
- `npm run test`: 37 pruebas OK.
- `npm run build`: OK, con advertencia no bloqueante por tamaño de Plotly.

Riesgos pendientes:

- Falta validar físicamente el ciclo completo con PCB de descarte.
- La subida y arranque de archivo compensado por Moonraker está preparada como preflight/UI, pero el inicio real queda para confirmación supervisada.
- La linealización usa la geometría discretizada por el analizador actual; comandos incompatibles siguen bloqueándose por análisis/preflight.


## Estado actualizado 2026-07-14: integración vertical visible

Implementado y verificado sobre la app servida por FastAPI:

- Sidebar, Sistema y workspace usan `MachineRuntime -> API -> MachineContext` como fuente única de modo; `PHYSICAL` se muestra como `FÍSICO`.
- `SystemBanner` dejó de ser estático y ya no anuncia modo simulado cuando el runtime está físico.
- `Sistema` quedó como diagnóstico técnico; las acciones productivas se ejecutan desde el montaje activo.
- `Referencia` en modo físico muestra conexión, homing, Z segura, centro, joystick X/Y, armado de referencia y captura X/Y/Z por sonda.
- `Mapa de alturas` expone `SIMULADO` y `MEDIDO FÍSICAMENTE`, área desde operaciones, configuración de margen/separación/Z segura, malla, recorrido, progreso y pausa/reanudación/cancelación.
- El visor 2D muestra malla y recorrido serpentino sobre el mapa, con selector local/máquina.
- `Compensación` permite previsualizar, generar y descargar G-code compensado real.
- `Ejecución` muestra preflight y acciones de preparación Moonraker/Klipper, manteniendo bloqueado el inicio real hasta validación supervisada.
- El timeout HTTP de G-code se reconcilia: si Klipper confirma homing/movimiento por estado, `last_error` se limpia y queda evento histórico.
- FastAPI sirve `frontend/dist`; el build final verificado referencia `assets/index-DiGQGU_B.js` y `assets/index-C_RZSt3A.css`.
- Capturas visuales de la app servida guardadas en `docs/artifacts/visual-verification/`.

No se ejecutaron movimientos físicos, no se reinició Klipper/Moonraker y no se hizo push.
