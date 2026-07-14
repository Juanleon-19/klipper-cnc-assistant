# Klipper CNC Assistant

Klipper CNC Assistant es una aplicacion para preparar trabajos PCB sobre una CNC adaptada a Klipper, priorizando seguridad de maquina y separacion estricta entre analisis digital y control fisico.

## Estado actual

La entrega actual implementa la **Fase 2: aplicacion web remota MVP**.

Incluye:

- backend FastAPI en `src/klipper_cnc_assistant/`;
- frontend React + TypeScript + Vite en `frontend/`;
- persistencia JSON de proyectos PCB;
- gestion visual de operaciones por proyecto;
- carga segura de G-code;
- analisis inicial de G-code;
- vista previa 2D informativa;
- diagnostico del sistema;
- servicio systemd preparado;
- acceso remoto privado documentado para Tailscale Serve.

No incluye todavia:

- movimiento real;
- homing;
- jog;
- probe;
- mapa de alturas real;
- ejecucion de G-code;
- spindle;
- interfaz 3D;
- comunicacion nueva con Moonraker para mecanizado.

## Arquitectura

```text
Frontend React/Vite
        |
        v
FastAPI /api + SPA estatica
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
- No se envian comandos a la CNC.
- No se llama a endpoints reales de movimiento en Moonraker.
- G2/G3 solo se marcan como soporte incompleto.
- La vista previa es informativa, no una simulacion exacta.

## Instalacion del backend

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

Mas detalles: [docs/frontend.md](docs/frontend.md)

## Produccion local

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

Mas detalles: [docs/deployment.md](docs/deployment.md)

## Acceso remoto privado

La aplicacion debe exponerse solo mediante Tailscale Serve, manteniendo FastAPI en `127.0.0.1:8000`.

Guia: [docs/remote-access.md](docs/remote-access.md)

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

## Limitaciones actuales del analisis G-code

- soporta trayectorias lineales `G0` y `G1`;
- detecta `G20`, `G21`, `G90`, `G91`;
- marca `G2` y `G3` como soporte geometrico incompleto;
- detecta `G28` y `G92` como errores criticos;
- registra acciones manuales de husillo y cambios de herramienta;
- no ejecuta ni simula mecanizado real.
