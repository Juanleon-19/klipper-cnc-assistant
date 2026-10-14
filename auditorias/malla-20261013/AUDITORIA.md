# Auditoría de Mapa de alturas / Malla

## Base y aislamiento

Base real: `7b72a8b43ed8df3772e43923c844fdf970b2abbc`, `main` productivo.
Worktree de implementación: `kca-worktrees/mesh-workflow-stabilization`, rama
`fix/mesh-workflow-stabilization-20261012`. No se integra ni publica.

La inspección inicial de los 26 worktrees registrados encontró una rama local
no integrada, `fix/integracion-runtime-mapa-ejecucion` (`2bcfcf2`), y modificaciones
sin commit en `klipper-cnc-assistant-hotfix` (base `af0099d`). Se informó antes de
implementar; ambos quedaron intactos. No apareció una rama/worktree identificable
como telemetría/idle-stale. Se pidió su ubicación; no llegó una respuesta con esa
ubicación. No se asumió que hubiera que incorporar el hotfix antiguo ni se modificó
la política de frescura del runtime. Los cambios actuales no dependen de ese diff.

Producción conservaba y conserva `AGENTS.md` modificado, `.codex/` y `graphify-out/`
locales. No se escribieron datos, código, servicios ni configuración productivos.

## Hallazgos y corrección

1. **Validación decorativa:** el botón solo asignaba `meshValidationMessage`.
   Ahora solicita `/validate`, usa `build_coverage_report` y guarda una prueba de
   cobertura ligada al fingerprint de configuración, geometría, fuentes y contexto.
2. **Gate inexistente:** el endpoint de armado podía crear una malla sin validación;
   el worker tampoco exigía prueba de armado. Ambos comprueban ahora el contrato.
3. **Huecos entre vértices:** coverage revisaba puntos; un segmento podía atravesar
   una exclusión. Se comprueban aristas completas, rectángulos/círculos y celdas sin
   las cuatro muestras necesarias para interpolar. Las exclusiones circulares se
   estaban omitiendo al construir el HeightMap: ahora también llegan a ejecución.
4. **Operaciones omitidas:** no se deben excluir de la evaluación las operaciones
   de la misma cara por carecer de análisis. Ahora bloquean con `ANALYSIS_STALE`.
5. **Armado silencioso:** los retornos tempranos no explicaban los requisitos;
   la inspección read-only informa recuperación, owner activo, conexión, preview,
   cobertura y referencia. El servidor vuelve a comprobarlos al armar.
6. **Recuperación en ARM:** la llamada de armado termina en
   `_bind_captured_reference` → `require_current_reference` → comprobación del
   contexto/ownership. Si recovery está pendiente, la evidencia no autoriza.
   Es una barrera legítima, no una razón para cambiar el token. La nueva inspección
   la presenta antes del clic con la acción de verificar reposo y recuperar controles.
   No se atribuye el incidente histórico a una causa física específica sin su traza.
7. **Pausa/cancelación:** la finalización normal del mapa conservaba el epoch desde
   la base auditada, pero pausa/cancelación voluntarias pasaban por reconciliación
   que lo rotaba. Ahora solo un cierre con quiescencia confirmada y sin error conserva
   el epoch. Incertidumbre, recovery, watchdog y guards permanecen fail-closed.
8. **Retry ambiguo y endpoint roto:** el general llamaba execute-all; el individual
   llamaba incorrectamente a un método keyword-only y después ejecutaba pendientes.
   Ambos usan un conjunto explícito FAILED/RETRY_REQUIRED, cada índice como máximo
   una vez por solicitud. Un nuevo fallo pausa por seguridad; el resultado informa
   recuperados y los índices restantes.
9. **Rehacer:** `or` sustituía márgenes cero por 2 mm; la nueva versión anidada no
   era encontrada por el buscador de mapas y la referencia podía volver a sondearse.
   Se preservan ceros/perfil, se consulta el puntero activo, se archiva la versión
   anterior y la nueva exige preview/validate/arm. Al armar se vincula la captura
   canónica vigente; no se vuelve a medir ni se reescribe su token.
10. **Todo rojo:** existía `.alert` rojo sin estilos para los modificadores de
    severidad. Ahora SUCCESS/INFO/WARNING/ERROR tienen estilos explícitos.
11. **Controles:** perfiles generales explicados y no editables; personalización
    editable con restauración; ayuda de parámetros y unidades. Las exclusiones,
    cambios de receta y cambios de fuentes invalidan el flujo correspondiente.
12. **Pausa concurrente:** la suite completa reprodujo un 409 entre la intención
    de pausa y el progreso del worker. Las mutaciones locales comparten ahora una
    unidad read/modify/write con el RLock de I/O existente; siguen pasando por
    revisiones/CAS del repositorio. Una prueba con barreras hace competir pausa
    y progreso sin perder la intención ni iniciar un punto adicional.
13. **Preview:** mantiene material, región efectiva tras márgenes, exclusiones y
    puntos; añade trayectoria ya analizada, dominio válido verde y conflicto rojo.
    Limpiar/cancelar preview solo afecta la solicitud y estado transitorio del cliente.

14. **Muestras de otra adquisición:** una malla parcial con puntos medidos no puede
    rearmarse con otro token, aunque la nueva referencia sea válida y tenga la misma Z.
    Se exige rehacer para descartar las muestras incompatibles; el mapa completado
    sigue siendo único y reutilizable por las herramientas posteriores.

## Transiciones auditadas y contrato final

Abreviaturas: `P=/projects/{project}/operations/{operation}/physical-map`,
`M=/projects/{project}/physical-maps/{map}`; todos bajo `/api`.
La columna hardware describe lo que el producto puede hacer **al desplegarlo**.
Durante esta tarea solo se utilizaron transportes fake y almacenamiento temporal.

| Acción frontend | Endpoint | Método backend | Estado previo | Estado posterior | Persistencia | ¿Puede mover? | Bloqueos |
|---|---|---|---|---|---|---|---|
| Editar receta | Ninguno | Ninguno | Configuración/preview | Preview/validación obsoletas | Estado React | NO | Valores inválidos; worker activo mantiene su receta persistida |
| Sugerir cuadrícula | POST P/suggest | suggest_mesh_config | Operación seleccionada | Receta propuesta | Ninguna malla | NO | Material/configuración |
| 1 Generar vista previa | POST P/preview | preparation.preview | Receta válida | MESH_PREVIEW local | Ninguna malla | NO | Región/configuración inválida |
| Cancelar generación | AbortController | Ninguno | Request en curso | Sin preview pendiente | Ninguna | NO | Respuestas tardías descartadas por id de request |
| Limpiar vista previa | Ninguno | Ninguno | Preview no persistida | Sin preview | Ninguna | NO | Deshabilitado sin preview; conserva mapa/ref/receta |
| 2 Validar límites | POST P/validate | preparation.validate/evaluate | Preview actual | Cobertura válida o bloqueada | reports/mesh-validation, fingerprint | NO | Análisis/fuente, segmentos, exclusiones, soporte, transformación, límites, Z segura |
| Inspección para armar | POST P/arm-readiness | preparation.readiness | Receta/preview | Evaluación, sin transición | Ninguna | NO | Recovery, runtime, owner, preview, cobertura, referencia |
| 3 Armar | POST P/plan-from-reference | preparation.arm → plan_from_saved_reference | Preview=validación vigente | MESH_PLANNED/MESH_READY armado; compatible conserva medición | Mapa, binding inmutable, proof y puntero activo | NO | Requisitos anteriores, carrera de ownership/contexto/fingerprint |
| 4 Iniciar automático | POST M/execute-all | start_all → _start → _run | PLANNED/READY/PAUSED armado | MESH_PROBING | Estado/generación del worker, puntos | YES | Guard ownership, contexto, proof, token; HTTP/serial y MotionAuthorizer por punto |
| Medir un pendiente | POST M/execute-next | start_next → mismo worker | Mismos estados | PAUSED o COMPLETE | Un punto y progreso | YES | Mismas barreras; presupuesto de un punto |
| Worker | Interno | _run/_probe_one_point | MESH_PROBING | Siguiente punto/COMPLETE/PAUSED | Transiciones y mediciones | YES | Heartbeat, watchdog, lease, referencias, límites y guards de movimiento |
| Pausar | POST M/pause | pause | Worker activo; pausa idempotente | MESH_PAUSING → MESH_PAUSED | Intención y progreso | YES, solo termina punto comprometido | No comienza otro punto; reanudar no disponible hasta cierre |
| Reanudar | POST M/resume | resume → _start | MESH_PAUSED, sin worker | MESH_PROBING | Nueva generación, mismo mapa | YES | Sesión/contexto/recovery/proof/freshness; no repite MEASURED |
| Reintentar N fallidos | POST M/retry-failed | retry_failed → _start | READY/PAUSED, sin worker | PROBING → PAUSED/COMPLETE | Solo índices fallidos, retry_result | YES | Sin objetivos, contexto, recovery; se detiene ante nuevo fallo |
| Reintentar individual | POST M/points/{i}/retry | retry_failed(point_index=i) | FAILED/RETRY_REQUIRED | Medido o fallido/pausado | Solo punto i | YES | MEASURED/EXCLUDED/PENDING no seleccionables |
| Cancelar | POST M/cancel | cancel | Plan/worker/pausa | MESH_CANCELING → CANCELLED | Intención terminal y progreso | YES, solo termina punto comprometido | Ownership retenido hasta cierre seguro; watchdog sigue obligatorio |
| Modificar y rehacer | POST M/repeat | preparation.rebuild → repeat_measurement | Sin owner/worker incompatible | REPROBE_CONFIRMATION, armed=false | Archivo histórico + nueva versión vacía + puntero | NO | Lease exclusivo, recovery/owner; no revive referencia histórica |
| Reiniciar referencia y malla | POST /projects/{p}/setups/{s}/reset-reference | Servicio de preparación existente | Confirmación destructiva, reposo | Referencias/mapa invalidados | Preparación e histórico dependiente | NO | Guards existentes; la UI pide confirmación de intención destructiva |
| Completar | Interno | record_point → validación/finally | Último punto medido | MESH_COMPLETE, MAP_READY/VALID si cobertura válida | HeightMap relativo y validación | NO, cierre posterior al retracto | Quiescencia verificada; sin extrapolación ni recuperación pendiente |

## Invariantes

- Mapa único por setup/cara/superficie/placement. Herramienta de adquisición:
  trazabilidad; herramientas posteriores usan el mismo delta y su propia referencia.
- `PhysicalReferenceToken` y sus comparaciones no se relajan ni migran.
- Fingerprint de preview = proof persistido = geometría/configuración del mapa.
  Inicio y cada punto revalidan dependencias; los guards físicos siguen siendo la
  autoridad final. La inspección no adquiere ownership ni escribe.
- Armar y rehacer reservan temporalmente el coordinador sin emitir movimiento.
- Cancelación voluntaria no es parada de emergencia: termina el punto comprometido.
  El watchdog conserva su política de interrupción/recuperación ante incertidumbre.
- La comprobación de soporte es conservadora en fronteras de celdas sin muestras;
  puede exigir ampliar la región o quitar una exclusión pegada a la trayectoria.
  No permite extrapolar ni inventa muestras como evidencia física. Las muestras
  hipotéticas Z=0 existen solo dentro del cálculo geométrico de validación.

## Alcance de pruebas

Tests nuevos de API y worker usan captura canónica fake, validación real, persistencia
real, thread real y finally real. Pruebas históricas de mecánica del worker aíslan el
nuevo gate mediante un stub explícito; no son la evidencia de autorización. Las
pruebas de referencia/ownership/MotionAuthorizer/CAS continúan en la suite completa.
Los resultados finales y comandos se registran en `REPORTE_FINAL.md`.
