# Mejoras diferidas — primer paquete

Base: `0ac6fd0c6b067c746fcb5ac819f154a546b43f44`. Sin hardware ni despliegue.

## Contratos de software

- `safety.telemetry_recent` físico deriva de `MachineState.live_position_updated_at`,
  nunca del tráfico agregado. `motion_report.live_position` (WS o HTTP) renueva
  la muestra incluso con XYZ idéntica. Toolhead, gcode_move o velocidad solos no.
- Moonraker expone `websocket_connected_recently`, `position_sample_recently`,
  `position_changed_recently` y edad del último cambio. El tercer indicador es
  diagnóstico: no participa en readiness ni autorización. La primera muestra
  establece su línea base; cambio de sesión invalida sus timestamps.
- No cambian los timeouts ni MotionAuthorizer. Ejecución solicita HTTP read-only
  cuando la posición envejece, aunque sigan llegando actualizaciones parciales.
- Recovery de transición solo ofrece retry explícito si la operación anterior
  está completada, la siguiente requiere otra herramienta y sigue pendiente,
  existe evidencia específica de fallo, sesión compatible, runtime admisible,
  Moonraker inactivo y ninguna identidad/journal ambiguos. Un journal `sent`
  solo se acepta para la operación ya completada. Retry vuelve a exigir spindle
  detenido; no ejecuta automáticamente la transición. Los guards finales siguen.
- Un contrato JSON compartido por tests backend/frontend comprueba las acciones
  reales de live_execution: recovery → spindle detenido → herramienta cambiada.
- `initialView` se aplica por identidad de proyecto/montaje/cara/operación;
  refrescar el objeto no revierte la navegación elegida por el operador.
- Referencias muestra accesos a los handlers existentes de reset. No hay nuevas
  rutas ni semántica backend de reset. Los diálogos describen archivo de JobRun,
  invalidación de mapa/referencias, desconexión del runtime y conservación del
  historial. Cancelar el diálogo no emite petición.

## Backlog que no se reabre

Ya implementados: Ir al punto de referencia, perfiles standard/long_tool,
Continuar con Legacy JIT, conservación histórica al reiniciar preparación,
validación y worker de malla, referencias por instalación y single-map,
MotionContext fresco antes de dispatch, clasificación not_sent/uncertain/sent.

## Fuera de este paquete

Reset individual por operación; presupuesto/índice Legacy; API antigua;
limpieza general; refactor runtime/routes/project.json; dependencias;
planner/arcos; nuevo ETA de preparación. El ETA actual depende del artefacto
remoto y no es una estimación de duración de generación/upload.

## Validación

Resultados exactos en [IMPLEMENTATION_REPORT.md](../IMPLEMENTATION_REPORT.md).
La validación software usa simulated, fakes y directorios temporales.
PHYSICAL_VALIDATION_PENDING = YES. No se afirma validación física.
