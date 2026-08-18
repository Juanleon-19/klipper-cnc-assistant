# Arquitectura verificada

Fecha de referencia: Monday, August 17, 2026.

## 1. Arquitectura actual comprobada

La aplicacion servida en produccion sigue fuera de este worktree. La arquitectura de codigo verificada en `fase-2/referencias-conectividad` queda organizada alrededor de una unica frontera fisica y una unica fuente de verdad de estado.

### Vista general actual

```text
React + Vite SPA
        |
        v
FastAPI (`api/app.py`, `api/routes.py`, `api/machine_routes.py`)
        |
        +-- `application/` casos de uso de proyecto y referencias
        +-- `execution/` servicios de ejecucion y preflight
        +-- `domain/` modelos e invariantes
        +-- `storage/` persistencia JSON
        |
        v
`machine/runtime.py`
        +-- `moonraker/client.py` (HTTP)
        +-- `moonraker/telemetry.py` (WebSocket)
        +-- `machine/state.py`
        +-- `input/connection_manager.py`
        +-- `input/serial_driver.py`
        +-- `input/command_mapper.py`
        +-- `jog/manual.py`
        +-- `jog/controller.py`
```

## 2. Flujo Arduino -> maquina

```text
Arduino
  -> SerialDriver
  -> ArduinoConnectionManager
  -> CommandMapper
  -> ManualJogController
  -> JogController
  -> MoonrakerClient
  -> Klipper
```

### Autoridad serial

- `MachineRuntime` sigue siendo el coordinador de alto nivel.
- `ArduinoConnectionManager` es ahora la unica autoridad sobre apertura, cierre, lectura, reconexion y diagnostico de `SerialDriver`.
- El runtime ya no mantiene directamente el ciclo completo de reconexion serial.
- Cada nueva sesion serial incrementa una generacion y fuerza un reset seguro de:
  - ultimo paquete utilizable;
  - ultimo comando;
  - `ready_for_jog`;
  - control manual;
  - estado de sonda asociado a la sesion anterior.

### Estados seriales

```text
DISCONNECTED
DISCOVERING
CONNECTING
CONNECTED
DEGRADED
RETRY_WAIT
STOPPED
```

### Reglas de reconexion

- La reconexion automatica solo reutiliza el puerto configurado o la identidad USB previamente conocida.
- Si existe identidad USB, se compara `VID`, `PID` y `serial_number`.
- Si no existe `serial_number`, se conserva solo el puerto configurado y esa limitacion queda documentada.
- La reconexion nunca habilita movimiento por si sola.
- Tras cada reconexion el sistema queda en diagnostico con `manual_enabled = false` y `ready_for_jog = false`.
- `POST /api/machine/reconnect-arduino` fuerza una nueva sesion serial sin reiniciar Moonraker ni mover la maquina.

## 3. Flujo frontend -> backend

```text
React feature
  -> FastAPI route
  -> application / execution / runtime
  -> domain / storage / machine
```

### Feature de Referencias

- La pestaña `Referencia` se extrajo de `ProjectWorkspace.tsx` hacia `frontend/src/features/references/ReferenceWorkspace.tsx`.
- `ProjectWorkspace` conserva la orquestacion del proyecto, pero el flujo de referencia ya no vive inline dentro del workspace general.
- La UI muestra por separado:
  - Moonraker HTTP;
  - WebSocket;
  - estado Klipper;
  - Arduino;
  - generacion de conexion;
  - edad del ultimo paquete;
  - edad de la ultima observacion HTTP;
  - edad del ultimo mensaje WebSocket;
  - edad de posicion;
  - operacion activa.

## 4. Flujo Moonraker HTTP y WebSocket

```text
FastAPI POST/GET
  -> MachineRuntime
  -> MoonrakerClient (HTTP)
  -> Moonraker / Klipper

MoonrakerTelemetry (WebSocket)
  -> MachineState
  -> snapshot de runtime
  -> UI / diagnostico
```

### Semantica de telemetria

El transporte WebSocket y la frescura de posicion ya no se tratan como la misma cosa.

Estados de transporte:

```text
DISCONNECTED
CONNECTING
CONNECTED
RECONNECTING
ERROR
STOPPED
```

Reglas vigentes:

- `moonraker/client.py` sigue siendo la unica conexion HTTP.
- `moonraker/telemetry.py` sigue siendo la unica conexion WebSocket.
- El WebSocket usa `ping/pong` para verificar transporte cuando la maquina permanece quieta.
- Una maquina estacionaria puede seguir en `CONNECTED` sin reconexiones falsas.
- `MachineState` sigue siendo la unica fuente de verdad de posicion, homing, velocidad y edades observadas.
- El snapshot expone por separado:
  - `http_state`;
  - `websocket_state`;
  - `telemetry_state`;
  - `last_websocket_message_age_s`;
  - `last_position_age_s`;
  - `last_http_observation_age_s`;
  - `last_http_error`;
  - `last_websocket_error`;
  - `reconnects`;
  - `klippy_state`.

## 5. Relacion entre Referencias y `MachineState`

La captura fisica de referencias ahora depende de observacion activa y de la sesion fisica actual.

```text
UI Referencia
  -> API de referencias
  -> MachineRuntime.capture_reference_observation()
  -> refresh HTTP activa
  -> MachineState actualizado
  -> validaciones de seguridad
  -> ReferenceSessionService.persistencia
```

Antes de persistir una referencia fisica:

1. se realiza una consulta HTTP activa;
2. se actualiza `MachineState`;
3. se verifica `klippy_state == "ready"`;
4. se verifican ejes homed;
5. se valida posicion finita y suficientemente fresca;
6. se verifica que la observacion pertenece a la sesion fisica actual;
7. se rechaza la captura si existe una operacion fisica incompatible.

La persistencia de referencias sigue viviendo en `ReferenceSessionService` y conserva la politica actual de invalidacion de mapa y compensacion.

Cada `OperacionPCB` conserva por compatibilidad el campo `tool_reference_profile` (`standard` o `long_tool`), cuya semantica es exclusivamente el perfil fisico de cambio. `MachineRuntime` lo resuelve contra `tool_change_clearance_z_mm` o `long_tool_change_clearance_z_mm`, valida ambos valores contra los limites Z descubiertos y respeta `tool_change_z_positive_up` para definir que direccion se aleja de la superficie. La aproximacion al punto de referencia usa siempre `reference_prep_z_mm`; al salir de la estacion de cambio la secuencia es despeje de la herramienta entrante -> X/Y de referencia -> Z de preparacion normal -> sondeo. El contacto de sonda sigue siendo la unica autoridad de `tool_reference_z`, y ninguna Z de traslado participa en el mapa ni en la compensacion.

Las velocidades Z auxiliares tienen responsabilidades separadas:

- `z_clearance_feed_mm_min` se usa para alejar la herramienta hacia el despeje de cambio;
- `reference_approach_z_feed_mm_min` se usa para acercarla desde el despeje hasta `reference_prep_z_mm`;
- `reference_probe_feed_mm_min` gobierna solo el descenso de sondeo;
- `reference_probe_retract_feed_mm_min` gobierna solo el retracto posterior al contacto.

Los settings antiguos con `reference_prep_z_feed_mm_min` se leen como valor inicial de los dos primeros feeds. El siguiente guardado escribe solo los nombres canonicos. La seleccion entre alejamiento y aproximacion usa `tool_change_z_positive_up`, no asume que aumentar la coordenada Z siempre aleja de la superficie.

Los feeds X/Y internos son viajes auxiliares, no velocidades de mecanizado: referencia y centro usan 1800 mm/min; X/Y de cambio y movimientos del mapa que conservan el default interno usan 600 mm/min. El G-code productivo conserva los feeds modales `F` generados por FlatCAM. Si Legacy subdivide un movimiento, cada subsegmento conserva el feed efectivo del movimiento original; `MachineRuntime` no inyecta sus feeds auxiliares en el artefacto compensado.

El flujo normal de ejecucion no requiere una generacion manual previa: `JobService` genera Legacy JIT para la operacion inmediata, sube el archivo a Moonraker y espera la confirmacion de Klipper. La generacion manual permanece solo como inspeccion tecnica avanzada. Despues de medir una herramienta nueva, `READY_TO_RESUME` mantiene la barrera humana antes de generar y arrancar la siguiente operacion.

## 6. Persistencia

Persistencia actual verificada:

- proyectos y montajes en `data/projects/<id>/project.json`;
- referencias bajo `MontajePCB.preparacion`;
- artefactos de ejecucion y reportes bajo `data/projects/.../reports/`;
- G-code compensado bajo `generated/compensated/` dentro de datos del proyecto.

`JobService.reset_runs_for_preparation()` es la autoridad para retirar un `current_run.json` durante un reinicio completo de preparacion. Primero comprueba Moonraker, `virtual_sdcard`, velocidad observada y propietarios vivos de movimiento; solo con inactividad comprobada archiva un run no terminal con motivo `preparation_reset`. El historial, los G-codes, las operaciones y las recetas conservadas en mapas archivados no se eliminan.

La UI mantiene por separado los settings editados, los confirmados por PUT y la lectura activa del runtime. Una diferencia o una lectura no confirmada bloquea preparar/iniciar el `JobRun` y cualquier movimiento de referencia dependiente. Una vez iniciado un `JobRun`, la API y la UI bloquean la edicion de settings fisicos hasta que el run alcance un estado terminal.

Restricciones activas:

- no cambiar formatos persistidos sin compatibilidad o migracion;
- no tocar `data/` real desde esta fase;
- no borrar artefactos fisicos por invalidacion logica.

## 7. Problemas encontrados

- `machine/runtime.py` sigue siendo grande y concentra demasiadas responsabilidades.
- `api/routes.py` aun mezcla varias capacidades bajo el mismo modulo HTTP.
- la UI de Referencias ya fue aislada, pero todavia conserva bastante densidad interna.
- la validacion fisica real de reconexion Arduino y captura de referencias sigue pendiente fuera de esta rama.

## 8. Fronteras de seguridad

- El frontend no es autoridad de seguridad fisica.
- No existe una segunda fuente de verdad fuera de `MachineState`.
- No existe un segundo cliente Moonraker HTTP ni un segundo WebSocket.
- La reconexion Arduino no habilita movimiento.
- Una posicion cacheada por si sola no autoriza capturar referencias fisicas.
- Un fallo WebSocket no destruye el cliente HTTP.
- Un fallo Arduino no obliga a desconectar Moonraker.
- Los endpoints que puedan mover la maquina siguen siendo frontera exclusiva del backend.

## 9. Componentes previstos para las siguientes fases

### Fase 3

- mapa de alturas medido;
- persistencia y reanudacion del mapa;
- compensacion y cobertura.

### Fase 4

- consola de ejecucion;
- maquina de estados de ejecucion;
- `JobRun`;
- cambio de herramienta;
- recuperacion y cierre del producto.
