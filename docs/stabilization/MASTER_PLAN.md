# Plan maestro de estabilización

Una fase por vez, un commit por fase, revisión del diff y parada al finalizar.

## Fase 1 — Reinicio independiente por operación

- Alcance: estado/API/UI para reiniciar una operación sin invalidar proyecto, montaje, mapa ni referencias compatibles.
- Archivos: `domain/models.py`, `application/services.py`, `application/job_service.py`, `api/routes.py`, `frontend/src/components/ProjectWorkspace.tsx`, `frontend/src/lib/api.ts`, pruebas.
- Riesgos: compensado incorrecto, job activo, referencia compartida.
- Pruebas/aceptación: transición, API, UI, conservación de mapa/referencias; solo cambia la operación; bloqueo ante job/movimiento activo.
- No modificar: firmware, Klipper, systemd, Moonraker, malla ni compensación.

## Fase 2 — Telemetría y reconexión

- Alcance: separar conexión/frescura, detectar hilo muerto, reconectar y resuscribir de forma acotada.
- Archivos: `moonraker/telemetry.py`, `machine/runtime.py`, `machine/state.py`, pruebas runtime.
- Riesgos: bucles o autorización con datos viejos.
- Pruebas/aceptación: fake WS, pérdida, resuscripción, stale; snapshot claro, sin hilos duplicados ni movimiento obsoleto.
- No modificar: Arduino ni G-code de movimiento.

## Fase 3 — Malla física robusta

- Alcance: lifecycle worker, interrupción cooperativa, recuperación persistida y decisión explícita por fallo.
- Archivos: `mesh_execution_service.py`, `physical_map_service.py`, `machine/runtime.py`, rutas/UI, pruebas.
- Riesgos: doble worker, G-code ya enviado, persistencia/reintentos peligrosos.
- Pruebas/aceptación: primer punto, sonda activa/sin contacto, timeout, pausa/cancelación, reinicio parcial; liberar worker y reintentar/omitir/cancelar auditables.
- No modificar: firmware, compensación, jobs.

## Fase 4 — Cobertura y compensación

- Alcance: cobertura previa por operación antes de sondear.
- Archivos: `physical_map_service.py`, `heightmap/coverage.py`, `compensated_gcode_service.py`, visor/UI, pruebas.
- Riesgos: exclusiones/extrapolación.
- Pruebas/aceptación: borde, exclusiones, arcos, parcial; preview informa cobertura y generación bloquea fuera.
- No modificar: runtime, firmware, referencias.

## Fase 5 — Ejecución real unificada

- Alcance: una puerta/política para operación individual y job.
- Archivos: `api/routes.py`, `application/job_service.py`, `moonraker/client.py`, frontend, pruebas.
- Riesgos: inicio accidental/rutas distintas.
- Pruebas/aceptación: preflight, aire, pausa/cancelación/reanudación/cambio herramienta; checks, confirmación y trazabilidad homogéneos.
- No modificar: altura/protocolo Arduino sin aprobación.

## Fase 6 — Eliminación de código no utilizado

- Alcance: decidir inventario y retirar solo aprobados.
- Archivos: candidatos, imports, pruebas/docs.
- Riesgos: migraciones o datos consumidos.
- Pruebas/aceptación: referencias, backend, lint/test/build, datos muestra; evidencia por eliminación.
- No modificar: datos de usuario ni lógica ajena.

## Fase 7 — Validación física progresiva

- Alcance: protocolo aprobado con PCB de descarte y evidencia.
- Archivos: documentación; código solo en corrección aprobada.
- Riesgos: herramienta/PCB/sonda/ejecución.
- Pruebas/aceptación: referencia, punto, malla, compensación, aire, operación, multioperación; parámetros y decisión por escalón.

Estado Fase 1: ampliada por solicitud para recuperación de referencia y malla; no modifica ejecución multioperación.
