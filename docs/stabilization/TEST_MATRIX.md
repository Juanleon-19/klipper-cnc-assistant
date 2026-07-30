# Matriz de pruebas de estabilización

| Caso | Nivel | Resultado esperado | Estado |
| --- | --- | --- | --- |
| Fallo en primer punto | mock + físico | pausa, error, worker liberado | mocks parciales; físico pendiente |
| Telemetría obsoleta | unitario/integración | stale, movimiento bloqueado | lógica presente |
| Pérdida/recuperación WebSocket | fake WS | resuscripción sin hilos duplicados | no cubierto |
| Sonda activa antes de bajar | mock + físico | sin descenso, error seguro | lógica presente; físico pendiente |
| Sonda sin contacto | mock + físico | aborta en límite sin mapa falso | lógica presente; físico pendiente |
| Cancelación durante descenso | integración/físico | no siguiente punto; recuperación | no cubierto; entre puntos |
| Reintento explícito | unidad/API/UI | solo con operador | no existe |
| Omisión explícita | unidad/API/UI | `SKIPPED` auditado | no existe |
| Reinicio de una operación | unidad/API/UI | solo cambia esa operación | no existe |
| Conservación mapa/referencias | unidad/API | mismo mapa/placement/referencias | no cubierto |
| Cobertura incompleta | unidad/API/UI | bloqueo previo y generación | previa pendiente |
| Servicio reiniciado con malla parcial | integración | no revive worker; recuperación | no cubierto |
| Ejecución en aire | físico | trazable, sin corte | pendiente |
| Trabajo multioperación | mock + físico | subida/cambios/referencias | mocks parciales; físico pendiente |

Actualización: las pruebas simuladas de integración de malla existentes pasan; siguen pendientes los nuevos escenarios de cancelación/WS en hardware.
