# Arduino Pro Mini Tests

Este directorio contiene las pruebas de validación del hardware utilizadas durante el desarrollo del controlador manual para Klipper CNC Assistant.

## Test 001 - Serial Alive

Objetivo:

- Verificar la comunicación serie entre el Arduino y el PC.

Resultado esperado:

- El Arduino transmite mensajes periódicos indicando que la comunicación funciona correctamente.

Estado:

- Finalizado.

---

## Test 002 - Joystick Axes

Objetivo:

- Verificar el funcionamiento de los ejes analógicos del joystick.

Entradas:

- A2
- A1

Resultado esperado:

- Lectura correcta de ambos ejes.

Estado:

- Finalizado.

---

## Test 003 - Digital Inputs

Objetivo:

- Verificar todas las entradas digitales.

Entradas:

- Botón del joystick
- Botón externo
- Sonda

Resultado esperado:

- Cambio correcto de estado en cada entrada.

Estado:

- Finalizado.

---

## Test 004 - Joystick Calibration

Objetivo:

- Determinar la orientación real del joystick.
- Obtener los valores de centro y extremos.

Resultado esperado:

- Confirmación de la asignación de ejes.
- Calibración de los límites de operación.

Estado:

- Finalizado.

---

## Test 005 - Serial Protocol

Objetivo:

- Validar el protocolo binario entre Arduino y Python.

La prueba verifica:

- Dirección.
- Estado del botón del joystick.
- Estado del botón externo.
- Estado de la sonda.
- Valores analógicos X e Y.
- Checksum.

Estado:

- Finalizado.

---

Todos los tests fueron desarrollados como herramientas de validación durante la fase inicial del proyecto y sirven como referencia para futuras modificaciones del hardware.
