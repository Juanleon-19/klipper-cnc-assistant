# Recuperación administrativa explícita

Base: `512e86348e4be77f5b7798b1df0423dab5dcbd7b`.
Rama: `fix/administrative-map-recovery-20261026`.
Worktree: `kca-worktrees/administrative-map-recovery-20261026`.

## Causa y corrección

La guarda persistía PENDING antes de terminar la validación previa al handler.
Un fallo posterior marcaba administración incompleta, pero no existía un camino
para conciliarla. La recuperación física rechazaba correctamente esa condición,
aunque su mensaje la confundía con productor activo o telemetría. Reiniciar la
aplicación reinstalaba el bloqueo al encontrar el journal.

El incidente tiene proyecto y mapa idénticos al respaldo verificado; el mapa no
fue archivado y la asociación no se retiró. El HTTP 400 original no guardó su
excepción: no se inventa cuál validación falló. El JobRun fue cancelado y un informe
de validación fue actualizado por actividad posterior al despliegue, ajena a este
trabajo. Ninguno se restaura desde el respaldo ni se oculta.

## Contrato implementado

- Journal v2: identidad, acción, sesión, fase, pasos completados, hashes previos,
  checkpoint posterior, archivos agregados/modificados/retirados y causa del fallo.
- Captura de evidencia sin mutex de bookkeeping durante disco/HTTP; la reserva
  exclusiva permanece. Excepciones que impidan verificar checkpoint conservan
  la evidencia anterior y el bloqueo, no inventan una transacción completa.
- GET `/api/machine/administrative-recovery`: inspección sin recuperación implícita.
- POST `/api/machine/administrative-recovery/{id}`: acción explícita con identidad
  exacta. Exige ausencia de leases, productor, hijos, dispatch, workers, edición
  concurrente y operation; MovementLock afirmativamente libre; CAS y sesión estables.
- Después de los hashes se obtiene HTTP fresco de reposo, homing/ready/velocidad
  según el contrato existente y se valida la identidad de cada JobRun. No se
  considera la falta de datos como reposo. No hay comandos físicos.
- Si los archivos coinciden con la preimagen: NO_DATA_CHANGE. Si coinciden con
  checkpoint posterior verificado: conserva las escrituras y el historial,
  distinguiendo COMPLETED de PARTIAL_PRESERVED_RETRY_RESET. Nunca hace rollback ni
  replay implícito de datos. Si no hay checkpoint que explique los cambios, bloquea
  con la causa concreta; no adopta cambios externos arbitrarios.
- Archiva el journal original byte por byte, con recibo de conciliación y hashes;
  fsync de directorios. No lo elimina para eludir controles. Repetir es idempotente,
  incluso tras interrupción entre archivado y actualización del coordinador.
- Solo cierra la condición administrativa incompleta. Ownership sigue RECOVERY;
  no certifica reposo, no renueva tokens, no habilita joystick ni libera un lease.
  El usuario debe verificar reposo con el mecanismo existente y después usar
  Reiniciar proceso. El reset conserva historial, originales y configuración.
- Un JobRun cancelado puede archivarse con filename remoto vacío SOLO si su
  impresión tiene toda la evidencia previa de finalización y vinculación exacta
  del start_attempt. La cancelación por sí sola no prueba finalización. Intentos
  ambiguos, archivo diferente o finalización no demostrada siguen bloqueados.

## Journal antiguo de este incidente

`scripts/administrative_recovery_evidence.py` importa evidencia local explícita;
no conecta transportes, no restaura datos y no libera ownership. Acepta únicamente
el reset-map antiguo con su conjunto de escrituras auditado (proyecto y mapa).
Verifica hashes del respaldo y coincidencia de entradas, configuración, mapas y
artefactos. Conserva y fija por hash los journals actuales y el informe de validación
independiente; este último jamás se usa como autoridad física. No sobrescribe una
importación existente. Dependencias inesperadas o nuevas bloquean la importación.

Se ejecutó sobre COPIA TEMPORAL del caso real: PASS, 544 dependencias. Datos originales
no usados como fixture escribible. La evidencia se debe importar en producción solo
tras instalar/arrancar y verificar estabilidad, antes de la acción explícita:

```sh
MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. \
  .venv/bin/python scripts/administrative_recovery_evidence.py \
  --data-dir /ruta/productiva/data --backup-dir /ruta/privada/respaldo-verificado
```

Las rutas concretas del respaldo se conservan fuera de Git. El script no ejecuta
la recuperación; el endpoint vuelve a comprobar hashes, sesión, exclusión y reposo.

## Interfaz y recorrido

En Referencia, la alerta existente identifica acción/fase/causa, ofrece
**Recuperar transacción administrativa** y no muestra «productor activo» por ese
journal. Después de conciliar cambia al control existente **Verificar reposo y
recuperar controles**. A continuación, **Reiniciar proceso** realiza el reset completo.
No hay nueva navegación ni movimiento automático. No se reanuda el JobRun cancelado.

Browser real React → FastAPI → runtime con transportes falsos comprobó:
conexión; interrupción administrativa; recuperación desde interfaz sin emisiones;
verificación de reposo; reset completo; referencia, malla, JIT y operaciones con
confirmaciones manuales; recarga y navegación responsive. Ninguna conexión física.

## Validación

- Backend completo: 851/851 PASS, 506,139 s.
- Focalizadas/regresiones + API E2E + recorrido real backend: 56/56 PASS.
- Nuevas pruebas de recuperación: 14 (reverificadas tras ajustes de importación).
- API E2E: 4/4 dentro de la selección integrada.
- Frontend completo final: 204/204 PASS.
- Lint y build: PASS; advertencia preexistente por tamaño del bundle.
- Browser E2E integrado: PASS, incluye recuperación administrativa explícita.
- Diff check: PASS.

La suite completa precedió los últimos ajustes de metadatos/importación; estos se
verificaron con la selección focalizada, copia temporal y navegador final. No se
repitió innecesariamente la suite completa tras ajustes acotados ya cubiertos.

Casos: fallos HTTP/disco, escrituras parciales conservadas, sesión/CAS cambiados,
productor retenido, lock desconocido, archivos inesperados, restart simulado,
idempotencia, interrupción después de archivar, GET sin efectos y error UI visible.

## Instalación y límites

No se ha desplegado ni modificado producción. El servicio sigue en el commit base.
La guardia efectiva permanece SendSIGKILL=no / Restart=no. sudo requiere contraseña.
Además, el aislamiento confirmado antes del despliegue anterior no acredita la
ventana actual después de las pruebas físicas; no se envió ninguna parada.

Para instalar: respaldo nuevo y hashes; aislamiento actual; parada única de Assistant
con guardia y salida verificada; fast-forward al commit de esta rama; publicar dist
validado preservando assets; arranque con MACHINE_AUTO_CONNECT=false; integridad y
HTTP de consulta; importar evidencia antigua. Si falta evidencia física fresca,
el journal queda pendiente y la interfaz explica el requisito. No se ejecuta reset
productivo ni recuperación física a ciegas.

Sonda: algoritmo intacto. Se registró señal activada y fresca después de retracto.
No demuestra por sí sola si fue contacto persistente, polaridad/cableado u otra
condición. Antes de sondear otra vez el operador debe verificar OPEN fresco con la
herramienta sin contacto. No se hicieron movimientos para investigar esto.

SOFTWARE_VALIDATED = YES
PRODUCTION_MODIFIED = NO
JOBRUN_MODIFIED = NO
PHYSICAL_COMMANDS = NO
SERVICES_RESTARTED = NO
DEPLOY = NO
PHYSICAL_VALIDATION_PENDING = YES
