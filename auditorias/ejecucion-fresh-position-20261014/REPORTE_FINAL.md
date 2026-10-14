# Ejecución: observación vigente después de reposo

Fecha: 2026-10-14.
Rama: `fix/execution-fresh-position-20261014`.
Base main: `bffd44dc36d9c73b4bff5027bbbd6a2bc4d98189`.
Commit funcional: `b7c516446e93328d5ee93a3fca0cd67afc135117`.
El HEAD final incluye este reporte; consultar `git rev-parse HEAD`.

## Causa y cambio

`JobService._execution_readiness` evaluaba únicamente `runtime.snapshot()`.
La prueba reproduce posición envejecida más allá de `telemetry_fresh_timeout_s`
con WS conectado, Klipper ready, homing xyz y heartbeat Arduino vigente. La
misma evaluación pura anterior devuelve `TELEMETRIA_VIGENTE`; la inspección
corregida consulta HTTP mediante la autoridad existente y deja de bloquear.

Ahora se reutiliza el snapshot fresco. Si está vencido y hay runtime físico con
HTTP disponible, sin recovery, ownership incompatible, operación activa ni
estado desconectado/error, se intenta una observación HTTP. Se evalúa esa
respuesta y se vuelve a consultar ownership. Un fallo conserva el bloqueo con
motivo y acción Moonraker/Klipper; no puede ocultarse por una actualización WS
concurrente. No se llama a connect ni se abre/reconecta serial.

`refresh_observed_state` exige que discovery entregue posición live y velocidad
presentes y finitas, con velocidad no negativa, antes de publicar timestamps.
La comprobación es parte de la autoridad runtime, no una autorización paralela.
El chequeo previo de Klipper ready se conserva. Homing vacío bloquea por homing
sin añadir posición stale cuando la observación sí fue válida.

La inspección no escribe proyectos, mapas, referencias, artifacts ni JobRun,
no adquiere ownership y no emite comandos físicos. Solo cambia el estado
observado/cacheado del runtime a partir de una consulta real o fake en tests.
No cambia ningún timeout ni PhysicalReferenceToken.

Start/resume no se modificaron: `_dispatch` conserva refresh, MotionAuthorizer,
revalidación posterior al último callback y dispatch autorizado. Las pruebas
usan el MotionAuthorizer real y verifican cancelación en ese último callback
sin llegar al envío. La inspección nunca proporciona permiso de movimiento.

## Evidencia

`tests/test_execution_fresh_position.py`: 11/11 PASS.

- Snapshot fresco: ningún refresh.
- Reposo simulado: bloqueo anterior reproducido; consulta HTTP fake usando
  discovery y MachineState reales; siguiente inspección no repite HTTP.
- Error HTTP: bloqueo y acción explícita.
- Klipper shutdown: bloqueo por Klipper.
- Homing vacío: bloqueo por homing sin falso bloqueo de posición.
- NaN, infinito, posición ausente/vacía y velocidad ausente/no finita/negativa:
  no publican timestamp HTTP ni frescura nuevos.
- Recovery previo y ownership ajeno: bloqueo sin refresh.
- Recovery aparecido durante HTTP: bloqueo al evaluar.
- HTTP desconectado: sin reconnect.
- Inspecciones con/sin run, incluido endpoint API: árboles de archivos iguales,
  ownership igual y ningún run nuevo.
- Start/resume: observación previa y autorización final independientes;
  cancelación entre autorización y envío impide el envío.

Los fakes sustituyen exclusivamente fronteras físicas. No se consultó Moonraker
productivo ni se abrió hardware. Los mensajes de fallos seriales en la suite
completa corresponden a negativos sintéticos.

## Validación

Entorno backend: `MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. PYTHONWARNINGS=ignore`, usando el
intérprete virtual existente y código del worktree aislado.

- Focalizados nuevos: `python -m unittest tests.test_execution_fresh_position -q`:
  11/11 PASS, 7.077 s.
- Backend: `python -m unittest discover -s tests -q`: 682/682 PASS, 294.987 s.
  Incluye cancel/CAS/PrintIdentity/MotionAuthorizer y regresiones existentes.
- Frontend: `npm test`: 165/165 PASS, 18 archivos, 58.41 s.
- `npm run lint`: PASS.
- `npm run build`: PASS; advertencia existente de chunks mayores de 500 kB.
- API E2E: 6/6 PASS, 13.613 s, comando:

```sh
python -m unittest \
  tests.test_execution_fresh_position.ExecutionFreshPositionTest.test_api_diagnostic_refreshes_idle_without_persisting \
  tests.test_reference_execution_handoff.RealMeshWorkerHandoffTest.test_api_diagnostic_prepare_explicit_start_and_jit_after_real_mesh \
  tests.test_final_workflow_api -q
```

- Browser E2E: N/A; no cambios en UI. Se ejecutó además todo frontend.
- `git diff --check`: PASS.

## Resultado solicitado

```text
IDLE_STALE_EXECUTION_REPRODUCED = YES
ROOT_CAUSE_CONFIRMED = YES
READINESS_ACTIVE_REFRESH = YES
CACHE_STALE_NO_LONGER_FALSE_BLOCKS = YES
REFRESH_FAILURE_BLOCKS = YES
HOMING_STILL_REQUIRED = YES
KLIPPER_READY_STILL_REQUIRED = YES
RECOVERY_FAIL_CLOSED = YES
OWNERSHIP_FAIL_CLOSED = YES
INSPECTION_WRITES_JOBRUN = NO
INSPECTION_ACQUIRES_OWNERSHIP = NO
PHYSICAL_COMMANDS = NO
START_FRESH_REVALIDATION = PASS
RESUME_FRESH_REVALIDATION = PASS
BACKEND_TESTS = 682/682 PASS
FRONTEND_TESTS = 165/165 PASS
LINT = PASS
BUILD = PASS
API_E2E = 6/6 PASS
BROWSER_E2E = N/A (sin cambios UI)
GIT_DIFF_CHECK = PASS
NEW_BLOCKERS = []
PRODUCTION_MODIFIED = NO
MAIN_MODIFIED = NO
SERVICES_RESTARTED = NO
PUSH = NO
READY_FOR_EXTERNAL_AUDIT = YES
```

Alcance: software validado con fakes, sin afirmar validación física. No se
incorporaron ramas pendientes ni trabajo histórico. No deploy, merge ni push.
