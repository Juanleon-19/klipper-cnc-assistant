# Joystick Controller — Arduino Pro Mini

## Objetivo

Este firmware lee el joystick, los botones y la sonda CNC, y transmite el estado mediante el protocolo binario existente. No controla movimientos de la máquina.

## Hardware y protocolo

- Placa: Arduino Pro Mini, ATmega328P, 16 MHz.
- FQBN: `arduino:avr:pro:cpu=16MHzatmega328`.
- Velocidad serial: `115200` baudios.
- Sonda: D4 configurado como `INPUT_PULLUP`.
- Filtro de sonda: activación de 20 ms y liberación de 40 ms.
- Protocolo: paquete serial de 8 bytes, sin cambios.

La semántica de la sonda es `false = OPEN` y `true = TRIGGERED`. Consulte la conexión eléctrica validada en [../../../docs/probe-wiring.md](../../../docs/probe-wiring.md).

## Compilar

```bash
cd /home/impresora/klipper-cnc-assistant/firmware/arduino_pro_mini/joystick_controller

rm -rf build
mkdir -p build

arduino-cli compile \
  --fqbn arduino:avr:pro:cpu=16MHzatmega328 \
  --output-dir build \
  .
```

`build/` es un artefacto local de compilación y no debe añadirse al control de versiones.

## Cargar firmware

La carga es una operación física: realícela solo con autorización del operador y con la máquina en una condición segura. El comando usado para la conexión validada es:

```bash
arduino-cli upload \
  --fqbn arduino:avr:pro:cpu=16MHzatmega328 \
  --port /dev/serial/by-path/pci-0000:00:14.0-usb-0:3:1.0-port0 \
  --input-dir build \
  --verbose
```

Cuando `avrdude` lo requiera, pulse manualmente `RESET` en el Pro Mini durante el intento de carga. No use rutas temporales como firmware oficial.

## Prueba serial directa

1. Detenga el servicio para que no compita por el puerto serie:

   ```bash
   sudo systemctl stop klipper-cnc-assistant.service
   ```

2. Conecte el Arduino y ejecute el monitor existente:

   ```bash
   cd /home/impresora/klipper-cnc-assistant
   .venv/bin/python firmware/arduino_pro_mini/tests/005_serial_protocol/test_serial_protocol.py
   ```

3. El monitor debe informar `Probe=OPEN` separado y cambios `SONDA -> TRIGGERED` al tocar. Termine con `Ctrl+C`.

4. Inicie de nuevo el servicio cuando la prueba termine:

   ```bash
   sudo systemctl start klipper-cnc-assistant.service
   ```

## Validación de la sonda

Con la herramienta/chuck a GND y la placa aislada a D4, repita al menos diez veces esta secuencia durante la prueba serial directa:

```text
separado  -> OPEN
contacto  -> TRIGGERED
separado  -> OPEN
```

La liberación se informa solo tras 40 ms estables en `HIGH`. Después, confirme en `/api/machine/status` que la misma secuencia aparece como `probe=false`, `probe=true`, `probe=false` antes de validar referencia Z, malla 2×2 y, solo después, malla 3×3.

## Estado validado

La lógica actual fue compilada, cargada y validada físicamente en el commit `f9e12bb fix(firmware): release probe state after contact`.
