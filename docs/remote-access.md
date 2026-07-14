# Acceso remoto privado

## Requisito

El dispositivo cliente debe pertenecer a la misma tailnet que el host donde corre Klipper CNC Assistant.

## Publicacion privada

FastAPI debe seguir escuchando solo en `127.0.0.1:8000`.

La publicacion privada se realiza con:

```bash
sudo tailscale serve --bg http://127.0.0.1:8000
```

No usar `tailscale funnel`.

## URLs privadas

URL privada actual del host:

- Aplicacion: `https://impresora-vostro-3458.tail923898.ts.net/`
- Documentacion: `https://impresora-vostro-3458.tail923898.ts.net/docs`
- Health: `https://impresora-vostro-3458.tail923898.ts.net/api/health`

Para verificar o recuperar la URL HTTPS privada:

```bash
sudo tailscale serve status
tailscale status
```

La publicacion actual en este host apunta a:

```text
https://impresora-vostro-3458.tail923898.ts.net
  └── /  ->  http://127.0.0.1:8000
```

## Diagnostico rapido

```bash
tailscale status
sudo tailscale serve status
tailscale ping impresora-vostro-3458
```

## Detener Tailscale Serve

```bash
sudo tailscale serve reset
```

## Reiniciar la aplicacion

```bash
sudo systemctl restart klipper-cnc-assistant.service
```

## Ver logs

```bash
sudo journalctl -u klipper-cnc-assistant.service -n 100 --no-pager
```
