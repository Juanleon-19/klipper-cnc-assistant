# Klipper CNC Assistant

Klipper CNC Assistant es una aplicación para preparar trabajos PCB sobre una CNC adaptada a Klipper, priorizando seguridad de máquina y separación estricta entre análisis digital y control físico.

## Estado actual

La entrega actual implementa la **Fase 4.3: estabilidad responsive, montajes y trayectorias independientes por operación**.

Incluye:

- backend FastAPI en `src/klipper_cnc_assistant/`;
- frontend React + TypeScript + Vite en `frontend/`;
- persistencia JSON real con jerarquía Proyecto → Montaje → Operaciones ordenadas y repetibles;
- migración automática de proyectos 1.3 a “Montaje principal” sin perder archivos, análisis ni mapas;
- carga segura de G-code por archivo;
- archivo, herramienta, análisis, trayectoria, advertencias y estado independientes por operación;
- análisis G-code con versionado persistido y detección de análisis obsoleto;
- soporte de `G0`, `G1`, `G2`, `G3`, `G20`, `G21`, `G90`, `G91` y `G94`;
- visor técnico 2D de trayectoria;
- selector de operación activa y guía de progreso global, por montaje y por operación;
- workflow interno por vistas: Archivo, Trayectoria, Referencia, Mapa de alturas y Validación;
- sesión de referencia completamente simulada con confirmaciones por pasos y sin control físico;
- mapa de alturas simulado con `probe_region`, zonas excluidas, superficie interpolada y vista 3D;
- previsualización matemática de compensación sobre la trayectoria, sin generar G-code ejecutable;
- servicio systemd preparado;
- acceso remoto privado documentado para Tailscale Serve.

No incluye todavía:

- movimiento real;
- homing real;
- jog real;
- probe físico;
- spindle controlado por la aplicación;
- ejecución de G-code;
- exportación de G-code compensado ejecutable;
- comandos de control hacia Moonraker.

## Arquitectura

```text
Frontend React/Vite
        |
        v
FastAPI /api + SPA estática
        |
        v
ProjectService / HeightMapService / ReferenceSessionService
        |
        v
Dominio + Repositorio JSON + Analizador G-code + Matemática de compensación
```

Detalles ampliados: [docs/architecture.md](docs/architecture.md)

## Seguridad

Todo permanece en **modo simulado**.

- No se ejecuta G-code.
- No se envían comandos de movimiento a la CNC.
- No se llama a endpoints de control de Moonraker para home, jog, probe o spindle.
- La referencia de máquina se confirma solo en simulación.
- La compensación actual es una vista previa matemática, no una salida para producción.

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

Si la aplicación se ejecuta como servicio, después de cada build de producción hay que reiniciarlo una sola vez para que FastAPI sirva el frontend nuevo:

```bash
sudo systemctl restart klipper-cnc-assistant.service
```

Resultado esperado:

- `http://127.0.0.1:8000/`
- `http://127.0.0.1:8000/docs`
- `http://127.0.0.1:8000/api/health`

## Flujo funcional actual

1. Crear un proyecto; el sistema crea “Montaje principal” automáticamente.
2. Añadir uno o más montajes y operaciones repetibles, asignando una herramienta a cada instancia.
3. Cargar y analizar el G-code propio de cada operación.
4. Confirmar en simulación la referencia de máquina, el origen X/Y y la referencia Z.
5. Configurar la región sondeable interior y las zonas excluidas.
6. Generar o importar el mapa de alturas.
7. Validar el mapa.
8. Abrir la previsualización matemática de compensación.

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

## Limitaciones actuales del análisis G-code y del mapa

- el análisis sigue siendo descriptivo, no ejecuta mecanizado real;
- la compensación no crea archivos descargables ni trayectorias ejecutables;
- la superficie de alturas actual es simulada o importada, no capturada desde hardware;
- el dominio interpolable queda limitado a `probe_region` menos las zonas excluidas.
