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

BACKEND_TESTS = 658/658 PASS
FRONTEND_TESTS = 154/154 PASS (17 archivos)
LINT = PASS
BUILD = PASS
API_E2E = 19/19 PASS
BROWSER_E2E = PASS (Firefox, 1440 y 390 px)
GIT_DIFF_CHECK = PASS

NEW_BLOCKERS = []
FINAL_HEAD = consultar hash exacto en la entrega final; este reporte pertenece al commit de cierre
GIT_STATUS = se verifica limpio después del commit de cierre
PRODUCTION_MODIFIED = NO
PHYSICAL_COMMANDS = NO
SERVICES_RESTARTED = NO
PUSH = NO
MERGE = NO
READY_FOR_DEPLOY_AND_PHYSICAL_MESH_RETEST = YES
```

## Cambios y evidencia

| Contrato | Implementación y prueba |
|---|---|
| Validación real | `MeshPreparationService.evaluate` llama a `build_coverage_report`, incluyendo segmentos XY completos. Pruebas de trayectoria fuera, exclusión rectangular/circular entre vértices, análisis faltante/obsoleto y Z segura fuera de límites. |
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
