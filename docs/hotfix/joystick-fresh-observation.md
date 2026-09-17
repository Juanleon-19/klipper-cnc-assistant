# Joystick bloqueado tras reposo

El runtime podia recibir paquetes Arduino y mensajes WebSocket recientes mientras la posicion y la observacion `ready` de Klipper ya estaban obsoletas. `_manual_move` solicitaba autorizacion sin refrescar esos datos: el autorizador rechazaba correctamente el movimiento, pero el siguiente toque repetia el mismo fallo.

El cambio consulta HTTP mediante `_refresh_machine` dentro del permiso exclusivo de cada intencion manual aceptada, antes de calcular el destino. Mantiene la revalidacion previa al envio, el homing obligatorio, los limites, la frescura serial, la cancelacion y la barrera centro/cardinal. Los errores de consulta se presentan como errores de runtime y no terminan el callback serial. Solo un jog completado y confirmado permite salir de `DEGRADED`; los estados de referencia ya capturada se conservan.

## Validacion offline

La prueba de regresion fallo antes de modificar el runtime: no se realizaba ninguna consulta de posicion. Con la correccion, un cache X=10 y una observacion HTTP X=40 producen un destino X=41 en modo NORMAL, sin homing. Todos los clientes, puertos y permisos fisicos utilizados por las pruebas son dobles locales; no se inicializa hardware.

```sh
MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONPATH=src python -m unittest \
  tests.test_joystick_fresh_observation tests.test_machine_runtime \
  tests.test_motion_authorization tests.test_physical_ownership \
  tests.test_runtime_serial_lifecycle tests.test_serial_driver_lifecycle \
  tests.test_serial_physical_policy tests.test_reference_go_to_state_hotfix -q
```

Resultado: 143 pruebas PASS, incluidas 16 nuevas. Se verifican consultas fallidas o malformadas, posicion ausente, Klipper no ready, perdida de homing, ownership de trabajo, proceso sin acceso, cancelacion, cambio de generacion Arduino, deshabilitacion del control durante la consulta, consulta lenta y joystick sostenido. El fake de movimiento de estas pruebas interpreta G91 para confirmar el destino real del jog relativo.

Alcance: correccion backend, sin cambios de firmware, configuracion operativa ni frontend. No se ejecutaron suites generales de producto ni pruebas de movimiento fisico. Queda pendiente desplegar y validar el joystick por el operador. Cada toque aceptado incorpora la latencia de la consulta HTTP existente; si esta excede la vigencia del paquete Arduino, el intento se rechaza y requiere volver al centro.
