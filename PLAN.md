# Plan de cierre del producto

Fecha de actualización: 2026-08-16

El proyecto se organiza en cinco fases. Cada cambio funcional se desarrolla en una rama independiente y solo puede integrarse en `main` después de revisión técnica, pruebas reproducibles y aprobación explícita del usuario.

## Fase 1 — Auditoría, arquitectura y organización

- Auditar estado Git, despliegue real y copias candidatas.
- Documentar procedencia funcional y arquitectura.
- Reorganizar backend/frontend sin cambiar deliberadamente comportamiento.
- Establecer CI y política de configuración externa.

Estado: **completada e integrada**.

## Fase 2 — Referencias, Arduino y conectividad

- Runtime físico con Moonraker HTTP/WebSocket y Arduino.
- Reconexión segura y telemetría diferenciada.
- Homing, origen X/Y, referencia Z y flujo de Referencia.
- Guards para acciones físicas y recuperación de conexión.

Estado: **implementada e integrada**. Incluida en la regresión física final.

## Fase 3 — Mapa de alturas y compensación

- Preview y mapa persistido equivalentes de forma canónica.
- Sondeo físico, persistencia incremental, pausa/recuperación y coverage.
- Compensación legacy/adaptive, auditoría y artefactos ejecutables.

Estado: **implementada e integrada**. El mapa físico ha sido ejercitado; incluida en la regresión final.

## Fase 4 — Ejecución, recuperación y JobRun

- Plan multioperación y preflight.
- Upload/seguimiento Moonraker y progreso en vivo.
- Cambio de herramienta, nueva referencia y regeneración de compensación.
- Pausa, cancelación, recuperación y cierre de ejecuciones obsoletas.

Estado: **implementada e integrada; validación física integral final pendiente**.

## Fase 5 — Estabilización y cierre de producción

- Ejecutar la campaña física final sobre una línea base fija.
- Corregir únicamente defectos reproducibles mediante hotfixes aislados.
- Confirmar suites automatizadas y build final.
- Actualizar documentación operativa y fijar SHA estable de cierre.

Estado: **activa**.

## Línea base de validación

- SHA: `af0099dda64fd9394045766b8475b689cf69a320`.
- Rama de referencia: `baseline/physical-validation-2026-08-16`.
- Checklist: `docs/FINAL_VALIDATION.md`.
- Estado operativo: `docs/CURRENT_STATE.md`.
