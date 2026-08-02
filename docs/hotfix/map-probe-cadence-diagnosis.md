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

## Bloqueos adicionales detectados en la preview

Después del hotfix inicial apareció un segundo defecto en la pestaña Mapa. La UI mezclaba dos conceptos distintos en el mismo estado:

- una preview temporal de malla;
- el mapa físico persistido con `map_id`, historial y estados de sondeo.

Ruta previa del flujo de preview:

`Generar vista previa -> withPhysicalMapAction -> planPhysicalMapFromReference -> persistencia backend -> setPhysicalMap -> polling -> Limpiar vista previa -> setPhysicalMap(null) -> polling restaura el mapa persistido`

Consecuencias:

- la preview física escribía en repositorio;
- la preview podía reaparecer por polling después de limpiarla;
- `heightMapBusy` compartía preview, armar, pausar, reanudar y cancelar;
- una respuesta vieja podía rehidratar un mapa ya limpiado;
- un mapa `CANCELLED` podía seguir ocupando el slot activo y bloquear un plan nuevo;
- el selector `Heredar referencia / Override del mapa` podía mostrar valores heredados, validar otros y enviar otros distintos.

## Causa de lentitud de la preview

La lentitud no venía de calcular la cuadrícula sino de reutilizar la ruta persistente de planeación para mostrar una preview:

- `planPhysicalMapFromReference` creaba un mapa medido real;
- la operación escribía `height_map.json`;
- podía actualizar `active_map_id`;
- refrescaba historial y disparaba polling adicional;
- el frontend trataba ese objeto persistido como si fuese una preview temporal.

La preview quedó acoplada a:

- persistencia del mapa;
- carga de historial;
- reconciliación del mapa activo;
- respuestas HTTP viejas que podían sobrescribir estado reciente.

## Diferencia confirmada preview vs mapa persistido

Antes de la corrección:

- preview en modo físico: persistía;
- `physicalMap` servía a la vez como preview y como mapa activo;
- `Limpiar vista previa` solo limpiaba React local y el polling reconstruía el mapa;
- cancelar el mapa y cambiar configuración podía dejar el flujo bloqueado.

Contrato correcto:

- `meshPreview`: temporal, no persistida, no ejecutable, no aparece en historial;
- `physicalMap`: persistido, con `map_id`, polling, pausa, cancelación, reanudación e historial.

## Bloqueo adicional confirmado tras `CANCELLED`

Al reproducir el caso reportado apareció una colisión real de `map_id`: la generación original usaba un timestamp con resolución de segundos.

Si el usuario cancelaba y volvía a armar una nueva versión dentro del mismo segundo, el mapa nuevo podía reutilizar el mismo `map_id`, lo que contradice el contrato de “nueva versión” y complica historial, archivos y rearmado inmediato.

La causa no era física; era un identificador insuficientemente granular en `PhysicalMapService._map_id()`.
