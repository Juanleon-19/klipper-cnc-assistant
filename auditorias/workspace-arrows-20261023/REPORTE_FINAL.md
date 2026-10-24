# Navegación lateral y latencia de sondeo

Base: `1e849c8565dad7899b43d1e3def26eecd603c156`.
Rama: `feat/workspace-arrows-20261023`.
Worktree: `kca-worktrees/workspace-arrows-20261023`.

## Sondeo: conservar lo que funciona

No se modifica Python productivo, parámetros físicos, filtros, tiempos, velocidades,
serial, límites, ownership, CAS ni comportamiento de referencia/malla.

Los logs existentes muestran respuestas exitosas de probe-and-capture y execute-all,
pero Uvicorn registra finalización de respuesta, no clic, entrada y primera emisión.
No permiten calcular la demora física por etapa. La causa dominante real queda
NO DETERMINADA; no se atribuye a Arduino, red ni filesystem sin medición.

El handler React remeasurePhysicalReference llama directamente a la API tras guards
locales; no hay timer/debounce/espera HTTP previa. En referencia, reference_service
adquiere ownership, obtiene observación real, construye contexto con archivos,
confirm_probe obtiene una segunda observación cercana al movimiento y exige OPEN
fresco/estable antes de MotionContext y descenso. La consulta posterior al trabajo
de contexto no se elimina: protege la frescura después de IO potencialmente lento.

La malla usa worker, fingerprint/referencia, carga/persistencia y autorización por
punto; el primer movimiento puede ser traslado, no descenso. No se presupone que
su demora sea la misma que referencia. Los checks serial son inmediatos si hay
evidencia válida. _require_fresh_open_probe comprueba cada 10 ms hasta un máximo
de 1 s; ese máximo no es una pausa fija. El umbral de estabilidad por defecto es
50 ms y requiere una muestra nueva cuando corresponde. No se elimina esta espera.

### Medición exclusivamente simulada

`tests/probe_latency_diagnostic.py` usa FastAPI/runtime/servicios reales y los
transportes en memoria del recorrido integral. Cronometra TestClient→transporte
fake, no la red física ni el clic humano. Duraciones de wrappers son inclusivas,
no sumables como fases disjuntas. La fixture introduce cadencia simulada.

| Medición | Referencia Z | Malla |
|---|---:|---:|
| Inicio de solicitud simulada → primera emisión fake | 10,141 ms | 190,114 ms |
| Respuesta HTTP simulada | 6329,534 ms (incluye sondeo) | 124,009 ms (worker asíncrono) |
| Refresh runtime antes de primera emisión | 2 / 0,175 ms | 2 / 0,323 ms |
| Construcción de contexto de referencia medida | 0,538 ms | no instrumentada separadamente |
| Checks serial antes de primera emisión | 2 / 0,007 ms | 2 / 0,015 ms |
| Espera OPEN estable antes de primera emisión | 0,027 ms | posterior al primer traslado |

No se extrapolan estos números a CNC real. No se demostró una optimización segura
y equivalente: SONDEO SIN CAMBIOS. Para medir latencia física posteriormente harían
falta tiempos de navegador/entrada HTTP/primera emisión correlacionados, durante
una prueba autorizada; no se realiza ni se requiere para esta entrega.

## Navegación

Una lista ordenada compartida por pestañas superiores y flechas. Las flechas llaman
únicamente setActiveView; reutilizan el mismo efecto de localStorage y contexto
proyecto/montaje/operación/cara. Sin handlers de movimiento, reset, preparación ni
confirmaciones nuevas. aria-pressed identifica la pestaña superior seleccionada;
flechas tienen nombres accesibles con el destino y extremos deshabilitados.

Dos columnas laterales propias de 44 px y botones sticky mantienen acceso durante
scroll sin superponer contenido. En tableta/móvil se reduce el espacio entre
columnas, conservando el área táctil de 44 px; no se usan overlays ni atajos globales
que interfieran con joystick. Se preservan formularios de referencia en estado
parental; prueba de borrador Z no guardado al salir/volver. La selección de archivo
existente dispara upload al seleccionar, no hay un nuevo borrado de draft.

## Validación

- Frontend completo: 200/200 PASS.
- Backend sonda/referencia unificada: 22/22 PASS.
- Diagnóstico de latencia y recorrido integrado API: PASS, transportes falsos.
- Browser React+FastAPI: recorrido completo PASS; navegación conectada en 1440,
  900 y 390 px, cuatro transiciones en cada sentido, extremos, recarga y
  conservación de selección; sin nuevas emisiones en el transporte durante navegar.
- Verificación de no superposición, acceso durante scroll y capturas: PASS.
  Viewports CSS reales 1440/900/390 comprobados con innerWidth. Firefox desktop
  impone un ancho mínimo de ventana; se usa un iframe de tamaño exacto que carga
  la aplicación real, sin cambiar sus respuestas. No sustituye una prueba táctil
  en dispositivo físico.
- Lint: PASS.
- Build: PASS (advertencia existente de bundle grande).
- Diff check: PASS.

La primera prueba frontend falló por una consulta regex que ahora coincidía con
pestaña y flecha; se acotó el selector al nombre exacto de la pestaña. El primer
instrumento de latencia no veía el descenso porque interceptaba un wrapper de
runtime y no el transporte del jog; se corrigió el instrumento, no el sondeo. La recarga del iframe requirió
resincronizar WebDriver con el documento nuevo antes de abrir el proyecto;
no se cambió la persistencia de pestañas para resolverlo.

## Alcance

Archivos productivos cambiados: ProjectWorkspace.tsx (navegación compartida),
styles.css (columnas y controles). Tests: ProjectWorkspace.test.tsx,
functional_journey_browser.py y probe_latency_diagnostic.py. Este informe.

No producción, datos, JobRun, servicios, hardware, dependencias ni sistema operativo
modificados. No despliegue ni push. Validación física pendiente, no declarada PASS.

## Comandos reproducibles

Con el Python existente, desde el worktree y con
`MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONDONTWRITEBYTECODE=1 PYTHONWARNINGS=ignore PYTHONPATH=src:.`:

- `python -m unittest tests.test_probe_and_capture tests.test_unified_mesh_probe -q`
- `python tests/probe_latency_diagnostic.py`
- `python tests/functional_journey_browser.py` (puertos locales aislados 18917/18918, datos temporales).

Desde frontend, dependencias existentes sin reinstalar:
`npm run test -- --run`, `npm run lint`, `npm run build`.
Desde raíz: `git diff --check`.
Suite backend completa: NOT_RUN; no cambia backend productivo.
