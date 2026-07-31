# Fase 2 — línea base de Referencias y conectividad

Fecha de ejecución: Thursday, July 30, 2026

## Confirmación de contexto

- `pwd`: `/home/impresora/klipper-cnc-assistant-fase2`
- rama: `fase-2/referencias-conectividad`
- `HEAD`: `5de5f163ba207404f0d260f466eb732a1de27938`
- base aprobada confirmada: `origin/main` en `5de5f163ba207404f0d260f466eb732a1de27938`
- worktrees observados:
  - `/home/impresora/klipper-cnc-assistant` -> `fase-1/auditoria-arquitectura`
  - `/home/impresora/klipper-cnc-assistant-fase2` -> `fase-2/referencias-conectividad`
  - `/home/impresora/klipper-cnc-assistant-viernes` -> `integration/viernes-mas-mejoras`

## Restricciones aplicadas

- No se trabajó en `/home/impresora/klipper-cnc-assistant`.
- No se enviaron G-code, homing, jog, movimientos, probe, spindle, mapas físicos ni trabajos CNC.
- No se llamaron endpoints `POST` que pudieran mover la máquina.
- No se reinició ni modificó el servicio activo, Moonraker, Klipper ni `systemd`.
- No se tocaron `/etc/klipper-cnc-assistant/`, `/etc/systemd/system/`, `/dev/ttyUSB*` ni `data/` real.

## Preparación del entorno

Comandos ejecutados:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
python -m pip check
```

Resultado:

- `python3 -m venv .venv`: correcto
- `python -m pip install --upgrade pip`: correcto, `pip` actualizado a `26.2`
- `python -m pip install -e .`: correcto
- `python -m pip check`: `No broken requirements found.`

## Línea base backend segura

Comando ejecutado:

```bash
MACHINE_MODE=simulated \
MACHINE_AUTO_CONNECT=false \
PYTHONPATH=src \
python -m unittest -v \
  tests.test_api \
  tests.test_gcode_analysis \
  tests.test_heightmap \
  tests.test_job_service \
  tests.test_moonraker_client \
  tests.test_project_service \
  tests.test_web_mvp
```

Resultado:

- `94` pruebas aprobadas
- `0` fallos
- duración aproximada: `43.521s`
- fallos preexistentes: ninguno en esta línea base
- advertencias observadas:
  - `fastapi/testclient.py`: `StarletteDeprecationWarning` sobre `httpx` con `starlette.testclient`
  - `api/app.py`: `DeprecationWarning` por `@app.on_event("startup")` y `@app.on_event("shutdown")`

## Línea base frontend

Comandos ejecutados:

```bash
cd frontend
npm ci
npm run lint
npm run test
npm run build
cd ..
```

Resultado:

- `npm ci`: correcto
- `npm run lint`: correcto
- `npm run test`: `12` archivos, `63` pruebas aprobadas, `27.62s`
- `npm run build`: correcto, `50.12s`

Observaciones frontend:

- `npm ci` reportó `15 vulnerabilities (1 low, 1 moderate, 12 high, 1 critical)`; no se corrigieron en esta etapa porque la línea base no debe mezclar remediaciones fuera del alcance de Fase 2.
- `npm run build` conserva la advertencia no bloqueante de chunks grandes, especialmente `plotly.min`.

## Estado previo a implementación

- La base de Fase 2 parte limpia y validada desde el merge aprobado de `main`.
- No se detectó una regresión inicial entre la rama de Fase 1 fusionada y el worktree de Fase 2.
- La auditoría específica de Referencias, Arduino y Moonraker se documentará en este mismo archivo antes de implementar cambios.


## Auditoría específica de Fase 2

### Referencias

Archivos revisados:

- `src/klipper_cnc_assistant/application/reference_service.py`
- `src/klipper_cnc_assistant/api/routes.py`
- `src/klipper_cnc_assistant/domain/models.py`
- `frontend/src/lib/api.ts`
- `frontend/src/types.ts`
- `frontend/src/features/projects/ProjectWorkspace.tsx`
- `tests/test_api.py`
- `tests/test_heightmap.py`

Hallazgos verificados:

- Las referencias persistidas viven por `MontajePCB.preparacion`, no por operación individual.
- `OperationPreparation` persiste `origen_trabajo`, `referencia_z`, `region_sondeable_configurada_en`, `mapa_disponible_en`, `mapa_validado_en`, `compensacion_previsualizada_en` y `motivo_invalidacion`.
- La asociación de una operación con sus referencias se resuelve a través de `operation.setup_id`.
- `GET /api/projects/{project_id}/operations/{operation_id}/reference-session` es de solo lectura: carga proyecto, consulta estado de máquina y serializa la sesión; no escribe en disco.
- Las operaciones que escriben en disco son:
  - `POST .../reference-session/machine-reference` en simulación
  - `POST .../reference-session/work-origin`
  - `POST .../reference-session/z-reference`
  - `POST .../reference-session/physical-work-origin`
  - `POST .../reference-session/physical-z-reference`
  - `POST .../reference-session/physical-z-reference-from-probe`
  - `mark_map_validated()` y `build_compensation_preview()` también persisten estado derivado
- La política actual de invalidación ya existe en `ReferenceSessionService`:
  - cambiar origen X/Y borra `referencia_z`, invalida `mapa_validado_en` y `compensacion_previsualizada_en`
  - cambiar referencia Z invalida `mapa_validado_en` y `compensacion_previsualizada_en`
  - no se borran artefactos físicos del mapa; solo se marcan bloqueos o invalidaciones
- Al recargar la página, `ProjectWorkspace.tsx` vuelve a cargar `reference-session` y, cuando aplica, el mapa físico y el mapa de altura correspondiente; por tanto se restauran referencias guardadas, fuente, posición capturada, sesión y motivos de invalidación ya persistidos.
- La UI de Referencias sigue mezclada dentro de `ProjectWorkspace.tsx`; la carga inicial y parte del flujo físico están acoplados al workspace general.

Brechas actuales de Fase 2:

- Las capturas físicas de referencia en `api/routes.py` llaman a `runtime.capture_current_position()` o `runtime.last_probe_position()` y persisten inmediatamente; no existe todavía una capa explícita de “observación activa” dentro del servicio de referencias.
- `capture_current_position()` no fuerza una consulta HTTP activa antes de devolver la posición; usa el `MachineState` ya cargado.
- No hay una protección explícita de idempotencia contra doble solicitud física en `ReferenceSessionService`; hoy una doble llamada volvería a sobrescribir la misma referencia con un nuevo timestamp.

### Arduino

Archivos revisados:

- `src/klipper_cnc_assistant/input/serial_driver.py`
- `src/klipper_cnc_assistant/input/command_mapper.py`
- `src/klipper_cnc_assistant/machine/runtime.py`
- `src/klipper_cnc_assistant/api/machine_routes.py`
- `tests/test_machine_runtime.py`
- `tests/test_physical_integration.py`

Comportamiento actual confirmado:

- `SerialDriver` solo implementa transporte: abrir puerto, esperar `startup_delay`, limpiar buffer de entrada, leer paquetes y mantener diagnósticos.
- `MachineRuntime.connect()` crea directamente `SerialDriver`, abre el puerto y lanza un único hilo `_serial_loop`.
- `MachineRuntime` es hoy la autoridad completa del ciclo serial; no existe aún un administrador separado de conexión.
- `CommandMapper` transforma el paquete en intención de alto nivel (`jog_x`, `jog_y`, `probe_request`, `probe_triggered`) sin lógica de reconexión.
- Paquetes parciales: `SerialDriver` incrementa `partial_packets` y continúa leyendo.
- Checksum inválido: `SerialDriver` incrementa `invalid_packets`, `checksum_errors`, registra `last_exception` y continúa leyendo.
- Puerto inexistente o fallo al abrir: el error ocurre en `connect()`, la conexión completa falla y el runtime pasa a `ERROR`.
- Desconexión USB o error de lectura durante `_serial_loop`: el runtime marca `disconnects`, guarda `last_error`, cambia a `DEGRADED` y solo espera `0.25s`; no cierra el handle, no reabre el puerto y no implementa backoff.
- Reconexión automática del mismo dispositivo: no existe hoy.
- Cambio accidental de `/dev/ttyUSB0` a `/dev/ttyUSB1`: no existe identidad USB ni descubrimiento controlado; solo se usa `self.config.serial_port`.
- Paquetes antiguos tras una reconexión: no hay una generación de conexión independiente. Solo `reset_physical_session()` invalida manualmente `last_packet`, `previous_command`, `ready_for_jog` y el estado relacionado.
- No existe endpoint público de reconexión serial manual; los endpoints actuales exponen `connect`, `disconnect`, `diagnostic-mode`, `manual-control`, `probe/*`, `cancel`, `safe-stop` y `emergency`.

Brechas actuales de Fase 2:

- Falta una única autoridad serial con estados explícitos `DISCONNECTED/DISCOVERING/CONNECTING/CONNECTED/DEGRADED/RETRY_WAIT/STOPPED`.
- Falta cierre garantizado del handle defectuoso y reapertura controlada.
- Falta generación de sesión serial y limpieza obligatoria de comandos previos al reconectar.
- Falta identidad USB exacta; hoy el código depende del puerto configurado.

### Moonraker y telemetría

Archivos revisados:

- `src/klipper_cnc_assistant/moonraker/client.py`
- `src/klipper_cnc_assistant/moonraker/telemetry.py`
- `src/klipper_cnc_assistant/machine/state.py`
- `src/klipper_cnc_assistant/machine/runtime.py`
- `tests/test_moonraker_client.py`
- `tests/test_machine_runtime.py`
- `tests/test_physical_integration.py`

Hallazgos verificados:

- `MachineState` ya distingue varias posiciones y edades:
  - `live_position` y `live_position_age_s`
  - `commanded_position` y `commanded_position_age_s`
  - `gcode_position` / `gcode_move_position` y `gcode_position_age_s`
- `MoonrakerTelemetry` actualiza `MachineState` desde WebSocket con `motion_report`, `toolhead` y `gcode_move`.
- `MachineRuntime` mantiene por separado:
  - `self._client` para HTTP
  - `self._telemetry_thread` para WebSocket
  - `self._telemetry_state`
  - `self._last_websocket_message_at`
  - `self._last_telemetry_at`
- `_refresh_machine()` ya hace observación HTTP activa mediante `self._discovery(self._client)` y luego sincroniza el `MachineState` existente.
- `refresh_observed_state()` expone esa observación HTTP activa sin enviar G-code.
- En el snapshot actual ya aparecen separados parcialmente:
  - `moonraker.http_connected`
  - `moonraker.websocket_connected`
  - `moonraker.telemetry_state`
  - `moonraker.last_websocket_message_age_s`
  - `klipper.position.*_age_s`
- La semántica actual de WebSocket es incorrecta para máquina quieta:
  - `MoonrakerTelemetry.run()` hace `asyncio.wait_for(websocket.recv(), timeout=3.0)`
  - si pasan 3 segundos sin mensaje, trata la suscripción como `STALE`, sale al bucle externo y reconecta
- `_telemetry_status()` mezcla transporte y frescura de posición:
  - si `live_position_age_s` es `None` o supera `telemetry_fresh_timeout_s`, devuelve `STALE`
  - si el último mensaje WebSocket supera ese mismo timeout, también devuelve `STALE`
  - por tanto una máquina quieta puede verse como “stale” aunque el transporte siga vivo
- `capture_current_position()` no hace observación HTTP activa antes de devolver la posición; se apoya en el `MachineState` existente.
- Un fallo WebSocket no destruye explícitamente el cliente HTTP, pero la semántica actual de `STALE` induce reconexiones y puede degradar operaciones que solo necesitan confirmar quietud con HTTP.
- Un fallo Arduino no desconecta Moonraker, pero la seguridad actual mezcla `telemetry_recent` y `serial_recent` dentro del mismo snapshot de seguridad.

Distinción actual observada:

1. Cliente HTTP disponible: `self._client is not None`
2. Transporte WebSocket visible: `self._telemetry_thread is alive`
3. Último mensaje WebSocket: `self._last_websocket_message_at`
4. Última posición viva: `MachineState.live_position_updated_at`
5. Última consulta HTTP activa: hoy se refleja indirectamente en `self._last_telemetry_at`; no existe un campo separado explícito de “última observación HTTP”
6. Estado Klipper: derivado del descubrimiento HTTP y del snapshot de máquina
7. Telemetría suficientemente fresca para operaciones: hoy se reduce a `telemetry_recent` y `serial_recent`, con mezcla parcial entre transporte y posición

Conclusión de auditoría de Fase 2:

- La base funcional existe, pero el ciclo serial y la semántica de telemetría siguen demasiado concentrados en `MachineRuntime`.
- El cambio clave de esta fase es separar autoridad serial, distinguir transporte WebSocket de frescura real de posición y exigir observación HTTP activa antes de persistir referencias físicas.
