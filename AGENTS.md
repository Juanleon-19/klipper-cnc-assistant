# Guía para agentes de IA — Klipper CNC Assistant

## Objetivo general

Klipper CNC Assistant es una aplicación experimental para operar una máquina CNC adaptada a Klipper mediante Moonraker.

El objetivo funcional es:

- Recibir intención del operador desde un controlador Arduino.
- Mantener estado y límites de la máquina desde Klipper/Moonraker.
- Ejecutar movimientos manuales seguros.
- Capturar referencias de PCB y, en etapas posteriores, realizar sondeo, mapa de altura, compensación e interpretación de G-code.

La seguridad de máquina tiene prioridad sobre la velocidad de desarrollo y la comodidad de la interfaz.

## Arquitectura aprobada

```text
Arduino Pro Mini
  │
  │ USB Serial / protocolo binario
  ▼
input.SerialDriver
  ▼
input.CommandMapper
  ▼
Comando de alto nivel
  ▼
jog.ManualJogController / jog.JogController
  │                         ▲
  │                         │
  ▼                         │
moonraker.MoonrakerClient   machine.MachineState
  │                         ▲
  │                         │
  └──── HTTP ───────────────┘
            Moonraker

Moonraker WebSocket
  ▼
moonraker.MoonrakerTelemetry
  ▼
machine.MachineState
```

El firmware solo lee hardware y emite paquetes. No controla directamente la máquina.

El software de entrada expresa intención. La capa `jog` es la única autorizada para generar movimiento. Moonraker/Klipper conserva la autoridad final sobre la máquina.

## Filosofía del proyecto

- Seguridad antes que funcionalidad.
- Estado real antes que supuestos locales.
- Límites y capacidades descubiertos dinámicamente desde Klipper.
- Entradas intercambiables: Arduino, teclado o UI no deben acoplarse al control de movimiento.
- Experimentos aislados antes de migrar comportamiento a `src/`.
- Ningún resultado de un experimento equivale a una función de producto sin criterios explícitos de validación.
- El jog continuo no se considera seguro hasta que la trayectoria pendiente y la parada estén caracterizadas y acotadas.

## Flujo de datos Arduino → Klipper

1. El Arduino lee ejes analógicos, botón de joystick, botón externo y sonda.
2. El firmware transmite paquetes binarios de 8 bytes a 115200 baudios.
3. `SerialDriver` sincroniza cabecera, valida checksum y produce `ControllerPacket`.
4. `CommandMapper` convierte el paquete en `ControllerCommand`.
5. La capa de orquestación decide si la intención es válida y cuándo puede convertirse en movimiento.
6. `ManualJogController` selecciona el perfil de jog.
7. `JogController` valida eje, homing, límites, velocidad y genera G-code relativo.
8. `MoonrakerClient` envía el G-code a Moonraker.
9. `MoonrakerTelemetry` actualiza `MachineState` con telemetría WebSocket.

## Responsabilidades de módulos principales

- `input/serial_driver.py`: transporte serie y validación del protocolo Arduino.
- `input/command_mapper.py`: traducción de paquetes a intención de alto nivel.
- `input/jog_input.py`: puente entre intención y control de jog; no debe contener reglas de seguridad duplicadas.
- `machine/discovery.py`: descubrimiento de estado, límites y capacidades desde Klipper.
- `machine/state.py`: estado compartido y sincronizado de posición, velocidad, homing y límites.
- `moonraker/client.py`: operaciones HTTP discretas contra Moonraker.
- `moonraker/telemetry.py`: telemetría WebSocket y actualización de estado.
- `jog/profiles.py`: perfiles de distancia y velocidad.
- `jog/manual.py`: interpretación de jog manual por eje y perfil.
- `jog/controller.py`: frontera de seguridad y única capa de generación de G-code de movimiento.
- `experiments/`: investigación aislada; no es código de producto.
- `firmware/`: firmware y validaciones de hardware Arduino.

## Reglas para trabajar en el repositorio

- Leer el código y documentación relevante antes de modificar una capa.
- Preservar cambios locales ajenos.
- Usar `apply_patch` para ediciones.
- Mantener `src/` libre de código experimental no validado.
- Añadir pruebas automatizadas para lógica pura y pruebas manuales documentadas para interacción física.
- Configurar mediante variables de entorno o configuración explícita; no codificar IP, puertos ni límites de máquina.
- Tratar la desconexión de Arduino, Moonraker o WebSocket como condición de fallo seguro.
- Documentar toda validación física: máquina, eje, velocidad, distancia, condiciones y resultado.

## Lo que un agente nunca debe hacer

- No enviar G-code, mover ejes, sondear ni activar spindle sin autorización explícita del usuario.
- No asumir que la máquina está homed.
- No asumir dimensiones, límites, orientación de ejes o puerto serie.
- No implementar jog continuo mediante segmentos repetidos sin una garantía validada de trayectoria pendiente y parada acotada.
- No convertir un experimento en producción solo porque “funciona” en una ejecución.
- No duplicar lógica de seguridad fuera de `JogController`.
- No borrar, resetear o sobrescribir cambios locales del usuario.
- No modificar firmware y host simultáneamente sin documentar la compatibilidad de protocolo.
- No usar `experiments/` como punto de entrada de producto.

## Desarrollo de experimentos

Cada experimento debe:

1. Tener un objetivo técnico único y verificable.
2. Declarar alcance, riesgos y si genera movimiento físico.
3. Reutilizar módulos de `src/` cuando estén disponibles.
4. Aislar el código nuevo dentro de su directorio experimental.
5. Exigir confirmación del operador antes de cualquier movimiento.
6. Incluir límites de distancia, velocidad, timeout y condición de parada.
7. Registrar parámetros, resultados medidos y conclusión arquitectónica.
8. Declarar explícitamente `PASSED`, `FAILED`, `PARTIALLY VALIDATED` o `IN PROGRESS`.

Los experimentos de movimiento deben comenzar con modo observación o dry-run siempre que sea posible.

## Criterios para migrar un experimento a producto

Un experimento puede migrar a `src/` solo si:

- Resuelve una necesidad de producto concreta.
- Tiene interfaz y responsabilidad claras.
- No duplica una capacidad existente.
- Tiene pruebas automatizadas para su lógica no física.
- Tiene validación manual reproducible cuando involucra hardware.
- Define manejo de errores, timeout y desconexión.
- Respeta homing, límites, velocidad y parada segura.
- Su documentación incluye limitaciones conocidas.
- La migración ha sido aprobada explícitamente por el usuario.

## Convenciones de commits

Formato recomendado:

```text
tipo(área): resumen imperativo y breve
```

Tipos:

- `feat`: funcionalidad de producto.
- `fix`: corrección.
- `test`: pruebas o validación.
- `experiment`: investigación aislada.
- `docs`: documentación.
- `refactor`: cambio estructural sin cambio funcional.
- `firmware`: cambios de Arduino.
- `chore`: mantenimiento.

Ejemplos:

```text
feat(jog): require homed axis before relative movement
experiment(input): validate Arduino direction transitions
firmware(protocol): document controller packet flags
docs(readme): add manual jog safety requirements
```

Un commit debe representar un cambio coherente, verificable y reversible.

## Convenciones de README

Cada README debe incluir, según corresponda:

- Objetivo.
- Alcance y exclusiones.
- Arquitectura o flujo relevante.
- Requisitos.
- Configuración.
- Riesgos de seguridad.
- Procedimiento de ejecución.
- Criterios de aceptación.
- Resultado y estado.
- Limitaciones y siguiente paso.

Los README de experimentos deben diferenciar con claridad hechos medidos, hipótesis e interpretación.

