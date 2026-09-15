# Revisión final de producto — 13 de septiembre de 2026

> Informe histórico de la revisión inicial. El cierre posterior de los hallazgos
> funcionales está en [correcciones Astra](astra-functional-corrections.md).

Base exacta: `0493571a71597020b06c177872c762becc8afa64`.
Rama: `improvement/astra-final-product-review-2026-09-13`.
Veredicto: **NEEDS_IMPROVEMENT**. La interpolación regular y el signo son correctos;
la preparación habitual funciona, pero quedan discrepancias de trayectoria y
preview que impiden afirmar precisión general del pipeline Legacy.
No es una nueva auditoría P0/P1 ni una validación física.

## Resultado

```text
VISUAL_REVIEW = FINDINGS
INTERPOLATION_CORRECT = YES (malla regular válida; no implica pipeline CNC completo)
HEIGHTMAP_BOUNDARY_POLICY = CLAMP hasta distancia 1e-6 mm; REJECT fuera; sin extrapolación
Z_SIGN_VERIFIED = YES
FLATCAM_FEEDS_PRESERVED = YES (feeds de corte efectivos; precisión F de 0.001 mm/min)
G90 = SUPPORTED
G91 = SUPPORTED (Legacy emite coordenadas absolutas)
G20 = UNSUPPORTED_DANGEROUSLY en cambios inline posteriores a ejes; preámbulo SUPPORTED
G21 = UNSUPPORTED_DANGEROUSLY al volver de pulgadas con ese orden; preámbulo SUPPORTED
G0_G1 = UNSUPPORTED_DANGEROUSLY para rampas Legacy; XY a Z fija y solo Z SUPPORTED
G2_G3 = UNSUPPORTED_DANGEROUSLY para hélices Legacy; I/J XY a Z fija SUPPORTED con discretización;
        R, K, sin I/J o geometría no representable REJECTED_EXPLICITLY en Legacy tras el cambio
TRAJECTORY_CONTINUITY = FAIL global; PASS interpolación/corte horizontal de diagnóstico
NUMERICAL_TESTS = 15/15
MEMORY_LEAK_STATUS = PROVEN_LEAK (retención global de snapshots); pico histórico NO atribuido
SOAK_TEST = PASS (160 ciclos, 3080 solicitudes, 27.128 s; mismo proyecto/mapa)
RSS_INITIAL = 76804 KiB (75.004 MiB, después de 10 ciclos de calentamiento)
RSS_FINAL = 78268 KiB (76.434 MiB)
THREADS_INITIAL = 6 (3 Python)
THREADS_FINAL = 6 (3 Python)
ASYNC_TASKS_INITIAL = 3
ASYNC_TASKS_FINAL = 3
BACKEND_TESTS = 517/517
FRONTEND_TESTS = 137/137
FRONTEND_LINT = PASS
BUILD = PASS
BROWSER_E2E = PASS: preparación nativa simulada + 16 vistas de ejecución con fakes + fixtures de conexión
API_E2E = 30/30 solicitudes del smoke existente
GIT_DIFF_CHECK = PASS
PHYSICAL_COMMANDS = NO
REAL_SERVICES_RESTARTED = NO
MAIN_MODIFIED = NO
PUSH = NO
```

## Hallazgos y decisiones

| Clase | Evidencia e impacto | Decisión |
| --- | --- | --- |
| A — HIGH VALUE / LOW RISK | Análisis incompleto podía omitir un arco R y generar el resto: 2 movimientos originales, 1 segmento representable, 1 emitido. | Implementado rechazo explícito Legacy de análisis incompleto/crítico antes de construir/publicar el artefacto. |
| A | JSON/CSV admitían muestras repetidas, posiciones incompatibles con su malla y valores no finitos. La selección por diccionario hacía ganar al último duplicado. | Implementadas validaciones previas a persistir; conserva alturas faltantes y mallas regulares válidas. |
| A | Respuesta live pendiente: 7 solicitudes iniciadas en 5 s, incluidas dos lecturas iniciales. | Implementado un único polling, sin solapamiento; regresión verifica resolución y desmontaje. |
| A | Checks fallidos decían «Moonraker HTTP conectado», «Klipper listo» o «Mapa físico activo». | Mensajes de fallo coherentes; mismas condiciones booleanas. |
| A | Encabezado sticky translúcido mezclaba texto y controles durante scroll, especialmente móvil. | Fondo opaco; sin rediseño. |
| A | Faltaban oráculos independientes de plano, cuadrática, continuidad, límites y signo. | 15 pruebas numéricas y diagnóstico CSV reproducible. |
| B — HIGH VALUE / MEDIUM RISK | Rampa Z=-0.1 → -1.1 en X=0 → 10: en X=1 Legacy ya usa -1.1, en vez de -0.2. Error 0.9 mm; la hélice pierde igualmente la progresión Z. | Documentado; decidir rechazo de rampas o conservar Z inicial/progresión en toda la representación, preview, cobertura y generador. No modificar planner automáticamente. |
| B | Preview antigua de sesión recibe delta físico pero resta referencia: delta=0.2, Zref=3, Zprog=-0.1 produce -2.9; generador máquina=3.1 y valor local esperado=0.1. | Documentado; unificar contrato de coordenadas de preview y artefacto con pruebas de ruta y herramientas diferentes. No corregir solo la etiqueta. |
| B | `G1 X1 F10 G20` produce X=1 mm, F=10, frente a X=25.4 mm con G20 antes de los ejes. Analizador, Adaptive y estimador procesan tokens secuencialmente. | Documentado; acordar normalización de bloques o rechazo explícito coherente en los tres consumidores. |
| B | Caché `_snapshots` sin límite global de rutas/bytes, hasta 32 revisiones completas por ruta. Retuvo 19,341,474 bytes de 80 snapshots sintéticos después de desaparecer sus rutas. | Documentado; presupuesto global y política de expulsión con pruebas de conflictos/persistencia. Eliminar bases puede obligar a recargar ediciones antiguas; no cambiar en esta revisión. |
| B | Cada interpolación reconstruye el índice de todas las muestras: 1000 consultas tardaron 0.027 / 0.358 / 1.980 s para 81 / 1681 / 6561 nodos. Legacy materializa puntos, líneas y trazas sin límite equivalente al de Adaptive (50,000). | Documentado; preparar índice por generación y presupuesto de expansión con pruebas de equivalencia. Posible pico por volumen, distinto de leak. |
| B | Tras agregar operación/subir/analizar, ciertas actualizaciones de proyecto restauran `initialView`, obligando a volver a Archivo. | Documentado; limitar restauración a cambio de contexto, verificando abrir/continuar/selección de operación. |
| C — NICE TO HAVE | Tarjetas de historial muestran referencia/mapa físicos pendientes junto a resumen simulado listo; checks repetidos y estados técnicos (`READY_TO_RESUME`, `JOB_CANCELLED`) aumentan la densidad. | Aclarar alcance simulado y traducir estados principales sin ocultar códigos diagnósticos. |
| C | En móvil hay bastante scroll antes del workspace y antes de algunas acciones de ejecución; tablas tienen scroll horizontal local. | Acercar acción principal al mensaje de espera y compactar historial, sin gran rediseño. |
| D — NO HACER AHORA | Nuevo planner, nuevo soporte complejo de arcos, arquitectura nueva, actualización masiva de dependencias y eliminación cosmética de warnings. | No implementados. |

## Demostración matemática y pipeline

`HeightMap` guarda región local PCB, dimensiones/pasos de malla y muestras
`(fila, columna, x_mm, y_mm, z_mm)`. En
`heightmap/analysis.py:interpolate_height`, las posiciones de celda son
`(x-xmin)/paso_x`, `(y-ymin)/paso_y`; selecciona la celda inferior de índices,
acota índices y pesos, e interpola sus cuatro esquinas. En xmax/ymax usa la
última celda con peso 1. Un rincón faltante/excluido devuelve `insuficiente`.
`check_domain` usa distancia euclídea al rectángulo: hasta 1e-6 mm se limita al
borde; fuera devuelve `fuera de dominio`. Las exclusiones también rechazan.
No extrapola. El servicio/importación admite 1xN, Nx1 y 1x1; configurar/simular
por HTTP exige actualmente al menos 2x2 y validar exige tres muestras válidas.

La adquisición en `PhysicalMapService.record_point` conserva Z absoluta y
`delta_z = z_measured - acquisition_reference_z` (punto REFERENCE: delta cero).
`_height_map_payload_from_points` coloca ese delta en `HeightSample.z_mm`.
`CompensatedGCodeService._reference_frame` toma la referencia Z vigente de la
herramienta; `compensate_cut_point` demuestra:

```text
X_maquina = X_PCB + machine_origin_x
Y_maquina = Y_PCB + machine_origin_y
Z_corte_maquina = measured_tool_reference_Z + interpolated_map_delta + programmed_Z
Z_auxiliar_maquina = measured_tool_reference_Z + programmed_Z
```

La selección de superficie actual es G1/G2/G3 con Z final programada < 0;
G0 y Z>=0 no usan delta. `tool_change_z_positive_up` gobierna despejes auxiliares,
no invierte la suma de compensación. Los deltas +0.7 y -0.4 con Zref=8 y
Zprog=-0.15 prueban ambos signos sin ambigüedad. La preview de sesión antigua
usa otra fórmula y presenta la discrepancia documentada arriba.

El analizador conserva coordenadas absolutas en mm, feed modal y Z final;
`PreviewSegment` no conserva Z inicial. Legacy subdivide XY (paso por defecto
max(0.25, medio paso mínimo de mapa)), omite el inicio repetido de cada segmento,
suma delta y referencia, emite **G1 incluso para G0** con XY/Z a 5 decimales y
F a 3. No inventa un feed XY global: cada subsegmento de corte conserva su F.
Un rápido inicial sin F continúa dependiendo del feed modal de ejecución;
no debe interpretarse como preservación del comportamiento G0 original.
Z auxiliar de cambios/sondeo sigue usando sus settings independientes,
comprobados por las suites existentes. Comentarios `;` y `(…)` no alteran los
movimientos probados; Legacy reconstruye el archivo y no conserva comentarios
originales ni instrucciones auxiliares como dwell. No equivale a un intérprete
general RS274.

G2/G3 I/J se reconstruyen en XY: tolerancia nominal de cuerda 0.05 mm,
mínimo 12 y máximo 720 subdivisiones de arco. Legacy recorre esa polilínea
y emite G1; no conserva arcos nativos. R, K, radio inconsistente o I/J ausentes
marcan análisis incompleto y ahora impiden generar Legacy. La tolerancia XY
nominal no es una cota de error Z en superficies arbitrarias. Adaptive tiene
su propio parser, interpolación de Z, preservación/división de arcos y pruebas;
no se amplió su soporte. **JobService ejecuta Legacy JIT**, no Adaptive.

La discrepancia por orden de G20 no es una preferencia de formato: la
[documentación primaria RS274/NGC de LinuxCNC](https://linuxcnc.org/docs/stable/html/gcode/overview.html#_item_order)
establece equivalencia al reordenar palabras del bloque. Preámbulos explícitos
G20/G21 y G90/G91 dieron trayectorias finales equivalentes en los tests.

La generación guarda hashes original/mapa/salida, frame, token de referencia,
traza por línea, feeds, deltas y estado ejecutable. `JobService` revalida antes
de upload sin `print_file`, y separa start con las autoridades ya existentes.
No se alteraron esas autoridades. Los fakes de las rutas prueban multi-herramienta,
READY_TO_RESUME con confirmación humana, pause/resume/cancel e identidad incorrecta.

## Evidencia y límites

- Constante: nodos, centros, bordes y esquinas exactos. Plano: 2000 puntos con tolerancia 2e-14 mm.
- Cuadrática `0.002x²+0.003y²+0.001xy`, paso 2 mm: máximo 0.004999807 mm, media 0.003323150 mm; cota analítica 0.005 mm. Continuidad en ambos ejes verificada a epsilon 1e-9 mm.
- Trayectoria rectangular: 6 movimientos originales, 402 emitidos, 401 con superficie; 400 muestras de corte horizontal, Z máquina 2.903–3.479 mm, max |ΔZ|=0.006 mm. Incluyendo aproximación/plunge: 0.097 mm. Cero fuera del mapa y cero NaN/inf. Datos: [trajectory.csv](artifacts/astra-product-review/trajectory.csv), [diagnostics.json](artifacts/astra-product-review/diagnostics.json).
- Soak: cinco lecturas status/proyecto/live por ciclo, mapa/estadísticas, recálculo cada cinco ciclos y connect/disconnect simulados. Criterio práctico: incremento RSS <64 MiB y no más de dos threads/tareas adicionales. [Checkpoints](artifacts/astra-product-review/soak.json). No ejercita WebSocket/serial físicos ni grandes archivos y no descarta crecimiento a largo plazo.
- Ciclo WebSocket revisado: recv/ping cancelados y esperados al timeout/stop; conexión única de telemetría, cierre en finally. Workers JobRun/mapa retiran threads terminados; eventos de runtime acotados a 100. Timers/listeners React revisados con cleanup y `Plotly.purge`. Sin evidencia adicional de leak de esas rutas; probar reconexión prolongada con fakes sigue siendo trabajo separado. Los registros de locks por ruta también crecen por cardinalidad, con menor volumen que los snapshots.
- Firefox headless: 1440x900 y 390x844 mediante viewport BiDi, no el ancho mínimo de ventana de Firefox. Creación de proyecto/operación, carga artificial, análisis, referencias, mapa 2D, pestaña 3D, configuración, inspección/plan y prepare bloqueado usados en la UI. Ocho snapshots de API con fakes renderizados en ambos tamaños; conexión/desconexión/error/settings mediante respuestas GET sintéticas, sin acciones físicas. No equivale a impresión nativa simulada.
- Sin excepciones JavaScript observadas en la página ni HTTP 5xx de la aplicación. Firefox headless no pudo crear un contexto WebGL: se abrió la pestaña 3D, pero su render 3D queda NO VERIFICADO; hubo warnings de WebGL y mensajes internos de Firefox. La revisión visual del mapa se sustenta en 2D. Los 404 correspondieron a mapas inexistentes antes de crearlos; los 24 rechazos de conexión ocurrieron mientras se detenía/levantaba el servidor aislado. Hubo errores de selector WebDriver al adaptar el recorrido, corregidos en el harness. Polling live observado aproximadamente a 1 Hz; sin medición formal de commits React.
- El encabezado final y los mensajes de bloqueo son legibles. En móvil no hubo desbordamiento horizontal del documento; las tablas anchas conservan su contenedor desplazable. Persisten densidad visual y las propuestas de navegación señaladas.

## Reproducir sin hardware

Desde esta rama, con el virtualenv disponible y `frontend/node_modules` instalado:

```bash
MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONPATH=src python -m unittest discover -s tests -v
npm --prefix frontend run lint
npm --prefix frontend run test
npm --prefix frontend run build
MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONPATH=src python -m unittest tests.test_product_numerics -v
MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONPATH=src:tests python tests/product_review_diagnostics.py /tmp/kca-product-diagnostics
MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONPATH=src python tests/product_review_server.py
```

El lanzador fija simulated, auto-connect=false, destinos localhost sin hardware,
puerto 18765 y datos `/tmp/kca-astra-review/data`. En otra terminal:

```bash
python tests/simulated_app_smoke.py --base-url http://127.0.0.1:18765 --data-dir /tmp/kca-astra-review/data
python tests/product_review_soak.py
MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONPATH=src:. python tests/product_review_fake_states.py
git diff --check
```

Para repetir la revisión de presentación, iniciar `geckodriver --host 127.0.0.1 --port 18766`
en otra terminal y ejecutar `python tests/product_review_browser.py start`,
`python tests/product_review_browser.py click 'Continuar proyecto'` y
`python tests/product_review_browser.py tour`. Desde Ejecución,
`visual_fakes` muestra los estados capturados; `runtime_fakes` sustituye solo
GETs y bloquea escrituras mientras presenta los estados de conexión/settings.
Terminar con `python tests/product_review_browser.py stop` y detener únicamente
los procesos de revisión iniciados para estas pruebas.

Validación focalizada previa: 80 backend y 82 frontend. Suite final completa
ejecutada una sola vez: 517 backend en 197.372 s; 137 frontend en 16 archivos.
Numéricas repetidas: 15/15. Build conserva el warning conocido de bundle grande
(Plotly 4.84 MB sin comprimir) y backend el de `on_event`; no se modificaron por estética.
Los bloqueos iniciales de sockets/TestClient del sandbox se resolvieron con
ejecución autorizada fuera de él, conservando aislamiento de datos/hardware.

Se conservó exactamente el HEAD de main y sus cambios locales preexistentes en
AGENTS.md, .codex y graphify-out. Solo se trabajó en el worktree nuevo. Ningún
servicio real se consultó, detuvo o reinició; ninguna conexión física o G-code
físico se emitió. Commits locales, sin push ni merge.

Capturas de presentación con fixtures: [tool change desktop](artifacts/astra-product-review/tool-change-fake-desktop.png), [READY_TO_RESUME móvil](artifacts/astra-product-review/ready-to-resume-fake-mobile.png), [settings móvil](artifacts/astra-product-review/settings-fake-mobile.png). El indicador FÍSICO en esta última captura procede exclusivamente del fixture GET del navegador; el backend siguió en simulated y se bloquearon escrituras desde ese fixture.
