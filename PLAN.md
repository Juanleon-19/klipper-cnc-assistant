# Plan de cierre del producto

Fecha de actualización: 12 de septiembre de 2026

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

Estado: cierre de software integrado en la rama final; validación física pendiente

## Fase 3 — Mapa de alturas y compensación

- Cerrar el flujo de mapa medido, persistencia y reanudación.
- Asegurar cobertura, validación de dominio y compensación reproducible.
- Separar claramente simulación, medición física y generación compensada.

Estado: implementación de software integrada en la rama final; validación física pendiente

## Fase 4 — Ejecución, recuperación y cierre del producto

- Cerrar consola de ejecución, `JobRun`, recuperación y trazabilidad.
- Asegurar preflight, cambio de herramienta, reanudación y cancelación segura.
- Validar el producto completo contra la arquitectura aprobada y dejar criterio de cierre.

Estado: implementación de software integrada en la rama final; validación física pendiente

## Cierre final de software

Base: `3ca3ff404a7896506305a8a5c14f9e40cfcc411a`.
Rama: `release/final-software-stabilization-2026-09-12`.

Los P0, PR #24, P1-07 y P1-04 se conservan. El cierre residual cubre la
aplicación coherente de settings mediante el coordinator, valores finitos,
reconciliación acotada de standby, evidencia de probe histórico, GETs de lectura,
confirmaciones humanas de spindle vinculadas al contexto y documentación HTTP.

La siguiente etapa es directamente validación física con el usuario. Esta rama
solo se valida con modo simulado, datos temporales y fakes; no modifica servicios
reales ni se publica o fusiona a main durante el cierre.
