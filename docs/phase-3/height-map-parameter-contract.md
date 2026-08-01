# Contrato de parámetros del mapa de alturas

Fecha de verificación: 2026-08-01.

| Parámetro UI | Campo API | Campo dominio | Campo persistido | Planeación | Preview |
| --- | --- | --- | --- | --- | --- |
| `grid_mode` | `grid_mode` | `PhysicalMeshConfig.grid_mode` | `mesh_config.grid_mode`, `grid_mode` | Sí | Sí |
| `rows` | `rows` | `PhysicalMeshConfig.rows` | `mesh_config.rows`, `rows` | Sí | Sí |
| `columns` | `columns` | `PhysicalMeshConfig.columns` | `mesh_config.columns`, `columns` | Sí | Sí |
| `edge_margin_left_mm` | `edge_margin_left_mm` | `PhysicalMeshConfig.edge_margin_left_mm` | `mesh_config.edge_margin_left_mm`, `edge_margins.left_mm` | Sí | Sí |
| `edge_margin_right_mm` | `edge_margin_right_mm` | `PhysicalMeshConfig.edge_margin_right_mm` | `mesh_config.edge_margin_right_mm`, `edge_margins.right_mm` | Sí | Sí |
| `edge_margin_bottom_mm` | `edge_margin_bottom_mm` | `PhysicalMeshConfig.edge_margin_bottom_mm` | `mesh_config.edge_margin_bottom_mm`, `edge_margins.bottom_mm` | Sí | Sí |
| `edge_margin_top_mm` | `edge_margin_top_mm` | `PhysicalMeshConfig.edge_margin_top_mm` | `mesh_config.edge_margin_top_mm`, `edge_margins.top_mm` | Sí | Sí |
| `max_spacing_mm` | `max_spacing_mm` | `PhysicalMeshConfig.max_spacing_mm` | `mesh_config.max_spacing_mm` | Sí | Sí |
| `margin_mm` | `margin_mm` | `PhysicalMeshConfig.margin_mm` | `mesh_config.margin_mm` | Sí | Sí |
| `safe_z_mm` | `safe_z_mm` | `PhysicalMeshConfig.safe_z_mm` | `probe_config.safe_z_mm` | Sí | Sí |
| `probe_step_mm` | `probe_step_mm` | `PhysicalMeshConfig.probe_step_mm` | `probe_config.probe_step_mm` | Sí | Sí |
| `probe_feed_mm_min` | `probe_feed_mm_min` | `PhysicalMeshConfig.probe_feed_mm_min` | `probe_config.probe_feed_mm_min` | Sí | Sí |
| `retract_mm` | `retract_mm` | `PhysicalMeshConfig.retract_mm` | `probe_config.retract_mm` | Sí | Sí |
| `exclusions` | `exclusions[]` | `PhysicalExclusion[]` | `exclusions[]` | Sí | Sí |
| `setup_id` | ruta/operación | `operation.setup_id` | `setup_id` | Sí | Sí |
| `operation_id` | ruta | `operation.id` | `operation_ids[]` | Sí | Sí |
| `placement_revision` | implícito desde proyecto | `setup.placement_revision` | `placement_revision` | Sí | Sí |

Validaciones cubiertas en backend el 2026-08-01:

- `tests.test_physical_integration` verifica recuento total, separación y recorrido serpentino determinista.
- `tests.test_api.ApiTest.test_plan_from_reference_uses_single_observation_and_persists_exact_parameters` verifica persistencia exacta y coherencia entre planeación y preview.
- `frontend/src/features/projects/ProjectWorkspace.test.tsx` verifica rehidratación de filas, columnas, límites, exclusiones y parámetros de sonda en la pestaña Mapa.
