# Estabilización de Mapa de alturas / Malla

## Resultado

Implementación aislada sobre `7b72a8b43ed8df3772e43923c844fdf970b2abbc`.
Rama: `fix/mesh-workflow-stabilization-20261012`.
La auditoría, causas y tabla completa de transiciones están en [AUDITORIA.md](AUDITORIA.md).
No se desplegó ni se utilizaron dispositivos físicos.

```text
LIMIT_VALIDATION_REAL = YES
LIMIT_VALIDATION_USES_EXECUTION_COVERAGE = YES
OUTSIDE_TOOLPATH_BLOCKS_ARM = YES
EXCLUSION_CROSSING_BLOCKS_ARM = YES

ARM_STUCK_FIXED = YES
ARM_BLOCK_REASON_VISIBLE = YES

CLEAR_PREVIEW = PASS
REBUILD_MESH = PASS
REPEAT_MEASUREMENT = PASS
PAUSE = PASS
RESUME = PASS
RETRY_FAILED_ONLY = PASS
CANCEL = PASS

PROBE_PARAMETERS_EDITABLE_WHEN_CUSTOM = YES
GENERAL_PROFILE_READONLY_EXPLAINED = YES
MESSAGE_SEVERITY = PASS
FLOW_GUIDE = PASS

BACKEND_TESTS = 671/671 PASS
FRONTEND_TESTS = 165/165 PASS (18 archivos)
LINT = PASS
BUILD = PASS
API_E2E = 29/29 PASS
BROWSER_E2E = PASS (Firefox, 1440 y 390 px)
GIT_DIFF_CHECK = PASS

NEW_BLOCKERS = []
FINAL_HEAD = consultar hash exacto en la entrega final; este reporte pertenece al commit de cierre
GIT_STATUS = se verifica limpio después del commit de cierre
PRODUCTION_MODIFIED = NO
PHYSICAL_COMMANDS = NO
SERVICES_RESTARTED = NO
PUSH = YES (solo rama del PR #25; sin merge)
MERGE = NO
READY_FOR_DEPLOY_AND_PHYSICAL_MESH_RETEST = YES
```

## Cambios y evidencia

| Contrato | Implementación y prueba |
|---|---|
| Validación real | `MeshPreparationService.evaluate` llama a `build_coverage_report`, incluyendo segmentos completos que requieren compensación de superficie. Pruebas de trayectoria fuera, exclusión rectangular/circular entre vértices, análisis faltante/obsoleto y Z segura fuera de límites. |
| Gate persistido | Preview, validación y mapa deben compartir fingerprint de geometría, fuentes/operaciones, placement, origen, Z, perfil y límites observados. Arm/start y cada punto verifican contexto. Pruebas de arm sin validación, configuración cambiada, archivo cambiado y geometría persistida alterada. |
| Recovery en ARM | Evaluación read-only con razón y acción antes del clic; el armado vuelve a comprobar y reserva ownership. Pruebas HTTP de recovery/owner activo; el navegador comprueba el bloqueo visible. No se modifica ni se revive el token. |
| Pausa/reanudación | Thread y finally reales; termina el punto comprometido, pausa antes del siguiente y reanuda sin repetir medidos. Pruebas durante/entre puntos y pausa concurrente con progreso. Sesión cambiada y recovery siguen bloqueando. |
| Retry | Selección explícita FAILED/RETRY_REQUIRED, individual o conjunto; no procesa PENDING/MEASURED/EXCLUDED. Resultado con número recuperado e índices restantes. Un fallo nuevo detiene el lote por seguridad. |
| Cancelación | Intención terminal dominante; completa el punto comprometido y solo libera con quiescencia comprobada. No llama a cancelación de emergencia para una cancelación voluntaria. Prueba con punto bloqueado y finally real. |
| Rehacer | Archiva la versión anterior, conserva receta/márgenes cero/origen y solo la referencia físicamente vigente; nueva versión sin muestras y desarmada. Rearmado sin una nueva captura Z. Sesión anterior no se revive; malla parcial de otra adquisición exige rehacer. |
| Preview | Limpiar solo elimina estado transitorio, sin endpoints destructivos. Abort/id de request impide respuestas tardías. Pruebas React de clear, invalidación y cambio de receta durante armado; E2E confirma que el mapa persistido permanece. |
| UX | General read-only explicado, custom editable y restauración; ayudas/unidades, guía de cuatro pasos, acciones de rehacer separadas, severidad explícita. Pruebas React y navegador real. |
| Visualización | Trayectoria analizada sobre material/región/márgenes/exclusiones/puntos, dominio verde al validar y primer conflicto rojo. Reutiliza geometría disponible sin nuevas descargas. |
| Seguridad existente | Suite completa incluye tokens, cambio de sesión, ownership, MotionAuthorizer, CAS/cancel, PrintIdentity, referencia inicial → finally del mapa → ejecución y A,A,B,B con mapa único. |

## Validación reproducible

Desde el worktree aislado, con Python del entorno del proyecto y **todas** las
pruebas backend bajo:

```sh
export MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false
export PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. PYTHONWARNINGS=ignore
python -m unittest tests.test_mesh_preparation tests.test_mesh_workflow_states -q
python -m unittest discover -s tests -v
(cd frontend && npm test)
(cd frontend && npm run lint)
(cd frontend && npm run build)
python -m unittest tests.test_mesh_preparation tests.test_mesh_workflow_states.MeshWorkflowStatesTest.test_api_e2e_preview_validate_arm_start_pause_resume_rebuild tests.test_reference_execution_handoff.RealMeshWorkerHandoffTest.test_api_diagnostic_prepare_explicit_start_and_jit_after_real_mesh tests.test_final_workflow_api -q
python tests/mesh_workflow_browser.py
git diff --check
```

El browser E2E usa Firefox/geckodriver, frontend compilado y backend real con
almacenamiento temporal y transportes fake. El fixture suministra observaciones
frescas de telemetría y heartbeat serial; no cambia los timeouts ni la política
productiva. Sus endpoints locales de prueba son independientes del servicio real.
Verifica que solo se produjo una captura inicial fake y ningún envío de G-code al
transporte de prueba.

Evidencia local: `/tmp/kca-mesh-stabilization/`, logs `focused-final.log`,
`backend-final-3.log`, `frontend-final.log`, `lint-final-2.log`, `build-final-2.log`,
`api-e2e-final.log`, `browser-final-2.log`; resultado y capturas en `browser/`.
Los logs y capturas no se incluyen en Git.

Se ejecutaron focalizados antes de la suite completa. Los fallos encontrados
obligaron a repetir la validación afectada: fixtures históricos sin el nuevo gate,
una carrera real pausa/progreso corregida conservando CAS y un heartbeat fake que
caducaba durante esperas deliberadas. No se omitieron tests para obtener PASS.

## Límites y comprobación productiva pendiente

- La rama local no integrada `fix/integracion-runtime-mapa-ejecucion` (`2bcfcf2`)
  y el checkout hotfix con cambios pendientes no se tocaron. No se identificó una
  rama de telemetría/idle-stale ni se incorporó trabajo supuesto de esa procedencia.
  La inspección y esta limitación se comunicaron antes de implementar.
- El chequeo de soporte es conservador junto a celdas excluidas: puede exigir
  ampliar dominio o retirar una exclusión pegada a la trayectoria. No extrapola.
- El build conserva el aviso de chunks mayores de 500 kB; compila correctamente.
- No se infiere la causa física histórica del recovery sin evidencia del incidente.
  Se corrigió su presentación y se mantiene el bloqueo legítimo.
- Validación de software completa: lista para revisión y despliegue autorizado.
  La validación física posterior requiere autorización independiente y una referencia
  capturada en sesión vigente cuando la histórica ya no sea válida. Esta tarea no
  demuestra contacto/retracto real ni autoriza movimientos.
- Producción conserva HEAD `7b72a8b43ed8df3772e43923c844fdf970b2abbc` y sus elementos
  locales preexistentes `AGENTS.md`, `.codex/` y `graphify-out/`, sin modificación.

## CORRECCIONES_AUDITORIA_EXTERNA

PR #25; revisión sobre el HEAD auditado
`38fe161979f3bb15e33c76fd7ff5a8a48dd0094b`, en la misma rama
`fix/mesh-workflow-stabilization-20261012`. Esta sección actualiza los resultados
anteriores y corrige la semántica descrita en la auditoría inicial.

1. **Paridad de cobertura.** El preflight usa `build_coverage_report` con su filtro
   normal `segment_uses_surface_map`, igual que compensación/JIT. Un G0 fuera del
   mapa no bloquea ARM si no requiere compensación; los segmentos compensados se
   comprueban completos contra dominio, exclusiones y celdas sin soporte. El
   contador `segments_checked` cuenta esos segmentos. Los límites Klipper de los
   puntos de sondeo y la Z segura siguen siendo una comprobación separada. No se
   añadió una autorización de viajes G-code ni se relajaron los guards físicos.
2. **Carreras de ownership.** ARM conserva la revisión observada en readiness y
   REBUILD captura la revisión antes de adquirir; ambos usan `expected_revision`.
   Una carrera devuelve HTTP 400 accionable sin iniciar planificación/movimiento.
   Si el contexto cambia después de planificar, el resultado queda desarmado. Un
   fallo de release retira solamente el productor propio y conserva recuperación,
   su causa previa y las evidencias; no reconcilia ni libera otro owner. El error
   original tiene prioridad sobre el de cleanup. El coordinador no se modificó.
3. **Último retry.** Al agotar objetivos se comprueban las muestras reales. Si todas
   están medidas/excluidas se completa mediante la finalización y validación normal;
   con pendientes se pausa. No se seleccionan pendientes durante retry. Un fallo
   nuevo pausa y no avanza al siguiente pendiente. La cancelación conserva prioridad.
4. **Inspección advisory.** Cambios de recuperación, operación activa, owner/revisión,
   estado runtime, Klipper, conexión, validación o fingerprint invalidan inmediatamente
   el assessment mostrado y disparan una nueva consulta. Respuestas tardías se
   descartan. El snapshot/API expone el owner existente en solo lectura; no duplica
   autoridad ni altera la política de frescura. La UI no autoriza ARM por sí sola.

Pruebas añadidas: paridad G0/corte, celda sin soporte, cambio de owner entre readiness
y acquire, revisión que cambió aunque vuelva a estar libre, recovery durante ARM,
cleanup con/sin excepción anterior, carreras y cleanup de REBUILD, snapshot de owner
read-only, retry final/con pendientes/con fallo y refresco de inspección en diez
cambios distintos más respuesta tardía. Se conservan los negativos de dominio y
exclusión y el resto de regresiones existentes.

Evidencia de esta revisión: `/tmp/kca-mesh-reaudit/`. Focalizados backend: 41/41;
inspección UI: 11/11. Backend completo: 671/671 (322.174 s); frontend completo:
165/165 en 18 archivos; API E2E: 29/29; browser E2E: PASS en 1440 y 390 px.
Lint, build y git diff --check: PASS. Ninguna prueba utiliza hardware real.
Logs: `focused-final.log`, `frontend-focused.log`, `backend.log`,
`frontend-final.log`, `lint-final.log`, `build.log`, `api-e2e.log`,
`browser-e2e.log`; capturas y resultado de navegador en `browser/`.

Durante la suite completa se corrigió también una carrera del test histórico de
Preparar trabajo: espera a que el botón esté habilitado tras la inspección antes
del clic. No cambió el comportamiento de Ejecución ni se omitió la prueba.


```text
EXECUTION_COVERAGE_PARITY = PASS
RAPID_OUTSIDE_MAP_ALLOWED_WHEN_UNCOMPENSATED = PASS
CUT_OUTSIDE_MAP_BLOCKED = PASS
OWNERSHIP_RACE_RETURNS_CONTROLLED_ERROR = PASS
OWNERSHIP_RACE_NO_PARTIAL_ARM = PASS
OWNERSHIP_CLEANUP_FAIL_CLOSED = PASS
FINAL_RETRY_AUTO_COMPLETES = PASS
RETRY_WITH_PENDING_REMAINS_PAUSED = PASS
ARM_INSPECTION_REFRESHED_ON_OWNERSHIP_CHANGE = PASS
BACKEND_TESTS = 671/671 PASS
FRONTEND_TESTS = 165/165 PASS
LINT = PASS
BUILD = PASS
API_E2E = 29/29 PASS
BROWSER_E2E = PASS
GIT_DIFF_CHECK = PASS
NEW_BLOCKERS = []
NEW_HEAD = hash exacto del commit de este reporte en la entrega final y PR #25
PR_NUMBER = 25
PRODUCTION_MODIFIED = NO
MAIN_MODIFIED = NO
PHYSICAL_COMMANDS = NO
SERVICES_RESTARTED = NO
READY_FOR_EXTERNAL_REAUDIT = YES
```

La publicación autorizada actualiza exclusivamente la rama del PR #25. No se
crea otro PR, no se integra main y no se despliega. Los resultados anteriores
658/658, 154/154 y API 19/19 corresponden al HEAD inicialmente auditado; los
resultados vigentes son los de esta sección.
