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

El servicio:

- usa el usuario `impresora`;
- usa el directorio del repositorio como `WorkingDirectory`;
- escucha solo en `127.0.0.1:8000`;
- reinicia ante fallos;
- mantiene modo simulado;
- no toca Moonraker ni hardware nuevo.

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

El modo predeterminado es simulado. Para validación física supervisada configure explícitamente:

```bash
MACHINE_MODE=physical
MACHINE_AUTO_CONNECT=false
MOONRAKER_URL=http://127.0.0.1:7126
MOONRAKER_WS=ws://127.0.0.1:7126/websocket
SERIAL_PORT=/dev/ttyUSB0
SERIAL_BAUDRATE=115200
MACHINE_SAFE_Z=10
```

Después de `npm run build` debe reiniciarse el servicio de la aplicación para servir el nuevo frontend. No se requiere modificar systemd ni reiniciar Klipper para esta fase.
