# Cierre final de software — 12–13 de septiembre de 2026

Base: `3ca3ff404a7896506305a8a5c14f9e40cfcc411a`.
Rama local: `release/final-software-stabilization-2026-09-12`.

## P1 cerrados

| ID | Resultado y autoridad |
| --- | --- |
| P1-11a | Check/apply de settings mediante revisión y lease del `PhysicalMachineCoordinator` existente; un owner intercalado impide aplicar cambios. |
| P1-11b | Schemas, configuración y feeds de runtime rechazan valores no finitos y valores no positivos cuando corresponde; NaN/inf HTTP devuelve 422, no 500. |
| P1-05 | Standby remoto contradictorio dispone de tolerancia de dos segundos, compartida por watcher y live; después entra en recovery mediante `JobRunStore`. |
| P1-09b | Un probe histórico necesita evidencia de sesión, contexto, configuración, frame y antigüedad de su propio `captured_at`; una lectura HTTP reciente no rejuvenece la medición. |
| P1-01 | GET de JobRun ausente devuelve null sin crearlo; preparar es una acción POST explícita. |
| P1-03 | Lecturas normales de proyecto, repositorio, plan y mapas no publican dominio. Apertura, generación de plan, normalización legacy y finalización son acciones explícitas. La reconciliación de ejecución live sigue siendo supervisión deliberada. |
| P1-08 | Confirmaciones humanas de spindle guardan run, operación y contexto físico existente; se revalidan antes/después de JIT y antes de start/resume. No son sensores ni comandos de spindle. |
| P1-10 | Documentación distingue una implementación HTTP de varias instancias y sesiones independientes; no promete una conexión TCP única. |

## Validación automatizada final

Todos los comandos backend se ejecutaron con `MACHINE_MODE=simulated` y
`MACHINE_AUTO_CONNECT=false`, desde esta rama, usando el Python del virtualenv.

- Focalizados de cierre y workflow HTTP: 18/18.
- `python -m unittest discover -s tests -v`: **496/496**, 213,925 segundos.
- Frontend `npm run lint`: PASS.
- Frontend `npm run test -- --run`: **136/136**, 16 archivos.
- Frontend `npm run build`: PASS.
- `git diff --check`: PASS.

La suite final incluye las regresiones de freshness, ownership, process lock,
serial/hotplug/exclusive, JobRun CAS/cancel dominance, PrintIdentity, mesh worker,
PhysicalReferenceToken, persistencia segura, feeds FlatCAM, velocidades auxiliares
y tool change. Los tests antiguos se adaptaron a prepare y confirmación humana
explícitos; no se eliminaron las garantías P0.

## Aplicación real aislada

Se levantó el backend sirviendo el build final en localhost, puerto 18764, con
directorio de datos temporal y modo simulated sin auto-connect. Los destinos
Moonraker se sustituyeron por localhost sin servidor y el puerto serial quedó
vacío. No se detuvo ni modificó ningún servicio real.

`tests/simulated_app_smoke.py` ejecutó 30 solicitudes HTTP: creación y apertura,
setup/cara, G-code artificial con F120/F240, análisis, referencias manuales
simuladas, mapa simulado, preview matemático de compensación Legacy, plan,
prepare explícito, bloqueos de ejecución/probe, settings y cancelación terminal.
Comparaciones de todos los bytes del proyecto comprobaron que GETs normales no
escriben ni crean JobRun.

Firefox headless 155.0.1 con geckodriver local probó la UI real: creación de
proyecto/operación, carga y análisis, referencia y mapa simulados, navegación a
ejecución, apertura explícita y polling sin escrituras. En el build final se
verificaron refresco tras acciones de referencia, ausencia de observación
Moonraker correctamente etiquetada, prepare bloqueado y cancelación reflejada
por polling. WebDriver BiDi no registró errores JavaScript ni HTTP 500 durante
esta comprobación final.

## Límites exactos y auditoría corta

El workflow nativo completo es **PARTIAL**: el simulador permite preparación y
compensación preview, pero no ofrece impresión Moonraker, probe físico ni worker
de cambio de herramienta ejecutable. No se fabricaron tokens físicos a partir
de referencias manuales simuladas. El historial usa los pointers de preparación
física; un mapa de diagnóstico simulado no establece un active map físico.

Cuatro tests HTTP con las rutas reales, workers reales y fronteras existentes
FakeRuntime/FakeAdapter verificaron por separado: secuencia multi-herramienta,
probe/token → READY_TO_RESUME sin auto-start → confirmación humana → JIT/upload/
start, pause/resume/cancel con identidad correcta, archivo incorrecto → recovery
sin resume y cancel durante JIT → cero upload/start. Son pruebas con fakes, no
impresión ni medición física. Errores stale telemetry, owner conflict y referencia
inválida siguen cubiertos por las regresiones deterministas de la suite.

No se encontraron bloqueadores nuevos ni P0 reabiertos. La revisión de rutas
confirmó upload sin print, start separado autorizado, writers JobRun mediante
store/CAS, next/all mediante el mismo mesh service y token físico vigente antes
de continuar. La única escritura `write_text` restante en storage/application/
execution es el archivo temporal de disponibilidad del repositorio, ajeno al
dominio persistente. No se cambió la fórmula Legacy ni los feeds FlatCAM.

Hallazgos no bloqueantes: advertencia existente de tamaño del bundle frontend y
deprecación existente de eventos startup/shutdown de FastAPI. No justifican un
refactor en este cierre.

Software listo para la siguiente etapa: **validación física realizada con el
usuario**. Esta fase no valida el comportamiento real de la CNC. No hubo push,
merge a main, comandos físicos ni reinicio de servicios reales.
