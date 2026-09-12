# Integración local PR24 después de P0

Base exacta: `2c568129b4bb0657680c3940b38d645f2637dbad`.
Head integrado: `fd102665153b6036cd65e864aeef792de097fc18`.
Rama: `integration/p0-pr24-2026-09-12`.

## Resolución semántica

Se resolvieron tres archivos con conflictos: `execution/job_service.py`,
`machine/runtime.py` y `tests/test_job_service.py`.

- JobService conserva cancelación dominante, CAS, PrintIdentity y las
  validaciones de referencia antes de JIT/start/resume. El progreso PR24 usa
  `_save_run`/JobRunStore; el guard de settings consulta el coordinator, los
  workers existentes y JobRunStore. Runtime vuelve a consultar el mismo owner
  antes de editar/persistir settings.
- Runtime combina feeds Z y callbacks PR24 con permit, source_context y
  dependencies P0. MotionAuthorizer y el process lock siguen autorizando las
  emisiones. No se modificaron las autoridades serial, JobRunStore,
  PrintIdentity ni el worker unificado de mapa.
- Los tests mantienen las dos operaciones ya terminadas antes del cambio y
  verifican que probe no inicia automáticamente la siguiente operación. La
  fixture de velocidades mide de nuevo después de cambiar configuración.
  El test geométrico sin runtime genera únicamente preview no ejecutable;
  otro test comprueba los mismos feeds en Legacy ejecutable con token vigente.

El nuevo feed de clearance participa en el fingerprint de referencia. La
aproximación ya participa mediante el prefijo reference. Ningún token viejo se
migra ni se considera confiable por timestamp.

## Flujo conservado

SPINDLE_STOP_REQUIRED → confirmación humana → clearance saliente → XY estación
→ Z estación → TOOL_CHANGE_REQUIRED → instalar y confirmar → clearance entrante
→ XY referencia → approach Z → probe → referencia canónica con token
→ READY_TO_RESUME → confirmación humana «Spindle preparado — continuar»
→ revalidación → Legacy JIT → upload sin iniciar → validación final → start.

La confirmación del spindle no representa un sensor. Los feeds XY de corte
siguen siendo los F modales de FlatCAM; no se creó un setting de corte XY.

## Auditoría P0

| Regresión | Evidencia automatizada |
| --- | --- |
| Position stale | test_stale_position_fresh_arduino_manual_enabled_has_zero_emissions; test_coordinated_motion_rejects_stale_consumed_frame |
| Owner incompatible | test_job_owner_rejects_manual_even_when_all_frames_are_fresh; test_settings_guard_uses_shared_physical_owner |
| Process lock y serial | test_physical_process_lock, test_runtime_serial_lifecycle, test_serial_driver_lifecycle, test_runtime_reconnect_hotfix |
| Cancelación dominante | test_A_cancel_before_start_prevents_remote_start; test_B_eta_loaded_before_cancel_cannot_change_cancelled_domain; test_cancel_dominates_new_transition_progress_callback |
| Identidad incorrecta | test_E_paused_other_file_cannot_resume; test_F_identity_query_failure_sends_no_remote_action |
| Mapa unificado | test_unified_mesh_probe; endpoint execute-next delega start_next y execute-all delega start_all |
| Referencia obsoleta | test_physical_reference_token; test_auxiliary_z_speed_changes_invalidate_ready_reference |

## Validación

Todos los comandos backend se ejecutaron con `MACHINE_MODE=simulated`,
`MACHINE_AUTO_CONNECT=false` y `PYTHONPATH=src:tests` en el entorno virtual
existente. Los transportes físicos son fake y los proyectos/G-codes sintéticos
se crean en directorios temporales.

Focalizados:

```bash
python -m unittest tests.test_physical_ownership tests.test_motion_authorization tests.test_physical_process_lock tests.test_runtime_serial_lifecycle tests.test_serial_driver_lifecycle tests.test_runtime_reconnect_hotfix tests.test_job_run_store tests.test_job_cancel_identity tests.test_unified_mesh_probe tests.test_physical_reference_token tests.test_job_service tests.test_machine_runtime tests.test_web_mvp tests.test_physical_integration tests.test_api tests.test_pr24_safety_integration tests.test_mesh_failure_recovery_guards tests.test_mesh_failure_recovery_hotfix tests.test_job_plan_hotfix tests.test_reference_go_to_state_hotfix -v
python -m unittest tests.test_pr24_safety_integration -v
```

Resultados: 321/321 OK y 7/7 OK. Se repitió el módulo de integración al añadir
la revalidación de settings del runtime. La primera iteración focalizada detectó
dos fixtures PR24 anteriores a los tokens; se adaptaron sin quitar las guardas.

Suite completa y frontend:

```bash
MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false python -m unittest discover -s tests -v
cd frontend
npm run lint
npm run test -- --run
npm run build
cd ..
git diff --check
```

Backend completo: **447/447 OK**, 77,116 segundos, una ejecución tras pasar
focalizados. P0-01, P0-02, P0-03, P0-04, P0-05 y P0-06: **PASS**.
Frontend lint: **OK**. Frontend tests: **136/136 OK** en 16 archivos,
51,48 segundos. Frontend build: **OK**, TypeScript sin errores y Vite en
50,82 segundos (advertencia de tamaño de bundles, sin fallo de compilación).
`git diff --check` y `git diff --cached --check`: **OK**.

Entrega únicamente local. Sin push, cambios en main, hardware, servicios,
movimiento físico ni G-code real.
