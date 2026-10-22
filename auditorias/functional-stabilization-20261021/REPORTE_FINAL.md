# Candidata de estabilización funcional

Base: `83ebcde64e946e61d08d178e537dd17c0db97b67`.
Rama: `fix/functional-stabilization-20261021`.
Worktree: `kca-worktrees/functional-stabilization-20261021`.
No se modifican producción, servicios, hardware ni dependencias.

## Causas confirmadas

`AdministrativeGuard` fechaba la observación antes de consultar Moonraker y
aplicaba después su límite de edad a todas las escrituras. Dos regresiones con
reloj controlado reprodujeron el mismo error: respuesta HTTP de 3 segundos y
escritura de histórico de 3 segundos. La segunda fallaba después de escribir el
histórico, dejaba el journal pendiente y conservaba current_run.
No fue necesario usar datos de mecanizado reales para reproducirlo.

## Corrección

- Fecha de recepción después de la consulta real; no se rejuvenecen respuestas
  cacheadas ni se modifican los timeouts físicos.
- `check()` verifica reserva, sesión, MovementLock y ausencia de operación local;
  ya no trata la duración del filesystem como frescura de posición.
- `observe_before_commit()` vuelve a consultar y validar Moonraker en fronteras
  de mutación, comprobando la reserva antes y después del HTTP.
- Se vuelve a validar la identidad persistida de cada ejecución contra esa nueva
  respuesta: nombre distinto, desconocido e inicio ambiguo siguen bloqueados.
- Hay observación antes del journal y después de su escritura, antes de modificar
  dependencias, antes de desconectar el runtime y antes de retirar runs cuando
  todavía existe transporte. Tras la desconexión administrativa autorizada solo
  se finaliza la persistencia local bajo reserva/CAS; no se reconecta hardware.
- Las rutas administrativas no persistentes también observan antes del handler.
  La administración independiente/offline conserva su contrato separado y no
  realiza consultas físicas adicionales.

No se cambia MotionAuthorizer, JIT, compensación, perfiles, referencias, ownership,
CAS, start_attempt, confirmaciones humanas ni navegación. El reset sigue siendo
una acción explícita; no se ejecuta automáticamente contra el trabajo anterior.

## Persistencia y límites

El histórico precede a los cambios de dependencias. Current_run solo se retira
cuando los callbacks terminaron y las revisiones/conjunto de runs siguen siendo
los esperados. Un fallo real posterior a una escritura mantiene el journal y
recovery; no devuelve éxito ni inventa rollback. La prueba de cambio de archivo
remoto después de escribir histórico verifica current_run idéntico byte por byte.

No se declara atomicidad distribuida con Moonraker ni entre todos los archivos.
Una reserva local no controla otros clientes físicos. Las observaciones son
comprobaciones puntuales y no permisos de movimiento. Los controles estrictos de
reposo/homing del reset físico se conservan; abrir/editar proyectos independientes
sigue usando la autorización offline existente. Ninguna acción administrativa
produce homing, reanuda una impresión o hace válida una referencia histórica.

## Recorrido integrado

Una aplicación FastAPI y un repositorio temporal, con MachineRuntime,
MoonrakerJobAdapter, sondeo, workers, persistencia y JIT reales. Solo HTTP/WS/serial
están sustituidos por transportes en memoria. El transporte simulado interpreta
coordenadas relativas, emite muestras estacionarias nuevas y contacto de sonda;
no se sustituyen los guards ni los estados de dominio para hacer avanzar el flujo.
La ejecución histórica inicial es una fixture sintética con evidencia de término.

El navegador utiliza React compilado y botones reales. No intercepta respuestas
frontend ni llama a endpoints productivos. El mismo recorrido se valida por API:

| Fase | Resultado |
|---|---|
| Abrir proyecto y montaje | PASS |
| Reiniciar histórico completado, standby con filename vacío | PASS |
| Conservar histórico y desconectar runtime | PASS |
| Conectar transportes simulados | PASS |
| Homing XYZ independiente | PASS |
| Preparar despeje Z y centro | PASS |
| Habilitar joystick y colocar XY con paquete simulado | PASS |
| Guardar conjuntamente XY y Z, sin captura XY adicional | PASS |
| Cobertura incorrecta rechazada; corregir márgenes sin crear mapa inválido | PASS |
| Preview → validación → armado → worker de malla | PASS |
| Malla completa/VALID y cierre del worker | PASS |
| Recargar página, reabrir proyecto y conservar datos | PASS |
| Preparar → confirmación spindle → JIT → upload → start | PASS |
| A → A reutiliza instalación y genera nuevo JIT | PASS |
| A → B exige spindle detenido e instalación confirmada | PASS |
| Retorno al origen del mapa, nueva Z, confirmar continuar | PASS |
| Tres operaciones terminadas, tres starts únicos | PASS |

El fixture usa avances explícitos del archivo sintético. No inventa un feed para
un traslado conservador sin avance modal: ese archivo continúa rechazado por el
validador ejecutable. Las pruebas físicas siguen pendientes y no están autorizadas.

## Pruebas

- Reproducción previa: 2/2 fallos esperados con el mensaje original.
- Focalizadas administración/latencia/identidad/offline: 45/45 PASS.
- Backend completo inicial: 815/815 PASS.
- Backend completo final (incluye recorrido nuevo): 816/816 PASS (402,159 s).
- Variante integral con rechazo de cobertura, corrección y continuación: PASS
  por API y navegador, ejecutada adicionalmente después de ampliar esa regresión.
- Frontend completo: 185/185 PASS.
- Lint: PASS.
- Build: PASS; aviso existente de tamaño de bundles, sin error.
- API E2E existentes: 4/4 PASS.
- Recorrido integrado API (tres operaciones): PASS.
- Browser E2E malla: PASS.
- Browser E2E ejecución: PASS.
- Browser E2E integral React + backend + transportes simulados: PASS.
- Diff check: PASS.

Las regresiones incluyen impresión activa, identidad diferente/desconocida,
shutdown, ausencia de homing, velocidad inválida, fallo HTTP, ownership/recovery,
sesión cambiada, CAS/cancelación y fallos parciales. Se conserva la negativa a
repetir un start ambiguo. No se rebaja ningún guard para pasar la prueba integral.

Comandos: `MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. PYTHONWARNINGS=ignore python -m unittest
discover -s tests -q`; `npm run test -- --run`; `npm run lint`; `npm run build`;
los scripts `tests/mesh_workflow_browser.py`, `tests/execution_lifecycle_browser.py`
y `tests/functional_journey_browser.py` con el mismo entorno seguro;
`git diff --check`. Intérprete y dependencias existentes, sin reinstalación.

## Integridad y continuidad

Baseline privado de 788 archivos de data y paths locales protegidos: sin
modificaciones durante el desarrollo. No se publica el manifiesto ni información
productiva. El índice Graphify pertenece a otro commit y no se utilizó ni escribió.
`docs/functional-continuity.md` recoge el contrato y las regresiones; AGENTS exige
su lectura únicamente en esta rama. AGENTS productivo permanece intacto.

Comprobación final: 788/788 hashes idénticos. La candidata se entrega en un único
commit, identificable con `git log -1` en esta rama; el informe está incluido en
ese mismo commit. No hay bloqueadores de software pendientes en el alcance
validado. No hay autorización de despliegue ni validación física derivada de este
informe.

## Archivos modificados

- `execution/administrative_guard.py`: separa observación fresca y guardia local.
- `execution/job_service.py`: helper de observación y fronteras de reset/archivado.
- `api/routes.py`: observación anterior a handlers físicos y desconexión del reset.
- `tests/test_administrative_exclusion.py`: reemplaza una prueba del timeout de
  transacción por el contrato de observación nueva fallida antes de mutar.
- `tests/test_administrative_latency.py`: latencia HTTP/disco, fecha de recepción,
  cambio de identidad después del histórico y conservación de journal/current_run.
- `tests/test_functional_journey.py`: recorrido de aplicación completo con tres
  operaciones y transportes simulados; no mocks de las funciones de negocio.
- `tests/functional_journey_browser.py`: el mismo recorrido mediante botones React,
  diálogos y confirmaciones en Firefox, incluida recarga y error recuperable.
- `docs/functional-continuity.md` y `AGENTS.md`: contrato breve y lectura obligatoria.
- Este informe: evidencia, alcance y límites.

CANDIDATA FUNCIONAL VALIDADA — exclusivamente en simulación.
