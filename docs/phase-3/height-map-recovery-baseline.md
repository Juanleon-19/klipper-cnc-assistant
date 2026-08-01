# Línea base de recuperación del mapa de alturas

Fecha base: 2026-08-01.

Estado recuperado en esta rama:

- El worker de malla puede iniciar, pausar, reanudar y cancelar sin monkey patch global.
- El mapa conserva `last_error`, `last_progress_at`, `pause_requested`, `cancel_requested` y `next_point_index` en persistencia.
- Un error después de persistir un punto no vuelve a medir ese punto en el mismo ciclo.
- Un worker desaparecido puede reconciliarse a un estado pausado y recuperable.
- `GET /physical-map` ya no escribe, no finaliza mapas y no migra durante lectura.
- `plan-from-reference` persiste exactamente la receta usada para la preview y no dispara movimiento físico.

Validación base ejecutada el 2026-08-01:

- `python -m unittest -v tests.test_job_service tests.test_physical_integration tests.test_machine_runtime`
- `python -m unittest -v tests.test_api.ApiTest.test_plan_from_reference_uses_single_observation_and_persists_exact_parameters tests.test_api.ApiTest.test_get_physical_map_returns_latest_paused_state_without_writing_files tests.test_api.ApiTest.test_pause_physical_map_is_idempotent_and_preserves_next_point`
- `npm run test -- --run src/features/projects/ProjectWorkspace.test.tsx`

Validación completa cerrada el 2026-08-01:

- `git diff --check`
- `MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONPATH=src python -m unittest discover -s tests -v`
- `MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONPATH=src python -m unittest -v tests.test_api tests.test_gcode_analysis tests.test_heightmap tests.test_job_service tests.test_moonraker_client tests.test_project_service tests.test_web_mvp`
- `cd frontend && npm run lint && npm run test && npm run build`
