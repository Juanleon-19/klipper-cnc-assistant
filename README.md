# Klipper CNC Assistant

Estado de referencia: **Fase 5 — estabilización y cierre**.
Fecha de referencia: 2026-08-16.

Klipper CNC Assistant es una aplicación web para preparar y ejecutar trabajos CNC basados en G-code sobre una máquina controlada por Klipper. El producto combina un backend FastAPI, un frontend React, persistencia JSON local y una frontera física que integra Moonraker, Klipper y un controlador Arduino.

## Estado actual

Las fases funcionales principales ya están implementadas e integradas en `main`:

- gestión de proyectos, montajes y operaciones;
- análisis de G-code y visor técnico;
- reordenamiento rápido y persistente de operaciones;
- runtime físico con Moonraker HTTP/WebSocket y Arduino;
- conexión/reconexión, homing y referencias X/Y/Z;
- preview y sondeo de mapa de alturas físico;
- persistencia y recuperación del mapa;
- compensación legacy/adaptive y auditoría;
- generación de artefactos compensados;
- plan y preflight multioperación;
- JobRun, upload Moonraker, progreso y ETA;
- cambio de herramienta, nueva referencia y regeneración de operaciones posteriores;
- pausa, cancelación, recuperación y cierre de ejecuciones obsoletas.

La campaña activa es la **validación física integral de cierre**. No se considera una fase de desarrollo de nuevas funcionalidades.

## Línea base de validación final

- SHA: `af0099dda64fd9394045766b8475b689cf69a320`
- Rama: `baseline/physical-validation-2026-08-16`
- Estado operativo: [docs/CURRENT_STATE.md](docs/CURRENT_STATE.md)
- Checklist de validación: [docs/FINAL_VALIDATION.md](docs/FINAL_VALIDATION.md)
- Plan por fases: [PLAN.md](PLAN.md)

## Política de cambios

- No trabajar directamente en `main`.
- Todo defecto reproducible descubierto durante validación se corrige en una rama `hotfix/...` independiente.
- Cada cambio funcional requiere pruebas, PR, CI y aprobación explícita antes de merge/deploy.
- La rama `baseline/physical-validation-2026-08-16` no se mueve durante la campaña.
- No enviar G-code, homing, jog, probe, spindle ni ejecución física sin autorización explícita.
- No reiniciar Klipper o Moonraker como parte de un cambio de aplicación salvo que exista una necesidad distinta y explícitamente autorizada.
- No versionar secretos, configuración operativa real, mapas físicos, referencias reales ni G-code privado.

## Arquitectura

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

## Instalación de desarrollo

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

## Pruebas seguras

Backend:

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

Las pruebas automatizadas no sustituyen la campaña física descrita en `docs/FINAL_VALIDATION.md`.
