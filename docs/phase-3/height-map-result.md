# Resultado de Fase 3

Fecha de cierre técnico local: 2026-08-01.

Resumen:

- Se eliminó el monkey patch global de `PhysicalMapService`.
- Se integró estado de progreso, `last_error`, `last_progress_at`, pausa, cancelación, reconciliación y protección post-persistencia en los servicios productivos.
- La API expone `GET /physical-map` mediante un método público puro: `get_latest_map()`.
- La pestaña Mapa del frontend ahora rehidrata la receta persistida, muestra exclusiones y primer/último punto, y bloquea iniciar preview con configuración inválida.

Validación cerrada en este worktree el 2026-08-01:

- `git diff --check`
- `MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONPATH=src python -m unittest discover -s tests -v`
- `MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONPATH=src python -m unittest -v tests.test_api tests.test_gcode_analysis tests.test_heightmap tests.test_job_service tests.test_moonraker_client tests.test_project_service tests.test_web_mvp`
- `cd frontend && npm run lint && npm run test && npm run build`

Estado pendiente fuera de este worktree:

- verificar GitHub Actions del PR #3 después del push;
- mantener el PR en borrador hasta nueva revisión humana.
