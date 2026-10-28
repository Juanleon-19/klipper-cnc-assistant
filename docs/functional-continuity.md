# Contrato funcional y continuidad

Leer este documento antes de cambiar referencias, malla, ejecución o administración.
El objetivo es preparar y ejecutar operaciones de un montaje con una sola malla,
referencias válidas por instalación y confirmaciones humanas explícitas. No se
considera terminado un cambio por compilar o pasar pruebas aisladas.

## Responsabilidades

React presenta acciones y errores; FastAPI valida solicitudes; application coordina
proyectos/referencia/mapa; execution coordina JobRun y JIT; MachineRuntime y
MotionAuthorizer autorizan la frontera física; storage conserva revisiones e historial.
La interfaz nunca es una autoridad de movimiento.

## Recorrido

Abrir proyecto/montaje → reinicio explícito y seguro, si corresponde → conectar →
homing XYZ → preparar altura y centro → joystick XY → guardar conjuntamente XY
actual y referencia Z → preview → validar cobertura → armar → medir malla → preparar
JobRun → confirmar spindle → Legacy JIT por operación → upload → start autorizado.
Misma herramienta: reutilizar instalación vigente, esperar confirmación explícita
de continuar y generar JIT nuevo. Nunca iniciar automáticamente otra operación. Cambio:
SPINDLE_STOP_REQUIRED → confirmación → despeje saliente → TOOL_CHANGE_REQUIRED →
instalación entrante → retorno al XY del mapa → una Z → READY_TO_RESUME → confirmación
→ JIT. No remallar ni rehabilitar tokens históricos.

## Persistencia y barreras

JobRun conserva revisión CAS, start_attempt e historial; cancelar domina sobre
escrituras tardías. Sending/uncertain no autorizan repetición automática.
Referencias ligan sesión, instalación, contexto y configuración. El mapa liga
montaje/cara/superficie/placement; nunca pertenece exclusivamente a una herramienta.
Ownership, MovementLock observable, ausencia de productores y recovery resuelta
son condiciones independientes de que un hilo parezca inactivo.

La observación física no es un timeout para escrituras administrativas. Consultar
estado remoto real en fronteras de mutación, bajo reserva administrativa. Timestamp
al recibir la observación; comprobar identidad y sesión. La reserva impide nuevos
productores locales y no autoriza movimiento. CAS protege los registros. Un error
parcial conserva current_run e histórico y deja administrative-reset.pending.json;
no reejecutar ni borrar ese journal automáticamente. No hay transacción distribuida
con Moonraker ni rollback automático de dependencias ya escritas.

## Regresiones obligatorias

- HTTP y disco lentos; cambio remoto entre observaciones; fallo de escritura.
- Archivo vacío standby con evidencia completa frente a identidad diferente,
  desconocida, inicio ambiguo, movimiento, desconexión o cambio de sesión.
- Ownership retenido, recovery, lock desconocido, CAS y cancelación concurrente.
- Captura XY/Z → malla → JIT → varias operaciones/cambio de herramienta.
- Recarga del navegador y persistencia; confirmaciones humanas y errores visibles.
- Muestras XYZ iguales siguen frescas; actividad WS sin posición no autoriza.

## Estado verificable

Base validada anterior: `83ebcde64e946e61d08d178e537dd17c0db97b67`.
Candidata en desarrollo: `fix/functional-stabilization-20261021`.
Recorrido integral de navegador validado con transportes simulados y tres
operaciones A → A → B. Resultados completos en
`auditorias/functional-stabilization-20261021/REPORTE_FINAL.md`. No declarar validación física mediante simulación.
Producción, datos, servicios y hardware quedan fuera del desarrollo.

## Candidata de reparación del flujo principal

`fix/main-flow-20261022`, basada en `a078a2b`: preview/cobertura PCB local sin
referencia; límites CNC únicamente con transformación y referencia vigente.
Ausencia de referencia no significa puntos fuera de límites. Reinicio explica
el flag concreto que bloquea; recuperación sigue siendo explícita y con evidencia
fresca, nunca implícita en reset. Referencias/sonda físicas pendientes de validación
supervisada. Evidencia en `auditorias/main-flow-20261022/REPORTE_FINAL.md`.

## Transición y llegada observada

Candidata `fix/operation-transition-reference-20261025`, base `0ccfa475`: llegar
requiere motion_report.live_position, transformada al frame objetivo con offsets
XYZ vigentes. gcode_position es el destino encolado, no evidencia de llegada.
No arrancar el plazo de estabilización mientras la herramienta sigue en tránsito.
Al cambiar herramienta: despeje entrante → XY original → aproximación de su perfil
→ una medición Z; no ejecutar de nuevo el traslado de un punto de malla.
La malla conserva referencia medida + separación relativa, con feeds auxiliares Z
configurados; contacto y retracto mantienen su perfil. Recuperación física del
incidente y validación real pendientes; ningún test simulado las sustituye.

### Recuperación administrativa (26 octubre)

Un journal incompleto ya tiene conciliación explícita: inspección/recuperación en
Referencia, evidencia de archivos, reserva exclusiva y observación remota fresca.
El original se archiva como evidencia; no se restauran JobRun ni referencias. La
conciliación administrativa conserva RECOVERY físico. Luego verificar reposo y
Reiniciar proceso mediante los controles existentes. Un journal antiguo requiere
importación local de respaldo verificado; cambios no explicados bloquean. Véase
`auditorias/administrative-map-recovery-20261026/REPORTE_FINAL.md`.

### Cierre y nueva preparación (26 octubre)

Los intentos terminales se conservan hasta un cierre explícito. «Cerrar intento y
preparar otro» archiva con reserva, identidad remota, velocidad, sesión y CAS; no
reutiliza el permiso local retirado. Una preparación nueva crea otra identidad y
cinco operaciones pendientes cuando el proyecto tiene cinco operaciones. Homing
sigue siendo obligatorio para movimiento, no para invalidación administrativa sin
movimiento. Desconexión, impresión activa e identidad incierta no acreditan reposo.

Reset de mapa archiva todas las versiones vigentes del montaje; la receta vuelve a
los defaults de la UI, sin tocar ajustes globales. Reset completo retira también la
asociación de ejecución y las referencias. Respuestas de un contexto anterior no
pueden repoblar paneles después del reset. Reintentar un reset reconciliado reutiliza
el histórico administrativo de la misma revisión, sin duplicarlo ni sobrescribirlo.
Validación y límites: `auditorias/preparation-reset-lifecycle-20261026/REPORTE_FINAL.md`.

### Preparación inicial por perfil (28 octubre; candidata, no desplegada)

La preparación independiente viaja al despeje y al centro antes de aproximar Z a
la altura efectiva del perfil seleccionado. Un descenso final requiere confirmación
humana de zona/recorrido libre, además de evidencia física, homing, límites,
ownership y cancelación existentes. La aproximación larga puede seleccionarse
explícitamente como115 y guardarse; null conserva herencia, sin migrar producción.
El cambio de herramienta y la captura/sonda mantienen sus contratos existentes.
Validación: `auditorias/tool-approach-profile-20261028/REPORTE_FINAL.md`.

### Abandono explícito de intento cancelado incompleto (28 octubre; no desplegado)

Solo Reiniciar proceso admite confirmación humana vinculada a run_id/revisión para
archivar una última emisión sent inequívoca, observada imprimiendo pero sin final
acreditado. Exige standby vacío y reposo HTTP verificable, exclusión física, sesión
y CAS. Conserva toda la evidencia como INCOMPLETE_UNVERIFIED; no inventa completion
ni cancelación física exitosa. Invalida dependencias dentro del mismo reset antes de
retirar current_run. El journal conserva la confirmación para recuperación explícita
con inventarios y reposo renovados; no libera ownership ni reanuda trabajos.
Regresión obligatoria: fallo parcial→recuperación→reconfirmación sin duplicar histórico.
Informe: `auditorias/cancelled-run-standby-reconciliation-20261028/REPORTE_FINAL.md`.

La ejecución cancelada de un proyecto no es la ejecución activa de otro. El control
físico es global y su owner_id identifica el proyecto/montaje/cara responsable.
Una referencia inicial solo evita sondeo duplicado si coinciden herramienta,
instalación, sesión y vínculo canónico con el mapa; nunca retaggear tokens. La
comparación XY observado/punto objetivo admite solo redondeo numérico de 1e-12 mm;
token y medición persistida siguen siendo exactamente iguales. El productor de una
medición fallida o de un supervisor terminado con recuperación física pendiente se
retira, conservando RECOVERY hasta comprobación explícita de reposo. Un rechazo
reintentable previo a emisión no se convierte por ello en nueva recuperación física.
Regresiones obligatorias: dos proyectos con A cancelado/B libre, dueño global activo
o recuperándose, referencia ajena, redondeo XY y primera operación sin sondeo duplicado.

Última implementación validada en simulación: `8e92d4795ae850e23b7c95fc1e03cdbf5bc6f9ee`
(895 backend, 213 frontend, API y navegador integrados). Informe:
`auditorias/cancelled-run-standby-reconciliation-20261028/REPORTE_FINAL.md`.
Pendiente: revisión/despliegue autorizado y recuperación física explícita del incidente;
no se ha reparado ni migrado automáticamente ningún token ni ejecución productiva.
