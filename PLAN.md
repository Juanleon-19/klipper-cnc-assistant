# Plan de cierre del producto

Fecha de actualización: 2026-08-09

El proyecto se organiza por capacidades y cada cambio debe ejecutarse en su propia rama. Nada se integra en `main` sin revisión técnica, pruebas reproducibles y aprobación explícita del usuario.

El estado operativo vivo se mantiene en [`docs/CURRENT_STATE.md`](docs/CURRENT_STATE.md). Este archivo describe el roadmap y el criterio de cierre, no el SHA desplegado.

## Fase 1 — Auditoría, arquitectura y organización

- Auditar el estado Git, el despliegue real y las copias candidatas.
- Documentar la procedencia funcional de cada componente útil.
- Verificar la arquitectura actual y definir la arquitectura objetivo.
- Reorganizar el repositorio sin cambiar comportamiento funcional de forma deliberada.
- Dejar reglas base para agentes y ramas.

Estado: completada e integrada en `main`.

## Fase 2 — Referencias, Arduino y conectividad

- Cerrar la pestaña `Referencia` y preservar la persistencia existente.
- Consolidar Arduino, `SerialDriver`, reconexión y límites de seguridad.
- Separar Moonraker HTTP, transporte WebSocket, frescura de posición y observación activa.
- Añadir pruebas automatizadas reproducibles para reconexión, telemetría y referencias.

Estado: implementación integrada en `main`; validación física completa todavía pendiente.

## Fase 3 — Mapa de alturas y compensación

- Cerrar el flujo de mapa medido, persistencia y reanudación.
- Asegurar cobertura, validación de dominio y compensación reproducible.
- Separar claramente simulación, medición física y generación compensada.
- Recuperar de forma segura puntos fallidos sin reiniciar servicios ni lanzar retries automáticos físicos.

Estado: implementación integrada en `main`; validación física del flujo completo en progreso.

## Fase 4 — Ejecución, recuperación y cierre funcional

- Cerrar consola de ejecución, `JobRun`, recuperación y trazabilidad.
- Asegurar preflight, cambio de herramienta, reanudación y cancelación segura.
- Evitar recalculos costosos en la preparación del plan.
- Exponer bloqueos concretos antes de una ejecución física.

Estado: implementación integrada en `main`; ejecución física final pendiente de validación controlada.

## Fase 5 — Estabilización y cierre de producción

Prioridad actual.

- Mantener una sola fuente de verdad entre GitHub, host y agentes.
- Validar `Reconectar runtime` sin movimiento.
- Validar referencia -> mapa -> fallo/reintento -> mapa completo.
- Auditar persistencia y recuperación ante reinicio del servicio.
- Reducir polling y consultas redundantes de UI/backend.
- Identificar solicitudes de lectura que puedan recibir timeout seguro sin abortar acciones físicas en curso.
- Revisar la frontera `machine/` + `moonraker/` + `input/` + `execution/` y separar responsabilidades solo cuando exista evidencia de beneficio y cobertura de pruebas.
- Archivar o retirar de uso operativo worktrees/copias históricas únicamente después de inventario y autorización; no borrar evidencia durante estabilización.

Estado: activa.

## Criterio de cierre del producto

El proyecto puede considerarse estable para uso controlado cuando se cumpla todo lo siguiente:

1. `main`, el SHA desplegado y `docs/CURRENT_STATE.md` están alineados.
2. Backend, frontend, lint y build pasan en CI.
3. Reconexión de Arduino y runtime funcionan en la CNC real sin movimiento inesperado.
4. Un mapa físico puede completarse y recuperarse de un fallo de punto sin reiniciar la aplicación.
5. Un reinicio autorizado de `klipper-cnc-assistant.service` no deja estados operativos ambiguos o irrecuperables.
6. Preflight bloquea condiciones inseguras con mensajes concretos.
7. Un trabajo de prueba autorizado completa el flujo de ejecución y recuperación esperado.
8. No quedan múltiples copias activas del código cuyo rol no esté documentado.
