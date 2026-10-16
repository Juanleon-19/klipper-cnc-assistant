# Correcciones de auditoría externa — Ejecución

Rama: `fix/execution-state-machine-20261014`.
HEAD auditado: `d4ad881f0e071865f352a62c88e2982aef6840f6`.
Base main preservada: `95f47cb93b28f499369cccda51a179b876f525db`.
Trabajo exclusivamente en el worktree aislado. No se incorporaron archivos ni
cambios del checkout productivo.

## Causa y protocolo corregido

Mover la primera validación a prepare no bastaba: el callback final todavía
recalculaba SHA256 de cinco dependencias, reconstruía reference_context,
consultaba PrintIdentity por HTTP y ejecutaba lectura CAS/persistencia durable de
begin_start. Todo ello ocurría después de require_context, dentro de la ventana
de dos segundos. Un disco o HTTP lento podía agotar esa ventana sin cambio físico.

Antes:

```text
prepare → refresh → require_context
→ hashes/contexto/HTTP/CAS/begin_start → revalidate → send
```

Ahora, tanto para start como para resume:

```text
prepare → verify (segunda validación costosa)
→ refresh → require_context
→ guardias en memoria → revalidate → send
```

Antes de require_context quedan carga de proyecto/mapa/fuente/artifact/metadata,
hashes completos, referencia y confirmación de spindle, segunda lectura de bytes,
reference_context, consultas PrintIdentity, validación CAS y, para start,
begin_start durable seguido de validación de su revisión. No cambian las funciones
begin_start/finish_start ni el contenido/semántica del intento. Un fallo posterior
continúa registrándose como uncertain; nunca se reintenta automáticamente.

Después quedan únicamente guardias de mutación en memoria, comparación de
configuración capturada, operación/instalación/token/archivo remoto materializados,
sesión física y de ownership, permiso, MotionAuthorizer.revalidate y envío.
La implementación de MotionAuthorizer y max_age_s=2.0 permanecen intactos.

## Binding y concurrencia

FileRevisionGuard se registra antes de leer dependencias y se invalida antes y
después de una escritura soportada. Un escritor que ya estaba en curso también
invalida una guardia recién creada. Se cubren proyecto, mapa, source, G-code
generado, metadata y run; los originales ahora se publican mediante atomic_write
y las eliminaciones de mapa/run notifican la misma frontera. Las rutas se
canonicalizan durante preparación/escritura, nunca durante la comprobación final.

La guardia del JobRun se registra después de begin_start y se vincula al CAS
persistido validado. Una escritura posterior, cancelación o reemplazo bloquea con
JobRunConflict. Es conservadora: incluso una escritura observacional del mismo
run puede bloquear ese envío. No se usan mtime ni timestamps inventados. Las
guardias débiles no retienen un historial ilimitado ni conceden autoridad física.

Alcance: son generaciones del proceso y de los escritores del repositorio. El
lock físico existente excluye otros procesos Assistant físicos; no convierte en
seguros editores externos que escriban directamente los datos durante ejecución.
La segunda lectura detecta alteraciones de bytes previas al refresh. No se afirma
atomicidad frente a escritores externos que ignoren las APIs/locks del sistema.

PrintIdentity se consulta antes del refresh y mantiene sus reglas. La identidad
esperada de la operación queda materializada y protegida contra cambios. No se
añade otro HTTP después de require_context: la siguiente solicitud es el envío
autorizado. No se modifica la matemática, JIT, hashes de metadata, tokens, cambio
de herramienta, perfiles, referencia Z ni workflow de malla.

## Pruebas de la frontera final

- Se conserva `test_expensive_checks_precede_fresh_frame_start_and_resume`.
- `test_real_callbacks_have_no_io_after_context_start_and_resume` usa callbacks
  reales de JobService y el dispatch/authorizer reales sobre transporte fake.
  Cada digest avanza tres segundos un reloj fake. Tras require_context, parches
  fallan ante Path.open/read_text/read_bytes, file_digest, json.loads,
  reference_context o consulta de print_status. Ambos envíos terminan y la secuencia
  registrada es refresh → context → send, sin IO intermedio ni sleeps largos.
- `test_post_context_concurrent_changes_block_send` inyecta revisión, cancelación,
  sesión, recovery/ownership o escritura de proyecto después de require_context.
  Comprueba que la mutación ocurrió y que send nunca se alcanza. Incluso reescribir
  idénticos bytes invalida la generación.
- `test_mutation_guard_rejects_writer_already_in_progress` cubre el escritor que
  comenzó antes de registrar la guardia y el rechazo tras finalizar esa escritura.
- Se mantienen las regresiones de identidad equivocada, cancelación dominante,
  no duplicación de start y validación final de MotionAuthorizer.

## Confirmación humana y checks

update-tool-reference exige checkbox con el texto:

> Confirmo que el spindle está completamente detenido y autorizo el movimiento
> al punto de referencia y la medición Z de la herramienta instalada.

La prueba frontend comprueba texto, botón deshabilitado, ausencia de acción sin
confirmación y onAction("update-tool-reference") tras marcar. No cambian los guards
backend. El mensaje de checks inexistentes solo aparece en fase inicial sin
checks; la prueba de OPERATION_PREFLIGHT verifica que no aparece durante actividad.

## Archivos funcionales modificados

- `src/klipper_cnc_assistant/execution/job_service.py`
- `src/klipper_cnc_assistant/storage/safe_persistence.py`
- `src/klipper_cnc_assistant/storage/json_repository.py`
- `src/klipper_cnc_assistant/storage/job_run_store.py` (notificación al eliminar;
  begin_start/finish_start y CAS intactos)
- `frontend/src/features/execution/ExecutionConsole.tsx`
- `frontend/src/features/execution/ExecutionConsole.test.tsx`
- `tests/test_execution_state_machine.py`
- `docs/architecture.md`

## Auditoría final de seguridad

```text
FILE_READ_OR_DIGEST_AFTER_CONTEXT = NO
PREPARATORY_HTTP_AFTER_CONTEXT = NO
REFRESH_CONTEXT_CHEAP_CHECKS_REVALIDATE_SEND = YES
PHYSICAL_SESSION_CHANGE_BLOCKS = YES
RUN_REVISION_CHANGE_BLOCKS = YES
CONCURRENT_CANCELLATION_BLOCKS = YES
OWNERSHIP_CHANGE_BLOCKS = YES
NO_DUPLICATE_START_PRINT = YES
PHYSICAL_COMMANDS = NO
PRODUCTION_MODIFIED = NO
MAIN_MODIFIED = NO
SERVICES_RESTARTED = NO
```

## Validación

Todos los comandos Python usaron `MACHINE_MODE=simulated`,
`MACHINE_AUTO_CONNECT=false`, `PYTHONDONTWRITEBYTECODE=1`, `PYTHONPATH=src:.`
y `PYTHONWARNINGS=ignore`. Se utilizó el intérprete del entorno existente, sin
modificarlo. Browser levanta únicamente servidores temporales del test y los
cierra al terminar; no toca servicios productivos.

```sh
python -m unittest tests.test_execution_state_machine tests.test_job_cancel_identity tests.test_final_workflow_api -q
python -m unittest discover -s tests -q
# En frontend:
npm run lint
npm run test -- --run
npm run build
# En la raíz, con el mismo entorno seguro:
python -m unittest \
  tests.test_execution_state_machine.ExecutionStateMachineTest.test_api_restart_recovery_and_manual_diagnostic_guards \
  tests.test_reference_execution_handoff.RealMeshWorkerHandoffTest.test_api_diagnostic_prepare_explicit_start_and_jit_after_real_mesh \
  tests.test_final_workflow_api -q
python tests/execution_lifecycle_browser.py
git diff --check
```

| Validación | Resultado |
| --- | --- |
| Focalizados backend | 44/44 PASS |
| Caso concurrente con aserción de inyección tras require_context | 1/1 PASS, repetido tras fortalecer la aserción |
| Backend completo | 703/703 PASS, 310.431 s |
| Frontend completo | 169/169 PASS, 18 archivos |
| Lint | PASS |
| Build | PASS |
| API E2E | 6/6 PASS |
| Browser E2E | PASS |
| git diff --check | PASS |

El build conserva su aviso de chunks mayores de 500 kB; no es un error.
No hubo cambios funcionales después de la validación completa.

Commit funcional: `37b3c1c104e689ca48f614b284d1af24301bcbad`.
El commit siguiente agrega únicamente este reporte y su enlace. FINAL_HEAD es
ese commit de documentación y se entrega con su hash exacto al cerrar la tarea.
El estado esperado tras commit es limpio (`git status --short` sin salida).

```text
NEW_BLOCKERS = []
GO_FOR_EXTERNAL_REAUDIT = YES
PRODUCTION_MODIFIED = NO
MAIN_MODIFIED = NO
PHYSICAL_COMMANDS = NO
SERVICES_RESTARTED = NO
```

GO corresponde a una nueva auditoría de software; no autoriza despliegue ni
validación física. El historial previo se conserva, sin merge ni force push.
