# Reset de mapa pendiente — diagnóstico focalizado

Base: 512e86348e4be77f5b7798b1df0423dab5dcbd7b.

## Evidencia

- Journal persistido: action=reset_setup_map, status=PENDING. No contiene excepción,
  fases, preimagen ni hashes. No es evidencia de que se haya modificado el mapa.
- Proyecto y mapa activo comparados byte por byte con respaldo privado de despliegue:
  ambos idénticos. Mapa MESH_COMPLETE, archived_at=null; proyecto mantiene asociación
  al mapa y referencia. No se completaron las dos escrituras del reset_map.
- La ejecución de reset_map sobre una copia temporal del proyecto pasó y modificó
  solamente proyecto y mapa copiados. No reproduce la excepción HTTP original.
- La guarda escribe el marcador y efectúa otra observación antes de entrar al handler;
  una excepción en esa ventana puede dejar PENDING sin haber iniciado las escrituras.
  Es una posibilidad de código, no la causa original demostrada.
- Ownership observado: RECOVERY, sin owner_id, producer_active=false, children=0,
  dispatching=0, sin operación activa. Motivo: mutación administrativa incompleta.
- reconcile_quiescent rechaza explícitamente _administrative_incomplete. No existe
  API de reparación de journal administrativo. Reiniciar tampoco lo resuelve:
  JobService reinstala ese bloqueo al encontrar el marcador.
- Snapshot final: DEGRADED, HTTP STALE, position/serial freshness=false. Los valores
  almacenados ready/velocity=0 no acreditan reposo actual.
- Evento de sondeo anterior: después del retracto raw=True y filtered=True, paquete
  fresco, estabilidad superior al filtro requerido. La protección rechazó seguir.
  Actualmente probe_live=STALE: no demuestra contacto presente ni desconexión física.
  No permite distinguir contacto persistente, cableado/polaridad o fallo de señal.

## Resultado

NEW_PREPARATION_AVAILABLE = NO
PRODUCTION_MODIFIED = NO
JOURNAL_REMOVED = NO
OWNERSHIP_FORCED = NO
PHYSICAL_COMMANDS = NO
SERVICES_RESTARTED = NO

No hay una ruta existente que pueda completar esta reparación sin ignorar la
barrera administrativa. La solución debe introducir reconciliación explícita y
exclusiva del journal, con comprobación de identidad/bytes, registro histórico del
resultado y evidencia física vigente cuando el contrato la exige; después usar el
reset completo existente. No es válido borrar el archivo o cambiar flags en vivo.
Conservar JobRun cancelado, originales, configuración y evidencia. No se ha hecho
el reset completo ni habilitado controles. No repetir sondeo hasta verificar señal
OPEN fresca y resolver la causa de contacto persistente, sin movimiento automático.
