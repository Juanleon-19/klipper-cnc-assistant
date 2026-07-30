# Plan de cierre del producto

Fecha de actualización: Thursday, July 30, 2026

## Estado general

El proyecto se organiza en cuatro fases independientes. Cada fase debe ejecutarse en su propia rama y solo puede integrarse en `main` después de revisión técnica, pruebas reproducibles y aprobación explícita del usuario.

## Fase 1 — Auditoría, arquitectura y organización

- Auditar el estado Git, el despliegue real y las copias candidatas.
- Documentar la procedencia funcional de cada componente útil.
- Verificar la arquitectura actual y definir la arquitectura objetivo.
- Reorganizar el repositorio sin cambiar comportamiento funcional de forma deliberada.
- Actualizar documentación principal y dejar una base estable para las fases siguientes.

Estado: activa en `fase-1/auditoria-arquitectura`

## Fase 2 — Referencias, Arduino y conectividad

- Cerrar la pestaña Referencias y su persistencia.
- Consolidar Arduino, `SerialDriver`, reconexión y límites de seguridad.
- Consolidar Moonraker HTTP y WebSocket sin duplicidades.
- Asegurar que la conectividad física y simulada tengan fronteras claras.

Estado: provisional hasta cerrar la Fase 1

## Fase 3 — Mapa de alturas y compensación

- Cerrar el flujo de mapa medido, persistencia y reanudación.
- Asegurar cobertura, validación de dominio y compensación reproducible.
- Separar claramente simulación, medición física y generación compensada.

Estado: provisional hasta cerrar la Fase 1

## Fase 4 — Ejecución, recuperación y cierre del producto

- Cerrar consola de ejecución, `JobRun`, recuperación y trazabilidad.
- Asegurar preflight, cambio de herramienta, reanudación y cancelación segura.
- Validar el producto completo contra la arquitectura aprobada y dejar criterio de cierre.

Estado: provisional hasta cerrar la Fase 1
