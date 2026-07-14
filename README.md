# Klipper CNC Assistant

Klipper CNC Assistant es una aplicación para preparar trabajos PCB sobre una CNC adaptada a Klipper, priorizando seguridad de máquina y separación estricta entre análisis digital y control físico.

## Estado actual

La entrega actual implementa la **Fase 3: rediseño UX completo y visor técnico 2D V2**.

Incluye:

- backend FastAPI en `src/klipper_cnc_assistant/`;
- frontend React + TypeScript + Vite en `frontend/`;
- persistencia JSON real de proyectos PCB;
- gestión visual de proyectos y operaciones;
- carga segura de G-code por archivo;
- análisis G-code con `G0`, `G1`, `G2`, `G3`, `G20`, `G21`, `G90`, `G91` y `G94`;
- segmentos enriquecidos con línea, avance, Z, distancia y geometría para vista previa;
- visor técnico 2D V2 con `react-konva`, zoom, desplazamiento, encuadres, capas, inspector y recorrido visual;
- dashboard operativo y pantalla de sistema secundaria;
- servicio systemd preparado;
- acceso remoto privado documentado para Tailscale Serve.

No incluye todavía:

- movimiento real;
- homing;
- jog;
- probe;
- mapa de alturas real;
- ejecución de G-code;
- spindle;
- visualización 3D;
- Gerber;
- comunicación nueva de mecanizado con Moonraker.

## Arquitectura

```text
Frontend React/Vite
        |
        v
FastAPI /api + SPA estática
        |
        v
ProjectService / SystemStatusService
        |
        v
Dominio + Repositorio JSON + Analizador G-code
```

Detalles ampliados: [docs/architecture.md](docs/architecture.md)

## Seguridad

Todo permanece en **modo simulado**.

- No se ejecuta G-code.
- No se envían comandos a la CNC.
- No se llama a endpoints reales de movimiento en Moonraker.
- El visor es informativo, no una simulación de mecanizado.
- Las acciones de husillo externo siguen marcadas como manuales.

## Instalación del backend

```bash
source .venv/bin/activate
python -m pip install -e .
python -m pip check
```

## Desarrollo del backend

```bash
source .venv/bin/activate
PYTHONPATH=src python -m unittest discover -s tests -v
python -m klipper_cnc_assistant serve --host 127.0.0.1 --port 8000 --data-dir data
```

## Desarrollo del frontend

```bash
cd frontend
npm install
npm run dev
```

Más detalles: [docs/frontend.md](docs/frontend.md)

## Producción local

```bash
cd frontend
npm run build
cd ..
source .venv/bin/activate
python -m klipper_cnc_assistant serve --host 127.0.0.1 --port 8000 --data-dir data
```

Resultado esperado:

- `http://127.0.0.1:8000/`
- `http://127.0.0.1:8000/docs`
- `http://127.0.0.1:8000/api/health`

## Servicio systemd

Archivos:

- `deploy/systemd/klipper-cnc-assistant.service`
- `deploy/install_service.sh`
- `deploy/uninstall_service.sh`

Más detalles: [docs/deployment.md](docs/deployment.md)

## Acceso remoto privado

La aplicación debe exponerse solo mediante Tailscale Serve, manteniendo FastAPI en `127.0.0.1:8000`.

Guía: [docs/remote-access.md](docs/remote-access.md)

## Pruebas

Backend:

```bash
source .venv/bin/activate
PYTHONPATH=src python -m unittest discover -s tests -v
python -m pip check
```

Frontend:

```bash
cd frontend
npm run lint
npm run test
npm run build
```

## Limitaciones actuales del análisis G-code

- soporta líneas `G0` y `G1`;
- soporta representación geométrica determinista de `G2` y `G3` con `I/J`;
- rechaza arcos ambiguos o no representables con advertencia explícita;
- detecta `G28` y `G92` como errores críticos;
- registra acciones manuales de husillo y cambios de herramienta;
- no ejecuta ni simula mecanizado real.
