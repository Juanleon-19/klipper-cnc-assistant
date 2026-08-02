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

## 7. Riesgos pendientes

- El `build` del frontend sigue generando chunks grandes en Plotly y en el bundle principal. No bloquea este hotfix, pero conviene revisar partición de código.
- FastAPI sigue emitiendo avisos de deprecación por `on_event`. No bloquea este hotfix.
- La validación definitiva de cadencia requiere una prueba física posterior controlada.

## 8. Protocolo de prueba física posterior

1. Desplegar primero este hotfix sin mezclar otros cambios.
2. Tomar una referencia física y observar la cadencia base.
3. Ejecutar una malla pequeña y segura.
4. Verificar que la bajada del mapa sea continua, sin pausas entre micropasos.
5. Verificar que Pausar y Cancelar actúen solo en límites seguros.
6. Revisar logs de `POINT_DESCENT_STARTED`, `POINT_CONFIRM_STEP`, `persistence_count` y `persistence_duration_s`.
7. Si la cadencia vuelve a degradarse, comparar tiempo de movimiento frente a tiempo de persistencia y polling.

## 9. Confirmaciones de seguridad

- No se realizó ningún movimiento físico en este turno.
- No se enviaron comandos G-code.
- No se desplegó la aplicación activa.
- No se reinició ningún servicio.
