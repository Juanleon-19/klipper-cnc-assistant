# Cierre explícito de ejecución cancelada e incompleta

Base: `17459c606cadf087e68ee2a4584acfc5e6b23c4c`.
Rama: `fix/cancelled-run-standby-reconciliation-20261028`.
Commit final: el commit que contiene este informe (`git rev-parse HEAD`).

## Causa y alcance

El archivo remoto vacío en standby no demuestra que la última emisión terminara.
El contrato ordinario exige evidencia de finalización y bloqueaba correctamente
el caso cancelado con operación iniciada pero sin finalización acreditada. Faltaba
una decisión humana distinta: abandonar el intento como incompleto, sin declararlo
completado. No se cambia `_assert_administrative_print_identity` ni el archivado
ordinario; se incorpora una autorización limitada al reset completo.

## Flujo visible

«Reiniciar proceso» conserva su diálogo habitual. Si la última emisión de una
ejecución cancelada corresponde a una operación iniciada, solicita además:
«Abandonar ejecución incompleta y reiniciar», advierte de pieza parcialmente
mecanizada y de invalidación de mapa/referencias/compensaciones. Rechazar el diálogo
no envía el reset. Aceptarlo envía una sola petición al endpoint existente con
`abandoned_run={run_id,revision,abandon_incomplete:true}`. La revisión obsoleta se
rechaza; no existe reintento automático ni confirmación implícita por filename vacío.

El servidor exige JOB_CANCELLED, intento sent con identificador, emisión posible,
operación inequívoca, archivo exacto válido y observación histórica de impresión.
La operación debe conservar RUNNING y no tener finished_at/completed_at: se archiva
esa evidencia original, no se transforma en COMPLETED. Un intento sending,
dispatching o uncertain sigue rechazado.

## Evidencia y exclusión

La reserva administrativa, MovementLock observable/libre, ausencia de productor,
operación/hijos/dispatch, sesión y CAS existentes siguen siendo obligatorios.
Se renueva HTTP en los límites transaccionales: conexión, Klipper ready, standby,
filename exactamente vacío, is_active_observed=false y sin indicadores contradictorios.
Se exige posición física finita y velocidad observada de reposo dentro de la tolerancia
existente, sin ampliarla. Datos ausentes, booleanos de velocidad, otro archivo o
impresión activa bloquean. No se exige homing para esta invalidación sin movimiento;
no se reutilizan permisos físicos ni referencias para una ejecución nueva.

Solo reset-preparation admite la confirmación y requiere callback de invalidación.
Otras ejecuciones del mismo montaje conservan sus controles ordinarios. El histórico
se guarda antes de invalidar dependencias; current_run se retira solo tras finalizar
los callbacks y verificar revisiones. La reserva cubre toda la secuencia, sin mantener
mutex de bookkeeping durante HTTP/disco. Un fallo deja el journal y el bloqueo:
no se anuncia éxito ni se presupone rollback de escrituras parciales.

## Historial y recuperación de interrupciones

Se conserva operations, progreso, referencias, archivo, start_attempt, resultado de
cancelación y estado JOB_CANCELLED. No se inventan finished_at ni completed_at para
este cierre. Se añade `administrative_closure.outcome=INCOMPLETE_UNVERIFIED`, la
confirmación vinculada, la última emisión y la observación remota de autorización.
No significa cancelación física exitosa ni certeza de pieza terminada.

El journal guarda la confirmación. La recuperación existente verifica inventarios,
proyecto/montaje, misma revisión/emisión y reposo nuevamente. Conserva el journal
original en histórico y NO libera ownership físico. Tras la recuperación se requiere
verificar reposo y volver a confirmar el reset; no se repiten escrituras automáticamente.
Reintentar la misma revisión reutiliza su archivo histórico. Una decisión histórica
incompatible se rechaza en lugar de sobrescribirla.

## Validación

Resultados definitivos al final del informe. Fixtures sintéticos y almacenamiento
temporal; MACHINE_MODE=simulated, MACHINE_AUTO_CONNECT=false. Los tests de contrato
físico utilizan adaptadores falsos, no la CNC ni datos productivos.

La regresión inicial confirmó que no existía el parámetro de abandono. La primera
prueba de fallo intermedio también reprodujo el bloqueo del recuperador al exigir
finalización completa. Se corrigió la conciliación usando la confirmación persistida,
no eliminando la validación de identidad. Otros fallos iniciales fueron expectativas
de fixtures (posición ausente, método de conciliación sin retorno y mock React con
llamadas anteriores); los fixtures se corrigieron sin relajar las barreras.

## Límites e instalación futura

No desplegado. No se modifican producción, rama estable, respaldo funcional,
configuraciones, preparación105/115, joystick, sondeo, cambio de herramienta ni JIT.
No se ejecutan comandos físicos ni reinicios. El caso requiere revisión y despliegue
posterior con ventana de mantenimiento/aislamiento y respaldo verificados, guardia
systemd conservada y MACHINE_AUTO_CONNECT=false. Después el operador debe verificar
reposo conectado y confirmar conscientemente el abandono desde la interfaz.

No resuelve el error local de preparación por confirmación de zona libre ni la señal
de sonda activada: son asuntos separados y fuera de este cambio. No autoriza sondeo
sin OPEN fresca ni reutilizar una pieza parcialmente mecanizada sin evaluación humana.

## Separación de proyectos y causa del nuevo incidente

La lectura del incidente distingue dos proyectos con sus propios run/map/token.
A conserva una ejecución cancelada: primera operación completada, segunda iniciada
sin finalización acreditada. B conserva otra ejecución, en recuperación antes de
emitir su primera operación (sin start_attempt). No se publican identificadores ni
mediciones productivas; la relación se contrastó en archivos y logs privados.

La raíz global de control físico corresponde a B, no a A. El mapa de B está completo
y su worker persistido figura terminado, sin hijos ni dispatch. La presentación
MESH_PROBING procede del adaptador de recuperación: por sí sola no acredita un
worker de malla vivo. No se cambia esa protección ni se libera la raíz productiva.

En B, la referencia inicial pertenece a una herramienta diferente de la primera
operación. Es correcto exigir una medición explícita para la herramienta real. La
ruta update-tool-reference registró movimiento a referencia y calibración, pero
rechazó la medición por igualdad exacta entre Y observada y Y del punto del mapa:
la diferencia es únicamente redondeo binario. No hay evidencia de inicio automático
del mecanizado de la primera operación. La petición concreta se infiere de la ruta
única que genera esos eventos y el motivo de recuperación; el cuerpo HTTP no estaba
registrado.

Correcciones limitadas:
- physical_reference conserva igualdad exacta token/medición, fecha, Z, sesión,
  instalación, proyecto y mapa. Solo la comparación de XY observado con el punto
  objetivo permite 1e-12 mm absolutos de redondeo numérico (no tolerancia física).
  No se reescribe ni regenera ningún token. Una discrepancia real sigue bloqueando.
- Si falta referencia para la herramienta seleccionada y el mapa identifica otra,
  el diagnóstico nombra ambas herramientas y la operación que requiere medición.
- Cuando termina con error la medición síncrona de ejecución, retira exclusivamente
  su productor. También lo hace el supervisor que termina en RECOVERY_REQUIRED con recuperación
  física global ya pendiente (no el reintento seguro anterior a una emisión).
  Retirar NO libera ownership: conserva RECOVERY, hijos/dispatch y evidencia incierta.
  La verificación explícita posterior sigue exigiendo reposo físico fresco.
- La interfaz retira ejecución/errores/plan cargados al cambiar de proyecto, montaje
  o cara. Muestra el identificador del proyecto y montaje/cara del dueño físico global.
  Las respuestas tardías siguen protegidas por cancelación/generación existentes.

Regresiones A/B: A cancelado e incompleto no impide iniciar B libre y con referencia
válida; B conserva su token y no repite sondeo inicial. Dueño A realmente activo o
RECOVERY bloquea B; token de A no autoriza B. Error de referencia retira al productor
terminado sin liberar control ni cambiar los bytes de A. No se promueve ninguna
referencia histórica ni se transfieren mediciones entre herramientas/proyectos.

## Protocolo de revisión e instalación

1. Revisar esta rama y respaldar código/datos/configuración en ventana autorizada.
2. Confirmar aislamiento físico; conservar guardia de cierre y autoconexión desactivada.
3. Detener solo Assistant una vez, verificar salida, actualizar por fast-forward y
   publicar frontend validado; iniciar solo Assistant y verificar hashes/datos.
4. No ejecutar recuperación, reset ni movimientos como parte del despliegue.

El productor ya retenido por el proceso antiguo no queda reparado retroactivamente
por esta corrección. La nueva versión debe cargarse mediante mantenimiento autorizado.
Después, la recuperación existente necesita evidencia fresca y explícita; nunca
borrar locks ni editar current_run. Si sigue existiendo un productor vivo, hijos,
dispatch o resultado físico incierto, detener el procedimiento y conservar evidencia.

## Protocolo físico posterior, exclusivo del operador

1. Seleccionar B y comprobar nombre de proyecto y primera herramienta antes de medir.
2. Resolver controles por la acción explícita de reposo solo con observación válida.
   Verificar señal de sonda OPEN fresca y el circuito reparado antes de cualquier sondeo.
3. Si la referencia corresponde a otra herramienta/sesión, efectuar preparación y
   captura conjunta supervisadas con la primera herramienta correcta; medir y validar
   el mapa del mismo proyecto. No copiar tokens ni considerar válidas las referencias
   históricas tras un cambio de sesión.
4. Preparar cinco operaciones, confirmar spindle preparado y autorizar la primera.
   Con referencia inicial vigente no debe repetirse su medición. JIT debe usarla.
5. Al cambiar herramienta: confirmar spindle detenido, estación, cambio humano,
   medición correspondiente y continuación explícita. Verificar cada operación.
6. Para abandonar A, abrir A → Referencia → Más acciones → Reiniciar proceso y
   confirmar «Abandonar ejecución incompleta». Exige reposo y control libre; conserva
   historia incompleta e invalida dependencias. El botón general del listado conserva
   el reset ordinario y no otorga esta confirmación especial.

No se promete que el estado productivo actual esté desbloqueado: no fue modificado.
La ausencia de actividad en registros no certifica por sí sola quiescencia física.

## Evidencia de validación y límites

Entorno de todas las pruebas Python:
`MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:.`.
Los fixtures que ejercitan contratos físicos inyectan transportes/serial/telemetría
falsos y directorios temporales. Los procesos HTTP y Firefox usan puertos locales
exclusivos de prueba; nunca el servicio instalado ni el repositorio de datos real.

| Validación | Comando/alcance | Resultado |
|---|---|---|
| Abandono y administración | unittest: cancelled_standby_reset, administrative_remote_identity, administrative_exclusion, administrative_latency, administrative_recovery, job_cancel_identity | 98/98 PASS |
| Límites finales del abandono | `python -m unittest tests.test_cancelled_standby_reset -q` | 13/13 PASS |
| A/B y tokens, incluido diagnóstico de herramienta | `python -m unittest tests.test_project_reference_isolation tests.test_physical_reference_token -q` | 26/26 PASS |
| Recorrido API real y cinco operaciones | `python -m unittest tests.test_project_reference_isolation tests.test_functional_journey tests.test_execution_state_machine -q` | 37/37 PASS (antes del caso adicional de texto de herramienta, validado arriba) |
| Frontend completo | `npm test -- --run` | 213/213 PASS, 18 archivos |
| Navegador: abandono | `python tests/cancelled_standby_browser.py` | PASS: rechazo, aceptación única, histórico, desconexión y recarga sin ejecución activa |
| Navegador: flujo completo | `python tests/functional_journey_browser.py` | PASS |
| Lint | `npm run lint` | PASS |
| Build | `npm run build` | PASS; advertencia existente de tamaño del bundle |
| Espacios y conflictos | `git diff --check` | PASS |

El navegador completo comprobó conexión, homing falso, preparación, control manual
falso, captura conjunta, preview/validación/medición de malla, plan, cinco operaciones,
confirmaciones humanas simuladas y recarga entre estados, cancelación, archivado,
reset de mapa y reset completo. El test de abandono inicialmente seleccionaba el
botón homónimo del listado general; se corrigió el selector para accionar el control
del workspace. El reset ordinario rechazó de forma segura la petición sin confirmación.

Graphify se consultó/actualizó únicamente en el worktree (AST, sin instalación de
paquetes). Sus artefactos generados se conservaron fuera del árbol versionado. No
se consultó ni escribió la caché productiva. Comprobación final de 601 archivos de
datos/protegidos: hashes idénticos entre ambas lecturas de cierre. No es una
instantánea atómica ni acredita períodos anteriores a esas lecturas. HEAD productivo
70475a8 y sus cambios locales permanecen; la rama estable remota conserva 70475a8.

PHYSICAL_COMMANDS = NO
PHYSICAL_CONNECTIONS_BY_TESTS = NO
PRODUCTION_WRITES = NO
SERVICES_RESTARTED = NO
DEPLOY = NO
MAIN_MODIFIED = NO


La primera suite backend integrada (894 casos) detectó una regresión: retirar el
productor en cualquier RECOVERY_REQUIRED convertía un fallo reintentable anterior
al movimiento de cambio en recuperación física nueva. Se corrigió la condición:
el supervisor solo retira en esa salida si el coordinador YA conserva recuperación
física. El reintento previo a emisión mantiene su contrato anterior. No se cambió
el movimiento de cambio ni se relajó su validación. Se repitieron las regresiones
de transición y la suite completa tras corregirlo.


Commit de implementación: `8e92d4795ae850e23b7c95fc1e03cdbf5bc6f9ee`.
Regresiones posteriores al ajuste del productor: 38/38 PASS
(`tests.test_job_service.JobServiceTest.test_retry_tool_change_transition_does_not_repeat_completed_operation`,
`tests.test_project_reference_isolation`, `tests.test_execution_state_machine`).

Archivos de implementación:
- execution/job_service.py: cierre explícito, conservación de histórico, retiro de
  productor terminado en recuperación física y diagnóstico de herramienta inicial.
- execution/administrative_recovery.py: conciliación de la decisión persistida de
  abandono con identidad de proyecto/montaje y evidencia actual.
- machine/physical_reference.py: comparación numérica acotada y causas específicas.
- api/schemas.py y api/routes.py: payload tipado exclusivo de reset completo.
- frontend/types.ts y lib/api.ts: confirmación vinculada a ejecución/revisión.
- ProjectWorkspace.tsx: confirmación humana adicional y limpieza del contexto visual.
- ReferenceWorkspace.tsx: responsable del control físico global.
- Tests Python/React y cancelled_standby_browser.py: regresiones y recorrido real
  de interfaz/API con transportes falsos; ningún fixture productivo.
- docs/functional-continuity.md y este reporte: contratos, validación y límites.

## Resultado definitivo

BACKEND_FULL = PASS — 895/895, 514.500 s
Comando: `python -m unittest discover -s tests -q`, con entorno simulado indicado.
API_E2E = PASS — 4/4 de tests.test_final_workflow_api, incluidos en la suite completa.
FRONTEND_FULL = PASS — 213/213
BROWSER_ABANDONMENT = PASS
BROWSER_FUNCTIONAL_JOURNEY = PASS
LINT = PASS
BUILD = PASS
DIFF_CHECK = PASS
IMPLEMENTATION_COMMIT = 8e92d4795ae850e23b7c95fc1e03cdbf5bc6f9ee
EXTERNAL_REVIEW = READY
DEPLOY_AUTHORIZED_BY_THIS_REPORT = NO

No hay fallo de software pendiente en los recorridos probados. Permanecen las
condiciones físicas y de sesión del incidente real, que no se han cambiado. La
publicación de esta candidata no constituye autorización para recuperar/mover la CNC.
