# Arquitectura verificada

Fecha de referencia: Thursday, July 30, 2026.

## 1. Arquitectura actual comprobada

La aplicacion realmente servida corre desde `/home/impresora/klipper-cnc-assistant`, usa FastAPI para la API y para servir la SPA construida en `frontend/dist`, y mantiene una frontera fisica que integra Moonraker, Klipper y Arduino.

### Vista general actual

```text
React + Vite SPA
        |
        v
FastAPI (`api/app.py`, `api/routes.py`, `api/machine_routes.py`)
        |
        +-- `application/` casos de uso de proyecto, referencia y mapa
        +-- `execution/` servicios de ejecucion y preflight
        +-- `domain/` modelos y reglas base
        +-- `storage/` persistencia JSON
        |
        v
`machine/runtime.py`
        +-- `moonraker/client.py` (HTTP)
        +-- `moonraker/telemetry.py` (WebSocket)
        +-- `machine/state.py`
        +-- `input/serial_driver.py`
        +-- `input/command_mapper.py`
        +-- `jog/manual.py`
        +-- `jog/controller.py`
```

### Flujo Arduino -> maquina

```text
Arduino
  -> SerialDriver
  -> CommandMapper
  -> ManualJogController
  -> JogController
  -> MoonrakerClient
  -> Klipper
```

### Flujo frontend -> backend

```text
React feature
  -> fetch a FastAPI
  -> servicio de aplicacion o ejecucion
  -> dominio / almacenamiento
```

### Flujo Moonraker HTTP y WebSocket

```text
POST API
  -> MachineRuntime / servicio de ejecucion
  -> MoonrakerClient (HTTP)
  -> Moonraker / Klipper

MoonrakerTelemetry (WebSocket)
  -> MachineState
  -> endpoints de diagnostico y UI
```

## 2. Estructura actual despues de la reorganizacion

### Backend

```text
src/klipper_cnc_assistant/
├── api/
├── application/
├── domain/
├── execution/
├── gcode/
├── heightmap/
├── input/
├── jog/
├── machine/
├── moonraker/
└── storage/
```

Cambios estructurales ya aplicados en Fase 1:

- `application/job_service.py` -> `execution/job_service.py`
- `application/mesh_execution_service.py` -> `execution/mesh_execution_service.py`
- `application/__init__.py` conserva compatibilidad de importacion para los servicios movidos

### Frontend

```text
frontend/src/
├── components/
│   └── StatusBadge.tsx
├── features/
│   ├── execution/
│   ├── height-map/
│   ├── projects/
│   ├── system/
│   └── viewer/
├── lib/
├── test/
├── App.tsx
├── main.tsx
└── types.ts
```

Cambios estructurales ya aplicados en Fase 1:

- los componentes de proyecto quedaron bajo `features/projects/`
- `MachineContext`, `SystemPage` y `SystemBanner` quedaron bajo `features/system/`
- `ToolpathPreview` quedo bajo `features/viewer/`
- `ExecutionConsole` quedo bajo `features/execution/`
- `features/heightmap/` paso a `features/height-map/`

## 3. Problemas encontrados

### Responsabilidades duplicadas o borrosas

- `api/routes.py` mezcla proyectos, referencias, mapas, compensacion y ejecucion.
- `machine/runtime.py` concentra demasiadas responsabilidades: conexion, telemetria, serie, referencia, sonda, mapa, tool change, cancelacion y estado.
- parte del flujo de ejecucion se repartia entre `application/`, runtime y UI; la creacion de `execution/` reduce esa dispersion, pero no la elimina.

### Duplicidades tecnicas observadas

- existen rutas fisicas cercanas en significado, por ejemplo `cancel` y `safe-stop`, o vistas de estado similares.
- durante la auditoria, la unidad instalada y su override no coincidian plenamente con la unidad versionada.
- la documentacion historica mezcla capacidades confirmadas con objetivos todavia abiertos.

### Acoplamientos no deseados

- `App.tsx` y `ProjectWorkspace` siguen siendo puntos de orquestacion demasiado grandes.
- el frontend conserva logica transversal que deberia quedar mejor encapsulada por feature.
- el runtime fisico sigue siendo un punto de alto riesgo por efectos secundarios de importacion y arranque.

### Riesgos de seguridad

- la maquina visible esta configurada en modo fisico en el host auditado.
- la configuracion operativa estuvo versionada historicamente en `deploy/klipper-cnc-assistant.env`; en esta rama deja de ser la fuente canonica y se externaliza a `/etc/klipper-cnc-assistant/klipper-cnc-assistant.env`.
- `deploy/klipper-cnc-assistant.env.example` contiene solo valores seguros de simulacion.
- existe codigo capaz de mover la maquina; por eso la seguridad no puede descansar en la UI ni en supuestos de entorno.

## 4. Arquitectura objetivo

La arquitectura objetivo conserva el comportamiento existente, pero hace explicitas las fronteras funcionales y de seguridad.

```text
React features
  -> api/routes/*
  -> application/
  -> domain/
  -> storage/

frontend system feature
  -> API de diagnostico
  -> machine/runtime.py
  -> moonraker/ + input/ + jog/
```

Objetivos concretos:

- separar `api/routes.py` en modulos por capacidad sin cambiar endpoints publicos salvo que sea imprescindible;
- reducir `machine/runtime.py` a coordinador de frontera fisica y extraer responsabilidades internas cuando exista un limite claro;
- mantener `execution/` como area unica para `JobRun`, preflight y consola de ejecucion;
- mantener `storage/` como persistencia unica de proyectos, mapas y artefactos, sin duplicidades en el frontend;
- mantener una unica conexion Moonraker HTTP, una unica conexion Moonraker WebSocket y una unica fuente de verdad de `MachineState`.

## 5. Responsabilidad de cada capa

- `frontend/src/features/`: experiencia de usuario por dominio funcional.
- `api/`: validacion HTTP, serializacion y compatibilidad de rutas.
- `application/`: casos de uso de producto que no son exclusivamente de ejecucion.
- `execution/`: coordinacion de preflight, `JobRun`, consola y estado de ejecucion.
- `domain/`: modelos, invariantes y estructuras de negocio.
- `storage/`: persistencia JSON y acceso a archivos de proyecto.
- `machine/`: runtime, descubrimiento y estado vivo de maquina.
- `moonraker/`: transporte HTTP y WebSocket hacia Moonraker.
- `input/`: serie Arduino y mapeo de intencion.
- `jog/`: unica capa autorizada a traducir intencion en movimiento manual.
- `gcode/`: analisis y modelo de trayectorias.
- `heightmap/`: dominio matematico de mapa, cobertura y compensacion.

## 6. Persistencia

Persistencia actual verificada:

- proyectos y montajes en `data/projects/<id>/project.json`;
- artefactos de ejecucion y reportes bajo `data/projects/.../reports/`;
- G-code compensado bajo `generated/compensated/` dentro de datos del proyecto;
- mapas y referencias conservados dentro de la estructura JSON del proyecto y sus carpetas asociadas.

Restricciones para fases siguientes:

- no cambiar formatos persistidos sin compatibilidad o migracion;
- no mover `data/` al control de versiones;
- no tratar mapas fisicos ni referencias reales como datos de prueba publicables.

## 7. Despliegue y configuracion

- la unidad versionada carga `/etc/klipper-cnc-assistant/klipper-cnc-assistant.env`;
- el archivo operativo se administra fuera del repositorio;
- la plantilla versionada permanece en modo simulado y no contiene posiciones, referencias ni limites de la maquina;
- una actualizacion Git no autoriza copiar configuracion, reinstalar la unidad, reiniciar el servicio ni activar modo fisico;
- la migracion de una instalacion anterior esta documentada en `docs/deployment.md`.

## 8. Fronteras de seguridad

- el frontend no debe ser autoridad de seguridad fisica;
- los endpoints `POST` fisicos son operaciones de riesgo y deben seguir en la frontera backend;
- `JogController` sigue siendo la frontera unica para movimiento manual;
- `MachineState` debe seguir siendo la fuente de verdad de homing, posicion, velocidad y frescura de telemetria;
- una conexion WebSocket abierta no equivale a telemetria valida para mover la maquina;
- la ausencia de hardware o la ejecucion en `MACHINE_MODE=simulated` solo valida software.

## 9. Componentes previstos para las siguientes fases

### Fase 2

- Referencias
- Arduino y reconexion serie
- conectividad Moonraker HTTP y WebSocket
- consolidacion de seguridad de runtime

### Fase 3

- mapa de alturas medido
- persistencia y reanudacion del mapa
- compensacion y cobertura

### Fase 4

- consola de ejecucion
- maquina de estados de ejecucion
- `JobRun`
- cambio de herramienta
- recuperacion y cierre del producto
