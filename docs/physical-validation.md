# Guía de validación física supervisada

Esta guía no debe ejecutarse sin operador presente, máquina despejada y parada física disponible.

## Preparación

1. Iniciar en modo simulado:
   `MACHINE_MODE=simulated`
2. Probar flujo web de proyecto, operaciones, referencias simuladas, mapa simulado y compensación.
3. Comprobar que no aparecen acciones físicas habilitadas en modo simulado.
4. Revisar el dominio de compensación; si hay puntos fuera, ampliar región sondeable o corregir referencias.

## Activación física

1. Detener solo el servicio de la aplicación si está corriendo.
2. Configurar explícitamente:
   `MACHINE_MODE=physical`
   `MOONRAKER_URL=http://127.0.0.1:7126`
   `MOONRAKER_WS=ws://127.0.0.1:7126/websocket`
   `SERIAL_PORT=/dev/ttyUSB0`
   `SERIAL_BAUDRATE=115200`
   `MACHINE_SAFE_Z=10`
3. Reiniciar el servicio de la aplicación después del build de producción.
4. No reiniciar Klipper automáticamente.

## Diagnóstico sin movimiento

1. Abrir la vista `Sistema`.
2. Verificar que el modo visible sea `FÍSICO`.
3. Pulsar `Conectar`.
4. Comprobar Moonraker HTTP conectado.
5. Comprobar WebSocket conectado.
6. Comprobar Klipper `ready`.
7. Comprobar puerto Arduino abierto.
8. Observar joystick sin movimiento en modo diagnóstico.
9. Comprobar botón del joystick.
10. Comprobar botón externo.
11. Comprobar señal de sonda sin movimiento.

## Inicialización

1. Ingresar un Z objetivo absoluto seguro, en milímetros.
2. Confirmar que el Z objetivo está dentro de límites reales de Klipper.
3. Confirmar inicialización desde la interfaz.
4. Verificar que se ejecuta homing.
5. Verificar que el sistema calcula:
   `center_x = (x_min + x_max) / 2`
   `center_y = (y_min + y_max) / 2`
6. Verificar movimiento primero a Z segura.
7. Verificar movimiento XY al centro.
8. Verificar movimiento final al Z objetivo ingresado.
9. Confirmar posición final y velocidad estable en diagnóstico.

## Joystick

1. Habilitar joystick solo después de inicialización correcta.
2. Probar `FINE` con espacio libre.
3. Probar `NORMAL` con espacio libre.
4. Probar `COARSE` solo con recorrido despejado.
5. Confirmar que solo se mueve una vez por transición `CENTER -> dirección`.
6. Confirmar que mantener el joystick inclinado no repite movimiento.
7. Confirmar que diagonales no producen movimiento.
8. Confirmar ciclo de modo con botón: `FINE -> NORMAL -> COARSE -> FINE`.

## Referencias físicas

1. Usar joystick para ubicar X/Y del origen del montaje.
2. Capturar origen X/Y físico desde la posición actual.
3. Confirmar que el montaje muestra fuente `MEASURED`.
4. Solicitar sonda de un punto.
5. Verificar que aparece `PROBE_REQUESTED`.
6. Confirmar sonda desde la interfaz.
7. Verificar descenso por pasos discretos.
8. Verificar que el contacto detiene nuevos pasos descendentes.
9. Verificar retracto seguro.
10. Guardar referencia Z desde el último contacto de sonda, no desde el Z retraído.

## Fallos que deben probarse

1. Desconectar Arduino y confirmar bloqueo por serie obsoleta.
2. Cortar telemetría y confirmar bloqueo por telemetría obsoleta.
3. Cancelar operación durante preparación o sonda.
4. Probar parada segura.
5. Probar emergencia `M112` solo con confirmación y operador listo para recuperación.

## Criterio de aceptación física

- La aplicación nunca se mueve en modo simulado.
- El modo físico requiere configuración explícita.
- El diagnóstico no declara `HEALTHY` solo porque la API responde.
- X/Y nunca se mueve con Z insegura.
- El joystick permanece discreto y cardinal.
- La sonda guarda el punto de contacto y retrae.
- Todas las condiciones de bloqueo muestran motivo concreto.
