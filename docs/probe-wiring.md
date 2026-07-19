# Conexión eléctrica de la sonda CNC

## Propósito y alcance

Esta sonda detecta el contacto eléctrico entre la herramienta de la CNC y una placa de cobre conductora para comunicar el estado al controlador Arduino. La conexión y el firmware descritos aquí fueron validados físicamente con un Arduino Pro Mini ATmega328P a 16 MHz y el firmware del commit `f9e12bb fix(firmware): release probe state after contact`.

El firmware solo publica el estado de entrada: no mueve ejes, no inicia sondeos y no controla el spindle. La validación de referencia Z y mallas es una operación física posterior, supervisada y autorizada.

## Conexión definitiva validada

| Elemento | Conectar a | Condición |
|---|---|---|
| Herramienta, fresa, chuck o punta del motor | GND del Arduino | Conductor de retorno de contacto |
| Placa de cobre o superficie conductora | D4 del Arduino | Debe permanecer aislada de GND sin contacto |
| Arduino Pro Mini | ATmega328P, 16 MHz | D4 usa `INPUT_PULLUP` interno |

```text
                    ARDUINO PRO MINI

    5 V
     │
     └── INPUT_PULLUP interno
                 │
                 D4 ───────── placa de cobre aislada
                                  │
                              contacto físico
                                  │
    GND ───────── herramienta / fresa / chuck
```

La placa debe estar eléctricamente aislada de GND mientras no existe contacto con la herramienta.

## Semántica eléctrica y del paquete

| Condición | Nivel D4 | Estado de sonda | Bit 2 del paquete de 8 bytes |
|---|---|---|---|
| Herramienta separada | HIGH por `INPUT_PULLUP` | `OPEN` | `probe=false` |
| Herramienta toca placa | LOW por conexión a GND | `TRIGGERED` | `probe=true` |
| Herramienta retirada | HIGH estable durante 40 ms | `OPEN` | `probe=false` |

```text
Sin contacto: D4 = HIGH -> OPEN -> probe=false
Con contacto: D4 = LOW  -> TRIGGERED -> probe=true
Después de retirar: D4 = HIGH estable durante 40 ms -> OPEN -> probe=false
```

El protocolo mantiene exactamente ocho bytes a 115200 baudios. La sonda ocupa el bit 2 de `flags`; `false` significa `OPEN` y `true` significa `TRIGGERED`.

## Filtro del firmware

`PIN_PROBE` es D4 y se configura como `INPUT_PULLUP`. El filtro temporal no modifica el protocolo:

- Una activación LOW debe mantenerse 20 ms para pasar a `TRIGGERED`.
- Una liberación HIGH debe mantenerse 40 ms para volver a `OPEN`.

Esto evita publicar pulsos breves y permite liberar un contacto confirmado. No interprete una transición de paquete como una orden de movimiento.

## Conexión correcta e incorrecta

La conexión correcta es herramienta/chuck a GND y placa aislada a D4. Esta produjo repetidamente `OPEN -> TRIGGERED -> OPEN`.

La conexión incorrecta que causó el problema era conectar D4 directamente a la herramienta o punta del motor. No use esa conexión: puede acoplar ruido o pulsos LOW desde el spindle/motor a D4.

### Causa del enclavamiento aparente

Con la conexión incorrecta, después del primer contacto la lectura cruda alternaba HIGH/LOW. Por ello el candidato de liberación nunca se mantenía HIGH los 40 ms requeridos y el estado filtrado permanecía `true`. El diagnóstico observó:

```text
Antes del contacto:            level=1 raw=0 candidate=0 filtered=0
Durante el contacto:           level=0 raw=1 candidate=1 filtered=1
Después, conexión incorrecta:  level alterna 1/0; raw alterna 0/1;
                               filtered permanece 1
Conexión definitiva:           OPEN -> TRIGGERED -> OPEN
```

No era una retención intencional de contactos previos: era una entrada que no alcanzaba 40 ms continuos en HIGH.

## Procedimiento de validación

No ejecute este procedimiento si no cuenta con autorización específica del operador y condiciones físicas seguras. No requiere mover la máquina para la prueba serial.

1. Detenga el servicio:

   ```bash
   sudo systemctl stop klipper-cnc-assistant.service
   ```

2. Ejecute la prueba serial directa:

   ```bash
   cd /home/impresora/klipper-cnc-assistant
   .venv/bin/python firmware/arduino_pro_mini/tests/005_serial_protocol/test_serial_protocol.py
   ```

3. Repita al menos diez veces: separado -> `OPEN`, contacto -> `TRIGGERED`, separado -> `OPEN`.

4. Inicie el servicio:

   ```bash
   sudo systemctl start klipper-cnc-assistant.service
   ```

5. Confirme desde `/api/machine/status`: separado -> `probe=false`, contacto -> `probe=true`, separado -> `probe=false`.

6. Valide referencia Z.
7. Valide una malla 2×2.
8. Solo después valide una malla 3×3.

Los pasos 6–8 pueden implicar movimiento físico y exigen un procedimiento aprobado, límites, recuperación y supervisión independiente.

## Diagnóstico

| Síntoma | Revisión inicial |
|---|---|
| D4 siempre LOW | Revise un cortocircuito entre placa y GND. |
| D4 alterna HIGH/LOW con la herramienta conectada | Hay ruido o acoplamiento desde spindle/motor; confirme que D4 está en la placa, no en la herramienta. |
| D4 vuelve HIGH pero el paquete sigue `true` | Revise que esté cargado el firmware validado y repita la prueba serial directa. |
| Prueba serial correcta pero interfaz incorrecta | Revise backend/runtime. |
| Estado correcto sin motor pero falla con motor | Revise cableado, separación y filtrado eléctrico. |

## Mejoras eléctricas opcionales no confirmadas como instaladas

Las siguientes mejoras son opcionales. No se documentan como componentes instalados en esta máquina:

- pull-up externo de 4.7 kΩ entre D4 y 5 V;
- resistencia serie de 1 kΩ entre la placa y D4;
- capacitor de 47–100 nF entre D4 y GND, cerca del Arduino;
- cable de señal y GND trenzado;
- separación del cable de sonda respecto de motores y potencia.

## Advertencias de seguridad

- No conecte D4 directamente al spindle, motor, herramienta o chuck.
- No asuma que la máquina está homed ni que la telemetría o el puerto serie son recientes solo porque estén conectados.
- Detenga el servicio antes de abrir el monitor serial para evitar competencia por el puerto; restáurelo solo al terminar la prueba.
- No envíe G-code, homing, sondeo, spindle ni movimientos durante una comprobación de cableado sin autorización específica del operador.
- Antes de cualquier malla, confirme al menos diez ciclos completos `OPEN -> TRIGGERED -> OPEN`.
