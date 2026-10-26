# Transición de operaciones, referencia Z y alturas

Base: `0ccfa4755e78a7a543f394039be706dce4228d05`.
Rama: `fix/operation-transition-reference-20261025`.
Worktree: `kca-worktrees/operation-transition-reference-20261025`.

## 1. Incidente confirmado

El journal persistido muestra: operación 1 COMPLETED → SPINDLE_STOP_REQUIRED →
confirmación humana (`POST .../job-run/action`) → RETRACTING → fallo tool_change_xy.
Solo el primer archivo tiene start_attempt=sent. La operación siguiente permanece
PENDING, sin artefacto, instalación ni start_print. El número 2/5 identifica la
operación pendiente, no demuestra que haya comenzado su mecanizado.

El snapshot conservado del fallo permite una conclusión concreta: distancia de
traslado con velocidad 10 mm/s, tiempo esperado 19,19 s, error a los 6,32 s.
La posición comandada ya era el destino, pero motion_report.live_position seguía
lejos del destino. _targets_reached consumía gcode_move.gcode_position, que Klipper
actualiza al encolar. El plazo de estabilización de 5 s arrancaba antes de llegar.
El código local de Klipper (`extras/gcode_move.py`, _get_gcode_position/get_status)
confirma la distinción; no se modificó Klipper.

La regresión con reloj falso reproduce el mismo error antes de la corrección.
Ahora la llegada/progreso se calcula con live_position, traducida al frame G-code
por la diferencia XYZ de gcode_move.position y gcode_move.gcode_position. Se exige
frescura de las tres entradas y velocidad vigente posterior al envío. No se
amplían timeouts ni se considera reposo una posición comandada. Datos ausentes,
no finitos, obsoletos o velocidad real siguen bloqueando. El error persistido
incluye fase, fuente de llegada, duración, distancia pendiente y edades sin
copiar comandos ni rutas privadas.

La presentación ahora prioriza el fallo concreto de transición frente a la referencia
pendiente de una herramienta aún no instalada, sin habilitar acciones adicionales.

El ownership RECOVERY es consecuencia legítima del movimiento emitido sin cierre
confirmado; no se libera, no se convierte en IDLE y no se reintenta automáticamente.
El snapshot productivo actual mantiene productor retenido, dispatch=0, children=0,
sin operación local activa. No equivale a autorización física ni prueba de reposo.

## 2. Alturas y velocidades

| Fase | Autoridad / valor | Cambio |
|---|---|---|
| Preparación inicial | aproximación estándar o larga explícita/heredada | Conservado |
| Despeje saliente | clearance del perfil instalado | Conservado |
| Estación | tool_change_work_z | Conservado; no es contacto |
| Retorno | clearance entrante → XY original → aproximación entrante | Conservado |
| Medición nueva Z | descenso desde aproximación verificada | Corrige reutilización del movimiento de malla |
| Malla | reference_z + safe_z_mm relativo | Conservado; independiente de clearance de herramienta |
| Contacto | medición nueva de sonda | Sin sustitución por alturas configuradas |

En la configuración observada, aproximación estándar=105 y clearance=115 son
conceptos distintos. El payload de malla calcula su traslado como referencia
medida + separación relativa; no lee el clearance de cambio. Un despeje a 115 antes de volver
al punto de referencia sí corresponde al perfil. No se reemplaza 115 globalmente.
La aproximación larga hereda 105 si no hay override; no se inventan alturas.

Se demostró otro acoplamiento: probe_tool_reference llamaba probe_mesh_point
DESPUÉS de aproximarse, volviendo a Z de la referencia histórica + separación de
malla. Ahora usa confirm_probe, comprueba XY fresco y mide una sola Z desde esa
aproximación. Mantiene filtros, perfil de contacto y retracto; la publicación sigue
comprobando contexto, CAS y token de instalación. No recaptura origen XY.

La subida/bajada auxiliar a Z segura de malla ignoraba los feeds configurados y
usaba F600 fijo. Ahora selecciona z_clearance_feed o reference_approach_z_feed por
dirección física. Los feeds de sonda, retracto, estación y G-code no se unifican.
XY mantiene 1800 en preparación/referencia y 600 en cambio/malla; no se añaden
opciones ni se aumentan velocidades. Configuración productiva intacta.

## 3. Inicio explícito y JIT

El cambio de herramienta ya exige confirmación de spindle, instalación y continuar.
Se conserva. Para cumplir el contrato solicitado también se elimina el arranque
implícito entre operaciones consecutivas de la misma herramienta: READY_TO_RESUME
con referencia reutilizada, sin nuevo probe, y confirmación explícita antes del
siguiente JIT/start. Cada confirmación se liga a la operación correspondiente.

JIT existente conservado: referencia vigente → generate legacy → verificación de
token/contexto, source hash, mapa y artefacto → upload sin start → journal previo
al transporte → MotionContext fresco → validación final → start. Preparar la
secuencia no genera artefactos futuros. Pruebas verifican cinco instalaciones,
cuatro cambios, cinco artefactos, mapa único, XY constante y ausencia de duplicados.

Antes de preparar, JOB_NOT_PREPARED es un requisito lógico; se muestra informativo
solo si es el único pendiente y los demás requisitos están verificados. Mapa
faltante, referencia inválida y errores reales no se ocultan. Referencias futuras
y JIT futuro no deben bloquear por sí solos la operación inicial; se conserva la
regresión existente. No se demostró que preparar pueda crear una malla física.

## 4. Latencia de reset

No hay medición histórica correlacionada de entrada/salida por fase que atribuya
la demora real a trabajo duplicado. Las regresiones de IO lento y transacción
administrativa se conservan. No se cambia ni optimiza reset por intuición.

## 5. Validación

Backend completo: **835/835 PASS** (480,165 s). Focalizados finales: **50/50 PASS**.
API E2E: **4/4 PASS**. Frontend completo: **202/202 PASS**.
Lint y build: **PASS** (advertencia preexistente por tamaño del bundle).
Browser React + FastAPI + runtime falso: **PASS**, incluye referencia, malla,
JIT, continuación manual con misma herramienta, cambio, medición Z, continuación
y recarga en TOOL_CHANGE_REQUIRED / READY_TO_RESUME sin duplicar emisiones/start.
Navegación lateral conservada, comprobada en 1440/900/390 px.
Diff check: **PASS**. Todos los tests usan almacenamiento
temporal y transportes falsos, MACHINE_MODE=simulated, MACHINE_AUTO_CONNECT=false.
Fixtures que ejercitan código físico inyectan explícitamente transportes en memoria.
No conexiones ni comandos contra hardware.

## 6. Integridad y despliegue

No se modifica JobRun productivo, referencias, mapas, G-code, configuración, sistema
operativo ni servicios durante desarrollo. Evidencia privada fuera de Git; informe
sin IDs productivos, G-code real, snapshots completos o secretos.

El despliegue requiere reiniciar backend. No se puede tratar la recuperación actual
con productor retenido como reposo confirmado. No se ejecutará parada, actualización
ni recuperación productiva con ese riesgo sin resolver. El software no rehabilita
la sesión ni el token del incidente y no ofrece repetición automática de comandos.

## 7. Prueba futura del operador

Después de revisión/instalación segura y recuperación física autorizada por separado:
1. Confirmar estado físico, sesión, homing y referencia válida; no reutilizar tokens
   históricos tras cambio de sesión ni forzar ownership.
2. Preparar cinco operaciones y verificar que los artefactos futuros aún no existen.
3. Iniciar primera operación explícitamente; al completar, confirmar spindle detenido.
4. Observar despeje saliente y traslado completo; debe terminar en TOOL_CHANGE_REQUIRED.
5. Confirmar instalación entrante; verificar clearance, retorno al XY original,
   aproximación según perfil y una medición Z (sin traslado de malla adicional).
6. Verificar READY_TO_RESUME: no inicia mecanizado hasta confirmación de continuar.
7. Repetir cambios y recarga de interfaz; comprobar mapa intacto y JIT nuevo por operación.
8. Con misma herramienta, verificar referencia reutilizada y nuevo inicio explícito.
9. Ante pérdida de evidencia o transición incierta, abortar prueba y conservar diagnóstico;
   no repetir start, traslado o probe automáticamente.

## Archivos y evidencia de pruebas

- `machine/runtime.py`: llegada observada y transformación; referencia Z directa con
  XY verificado; feed auxiliar Z de malla y textos de fases.
- `execution/job_service.py`: adaptación al sondeo de referencia, espera explícita
  también con misma herramienta y diagnóstico acotado de transición fallida.
- `execution/health.py`: conserva la causa del fallo de movimiento aunque falte Z
  de una herramienta futura; no agrega permisos.
- `ExecutionConsole.tsx`: referencia vigente (reutilizada o recién medida), requisito
  lógico de preparación separado de bloqueos físicos.
- Tests nuevos: `test_arrival_observation.py` (7), `test_reference_phase_contract.py` (4).
- Tests ampliados: execution_state_machine/readiness, job_service, final_workflow_api,
  functional_journey, functional_journey_browser y ExecutionConsole.
- `docs/functional-continuity.md`: registra inicio explícito, llegada y separación Z.

La reproducción inicial falló como se esperaba: destino encolado y velocidad 10
producían timeout de estabilización antes de llegada. También se rechazó considerar
correcta una prueba que aceptaba posición comandada sin live_position.
La primera suite completa cargó pruebas todavía con el contrato anterior de avance
automático y detectó una extracción incorrecta del resultado de sonda. Se corrigió
el consumo de la respuesta de la medición recién ejecutada (sin rescatar una sonda
histórica) y se repitió validación. Un test de malla con límite de espera del harness
falló bajo carga paralela; pasó aislado sin ampliar timeouts productivos ni del test.

Comandos (Python del entorno existente, sin instalar dependencias):

```sh
export MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. PYTHONWARNINGS=ignore
python -m unittest tests.test_execution_state_machine tests.test_execution_readiness tests.test_arrival_observation tests.test_reference_phase_contract -q
python -m unittest tests.test_final_workflow_api -q
python -m unittest discover -s tests -q
python tests/functional_journey_browser.py
# En frontend:
npm run test -- --run
npm run lint
npm run build
# En raíz:
git diff --check
```

No se ejecutaron movimientos reales, reconexiones, reset, cancelación, reinicios ni
actualizaciones de producción. No se borraron locks ni journals. El manifiesto
privado cubre 796 archivos operativos/protegidos; no sustituye un respaldo nuevo
de la futura ventana de despliegue. Respaldos anteriores conservados.

## Cierre de candidata

Commits de implementación:
- `5172239`: llegada observada fresca, sin adelantar el plazo de estabilización.
- `e1302dc`: referencia de herramienta independiente del traslado de malla y feeds Z existentes.
- `d5008b3`: inicio explícito, diagnóstico y regresiones integradas.

Comprobación final del manifiesto privado: **796/796 intactos**, cero diferencias.
Checkout productivo conserva HEAD base y los cambios locales preexistentes de
AGENTS.md, .codex/ y graphify-out/. No hubo integración en main ni despliegue.

SOFTWARE_VALIDATED = YES
PHYSICAL_VALIDATION_PENDING = YES
DEPLOY_EXECUTED = NO
SERVICES_RESTARTED = NO
PHYSICAL_COMMANDS = NO
PRODUCTIVE_JOB_MODIFIED = NO
MAIN_MODIFIED = NO
VERDICT = CANDIDATA VALIDADA EN SIMULACIÓN; DESPLIEGUE BLOQUEADO POR RECUPERACIÓN PRODUCTIVA SIN REPOSO/ AISLAMIENTO ACTUAL VERIFICADO.

No se declara resuelto el ownership del incidente. Para instalar se necesita una
ventana segura bajo el procedimiento existente, con evidencia actual de reposo o
la excepción de aislamiento físico explícitamente aceptada por el operador.
La validación software no autoriza recuperar ni repetir el movimiento incierto.
