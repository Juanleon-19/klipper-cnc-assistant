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
