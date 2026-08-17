# Arquitectura verificada

Fecha de referencia: Thursday, July 30, 2026.

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

Cada `OperacionPCB` persiste `tool_reference_profile` (`standard` o `long_tool`). `MachineRuntime` resuelve ese perfil contra `reference_prep_z_mm` o `long_tool_reference_prep_z_mm`, valida ambos valores contra los limites Z descubiertos y respeta `tool_change_z_positive_up` para definir que direccion se aleja de la superficie. La aproximacion al punto de referencia conserva la secuencia Z segura -> X/Y -> sondeo; el contacto de sonda sigue siendo la unica autoridad de `tool_reference_z`.

## 6. Persistencia

Persistencia actual verificada:

- proyectos y montajes en `data/projects/<id>/project.json`;
- referencias bajo `MontajePCB.preparacion`;
- artefactos de ejecucion y reportes bajo `data/projects/.../reports/`;
- G-code compensado bajo `generated/compensated/` dentro de datos del proyecto.

`JobService.reset_runs_for_preparation()` es la autoridad para retirar un `current_run.json` durante un reinicio completo de preparacion. Primero comprueba Moonraker, `virtual_sdcard`, velocidad observada y propietarios vivos de movimiento; solo con inactividad comprobada archiva un run no terminal con motivo `preparation_reset`. El historial, los G-codes, las operaciones y las recetas conservadas en mapas archivados no se eliminan.

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
