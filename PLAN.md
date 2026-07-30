# Plan de cierre del producto

Fecha de actualización: Thursday, July 30, 2026

El proyecto se organiza en cuatro fases independientes. Cada fase debe ejecutarse en su propia rama y solo puede integrarse en `main` después de revisión técnica, pruebas reproducibles y aprobación explícita del usuario.

## Fase 1 — Auditoría, arquitectura y organización

- Auditar el estado Git, el despliegue real y las copias candidatas.
- Documentar la procedencia funcional de cada componente útil.
- Verificar la arquitectura actual y definir la arquitectura objetivo.
- Reorganizar el repositorio sin cambiar comportamiento funcional de forma deliberada.
- Dejar la documentación base y la estructura estable del repositorio.

Estado: completada y fusionada en `main`

## Fase 2 — Referencias, Arduino y conectividad

- Cerrar la pestaña `Referencia` y preservar la persistencia existente.
- Consolidar Arduino, `SerialDriver`, reconexión y límites de seguridad.
- Separar Moonraker HTTP, transporte WebSocket, frescura de posición y observación activa.
- Añadir pruebas automatizadas reproducibles para reconexión, telemetría y referencias.

Estado: activa en `fase-2/referencias-conectividad`

## Fase 3 — Mapa de alturas y compensación

- Cerrar el flujo de mapa medido, persistencia y reanudación.
- Asegurar cobertura, validación de dominio y compensación reproducible.
- Separar claramente simulación, medición física y generación compensada.

Estado: pendiente

## Fase 4 — Ejecución, recuperación y cierre del producto

- Cerrar consola de ejecución, `JobRun`, recuperación y trazabilidad.
- Asegurar preflight, cambio de herramienta, reanudación y cancelación segura.
- Validar el producto completo contra la arquitectura aprobada y dejar criterio de cierre.

Estado: pendiente
