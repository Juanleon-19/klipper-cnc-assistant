# Estado del proyecto: Klipper CNC Assistant

**Última actualización:** 2026-07-14
**Rama local:** `fix/phase-43-stability-workflow`
**Estado global:** Fase 1 implementada y validada en software; pendiente validación física supervisada

Este documento resume el estado local real del repositorio. La rama contiene cambios no publicados de Fase 1 sobre la base `3911214`.

## Resumen ejecutivo

La aplicación conserva el modo simulado como predeterminado y añade una integración física segura y explícita con Moonraker, Klipper y el controlador Arduino. El producto sigue separando preparación digital, simulación y control físico.

Implementado en esta fase:

- runtime físico singleton por proceso FastAPI;
- modo `SIMULATED` predeterminado y modo `PHYSICAL` mediante `MACHINE_MODE=physical`;
- diagnóstico consolidado de aplicación, Moonraker, Klipper, Arduino, controlador y seguridad;
- flujo físico guiado de conexión, diagnóstico, Z objetivo, homing, cálculo de centro con límites reales, Z segura, XY centro y Z objetivo;
- joystick discreto cardinal usando `ManualJogController` y `JogController`;
- solicitud y confirmación de sonda de un punto;
- captura física de origen X/Y y referencia Z de montaje, con fuente `MEASURED`;
- separación de referencias `SIMULATED` y `MEASURED`;
- bloqueo de sobrescritura silenciosa de referencias medidas con simuladas;
- dominio de compensación con tolerancia explícita, detalle de puntos fuera, distancia al dominio y bloqueo de validación si la cobertura es insuficiente;
- vista web “Sistema físico” con modo permanente SIMULADO/FÍSICO y acciones físicas bloqueadas fuera de modo físico;
- documentación de variables y procedimiento manual.

No se implementó todavía:

- sondeo físico de malla completa;
- recorrido automático de múltiples puntos;
- generación o descarga de G-code compensado ejecutable;
- ejecución completa de trabajos;
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
- `MACHINE_TELEMETRY_FRESH_TIMEOUT=2.0`
- `MACHINE_SERIAL_FRESH_TIMEOUT=2.0`
- `MACHINE_SETTLE_TOLERANCE=0.02`
- `MACHINE_VELOCITY_TOLERANCE=0.05`
- `MACHINE_MOVE_TIMEOUT=8.0`
- `PROBE_STEP_DISTANCE=0.05`
- `PROBE_LOWER_SPEED=1.0`
- `PROBE_RETRACT_DISTANCE=1.0`
- `PROBE_RETRACT_SPEED=2.0`

En esta máquina se detectaron dos instancias Moonraker activas: puerto 7125 y puerto 7126. Por seguridad el producto no elige una automáticamente; `MOONRAKER_URL` y `MOONRAKER_WS` deben declararse explícitamente en modo físico.

## Klipper local

Se inspeccionaron:

- `/home/impresora/printer_kp3s1_data/config/printer.cfg`
- `/home/impresora/printer_kp3s2_data/config/printer.cfg`

No se modificó configuración Klipper, no se reinició Klipper y no se creó macro `HOME_AND_CENTER`. El cálculo de centro, Z segura y Z objetivo pertenecen al backend. Existe un riesgo preexistente: ambos `printer.cfg` incluyen `mainsail.cfg` dos veces. Queda documentado, pero no se tocó porque no era necesario para Fase 1.

## Modelo de coordenadas y dominio

Convención usada por la compensación:

- el archivo G-code se analiza en coordenadas propias del archivo;
- para PCB, esas coordenadas se tratan como coordenadas locales del montaje/material;
- el mapa se define en coordenadas locales del montaje/material mediante `probe_region`;
- las referencias físicas guardan posición de máquina para ubicar ese montaje en la CNC;
- la compensación matemática consulta el mapa en X/Y local del montaje;
- la transformación a máquina se reserva para movimientos físicos y no genera G-code ejecutable en esta fase.

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
- botón externo genera `PROBE_REQUESTED`;
- la sonda baja solo tras confirmación explícita;
- emergencia `M112` separada de cancelar operación y requiere confirmación API;
- no existe jog continuo de producto.

## Pruebas ejecutadas

| Verificación | Resultado |
| --- | --- |
| `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v` | 57 pruebas correctas |
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

## Pendiente para Fase 2

- Validación física supervisada completa.
- Endurecer reconexión y recuperación de Moonraker/WebSocket/serie.
- Medir repetibilidad de sonda y definir debounce si la evidencia lo exige.
- Implementar malla física completa solo después de validar sonda de un punto.
- Diseñar generación revisable de G-code compensado, todavía sin ejecución automática.
- Separar carga diferida de Plotly si afecta operación real.
