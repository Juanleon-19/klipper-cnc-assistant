# Tercera auditoría externa — clasificación de start_attempt

Rama: `fix/execution-state-machine-20261014`.
Base auditada de esta corrección: `d11bc05cac9eef71e2d708af8e33f3d1ffcd41fa`.
Main permanece en `95f47cb93b28f499369cccda51a179b876f525db`.
Trabajo exclusivo en el worktree aislado; sin datos, comandos o servicios físicos.

## Causa exacta y semántica anterior

begin_start persistía dispatching con may_have_been_sent=True antes de observar.
El except exterior terminaba cualquier fallo como uncertain, aunque la excepción
hubiera ocurrido en refresh, require_context o los checks finales, antes de llamar
al transporte. preparation_retry_safe rechazaba cualquier start_attempt, dejando
el fallo conocido pre-send en la misma categoría que un envío ambiguo.

## Nueva semántica

| Estado persistido | may_have_been_sent | Significado |
| --- | --- | --- |
| dispatching | False | Intención durable; aún no se habilitó entrar al transporte |
| sending | True | Marca durable previa al transporte; tras crash es ambigua |
| not_sent | False | Dispatch observó y certificó que no invocó el transporte |
| uncertain | True | Excepción dentro/después del transporte, o adaptador sin testigo verificable |
| sent | True | Callable de transporte terminó sin excepción |

DispatchEvidence es el testigo explícito compartido entre adaptador y caller.
El adaptador registra que observa el dispatch antes de preparar. Marca entrada al
transporte inmediatamente antes de invocar el callable ya materializado, después
de los checks finales y revalidate. El except exterior usa ese testigo sin depender
del tipo o texto de la excepción; conserva la excepción original. Si un adaptador
no implementa el testigo, el fallo permanece uncertain, nunca se presume not_sent.

No se migran ni se reinterpretan intentos históricos uncertain/sent/sending.

## Frontera durable y ventana de crash

```text
prepare/verify costoso
  → begin_start: dispatching/False, durable
  → mark_start_sending: sending/True, CAS + identidad + durable
  → capturar guardia de revisión del run y validar CAS
refresh
  → require_context
  → checks finales en memoria
  → revalidate
  → testigo de entrada al callable (solo memoria)
  → start_print
  → finish_start: sent / uncertain / not_sent
```

El punto exacto donde may_have_been_sent pasa a True es mark_start_sending,
mediante la escritura durable del JobRun bajo lock, CAS y validación de operación,
archivo e intento. Esa escritura debe terminar antes del refresh final; el
transporte no se alcanza si falla. El test fake lee el JSON desde start_print y
comprueba sending/True antes de devolver o lanzar excepción.

Se elige deliberadamente persistir sending **antes** de la observación final,
en lugar de añadir fsync entre require_context y send. Esta ubicación conserva
la arquitectura de frescura aprobada y evita reabrir el hallazgo anterior. No
existe IO de journal en la ventana final del MotionContext ni cambio de timeout.

Un crash después de persistir sending sigue siendo ambiguo, incluso si ocurrió
antes del transporte: al reiniciar no existe el testigo vivo que permita probar
lo contrario. Se bloquea cualquier replay. En cambio, si el proceso está vivo y
refresh/contexto/checks/revalidación fallan antes de entrar al callable, puede
persistir not_sent/False. Si esa escritura final falla, la evidencia durable
anterior queda conservadora; no se habilita retry por una clasificación no guardada.

Una excepción dentro del callable o después de él (incluido cleanup) deja
uncertain/True. El testigo se marca antes de llamar, sin IO entre ambos pasos;
una interrupción en esa frontera mínima se trata conservadoramente como ambigua.

## Recuperación explícita

preparation_retry_safe acepta not_sent únicamente con ID de intento, operación
coincidente, may_have_been_sent exactamente False, Moonraker conectado en standby
e inactive, sin observed_printing ni started_at de operación. Se contemplan
WAITING_FOR_KLIPPER, JOB_ERROR y RECOVERY_REQUIRED además de los estados iniciales
ya admitidos. JOB_CANCELLED no se rehabilita.

Un remote_file persistido por upload no equivale a archivo activo: para not_sent
se exige coincidencia con el intento y observación de filename vacía o compatible.
El retry explícito elimina los punteros al upload y artifact, no los archivos ni
la evidencia, y vuelve a generar JIT/subir desde cero. Conserva start_attempt y,
al crear otro intento, archiva el anterior en start_attempt_history. Los writers
de dominio preservan ese historial. Limpia la presentación del error terminal
solo después de superar las validaciones de la acción explícita.

Referencia, sesión, mapa, spindle, recovery, ownership, CAS y cancelación siguen
siendo barreras independientes. El supervisor mantiene su recuperación física
fail-closed: not_sent no borra recovery ni autoriza por sí mismo un nuevo start.
No hay retry automático. sending, uncertain, sent y dispatching interrumpido no
se ofrecen como preparación reintentable por inferencia.

## Resume

resume_print no tiene un journal equivalente a start_attempt. No se añade uno ni
se cambia su máquina de estados. Mantiene PrintIdentity, contexto fresco,
FileRevisionGuard, sesión, ownership y revalidación final, sin retry automático.
La instrumentación es opcional y solo el start instala DispatchEvidence.

## Tests nuevos y regresiones

- `test_start_refresh_failure_is_not_sent`: journal creado, refresh falla,
  transporte no invocado, not_sent/False y acción explícita disponible si es seguro.
- `test_start_context_and_final_failures_are_not_sent`: fallos de captura de
  contexto y configuración final, sin invocar transporte.
- `test_start_transport_exception_is_uncertain_and_journal_precedes_call`:
  excepción dentro del transporte y después de él; ambas quedan uncertain/True.
- `test_start_success_is_sent_and_journal_precedes_call`: sent/True.
  Tanto este test como el anterior leen current_run dentro del callable y prueban
  la marca durable previa, cubriendo la ventana de crash.
- `test_not_sent_explicit_retry_regenerates_and_preserves_attempt`: recuperación
  desde JOB_ERROR, sin adoptar upload anterior; un JIT nuevo, upload nuevo, un solo
  start y conservación del intento not_sent en el historial.
- `test_retry_rejects_ambiguous_journal_and_non_idle_even_if_not_sent`: bloquea
  intentos ambiguos, máquina activa, no standby, desconexión o archivo distinto.
- `test_sending_journal_transition_rejects_cancelled_revision`: begin_start deja
  dispatching/False; cancelación concurrente impide mark_start_sending sin modificar
  el run cancelado.

Se conservan las pruebas que prohíben IO/HTTP post-context para start/resume,
las de cambios concurrentes tras require_context y las de no duplicación de start.

## Archivos

- `execution/job_service.py`: testigo y clasificación, frontera durable, retry explícito.
- `storage/job_run_store.py`: estados, transición CAS sending e historial.
- `execution/health.py`: elegibilidad de recuperación not_sent.
- `tests/test_execution_state_machine.py`: siete tests nuevos.
- `docs/architecture.md`: contrato actualizado.

Sin cambios en UI, API pública, MotionAuthorizer, FileRevisionGuard, compensación,
mapa, referencia Z, perfiles, PrintIdentity o timeouts.

## Validación final

Entorno de todos los comandos Python:

```sh
MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false \
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. PYTHONWARNINGS=ignore
```

Comandos ejecutados con el intérprete del entorno existente y datos temporales:

```sh
python -m unittest tests.test_execution_state_machine -q
python -m unittest discover -s tests -q
npm --prefix frontend run lint
npm --prefix frontend run test -- --run
npm --prefix frontend run build
python -m unittest \
  tests.test_execution_state_machine.ExecutionStateMachineTest.test_api_restart_recovery_and_manual_diagnostic_guards \
  tests.test_reference_execution_handoff.RealMeshWorkerHandoffTest.test_api_diagnostic_prepare_explicit_start_and_jit_after_real_mesh \
  tests.test_final_workflow_api -q
python tests/execution_lifecycle_browser.py
git diff --check
```

| Validación | Resultado |
| --- | --- |
| Backend focalizado | 28/28 PASS |
| Backend completo | 710/710 PASS, 316.203 s |
| Frontend | 169/169 PASS, 18 archivos |
| Lint | PASS |
| Build | PASS |
| API E2E | 6/6 PASS |
| Browser E2E | PASS |
| git diff --check | PASS |

El build mantiene el aviso de tamaño de chunks; compiló correctamente.
No hubo cambios funcionales posteriores a estas validaciones.

Commit funcional validado: `6dac0d09c7e4956bfa7dab91de53f1b8edc36c35`.
El siguiente commit agrega exclusivamente este reporte y su enlace. Su hash
exacto (FINAL_HEAD), status y log se entregan al cerrar la tarea; el estado tras
commit debe ser limpio, con `git status --short` sin salida.

```text
PRE_SEND_FAILURE_NOT_SENT = PASS
TRANSPORT_FAILURE_UNCERTAIN = PASS
SUCCESS_SENT = PASS
DURABLE_MARKER_BEFORE_TRANSPORT = PASS
NOT_SENT_EXPLICIT_RETRY = PASS
NO_AUTOMATIC_RETRY = YES
NO_POST_CONTEXT_PREPARATORY_IO = YES
START_ATTEMPT_HISTORY_PRESERVED = YES
NEW_BLOCKERS = []
GO_FOR_THIRD_EXTERNAL_AUDIT = YES
MAIN_MODIFIED = NO
PRODUCTION_MODIFIED = NO
PHYSICAL_COMMANDS = NO
SERVICES_RESTARTED = NO
MERGE = NO
DEPLOY = NO
```

GO corresponde únicamente a revisión externa de software. No autoriza ejecución
ni validación física. El historial anterior se conserva sin rebase ni force push.
