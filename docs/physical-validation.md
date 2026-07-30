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
2. Verificar que sidebar, Sistema y workspace muestran `FÍSICO`.
3. Pulsar `Conectar diagnóstico`.
4. Comprobar Moonraker HTTP conectado.
5. Comprobar WebSocket conectado.
6. Comprobar Klipper `ready`.
7. Comprobar puerto Arduino abierto.
8. Observar joystick sin movimiento en modo diagnóstico.
9. Comprobar botón del joystick.
10. Comprobar botón externo.
11. Comprobar señal de sonda sin movimiento.

## Inicialización

1. Ingresar una Z segura de traslado, en milímetros.
2. Confirmar que la Z segura de traslado está dentro de límites reales de Klipper.
3. Confirmar inicialización desde la interfaz.
4. Verificar que se ejecuta homing.
5. Verificar que el sistema calcula:
   `center_x = (x_min + x_max) / 2`
   `center_y = (y_min + y_max) / 2`
6. Verificar movimiento primero a Z segura.
7. Verificar movimiento XY al centro.
8. Verificar que X/Y se mueve al centro manteniendo la Z segura de traslado.
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


## Procedimiento de validación de malla física medida

1. Iniciar `klipper-cnc-assistant.service` en `MACHINE_MODE=physical` con Moonraker y serie explícitos.
2. Abrir la web y seleccionar proyecto, montaje, operación y herramienta.
3. Abrir `Sistema`, pulsar `Conectar` y comprobar Moonraker HTTP, WebSocket, Klipper `ready` y Arduino con paquetes válidos recientes.
4. Observar joystick, botón externo y sonda en diagnóstico sin movimiento.
5. Ir al montaje activo, pestaña `Referencia`, ingresar Z segura de traslado y ejecutar homing/Z segura/centro desde el flujo físico.
6. Confirmar que `homed_axes` contiene `xyz`, que la posición se estabilizó y que X/Y está en el centro real.
7. Habilitar joystick X/Y y ubicar la herramienta en el 0,0 del G-code de FlatCAM.
8. Armar referencia dentro del montaje y pulsar el botón externo. Verificar descenso discreto, contacto, retracto y referencia `MEASURED`.
9. Abrir `Mapa de alturas`, seleccionar `MEDIDO FÍSICAMENTE`, usar área desde operaciones/generar malla y revisar cantidad, separación, límites locales, límites de máquina y recorrido serpentino.
10. Confirmar ejecución física. Ejecutar un punto por vez, observar `MOVING/PROBING/MEASURED`, pausar, reanudar y cancelar conservando puntos medidos.
11. Completar todos los puntos y revisar mapa 2D, superficie 3D, plano, residuos, rango, RMS y cobertura de operaciones.

No ejecutar este procedimiento sin operador presente, máquina despejada y parada física disponible. No reiniciar Klipper automáticamente.


## Prueba integral con PCB de descarte

No ejecutar este procedimiento sin operador presente, material de descarte, recorrido despejado, sonda verificada y parada física disponible. La implementación no ejecutó movimientos durante desarrollo.

1. Iniciar el servicio en modo físico con `MACHINE_MODE=physical`, Moonraker y Arduino explícitos.
2. Abrir la web.
3. Seleccionar proyecto.
4. Seleccionar montaje superior o la cara correspondiente.
5. Cargar operaciones FlatCAM si faltan.
6. Confirmar herramienta de cada operación.
7. Revisar trayectorias y límites analizados.
8. Abrir `Sistema` y conectar máquina.
9. Verificar Moonraker HTTP.
10. Verificar WebSocket.
11. Verificar Klipper `ready`.
12. Verificar Arduino: hilo activo, bytes, paquetes completos, paquetes válidos y edad reciente.
13. Observar joystick, botones y sonda sin movimiento.
14. Ejecutar homing desde la preparación física.
15. Confirmar `toolhead.homed_axes=xyz` y velocidad cero.
16. Introducir Z segura de traslado; no usarla como referencia Z ni profundidad de corte.
17. Mover Z a la altura segura.
18. Mover X/Y al centro calculado desde límites reales.
19. Habilitar joystick X/Y.
20. Posicionar la herramienta sobre el X0/Y0 real del G-code de FlatCAM.
21. Armar referencia.
22. Pulsar el botón externo.
23. Verificar descenso discreto, contacto, captura X/Y/Z y retracto.
24. Abrir `Mapa de alturas`.
25. Seleccionar `MEDIDO FÍSICAMENTE`.
26. Pulsar `Preparar mapa físico`.
27. Revisar región local, región de máquina, margen, filas, columnas, dx, dy, puntos y recorrido serpentino.
28. Confirmar límites y que no haya puntos inválidos.
29. Pulsar `Iniciar sondeo de malla` para ejecutar punto por punto.
30. Observar progreso `MOVING/PROBING/MEASURED`.
31. Probar `Pausar` después de un punto medido.
32. Probar `Reanudar`.
33. Completar todos los puntos.
34. Revisar mapa 2D, superficie 3D, plano, residuos, rango y RMS.
35. Validar cobertura de operaciones.
36. Abrir `Compensación`.
37. Generar G-code compensado.
38. Descargar el archivo de `generated/compensated/`.
39. Comparar original y compensado: X/Y deben conservarse; solo Z debe cambiar por `delta_superficie`.
40. Abrir `Ejecución` y revisar preflight.
41. Dejar la ejecución real final para confirmación supervisada separada.

Criterio de parada: ante ruido de sonda, pérdida de paquetes Arduino, telemetría obsoleta, homing incompleto, punto fuera de límites o cualquier movimiento inesperado, cancelar la secuencia. Usar `M112` solo como emergencia real.


## Verificación visual previa realizada

Se verificó la aplicación servida por FastAPI con fixture local en modo físico sin autoconexión. Capturas guardadas en `docs/artifacts/visual-verification/`:

- Sidebar en modo físico.
- Referencia física dentro del montaje.
- Malla configurada.
- Malla superpuesta en visor 2D.
- Progreso de sondeo con fixture de puntos medidos.
- Mapa medido/superficie 3D.
- Visor con ejes y ticks.
- Pantalla completa.
- Compensación.
- Ejecución/preflight.

Limitación de la verificación headless: Firefox sin pantalla no tuvo WebGL para Plotly, así que la superficie 3D debe comprobarse visualmente en navegador normal con WebGL durante la prueba con PCB de descarte.

## Malla física por material

La malla física se planifica desde las dimensiones reales del material, no desde las operaciones. El operador define filas y columnas manualmente. La aplicación genera todos los puntos e incluye los cuatro límites interiores.

Fórmula de región interior:

```text
probe_x_min = material_x_min + edge_margin_left
probe_x_max = material_x_max - edge_margin_right
probe_y_min = material_y_min + edge_margin_bottom
probe_y_max = material_y_max - edge_margin_top
```

Si `probe_x_min >= probe_x_max` o `probe_y_min >= probe_y_max`, el sondeo se bloquea con el mensaje de retiro inválido.

Ejemplo: material 60 x 60 mm, retiro 2 mm por lado, filas 7 y columnas 6 produce región X=2..58, Y=2..58, 42 puntos, `dx=11.200 mm` y `dy=9.333 mm`, ordenados en serpentina.

Las exclusiones rectangulares y circulares representan pinzas, tornillos y obstáculos. Los puntos dentro se marcan `EXCLUDED`, se muestran en el visor y no se ejecutan. El sondeo automático mide todos los puntos ejecutables después de una sola confirmación; pausa termina el punto seguro actual, cancelar conserva puntos medidos y emergencia sigue siendo M112.
