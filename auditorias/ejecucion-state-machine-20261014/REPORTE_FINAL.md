# Estabilización de Ejecución — cierre de software

Actualización posterior: [correcciones de auditoría externa](CORRECCIONES_AUDITORIA_EXTERNA.md).
Ese documento registra el cierre del hallazgo de frescura y las dos correcciones
UX, con la validación actual de 703 backend y 169 frontend. Las cifras siguientes
se conservan como evidencia de la entrega original auditada.

Fecha: 2026-10-15. Rama: `fix/execution-state-machine-20261014`.
Base: `95f47cb93b28f499369cccda51a179b876f525db`.
Commit funcional validado: `b85d2b837006ee4707d22485a08a133b14d821b4`.
El commit posterior agrega exclusivamente este reporte y el diagnóstico sanitizado.

## Incidente y límites de la evidencia

Véase [DIAGNOSTICO.md](DIAGNOSTICO.md). El run conservado comenzó después del
reinicio observado: no demuestra un preflight huérfano causado por ese reinicio.
Su worker terminó por MotionAuthorizationError al revalidar un contexto envejecido
después de validaciones costosas. La referencia estaba LISTA al iniciar; el
diagnóstico posterior la bloquea por recovery, no por una sesión distinta.

El frontend fabricaba un 503 ante cualquier fallo del polling. Los accesos live
observados en journal fueron 200; no se conserva el disparador de red original.
No se atribuye al backend una excepción HTTP que no fue observada. La ausencia de
supervisor se deduce del worker terminado y del estado terminal, no de una
inspección de memoria del proceso productivo.

## Corrección

- Readiness inicial y salud activa son proyecciones distintas. PREFLIGHT sano
  muestra PREPARANDO; los estados válidos del dominio producen snapshots live.
  Un fallo de diagnóstico no oculta el monitor y un fallo de polling conserva
  endpoint, status real y mensaje, sin inventar 503.
- La recuperación explícita de preparación exige standby/inactividad, ausencia
  de supervisor y ausencia de upload/start ambiguo. Revalida mapa, referencia y
  confirmación humana. Upload o identidad remota ambigua permanecen bloqueados;
  no hay reintento automático de start_print.
- Actualizar referencia es una acción explícita protegida por las autoridades
  físicas existentes. Conserva mapa y origen XY; no reactiva tokens históricos.
  Una nueva sesión exige evidencia nueva y nueva confirmación humana aplicable.
- Las lecturas costosas se completan antes de observar para dispatch. Después
  se conserva refresh → MotionAuthorizer → revalidación → send. Una guardia final
  de hashes de bytes y contexto detecta cambios de proyecto, fuente, mapa,
  artifact o configuración ocurridos durante la observación. Se mantienen CAS,
  cancelación dominante, PrintIdentity y exclusividad física.
- Cada operación genera Legacy JIT con la referencia vigente de su instalación.
  El metadata incluye SHA256 original y token; se comprueban mapa y fuente antes
  de usar el par generado. La fórmula sigue siendo Zref + map_delta + Z_programmed.
  Los perfiles standard/long_tool afectan los despejes, no la compensación.
- A,A,B,B,C conserva un mapa, tres instalaciones/referencias, dos mediciones
  posteriores a la inicial y cinco artifacts. Los cambios vuelven al mismo XY
  del mapa. Las operaciones consecutivas de la misma herramienta reutilizan Z.
- Legacy manual queda bloqueado durante run activo. La UI solicita artifacts
  diagnósticos no ejecutables y explica que el flujo normal genera JIT.
- Confirmaciones humanas prominentes, etiqueta clickeable y checkbox de 24 px;
  no se autoconfirman. Referencias y recovery muestran motivos accionables.

## Validación ejecutada sin hardware

Backend ejecutado con `MACHINE_MODE=simulated`, `MACHINE_AUTO_CONNECT=false`,
`PYTHONDONTWRITEBYTECODE=1`, `PYTHONPATH=src:.`, `PYTHONWARNINGS=ignore`.
Datos temporales y transportes fake; ninguna prueba autoriza una validación física.

| Validación | Comando | Resultado final |
| --- | --- | --- |
| Focalizados | `python -m unittest tests.test_execution_state_machine tests.test_job_cancel_identity tests.test_final_workflow_api -q` | 41/41 PASS |
| Backend completo | `python -m unittest discover -s tests -q` | 700/700 PASS |
| Frontend completo | `npm run test` en frontend | 168/168 PASS, 18 archivos |
| Lint | `npm run lint` en frontend | PASS |
| Build | `npm run build` en frontend | PASS |
| API E2E | Comando debajo | 6/6 PASS |
| Browser E2E | `python tests/execution_lifecycle_browser.py` | PASS |
| Diff | `git diff --check` | PASS |

API E2E:

```sh
python -m unittest \
  tests.test_execution_state_machine.ExecutionStateMachineTest.test_api_restart_recovery_and_manual_diagnostic_guards \
  tests.test_reference_execution_handoff.RealMeshWorkerHandoffTest.test_api_diagnostic_prepare_explicit_start_and_jit_after_real_mesh \
  tests.test_final_workflow_api -q
```

El navegador comprueba preflight activo, confirmación clickeable, ancho desktop
y móvil, diagnóstico sin escrituras y ausencia de comandos físicos en el fake.
Los tests nuevos cubren restart en READY, antes/después de artifact, upload,
RUNNING, TOOL_CHANGE_REQUIRED y READY_TO_RESUME; cambios de sesión, configuración
y archivos; transiciones standard→long, long→standard y long→long.
Las regresiones de MotionAuthorizer, PrintIdentity, CAS, cancelación, frescura y
malla forman parte del backend completo. El build conserva el aviso de chunks
mayores de 500 kB; no impide compilar. No se modificó el workflow de malla.

## Resultado solicitado

```text
PRODUCTION_INCIDENT_ROOT_CAUSE = IO costoso después de observar envejecía MotionContext; UI mezclaba readiness inicial con salud activa y fabricaba HTTP 503
PERSISTED_PREFLIGHT_AFTER_RESTART = NO (en el run observado)
REAL_503_SOURCE = fallback sintético del frontend; causa original del rechazo de polling no conservada
SUPERVISOR_MISSING = YES (worker terminado por error; no se demuestra pérdida por restart)
ACTIVE_STATE_READINESS_FIXED = YES
OPERATION_PREFLIGHT_PRESENTATION = PASS
SINGLE_MAP = PASS
SINGLE_REFERENCE_POINT_PER_TOOL_CHANGE = PASS
SAME_TOOL_REUSES_Z = PASS
STANDARD_TO_LONG_CLEARANCE = PASS
LONG_TO_STANDARD_CLEARANCE = PASS
PROFILE_DOES_NOT_CHANGE_COMPENSATION = PASS
JIT_PER_OPERATION = PASS
JIT_REFERENCE_BINDING = PASS
JIT_SOURCE_HASH_BINDING = PASS
JIT_FLATCAM_FEEDS_PRESERVED = PASS
RESTART_BEFORE_UPLOAD_RECOVERY = PASS
RESTART_AFTER_UPLOAD_FAIL_CLOSED = PASS
NO_DUPLICATE_START_PRINT = PASS
LEGACY_DIAGNOSTIC_ISOLATED = PASS
LIVE_EXECUTION_ALL_STATES = PASS
GENERIC_503_REMOVED = PASS
SPINDLE_CONFIRMATION_PROMINENT = PASS
REFERENCE_MESSAGES_ACTIONABLE = PASS
BACKEND_TESTS = 700/700 PASS
FRONTEND_TESTS = 168/168 PASS
LINT = PASS
BUILD = PASS
API_E2E = 6/6 PASS
BROWSER_E2E = PASS
GIT_DIFF_CHECK = PASS
NEW_BLOCKERS = []
FINAL_HEAD = commit de este reporte, consultable con git rev-parse HEAD
GIT_STATUS = limpio tras commit del reporte
PRODUCTION_MODIFIED = NO
PHYSICAL_COMMANDS = NO
SERVICES_RESTARTED = NO
READY_FOR_EXTERNAL_AUDIT = YES
```

No push, merge ni deploy. Main permanece en la base indicada. No se incorporan
otras ramas. La revisión externa y la posterior validación física requieren sus
propias etapas; este resultado acredita únicamente validación de software.
