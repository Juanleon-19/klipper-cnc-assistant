# Estado actual de estabilización

Fecha: 2026-07-18. Auditoría de código y despliegue; no constituye validación física nueva.

## Git y despliegue

- Rama: `stabilization/phase-0-audit`; SHA: `c627f2d952b2faeb494a12903fede804283d8d8d` (`Allow physical reference remeasurement`).
- Frente a `main`: 39 commits por delante y 0 por detrás.
- Cambios preservados: modificaciones en `firmware/arduino_pro_mini/joystick_controller/joystick_controller.ino` y `firmware/.../tests/005_serial_protocol/test_serial_protocol.py`; copias no rastreadas `joystick_controller.ino.bak` y `test_serial_protocol.py.bak`.
- La modificación local de firmware declara v0.1.1 y filtra temporalmente la entrada de sonda. No se compiló ni validó en esta auditoría.
- Servicio activo `klipper-cnc-assistant.service`: `WorkingDirectory=/home/impresora/klipper-cnc-assistant`; `ExecStart=.venv/bin/python -m klipper_cnc_assistant serve --host 127.0.0.1 --port 8000 --data-dir /home/impresora/klipper-cnc-assistant/data`.
- EnvironmentFile: `deploy/klipper-cnc-assistant.env`; modo físico, auto-connect desactivado, Moonraker HTTP `127.0.0.1:7126` y WS `127.0.0.1:7126/websocket`. Existe otra instancia en 7125.
- systemd no registra un SHA desplegado: el servicio usa el árbol de trabajo anterior mientras ese checkout no cambie.
- Persistencia JSON: `data/projects/<id>/project.json`; originales `originals/`; mapas `maps/`; compensados `generated/compensated/`; jobs `reports/jobs/<setup>/<face>/`.

## Arquitectura y funciones

```text
React/Vite -> FastAPI -> servicios -> dominio + JSON
                         |-> G-code, mapas, compensación, jobs
                         |-> MachineRuntime -> Moonraker HTTP/WS -> Klipper
                                              -> Arduino serie -> jog/sonda
```

- Frontend React/TypeScript/Vite con workspace, visor, referencias, mapa, compensación y ejecución.
- API para proyecto/operación, referencias, mapa físico, compensación, runtime y jobs.
- Servicios: `ProjectService`, `ReferenceSessionService`, `HeightMapService`, `PhysicalMapService`, `CompensatedGCodeService`, `MeshExecutionService`, `JobService`.
- XY pertenece al montaje; Z se guarda por herramienta en `tool_references` del mapa de superficie. Mapa, región/exclusiones y placement son de montaje/cara; G-code, análisis, compensado y fila de job son por operación.

## Validación y hallazgos

- Automatizado/mocks: proyectos, análisis, cobertura, compensación, API y partes de runtime. No validan hardware.
- Según registros previos (no repetidos): conexión Moonraker/Klipper/Arduino, preparación y sonda de referencia.
- Pendiente físico integral: recuperación WS, malla robusta, fallo inicial, debounce local, compensación en PCB, aire y multioperación.

### Telemetría

`MoonrakerTelemetry` abre una conexión y se suscribe una vez a `motion_report`, `toolhead`, `gcode_move`. `MachineRuntime` llama conectado a un hilo vivo; no hay bucle de reconexión ni resuscripción. `last_telemetry_at` se actualiza por callbacks/refresh HTTP y stale usa `TELEMETRY_STALE_TIMEOUT`. Hilo vivo y telemetría reciente son distintos; el control manual se bloquea si está obsoleta.

### Malla física

Un único worker global. Cada punto intenta tres veces (dos reintentos); al agotarse queda `FAILED`, la malla `MESH_PAUSED` y el `finally` libera el worker. Mientras sigue vivo, otra solicitud recibe “Ya hay una operación física de malla en curso.” Pausa/cancelación son cooperativas entre puntos: no interrumpen `probe_mesh_point` ni G-code ya aceptado. Tras reinicio no revive worker; requiere reanudar explícitamente.

`next_pending_point()` incluye `FAILED`: reanudar reintenta implícitamente el fallo. No hay endpoint/UI de reintento individual ni omisión explícita, aunque `record_point()` admite `SKIPPED`.

`probe_config` de UI se usa en runtime: `safe_z_mm`, `probe_step_mm`, `probe_feed_mm_min` (mm/s) y `retract_mm`; faltantes usan defaults de entorno. La velocidad de sonda también se usa para retracto.

### Cobertura y ejecución

La región de malla parte de material menos márgenes/exclusiones, no del envolvente del G-code. La cobertura de operaciones del mismo montaje/cara se finaliza tras `MESH_COMPLETE`; preview no ofrece prevalidación bloqueante. Por ello un contorno de borde puede quedar fuera pese a una malla medida.

La ruta individual `execution/start` se bloquea deliberadamente. La ruta `job-run/start` sí crea worker, sube el compensado y llama `start_print`: hay dos políticas de inicio. README afirma inicio real bloqueado, contradicción con `JobService`.

No existe reinicio independiente por operación. Reiniciar sin afectar mapa/referencias debe aislar G-code, análisis, compensados/metadatos y estado/run de esa operación; no tocar origen XY, placement, mapa, malla ni referencias de herramienta compatibles.

## Deuda conocida

- README histórico indica inicio real bloqueado y conteos de pruebas ya desactualizados.
- Sin reconexión WS automática.
- `FAILED` vuelve a pendiente sin decisión de operador.

Actualización de Fase 1: se añadieron acciones persistidas de reintentar/omitir punto fallido y cancelación cooperativa de malla; falta validación física.
