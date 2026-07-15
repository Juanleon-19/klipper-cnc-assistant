# Klipper CNC Assistant

Klipper CNC Assistant prepara trabajos PCB sobre una CNC adaptada a Klipper. El principio operativo es separar claramente simulación, preparación digital y control físico supervisado.

## Estado actual

La entrega local implementa la **integración vertical del flujo físico, mapa medido, compensación y preflight de ejecución visibles en la aplicación**.

Incluye:

- backend FastAPI en `src/klipper_cnc_assistant/`;
- frontend React + TypeScript + Vite en `frontend/`;
- persistencia JSON con esquema `1.5`;
- jerarquía Proyecto -> Montaje -> Operaciones ordenadas y repetibles;
- G-code, análisis, herramienta, advertencias y trayectoria independientes por operación;
- referencias por montaje, referencias Z por herramienta y mapas físicos medidos por montaje/cara/revisión de colocación/configuración de malla;
- mapas simulados/importados, interpolación, plano, residuos, visor 2D y superficie 3D;
- previsualización y generación real de G-code compensado en `generated/compensated/`, con descarga desde la interfaz;
- validación de dominio con detalle de puntos fuera y distancia al dominio;
- modo `SIMULATED` predeterminado;
- modo `PHYSICAL` explícito mediante configuración, mostrado como `FÍSICO` en toda la UI;
- runtime físico singleton con Moonraker HTTP, WebSocket, Arduino, joystick discreto, botón externo, sonda y malla punto a punto;
- `MachineContext` como fuente única de estado visible en sidebar, Sistema y workspace;
- Sistema limitado a diagnóstico técnico, conexión, cancelación y `M112`;
- flujo físico productivo dentro del proyecto/montaje: Referencia, Mapa de alturas, Compensación y Ejecución.

No incluye todavía:

- arranque real de mecanizado sin confirmación física supervisada;
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
MOONRAKER_REQUEST_TIMEOUT=2
MACHINE_HOME_TIMEOUT=90
MACHINE_MOVE_TIMEOUT=8
MACHINE_SETTLE_TIMEOUT=0.02
TELEMETRY_STALE_TIMEOUT=2
SERIAL_STARTUP_DELAY=2
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

## Flujo físico implementado

1. Seleccionar proyecto, montaje, operación y herramienta.
2. Confirmar que sidebar, Sistema y workspace muestran `FÍSICO`.
3. Usar `Sistema` solo para diagnóstico técnico y conexión; la preparación productiva vive dentro del montaje.
4. Conectar Moonraker HTTP, WebSocket, Klipper y Arduino.
4. Revisar hilo serie, bytes, paquetes completos, paquetes válidos, checksums, edad del último paquete y causa exacta de bloqueo.
5. Ejecutar homing; el backend confirma `toolhead.homed_axes` y velocidad cero, no la duración de la petición HTTP.
6. Ingresar Z segura de traslado, mover Z, calcular centro real y mover X/Y al centro con Z segura.
7. Habilitar joystick X/Y discreto para ubicar el 0,0 del G-code de FlatCAM.
8. Armar referencia, pulsar botón externo o confirmar desde la interfaz, sondear Z en pasos discretos y guardar X/Y/Z medidos.
9. Generar mapa físico medido por montaje + cara + revisión de colocación + configuración de malla; la referencia Z queda separada por herramienta.
10. Revisar puntos, límites, separación, recorrido serpentino y estado.
11. Ejecutar la malla solo tras confirmación explícita: un punto por etapa, con persistencia inmediata.
12. Pausar, reanudar o cancelar conservando puntos medidos; `M112` queda separado como emergencia real.
13. Generar G-code compensado real en `generated/compensated/` conservando X/Y y aplicando `Z += delta_superficie(x,y)`.

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

Última validación local: backend 64 pruebas, frontend 37 pruebas, `pip check`, lint y build correctos. `npm run build` generó `frontend/dist/index.html` con `assets/index-DiGQGU_B.js` y conserva la advertencia no bloqueante de tamaño de Plotly.


### Corrección servida 2026-07-14

- `ReferenceSessionResponse` usa ahora `CapturedPosition { x_mm, y_mm, z_mm? }` para `posicion_captura`; las referencias físicas existentes se migran sin repetir sondeo.
- En `MACHINE_MODE=physical`, la pestaña Mapa de alturas abre directamente el flujo `MEASURED`: área desde operaciones, configuración de malla/sonda, armado, inicio, pausa, reanudación, cancelación y progreso. Los mapas simulados quedan como consulta secundaria.
- Build servido verificado: `frontend/dist/index.html` referencia `/assets/index-DNVlB1UT.js` y `/assets/index-yhqof53C.css`.
