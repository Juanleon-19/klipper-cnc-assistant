# Candidata única: flujo principal

Base: a078a2b108c5012613355f67ef0d39894ff9e630.
Rama/worktree: fix/main-flow-20261022 / kca-worktrees/main-flow-20261022.

## Reinicio: evidencia y alcance

La consulta productiva GET /api/machine/status es un snapshot cacheado y no
reconcilia ni conecta transportes. En esta inspección: DIAGNOSTIC, ownership IDLE,
producer_active=false, dispatching=0, children=0, active_operation=null y
recovery_pending=false. No había current_run. La posición estaba stale y faltaba
homing; no se consideraron autorizaciones de movimiento.

Los logs existentes muestran rechazos anteriores de reset/recuperación y
recuperaciones explícitas posteriores exitosas. Los eventos conservados muestran
una referencia cancelada y otra fallida porque la sonda no quedó OPEN después
del retracto, seguidas de recuperación explícita de controles. No se enviaron
esas acciones durante esta tarea. El estado anterior exacto de movement_lock y
cada flag en el momento del mensaje genérico no quedó registrado: **no puede
atribuirse retrospectivamente el rechazo a uno de ellos con certeza**.

Defecto demostrado: el predicado de exclusión devolvía solo un booleano y el
mensaje enumeraba seis posibles causas. Ahora devuelve el motivo concreto y la
acción siguiente: productor, dispatch, hijos, operación, lock ocupado/desconocido,
recovery, reserva o actualización de JobRun. Se conserva el mismo fail-closed.
No se encontró evidencia suficiente para alterar el ciclo de liberación; no se
fuerza ownership ni se resuelve recovery desde reset. El camino legítimo existente
«Verificar reposo y recuperar controles» se verifica con API y navegador antes del
reset. Productor activo y movimiento siguen rechazados.

## Malla: causa y corrección

MeshPreparationService.evaluate detectaba TRANSFORM_UNAVAILABLE, pero continuaba
comparando coordenadas CNC None y Z ausente con límites reales. Eso generaba una
lista falsa de puntos X/Y fuera de recorrido y SAFE_Z_OUTSIDE_LIMITS.

Se separan resultados local_coverage_valid y physical_limits_valid. La misma
autoridad geométrica de compensación valida cobertura local, exclusiones y
segmentos sin conexión. Si falta origen/Z, se devuelve una sola causa física
TRANSFORM_UNAVAILABLE (además de problemas geométricos reales, si existen).
La geometría local sigue siendo visualizable. La referencia histórica con números
pero sin token/contexto vigente se informa como REFERENCE_INVALID y no como fallo
geométrico local. Armado/medición permanecen bloqueados.

Con referencia vigente se comprueban puntos transformados y límites X/Y/Z, contacto,
separación y retracto. Un punto fuera muestra índice, eje, coordenada CNC calculada
y límites. Se preservan fingerprint, validación vigente, CAS y guards de ejecución.
No se modifica el algoritmo de transformación ni la compensación/JIT.

## Conexión y velocidades

Tras reset se explica «Reconectar para continuar» y que la preview PCB sigue
disponible. No se añade autoconexión ni se impone conexión para geometría local.
Homing y preparación conservan endpoints independientes y el hotfix de observación
fresca de preparación. Los textos ya no describen esas acciones como una sola.

Z de despeje y aproximación: campos existentes editables/guardables, persistencia
verificada por test_machine_runtime. _reference_target_z_feed selecciona según
el sentido físico, no solo por signo. Se muestran ambos valores y traslado XY;
no se sube ninguna velocidad y se conservan límites Klipper.
reference_prep_xy_feed_mm_min en snapshot es actualmente una constante auxiliar
1800 mm/min, no un ajuste editable. Propuesta mínima, NO implementada: convertir
esa constante en configuración validada/persistida y exponer el campo en el mismo
formulario, con regresión de persistencia y límite efectivo Klipper. No es un
bloqueador para el flujo ni se modifica una velocidad productiva.

## Validación

- Backend relevante (administración, latencia, ownership/recovery, malla,
  preparación, runtime y API E2E): 148/148 PASS.
- Malla final ampliada: 27/27 PASS (incluye contexto obsoleto y retracto fuera).
- Frontend completo final: 198/198 PASS.
- Recorrido FastAPI real + transportes falsos: PASS.
- Browser integrado React + FastAPI: PASS en todas las fases del recorrido ampliado.
- Lint: PASS.
- Build: PASS (advertencia existente de tamaño de bundles).
- Diff check: PASS.

No se repite backend completo: el alcance se valida con suites pertinentes y el
recorrido real. Las pruebas no apuntan a datos ni transportes productivos.

Recorrido ampliado: rechazo reset en recovery con bytes current_run preservados;
recuperación explícita; reset e histórico conservado; desconexión; preview/validación
local sin origen/Z; conexión; homing; preparación con caché stale y error visible;
joystick simulado; captura conjunta XY/Z; cobertura inválida y corrección; validar,
armar y medir; recarga; JIT y ejecución A→A→B con confirmaciones humanas simuladas.

Se mantiene una sola candidata. No se cambian navegación, pestañas, diseño general,
servicios, dependencias ni datos productivos. No hay despliegue ni comandos físicos.
La validez física de la sonda y su liberación después del contacto requieren la
prueba posterior supervisada: no se declaran corregidas por simulación.

Integridad final: 788/788 hashes productivos y archivos protegidos idénticos al baseline de esta fase. Worktree entregado limpio, en un único commit local identificable con git log -1. No push ni despliegue.

FLUJO PRINCIPAL CORREGIDO Y VALIDADO EN SIMULACIÓN
