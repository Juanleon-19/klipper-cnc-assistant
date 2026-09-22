# Corrección de referencia → ejecución y rendimiento

Rama: `review/reference-to-execution-2026-09-19`. Trabajo exclusivamente aislado,
sin modificación de producción, push, merge, servicios reales ni movimiento.
Complementa la [revisión inicial](reference-to-execution-2026-09-19.md).

## Corrección de evidencia

La captura combinada guardaba XYZ, pero no el token que ejecución exige. Armar
creaba otra entrada numérica y completar la malla reemplazaba los metadatos de
captura. Además, `_reference_status` ignoraba el binding recibido.

Ahora captura persiste conjuntamente la referencia canónica y un token v2, nacido
de la observación de sonda bajo ownership, con el instante original. Armar añade
un binding auditable mapa/perfil → hash del token sin modificarlo ni reasignar
herramienta. Finalizar conserva origen, instante e instalación de esa medición.
Se diferencian perfil efectivo de captura y configuración posterior de malla;
ambos quedan protegidos. Los tokens v1 conservan su validación anterior.

Recorrido probado: API de captura con transporte físico falso → armar → completar
puntos → mapa VALID → JobPlan → JobRun JOB_READY → start → generación Legacy JIT
→ adaptador falso. `_initial_reference_binding` selecciona la entrada de la misma
herramienta; `_reference_entry` no crea alias; `_reference_status` valida su token
esperado; `_tool_installation_calibration` lo transmite al run. Start/resume y JIT
siguen pasando por `_require_run_reference` / `_require_operation_reference` y
`require_current_reference` contra el estado vigente.

La igualdad del token incluye sesión física/ownership, proyecto/setup/cara,
herramienta/perfil, placement, referencia/contexto activo, instalación, XYZ,
measured_at y reference_revision. La guardia v2 comprueba también la igualdad
con el registro canónico. Z idéntica sin token, otra sesión, otra herramienta,
campos alterados, cambio de placement/perfil y pérdida de evidencia canónica
siguen bloqueados. Un cambio de sesión/configuración durante el sondeo no publica
evidencia. Los mensajes muestran la causa real, no «Falta referencia Z» cuando
existe una medición sin evidencia válida.

## Rendimiento y método

Misma copia congelada privada del proyecto que reprodujo el problema: JSON de
27 MB, cinco operaciones y mapa activo. El benchmark copia esos datos otra vez
a un temporal; nunca escribe en el origen. No arranca conexiones físicas.
El historial `generated/` NO se incluyó: la comparación no cuantifica el coste de
verificar su historial completo. Los artefactos se cubren con regresiones
sintéticas de identidad/hash y persistencia.

El coste dominante era reconstruir/serializar análisis por cada carga completa,
no ejecutar el parser. Preview reutiliza un proyecto; armado guarda solo el
setup con CAS estricto y escritura durable, preservando los análisis JSON.
Los helpers de plan comparten proyecto/mapa/operaciones resueltos.

La identidad del plan usa contenido/revisión del proyecto, operaciones/orden/
configuración, SHA256 de los originales, versiones, mapa y token, artefactos,
configuración física y sesiones. Cache hit evita reconstrucción. Cambios durante
build impiden publicar. Lecturas no escriben planes. Ambas cachés internas están
acotadas a ocho entradas; la de metadatos no retiene los análisis y verifica el
hash de bytes en cada lectura. Start/resume/JIT mantienen sus guardias frescas.

Los originales CRLF se identifican por sus bytes, sin confundir el hash con el
texto normalizado del parser. Subir archivo, cambiar versión o modificar material
invalida el análisis correspondiente. Los cinco análisis v2 de esta copia están
obsoletos: la primera construcción los reanaliza en memoria; no se falsea un
cache hit ni se migra el proyecto mediante GET.

| Acción | Antes (s) | Después (s) | Cargas de proyecto antes → después |
| --- | ---: | ---: | ---: |
| Vista previa | 5.0925 | 2.5444 | 2 → 1 |
| Primera lectura del plan | 17.1965 | 5.1689 | 7 → 1 |
| Preparar tras leer plan | 17.3781 | 0.1796 | 7 → 0 |
| Preparar repetido | 19.5273 | 0.1759 | 7 → 0 |
| Armar mapa nuevo | 15.0967 | 5.8440 | 3 → 1 |

Preparar **sin lectura previa ni caché**: 5.1957 s, una carga,
una construcción y cinco análisis obsoletos actualizados en memoria. El valor
de 0,18 s corresponde a reutilización; no incluye la primera construcción.

### Contadores y tiempos acumulados

Cada celda muestra llamadas / segundos inclusivos. No sumar tiempos anidados.
Los ceros significan ausencia de llamadas a esa función, no ausencia de I/O:
los cache hits todavía verifican contenido, mapas y archivos fuente.

| Función | Armar antes | Armar después | Preparar antes | Preparar después |
| --- | ---: | ---: | ---: | ---: |
| project_load | 3 / 5.8344 | 1 / 2.2882 | 7 / 17.1852 | 0 / 0.0000 |
| analysis_deserialize | 25 / 2.7256 | 5 / 0.9162 | 35 / 7.1780 | 0 / 0.0000 |
| analysis_serialize | 25 / 4.4606 | 5 / 0.4066 | 35 / 2.7742 | 0 / 0.0000 |
| plan_build | 0 / 0.0000 | 0 / 0.0000 | 1 / 17.3571 | 0 / 0.0000 |
| coverage | 0 / 0.0000 | 0 / 0.0000 | 1 / 0.1673 | 0 / 0.0000 |
| artifact_scan | 0 / 0.0000 | 0 / 0.0000 | 1 / 0.0003 | 0 / 0.0000 |
| artifact_validation | 0 / 0.0000 | 0 / 0.0000 | 0 / 0.0000 | 0 / 0.0000 |
| active_map_load | 0 / 0.0000 | 0 / 0.0000 | 1 / 2.0059 | 0 / 0.0000 |
| plan_load | 0 / 0.0000 | 0 / 0.0000 | 1 / 0.0010 | 0 / 0.0000 |
| plan_save | 0 / 0.0000 | 0 / 0.0000 | 1 / 0.0061 | 1 / 0.0067 |
| gcode_analysis | 0 / 0.0000 | 0 / 0.0000 | 0 / 0.0000 | 0 / 0.0000 |

En la primera lectura de plan: parser antes 0, después 5 (análisis v2 obsoleto);
cobertura 1 → 1; barrido de artefactos 1 → 1. En preparar, ambos pasan 1 → 0
con reutilización. Validación de candidatos de artefactos: 0 en esta copia.
El contador incluye ahora también la entrada del analizador usada por JobService.

Frontend: preview y armar ya tenían control de solicitud/cancelación. Abrir
proyecto solicita plan y live; preparar hace POST seguido de refresco plan/live.
Ahora las lecturas GET idénticas solapadas comparten la solicitud. Las respuestas
resueltas no se cachean y una mutación invalida las solicitudes compartibles
anteriores. Se conserva el polling de telemetría. Prueba controlada: dos lecturas
solapadas de plan y dos de live producen dos requests, en vez de cuatro.
Navegador: cuatro GET plan en dos aperturas + prepare + start; un POST prepare y
uno start, sin respuestas HTTP 4xx/5xx inesperadas. No se atribuyen a este cambio
los tiempos de red de la máquina real.

## Validación y alcance

- Backend completo final: **609/609 PASS**, tras el ajuste CRLF.
- Backend focalizado inicial: 125/125; metadatos/API: 47/47.
- Regresión posterior de CRLF, referencias, números y proyectos: 50/50.
- Frontend: 144/144; lint y build PASS. Warning de tamaño de bundle preexistente.
- API E2E: JOB_READY → JOB_STARTING → JIT/adaptador falso, token idéntico.
- Navegador Firefox aislado: referencia y mapa visibles, preparación muestra
  «Referencia inicial: LISTA», start aceptado por el backend falso; viewports
  1440×900 y 390×844 sin desbordamiento horizontal. No se ejecutó hardware.
- Los tests conservan ownership, MotionAuthorizer, CAS/cancel, PrintIdentity,
  persistencia, generaciones de mapa, rampas, signo, feeds y política G20/G21/G2/G3.
- El diagnóstico offline de ocho trayectorias y retención acotada pasa: error
  máximo 4,45e-16 mm, error de endpoint 0; sin NaN/inf, extrapolación ni inversión
  inesperada. `git diff --check`: PASS.

La prueba positiva usa G-code sintético con feed modal explícito. Los cinco
archivos de la copia tienen feed inicial y no presentan errores críticos de
análisis. No se amplía el soporte CNC ni se declara validación física.
Al desplegar/reiniciar posteriormente habrá que capturar una referencia válida
en la nueva sesión; las referencias históricas no se reactivan por tener la misma Z.

Reproducción del benchmark: `experiments/reference_execution/benchmark.py`;
`--cold-prepare` mide la variante sin lectura previa. Datos, capturas y logs
operativos permanecen en temporales privados y no se incluyen en Git.

Comandos de validación (todos los Python con `MACHINE_MODE=simulated`,
`MACHINE_AUTO_CONNECT=false`, `PYTHONPATH=src:.`):

```text
python -m unittest discover -s tests -v
npm run lint
npm test
npm run build
python tests/product_fix_diagnostics.py /tmp/kca-reference-final/diagnostics
git diff --check
```

**REFERENCE_TO_EXECUTION_FIXED = YES**
**PHYSICAL_REFERENCE_TOKEN_PRESERVED = YES**
**INVALID_REFERENCE_STILL_BLOCKS = YES**
**MISLEADING_REFERENCE_MESSAGE_FIXED = YES**
**NEW_BLOCKERS = []**
**READY_FOR_PHYSICAL_RETEST = YES**, tras despliegue autorizado y nueva captura de
referencia en la sesión física correspondiente. Esta revisión no despliega.
