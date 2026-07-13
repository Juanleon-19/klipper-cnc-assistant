# Estado del proyecto — Klipper CNC Assistant

**Última actualización:** 2026-07-13  
**Estado global:** Pre-MVP / control manual discreto integrado en experimentos

Este documento es vivo: debe actualizarse al cerrar un experimento, migrar una capacidad a `src/`, cambiar una decisión de arquitectura o descubrir un riesgo relevante.

## Objetivo final

Construir un asistente CNC para máquinas adaptadas a Klipper que permita:

- Control manual seguro desde un controlador Arduino.
- Descubrimiento dinámico de límites, homing y capacidades de la máquina mediante Moonraker.
- Captura de referencias de PCB y definición de coordenadas de trabajo.
- Sondeo eléctrico independiente y generación de un mapa de altura.
- Compensación de superficie e interpretación de G-code para mecanizado de PCB.

La prioridad es la seguridad de movimiento. Una característica no se considera terminada si puede producir movimiento no acotado o no supervisable.

## Estado actual

La base de comunicación host–Moonraker y Arduino–host está validada de forma independiente.

El proyecto puede:

- Consultar Moonraker por HTTP.
- Descubrir posición, límites, homing, velocidad y aceleración.
- Recibir posición y velocidad mediante WebSocket.
- Emitir jog relativo discreto mediante G-code.
- Leer paquetes binarios válidos desde el Arduino Pro Mini por `/dev/ttyUSB0` a 115200 baudios.

El proyecto todavía no dispone de una aplicación de producto integrada en `src/` que conecte de forma segura Arduino, telemetría, jog y sondeo. Sin embargo, los Experimentos 007 y 008 ya validan esa integración en el espacio experimental bajo supervisión física.

## Componentes terminados o validados

| Componente | Estado | Evidencia / alcance |
| --- | --- | --- |
| Entorno Arduino Pro Mini | Validado | Compilación, carga y comunicación serie documentadas. |
| Firmware de controlador | Validado | Lee ejes, botones y sonda; emite paquetes binarios de 8 bytes cada 20 ms. |
| Protocolo serie | Validado | Cabecera `0xAA`, dirección, flags, valores X/Y y checksum XOR. |
| `SerialDriver` | Implementado y verificado | Decodifica paquetes del Arduino conectado en `/dev/ttyUSB0`. |
| `CommandMapper` | Implementado | Convierte direcciones en intención X/Y y flags de entrada. |
| Cliente Moonraker HTTP | Implementado | Estado del servidor, consulta de objetos y envío de G-code. |
| Descubrimiento de máquina | Implementado | Posición, límites, homing, velocidad y aceleración desde `toolhead`. |
| Telemetría Moonraker | Implementada | Suscripción a `motion_report.live_position` y `live_velocity`. |
| Jog discreto relativo | Validado manualmente | Movimiento por eje, perfiles y limitación por límites configurados. |
| Perfiles manuales | Validados manualmente | COARSE, NORMAL y FINE sobre X, Y y Z. |
| Experimento 007: controlador manual Arduino | Validado manualmente | Jog discreto cardinal por transición `CENTER -> dirección`, con telemetría y confirmación del operador. |
| Experimento 008: secuencia de sonda discreta | Parcialmente validado | Una corrida física completó detección de contacto, captura de punto y retract seguro. |

“Validado” no significa “listo para producto”; indica que el comportamiento fue comprobado bajo un alcance limitado.

## Componentes en desarrollo

| Componente | Estado | Próxima acción |
| --- | --- | --- |
| Política de seguridad de jog | Parcialmente implementada | Consolidar cobertura de pruebas y definir comportamiento ante estado obsoleto o desconexión. |
| Experimento 008: rutina de sonda | Validación parcial | Medir repetibilidad, rebote de señal y abortos seguros en más escenarios. |
| Manejo robusto de serie | Incompleto | Definir reconexión, timeout, recuperación ante checksum inválido y watchdog. |
| Integración de telemetría con control | Incompleta | Mantener `MachineState` actualizado durante movimientos manuales. |
| Horizonte de movimiento con TrapQ | Investigación en curso | Caracterizar y validar control de cola antes de cualquier migración a producto. |

## Componentes pendientes

- Punto de entrada de aplicación que integre Arduino, Moonraker, telemetría y control de jog.
- Pruebas automatizadas unitarias y mocks de Moonraker/serial.
- Configuración de proyecto y dependencias reproducibles (`pyproject.toml`, lockfile o equivalente).
- Gestión de errores y reconexión HTTP/WebSocket.
- Migración aprobada de la rutina de sonda y el botón externo desde `experiments/` a `src/`, si supera validación adicional.
- Calibración persistente del joystick y zona muerta configurable.
- Referencias de PCB, transformación de coordenadas y corrección de rotación.
- Mapa de altura, interpolación y visualización de superficie.
- Transformación y envío de G-code compensado.
- Empaquetado, CI y documentación operativa de despliegue.

## Próximo objetivo

**Experimento 008 — repetibilidad y robustez de la secuencia de sonda.**

Alcance aprobado:

1. Repetir la captura del mismo punto para medir dispersión en Z.
2. Confirmar que la secuencia se inicia una vez por flanco ascendente del botón externo.
3. Caracterizar los eventos repetidos de `probe_triggered` durante contacto y retract.
4. Ejecutar escenarios de aborto por límite Z, timeout de telemetría y señal de sonda activa antes de bajar.
5. Decidir si hay cambios de seguridad o filtrado que deban mantenerse en el experimento antes de proponer migración a `src/`.

Quedan fuera de este objetivo: jog continuo, compensación PCB, persistencia de offsets y migración automática a producto.

## Riesgos conocidos

| Riesgo | Impacto | Estado / mitigación |
| --- | --- | --- |
| Cola de movimiento Klipper al enviar segmentos repetidos | Alto | El jog continuo no está autorizado; Experiment 006 sigue en investigación. |
| Rebote o duplicación de eventos de sonda | Alto para captura fiable | Experiment 008 mostró flancos repetidos; falta caracterizar si son rebote eléctrico o solo logging redundante. |
| Estado de posición obsoleto en jog manual o sondeo | Alto | Telemetría activa y `discover_machine(...)` al iniciar la sonda reducen el riesgo, pero falta endurecer la política global. |
| Un movimiento por paquete serie de 20 ms | Alto | Experiment 007 actúa por transición, no por repetición de paquetes. |
| Telemetría más lenta que el bucle de control | Alto para jog continuo | No usar posición WebSocket como reloj de control de horizonte. |
| Desconexión de serie/WebSocket sin reconexión | Medio/alto | Implementar fallo seguro y recuperación controlada. |
| Puertos serie divergentes en documentación | Medio | Documentación menciona `/dev/ttyUSB1`; implementación actual usa `/dev/ttyUSB0`. |
| Límites de joystick codificados en firmware | Medio | Calibración y zona muerta configurable pendientes. |
| Ausencia de pruebas automatizadas y CI | Medio | Añadir antes de migrar capacidades experimentales. |

## Decisiones técnicas importantes

- Moonraker HTTP se usa para operaciones discretas y envío de G-code.
- Moonraker WebSocket se usa para telemetría persistente.
- Los límites, posición y capacidades se descubren desde Klipper; no se codifican dimensiones de máquina.
- Arduino es un dispositivo de interfaz humana: no ejecuta control de máquina.
- La capa `jog` es la única autorizada para transformar intención en G-code de movimiento.
- El G-code de jog conserva y restaura el estado mediante `SAVE_GCODE_STATE` y `RESTORE_GCODE_STATE`.
- El jog continuo no se migrará a `src/` sin una garantía experimental de horizonte pendiente y parada acotada.
- Los experimentos permanecen aislados de `src/` hasta cumplir criterios explícitos de migración.

## Historial de decisiones de arquitectura

| Fecha / etapa | Decisión | Motivo | Estado |
| --- | --- | --- | --- |
| Experimento 001 | Usar Moonraker HTTP para conectividad y consultas | API disponible para operaciones discretas | Adoptada |
| Experimento 002 | Usar WebSocket para telemetría viva | `motion_report` entrega posición y velocidad durante movimiento | Adoptada |
| Experimento 003 | Separar emisión de G-code y observación de movimiento | La telemetría y el control son canales distintos | Adoptada |
| Experimento 004 | Rechazar jog continuo ingenuo por segmentos | Se observó desplazamiento posterior a la liberación del control | Adoptada |
| Experimento 005 | Separar dispositivo de entrada de intención de velocidad | El teclado SSH no representa bien estado simultáneo de teclas | Adoptada |
| Experimento 006, iteraciones 1–4 | No basar horizonte continuo solo en tiempo, posición o estimación híbrida | No lograron continuidad y parada acotada a la vez | Adoptada |
| Experimento 006, dirección actual | Investigar la cola interna TrapQ de Klipper | Es la fuente apropiada para medir trayectoria pendiente | En investigación |
| Firmware Arduino | Usar protocolo binario de 8 bytes con checksum XOR | Comunicación compacta y validable entre controlador y host | Adoptada |
| Preparación de Experimento 007 | Implementar primero jog discreto por transición | Evita acumulación de órdenes a 50 Hz y reutiliza arquitectura existente | Completada en experimento |
| Experimento 008 | Reusar `JogController` para cada paso de sonda y retract | Mantiene una sola frontera de seguridad para movimiento manual y sondeo discreto | Adoptada en experimento |

## Criterio de actualización

Actualizar este documento cuando ocurra cualquiera de estos eventos:

- Un experimento cambia de estado o produce una nueva conclusión.
- Una capacidad migra a `src/`.
- Se descubre, corrige o acepta un riesgo de seguridad.
- Cambia el alcance del MVP.
- Se aprueba una nueva decisión de arquitectura.

