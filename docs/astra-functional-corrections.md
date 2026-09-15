# Cierre de las correcciones funcionales Astra

Continuación de la revisión, del 13 al 14 de septiembre de 2026. Base de esta
corrección: `3b0da8f3b22606a3b19c03c7fb7c0c9eb4d649d3`.
Rama: `improvement/astra-final-product-review-2026-09-13`.
Todas las mejoras anteriores permanecen. No se abrió otra auditoría.

## Contratos corregidos

- **Rampas Legacy:** `PreviewSegment.inicio_z_mm` conserva el inicio modal de Z.
  `heightmap.transform.sample_programmed_segment` interpola XYZ con el mismo
  parámetro del segmento y conserva exactamente el endpoint modal analizado.
  La emisión redondea XYZ a 5 decimales y F a 3. G91 se resuelve a coordenadas
  absolutas antes de subdividir; Legacy sigue emitiendo G90/G21. Se preservan
  feeds, dirección, movimientos horizontales y movimientos solo Z. G0 y los
  movimientos auxiliares conservan la política previa sin delta superficial;
  sus rampas programadas tampoco se aplanan. No se cambió esa política física.
- **Arcos:** I/J planos continúan discretizándose con el método anterior y ahora
  cierran en el endpoint programado exacto. Un G2/G3 con Z final distinta de la
  inicial se marca incompleto, con línea, comando, motivo y confirmación de que
  no se generó G-code compensado. R/K y otras geometrías no representables
  continúan bloqueadas. No se implementó compensación helicoidal.
- **Unidades (política B):** se acepta un régimen estable de pulgadas o mm,
  declarado en el preámbulo o en el primer bloque geométrico. G20/G21 se resuelve
  antes de interpretar las coordenadas, parámetros I/J y F del bloque, sin
  depender del orden de palabras. Un cambio de régimen posterior al inicio de
  geometría, o G20 y G21 contradictorios en el mismo bloque, se rechaza
  explícitamente en análisis, Legacy, Adaptive y estimación interna. Repetir
  la unidad vigente sigue permitido. G20/G21: **SUPPORTED dentro de ese contrato;
  cambios posteriores REJECTED_EXPLICITLY**.
- **Preview/generación:** `heightmap.transform.compensated_z` es la única fórmula
  compartida por Legacy y preview: `Z_máquina = Z_referencia_medida + delta_mapa
  + Z_programada` cuando aplica mapa; para auxiliares omite el delta. El preview
  identifica ahora Z máquina y altura superficial máquina, no una falsa Z local.
  La selección de la referencia de la herramienta también se comparte: no toma
  una referencia anterior del montaje cuando existe la medición de esa herramienta.
  El test contractual usa el mismo proyecto persistido, mapa y herramienta,
  incluso con referencia de preparación anterior. El antiguo caso −2.9/3.1 se
  resuelve a **3.1/3.1 mm**.
- **Compatibilidad:** análisis v3 y Legacy v2. El campo Z inicial es aditivo al
  JSON. Generación, comparación y preview reanalizan el original en memoria;
  un análisis guardado antiguo no puede ocultar una hélice o cambio de unidades.
  Se valida antes de cobertura y antes de publicar cualquier artefacto.

## Retención de snapshots

LRU global: **512 versiones**, **8 MiB de payload JSON serializado**, máximo
**32 versiones por ruta**. Se conservan bytes JSON inmutables en lugar de árboles
Python duplicados. Las transacciones activas y sus esperas protegen sus rutas;
una base ya obtenida para el merge sigue viva en una referencia local aunque
otro thread expulse la entrada del caché. El registro de RLocks usa referencias
débiles sin perder la exclusión entre propietarios/esperadores vivos.

Los recursos eliminados se liberan mediante los hooks del repositorio y un
barrido acotado del caché, excluyendo transacciones activas hasta su salida.
Si todas las entradas están protegidas, o una base excede el presupuesto, no se
admite esa base nueva. Una edición histórica sin base disponible exige recarga y
produce `PersistenceConflict`, nunca sobrescribe a ciegas. Una escritura con
revisión actual no depende del caché. Los objetos de una vista abierta no son
leases indefinidos: se mantiene el contrato anterior de conflicto al expirar
una base, ahora con límite global explícito.

Stress: **1.024 recursos creados/eliminados, máximo 512 snapshots, final 0**.
Tests adicionales verifican límite de bytes, límite por ruta, concurrencia,
protección de bases activas, presión con todas las entradas protegidas, rechazo
seguro de escritores obsoletos, limpieza del repositorio y liberación de RLocks.
Esto cierra la retención demostrada; no demuestra la causa del pico histórico de 5 GB.

## Trayectorias offline

Muestreo XY máximo 0.1 mm, referencia medida 3 mm. Las métricas comparan también
el G-code emitido con un oráculo analítico independiente. El CSV contiene XY,
Z programada, delta interpolado y Z máquina esperada/emitida.

| Escenario | max error Z (mm) | max abs ΔZ (mm) | error endpoint XYZ (mm) |
| --- | ---: | ---: | ---: |
| horizontal | 0 | 0 | 0 |
| ascending_ramp | 4.44e-16 | 0.005 | 0 |
| descending_ramp | 4.44e-16 | 0.005 | 0 |
| cell_crossings_inclined_plane | 4.44e-16 | 0.001 | 0 |
| xmin_boundary_inclined_plane | 4.44e-16 | 0.007 | 0 |
| xmax_boundary_inclined_plane | 4.44e-16 | 0.007 | 0 |
| ymax_boundary_inclined_plane | 4.44e-16 | 0.00275 | 0 |
| former_0_9_mm_error | 4.44e-16 | 0.01 | 0 |

En los ocho escenarios: **0 NaN/inf**, **0 puntos fuera del mapa**, **0 inversiones
inesperadas de pendiente**. Máximo salto artificial residual: **8.89e-16 mm**.
El salto consecutivo observado sigue la pendiente programada y del plano, no un
salto en la frontera de celda. Las pruebas numéricas adicionales mantienen la
cobertura de superficie constante, plano, cuadrática, bordes y signo.

## Validación

Checks focalizados finales: **170/170**.
Una única ejecución completa final: **backend 540/540, 203.408 s**;
**frontend 137/137, 16 archivos**; lint PASS; build PASS.
Se conserva el warning previo del bundle; no se modificaron dependencias.
Pruebas numéricas finales: **27/27**. Diagnóstico offline: **8/8 PASS**.
API E2E: **30/30 solicitudes PASS**, incluidas las barreras nativas de simulated.
Browser E2E: **PASS**, recorrido real en **1440×900 y 390×844**, sin overflow
horizontal del documento; **16/16** comprobaciones de estados de ejecución con
fixtures actuales de API/JobService (running, pause, recovery, cancel, ready,
spindle-stop, tool-change y ready-to-resume). Sin errores de consola ni HTTP 5xx
en el recorrido instrumentado; un 404 esperado de mapa físico ausente en simulated.
La ejecución física se representa mediante fakes; no se afirma impresión real.
No se revalidó renderizado WebGL 3D, que quedó limitado por el navegador headless
en la revisión original; no hubo cambios del visor en esta corrección.

Soak: **160 ciclos / 3.080 solicitudes / 27.018 s**, 10 ciclos de calentamiento.
RSS **76.680 → 77.652 KiB (74.883 → 75.832 MiB)**; threads **5 → 5**;
tareas asyncio **3 → 3**; snapshots **8 → 66**, máximo **66**, payload final
**258.304 bytes**. PASS. Repitió connect/disconnect simulated, status, project,
execution/live, mapas/estadísticas y recálculo. No es un soak físico ni una prueba
de varias horas de impresión.

No hubo comandos físicos, uso de Moonraker/Klipper/Arduino reales, cambios en
main, push, merge ni reinicios de servicios reales. Solo se levantaron y cerraron
procesos propios de revisión, con `MACHINE_MODE=simulated`,
`MACHINE_AUTO_CONNECT=false`, datos temporales y puertos 18765/18766.
El sandbox requirió permitir puertos locales para pruebas; el intento de fixtures
con PYTHONPATH incompleto se corrigió y el intento con TestClient bloqueado por
sandbox se reanudó fuera de él. La pausa entre turnos cerró el primer navegador y
servidor temporal; solo se reabrieron esos procesos para completar checks pendientes.
No se repitieron las suites completas.

## Reproducción

Desde este worktree, con Python del entorno disponible:

```bash
export MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONPATH=src:.
python -m unittest discover -s tests -v
(cd frontend && npm run lint && npm run test && npm run build)
python -m unittest tests.test_product_numerics tests.test_product_functional_fixes.RampAndPreviewContracts tests.test_product_functional_fixes.UnitRegimeContracts -v
python tests/product_fix_diagnostics.py /tmp/kca-astra-fixes-diagnostic
python tests/product_review_server.py
# En otro terminal, misma configuración simulada:
python tests/simulated_app_smoke.py --base-url http://127.0.0.1:18765 --data-dir /tmp/kca-astra-review/data
python tests/product_review_soak.py
python tests/product_review_fake_states.py
geckodriver --host 127.0.0.1 --port 18766
# En otro terminal:
python tests/product_review_browser.py start
python tests/product_review_browser.py click 'Continuar proyecto'
python tests/product_review_browser.py tour
python tests/product_review_browser.py visual_fakes
python tests/product_review_browser.py stop
git diff --check
```

Evidencias sintéticas versionadas: [métricas](artifacts/astra-product-fixes/diagnostics.json),
[trayectorias CSV](artifacts/astra-product-fixes/trajectory.csv),
[soak](artifacts/astra-product-fixes/soak.json),
[navegador](artifacts/astra-product-fixes/browser.json).
Logs completos: `/tmp/kca-astra-review/fixes-*-final.log`.

## Resultado de cierre

`FINAL_PRODUCT_READY_FOR_PHYSICAL_VALIDATION = YES` para el subconjunto G-code
explícitamente soportado arriba. Los cinco hallazgos funcionales pedidos quedan
corregidos o rechazados explícitamente; no hay nuevos bloqueos de software.
La validación física sigue siendo una etapa posterior que requiere al operador.
Esta rama se detiene aquí: no se integra, publica ni ejecuta en máquina.
`git diff --check` PASS. Todos los cambios quedan en commits locales.
