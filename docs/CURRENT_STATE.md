# Estado actual del producto

Fecha: 2026-08-16

## Fuente de verdad

- Código integrado: `main`.
- Línea base para la validación física final: `af0099dda64fd9394045766b8475b689cf69a320`.
- Rama de referencia: `baseline/physical-validation-2026-08-16`.
- Los cambios de documentación de cierre se preparan en `chore/final-closeout-2026-08-16`.
- Ningún hotfix de comportamiento debe mezclarse con la rama documental de cierre.

## Estado por fases

### Fase 1 — Auditoría, arquitectura y organización

Estado: completada e integrada.

Se consolidó la arquitectura del backend/frontend, la fuente de verdad Git, la configuración operativa externa al repositorio y la CI segura.

### Fase 2 — Referencias, Arduino y conectividad

Estado: implementada e integrada.

Incluye runtime físico, Moonraker HTTP/WebSocket, Arduino, reconexión segura, homing, captura de X/Y y referencia Z, además de recuperación de conexión. El flujo ha sido ejercitado físicamente durante la integración; forma parte de la regresión final.

### Fase 3 — Mapa de alturas y compensación

Estado: implementada e integrada.

Incluye preview, armado, sondeo físico, persistencia, recuperación, cobertura, compensación legacy/adaptive, auditoría y generación de artefactos compensados. El mapa físico ha completado ejecuciones durante la integración. La regresión final debe comprobar que mapa, referencias y artefactos siguen siendo coherentes con el trabajo actual.

### Fase 4 — Ejecución, recuperación y JobRun

Estado: implementada e integrada; validación física integral final pendiente.

Incluye preflight, JobRun multioperación, upload Moonraker, progreso, pausa/cancelación, cambio de herramienta, nueva referencia, regeneración de compensación y recuperación de ejecuciones obsoletas.

### Fase 5 — Estabilización y cierre

Estado: activa.

Objetivo: completar la validación física de extremo a extremo, corregir únicamente defectos reproducibles, actualizar la documentación y fijar una referencia final estable.

## Estado técnico inmediato

- Último cambio integrado: PR #17, reordenamiento rápido de operaciones con persistencia serializada en segundo plano.
- La CI del PR #17 terminó correctamente.
- No hay un hotfix funcional abierto para el estado actual de `main`.
- Existe recuperación explícita para JobRun obsoleto; debe validarse durante el cierre si vuelve a presentarse el caso.
- El spindle permanece bajo control manual en el flujo actual.

## Política para el cierre

1. Las pruebas físicas se realizan sobre la línea base indicada arriba.
2. Un fallo reproducible genera una rama nueva `hotfix/<problema>` desde el `main` vigente; no se corrige directamente en `main`.
3. Cada hotfix requiere pruebas automatizadas, PR, CI y aprobación explícita antes de merge/deploy.
4. La rama `baseline/physical-validation-2026-08-16` no se mueve durante esta campaña de validación.
5. La documentación de cierre se fusiona únicamente cuando la validación final termina.

## Criterio de producto cerrado

El producto se considera cerrado para esta versión cuando:

- el flujo de proyecto y operaciones funciona sin inconsistencias visibles;
- conexión, homing y referencias completan correctamente;
- mapa y compensación generan artefactos vigentes y ejecutables;
- un trabajo multioperación completa el flujo físico previsto, incluidos los cambios de herramienta que correspondan;
- la consola refleja estado, progreso y siguiente acción de forma coherente;
- las recuperaciones necesarias no requieren editar archivos ni reiniciar Klipper/Moonraker;
- la suite automatizada y el build final están en verde;
- no quedan PR funcionales pendientes de esta campaña de cierre.
