# Corrección del ciclo de referencia y Ejecución

Base: `4f34911ad87431e8507bf29997d5163c3b0423a1`.
Rama local: `fix/execution-reference-lifecycle-20260923`.
Implementación derivada del reporte de auditoría de Ejecución del 22/09/2026,
leído antes de modificar código. No se repitió la auditoría ni se accedió al hardware.

## Resultado funcional

- El cierre exitoso del worker real MESH, incluido su `finally`, revoca el lease
  y deja IDLE sin rotar la identidad física. Exige observación fresca de
  quiescencia, revisión coincidente, ausencia de hijos, despacho, cancelación y
  recovery. Las vías de incertidumbre conservan la invalidación anterior.
- El token sigue inmutable y fail-closed. No se elimina ninguna comparación de
  ownership, sesión, instalación, herramienta, placement o configuración.
  No se migra evidencia anterior al reinicio.
- Un único mapa por montaje/cara/superficie/placement. A,A,B,B reutiliza A en
  las dos primeras operaciones; B se instala y calibra una sola vez y su
  evidencia se revalida antes de B1 y B2. El mapa adquirido no cambia.
- `prepare_run` acepta únicamente ausencia de run o DRAFT/VALIDATING/READY
  sin inicio previo. Rechaza estados activos, transitorios, recovery y terminales
  antes de escribir. CAS protege además una cancelación concurrente.
- `GET execution/diagnostic` evalúa sin crear run, modificar revisión, publicar
  artifacts, preparar ni adquirir ownership. Conserva la caché y fingerprints
  de la base; dos lecturas sin cambios construyen el plan una sola vez.
- Preparación, estado en vivo y diagnóstico usan la misma evaluación backend.
  Separa geometría, runtime y referencia actual. Las referencias de herramientas
  futuras no bloquean el inicio. MotionAuthorizer y guards finales siguen siendo
  la autoridad de emisión.
- La UI separa cobertura de readiness, presenta causa/acción y referencia actual,
  deriva las filas consecutivas desde la evidencia vigente y muestra archivo JIT
  pendiente como «Se generará al iniciar». La inspección solo hace GET.
- Confirmación inicial explícita «Confirmo que el spindle está preparado»;
  cambia/invalida con run, sesiones e instalación/revisión. La API exige booleano
  `true`; los proofs backend mantienen la vinculación física existente.
- Recovery distingue referencia/sesión, identidad remota, standby incompatible,
  supervisor ausente y fallo de transición.

## Evidencia de validación

Se usaron datos temporales y fakes explícitos en todas las fronteras físicas.
No se omitieron ni relajaron MotionAuthorizer, PrintIdentity, CAS o cancelación.

| Requisito | Evidencia |
| --- | --- |
| Primera referencia → mesh real/finally → READY → JIT | `RealMeshWorkerHandoffTest`, token/sesiones/instalación idénticos; una sola captura inicial |
| Reinicio y negativos | 34 tests negativos: 18 de `PhysicalReferenceTokenTest` y 8 de handoff en cada una de sus dos variantes |
| A,A y A,A,B,B | `ExecutionReadinessTest.test_a_a_b_b_uses_single_map_and_probes_new_installation_only_once` |
| Preparación protegida | Todos los estados activos/espera/terminales; bytes del run idénticos tras rechazo; carrera con cancelación |
| Diagnóstico de solo lectura | Árbol de archivos y ownership idénticos, con/sin run; API comprueba revisión/bytes |
| Coherencia visual | Tests de React y Firefox: 5 coberturas / 0 bloqueos geométricos + sesión anterior = NO PUEDE INICIAR |
| Spindle humano | Rechazo API sin confirmación; checkbox obligatorio, invalidación por contexto y clic explícito en Firefox |
| API E2E | 6 escenarios: worker/finally + prepare/start/JIT, multiherramienta, pausa/resume/cancel, identidad ajena, cancelación en JIT y pausa idempotente del mapa |
| Browser E2E | Firefox, backend real, transporte fake, viewports reales 1440 y 390 px, sin overflow horizontal |

Una sola pasada completa de backend: **630 tests**, inicialmente 621 PASS y 9
fallos. Ocho correspondían a expectativas/hooks antiguos tras el cambio de
contrato (confirmación explícita, preparación de cancelados y evaluación común).
Se actualizaron manteniendo sus aserciones de seguridad y se repitieron los
módulos afectados. La prueba de pausa tenía una barrera fake de un segundo que
podía liberar el sondeo antes de la petición: ahora espera liberación explícita,
con timeout de fallo de diez segundos y cleanup. La repetición focalizada pasa.
Resultado agregado final: **630/630**; no se presenta como una segunda pasada
completa verde.

Una sola pasada completa de frontend: **149 tests**, inicialmente 146 PASS y 3
expectativas antiguas fallidas. Se corrigieron y repitieron los tests afectados.
La revisión final añadió tests para filas B,B después de calibrar y para el botón de inicio
cuando readiness se recupera desde VALIDATING; resultado agregado final **151/151**. Lint completo y build pasaron; tras el último cambio
visual se repitieron lint de archivos afectados, build y browser E2E.

Comandos reproducibles (desde un entorno de pruebas con dependencias instaladas):

```sh
MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. python -m unittest discover -s tests -p 'test_*.py'
cd frontend
npm test
npm run lint
npm run build
```

Desde la raíz del checkout, API/browser focalizados:

```sh
MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. python -m unittest tests.test_final_workflow_api tests.test_reference_execution_handoff.RealMeshWorkerHandoffTest.test_api_diagnostic_prepare_explicit_start_and_jit_after_real_mesh tests.test_api.ApiTest.test_pause_physical_map_is_idempotent_and_preserves_next_point
MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. python tests/execution_lifecycle_browser.py
git diff --check
```

El browser requiere Firefox/geckodriver y sockets localhost. Sus servidores
transitorios no inicializan el runtime real y se cierran al finalizar. Los logs
contienen advertencias existentes de deprecación FastAPI y tamaño de bundles;
no se abordó ese refactor.

## Límites y despliegue pendiente

Sin cambios de producción, push, merge, comandos físicos ni reinicios de
servicios reales. No se optimizó el proyecto histórico ni se migraron artifacts.
La inspección conserva la reutilización del plan; no se afirma una mejora de
latencia productiva sin medirla.

Después de un despliegue autorizado debe capturarse una **nueva referencia Z**
en la sesión física vigente. La referencia histórica de la auditoría permanece
inválida. El retest físico posterior debe comprobar referencia inicial → mapa →
operación 1 y A,A,B,B con confirmaciones humanas; no se ejecutó en esta fase.
