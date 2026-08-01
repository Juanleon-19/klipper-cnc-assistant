# Diagnóstico del bloqueo de la malla

Fecha de diagnóstico: 2026-08-01.

## Causa principal

El bloqueo de Fase 3 venía de mezclar estado de ejecución efímero con el payload estable del mapa mediante un monkey patch global en `application/__init__.py`.

Efectos observados:

- `PhysicalMapService` quedaba modificado para toda la aplicación, incluida la preparación de trabajos.
- `get_active()` y `get_by_id()` empezaban a devolver un mapa decorado con campos temporales como `last_progress_age_s`.
- `CompensatedGCodeService` y `JobService` hasheaban ese payload decorado; como `last_progress_age_s` cambia en cada lectura, los artefactos compensados dejaban de coincidir y `prepare_run()` se quedaba en `JOB_VALIDATING`.
- La edad del progreso también se calculaba con mezcla de timestamps naive y aware, lo que provocaba `TypeError` al actualizar estados del worker.

## Corrección aplicada

- Se eliminó `src/klipper_cnc_assistant/application/physical_map_phase3_patch.py`.
- Se quitó `apply_phase3_patch(PhysicalMapService)` de `application/__init__.py`.
- La lógica necesaria se integró directamente en `physical_map_service.py` y `mesh_execution_service.py`.
- `get_latest_map()` quedó como consulta pública pura para la API; ahí sí se decora `last_progress_age_s`.
- `get_active()` y `get_by_id()` conservan un payload estable para servicios internos y hashes de artefactos.
- `mesh_execution_service._iso_now()` ahora genera timestamps UTC con zona horaria.
- `PhysicalMapService._parse_datetime()` normaliza timestamps naive a UTC.

## Reproducción cubierta por pruebas

- `tests.test_physical_integration.PhysicalIntegrationTest.test_mesh_execution_worker_completes_2x2_without_per_point_frontend_continue` verifica avance continuo entre puntos.
- `tests.test_physical_integration.PhysicalIntegrationTest.test_mesh_worker_watchdog_cancels_hung_point_and_preserves_measured_progress` reproduce el watchdog sobre un probe colgado y confirma preservación de progreso medido.
- `tests.test_physical_integration.PhysicalIntegrationTest.test_pause_requested_between_points_stops_before_next_probe` y `test_pause_requested_during_point_stops_before_next_point` fijan la pausa determinista sin iniciar un punto nuevo.
- `tests.test_physical_integration.PhysicalIntegrationTest.test_cancel_requested_between_points_stops_before_next_probe` y `test_cancel_requested_during_point_finishes_worker_without_next_probe` fijan la cancelación determinista y la liberación del worker.
- `tests.test_physical_integration.PhysicalIntegrationTest.test_reconcile_missing_worker_pauses_map_and_allows_resume` fija la recuperación de un worker desaparecido.
- `tests.test_physical_integration.PhysicalIntegrationTest.test_post_persist_error_does_not_repeat_confirmed_point` garantiza que un punto confirmado no se repite si falla algo después de persistirlo.
- `tests.test_physical_integration.PhysicalIntegrationTest.test_resume_rejects_changed_context_and_active_worker` cubre invalidaciones de reanudación por contexto y worker activo.
- `tests.test_physical_integration.PhysicalIntegrationTest.test_mesh_worker_prevents_double_start_and_releases_after_unexpected_error` verifica liberación de ownership y recuperación tras error.
- `tests.test_api.ApiTest.test_pause_physical_map_is_idempotent_and_preserves_next_point` verifica pausa persistida sin iniciar otro punto.
- `tests.test_api.ApiTest.test_get_physical_map_returns_latest_paused_state_without_writing_files` verifica lectura pura con `last_error` y edad de progreso.
