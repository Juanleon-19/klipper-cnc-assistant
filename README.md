# Klipper CNC Assistant

Klipper CNC Assistant prepara trabajos PCB sobre una CNC adaptada a Klipper. El principio operativo es separar claramente simulación, preparación digital y control físico supervisado.

## Estado actual

La entrega local implementa la **Fase 1: integración física segura inicial y correcciones de producto**.

Incluye:

- backend FastAPI en `src/klipper_cnc_assistant/`;
- frontend React + TypeScript + Vite en `frontend/`;
- persistencia JSON con esquema `1.5`;
- jerarquía Proyecto -> Montaje -> Operaciones ordenadas y repetibles;
- G-code, análisis, herramienta, advertencias y trayectoria independientes por operación;
- referencias y mapa compartidos por montaje;
- mapas simulados/importados, interpolación, plano, residuos, visor 2D y superficie 3D;
- previsualización matemática de compensación, sin exportación ejecutable;
- validación de dominio con detalle de puntos fuera y distancia al dominio;
- modo `SIMULATED` predeterminado;
- modo `PHYSICAL` explícito mediante configuración;
- runtime físico singleton con Moonraker HTTP, WebSocket, Arduino, joystick, botones y sonda;
- vista “Sistema físico” con diagnóstico consolidado y acciones físicas explícitas.

No incluye todavía:

- sondeo físico de malla completa;
- generación o descarga de G-code compensado ejecutable;
- ejecución completa de trabajos;
- spindle automático;
- jog continuo;
- cambios automáticos de herramienta.

## Arquitectura

```text
Frontend React/Vite
        |
        v
FastAPI /api + SPA estática
        |
        +-- Servicios de proyecto, montaje, operación, mapas y referencias
        |
        +-- MachineRuntime singleton
                +-- MoonrakerClient HTTP
                +-- MoonrakerTelemetry WebSocket
                +-- MachineState
                +-- SerialDriver + CommandMapper
                +-- ManualJogController -> JogController -> Moonraker
```

Detalles ampliados: [docs/architecture.md](docs/architecture.md)

## Seguridad

Modo predeterminado:

```bash
MACHINE_MODE=simulated
```

En modo simulado:

- no se abre puerto serie;
- no se inicia telemetría física;
- no se envían movimientos;
- no se habilitan controles reales;
- los datos simulados no se presentan como mediciones físicas.

Modo físico explícito:

```bash
MACHINE_MODE=physical
MOONRAKER_URL=http://127.0.0.1:7126
MOONRAKER_WS=ws://127.0.0.1:7126/websocket
SERIAL_PORT=/dev/ttyUSB0
SERIAL_BAUDRATE=115200
MACHINE_SAFE_Z=10
```

Hay dos instancias Moonraker detectadas en la máquina local; por seguridad no se elige una automáticamente. Configure URL y WebSocket de forma explícita.

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

No reinicie Klipper ni systemd de Klipper salvo que esté validando físicamente y sepa qué instancia corresponde.

## Flujo web principal

1. Crear proyecto y montaje.
2. Crear operaciones repetibles y asignar herramientas.
3. Cargar G-code propio de cada operación.
4. Analizar cada operación.
5. Confirmar referencias simuladas o capturar referencias físicas del montaje.
6. Configurar región sondeable y exclusiones.
7. Generar/importar mapa.
8. Validar cobertura del mapa.
9. Previsualizar compensación matemática.

## Flujo físico de Fase 1

1. Abrir “Sistema”.
2. Confirmar que la etiqueta muestra `FÍSICO`.
3. Conectar Moonraker/Klipper/Arduino.
4. Revisar diagnóstico y seguridad.
5. Ingresar Z objetivo absoluto en mm.
6. Confirmar inicialización.
7. El backend ejecuta homing, calcula centro con límites reales, mueve Z segura, mueve XY al centro y mueve a Z objetivo.
8. Habilitar joystick solo después de inicialización correcta.
9. Capturar origen X/Y desde la posición actual.
10. Solicitar sonda, confirmar sonda y guardar referencia Z desde el contacto.

Guía detallada: [docs/physical-validation.md](docs/physical-validation.md)

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

Última validación local: backend 57 pruebas, frontend 37 pruebas, `pip check`, lint y build correctos.
