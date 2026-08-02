# Resultado del hotfix de cadencia del mapa de alturas

## Corrección aplicada

### 1. Heartbeat en memoria

Los eventos de micropaso ya no persisten por sí mismos.

Cada `POINT_LOWER_STEP` y `POINT_CONFIRM_STEP` actualiza solo:

- timestamp monotónico;
- fase;
- estado;
- contador de pasos;
- métricas de comando para el watchdog.

## 2. Persistencia fuera del hilo de movimiento

El callback de progreso del runtime ya no:

- llama a `PhysicalMapService.update_execution_state()`;
- llama a `_save()`;
- toma snapshots del runtime para persistir cada paso.

Las transiciones relevantes se encolan y el worker de malla las persiste fuera del hilo que emite movimiento.

## 3. Persistencias acotadas por punto

Las persistencias quedan limitadas a transiciones relevantes:

- inicio del punto;
- Z segura iniciada/confirmada;
- XY iniciado/confirmado;
- descenso iniciado;
- contacto detectado;
- retracto iniciado/confirmado;
- captura de Z;
- punto persistido o estado terminal.

Con 100 micropasos:

- antes: ~110 persistencias por punto;
- ahora: máximo validado `<= 12` persistencias por punto.

## 4. Perfil efectivo unificado

El mapa ahora resuelve un único perfil efectivo con origen explícito:

- `machine_reference_profile`
- `map_override`

La API y la UI exponen:

- `source`
- `effective_probe_step_mm`
- `effective_probe_feed_mm_min`
- `effective_retract_mm`

Sin override explícito, la malla hereda exactamente el perfil de Tomar referencia.

## 5. UI del mapa

La pestaña Mapa ahora:

- muestra `Descendiendo: búsqueda de contacto`;
- diferencia herencia vs override;
- muestra el perfil efectivo;
- mantiene Pausar y Cancelar;
- evita polling solapado de runtime y malla.

## 6. Pruebas ejecutadas en Sunday, August 2, 2026

Backend:

- `python -m pip check`
- `PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v`

Frontend:

- `npm ci`
- `npm run lint`
- `npm run test`
- `npm run build`

## 7. Corrección de preview y regeneración

### Preview pura y separada

La preview física ya no reutiliza `planPhysicalMapFromReference`.

Ahora el flujo queda separado así:

- `meshPreview`: resultado temporal, no persistido, no ejecutable, no aparece en historial;
- `physicalMap`: mapa persistido, con `map_id`, estados de sondeo, historial y polling.

La UI ya no usa `physicalMap` como contenedor dual.

### Cancelación propia y respuestas obsoletas

La generación de preview ahora usa:

- `AbortController`;
- token de generación por solicitud;
- descarte explícito de respuestas tardías.

Resultado:

- `Cancelar generación` no crea mapas `CANCELLED`;
- `Limpiar vista previa` no borra mediciones ni referencias;
- el polling del mapa persistido no reconstruye `meshPreview`;
- una respuesta vieja no puede reaparecer después de limpiar o cancelar.

### Perfil efectivo coherente

El editor de perfil usa `probeProfileMode` como fuente de verdad local.

En `machine_reference_profile`:

- se muestran los valores de máquina;
- los campos override quedan deshabilitados;
- el request envía `probe_profile_source=machine_reference_profile`;
- no envía `probe_step_mm`, `probe_feed_mm_min` ni `retract_mm`.

En `map_override`:

- la UI muestra y valida exactamente los valores escritos;
- el request envía el override explícito.

### Preview después de `CANCELLED`

Además del desacople preview/mapa, se corrigió la creación de `map_id` para nuevas versiones:

- antes: timestamp con resolución de segundos;
- ahora: timestamp con microsegundos en `PhysicalMapService._map_id()`.

Eso evita reutilizar el mismo `map_id` cuando se cancela y se arma una nueva versión dentro del mismo segundo.

## 8. Métricas antes y después

Cadencia de sondeo:

- antes: ~110 persistencias por punto con 100 micropasos;
- ahora: `<= 12` persistencias por punto.

Preview física:

- antes: reutilizaba la ruta persistente, con escrituras, historial y polling posterior;
- ahora: `0` escrituras de repositorio en preview validadas por prueba;
- ahora: el backend devuelve `preview_backend_duration_ms`;
- ahora: el frontend registra `preview_request_duration_ms`.

No se fija una SLA temporal rígida en documentación porque el objetivo del hotfix es eliminar trabajo persistente del camino crítico, no prometer una cifra dependiente del host.

## 9. Riesgos pendientes

- El `build` del frontend sigue generando chunks grandes en Plotly y en el bundle principal. No bloquea este hotfix, pero conviene revisar partición de código.
- FastAPI sigue emitiendo avisos de deprecación por `on_event`. No bloquea este hotfix.
- La validación definitiva de cadencia requiere una prueba física posterior controlada.

## 10. Protocolo de prueba física posterior

1. Desplegar primero este hotfix sin mezclar otros cambios.
2. Tomar una referencia física y observar la cadencia base.
3. Generar preview y confirmar que no reaparece después de limpiarla.
4. Cambiar configuración y regenerar preview sin recargar la página.
5. Armar un mapa nuevo después de cancelar uno previo y verificar `map_id` nuevo.
6. Ejecutar una malla pequeña y segura.
7. Verificar que la bajada del mapa sea continua, sin pausas entre micropasos.
8. Verificar que Pausar y Cancelar actúen solo en límites seguros.
9. Revisar logs de `POINT_DESCENT_STARTED`, `POINT_CONFIRM_STEP`, `persistence_count` y `persistence_duration_s`.
10. Si la cadencia vuelve a degradarse, comparar tiempo de movimiento frente a tiempo de persistencia, polling y callback.

## 11. Confirmaciones de seguridad

- No se realizó ningún movimiento físico en este turno.
- No se enviaron comandos G-code.
- No se desplegó la aplicación activa.
- No se reinició ningún servicio.
