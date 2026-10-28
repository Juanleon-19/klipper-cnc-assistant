# Preparación inicial por perfil — revisión de software

Base: `70475a88475b603dacbcd5805ac229f216359c82`.
Rama: `fix/tool-approach-profile-20261028`.

## Causa y corrección

`prepare_position()` seleccionaba el clearance de cambio y `initialize()` terminaba
allí tras centrar XY. El frontend mostraba la aproximación estándar aunque la
operación seleccionada fuera larga. Ambos conceptos ya estaban configurados por
separado, pero preparación no consumía la aproximación final.

Ahora la ruta de preparación inicial resuelve ambas alturas por perfil:
1. Evidencia HTTP válida, homing XYZ, ownership y configuración coherentes.
2. Despeje sin bajar hacia la superficie: combina posición actual, clearance,
   safe_z y aproximación, respetando el sentido Z configurado.
3. Traslado XY al centro, con confirmación de llegada.
4. Aproximación Z final según perfil, con contexto físico fresco ligado a la
   posición del centro/despeje y verificación de llegada final.

Para aproximarse hacia la superficie se exige confirmación explícita del operador
sobre zona y recorrido vertical libres, antes del primer movimiento. El frontend
presenta perfil, centro y Z final y solicita esa confirmación en cada preparación
(incluida repetición). El backend recibe `preparation_area_clear`, false por defecto;
sin ella rechaza el descenso. Esta declaración humana NO detecta obstáculos ni
sustituye límites, homing, frescura, velocidad, ownership o cancelación. La operación
solo debe solicitarse cuando el operador pueda verificar físicamente ese volumen.
No se introduce sondeo automático ni se presume que estar dentro del recorrido
Klipper acredita una zona libre. Clientes antiguos sin confirmación pueden recibir
un rechazo explicativo cuando se necesite descenso.

Se comprueba que la configuración no cambió entre resolver alturas y adquirir el
contexto de preparación. Un fallo de despeje/XY impide aproximación; fallo final,
cancelación o falta de observación impiden publicar preparación terminada.
`home_xyz()` sigue realizando solo homing. La ruta legacy `initialize()` conserva
su secuencia sin la aproximación opcional; la nueva fase solo se solicita mediante
`prepare_position()`. No se modifica cambio de herramienta, sonda, joystick, mapa,
JIT ni ejecución.

## Configuración e interfaz

- Estándar termina en `reference_prep_z_mm` (pruebas:105).
- Larga termina en `long_tool_reference_approach_z_mm` (pruebas:115).
- Null sigue significando heredar estándar, por compatibilidad. No hay migración
  silenciosa ni valor productivo cambiado.
- La acción «Seleccionar aproximación larga de 115 mm» solo edita el formulario.
  Se debe guardar mediante el flujo existente y confirmar coherencia del runtime.
  Continúan disponibles altura personalizada y volver a herencia.
- Se muestran aproximación final efectiva y despeje por separado para la operación
  seleccionada. Los feeds auxiliares ya existentes se reutilizan; ninguno aumenta.

## Archivos

- `machine/runtime.py`: fase final opcional de aproximación, confirmación, contexto,
  límites y comprobación de configuración; secuencia anterior de homing conservada.
- `api/machine_schemas.py`, `api/machine_routes.py`: payload exclusivo de preparación
  con confirmación; no cambia el contrato de tool-change-position.
- `frontend/src/lib/api.ts`, `features/system/MachineContext.tsx`, `App.tsx`: propagan
  confirmación únicamente a preparación.
- `ProjectWorkspace.tsx`, `ReferenceWorkspace.tsx`: perfil, confirmación explícita,
  altura efectiva y selección opcional de115 pendiente de guardar.
- Tests de runtime/API/React: secuencia por perfil, herencia/persistencia, límites,
  homing, fallo HTTP, cancelación, fallo XY/final, cambio de configuración y mensajes.
- Journey API/browser: acepta la nueva confirmación y verifica la aproximación
  final; mantiene referencia, mapa y cinco operaciones con cambios de herramienta.

## Alcance físico

SOFTWARE_VALIDATED: ver resultados de cierre abajo.
PHYSICAL_VALIDATION_PENDING = YES.
PRODUCTION_MODIFIED = NO.
STABLE_BRANCH_MODIFIED = NO.
PHYSICAL_COMMANDS = NO.
SERVICES_RESTARTED = NO.
DEPLOY = NO.

Prueba física posterior, solo tras revisión/instalación autorizadas: verificar zona
libre con la herramienta realmente instalada, guardar alturas105/115 explícitas,
seleccionar cada operación y comprobar despeje→centro→aproximación final. Confirmar
que rechazar el diálogo no inicia preparación. No se ejecutó esta prueba ahora.

## Resultados de cierre

- Runtime, preparación/API y ejecución: **123/123 PASS** (85,360 s).
- Selección final tras añadir chequeo de configuración: **29/29 PASS** (110,961 s),
  `test_separate_preparation`, `test_preparation_api`, `test_functional_journey`,
  `test_final_workflow_api`; incluye API E2E y cinco operaciones con herramientas.
- Frontend completo: **209/209 PASS**,18 archivos (88,02 s).
- Browser E2E React→API→runtime con transportes falsos: **PASS**. Diálogo de zona
  libre aceptado explícitamente en el navegador; una petición por preparación.
  Referencia, malla, cancelación/reset y cinco operaciones conservados.
- Build: **PASS**, advertencia previa de tamaño del bundle, sin cambios de dependencias.
- Lint y `git diff --check`: **PASS**.
- Backend completo: NOT_RUN; se ejecutan las regresiones focalizadas solicitadas y
  la integración completa pertinente, sin repetir las suites ajenas al cambio.

Los primeros fallos de tests correspondían a expectativas anteriores: altura final
igual al clearance y preparación sin diálogo. Se actualizaron para comprobar el
contrato nuevo; no se eliminaron barreras ni aserciones físicas. Logs de esta fase:
`/tmp/kca-approach-{backend,integration-final,frontend-acceptance,browser-final,build,lint-final}.log`.
Python ejecutado con `MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. PYTHONWARNINGS=ignore` y fixtures temporales.
Dependencias Node copiadas al worktree, no enlazadas a producción; sin instalaciones.

SOFTWARE_VALIDATED = YES.
RESULTADO = READY_FOR_EXTERNAL_REVIEW.
No autoriza despliegue ni valida físicamente el descenso.

Último endurecimiento focalizado: cancelación recibida durante la observación final
se comprueba antes de publicar WAITING_FOR_XY_REFERENCE, bajo el lock existente.
Regresión determinista incluida. Preparación/API finales: **25/25 PASS**,3,914 s
(`/tmp/kca-approach-final-cancel.log`); ningún cambio adicional de frontend.
