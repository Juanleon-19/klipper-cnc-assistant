# Nota de migración de configuración

La rama de Fase 1 deja de versionar `deploy/klipper-cnc-assistant.env` y cambia la unidad de referencia para cargar:

```text
/etc/klipper-cnc-assistant/klipper-cnc-assistant.env
```

Antes de desplegar la unidad nueva en el servidor activo:

```bash
sudo install -d -m 0750 -o root -g impresora /etc/klipper-cnc-assistant
sudo install -m 0640 -o root -g impresora \
  /home/impresora/klipper-cnc-assistant/deploy/klipper-cnc-assistant.env \
  /etc/klipper-cnc-assistant/klipper-cnc-assistant.env
```

Estos comandos deben ejecutarse mientras el archivo operativo anterior todavía exista. Después se revisa la copia y, en una intervención separada, se instala la unidad nueva. No reiniciar el servicio ni probar movimiento como parte del merge.
