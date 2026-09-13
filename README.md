# Klipper CNC Assistant

Estado de referencia: cierre de software en `release/final-software-stabilization-2026-09-12`.
Fecha de referencia: 12 de septiembre de 2026.

Klipper CNC Assistant es una aplicacion web para preparar trabajos CNC basados en G-code sobre una maquina controlada por Klipper. El producto combina un backend FastAPI, un frontend React, persistencia JSON local y una frontera fisica que integra Moonraker, Klipper y un controlador Arduino.

## Capacidades confirmadas

- gestion de proyectos, montajes y operaciones;
- analisis de G-code y visor tecnico;
- persistencia JSON local de proyectos, referencias, mapas y artefactos;
- runtime fisico con Moonraker HTTP, Moonraker WebSocket y entrada serial Arduino;
- reconexion segura del Arduino sobre una unica autoridad serial;
- semantica separada de transporte WebSocket, frescura de posicion y observacion HTTP;
- captura de referencias fisicas basada en observacion activa antes de persistir;
- pestana `Referencia` extraida como feature del frontend.

## Cierre de software y siguiente etapa

La base incorpora ownership físico, autorización por frescura, process lock,
JobRunStore con CAS y cancelación dominante, PrintIdentity, worker único de mapa,
PhysicalReferenceToken, identidad serial verificable y exclusiva, y persistencia
atómica con revisiones y comprobación de generaciones/hash de artefactos.
También conserva el flujo humano de cambio de herramienta y las velocidades Z
auxiliares de PR #24; el feed de mecanizado sigue viniendo del G-code FlatCAM.

Las lecturas no preparan ejecuciones ni publican planes/mapas. Preparar JobRun,
abrir proyecto y finalizar un mapa recuperado son acciones POST explícitas.
La confirmación de spindle es humana y queda vinculada al run, operación y sesión.

La validación física con el usuario sigue pendiente. `MACHINE_MODE=simulated`
permite preparación, referencias y mapa simulados, pero no implementa un impresor
Moonraker ni probe productivo: un JobRun físico permanece bloqueado por preflight.
Las pruebas con fakes verifican el workflow sin afirmar movimiento real.

Resultados y límites del cierre: [validación final de software](docs/final-software-validation.md).

## Seguridad

- No trabajar directamente en `main`.
- No enviar G-code, homing, jog, probe, spindle ni ejecucion de trabajos sin autorizacion explicita del usuario.
- No reiniciar ni reconfigurar `systemd`, Klipper, Moonraker, Arduino ni la maquina durante desarrollo o pruebas seguras.
- No publicar secretos, `.env`, datos reales de produccion, mapas fisicos, referencias reales ni G-code privado.
- Mantener la configuracion operativa fuera del repositorio, en `/etc/klipper-cnc-assistant/`.
- Usar solo configuraciones seguras o simuladas durante pruebas automatizadas.

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

Auditoria Fase 1: [docs/recovery/current-project-audit.md](docs/recovery/current-project-audit.md)

Linea base de Fase 2: [docs/phase-2/references-connectivity-baseline.md](docs/phase-2/references-connectivity-baseline.md)

Resultado de Fase 2: [docs/phase-2/references-connectivity-result.md](docs/phase-2/references-connectivity-result.md)

Plan por fases: [PLAN.md](PLAN.md)

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

Linea segura de backend usada en Fase 2:

```bash
source .venv/bin/activate
MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONPATH=src python -m unittest discover -s tests -v
```

Si alguna suite intenta inicializar hardware, ejecutar la linea base documentada en `docs/phase-2/references-connectivity-baseline.md` mas las pruebas nuevas de Fase 2.

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

La aplicacion activa del host no se modifica desde esta rama. El desarrollo de Fase 2 se realiza exclusivamente en `/home/impresora/klipper-cnc-assistant-fase2`.

## Estado de fases

- Fase 1: completada y fusionada en `main`.
- Fase 2: activa en `fase-2/referencias-conectividad`.
- Fase 3: pendiente.
- Fase 4: pendiente.
