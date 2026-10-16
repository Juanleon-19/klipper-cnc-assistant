# IMPLEMENTATION_REPORT — paquete diferido 1

Fecha de cierre: 2026-10-16. Máquina física apagada; pruebas exclusivamente offline.

## Identidad y alcance

BASE_HEAD = 0ac6fd0c6b067c746fcb5ac819f154a546b43f44
BRANCH = feat/deferred-improvements-20261015
WORKTREE = /home/impresora/kca-worktrees/deferred-improvements-20261015
IMPLEMENTATION_HEAD = dbed5e247452e9dc844772b2535433a3d62ef318
FINAL_HEAD = commit de cierre que contiene este reporte (resolver con `git rev-parse HEAD`)

La implementación y sus regresiones están registradas en commits de backend y UI.
Este reporte y la documentación forman el commit final de cierre. Para auditar,
comparar la base indicada con el HEAD final de la rama publicada, incluyendo
los archivos nuevos enumerados abajo. El SHA final y su coincidencia con origin
se confirman en la entrega de publicación; no se incluye un hash autorreferencial.

## FILES_CHANGED

- `IMPLEMENTATION_REPORT.md`
- `PLAN.md`
- `README.md`
- `docs/architecture.md`
- `docs/deferred-improvements-20261015.md`
- `frontend/src/features/execution/ExecutionConsole.test.tsx`
- `frontend/src/features/projects/ProjectWorkspace.test.tsx`
- `frontend/src/features/projects/ProjectWorkspace.tsx`
- `frontend/src/features/references/ReferenceWorkspace.tsx`
- `frontend/src/test/tool-change-recovery-contract.json`
- `src/klipper_cnc_assistant/execution/health.py`
- `src/klipper_cnc_assistant/execution/job_service.py`
- `src/klipper_cnc_assistant/machine/runtime.py`
- `src/klipper_cnc_assistant/machine/state.py`
- `tests/test_deferred_execution_health.py`
- `tests/test_execution_fresh_position.py`
- `tests/test_job_service.py`
- `tests/test_moonraker_telemetry.py`

## TELEMETRY_CONTRACT = PASS

`safety.telemetry_recent` físico consume la edad de la muestra real de
`live_position`. Toolhead, gcode_move y velocidad sin XYZ no renuevan esa muestra.
XYZ idéntica sí renueva la muestra, sin renovar el reloj de cambio XYZ.
El primer XYZ establece una línea base diagnóstica. Cambiar sesión invalida
los timestamps anteriores. No se cambian timeouts ni MotionAuthorizer.

Se exponen `websocket_connected_recently` (conexión y mensaje WS reciente),
`position_sample_recently`, `position_changed_recently` y edad del cambio.
El cambio XYZ solo aparece en diagnóstico, nunca como autorización.
La excepción simulada preexistente de safety se conserva; simulated no concede
permiso de ejecución física.

La regresión t=100/101/110 comprueba muestras iguales, updates parciales,
cambio real, invalidación de sesión y rechazo de MotionAuthorizer con WS vivo
pero posición vieja. La regresión de inspección usa runtime real y HTTP fake:
los updates parciales ya no evitan la consulta real de posición necesaria.
No crea JobRun, no reconecta runtime ni emite comandos físicos.

## RETRY_TOOL_CHANGE_CONTRACT = PASS

La evaluación preserva retry explícito solo para una transición identificada
como fallida después de operación COMPLETED, con siguiente operación PENDING
de otra herramienta, sesión vigente y sin ejecución remota ambigua.
Conserva cancel. Bloquea worker activo, otra sesión, recovery físico, ownership
incompatible, impresión activa/pausada, archivo ajeno, siguientes operaciones
ya iniciadas y journal dispatching/sending/uncertain o de otra operación.
Un journal sent solo corresponde a la operación previamente completada.

El handler verifica también las acciones actuales del monitor y mantiene los
guards previos. No reintenta automáticamente ni vuelve a imprimir operaciones.
Retry → SPINDLE_STOP_REQUIRED → confirmación humana → TOOL_CHANGE_REQUIRED.

`tool-change-recovery-contract.json` comparte las acciones entre el test de
JobService/live_execution real con transportes fake y el test de ExecutionConsole.
Comprueba presencia de retry, su retirada, checkbox obligatorio de spindle,
acción de herramienta cambiada y ausencia de retry en recovery ambiguo.

## TAB_RESTORATION_CONTRACT = PASS

La regresión falló antes del fix. El efecto depende de identificadores estables
de proyecto, montaje, cara y operación, no de los objetos refrescados.
El usuario navega X→Y; un nuevo objeto del mismo proyecto conserva Y.
Cambiar proyecto o cara permite aplicar initialView. No hay timers correctivos.

## RESET_UI_CONTRACT = PASS

Referencias expone los resets mediante el handler existente del workspace.
No se añaden endpoints, estado de reset paralelo ni semántica backend.
Los guards y window.confirm existentes se conservan. Cancelar no envía request.

El diálogo de proceso explica archivo de ejecución y mapa, invalidación de
origen/Z y dependencias, vuelta a SIN_INICIAR y desconexión del runtime interno.
Aclara que conserva G-code originales, operaciones, artifacts e historial.
Origen/Z explicita la invalidación del mapa y compensaciones dependientes.

## Validación

BACKEND_FOCUSED = 119/119 PASS
BACKEND_FULL = 713/713 PASS (377.165 s)
FRONTEND_FOCUSED = 114/114 PASS
FRONTEND_FULL = 172/172 PASS (18 archivos)
REFERENCE_FINAL_CHECK = 12/12 PASS
LINT = PASS
BUILD = PASS
DIFF_CHECK = PASS

El build inicial detectó Array.at en un test nuevo bajo ES2020; se corrigió
usando indexación, sin cambiar dependencias ni tsconfig. El build final conserva
el aviso de chunks mayores de 500 kB; optimización de bundles fuera de alcance.
Se corrigió además la ubicación HTML del texto diagnóstico y se repitieron los
12 tests de Referencias y lint después de ese ajuste.

Comandos backend con MACHINE_MODE=simulated, MACHINE_AUTO_CONNECT=false,
PYTHONDONTWRITEBYTECODE=1, PYTHONPATH=src:., PYTHONWARNINGS=ignore:

- python -m unittest tests.test_moonraker_telemetry tests.test_motion_authorization tests.test_execution_fresh_position tests.test_execution_readiness tests.test_deferred_execution_health tests.test_job_service tests.test_execution_state_machine tests.test_job_cancel_identity -q
- python -m unittest discover -s tests -q

Frontend con MACHINE_MODE=simulated y MACHINE_AUTO_CONNECT=false:

- npm run test -- --run --no-cache (focalizados y suite completa)
- npm run lint
- npm run build
- git diff --check (raíz)

API/browser E2E no se exigieron en este paquete ni se levantaron servidores.
La suite backend completa incluye tests API y los contratos de concurrencia,
CAS, cancelación dominante y autorización física con fakes.

## Límites y estado final

SOFTWARE_VALIDATED = YES
PHYSICAL_VALIDATION_PENDING = YES
MAIN_MODIFIED = NO
PRODUCTION_MODIFIED = NO
PHYSICAL_COMMANDS = NO
SERVICES_RESTARTED = NO
DEPLOY = NO
PUBLICATION = push normal autorizado exclusivamente de feat/deferred-improvements-20261015; verificar SHA remoto en la entrega
PR_CREATED = NO
MERGE = NO

Producción permanece en la base, con sus cambios locales preexistentes:
` M AGENTS.md`, `?? .codex/`, `?? graphify-out/`. No se copiaron al worktree.
No se consultó ni reconcilió el runtime productivo ni se alteraron sus datos.

GIT_STATUS = cierre en commits; verificar worktree limpio después del commit documental.
READY_FOR_EXTERNAL_AUDIT = YES

No se implementaron reset individual, ETA nuevo, optimización Legacy, planner,
refactors generales, migraciones, perfiles nuevos ni cambios de compensación,
referencia física, PrintIdentity, start_attempt o MotionAuthorizer.


## Cierre para auditoría externa

Se revisaron git status, diff --check, archivos nuevos y alcance contra la base.
Solo se incluyen archivos de este paquete. No se incluyen AGENTS.md, .codex/,
graphify-out/, data productivo, dependencias ni artefactos de build.
Los resultados de pruebas anteriores corresponden al mismo código; el cierre
solo cambia documentación y registra commits, sin repetir suites innecesarias.
No se crea PR, merge ni deploy. La validación física permanece pendiente.
