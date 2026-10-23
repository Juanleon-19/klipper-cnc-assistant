# Hotfix: posición de preparación

Base: `89ce1a657c90a7a9dc05eecc1d7e4f60b10afdde`.
Rama: `fix/preparation-button-20261022`.

## Diagnóstico focalizado

GET `/api/machine/status` llama exclusivamente `runtime.snapshot()`: no refresca
transportes ni persiste. La lectura autorizada mostró HOMED, ready, homing xyz,
serial reciente, ownership IDLE, recovery false y active_operation null. La muestra
real de posición tenía aproximadamente 144 segundos: telemetry_recent=false.
No existía current_run en la lectura de almacenamiento. Los logs del intervalo
inspeccionado registraban homing exitoso y ninguna petición prepare-position.

`canPrepare = !toolChangeBlock` heredaba el rechazo de telemetría cacheada stale.
Esto basta para deshabilitar el botón en frontend, sin invocar handler ni backend
(escenario A). No hay evidencia de rechazo backend en el incidente. Los flags
referenceBusy/refreshing/ediciones locales del navegador del operador no están
expuestos por el GET: no se afirma haberlos observado. Sus combinaciones se
cubren en React. El guard de configuración y el retorno anticipado del handler
se conservan; ahora sus bloqueos son visibles junto a preparación.

## Cambio mínimo

- ReferenceWorkspace: autorización advisory propia para preparación, independiente
  del botón de cambio. Conserva homing, conexión/ready, recovery, ownership,
  JobRun, coherencia de ajustes y acciones en curso. Caché stale permite solicitar
  observación backend; se explica que no autoriza movimiento por sí misma.
- App: propaga al consumidor el rechazo de prepare-position; antes lo absorbía
  mostrando únicamente el error global.
- ProjectWorkspace: conserva y presenta ese error en el paso de preparación.
- No se modifica backend, MotionAuthorizer, timestamps ni timeouts. El runtime
  existente refresca HTTP antes de preparar, verifica homing y autoriza cada
  movimiento. No se repite homing. Falta de observación válida sigue bloqueando.

## Regresiones

React: petición única con caché fresca/obsoleta; mensajes para recovery, trabajo
activo, operación incompatible, homing incompleto, ajustes pendientes, busy,
refreshing y desconexión; error de servidor junto al botón.

Backend: separación home/preparación, perfiles, límites, frescura de muestras XYZ
idénticas, errores de observación sin emisiones. Recorrido FastAPI completo con
caché envejecida y nuevos datos HTTP reales del transporte fake.

Navegador: React compilado y FastAPI con datos temporales y transportes falsos.
Suspende explícitamente muestras WS del simulador para reproducir caché stale;
inyecta un rechazo HTTP de preparación, verifica su mensaje local y reintenta
explícitamente. Solo una petición exitosa de preparación. Continúa referencia
XY/Z, malla y ejecución simulada con cambio de herramienta. La primera ejecución
falló porque el simulador renovaba XYZ cada 50 ms; se corrigió la fixture con
señal y acuse de suspensión, sin modificar comportamiento productivo.

## Validación

- Frontend completo: 196/196 PASS.
- Backend runtime/preparación/telemetría: 90/90 PASS.
- Recorrido backend completo: 1/1 PASS (39,791 s).
- Browser integrado: PASS, incluyendo rechazo visible, stale y reintento explícito.
- Lint: PASS.
- Build: PASS; advertencia existente de tamaño de bundles.
- Diff check: PASS.

No hardware, reinicios, despliegue, modificación de datos ni publicación remota.
La inspección productiva fue de lectura. No se usó Graphify productivo ni se
modificó su caché. Suites completas backend no repetidas: no cambia Python
productivo; se ejecutan las regresiones pertinentes y el recorrido integrado.

## Instalación posterior

Tras revisar el commit y autorizar mantenimiento: confirmar aislamiento, guardar
baseline privado, conservar guardia y cambios locales, detener solo Assistant,
verificar salida, aplicar fast-forward local, publicar el frontend de este
worktree (sí cambia), arrancar solo Assistant con autoconexión desactivada y
comprobar hashes/HTTP/JobRun. No ejecutar preparación durante el despliegue.
La prueba física del botón requiere autorización y supervisión posteriores.
