# Cierre seguro de aplicación — 2026-10-20

## Investigación y causa demostrada

Se leyó completo el informe de preparación de despliegue y se ejecutó **antes de editar** su prueba original: `/tmp/kca-deploy-lifecycle-check.py`. Construye FastAPI/Uvicorn LifespanOn con datos temporales y sockets prohibidos, adquiere un permiso JOB_EXECUTION sintético, entra en RECOVERY, instala un manager Mock y cierra el lifespan. Resultado anterior: startup PASS, shutdown_failed=true, manager.stop no invocado, bytes sintéticos conservados.

El productor retenido es la **raíz lógica JOB_EXECUTION**, no necesariamente un hilo vivo. `execution/job_service.py:55` incluye SPINDLE_STOP_REQUIRED entre los estados de espera. `_run_worker`, ahora línea 1446, retorna al alcanzar ese estado y su finally conserva la raíz cuando el trabajo no es terminal. Es la semántica correcta durante la espera humana. No se diagnosticó nuevamente producción ni se consultó su runtime.

El callback anterior de `api/app.py` llamaba directamente a `runtime.stop()`. `machine/runtime.py:_stop_locked` (línea 564 después del parche; 537 en la base) rechaza productor/hijos/dispatch/operación antes de señalar y cerrar transportes. No había protocolo de salida de los supervisores desde FastAPI. El fallo reproducido es **orden de cierre y confusión entre productor lógico retenido y actividad local**, no evidencia de deadlock de colas ni de CancelledError ignorado.

No se ha quitado la protección de stop/desconexión ordinaria: sus condiciones permanecen y se añade el rechazo si ya comenzó el cierre definitivo. Tampoco se libera ownership para simular reposo.

## Inventario del lifecycle

- FastAPI conserva sus handlers startup/shutdown (`api/app.py:116`). Startup mantiene reconciliación de mapas y runtime.start; no se modifica su política.
- JOB: hilos daemon registrados en `_threads`; el observador consulta HTTP y espera entre polls. Sus journals/CAS continúan bajo JobRunStore.
- MESH: hilos de workflow y de punto registrados; SimpleQueue de progreso entre punto y worker, no cola de movimientos. El punto usa OperationContext y señales existentes. El finally existente conserva evidencias y pausa una malla interrumpida; no se introduce cancelación remota.
- Runtime: OperationContext con cancel_event, MovementLock y hilo manual. Los callbacks de Arduino no reciben permiso de emisión después del cierre.
- ArduinoConnectionManager: hilo propio, señal stop/wake, cancelación de lectura y join acotado; si conserva el hilo, su stop falla. Se reutiliza sin cambiarlo.
- MoonrakerTelemetry: loop asyncio en hilo propio, stop_waiter y cierre WS; se señala y se comprueba terminación del hilo.
- HTTP: sesión del cliente del runtime, cerrada después de terminar consumidores locales. No se crea otro transporte.
- Uvicorn espera normalmente las solicitudes antes del lifespan shutdown. Se añade además rechazo de nuevas solicitudes y conteo de solicitudes ya admitidas durante el cierre explícito.

## Diseño y orden nuevo

1. `ApplicationShutdown.close` (`application/shutdown.py:56`) activa admisión cerrada y la barrera terminal del coordinator antes de programar cleanup.
2. `PhysicalMachineCoordinator.begin_shutdown` (`machine/physical_ownership.py:50`) impide acquire/delegate/dispatch, nuevas reservas administrativas/escrituras coordinadas y reconciliación. No borra permisos, no rota sesión, no altera cancel_epoch ni tokens. Conserva RECOVERY; no puede reabrirse esa instancia.
3. Se señalan runtime, JOB y MESH. Runtime deshabilita manual y señala el OperationContext existente. JOB sale cooperativamente de sus bucles y despierta su espera; MESH no comienza otro punto. **No se llama a cancel_print ni a cancelación de JobRun.**
4. Se esperan workers/probes, solicitudes admitidas, operación runtime, hilo manual, MovementLock, lifecycle de conexión/desconexión, hijos, dispatch, reservas administrativas y escrituras en curso. El indicador productor de la raíz no equivale a un hilo; la raíz permanece retenida y no autorizante.
5. Solo tras esa evidencia local se cierran Arduino, telemetría/WS y sesión HTTP. Un fallo en una de estas etapas no evita las otras etapas independientes. No se cierra ningún transporte si la fase de productores es desconocida o incompleta.
6. El process lock se cierra únicamente si todos los cierres anteriores terminaron sin error. No se declara estado físico IDLE, no se reconcilia y no se modifica el JobRun para facilitar la salida.

### Plazos, excepciones e idempotencia

Espera de productores: 5 s, presupuesto local nuevo; no cambia los timeouts físicos de frescura ni incrementa timeouts de transportes. Cada etapa de señal/cleanup tiene presupuesto 2 s. Hay un margen de 0.1 s para recoger el resultado del hilo de drenaje. Los callbacks potencialmente bloqueantes se ejecutan en hilos daemon dedicados y se observan sin bloquear el loop asyncio. No se mantienen locks de bookkeeping durante joins/IO.

Si un callback o hilo no termina, el callback **no se mata**: se informa etapa incompleta, se mantiene la barrera y no se suelta el process lock. Las etapas exitosas no se repiten. Las llamadas concurrentes/repetidas reciben el resultado de la misma tarea; un fallo queda explícitamente fallido, incluso si posteriormente termina el callback. No hay retry automático. Cancelar al llamador no cancela el cleanup protegido mediante shield.

Las excepciones se registran con etapa y traceback. Un error de productor no se convierte silenciosamente en estado terminal del JobRun por cerrar la aplicación. Los resultados de dispatch ya comprometidos siguen finalizando por el journal original: no se suprimen finish_start, not_sent/uncertain ni CAS.

## Persistencia y fronteras de seguridad

La nueva orquestación no escribe archivos de dominio. La prueba usa un JobRun real de un repositorio temporal preparado, marca espera y primera operación completada, compara **todos los archivos** antes/después y verifica cero starts/cancels del adaptador fake. El caso original conserva además los bytes del registro sintético.

Una operación que ya estaba en curso puede finalizar sus escrituras/journal legítimos antes del drenaje. No se promete byte-identidad de datos activamente escritos, ni se interrumpe fsync. Un mapa activo conserva su finally existente y puede quedar pausado; esto es diferente del JobRun en espera, que no se reescribe. Ante IO atascado el shutdown falla explícitamente.

MotionContext y su timeout no cambian. Un contexto capturado antes del cierre no puede emitir después de la barrera. Un envío que ya entró al transporte se considera en curso y bloquea su cierre hasta terminar; no se finge que no fue enviado. No se altera PrintIdentity, start_attempt, confirmaciones humanas, compensación ni referencias.

## Archivos modificados

| Archivo | Cambio |
|---|---|
| api/app.py | Orquestador de cierre definitivo, rechazo HTTP durante shutdown y conteo de admisiones |
| application/shutdown.py | Nuevo cierre ordenado, acotado, idempotente y con errores explícitos |
| machine/physical_ownership.py | Barrera terminal de emisiones/admisión; conserva permisos y recuperación; expone conteo de escritores |
| machine/runtime.py | Señal cooperativa y comprobación de productores locales; niega acceso/reconexión tras barrera |
| execution/job_service.py | Señal de cierre, salida de supervisor/observador y registro de interrupciones; preserva run en espera |
| execution/mesh_execution_service.py | Señal de cierre, registro consultable de workers/probes y no comenzar otro punto |
| tests/test_application_shutdown.py | Regresiones exclusivamente con fakes y almacenamiento temporal |
| Este informe | Causa, protocolo, evidencias y límites; sin datos productivos ni G-code |

## Matriz de pruebas nuevas

| Propiedad | Prueba |
|---|---|
| Normal y repetido | test_normal_and_idempotent |
| Reproducción original Uvicorn / raíz retenida | test_original_uvicorn_reproduction_now_passes_without_releasing_owner |
| Productor cooperativo | test_cooperative_worker_finishes_before_transport |
| Productor no responde / timeout | test_unresponsive_worker_fails_closed |
| Señal productor falla, otras señales se ejecutan | test_signal_exception_still_signals_other_producers |
| Transporte falla y cleanup independiente continúa | test_transport_exception_continues_independent_cleanup |
| Transporte atascado, no doble cierre | test_transport_timeout_bounded_no_double_close |
| Dispatch en curso | test_dispatch_in_flight_prevents_transport_close |
| Hijos retenidos | test_children_prevent_transport_close |
| Nuevas operaciones / reconexión / reconciliación | test_new_operations_and_reconcile_blocked |
| Admisión HTTP | test_http_rejected_after_shutdown_begins |
| Solicitud previa aún activa | test_active_request_prevents_transport_cleanup |
| Supervisor real termina en espera | test_real_waiting_job_worker_preserves_current_run |
| JobRunStore y todos los archivos temporales íntegros | test_actual_job_store_and_all_project_files_preserved |
| Observador JOB real cooperativo | test_real_job_observer_exits_without_poll_result_write |
| MovementLock ocupado | test_movement_lock_blocks_cleanup |
| Escritor administrativo activo | test_project_writer_blocks_cleanup |
| WS falla, HTTP se cierra | test_telemetry_failure_does_not_skip_http_cleanup |
| Cancelación del llamador | test_caller_cancellation_does_not_cancel_shutdown |
| Contexto previo ya no autoriza | test_motion_context_captured_before_shutdown_cannot_dispatch |
| Señal OperationContext | test_runtime_operation_gets_cooperative_signal |
| Dos llamadores concurrentes | test_concurrent_shutdown_callers_share_one_cleanup |
| Conexión/desconexión en curso | test_lifecycle_in_progress_blocks_cleanup |
| Stop ordinario no duplica cierre definitivo | test_direct_stop_cannot_double_close_after_application_shutdown |

No se usan sleeps de varios segundos para simular antigüedad; las barreras son Events y los casos de timeout usan presupuestos cortos. Las suites existentes de lifecycle, telemetría, MotionAuthorizer, cancel/CAS y state machine se conservan.

## Comandos de validación

Todos los comandos Python se ejecutan con el intérprete del venv existente, leído sin instalar/modificar dependencias, y:

```bash
MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false \
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. PYTHONWARNINGS=ignore \
python -m unittest tests.test_application_shutdown \
  tests.test_runtime_serial_lifecycle tests.test_connection_manager \
  tests.test_moonraker_telemetry tests.test_physical_ownership \
  tests.test_motion_authorization tests.test_job_cancel_identity \
  tests.test_execution_state_machine -q
```

Suite total: mismas variables, `python -m unittest discover -s tests -q`.
Importación: `from klipper_cnc_assistant.api import create_app` con el mismo entorno.
Smoke: proceso CLI aislado, puerto localhost efímero, datos temporales, sockets salientes prohibidos; GET machine/status, OpenAPI, index y assets. No `/api/health`. Cierre únicamente del proceso hijo simulado creado por la prueba, nunca de servicios.

La reproducción exacta externa se repitió conservando el script original y cambiando únicamente sus dos aserciones de resultado en una copia temporal. Resultado nuevo: `startup=PASS`, `shutdown_failed_with_retained_owner=false`, `transport_cleanup_called=true`, `synthetic_job_unchanged=true`.

Frontend: no hay cambios; se reutilizó para smoke una copia del build aprobado en staging. Suite frontend/build/lint frontend NOT_RUN en esta fase, no presentados como validación nueva.

## Límites y riesgos residuales

- No se valida físicamente ninguna recuperación, cierre serial real o estado de la CNC. Se requiere auditoría externa antes de autorizar despliegue.
- El parche vive en una versión nueva: **no corrige en caliente el callback de shutdown del proceso productivo anterior**. El primer despliegue sigue requiriendo un plan autorizado para cerrar ese proceso; no se recomienda restart ciego ni force-clear.
- El cierre local no detiene una impresión ya iniciada en Klipper ni demuestra reposo remoto. Conserva la incertidumbre; no autoriza otra ejecución ni reutiliza tokens en una sesión nueva.
- El presupuesto acotado empieza al entrar en el protocolo de cierre de aplicación. Uvicorn puede esperar solicitudes en curso antes de invocar el lifespan; este parche no configura su timeout de graceful HTTP ni promete una cota absoluta de terminación del proceso ante un handler arbitrario bloqueado. Esa propiedad global no se declara demostrada y debe revisarse antes de desplegar.
- Python no puede detener con seguridad un hilo arbitrario bloqueado. Ante ese caso el resultado es incompleto, con recursos retenidos; no kill ni timeout ampliado. El comportamiento posterior del supervisor del sistema operativo no está bajo control de este módulo.
- Las garantías cubren los productores registrados, OperationContext, solicitudes y transportes de esta arquitectura. Acceso externo directo no autorizado al cliente o dispositivos queda fuera del contrato.
- Un fallo conserva su resultado durante la vida de la instancia; no se puede volver a usar el mismo runtime después de iniciar shutdown.

## Integridad productiva

Verificación read-only del manifiesto privado: 535/535 archivos de datos íntegros y 810/810 copias del respaldo intactas. La única diferencia histórica global sigue siendo la caché Graphify documentada, cuyo hash coincide con el baseline posterior al incidente. No se ejecutó Graphify. AGENTS.md, .codex/ y graphify-out/ conservan su estado local; main productivo no cambió. No se consultaron endpoints productivos.

## Resultado

Validación final de implementación: `1f696d41f09fc4d9d6b5d20af084895f4c2e407b`.
El commit documental posterior no modifica software. Su SHA exacto se entrega
fuera del documento para evitar una autorreferencia de hash imposible; también
se obtiene con `git rev-parse HEAD` en esta rama.

```text
SHUTDOWN_FIX_REPORT
BASE_HEAD = f897ab70fe08dcf57811757540ca4eb80f45b94b
FINAL_HEAD = HEAD (commit documental final; SHA exacto en la entrega)
IMPLEMENTATION_HEAD = 1f696d41f09fc4d9d6b5d20af084895f4c2e407b
BRANCH = fix/safe-application-shutdown-20261020
WORKTREE = /home/impresora/kca-worktrees/safe-application-shutdown-20261020
GIT_STATUS = CLEAN tras commit documental
ROOT_CAUSE = stop ordinario rechaza raíz JOB_EXECUTION retenida antes del cleanup; faltaba protocolo terminal de productores
ORIGINAL_REPRODUCTION = PASS; mismo escenario Uvicorn, cleanup ejecutado y datos conservados
SHUTDOWN_ORDER_BEFORE = stop -> rechazo por productor -> transportes sin cerrar
SHUTDOWN_ORDER_AFTER = fence -> señales -> drenaje acotado -> transportes independientes -> process lock
PRODUCER_CANCELLATION = Cooperativa local; sin cancelación remota ni cancelación del JobRun
TRANSPORT_CLEANUP = Solo tras drenaje; errores registrados; etapas independientes continúan
TIMEOUT_BEHAVIOR = FAIL CLOSED; espera de productores 5s y etapas 2s; no modifica timeouts físicos
UNRESPONSIVE_TASK_POLICY = Conserva recursos y error; no terminación forzada ni retry automático
FAIL_CLOSED_BEHAVIOR = Emisiones/admisiones/reconexión/reconciliación bloqueadas permanentemente en esta instancia
PERSISTENCE_SAFETY = PASS; JobRun en espera y archivos del proyecto temporal byte a byte; journal de dispatch intacto
IDEMPOTENCY = PASS; repetición y llamadores concurrentes no duplican cleanup
TESTS_FOCUSED = 35/35 PASS (24 nuevos shutdown + 11 lifecycle, versión final)
TESTS_BACKEND = 800/800 PASS (399.061s)
TESTS_FRONTEND = NOT_RUN (sin cambios frontend)
IMPORT = PASS
HTTP_SMOKE = PASS (proceso simulado, datos temporales, red saliente prohibida)
DIFF_CHECK = PASS
PHYSICAL_COMMANDS = NO
PHYSICAL_CONNECTIONS = NO
SERVICES_RESTARTED = NO
DEPLOY_EXECUTED = NO
PRODUCTION_MODIFIED = NO
MAIN_MODIFIED = NO
NEW_BLOCKERS = [] para el fallo reproducido y el protocolo local validado
REMAINING_RISKS = Cierre inicial de la versión productiva anterior requiere plan autorizado; cota global previa al lifespan no demostrada; hilos externos atascados no se fuerzan; validación física pendiente
FINAL_VERDICT = READY_FOR_EXTERNAL_AUDIT
```

Logs de validación locales, no publicados: `/tmp/kca-shutdown-lifecycle-final.log`
(35 pruebas) y `/tmp/kca-shutdown-validated-full.log` (800 pruebas).
La selección ampliada anterior de lifecycle/transportes/ownership/MotionContext/
cancelación/state machine pasó 118/118; la suite final incluye esas regresiones
más las dos últimas pruebas de exclusión de lifecycle. Las pasadas intermedias
789/789 y 798/798 no se presentan como el resultado de la versión final.

Los errores de serial/timeout en logs de tests son casos negativos de transportes
falsos. No representan conexiones ni acciones sobre hardware productivo.

No hay push, merge, deploy, restart ni autorización operativa. El resultado
habilita únicamente revisión externa del cambio y de sus límites declarados.
