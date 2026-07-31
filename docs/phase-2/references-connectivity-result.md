# Fase 2 — resultado de Referencias y conectividad

Fecha de cierre técnico: Thursday, July 30, 2026
Rama: `fase-2/referencias-conectividad`
Base aprobada: `main` en `5de5f163ba207404f0d260f466eb732a1de27938`

## Cambios realizados

### Serial y Arduino

- Se introdujo `src/klipper_cnc_assistant/input/connection_manager.py` como autoridad unica del ciclo serial.
- `MachineRuntime` conserva la coordinacion de alto nivel, pero delega apertura, lectura, cierre y reconexion del `SerialDriver`.
- Cada nueva sesion serial incrementa una generacion y limpia estado riesgoso heredado:
  - ultimo paquete utilizable;
  - ultimo comando;
  - `ready_for_jog`;
  - control manual;
  - estado de sonda ligado a la sesion anterior.
- La reconexion automatica reutiliza solo el puerto configurado o la identidad USB conocida.
- Se añadió `POST /api/machine/reconnect-arduino` como reconexion manual idempotente y sin movimiento.

### Telemetria Moonraker

- `moonraker/telemetry.py` separa transporte WebSocket de frescura de posicion.
- El WebSocket verifica transporte con `ping/pong` y deja de reconectar solo porque la maquina permanezca quieta.
- `MachineRuntime.snapshot()` ahora expone por separado:
  - `http_state`;
  - `websocket_state`;
  - `telemetry_state`;
  - `last_websocket_message_age_s`;
  - `last_position_age_s`;
  - `last_http_observation_age_s`;
  - `last_http_error`;
  - `last_websocket_error`;
  - `reconnects`;
  - `klippy_state`.
- Un fallo WebSocket no destruye el cliente HTTP.
- Un fallo Arduino no fuerza desconexion Moonraker.

### Referencias y seguridad de captura

- La captura fisica de referencias ahora usa observacion activa desde `MachineRuntime.capture_reference_observation()` y `capture_probe_reference_observation()`.
- Antes de persistir una referencia fisica se verifica:
  - observacion HTTP activa;
  - `klippy_state == ready`;
  - homing requerido;
  - posicion finita;
  - frescura suficiente;
  - sesion fisica vigente;
  - ausencia de operacion fisica incompatible.
- `ReferenceSessionService` conserva la persistencia existente y ahora evita reescrituras contradictorias cuando la misma captura medida se repite.
- Los `GET` de referencias permanecen sin efectos de escritura.

### Frontend

- La pestaña `Referencia` se extrajo de `ProjectWorkspace.tsx` a `frontend/src/features/references/ReferenceWorkspace.tsx`.
- La UI muestra estados separados de Moonraker HTTP, WebSocket, Klipper y Arduino.
- La accion `Reconectar Arduino` se expone con bloqueos de seguridad y un mensaje explicito de que reconectar no habilita movimiento.
- No se modificaron deliberadamente las pestañas `Mapa` ni `Ejecucion`.

## Decisiones arquitectonicas

- `MachineState` sigue siendo la unica fuente de verdad para posicion, homing y edades observadas.
- `MoonrakerClient` sigue siendo el unico cliente HTTP.
- `MoonrakerTelemetry` sigue siendo la unica conexion WebSocket.
- `ArduinoConnectionManager` es la unica autoridad serial.
- La seguridad fisica no se delega al frontend; la validacion final queda en backend.
- La persistencia de referencias sigue viviendo en `MontajePCB.preparacion` y no se cambiaron formatos almacenados.

## Validacion ejecutada

### Linea base inicial

- Backend seguro inicial: `94` pruebas aprobadas en `43.521s`.
- Frontend inicial: `63` pruebas aprobadas y `build` correcto.

### Validacion puntual durante implementacion

- `tests.test_connection_manager`: verde.
- `tests.test_api`: verde con nuevas pruebas de reconexion e idempotencia.
- `frontend/src/features/projects/ProjectWorkspace.test.tsx`: verde con nuevos estados y bloqueos de reconexion.

### Validacion final

Comandos ejecutados:

```bash
. .venv/bin/activate
python -m pip check

MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONPATH=src python -m unittest discover -s tests -v

cd frontend
npm run lint
npm run test
npm run build
cd ..

git diff --check
```

Resultados:

- `python -m pip check`: correcto, sin dependencias rotas.
- Backend final: `175` pruebas aprobadas en `62.772s`.
- Frontend final: `12` archivos, `65` pruebas aprobadas en `31.66s`.
- `npm run build`: correcto.
- `git diff --check`: sin errores.

### Smoke test local seguro

Backend temporal levantado en modo simulado y puerto alterno:

```bash
MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONPATH=src python -m klipper_cnc_assistant serve --host 127.0.0.1 --port 8011 --data-dir /tmp/kca-phase2-smoke
```

Verificaciones:

- `GET /api/health` -> `200 OK` con `{"estado":"ok","version":"0.1.0","modo_maquina":"simulado","almacenamiento":"disponible"}`
- `GET /` -> `200 OK`, `text/html`, con `<!doctype html>` e `id="root"`
- `HEAD /` -> `405 Method Not Allowed`; se considera comportamiento HTTP aceptable porque la ruta raiz responde correctamente por `GET`

## Limitaciones y riesgos pendientes

- No se realizaron pruebas fisicas sobre la CNC real.
- La validacion de identidad USB exacta depende de la informacion que exponga el dispositivo real.
- `machine/runtime.py` sigue concentrando demasiado comportamiento y sigue siendo un candidato claro de extraccion futura.
- `api/routes.py` aun contiene demasiadas capacidades en el mismo modulo.
- Persisten advertencias preexistentes de FastAPI/Starlette por `@app.on_event(...)` y `httpx` con `starlette.testclient`.
- `npm ci` sigue reportando vulnerabilidades del arbol frontend ya existentes en la linea base.

## Pruebas fisicas pendientes

- desconexion real del Arduino y reconexion del mismo dispositivo;
- validacion de diagnostico posterior a reconexion;
- captura fisica de origen X/Y y referencia Z con observacion activa real;
- verificacion de identidad USB real cuando el dispositivo exponga `VID`, `PID` y `serial_number`.

## Procedimiento de rollback

1. Revertir los commits de Fase 2 en orden inverso.
2. Volver a ejecutar `python -m pip check`, la suite backend segura y `npm run lint/test/build`.
3. Verificar que el snapshot de runtime vuelve a la semantica previa si se revierte la serie de commits.
4. No aplicar rollback sobre el servicio activo sin una revision separada, porque esta rama trabaja en un worktree aislado.

## Confirmaciones de seguridad

- No se enviaron G-code, homing, jog, probe ni movimientos fisicos.
- No se reinicio ni modifico el servicio activo.
- No se tocaron `/etc/klipper-cnc-assistant/`, `/etc/systemd/system/`, `/dev/ttyUSB*` ni `data/` real.
- Todo el desarrollo y validacion de Fase 2 se hizo en `/home/impresora/klipper-cnc-assistant-fase2`.
