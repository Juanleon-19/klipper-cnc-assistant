# Diagnóstico del hotfix de cadencia del mapa de alturas

## Resumen

La referencia física y el mapa de alturas compartían el mismo descenso seguro en `MachineRuntime._perform_probe_descent()`, pero no compartían el mismo camino crítico alrededor de cada micropaso.

- `MachineRuntime.confirm_probe()` descendía sin callback persistente.
- `MachineRuntime.probe_mesh_point()` ejecutaba un callback de progreso por cada micropaso.
- Ese callback terminaba en `PhysicalMapService.update_execution_state()`.
- `update_execution_state()` recargaba el mapa y ejecutaba `_save()`.
- `_save()` serializaba JSON síncrono a disco.

Resultado: el mapa físico convertía cada `POINT_LOWER_STEP` en IO síncrono antes de poder continuar, mientras la referencia descendía con una cadencia continua.

## Diferencia confirmada entre referencia y mapa

Ruta de referencia:

`confirm_probe() -> _perform_probe_descent()`

Ruta de mapa antes del hotfix:

`_probe_one_point() -> _probe_with_watchdog() -> probe_mesh_point() -> _perform_probe_descent() -> progress_callback() -> update_execution_state() -> _save()`

El defecto no estaba en el movimiento Z seguro, ni en el paso, ni en la sonda Arduino por sí solos. El defecto era la persistencia síncrona metida en la ruta de progreso del mapa.

## Causa física observada

1. `POINT_LOWER_STEP` se notificaba antes del movimiento.
2. El callback del mapa persistía ese estado antes de volver al hilo que enviaba movimiento.
3. Cada persistencia implicaba JSON y escritura a disco.
4. El frontend además hacía polling concurrente de runtime y mapa, agregando presión de lectura.
5. El watchdog dependía del mismo flujo de progreso y podía confundir lentitud de persistencia con ausencia de movimiento.

## Impacto medido en la implementación previa

- Había una escritura persistente por cada `POINT_LOWER_STEP`.
- Con una bajada de 100 micropasos, el punto terminaba en aproximadamente 110 persistencias:
  - 100 por `POINT_LOWER_STEP`
  - ~10 adicionales por precheck, transiciones, captura y cierre del punto
- La referencia no tenía esa penalización.

## Conclusión

La diferencia real entre referencia y mapa no era un perfil Z distinto por defecto, sino una ruta crítica distinta:

- referencia: descenso -> movimiento
- mapa: callback persistente -> escritura JSON -> movimiento

Eso explicaba la bajada por intervalos, la sensación de bloqueo y el retraso acumulado antes de que Klipper recibiera el siguiente micropaso.
