# P1-07 — Identidad serial verificable y exclusividad física

Base exacta: `90bcc6cfc9bc05d0d53e18588d2f330084ac4c82`.
Rama: `stabilization/p1-07-serial-identity-exclusive-2026-09-12`.

## Contrato

Runtime transmite el modo al manager existente. En físico, discovery solo
considera el SERIAL_PORT configurado, resuelto a su dispositivo canónico.
La primera conexión exige VID/PID válidos y una serie USB no placeholder,
o VID/PID/location para un by-path que resuelve al dispositivo. La configuración
selecciona el endpoint; la metadata de discovery establece el primer vínculo
verificable. No se añade una lista de dispositivos ni una autoridad nueva.
La identidad se fija únicamente después del primer paquete válido y de volver
a comprobar identidad/destino. Reconnect conserva el vínculo conocido y
compara VID/PID/serie/location; una diferencia no publica sesión/generación.

SerialDriver exige exclusividad por defecto. Si PySerial no reconoce exclusive,
el OS no permite verificarlo o el descriptor no confirma exclusive=True, se
rechaza la conexión física sin abrir un fallback compartido. El manager también
rechaza drivers sin evidencia de exclusividad. Un descriptor rechazado no puede
reutilizarse mediante read_packet; el worker propietario lo cierra en finally.
El fallback compartido requiere política no física explícita. Runtime simulated
no descubre ni abre dispositivos reales.

Hotplug, retry, by-path, manager epoch, generación, cancel-read, cierre desde
worker y recuperación EBADF mantienen su implementación existente. La validación
adicional se ejecuta antes de publicar sesión/paquetes. No se modifican P1-04,
otros P1, frontend, configuración operativa ni las autoridades P0/PR24.

## Pruebas

Todas las aperturas y descubrimientos en los tests físicos son fake. Los nombres
de puerto, metadata y paquetes son sintéticos; no se inspeccionó hardware.
Se verificaron conexión física válida/exclusiva, identidad insuficiente,
incompatibilidad VID/PID/location/serie, keyword/OS/feature no exclusiva, driver
sin evidencia, simulated, hotplug desde ausencia, distractores, cambio de
identidad durante apertura y EBADF con el mismo owner y nueva generación.

```bash
MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONPATH=src:tests python -m unittest tests.test_serial_physical_policy tests.test_connection_manager tests.test_serial_driver_lifecycle tests.test_runtime_serial_lifecycle tests.test_runtime_reconnect_hotfix tests.test_machine_runtime tests.test_physical_process_lock tests.test_physical_ownership tests.test_motion_authorization -v
```

Focalizados finales: **142/142 OK**, 7,884 segundos. La primera ronda de
125 pruebas pasó antes de añadir dos casos y ampliar regresiones de ownership.
Las fixtures anteriores de fallback se declaran no físicas explícitamente;
la fixture runtime físico de hotplug aporta metadata y exclusividad fake válidas.

```bash
MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONPATH=src:tests python -m unittest discover -s tests -v
git diff --check
```

Suite completa: **464/464 OK**, 77,650 segundos; una sola ejecución
después de pasar focalizados. `git diff --check`: **OK**.
Frontend no requerido, sin cambios.
Entrega local, sin push/merge, comandos físicos ni reinicios de servicios.
