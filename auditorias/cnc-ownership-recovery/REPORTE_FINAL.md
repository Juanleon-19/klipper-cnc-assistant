# Auditoría de ownership y recuperación CNC — 2026-10-19

## Decisión

**NO-GO operativo.** No existe autorización ni evidencia para desbloquear la máquina actual. El shutdown y la pérdida serial posterior explican el bloqueo legítimo; no se debe convertir en IDLE. Se corrigió una debilidad independiente de archivado, que habría permitido quitar el JobRun actual sin resolver el ownership. La corrección bloquea esa salida por archive-stale, no libera la máquina. La revisión de publicación detectó un bypass separado en reset-preparation, detallado al final; no se declara cobertura integral de todas las rutas.

Decisión entre las opciones solicitadas: **D ahora; B condicionada posteriormente**. Conservar proyecto, originales, operaciones e historial; mapa como evidencia histórica, no como geometría físicamente vigente demostrada. Tras intervención autorizada puede prepararse otra ejecución con sólo las cuatro operaciones pendientes. Si no se puede demostrar la correspondencia XY/montaje después de recuperar homing, aplicar C: preparación y mapa nuevos conservando el historial. No se recomienda A con las evidencias actuales.

## Evidencia y causas

Se leyó completo el informe predeploy antes de decidir. Se contrastaron archivos del JobRun y logs, sin invocar endpoints de ejecución que pudieran reconciliar/persistir. Consultas actuales: GET machine/status, GET Moonraker objects/query; systemctl show y journalctl de sólo lectura.

Cronología en America/Bogota:

| Momento | Evidencia | Conclusión |
|---|---|---|
| 18 octubre 13:17:12 | Evento COMPLETED de operación 1; después SPINDLE_STOP_REQUIRED | Primera operación finalizada según aplicación y remoto |
| 18 octubre 13:18–13:19 | Tres POST job-run/action con 400 | Confirmación no avanzó; no cambio de herramienta acreditado |
| 18 octubre 14:22:15 | Kernel: USB desconectado, adaptador ch341 retirado | Pérdida de enlace del MCU |
| 18 octubre 14:22:19 | Klippy EOF/Timeout MCU y Moonraker shutdown | `Lost communication with MCU 'mcu'` |
| 19 octubre 07:11:35 | Kernel: USB del pl2303 desconectado; Assistant READ_HANGUP y PORT_ABSENT | Pérdida posterior de la sesión Arduino; causa de RECOVERY del owner |

Shutdown ocurrió unos 65 minutos después de la finalización, no durante el mecanizado registrado. En el evento causal no aparece una causa térmica/endstop/motor que sustituya la pérdida de comunicación. Las menciones de temperatura en macros/configuración no son prueba de una avería térmica. No hay evidencia suficiente para distinguir apagado, cable, alimentación o avería USB/firmware subyacente. Tampoco el texto genérico de pyserial demuestra acceso concurrente: el kernel sí confirma desconexión.

Lectura actual: firmware shutdown, homed_axes vacío, print_stats complete, virtual_sdcard false. El archivo remoto coincide exactamente con el de la operación 1. JobRun SPINDLE_STOP_REQUIRED, revisión 11, índice 0; estados COMPLETED/PENDING/PENDING/PENDING/PENDING. Journal start_attempt sent con may_have_been_sent=true. Los PID de los tres servicios permanecieron estables, sin reinicios durante auditoría. No se divulgaron nombres de archivos de mecanizado, coordenadas ni tokens.

## PhysicalOwnership: qué significa realmente

`machine/physical_ownership.py` mantiene un coordinador **en memoria**, no un lock persistido con un PID desaparecido. El owner lógico corresponde al proyecto/setup/cara del JobRun actual; sesión aleatoria y revisión CAS registrada en la evidencia privada. La identidad del productor es el permit raíz conservado por `JobService._physical_leases`, no un thread-id. `producer_active` se activa al acquire y se desactiva al retire/release; no consulta is_alive.

`JobService._run_worker` sale en estados de espera y elimina su registro de thread, pero conserva deliberadamente el lease del workflow. Por eso SPINDLE_STOP_REQUIRED puede tener producer_active=true sin ejecutar un worker ni emitir movimientos. La sesión del proceso no se ha reiniciado. No se adjuntó un debugger al proceso; la ausencia exacta de thread registrado se infiere del código y estado observado, no de una inspección invasiva de memoria. No hay evidencia de un hilo bloqueado en IO.

`MachineRuntime._on_serial_session_lost` valida epoch/generation del manager, invalida controles manuales/muestras y llama `physical_ownership.request_cancel()` si hay owner. Esa función pone cancel_requested=true, recovery=true y razón "Cancelación pendiente de quiescencia", sin retirar el productor. Esto coincide con la lectura actual y el evento serial. No equivale a cancelar el JobRun ni cambia su journal.

Estado observado: RECOVERY, producer_active=true, children=0, dispatching=0, active_operation=None. Los contadores nulos no autorizan a mover ni demuestran por sí solos quiescencia remota. MovementLock protege movimientos locales; los permits hijos y dispatching cubren actividad delegada. CAS de ownership rechaza revisión/sesión obsoleta; CAS del JobRun protege run_id/revision/cancellation_epoch y no puede sustituirse por antigüedad.

**No se demostró deadlock circular de locks.** Existe una espera lógica intencional: reconciliar requiere productor retirado; continuar el workflow exige permit válido y no cancelado. La salida es disponer explícitamente del workflow, no limpiar el coordinador. `_cancel_run` primero persiste cancelación mediante CAS, contrasta PrintIdentity para cualquier cancel remoto, conserva evidencia/historial y retira el productor si no hay supervisor vivo. Si lo hay, el worker debe finalizar; no se fuerza. Es una acción mutante potencialmente remota que NO fue ejecutada.

`recover_idle_controls` no retira productores: necesita cierre previo y una consulta HTTP válida con firmware ready, estado remoto inactivo y velocidad finita dentro de tolerancia. Conserva recovery ante falta de evidencia. La pérdida de homing bloquea movimiento; reconciliar reposo no equivale a autorizar homing ni mecanizado. `runtime.stop` rechaza productor/operación/hijos/dispatch activos; no se debe forzar shutdown para saltarlo.

## Validez y recuperabilidad

La finalización de operación 1 sigue siendo evidencia de progreso y debe conservarse. No demuestra calidad dimensional ni autoriza repetirla. Originales, hashes de origen, identidad de archivo remoto y journal siguen siendo evidencia histórica útil. El mapa y origen guardados no se han borrado.

Las referencias **no son utilizables para autorizar movimiento**: shutdown, ausencia de homing, serial perdido y recovery son bloqueos independientes. No se afirma que los bytes del token sean corruptos ni que el session-id cambie necesariamente al instante de desconexión: la generación aumenta al establecer una sesión nueva. Un restart cambia la identidad de proceso; reconexión serial y reconciliación de ownership también invalidan dependencias según su contrato. No migrar ni reescribir tokens para hacerlos coincidir.

`preparation_retry_safe` rechaza este run con operación observada/imprimida y start_attempt sent. No se puede convertir en not_sent ni reintentar start. `tool_change_retry_safe` requiere una transición fallida específica y sesión válida; una pérdida serial no basta. Después de reiniciar, la clasificación exige recuperación de sesión y no ofrece reintento de preparación para este caso.

Preparar un nuevo run con las cinco operaciones activas podría repetir los primeros agujeros. La selección autorizada futura debe excluir la operación COMPLETED expresamente, y comprobar que las cuatro restantes son las previstas. No editar índices, estados o journals a mano.

## Defecto reproducido y corrección mínima

En la base aprobada, `_moonraker_is_idle` interpretaba cualquier estado distinto de printing/paused como idle; también un fallo HTTP, shutdown o un campo ausente. `_diagnose_run` convertía ausencia de is_active en False. Además, `describe_run_conflict` y `archive_stale_run` no verificaban el owner físico retenido. Un run antiguo sin thread podía ofrecer archivado aunque conservara su productor, dejando una dependencia huérfana y anunciando posibilidad de un nuevo trabajo.

Tests escritos primero: el caso owner RECOVERY falló porque can_archive_stale era True; cinco variantes de evidencia remota insuficiente también fueron aceptadas indebidamente. El test del worker en espera pasó y demuestra que retener el lease es la semántica deliberada, no un permiso para borrarlo.

Cambio en `execution/job_service.py`:

- Mantener el campo observado de virtual_sdcard separado del booleano de compatibilidad del adaptador; conservar None en diagnóstico si falta evidencia.
- Exigir connected=True, Klipper ready, estado remoto explícitamente inactivo e is_active=False para clasificar archivado remoto.
- Una evaluación común de ownership exige ausencia de owner/recovery/productor/hijos/dispatch/operación local y de MovementLock ocupado.
- Usar ese gate tanto en acciones ofrecidas como antes de archivar. Rechazo accionable sin escritura ni retirada de permits.
- CAS, cancel dominance, journal, MotionContext y reconciliación no se modificaron. El gate no es autorización de movimiento ni sustituye esos guards. No añade un mecanismo automático de recuperación ni cambia la semántica física.

Archivo de pruebas `tests/test_job_service.py`: seis regresiones nuevas cubren owner retenido, datos remotos desconocidos/shutdown, worker de espera, fallo HTTP con bytes del run intactos, productor retirado todavía en recovery y campo virtual_sdcard ausente a través del adaptador real con cliente fake. Conservan cero start/cancel en los rechazos. La suite existente cubre productor real, hijos/dispatch, cambio de sesión, conflictos CAS, cancel concurrente, restart, referencias y no duplicación de start.

Limitación deliberada: el nuevo gate protege archive-stale, **no resuelve la causa física ni cierra el workflow en producción**. No se añade una vía sin evidencia para archivar después de un shutdown.

## Matriz de decisiones

| Condición | Acción permitida | Acción prohibida |
|---|---|---|
| Klipper shutdown | Diagnóstico read-only | Movimiento |
| Sin homing | Verificar causa y planificar homing autorizado | Reanudar |
| Ownership RECOVERY | Diagnosticar productor y mecanismo legítimo | Force-clear |
| JobRun esperando spindle | Evaluar recuperabilidad | Confirmar automáticamente |
| Nueva sesión física | Revalidar dependencias | Reutilizar tokens sin comprobar |
| Worker ausente, lease retenido | Disponer del workflow con autorización específica | Inferir idle por tiempo |
| Operación 1 completada | Preservar journal y excluir de nuevo plan | Repetir start para comprobar |
| HTTP fallido/campo ausente | Bloquear archivado y conservar run | Tratar ausencia como false/cero |

## Procedimiento futuro, NO ejecutado

Las etapas siguientes requieren autorizaciones separadas. Los ejemplos POST son especificaciones de intervención futura, no comandos ejecutados. No existe un único script seguro que pueda ejecutarse ahora sin decisiones del operador.

### 1. Diagnóstico

Prerrequisitos: ninguno físico; acceso sólo lectura. KCA_API_URL y KCA_MOONRAKER_URL deben establecerse con los endpoints locales verificados; no se publican rutas del host. Comandos:

```bash
systemctl show klipper-cnc-assistant.service -p MainPID -p ActiveState -p SubState -p NRestarts
journalctl -u klipper-cnc-assistant.service --since '-15 min' --no-pager
curl -fsS ${KCA_API_URL}/api/machine/status
curl -fsS "${KCA_MOONRAKER_URL}/printer/objects/query?webhooks&toolhead&motion_report&print_stats&virtual_sdcard"
```

Esperado: identidad remota y JobRun coincidentes, ausencia de impresión activa; no inferir seguridad de una velocidad residual. Sin efectos sobre referencias/run. Abortar cualquier plan mutante si cambia identidad, hay impresión/actividad, faltan campos o falla HTTP.

### 2. Resolver shutdown

Operador debe identificar alimentación/cableado/conectores de ambos dispositivos, inmovilización y herramienta presente, spindle físicamente detenido por sus medios autorizados. No conectar automáticamente: podría establecer nueva sesión y alterar referencias. Riesgo: movimiento/energización inesperada, pérdida de posición y cambio de sesión.

**No hay un comando de reset justificable todavía**: los logs no prueban una causa física única. No se prescribe FIRMWARE_RESTART como cura del fallo. Tras inspección se debe autorizar un procedimiento concreto de recuperación firmware/conexión. Evidencia necesaria: firmware ready estable, sensores coherentes y enlace estable; abortar ante nueva desconexión, error o estado ambiguo.

### 3. Restablecer estado seguro

Primero observaciones read-only de ready, sensores, posición/velocidad y estado remoto. Homing y nueva referencia son acciones físicas separadas. El owner actual impide home: no intentar sortearlo; completar etapa 4 antes de homing. Después de ownership resuelto y autorización física, la versión aprobada ofrece `POST /api/machine/home` (sólo homing); producción anterior puede tener initialize combinado y NO debe utilizarse como sustituto. Posponer homing hasta despliegue auditado si esa separación no está instalada.

Cada nueva sesión/homing exige revalidar origen y montaje. No usar el mapa como permiso físico. Abortar si dirección/clearance/endstops no están confirmados o si cualquier guard rechaza. No hay coordenadas propuestas ni alturas inventadas.

### 4. Disponer del workflow y resolver ownership

No existe liberación administrativa silenciosa validada. La ruta existente es **cancelación explícita del JobRun**, potencialmente remota, que el usuario debe autorizar por separado si decide abandonar la ejecución conservando historial. No usar `/api/machine/cancel` como sustituto: puede dejar el JobRun esperando.

Prerrequisitos: copia actual verificada; identidad exacta run/proyecto/setup; elección expresa de no continuar este run; ausencia de supervisor activo no debe inferirse sólo del tiempo; inspección/estado remoto y riesgos de cancelación revisados. Ejemplo parametrizado, con variables verificadas contra el JSON real sólo después de autorizar:

```bash
curl -fsS -H 'Content-Type: application/json' \
  --data "{\"setup_id\":\"$KCA_SETUP_ID\",\"face\":\"$KCA_FACE\",\"action\":\"cancel\"}" \
  "${KCA_API_URL}/api/projects/$KCA_PROJECT_ID/job-run/action"
```

Puede invocar cancel_print: no es read-only ni permitido ahora. Efectos: JobRun cancelado y archivado con resultados de identidad/transporte, operaciones completadas conservadas; lease retirado sólo cuando no exista productor ejecutándose. Ante conflicto CAS, identidad distinta, error remoto o producer_active persistente: detenerse, conservar recovery, no repetir a ciegas ni forzar.

Después, sólo con firmware ready y evidencia remota inactiva estable, solicitar explícitamente:

```bash
curl -fsS -X POST ${KCA_API_URL}/api/machine/recover-idle-controls
```

Este mecanismo realiza consulta de reposo y CAS del coordinador; no libera productor activo ni envía movimiento. Efecto: cambia epoch de ownership y deja modo diagnóstico, no autorización de mecanizado. Exigir owner sin productor/hijos/dispatch, recovery despejado y nueva evidencia; si rechaza, abortar. No quitar archivos de lock ni llamar retire manualmente.

### 5. Preservar proyecto y preparar las pendientes

Con autorización mutante posterior, usar historial de cancelación/archivo y los controles soportados para preparar operaciones 2–5 exclusivamente. No ejecutar archive-stale para resolver el owner retenido; la corrección lo prohíbe. Una cancelación ya conserva su archivo histórico.

Mapa/origen: verificar montaje y correspondencia XY después de recuperar homing; repetir preparación/mapa si no hay certeza. Referencia de herramienta nueva y confirmaciones humanas necesarias. No ejecutar start, probe ni nueva captura como parte del despliegue. Abortar si el plan incluye operación 1, usa referencia histórica o cambia identidad del montaje sin una nueva validación.

### 6. Despliegue separado

Sólo después de auditoría externa del cambio local, disposición autorizada del workflow y evidencia de cierre seguro. Repetir predeploy: estado/hash/HEAD aprobados y build. Comando de restart futuro exclusivo, NO ejecutado:

```bash
sudo systemctl restart klipper-cnc-assistant.service
```

No ejecutar con producer_active. El restart cambia sesión: verificar que no se inicia worker y que referencias antiguas siguen bloqueadas. No reiniciar Klipper/Moonraker como parte del deploy. Si no puede demostrarse un cierre limpio, NO-GO; no usar timeout/kill para resolverlo. El reporte predeploy contiene publicación/rollback condicionados y no constituye autorización vigente.

## Integridad y alcance

El respaldo original permanece intacto. Los datos productivos, mapas, referencias, journals y current_run conservan sus hashes. La comprobación global de 810 archivos detectó **una excepción de metadatos**: `graphify-out/cache/last_query_stamp` cambió por el efecto lateral de `graphify query`, herramienta exigida por AGENTS. No se restauró ni sobrescribió para ocultarlo. Por ello no puede afirmarse igualdad global 810/810 ni "producción absolutamente sin cambios"; sí ausencia de cambios en código/configuración/datos operativos. La actualización de graphify posterior se ejecutó sólo en el worktree nuevo y sus archivos no se incorporan al commit.

No se adjuntaron G-code real, datos de proyecto, referencias ni logs privados a Git. Los resultados operativos crudos quedaron en directorio privado fuera del repositorio. No hubo llamadas mutantes, conexión manual, cancelación real, restart ni deploy.

## Validación y reproducibilidad

Entorno de pruebas: `MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. PYTHONWARNINGS=ignore`, Python del venv existente ejecutado desde el worktree aislado. Ningún test usa data productivo ni Moonraker real. Los errores serial sintéticos esperados de las pruebas negativas no son fallos del host.

- Reproducción inicial: 3 tests; 2 métodos fallaban (6 assertions/subcasos), demostrando el fallo previo. La retención del lease en espera se confirmó sin error.
- Regresiones nuevas: 6/6 PASS en ejecuciones focalizadas.
- Conjunto focalizado JobService/PhysicalOwnership/cancel identity/execution state machine/MotionContext: 106/106 PASS.
- API E2E simulado: 4/4 PASS (14.004 s).
- Frontend, lint y build: NOT_RUN en esta fase; no se cambiaron fuentes frontend, dependencias ni distribución. La auditoría anterior había validado 185/185 y lint/build, sin presentarlo como una ejecución nueva.
- Diff check ejecutado sobre los cambios finales.

La primera selección focalizada detectó sólo una incompatibilidad del prefijo de un mensaje esperado por un test existente; se conservó ese prefijo con la explicación nueva. Se reiniciaron exclusivamente procesos de tests simulados propios para validar la última revisión. Ningún servicio fue detenido ni reiniciado.

Integridad: 535/535 archivos de data y 810/810 copias del respaldo intactos. Comparación de todos los originales: 809/810 intactos; única diferencia last_query_stamp de Graphify ya declarada. HEAD productivo sigue b0dbcdc691a145b4b086dcd9cc2dcf1795e10880; estado Git conserva AGENTS.md modificado y .codex/graphify-out sin seguimiento. No se escribió configuración ni código productivo.

## Resultado estructurado

```text
OWNERSHIP_RECOVERY_AUDIT
BASE_HEAD = e6b3725a1f46ec6e89f706d02f1cc0d40bdb7184
IMPLEMENTATION_COMMIT = 008f1d93f7fa58c3e9a4eb8082ee82ac9de22409
FINAL_HEAD = commit documental que contiene este informe; SHA exacto en entrega de publicación
BRANCH = fix/cnc-ownership-recovery-20261019
WORKTREE = cnc-ownership-recovery-20261019 (aislado; ruta local omitida)
KLIPPER_SHUTDOWN_CAUSE = pérdida de comunicación MCU tras desconexión USB; causa eléctrica/mecánica no determinada
HOMING_STATUS = INVALID; homed_axes vacío
OWNERSHIP_PRODUCER = lease lógico del JobRun retenido durante SPINDLE_STOP_REQUIRED; no equivale a thread vivo
OWNERSHIP_RECOVERY_CAUSE = pérdida de sesión serial Arduino 2026-10-19 07:11:35; request_cancel del coordinador
DEADLOCK_DETECTED = NO mutex circular demostrado; espera lógica de cierre explícito
SOFTWARE_DEFECT_CONFIRMED = YES; archivado aceptaba evidencia remota desconocida y owner retenido
SOFTWARE_FIX_APPLIED = YES; gates fail-closed de archivado, sólo en worktree
JOBRUN_RECOVERABLE = NO directamente en condiciones actuales; D ahora, B/C condicionadas después
FIRST_OPERATION_PRESERVED = YES
REFERENCE_TOKENS_VALID = NO para autorización física actual; no alterados
RESTART_EFFECTS = nueva sesión invalida evidencias anteriores; cierre actual bloqueado por productor
BACKEND_TESTS = PASS 745/745 (353.654 s); focalizados 106/106 PASS
API_E2E = PASS 4/4
FRONTEND_TESTS = NOT_RUN; sin cambios frontend
DIFF_CHECK = PASS
PRODUCTIVE_HASHES_UNCHANGED = YES data 535/535; NO inventario global 809/810 por caché Graphify
PRODUCTION_MODIFIED = YES únicamente graphify-out/cache/last_query_stamp por consulta; NO código/configuración/data
PHYSICAL_COMMANDS = NO
SERVICES_RESTARTED = NO
DEPLOY = NO
MAIN_MODIFIED = NO
RECOVERY_PROCEDURE = seis etapas documentadas; ninguna acción mutante ejecutada
PHYSICAL_AUTHORIZATIONS_REQUIRED = inspección/energización/conexión/recuperación firmware/homing/nueva referencia; cancelación JobRun y deploy por separado
REMAINING_BLOCKERS = bypass de reset-preparation demostrado durante cierre; causa física subyacente no resuelta; shutdown; serial ausente; owner cancelado no retirado; homing y referencias no utilizables
VERDICT = NO-GO operativo; corrección software pendiente de auditoría externa, sin desbloqueo productivo
```

No se puede emitir literalmente `PRODUCTION_MODIFIED = NO` para todo el árbol después del efecto lateral documentado. Se conserva la distinción entre el único metadato de herramienta modificado y los datos/código/configuración productivos intactos. No se maquilló la comprobación de integridad.


## Cierre para publicación y auditoría externa

Implementación inalterada: `008f1d93f7fa58c3e9a4eb8082ee82ac9de22409`, cuyo padre único es exactamente el main aprobado `e6b3725a1f46ec6e89f706d02f1cc0d40bdb7184`. El main remoto se comprobó mediante ls-remote y coincidía. Esta fase añade exclusivamente el informe mediante commit documental separado; no cambia código ni tests. El HEAD publicado es el commit documental, que debe resolver igual localmente y en origin.

### Rutas alternativas: hallazgo abierto

**La verificación de que ninguna ruta alternativa archiva con evidencia insuficiente NO PASA.** No se oculta este resultado para publicar la rama.

| Ruta | Resultado de revisión |
|---|---|
| POST job-run/archive-stale | Usa guards nuevos de owner y evidencia remota explícita |
| describe_run_conflict / available_recovery_actions | Usan los mismos guards para ofrecer archive-stale |
| POST reset-preparation → reset_runs_for_preparation | Bypass pendiente: usa _assert_preparation_reset_safe, que no consulta directamente PhysicalMachineCoordinator y acepta ciertos estados remotos incompletos |
| _archive_run desde finalización/cancelación | Copia histórica CAS; conserva current_run y no es equivalente a archivado administrativo que elimina current_run. Puede registrar evidencia antes del retiro del owner, deliberadamente |

Reproducción aislada con JobServiceTest, repositorio temporal y adaptador fake (sin código nuevo en la rama):

1. SPINDLE_STOP_REQUIRED + lease JOB_EXECUTION retenido + remoto complete/inactive: reset_runs_for_preparation elimina current_run y deja el owner igual.
2. Firmware shutdown + remoto complete/inactive + velocidad cero observada por fake: elimina current_run.
3. Estado remoto desconocido/campo is_active desconocido + velocidad cero: elimina current_run.

En los tres casos se observó current_runs_cleared=1 y owner sin cambios. La ruta HTTP llama al servicio antes del reset de mapa/runtime, de modo que un rechazo posterior no revierte la retirada del JobRun. Ninguna reproducción utilizó datos reales.

Esto requiere una corrección posterior y regresiones específicas de reset-preparation; esta fase de publicación no altera el commit de implementación solicitado. Tampoco se afirma atomicidad entre el snapshot del owner y todas las mutaciones concurrentes: la auditoría externa debe revisar esa ventana además de los guards y CAS existentes. No se concede GO operativo ni GO integral al conjunto de rutas.

### Regresiones y alcance

Se vuelven a ejecutar en simulated JobService, PhysicalOwnership, cancel identity, execution state machine, MotionAuthorizer, execution readiness y API final. Cubren productor, recovery, shutdown/estado desconocido y cambios de sesión. La ausencia de homing se comprueba como bloqueo de movimiento/readiness; archive-stale es administrativo y no exige homing por sí mismo. No se presenta esa distinción como permiso físico.

PUBLICATION_TEST_RESULT = PASS 119/119 (123.040 s), incluyendo API E2E simulado
PUBLICATION_DIFF_CHECK = PASS

Los 745 tests completos PASS y API 4/4 del bloque anterior corresponden a la implementación auditada en la fase previa. En esta fase no se modifica código y no se repite frontend/lint/build; se ejecuta diff check del rango completo y del documental.

### Conciliación de manifiestos y privacidad

No son dos inventarios incompatibles: los 535 archivos son el subconjunto data/ del manifiesto predeploy de 810. Los otros 275 incluyen archivos locales protegidos, frontend, configuración y otros respaldos. Se volvieron a verificar las 810 copias y los originales: copias 810/810 intactas; datos originales 535/535 intactos; inventario original global 809/810, con la excepción histórica conocida.

Archivo exacto afectado: `<checkout-productivo>/graphify-out/cache/last_query_stamp`. El cambio frente al predeploy permanece. No se restauró ni modificó durante este cierre. Para evitar otro efecto lateral no se ejecuta ninguna consulta Graphify productiva; se utiliza lectura directa del código. La huella de esa caché se compara antes y después del cierre.

Se retiraron del informe rutas absolutas privadas, endpoints específicos de instalación y PID innecesarios. No se incluyen IDs reales de proyectos, tokens, posiciones, mapas, G-code real, credenciales ni logs crudos. Las fixtures de tests son sintéticas. Se publican sólo los dos archivos del commit de implementación y este informe. graphify-out/ local queda sin seguimiento y fuera del push.

PRODUCTION_ADDITIONAL_MODIFICATIONS = NO
GRAPHIFY_ADDITIONAL_WRITES = NO
OPERATIONAL_AUTHORIZATION = NONE

## Cierre integral de bypass administrativos — 2026-10-20

Esta sección sustituye el hallazgo abierto de reset-preparation de la publicación anterior. Conserva el diagnóstico físico previo como evidencia histórica: **NO-GO operativo**, sin nuevas consultas al runtime ni autorización de recuperación. Base de esta corrección: `909dac6318556a9e8047abbd4abe94d350db2ac7`, descendiente del main aprobado `e6b3725a1f46ec6e89f706d02f1cc0d40bdb7184`.

### Defectos reproducidos antes de corregir

La reproducción inicial en `tests/test_administrative_exclusion.py` produjo cuatro métodos fallidos: reset con productor retenido/RECOVERY eliminaba el run; shutdown/estado remoto desconocido no impedían el reset; MovementLock ausente se aceptaba como libre; reset-reference/reset-map aceptaban un lease JOB_EXECUTION retenido. Todas las reproducciones utilizaron almacenamiento temporal y transportes fake.

La comprobación `is not True` se reemplazó por evidencia afirmativa `is False`. Un lock inexistente, una consulta que lanza excepción o una respuesta no booleana no demuestran exclusión. Ownership desconocido tampoco es IDLE.

### Política y secuencia de exclusión

`execution/administrative_guard.py` concentra una reserva administrativa del coordinador existente. No crea un PhysicalPermit ni permite despacho físico. La reserva exige revisión CAS vigente, IDLE explícito, ausencia de productor, hijos, dispatch, recovery y solicitudes de ejecución en curso. Los mutex del coordinador sólo protegen bookkeeping; no se mantienen durante HTTP, disco ni callbacks.

Antes de reservar se obtiene evidencia HTTP de firmware ready, homing XYZ, estado remoto explícitamente inactivo, `is_active_observed is False` y velocidad finita dentro de tolerancia. No se utiliza la velocidad cacheada para completar datos ausentes. El plazo de observación empieza antes de consultar y utiliza el timeout existente, sin ampliarlo. En modo simulado no se contacta Moonraker; los gates locales siguen activos.

Durante la reserva se revalidan sesión física, revisión de ownership, MovementLock y edad de la observación. Prepare/start/actions poseen tickets lógicos de escritura que excluyen la administración durante su solicitud; la administración excluye esos tickets y nuevas adquisiciones físicas. Así no se considera permanente el precheck anterior a HTTP.

El reset completo sigue este orden:

1. Validar todos los JobRun del setup, sus revisiones y, si existe, su identidad remota. Intentos de start ambiguos bloquean.
2. Persistir `administrative-reset.pending.json` antes de las mutaciones destructivas.
3. Crear históricos de todas las caras, conservando evidencia y progreso.
4. Ejecutar reset de mapa/referencia y sesión; comprobar el guard entre pasos.
5. Cerrar el runtime interno sólo mediante la reserva exacta. Este cierre conserva RECOVERY; no fabrica IDLE. No es un restart de servicio.
6. Revalidar conjunto de runs y CAS de cada archivo; retirar los actuales únicamente después de completar las dependencias.
7. Comprobar guard final y retirar el marcador sólo al terminar correctamente.

Si falla mapa/sesión, current_run aún existe. Si falla la retirada de un segundo run, los históricos de ambos ya existen y el marcador permanece. No se devuelve éxito. El coordinador permanece bloqueado por administración incompleta; la reconciliación ordinaria no puede despejarla. Tras reinicio simulado, JobService detecta el marcador y reinstala ese bloqueo. No hay replay, rollback, eliminación automática del journal ni endpoint nuevo de force-clear. La reparación de un marcador pendiente requiere auditoría explícita, no repetir ciegamente el reset.

**No se declara atomicidad global entre archivos.** CAS y reserva previenen nuevas solicitudes locales competidoras; un fallo de disco o una pérdida de sesión después de una escritura pueden dejar cambios parciales con historial y marcador. Este es un resultado bloqueado y trazable, no un reset exitoso. El protocolo no controla clientes externos de Moonraker ni escrituras manuales al filesystem. La evidencia HTTP expira y se comprueba, pero no se presenta como exclusión transaccional del firmware remoto.

### Matriz de rutas y cobertura

| Rutas / método | Exclusión y condiciones | Evidencia / prueba |
|---|---|---|
| job-run/archive-stale | Gate remoto estricto + reserva CAS + identidad + CAS del run + journal antes de retirar actual | JobService/archive; unknown/shutdown/owner; MovementLock libre/ocupado/ausente/error |
| reset-preparation / reset_runs_for_preparation | Gate completo, todos los runs/caras, históricos antes de dependencias, retirada al final, fallo persistido | Productor retenido, RECOVERY, shutdown, homing ausente, campo remoto ausente, HTTP fallido, cancel CAS, fallo de callback y segunda retirada |
| reset-reference | Reserva común; no run iniciado/no terminal; marcador de mutación | HTTP 400 con lease retenido; bytes de proyecto, referencias y mapa intactos |
| reset-map | Misma política de reset-reference | Mismos rechazos y preservación byte a byte |
| Project archive/trash/permanent-delete/restore | Reserva común y rechazo de ejecuciones que conservan dependencias | Suite API y exclusión del coordinador; no sustituye el archivado específico del JobRun |
| Actualizar proyecto/setup; añadir setup; editar/añadir/mover/duplicar/eliminar operación; cargar/quitar/analizar G-code | Reserva durante la escritura; no modifica dependencias de un run iniciado | Suite API; prepare/start no pueden comenzar durante reserva |
| Confirmar referencia máquina/origen/Z; capturar origen físico/Z/Z desde probe previo | Reserva común, sin añadir comandos físicos | Suite API/reference; productor y RECOVERY bloquean |
| probe-and-capture inicial | Rechazo de administración pendiente y de run con dependencias; conserva permiso físico y guards existentes del sondeo | Suite referencias/ownership; no se convierte en acción administrativa sin permiso |
| Armar y rehacer malla | Rechazo de run con dependencias; permiso MESH con revisión esperada y guards existentes | Suite mesh preparation/race; no se encapsula en un permiso administrativo que pueda mover |
| Edición manual/skip de punto, configurar/simular/importar/recalcular/validar/eliminar height-map, finalizar mapa | Reserva común y rechazo de dependencias de ejecución | Suite API/mapas; coordinador impide competir con productor físico |
| Medir/reintentar/reanudar/pausar/cancelar malla | Continúan contratos del worker, permisos MESH y cancelación propios; no son un archivado administrativo de current_run | Suite mesh, cancel/retry/recovery; sin comandos reales |
| prepare_run / start_run / run_action | Ticket lógico de escritura, rechazo de reserva/journal pendiente; permisos y guards físicos originales siguen obligatorios | Ambas direcciones de carrera administración/ejecución, cancel identity, state machine |
| _archive_run por finalización/cancelación | Histórico no destructivo; conserva current_run, no exige fingir IDLE para guardar evidencia | Suite JobRun/cancel/state machine |
| GET proyecto/mapa/historial, continue_project_step | No retiran current_run ni ofrecen un reset alternativo; continue sólo selecciona vista | Inspección de consumidores |
| open_project | Actualiza metadatos de apertura, protegido por reserva por ser escritura de project.json | Suite API |
| reset_session / reset_physical_session | En el reset completo se invocan bajo reserva y antes de retirar current_run; cierre autorizado conserva recovery | API reset completo; fallo intermedio y restart simulado |

Los servicios de persistencia no se presentan como una API de autorización independiente: las rutas administrativas se coordinan en backend. El worker conserva sus escrituras bajo su propio permiso físico; la reserva no permite reemplazarlo ni liberar un lease ajeno.

### Regresiones y resultados finales

`tests/test_administrative_exclusion.py`: **19/19 PASS**. Incluye productor activo/RECOVERY, remoto shutdown/desconocido, lock ausente/ocupado/error/no booleano, HTTP fallido, homing ausente, NaN, expiración, ownership adquirido durante preflight, cambio de sesión, cancelación CAS, dos caras del setup, fallo intermedio, fallo en segunda retirada, persistencia tras restart, exclusión de nuevos productores y prepare, y los tres endpoints de reset con bytes intactos.

Se actualizaron fixtures para exponer homing, virtual_sdcard observado y MovementLock de manera explícita. Una regresión antigua usaba reset para salir de una cancelación con evidencia remota fallida: ahora comprueba que ese reset se rechaza; el caso independiente de start con HTTP fallido usa una ejecución preparada nueva en otro test. No se relajó el guard para mantener ese bypass de fixture. Los tests de mensajes se ajustaron al diagnóstico estructurado nuevo.

Comandos ejecutados desde el worktree con `MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. PYTHONWARNINGS=ignore`:

- `python -m unittest tests.test_administrative_exclusion -q`: 19/19 PASS.
- `python -m unittest tests.test_administrative_exclusion tests.test_job_cancel_identity tests.test_mesh_failure_recovery_guards -q`: 42/42 PASS en la selección previa a los dos últimos casos de exclusión, también incluidos en la suite final.
- `python -m unittest discover -s tests -q`: **765/765 PASS**, 371.556 s, sobre el código final.
- `python -m unittest tests.test_final_workflow_api -q`: **4/4 PASS**, 11.351 s; los cuatro casos también están incluidos en la suite completa final.
- Frontend/lint/build: NOT_RUN; no se modifican frontend, dependencias ni archivos de distribución en esta fase.
- `git diff --check`: PASS.

Las primeras ejecuciones durante desarrollo detectaron expectativas de mensajes y fixtures incompatibles, además de los bypass reproducidos. No se contabilizan como PASS. Los logs seriales sintéticos de pruebas negativas no son observaciones ni conexiones de la CNC productiva.

### Integridad, límites y decisión

Verificación final: respaldo **810/810** intacto; datos productivos **535/535** intactos. Inventario global original **809/810**, misma excepción histórica `graphify-out/cache/last_query_stamp`. Su hash coincide con el inicio de esta fase: no se ejecutó Graphify ni se restauró su caché. Checkout productivo y cambios locales protegidos permanecen intactos. No se publican mapas, originales, artifacts productivos, tokens, logs crudos ni secretos.

Rutas bloqueadas deliberadamente: cualquier reset/archivado con productor retenido, RECOVERY, lock no observable, firmware shutdown, homing inválido, campos remotos ausentes, identidad incoherente, CAS/sesión cambiados o journal administrativo pendiente. Un run iniciado conserva sus dependencias frente a modificaciones independientes de referencia/mapa, aunque su worker haya desaparecido.

Riesgos residuales a revisar externamente: ausencia de transacción multiarchivo, posible cambio remoto por un cliente ajeno, tiempo de IO que puede agotar el plazo de observación y requerir revisión del journal. Ninguno se resuelve inventando reposo, ampliando freshness ni borrando evidencia. No se incluye una reparación automática de fallos parciales.

**GO para nueva auditoría externa del software; NO-GO operativo.** La situación física previamente observada sigue sin intervención autorizada. Esta fase no autoriza desplegar, recuperar, cancelar ni mover la máquina.

```text
RESET_ARCHIVE_CLOSURE
BASE_HEAD = 909dac6318556a9e8047abbd4abe94d350db2ac7
APPROVED_MAIN_ANCESTOR = e6b3725a1f46ec6e89f706d02f1cc0d40bdb7184
BRANCH = fix/cnc-ownership-recovery-20261019
FINAL_HEAD = commit documental de este cierre; SHA exacto en entrega y origin
RESET_PREPARATION_BYPASS_CLOSED = YES
RESET_REFERENCE_MAP_EXCLUDED = YES
UNKNOWN_MOVEMENT_LOCK_FAIL_CLOSED = YES
ADMINISTRATIVE_RESERVATION = YES; no permiso de movimiento
GLOBAL_FILESYSTEM_ATOMICITY = NO; journal e históricos, fallo cerrado
BACKEND_FULL = PASS 765/765
NEW_REGRESSIONS = PASS 19/19
PRODUCTION_ADDITIONAL_MODIFICATIONS = NO
PRODUCTIVE_DATA_HASHES = PASS 535/535
BACKUP_HASHES = PASS 810/810
GRAPHIFY_ADDITIONAL_WRITES = NO
MAIN_MODIFIED = NO
PHYSICAL_COMMANDS = NO
SERVICES_RESTARTED = NO
DEPLOY = NO
EXTERNAL_REAUDIT = GO
OPERATIONAL_VERDICT = NO-GO
```

Implementación de este cierre: `35168f7bd5656ddaa2a21252c0baa8eb97532209`. El siguiente commit contiene exclusivamente este informe.

Archivos de implementación:

- `execution/administrative_guard.py`: reserva, journal y exclusión de solicitudes de ejecución.
- `execution/job_service.py`: evidencia explícita, CAS, orden de archivado/reset y rechazo de journal pendiente.
- `machine/physical_ownership.py`: reserva administrativa sin permiso físico; recuperación incompleta no reconciliable automáticamente.
- `machine/runtime.py`: cierre interno bajo la reserva exacta del reset, conservando recuperación.
- `api/routes.py`: exclusión backend de resets y escrituras de dependencias; reset completo coordinado.
- `tests/test_administrative_exclusion.py`: 19 regresiones nuevas.
- `tests/physical_fakes.py`, `tests/test_job_service.py`, `tests/test_job_cancel_identity.py`, `tests/test_mesh_failure_recovery_guards.py`: fixtures explícitas y expectativas coherentes con fail-closed.

Estado local de entrega: archivos versionados sin cambios pendientes; `graphify-out/` preexistente permanece sin seguimiento y fuera de ambos commits. Push normal limitado a esta rama, sin merge ni cambios en main; el SHA remoto se verifica en la entrega final.
