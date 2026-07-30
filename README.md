# Klipper CNC Assistant

Estado de esta rama: Fase 1 de auditoria, arquitectura y organizacion.
Fecha de referencia: Thursday, July 30, 2026.

Klipper CNC Assistant es una aplicacion web para preparar trabajos CNC basados en G-code sobre una maquina adaptada a Klipper. El producto combina un backend FastAPI, un frontend React, persistencia JSON local y una frontera fisica que interactua con Moonraker, Klipper y un controlador Arduino.

## Estado actual verificado

En esta rama se audito el estado real del repositorio, se verifico la ruta servida por `systemd` y se reorganizo el codigo sin introducir cambios funcionales deliberados.

Capacidades comprobadas en software:

- gestion de proyectos, montajes y operaciones;
- analisis de G-code y visor tecnico;
- persistencia JSON local en `data/projects/`;
- backend FastAPI servido desde `src/klipper_cnc_assistant/`;
- frontend React/Vite servido desde `frontend/dist` cuando existe build;
- integracion de modulos fisicos para Moonraker, estado de maquina, serie y jog;
- consola de ejecucion y servicios de ejecucion presentes en codigo;
- documentacion de auditoria y arquitectura actualizada.

Funciones que existen en codigo pero no quedan cerradas en esta fase:

- Referencias;
- Arduino y reconexion serie;
- mapa de alturas medido y compensacion fisica;
- ejecucion fisica de trabajos y recuperacion.

Esas areas se preservan y documentan, pero su reparacion funcional queda diferida a las Fases 2 a 4.

## Seguridad

Reglas permanentes del repositorio:

- no trabajar directamente en `main`;
- no enviar G-code, homing, jog, probe, spindle ni ejecucion de trabajos sin autorizacion explicita del usuario;
- no reiniciar ni reconfigurar `systemd`, Klipper, Moonraker, Arduino ni la maquina durante una fase de auditoria o reorganizacion;
- no publicar secretos, `.env`, datos reales de produccion, mapas fisicos, referencias reales ni G-code privado;
- tratar `deploy/klipper-cnc-assistant.env` como configuracion operativa local heredada, no como plantilla canonica.

El host auditado en Thursday, July 30, 2026 tiene un servicio activo en modo fisico. Por esa razon la validacion automatizada de esta fase se limito a comandos seguros que no envian movimiento.

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

Auditoria y procedencia funcional: [docs/recovery/current-project-audit.md](docs/recovery/current-project-audit.md)

Plan por fases: [PLAN.md](PLAN.md)

## Instalacion

Backend:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m pip check
```

Frontend:

```bash
cd frontend
npm ci
```

## Desarrollo

Backend local:

```bash
source .venv/bin/activate
PYTHONPATH=src python -m klipper_cnc_assistant serve --host 127.0.0.1 --port 8010 --data-dir /tmp/kca-dev-data
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

Linea base backend pedida por el proyecto:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

En un host con runtime fisico activo, ejecutar solo en un entorno controlado que garantice `MACHINE_MODE=simulated` y que no inicialice hardware.

Linea segura usada en esta fase:

```bash
MACHINE_MODE=simulated PYTHONPATH=src .venv/bin/python -m unittest -v   tests.test_api   tests.test_gcode_analysis   tests.test_heightmap   tests.test_job_service   tests.test_moonraker_client   tests.test_project_service   tests.test_web_mvp
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

El despliegue real auditado en Thursday, July 30, 2026 corre desde `/home/impresora/klipper-cnc-assistant`, usa la venv local y escucha en `127.0.0.1:8000`. Esta fase no reinicia ese servicio ni cambia su configuracion real.

## Estado de la fase

- Fase 1 activa en `fase-1/auditoria-arquitectura`.
- No se hizo merge a `main`.
- La reorganizacion es estructural; las reparaciones funcionales siguen pendientes.
- La publicacion de la rama debe revisarse con especial cuidado por la existencia historica de configuracion operativa local en `deploy/`.
