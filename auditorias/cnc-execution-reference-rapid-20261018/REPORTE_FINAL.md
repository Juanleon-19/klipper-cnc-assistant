# CNC — auditoría y correcciones para revisión externa

## Alcance y evidencia

Base verificada: `b0dbcdc691a145b4b086dcd9cc2dcf1795e10880` (main, origin/main local y producción al iniciar).
Rama: `fix/cnc-execution-reference-rapid-20261018`.
Worktree: `/home/impresora/kca-worktrees/cnc-execution-reference-rapid-20261018`.
Los commits locales se listan en la entrega. FINAL_HEAD corresponde al commit que contiene este informe (`git rev-parse HEAD`); no se incluye una autorreferencia SHA imposible de mantener dentro del propio commit.

Se leyeron instrucciones, arquitectura, historial, JobRun, proyecto, original, artefacto ejecutado, metadata, mapa, journal y respuestas GET. No se invocaron acciones productivas ni la consulta live_execution que podría reconciliar persistencia. La evidencia completa permanece privada fuera de Git; este informe contiene resultados agregados, no archivos del cliente ni snapshots productivos.

El historial ya incluye MotionContext y el journal start_attempt del trabajo anterior. No se incorporó la rama pendiente de integración runtime. El hotfix histórico ya era ancestro de la base; no se ha integrado ninguna otra rama.

## 1. P0 — observación HTTP al confirmar spindle detenido

**Causa verificada:** Moonraker expone motion_report y una posición finita. La lectura directa devolvió velocidad `-5.551115123125783e-17`. Klipper calcula esa magnitud con `start_v + accel * move_time`, susceptible a residuos de cancelación flotante. El runtime rechazaba cualquier valor negativo. La misma respuesta reproducida con transporte fake produce el rechazo previo. El journal registra tres HTTP 400 después de completar la operación. No se conserva la respuesta HTTP histórica de esos tres intentos: no se afirma que su valor exacto fuera idéntico al observado después.

El estado actual de Klipper era shutdown y sin homing; eso continúa bloqueando legítimamente. No se recuperó ni desbloqueó el trabajo.

**Corrección:** MachineState normaliza exclusivamente residuos negativos dentro de 1e-12 mm/s a cero. Ausencia de velocidad no se interpreta como cero; NaN, infinitos y negativos significativos siguen rechazándose. Es una corrección numérica, no una ampliación de tolerancia de movimiento. HTTP estricto verifica muestra real de motion_report, edad, duración de consulta y continuidad de sesión/transporte. La confirmación exige además evidencia reciente de velocidad dentro de la tolerancia configurada, homing, telemetría y estado de impresión compatible.

La observación HTTP y WebSocket convergen en MachineState. El descubrimiento conserva presencia, timestamps y procedencia de live_position/live_velocity; no se autoriza con toolhead.position como sustituto físico. Ninguna marca de tiempo se renueva para suplir datos ausentes. MotionAuthorizer, ownership, CAS, PrintIdentity, journal y sus tiempos no se relajan.

**Tests:** `test_http_velocity_residual.py`: HTTP válido/residuo, faltantes, NaN/inf/negativo, posición ausente, muestra vieja, fallo HTTP, shutdown, sesión cambiada, máquina activa y evidencia válida detenida. Fakes verifican ausencia de emisión ante fallo.

**Estado:** PASS software; requiere repetición física autorizada posterior. Riesgo residual: no existe sensor de spindle; la confirmación humana no sustituye evidencia independiente de reposo de ejes.

## 2. Probar posición de cambio

La ruta UI enviaba la acción sin perfil; backend seleccionaba standard. Además, varios bloqueos solo deshabilitaban el botón sin explicación. El trabajo productivo sigue en SPINDLE_STOP_REQUIRED, con ownership de ejecución: probar la estación fuera del JobRun es incompatible y debe seguir bloqueado.

Se transmite el perfil seleccionado por ProjectWorkspace → MachineContext → API → runtime. Se explica trabajo activo, configuración sin guardar, conexión/Klipper, homing incompleto, recovery, operación incompatible y posición antigua. Las rutas standalone verifican también el guard existente del JobRun, incluyendo ejecuciones persistidas sin worker. La UI es informativa: la autoridad final sigue en backend y MotionAuthorizer.

**Tests:** API separada/perfiles y rechazo sin invocar runtime cuando hay trabajo activo; pruebas runtime existentes de límites, perfil largo/estándar, Z antes de XY y fallos; nuevas pruebas UI de causas de bloqueo. **Estado:** PASS software. No se ha pulsado el botón real.

## 3. Homing, preparación y referencia independientes

- `home_xyz` ejecuta solamente homing y verifica ejes; termina HOMED, sin traslado al centro.
- `prepare_position(profile)` conserva homing actual, obtiene observación fresca, elige despeje del perfil y no baja hacia superficie para alcanzar un despeje menor. Verifica Z antes de XY y llegada al centro. No emite homing.
- `go_to_reference_point` conserva el origen guardado, sube al despeje aplicable, viaja XY y después se aproxima a referencia; no sondea ni cambia origen.

Las dos primeras operaciones reutilizan la secuencia protegida de initialize con fases explícitas; no son dos botones que disparan el workflow combinado. La API initialize anterior permanece compatible. Fallo de Z impide comenzar XY y deja ERROR, nunca preparación válida parcial. Se conservan cancelación/ownership y validación final de movimiento.

**Tests:** `test_separate_preparation.py`, `test_preparation_api.py`, runtime y frontend. **Estado:** PASS software.

## 4. Perfiles y coordenadas

Se añade `long_tool_reference_approach_z_mm`, opcional y persistente, distinto del alias histórico de clearance. Si no está configurado hereda aproximación estándar, evitando inventar una altura. No se modificó configuración productiva. JobService y adaptador propagan el perfil entrante; el despeje saliente sigue usando la herramienta saliente.

| Etapa | Estándar | Larga |
|---|---|---|
| Preparación inicial al centro | clearance estándar, sin bajar desde una Z más alejada | clearance largo, misma regla |
| Salida hacia estación | clearance de herramienta saliente | clearance largo si saliente larga |
| Manipulación en estación | work_z configurada | work_z configurada |
| Retorno XY | clearance de herramienta entrante | clearance largo si entrante larga |
| Aproximación a referencia guardada | reference_prep_z_mm | long_tool_reference_approach_z_mm o herencia explícita |
| Sondeo/contacto | posición real medida | posición real medida; no offset inventado |

El flujo inicial de captura X/Y+Z sigue sondeando desde la posición real elegida por el operador. No se añadió un traslado silencioso al pulsar captura. La preparación inicial usa ya el despeje del perfil; la visita posterior a una referencia guardada usa la aproximación específica.

Se respeta la orientación configurada de Z y los límites. La geometría de abrazaderas, voladizo real y longitud de herramienta no está modelada completamente. Dentro de límites NO equivale a libre de colisiones. Configuración observada: ambos despejes actuales son iguales; el override nuevo no se ha configurado en producción. Se requiere validar alturas con el operador antes de una prueba física. Cambiar configuración física relevante mantiene la invalidación de evidencias existente; no se migran ni reviven tokens históricos. Software no aprueba esas alturas.

## 5. Legacy y lentitud real

Se identificó por JobRun y metadata el artefacto realmente ejecutado y se comprobó su correspondencia con el archivo remoto. Original y artefacto se analizaron en copias privadas. La identidad/hash del original coincide con metadata. SHA256 original: `cbb2d566d3016d98f6afebea4d8150dc41df840930bdc5cb7516907b8e601c20`; artefacto ejecutado: `287e93eaf15899754e3af0eb098e820210c4aac4a040c8be8ece694d72971419`. La primera operación completó; cuatro siguen pendientes.

**Causas:** `_format_move` emitía siempre G1. El archivo original contiene F25 modal; “Rapids:1500” es un comentario, no una orden F1500. Klipper registra G0 en el mismo handler que G1: cambiar la letra por sí solo no aumenta velocidad. A F25, el límite de velocidad/aceleración observado no es el cuello de botella dominante. La subdivisión agrega solo tres segmentos.

| Métrica | Original FlatCAM | Legacy ejecutado |
|---|---:|---:|
| G0 | 8 | 0 |
| G1 | 6 | 17 |
| Segmentos analizados | 14 | 17 |
| Expansión | 1 | 1.2143 |
| F efectivo (mm/min) | 25 | 25 |
| Distancia G0 (mm) | 135.1181 | 0 |
| Distancia G1 bruta del parser (mm) | 21 | 326.7991 |
| Distancia comparable excluyendo primer movimiento (mm) | 154.1181 | 154.1531 |
| Tiempo cinemático a F, sin primer movimiento (s) | 369.8833 | 369.9674 |

La distancia bruta Legacy incluye una primera trayectoria artificial desde cero del parser hasta coordenadas absolutas de máquina. No debe interpretarse como movimiento real adicional ni usarse para atribuir lentitud. El tiempo real observado del run (~467.6 s) incluye diferencias de inicio/cola/no reconstruibles exactamente con esta evidencia.

Ejemplos relativos anonimizados de traslados consecutivos entre perforaciones: 39.1196 mm, 29 mm y 45.9985 mm, a Z programada +2 mm. Sumados: 114.1181 mm, aproximadamente 273.8833 s a F25. Cada perforación baja de +2 a -2.5, retorna controlada a 0 y retrae a +2; último retiro a +15. Distancia de trabajo vertical total: 21 mm, ~50.4 s. Rápidos nominales después del primer movimiento: 133.1181 mm, ~319.4833 s a F25. Estimaciones ignoran aceleración, cola y transitorios; no son tiempos físicos garantizados.

**Corrección Legacy:** conserva G0 solo en segmentos originales G0 con inicio conocido, clearance configurado, trayectoria completamente soportada sin exclusiones/celdas faltantes, orientación positiva conocida y traslado horizontal por encima de la cota o retracto vertical ascendente. Comparte la cota de clearance de adaptive (máximo delta de mapa + margen y safe-Z configurada). Desconocidos, diagonales, descenso, falta de soporte o altura insuficiente permanecen conservadoramente G1 con advertencia. No cambia F de perforación ni interpreta comentarios como autorización de avance. No modifica compensación, unidades, normalización absoluta ni referencia Z. Se versiona el algoritmo para no reutilizar artefactos viejos silenciosamente.

También se corrigió que la deserialización Legacy descartaba exclusion_zones; ahora conserva esas regiones en la autoridad geométrica.

**Resultado sobre copia real:** Z de tránsito original +2, safe-Z del mapa +4. No se promocionan sus traslados XY. Candidato: 1 G0 (retiro vertical final) y 16 G1, mismos avances. Mejora de tiempo aplicada: **0%**. Un F1500 explícito reduciría teóricamente esos 114.1181 mm a ~4.56 s más aceleración, pero NO está autorizado por un comentario ni implementado. Hace falta aprobar tanto altura segura como política de avance antes de acelerar. Cambiar todos los movimientos a rápido habría ocultado un problema geométrico real.

**Tests:** `test_legacy_safe_rapid.py`: seguro, insuficiente, fuera de dominio, diagonal, delta alto, configuración ausente, exclusión cruzada, celda sin soporte, serialización, unidades pulgadas, relativo/modal y preservación F.

**Estado:** PASS conservación segura de semántica; BLOCKED mejora física de tiempo del archivo actual, por altura y avance sin aprobación. G1 conservador tampoco constituye certificación de colisión: conserva comportamiento/avance existente y advertencias; la validación geométrica física sigue pendiente.

## 6. Integridad de ejecución

Las pruebas existentes A,A,B,B,C verifican un mapa, tres instalaciones, dos mediciones posteriores, cinco JIT nuevos, cinco starts, origen XY constante, token reutilizado solo para misma instalación, hash de fuente y metadata por operación. Se cubre standard→long, long→standard y long→long. También regresiones de restart, cancel dominance, CAS, no IO costoso post-MotionContext y journal not_sent/sending/uncertain. No se cambiaron esos mecanismos.

Flujo: preparar → validar → confirmar spindle → JIT por operación → validar/subir/revalidar/start. Misma herramienta continúa sin probe; distinta exige spindle detenido y evidencia fresca, salida con perfil saliente, instalación, retorno con entrante, una Z nueva y confirmación Continuar. No remalla.

## Validación

Todas las pruebas con MACHINE_MODE=simulated, MACHINE_AUTO_CONNECT=false, datos temporales y transportes fake. Los E2E usan puertos aislados, no la API/Moonraker productivos. Los mensajes de errores seriales en tests corresponden a casos negativos fake.

- Backend focalizado nuevo: 22/22 PASS; selección amplia previa: 125/125 PASS.
- Backend completo final: 735/735 PASS.
- Frontend completo final: 181/181 PASS, 18 archivos.
- API E2E: 4/4 PASS (`test_final_workflow_api`).
- Browser E2E: PASS (`execution_lifecycle_browser.py`, Firefox, servidor y transportes aislados).
- Lint: PASS.
- Build: PASS.
- Diff check: PASS.

La primera pasada completa detectó incompatibilidades en fakes, serialización del override None y un wrapper instrumentado; se corrigieron y la siguiente pasó 731/731. No se ocultan esos fallos como preexistentes. La pasada final comprueba además todos los negativos añadidos durante la revisión.

Integridad: SHA256 de proyecto, current_run, fuente, artefacto y metadata coinciden con el manifiesto capturado antes de trabajar. El mapa copiado también coincide con producción. HEAD productivo y status local siguen iguales (`M AGENTS.md`, `.codex/`, `graphify-out/`); no se escribieron esos paths.

```text
CNC_AUDIT_FINAL
BASE_HEAD = b0dbcdc691a145b4b086dcd9cc2dcf1795e10880
FINAL_HEAD = commit local que contiene este informe; SHA exacto en entrega
BRANCH = fix/cnc-execution-reference-rapid-20261018
WORKTREE = /home/impresora/kca-worktrees/cnc-execution-reference-rapid-20261018
SPINDLE_HTTP_ROOT_CAUSE = residuo flotante negativo rechazado, reproducido; respuesta histórica exacta no disponible
SPINDLE_HTTP_FIX = PASS SOFTWARE; shutdown/homing ausente continúan bloqueando
TOOL_CHANGE_BUTTON_ROOT_CAUSE = perfil omitido y bloqueos poco visibles; JobRun activo bloquea legítimamente
TOOL_CHANGE_BUTTON_FIX = PASS SOFTWARE
HOMING_SEPARATION = PASS SOFTWARE
REFERENCE_BUTTON_PRESERVED = YES
LONG_TOOL_INITIAL_REFERENCE = PASS SOFTWARE para preparación por perfil; sondeo inicia en posición elegida
LONG_TOOL_CHANGE_CLEARANCE = PASS SOFTWARE
LONG_TOOL_RETURN_REFERENCE = PASS SOFTWARE
LEGACY_G0_G1_ROOT_CAUSE = formatter siempre G1; F25 modal también limita G0 en Klipper
LEGACY_RAPID_FIX = PASS SOFTWARE, promoción solo con evidencia de clearance/soporte
FLATCAM_CONTRIBUTION = F25 ejecutable; 1500 aparece solo en comentario
ESTIMATED_TRAVEL_IMPROVEMENT = 0% aplicado al archivo real; aceleración BLOCKED
SAME_TOOL_FLOW = PASS
TOOL_CHANGE_FLOW = PASS
JIT_COMPENSATION = PASS
BACKEND_TESTS = 735/735 PASS
FRONTEND_TESTS = 181/181 PASS
API_E2E = 4/4 PASS
BROWSER_E2E = PASS
LINT = PASS
BUILD = PASS
DIFF_CHECK = PASS
PHYSICAL_COMMANDS = NO
PRODUCTIVE_JOB_MODIFIED = NO
MAIN_MODIFIED = NO
SERVICES_RESTARTED = NO
DEPLOY = NO
PUSH = NO
EXTERNAL_AUDIT_READY = YES, con limitaciones físicas explícitas
PHYSICAL_TESTS_PENDING = YES
BLOCKERS = [validar geometría/alturas de herramientas, aprobar clearance y avance explícito antes de acelerar perforación, prueba física pendiente de autorización]
```

La preparación de auditoría externa no equivale a GO de despliegue ni a cierre físico del problema de velocidad.

## Revisión física posterior — NO ejecutada

1. Auditoría externa y autorización de despliegue separadas; preservar el JobRun y evidencia actuales.
2. Con operador y máquina habilitada posteriormente, comprobar GET con motion_report completo, sesión estable, homing y velocidad de reposo válidos. Un estado shutdown sigue siendo bloqueo; no resolverlo con este parche.
3. Aprobar explícitamente despeje y aproximación de cada herramienta, orientación Z, recorrido y obstáculos. En entorno seguro, probar homing solo; después preparación independiente; después referencia guardada, verificando Z antes de XY.
4. Probar estación solamente sin JobRun incompatible. Para el trabajo interrumpido usar exclusivamente el flujo de Ejecución autorizado, no botón standalone.
5. Verificar salida estándar/entrada larga y viceversa con alturas aprobadas, misma XY de referencia, una Z nueva y sin remalla.
6. Revisar el archivo de perforación en copia: resolver +2 versus safe-Z +4 y definir avance de traslado explícito. No modificar F de corte ni ejecutar candidato hasta nueva auditoría y autorización.

Ninguno de estos pasos fue ejecutado físicamente. No se proporciona una aprobación física basada en simulación.

## Comandos reproducibles y archivos

Desde el worktree aislado, con Python del entorno existente (sin reinstalaciones):

```sh
MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. PYTHONWARNINGS=ignore python -m unittest tests.test_http_velocity_residual tests.test_separate_preparation tests.test_preparation_api tests.test_legacy_safe_rapid -q
MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. PYTHONWARNINGS=ignore python -m unittest discover -s tests -q
MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. PYTHONWARNINGS=ignore python -m unittest tests.test_final_workflow_api -q
MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. PYTHONWARNINGS=ignore python tests/execution_lifecycle_browser.py
npm --prefix frontend run test -- --run
npm --prefix frontend run lint
npm --prefix frontend run build
git diff --check
```

En este host se usó el ejecutable Python del virtualenv ya instalado, no el alias `python` del sistema. Build generado solamente en el worktree; no se publicó frontend.

| Archivo/grupo | Cambio |
|---|---|
| machine/state.py | Normalización acotada del residuo de velocidad observado |
| machine/runtime.py | HTTP fresco/sesión, homing/preparación separados, clearance y aproximación por perfil |
| machine/config.py | Override opcional de aproximación larga |
| execution/job_service.py | Evidencia de velocidad al confirmar spindle y propagación de perfil |
| api/machine_routes.py y machine_schemas.py | Acciones independientes, guard de JobRun y perfil validado |
| application/compensated_gcode_service.py | G0 condicionado a política compartida; exclusiones; versión algoritmo |
| frontend App, MachineContext, api, types | Tipos y despacho de acciones/perfiles |
| ProjectWorkspace | Conecta botones independientes y setting largo |
| ReferenceWorkspace | Botones separados, bloqueos visibles, campo de aproximación |
| tests nuevos HTTP, preparation, API, Legacy | Regresiones y negativos exclusivamente fake |
| test_job_service y test_machine_runtime | Fakes con evidencia realista y nuevas expectativas de clearance |
| test_physical_reference_token | Wrapper instrumentado acepta parámetros de política; conserva guard de token |
| tests frontend ProjectWorkspace y ReferenceWorkspace | Acción/perfil y motivos de bloqueo |
| auditorias de esta fase | Diagnóstico inicial y cierre con limitaciones |

No se modificaron dependencias, datos productivos, malla, fórmula de compensación, journal start_attempt ni MotionContext.


## Commits locales de implementación

- `33c7498`: observación HTTP fresca y residuo numérico de velocidad; evidencia de reposo.
- `c79cb5f`: separación homing/preparación, perfiles y UI con bloqueos explicados.
- `ec45377`: rápidos Legacy bajo política de clearance compartida y exclusiones preservadas.

El commit documental siguiente contiene este cierre. El árbol queda limpio tras él. No se realizó push, merge ni deploy. El resultado es GO para auditoría externa del software y NO-GO para acelerar el archivo real sin resolver los bloqueos físicos descritos.

## Corrección de auditoría externa — persistencia de aproximación larga

Base de esta revisión: `9fb0e815c99d99a40eeea2139c172c8403c863a0`, misma rama y worktree. Solo se corrige el contrato de configuración; los resultados anteriores describen el paquete original.

**Causa:** machine_settings exponía únicamente el número efectivo. El frontend lo reenviaba al guardar cualquier ajuste, convirtiendo una herencia en un override sin intención del operador. Además, el PUT ignoraba null, impidiendo restablecer herencia.

**Contrato corregido:**

- `settings.long_tool_reference_approach_z_mm = null` significa herencia; un número significa override explícito. GET, PUT y snapshot mantienen esa distinción.
- Omitir el campo en PUT conserva la configuración actual. Enviar null elimina el override de la persistencia. Al recargar un archivo antiguo o sin ese campo, el modo es heredado. El alias antiguo de clearance no se reinterpreta como aproximación.
- La Z efectiva continúa en `preparation.long_tool_reference_approach_z_mm`, calculada con la configuración vigente; no se altera la resolución física de alturas.
- Referencias permite elegir “Heredar Z estándar” o “Usar altura personalizada”. En herencia, el campo es read-only y muestra la Z estándar editada; se muestra el valor efectivo y la necesidad de guardar/confirmar antes de mover. Al elegir explícitamente personalizar se propone el valor estándar ya presente, sin crear una altura arbitraria ni guardar automáticamente.
- Guardar otro ajuste reenvía null cuando corresponde; nunca materializa la altura heredada como override. Volver a herencia envía null explícito.
- La confirmación compara configuración guardada y snapshot, incluyendo null versus número. Coincidir numéricamente hoy no basta si el modo difiere. Un snapshot sin el campo o sin settings completos no confirma configuración para movimiento; se retiró el fallback que infería coherencia a partir de alturas efectivas solamente.
- Los archivos antiguos siguen funcionando con el backend actual. Un cliente/backend antiguo que solo exponga alturas efectivas no ofrece prueba suficiente del modo y permanece bloqueado en la UI nueva hasta recibir un contrato completo.

**Regresiones:** tests de preparación verifican cambio de estándar, guardado de otro ajuste, independencia explícita, eliminación en disco/relectura y alias antiguo. Test API distingue PUT parcial, valor y null, y valida el snapshot. Frontend verifica selector, campo heredado read-only, altura efectiva, payload null/número, vuelta a herencia y bloqueo con modo ausente o override numéricamente igual.

**Resultados de esta revisión:**

- Backend focalizado: 14/14 PASS.
- Frontend focalizado: 95/95 PASS.
- Backend completo: 739/739 PASS.
- Frontend completo: 185/185 PASS.
- Lint: PASS.
- Build: PASS.
- Diff check: PASS.

Pruebas ejecutadas en el worktree con MACHINE_MODE=simulated y MACHINE_AUTO_CONNECT=false para backend; frontend usa mocks. Sin servicios productivos ni transportes físicos. No se requiere una prueba física para verificar este contrato de serialización/UI; las validaciones físicas pendientes del paquete original permanecen pendientes.

Archivos: runtime y esquemas/rutas ajustan nulabilidad/persistencia; api/types frontend transportan null; ProjectWorkspace conserva y confirma el modo; ReferenceWorkspace ofrece selector y valor efectivo; tests y este informe documentan la regresión. Sin cambios a MotionContext, JobRun, Legacy, mapas, compensación ni secuencias de movimiento.
