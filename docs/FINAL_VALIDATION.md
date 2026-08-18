# Validación final del producto

Fecha: 2026-08-16

Esta validación se ejecuta sobre la línea base `af0099dda64fd9394045766b8475b689cf69a320` (`baseline/physical-validation-2026-08-16`).

No se modifica código durante una prueba física. Si aparece un fallo reproducible, se detiene el flujo afectado, se documenta el estado observado y el arreglo se prepara en una rama `hotfix/...` independiente.

## A. Interfaz y proyecto — sin movimiento

- Abrir el proyecto y confirmar que montajes y operaciones cargan correctamente.
- Reordenar una operación varias posiciones consecutivas con las flechas y comprobar respuesta inmediata.
- Recargar la página y confirmar que el orden persiste.
- Confirmar que los archivos G-code asignados, análisis y herramienta de cada operación siguen asociados a la operación correcta.
- Generar o refrescar plan/compensación únicamente cuando corresponda y verificar mensajes/bloqueos explícitos.

Criterio: ninguna acción queda bloqueada visualmente sin explicación; el orden persiste después de recargar.

## B. Runtime, homing y referencias

- Conectar/reconectar el runtime desde la UI si es necesario.
- Confirmar Moonraker HTTP, WebSocket, Klipper y Arduino según el flujo de Referencia.
- Ejecutar homing mediante el flujo autorizado para la prueba.
- Confirmar origen X/Y y referencia Z.
- Usar la acción de ir al punto de referencia y comprobar que una referencia ya capturada conserva su estado válido.

Criterio: conexión estable, homing completo y referencias coherentes sin reiniciar Klipper/Moonraker.

## C. Mapa físico

- Revisar preview y configuración de malla.
- Armar el sondeo y comprobar que preview/mapa persistido no producen falsos bloqueos.
- Ejecutar el sondeo físico controlado cuando sea necesario para la campaña actual.
- Confirmar progreso, finalización y mapa activo válido.

Criterio: el mapa termina sin bloqueo entre puntos, conserva las referencias y queda disponible para compensación.

## D. Compensación y preflight

- Generar compensación del proyecto.
- Confirmar que cada operación activa tiene artefacto compensado vigente o un bloqueo explícito.
- Revisar auditoría/ETA cuando esté disponible.
- Preparar el trabajo y comprobar todos los checks de preflight.

Criterio: `JOB_READY` solo aparece cuando todos los checks requeridos están aprobados.

## E. Ejecución física multioperación

- Iniciar el trabajo una sola vez.
- Confirmar que Moonraker y JobRun siguen el mismo archivo/estado.
- Verificar progreso de operación y progreso total.
- Mantener el spindle bajo control manual según el flujo actual.
- Cuando corresponda cambio de herramienta: detener spindle manualmente, confirmar transición, cambiar herramienta, medir nueva referencia y continuar.
- Confirmar regeneración/validación de las operaciones posteriores cuando dependen de la nueva referencia.
- Completar todas las operaciones previstas.

Criterio: el trabajo llega a `JOB_COMPLETE` sin duplicar uploads/inicios, sin perder sincronización y sin necesitar editar archivos manualmente.

## F. Recuperación

No se fuerza deliberadamente un fallo peligroso. Si durante la campaña aparece un estado recuperable real:

- una ejecución obsoleta debe ofrecer `Cerrar ejecución obsoleta` solo cuando Moonraker está inactivo y no existe ownership activo;
- una desincronización debe ser visible y no debe iniciar un segundo trabajo a ciegas;
- una pausa/cancelación utilizada durante una prueba controlada debe reflejarse correctamente en UI y backend;
- un fallo de sondeo no debe iniciar automáticamente otro movimiento mientras exista cleanup/ownership pendiente.

Criterio: las recuperaciones son fail-closed y no requieren reiniciar Klipper/Moonraker para limpiar un estado de aplicación recuperable.

## G. Cierre de software

Después de terminar las pruebas físicas:

- registrar cualquier defecto reproducible;
- resolver cada defecto en un PR independiente;
- ejecutar CI completa de la cabeza final;
- confirmar que no quedan PR funcionales abiertos;
- actualizar `docs/CURRENT_STATE.md` con el resultado real de la campaña;
- fusionar la documentación de cierre;
- fijar el SHA final estable de la versión validada.
