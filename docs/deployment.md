# Despliegue

## Modo de produccion local

1. Construir el frontend.
2. Servir FastAPI en `127.0.0.1:8000`.
3. Publicar acceso privado mediante Tailscale Serve.

## Backend

```bash
source .venv/bin/activate
python -m pip install -e .
python -m pip check
```

## Frontend

```bash
cd frontend
npm ci
npm run build
```

Después de `npm run build` en producción hay que reiniciar el servicio para que se sirvan juntos el backend y los assets nuevos:

```bash
sudo systemctl restart klipper-cnc-assistant.service
```

No es necesario modificar la unidad systemd ni repetir instalaciones para cada build. Opcionalmente se pueden definir `KCA_FRONTEND_BUILD` y `KCA_GIT_COMMIT` en el entorno del servicio.

## Servicio systemd

Archivos incluidos:

- `deploy/systemd/klipper-cnc-assistant.service`
- `deploy/install_service.sh`
- `deploy/uninstall_service.sh`
- `deploy/klipper-cnc-assistant.env.example`

El servicio:

- usa el usuario `impresora`;
- usa el directorio del repositorio como `WorkingDirectory`;
- escucha solo en `127.0.0.1:8000`;
- reinicia ante fallos;
- carga la configuración local desde `/etc/klipper-cnc-assistant/klipper-cnc-assistant.env`;
- no toca Moonraker ni hardware si `MACHINE_AUTO_CONNECT=false`;
- sirve la SPA desde `frontend/dist` o `KCA_FRONTEND_DIST`.

## Configuración local no versionada

La configuración operativa de la máquina no pertenece al repositorio. El archivo versionado es únicamente una plantilla segura en modo simulado:

```text
deploy/klipper-cnc-assistant.env.example
```

La copia efectiva debe vivir fuera del repositorio:

```text
/etc/klipper-cnc-assistant/klipper-cnc-assistant.env
```

Para una instalación nueva, `deploy/install_service.sh` crea esa copia con permisos `root:impresora` y modo `0640` si todavía no existe. Nunca reemplaza una configuración externa existente.

### Migración de una instalación anterior

Antes de actualizar una instalación cuyo servicio todavía lee `deploy/klipper-cnc-assistant.env`, preserve la configuración actual:

```bash
sudo install -d -m 0750 -o root -g impresora /etc/klipper-cnc-assistant
sudo install -m 0640 -o root -g impresora \
  deploy/klipper-cnc-assistant.env \
  /etc/klipper-cnc-assistant/klipper-cnc-assistant.env
```

Revise el archivo copiado y confirme que corresponde a la máquina correcta. Después instale la unidad nueva o actualice únicamente su línea `EnvironmentFile` para usar la ruta externa.

No elimine la configuración anterior hasta verificar:

```bash
sudo systemctl cat klipper-cnc-assistant.service
sudo systemctl show klipper-cnc-assistant.service -p EnvironmentFiles -p Environment
```

La migración y el reinicio de la aplicación requieren una acción separada y supervisada. Un merge o un `git pull` no autorizan por sí mismos un reinicio ni una prueba física.

## Instalacion del servicio

```bash
bash deploy/install_service.sh
```

## Desinstalacion del servicio

```bash
bash deploy/uninstall_service.sh
```

## Logs y estado

```bash
sudo systemctl status klipper-cnc-assistant.service
sudo journalctl -u klipper-cnc-assistant.service -n 100 --no-pager
```

## Variables de modo físico

El modo predeterminado es simulado. Para validación física supervisada configure explícitamente el archivo externo `/etc/klipper-cnc-assistant/klipper-cnc-assistant.env`.

Ejemplo parcial; los valores geométricos y de movimiento deben medirse y aprobarse para la máquina real:

```bash
MACHINE_MODE=physical
MACHINE_AUTO_CONNECT=false
MOONRAKER_URL=http://127.0.0.1:7126
MOONRAKER_WS=ws://127.0.0.1:7126/websocket
SERIAL_PORT=/dev/ttyUSB0
SERIAL_BAUDRATE=115200
MACHINE_SAFE_Z=10
```

No copie posiciones de referencia, cambio de herramienta ni límites desde documentación o historial sin validación física.

Después de `npm run build` debe reiniciarse el servicio de la aplicación para servir el nuevo frontend. No se requiere modificar systemd ni reiniciar Klipper para esta fase.

## Timeouts físicos

El servicio acepta variables separadas para transporte y operación física:

```bash
MOONRAKER_REQUEST_TIMEOUT=2
MACHINE_HOME_TIMEOUT=90
MACHINE_MOVE_TIMEOUT=8
MACHINE_SETTLE_TIMEOUT=0.02
TELEMETRY_STALE_TIMEOUT=2
SERIAL_STARTUP_DELAY=2
```

`MOONRAKER_REQUEST_TIMEOUT` no significa que el movimiento terminó; solo limita la llamada HTTP. Homing y movimientos se confirman por estado de Klipper. `SERIAL_STARTUP_DELAY` contempla el reinicio del Arduino al abrir `/dev/ttyUSB0`.

## Inicio del servicio físico para la prueba integral

No cambie firmware, pasos, límites ni configuración mecánica. No reinicie Klipper automáticamente.

1. Editar el archivo externo:

```bash
sudoedit /etc/klipper-cnc-assistant/klipper-cnc-assistant.env
```

2. Recargar systemd únicamente si cambió la unidad:

```bash
sudo systemctl daemon-reload
```

3. Reiniciar solo la aplicación:

```bash
sudo systemctl restart klipper-cnc-assistant.service
```

4. Verificar entorno efectivo:

```bash
sudo systemctl show klipper-cnc-assistant.service -p EnvironmentFiles -p Environment
```

Si `/dev/ttyUSB0` está ocupado, identifique el proceso con `sudo fuser -v /dev/ttyUSB0` y detenga solo ese proceso si corresponde. No use `M112` para liberar el puerto serie.

## Verificación del build servido

FastAPI registra rutas estáticas si existe `frontend/dist/index.html`. El build final verificado contiene:

```text
frontend/dist/index.html
frontend/dist/assets/index-DiGQGU_B.js
frontend/dist/assets/index-C_RZSt3A.css
frontend/dist/assets/plotly.min-CofRTlwV.js
```

Verificación local realizada con servidor temporal sin hardware:

```bash
MACHINE_MODE=simulated PYTHONPATH=src .venv/bin/python -m klipper_cnc_assistant serve --host 127.0.0.1 --port 8010 --data-dir /tmp/kca-served-check
curl -s http://127.0.0.1:8010/
curl -s http://127.0.0.1:8010/assets/index-DiGQGU_B.js
```

El HTML servido referenció `index-DiGQGU_B.js`. Para que el backend Python nuevo quede activo en el servicio real, reinicie solo la aplicación:

```bash
sudo systemctl restart klipper-cnc-assistant.service
```

Después de reiniciar, comprobar:

```bash
curl -s http://127.0.0.1:8000/ | grep index-DiGQGU_B
curl -s http://127.0.0.1:8000/api/machine/runtime
```

Si el navegador conserva assets anteriores, usar recarga forzada (`Ctrl+Shift+R`) o limpiar caché del sitio. No hay Service Worker registrado por la aplicación.

## Build servido verificado 2026-07-14

FastAPI sirve `frontend/dist` salvo que `KCA_FRONTEND_DIST` indique otra ruta. El build final verificado por `curl http://127.0.0.1:8000/` referencia:

```text
/assets/index-DNVlB1UT.js
/assets/index-yhqof53C.css
```

El backend real se recargó reiniciando solo el proceso de `klipper-cnc-assistant.service`; no se reiniciaron Klipper ni Moonraker y el runtime quedó en `DISCONNECTED` tras el reinicio.
