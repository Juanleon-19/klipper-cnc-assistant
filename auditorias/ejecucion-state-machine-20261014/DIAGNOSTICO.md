# Incidente de Ejecución: evidencia read-only

Base inspeccionada: 95f47cb93b28f499369cccda51a179b876f525db.
No se copiaron datos productivos al repositorio. No se modificó current_run,
referencia, mapa, artifacts, servicios ni controles físicos. Se leyeron journal,
archivos JSON, GET machine/status y GET execution/diagnostic. No se invocó live
productivo: su implementación contiene reconciliación de standby y persistencia
ETA; los accesos live existentes se analizaron en journal.

## Hallazgos observados

- El proceso productivo arrancó el 2026-10-14 a las 21:29:58 UTC.
- El run actual comenzó el 2026-10-15 a las 00:30:13 UTC, entró a preflight a
  las 00:30:33, upload a las 00:30:58 y terminó JOB_ERROR a las 00:31:18.
- Por tanto, este run no quedó en preflight antes de ese reinicio. El historial
  disponible de runs archivados de ese día tampoco contiene un preflight iniciado.
  No se puede reconstruir otro incidente no conservado a partir de estos datos.
- Operación actual: índice 0. Estado de operación WAITING_FOR_KLIPPER; existe
  generated_file y remote_file. Start attempt quedó `uncertain`, con
  `may_have_been_sent=true`. Esto es evidencia ambigua: no debe reintentarse start.
- La excepción persistida es `MotionAuthorizationError: Klippy state ausente,
  stale o no ready.` Se produjo en `_dispatch → authorized_send → revalidate`,
  después del callback de validaciones y antes de llamar a send. El journal de
  intento se mantiene conservador; no se corrigen sus datos productivos.
- El worker terminó por esa excepción; no es evidencia de un supervisor perdido
  al reiniciar. El estado observado tiene owner RECOVERY, sin productor, hijos,
  dispatch ni operación física activa.
- La readiness persistida al inicio conserva `REFERENCE_VALID / LISTA`.
  El token de operación coincide con la sesión física, sesión de ownership e
  instalación observadas. El mapa activo sigue MESH_COMPLETE.
- El diagnóstico actual devuelve `REFERENCE_RECOVERY_REQUIRED`: la etiqueta
  CONTEXTO CAMBIÓ se debe a recuperación pendiente, no a Z diferente ni a un token
  de sesión anterior en esta observación. No se revivió ninguna referencia.
- Journal consultado desde el reinicio: accesos execution/live 200; sin excepción
  API 500/503 observada. El frontend fabricaba `ApiError(...,503)` para cualquier
  rechazo del polling. El disparador de red concreto de aquel rechazo no se
  conserva y no debe confundirse con la excepción del worker.

## Causa reproducible en software

El callback final hacía lecturas de proyecto/archivo/mapa/referencia después de
capturar el MotionContext. Ese trabajo puede exceder la ventana de frescura.
La prueba con frame envejecido por IO reproduce la necesidad de observar después
del trabajo costoso. La corrección no cambia el timeout: prepara, observa y
revalida inmediatamente antes de enviar.

Además, readiness de inicio se mostraba en estados activos. El preflight sano
quedaba etiquetado NO PUEDE INICIAR por EXECUTION_STATE, y cualquier fallo de
polling añadía un 503 sintético. Ahora se separan salud activa y readiness inicial,
y se conserva el error real del monitor sin ocultar el diagnóstico.

Los escenarios de restart solicitados se validan con repositorios temporales y
transporte fake, aunque el run productivo observado tuvo una causa diferente.
