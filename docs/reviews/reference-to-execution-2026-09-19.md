# Revisión del recorrido referencia → mapa → ejecución

Estado histórico; la corrección y las mediciones posteriores están en
[el informe de cierre](reference-to-execution-fix-2026-09-21.md).

Fecha local: 2026-09-19. Código revisado: `6bebfd70eadea78f7d72a9919e6ce2d3c96c917f`.

Resultado: **la medición de mapa funciona; la transición normal a ejecución tiene un bloqueo reproducible**. Esta revisión no cambia código de producto, configuración, referencias ni mapas reales. No reinicia servicios y no autoriza movimiento.

## Estado por etapa

| Etapa | Evidencia y resultado |
| --- | --- |
| Joystick | Usuario confirma funcionamiento correcto; fuera de cambios |
| Captura X/Y y Z | El endpoint combinado conserva coordenadas y Z de contacto; pruebas con transportes falsos pasan |
| Vista previa | Geometría correcta; coste excesivo de cargar dos veces el proyecto completo |
| Armar | Persiste el mapa y referencia numérica, pero no evidencia de sesión ejecutable; además es lento |
| Medir y validar mapa | Mapa observado completo, cobertura válida, cero puntos fallidos, worker terminado |
| Preparar ejecución | No hay JobRun preparado en la sesión observada ni POST de preparación en los registros de esa sesión. Reproducir preparación sobre copia aislada con runtime falso listo deja `JOB_VALIDATING`: falla `referencia_inicial` |
| Compensación/JIT | El contrato exige token físico vigente y debe seguir rechazando su ausencia |
| Ejecución y cambios de herramienta | Pruebas existentes alcanzan `JOB_COMPLETE` con cuatro operaciones y dos cambios, usando adaptadores falsos; su fixture añade evidencia de referencia que falta en el recorrido normal |
| Pausa, cancelación y recuperación | Cubiertas por pruebas existentes del servicio; ninguna acción física realizada durante esta revisión |

## Bloqueo demostrado, sin inferir el texto exacto visto por el usuario

1. `ReferenceSessionService.probe_and_capture_reference` guarda `CoordinateReference` X/Y y Z con sesión, pero no `PhysicalReferenceToken`.
2. `PhysicalMapService.plan_from_saved_reference` llama a `capture_reference_and_plan`; `_tool_reference` publica una referencia numérica marcada válida, sin token ejecutable.
3. Completar los puntos y validar cobertura tampoco publica ese token.
4. `require_current_reference` rechaza esa referencia con: **La referencia de herramienta requiere una medición con token de sesión física vigente.**
5. `JobService._reference_status` convierte el rechazo en `REQUIERE_REFERENCIA`. El preflight muestra **Falta referencia Z de la herramienta inicial**, un mensaje insuficiente porque la Z sí existe.

Se reprodujo de dos formas: sobre la referencia guardada (en copia temporal), y desde el endpoint combinado con medición falsa → armado → muestras sintéticas → mapa completo/validado → guardia de referencia de ejecución. No se insertó ni fabricó evidencia en producción.

En la copia del proyecto, las cinco operaciones tienen cobertura suficiente y no muestran bloqueos geométricos. Con un runtime falso listo, nueve checks pasan y solo falla `referencia_inicial`. Esto no afirma que nueve checks físicos estén aprobados en la máquina real.

La cobertura automatizada previa ocultaba la desconexión: `tests/physical_fakes.py::measure_test_tool_reference` llama expresamente a `record_tool_reference(runtime=...)`, que sí crea token. Los tests de captura/armado y los de ejecución validan piezas separadas.

## Rendimiento medido

Copia temporal local de un proyecto con análisis voluminoso; `MACHINE_MODE=simulated`, `MACHINE_AUTO_CONNECT=false`, sin conexión física. Tiempos de servicio, no SLA ni duración exacta de los clics originales.

| Acción | Tiempo | Cargas completas | Tiempo dentro de cargas |
| --- | ---: | ---: | ---: |
| Vista previa, primera | 5,289 s | 2 | 5,210 s |
| Vista previa, segunda | 5,532 s | 2 | 5,446 s |
| Armar un mapa nuevo | 15,918 s | 3 | 7,450 s |
| Construir plan | 18,475 s | 7 | 18,062 s |
| Preparar trabajo, runtime falso | 18,566 s | 7 | 18,171 s |

`JsonProjectRepository.load_project` no solo lee JSON: reconstruye todo el análisis, vuelve a serializarlo y registra un snapshot. El perfil identifica deserialización/serialización de segmentos como coste dominante. La vista previa de pocos puntos no necesita reconstruir varias veces todo ese análisis. El armado añade guardados durables y transformaciones adicionales; no se atribuye todo su coste a lecturas ni a red.

El polling de ejecución sin JobRun también analiza el JSON completo para validar la existencia del montaje. Es trabajo adicional evitable, pero esta revisión no demuestra un bloqueo total del event loop ni una fuga de memoria.

## Correcciones necesarias antes de considerar cerrado el recorrido

1. **Prioridad alta: continuidad de evidencia de referencia.** Conservar prueba de medición real desde captura, vincularla al contexto vigente y hacer que la ruta normal llegue al preflight. No crear tokens a partir de Z histórica ni quitar la validación. Cubrir fin de malla: `_sync_setup_preparation_from_completed_map` reescribe metadatos de origen que participan en el fingerprint; un token creado prematuramente puede quedar invalidado.
2. **Prioridad alta: prueba contractual completa.** Captura real de la API con sonda falsa → armado → finalización → preparación → generación inicial → ejecución falsa hasta terminar. Sin helper que cree el token faltante. Casos negativos: cambio de sesión, configuración, montaje, herramienta y cancelación.
3. **Rendimiento:** reutilizar un proyecto inmutable por operación/solicitud; evitar reconstruir análisis para obtener origen, región o existencia del montaje. Medir proyectos grandes y concurrencia; conservar revisiones, CAS y guardado durable. No añadir caché global mutable sin validación de revisión.
4. **Claridad:** diferenciar mapa válido, referencia numérica guardada y herramienta autorizada para ejecución. Hacer visible el siguiente paso `Preparar trabajo`; explicar la ausencia de evidencia de sesión en vez de anunciar simplemente falta de Z. La preparación no debe comenzar movimiento.

No es una razón para borrar o volver a medir la malla durante el diagnóstico. La reutilización física posterior depende de que se mantengan montaje, origen y contexto; no se declara autorización para mecanizar.

## Validación ejecutada

- Backend focalizado: **123/123 PASS**: `python -m unittest tests.test_probe_and_capture tests.test_physical_integration tests.test_unified_mesh_probe tests.test_physical_reference_token tests.test_job_service -q`, con modo simulado y auto-conexión desactivada.
- Frontend focalizado: **32/32 PASS**: `ProjectWorkspace.job-run.test.tsx`, `ReferenceWorkspace.connection-ui.test.tsx`, `ExecutionConsole.test.tsx`.
- Reproducción adicional del enlace referencia → ejecución: **BLOCKED reproducido**, no presentado como PASS del recorrido completo.
- Perfiles y datos de trabajo permanecen en temporales locales; no se publican mapas, coordenadas, referencias ni G-code de producción.
- Sin modificaciones de producto: no se repitió build ni suites completas; solo comprobaciones relevantes y `git diff --check`.
