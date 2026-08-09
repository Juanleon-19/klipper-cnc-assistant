# Klipper CNC Assistant

Estado operativo actual: ver [`docs/CURRENT_STATE.md`](docs/CURRENT_STATE.md).

Klipper CNC Assistant es una aplicacion web para preparar trabajos CNC basados en G-code sobre una maquina controlada por Klipper. El producto combina un backend FastAPI, un frontend React, persistencia JSON local y una frontera fisica que integra Moonraker, Klipper y un controlador Arduino.

## Fuente de verdad

- Codigo integrado: rama `main` de `Juanleon-19/klipper-cnc-assistant`.
- Estado operativo y siguiente paso: [`docs/CURRENT_STATE.md`](docs/CURRENT_STATE.md).
- Reglas para agentes y desarrollo: [`AGENTS.md`](AGENTS.md).
- Roadmap y criterio de cierre: [`PLAN.md`](PLAN.md).

Las ramas `fase-*`, `fix/*`, worktrees, rescates y backups se consideran historicos o de trabajo hasta que se demuestre lo contrario comparandolos con `origin/main`.

## Capacidades implementadas

- gestion de proyectos, montajes y operaciones;
- analisis de G-code y visor tecnico;
- persistencia JSON local de proyectos, referencias, mapas y artefactos;
- runtime fisico con Moonraker HTTP, Moonraker WebSocket y entrada serial Arduino;
- reconexion separada del Arduino y reconexion segura del runtime;
- semantica separada de transporte WebSocket, frescura de posicion y observacion HTTP;
- captura y persistencia de referencias fisicas;
- mapa de alturas fisico con pausa, reanudacion y recuperacion;
- compensacion de altura y auditoria previa;
- planificacion, preflight y flujo de ejecucion `JobRun`;
- pruebas automatizadas de backend y frontend en CI.

## Validacion pendiente

El codigo anterior existe y esta integrado, pero el cierre del producto depende de validar en la CNC real, de forma controlada:

- estabilidad de referencia -> mapa -> recuperacion -> mapa completo;
- reconexion del runtime sin movimiento ni reinicio de servicios;
- persistencia y recuperacion ante fallos reales;
- ejecucion fisica completa de un trabajo solo despues de preflight y autorizacion explicita.

## Seguridad

- No trabajar directamente en `main`.
- No enviar G-code, homing, jog, probe, spindle ni ejecucion de trabajos sin autorizacion explicita del usuario.
- No reiniciar ni reconfigurar `systemd`, Klipper, Moonraker, Arduino ni la maquina durante desarrollo o pruebas seguras salvo autorizacion expresa.
- No publicar secretos, `.env`, datos reales de produccion, mapas fisicos, referencias reales ni G-code privado.
- Mantener la configuracion operativa fuera del repositorio, en `/etc/klipper-cnc-assistant/`.
- Usar configuraciones seguras o simuladas durante pruebas automatizadas.

## Estructura principal

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

frontend/src/
├── components/
├── features/
│   ├── execution/
│   ├── height-map/
│   ├── projects/
│   ├── references/
│   ├── system/
│   └── viewer/
├── lib/
└── test/

firmware/
tests/
deploy/
docs/
```

Arquitectura detallada: [docs/architecture.md](docs/architecture.md)

Auditoria historica del host: [docs/recovery/current-project-audit.md](docs/recovery/current-project-audit.md)

Estado operativo actual: [docs/CURRENT_STATE.md](docs/CURRENT_STATE.md)

Plan de cierre: [PLAN.md](PLAN.md)

## Instalacion

Backend:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
python -m pip check
```

Frontend:

```bash
cd frontend
npm ci
```

## Desarrollo

Backend local seguro:

```bash
source .venv/bin/activate
MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONPATH=src python -m klipper_cnc_assistant serve --host 127.0.0.1 --port 8010 --data-dir /tmp/kca-dev-data
```

Analisis de G-code sin hardware:

```bash
source .venv/bin/activate
PYTHONPATH=src python -m klipper_cnc_assistant check-gcode ruta/al/archivo.nc
```

Frontend local:

```bash
cd frontend
npm run dev
```

## Pruebas

Backend seguro:

```bash
source .venv/bin/activate
MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONPATH=src python -m unittest discover -s tests -v
```

Frontend:

```bash
cd frontend
npm run lint
npm run test
npm run build
```

## Ejecucion

CLI soportada:

```bash
python -m klipper_cnc_assistant serve --host 127.0.0.1 --port 8000 --data-dir data
python -m klipper_cnc_assistant check-gcode archivo.nc
```

La instancia de produccion del host se despliega desde `/home/impresora/klipper-cnc-assistant` y debe alinearse con el SHA autorizado de `origin/main`. La configuracion fisica real permanece fuera de Git.
