# Estado actual del proyecto

Fecha de actualización: 2026-08-09

## Fuente de verdad

- Repositorio oficial: `Juanleon-19/klipper-cnc-assistant`.
- Rama de producción: `main`.
- Directorio servido en el host: `/home/impresora/klipper-cnc-assistant`.
- Commit de producción verificado después del PR #10: `27298e160e550db4c7d9bf4dac6f655779fcd13a`.
- No asumir que ramas `fase-*`, `fix/*`, worktrees, copias `viernes` o backups son más nuevas que `main`; comprobar siempre contra `origin/main`.

## Estado funcional

Implementado y cubierto por pruebas automatizadas:

- gestión de proyectos, montajes y operaciones;
- análisis y visor de G-code;
- referencias físicas y persistencia;
- conexión Moonraker HTTP/WebSocket y Arduino;
- reconexión separada de Arduino y reconexión segura del runtime;
- mapa físico, persistencia, pausa, reanudación y recuperación de punto fallido;
- compensación de altura y auditoría previa;
- planificación y preflight de ejecución;
- flujo `JobRun` y recuperación de estados cubiertos por pruebas.

Pendiente de validación física completa:

- estabilidad real de referencia -> mapa -> recuperación -> mapa completo;
- comportamiento de reconexión del runtime en la CNC real;
- ejecución física completa de un trabajo autorizado después del preflight;
- persistencia y recuperación tras reinicio del servicio en escenarios reales.

## Problema activo

La prioridad actual es estabilizar la frontera física. La UI y el backend pasan sus suites de pruebas, pero los fallos observados en uso real se concentran en coordinación entre runtime, telemetría, Arduino, mapa y recuperación.

El PR #10 redujo polling redundante de la pantalla Sistema y añadió `Reconectar runtime` con política fail-closed. El siguiente paso es validar esa función en el host real sin movimiento y después ejecutar una prueba física mínima del flujo de mapa con autorización explícita.

## Flujo de trabajo obligatorio

1. Antes de trabajar, leer `AGENTS.md` y este archivo.
2. Confirmar `git rev-parse HEAD`, `git rev-parse origin/main` y el `WorkingDirectory` del servicio antes de diagnosticar el host.
3. Crear una rama por cambio; nunca programar directamente en `main`.
4. Codex se usa principalmente para inspección local del Vostro, journal, systemd, filesystem, reproducción y cambios en una rama de trabajo.
5. ChatGPT coordina arquitectura, revisa diffs/PR/CI y decide el alcance técnico antes de pedir merge.
6. El usuario es la autoridad para merge, deploy, reinicios y cualquier acción física.
7. Ningún agente debe interpretar una copia local o una rama antigua como fuente de verdad sin compararla con GitHub `main`.

## Seguridad permanente

- No ejecutar G-code, homing, jog, probe, spindle ni movimientos físicos sin autorización explícita.
- No reiniciar Klipper ni Moonraker sin autorización específica.
- No usar `Reconectar runtime` para saltarse una operación activa; debe fallar cerrado si existe movimiento, lock o cleanup pendiente.
- No borrar ni reemplazar datos de producción durante reorganizaciones.

## Siguiente secuencia de validación

1. Abrir la aplicación y comprobar que la pantalla Sistema responde con normalidad.
2. Con la máquina inmóvil y sin operación activa, pulsar una vez `Reconectar runtime` y comprobar retorno a `DIAGNOSTIC`.
3. Verificar que `Reconectar Arduino` sigue siendo una acción separada.
4. Solo con autorización física nueva: validar referencia y mapa pequeño/controlado.
5. Si aparece un fallo de punto: confirmar pausa, error visible, ausencia de retry automático y recuperación manual sin reiniciar el servicio.

## Regla de actualización

Este archivo debe actualizarse en el mismo PR que cambie de forma material el estado de producción, el flujo activo o la arquitectura operativa. Si el código y este documento discrepan, detener el trabajo y reconciliar primero la diferencia.
