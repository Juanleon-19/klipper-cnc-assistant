from __future__ import annotations

from pathlib import Path
from typing import Any

from klipper_cnc_assistant.application.errors import ApplicationError
from klipper_cnc_assistant.domain import OperacionPCB

from .job_service import (
    JOB_PLAN_SCHEMA,
    JobContext,
    JobService as BaseJobService,
    _iso_now,
    _safe_face,
    _tool_key,
)


class JobService(BaseJobService):
    """Hotfix wrapper that keeps plan rebuilds bounded and metadata-only."""

    def _build_plan(self, context: JobContext, generated_results: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
        project = self._load_project(context.project_id)
        setup = project.get_setup(context.setup_id)
        operations = sorted(
            [item for item in project.operations_for_setup(context.setup_id) if str(item.cara) == str(context.face)],
            key=lambda item: item.orden,
        )
        if not operations:
            raise ApplicationError("No existen operaciones activas para este montaje/cara.")
        active_map = self._load_active_map(context.project_id, operations[0].id)
        coverage_by_operation = self._coverage_by_operation(active_map, operations)
        generated_by_operation = self._latest_generated_by_operation(context.project_id, operations, active_map)
        if generated_results:
            generated_by_operation.update({key: value for key, value in generated_results.items() if "relative_path" in value})
        operation_rows: list[dict[str, Any]] = []
        previous_tool_key: str | None = None
        tool_change_count = 0
        distinct_tools: list[str] = []
        initial_reference_binding = self._initial_reference_binding(active_map, setup, operations)
        for index, operation in enumerate(operations):
            tool_key = _tool_key(operation)
            if tool_key not in distinct_tools:
                distinct_tools.append(tool_key)
            tool_changed = previous_tool_key is not None and previous_tool_key != tool_key
            if tool_changed:
                tool_change_count += 1
            binding = initial_reference_binding
            previous_tool_key = tool_key
            generated = generated_by_operation.get(operation.id)
            generated_metadata = generated.get("metadata") if isinstance(generated, dict) else None
            coverage = coverage_by_operation.get(operation.id)
            reference_status = self._reference_status(active_map, operation, binding)
            calibration = self._tool_installation_calibration(active_map, operation, binding)
            blocking_reasons: list[str] = []
            original_time_estimate: dict[str, Any] | None = None
            if operation.archivo_gcode is None:
                blocking_reasons.append("Falta G-code original.")
            if operation.analisis is None:
                blocking_reasons.append("Falta análisis G-code.")
            if active_map is None:
                blocking_reasons.append("Falta mapa físico activo.")
            if coverage is not None and not coverage["sufficient"]:
                first = coverage["issues"][0] if coverage["issues"] else None
                blocking_reasons.append(
                    "Mapa insuficiente."
                    + (
                        f" Primer punto fuera: línea/segmento {first['segment_index']}, X={first['x_mm']:.3f}, Y={first['y_mm']:.3f}, distancia={first['distance_mm']:.3f} mm."
                        if first is not None
                        else ""
                    )
                )
            operation_rows.append(
                {
                    "operation_id": operation.id,
                    "order": operation.orden,
                    "order_label": f"{index + 1:03d}",
                    "name": operation.nombre,
                    "type": str(operation.tipo),
                    "tool_id": operation.tool_id,
                    "tool_name": operation.herramienta or operation.tool_id or "sin herramienta",
                    "tool_key": tool_key,
                    "tool_changed": tool_changed,
                    "map_status": "LISTO" if active_map is not None else "PENDIENTE",
                    "coverage_status": "VALIDA" if coverage is None or coverage["sufficient"] else "FUERA_DE_DOMINIO",
                    "coverage_detail": None if coverage is None or coverage["sufficient"] else blocking_reasons[-1],
                    "reference_status": reference_status,
                    "tool_installation_calibration": calibration,
                    "generated_file": None if generated is None else generated["relative_path"],
                    "generated_file_name": None if generated is None else Path(str(generated["relative_path"])).name,
                    "generated_metadata_path": None if generated is None else generated.get("metadata_path"),
                    "time_estimate": None if generated is None else generated.get("time_estimate"),
                    "estimated_time_s": None if generated is None else (generated.get("time_estimate") or {}).get("estimated_time_s"),
                    "original_time_estimate": original_time_estimate,
                    "original_estimated_time_s": None if original_time_estimate is None else original_time_estimate.get("estimated_time_s"),
                    "source_file_hash": None if generated_metadata is None else generated_metadata.get("original_hash"),
                    "generated_file_hash": None if generated_metadata is None else generated_metadata.get("generated_hash"),
                    "compensation_mode": None if generated_metadata is None else generated_metadata.get("compensation_mode", "legacy"),
                    "algorithm_version": None if generated_metadata is None else generated_metadata.get("algorithm_version"),
                    "max_z_error_mm": operation.max_z_error_mm,
                    "compensation_status": "COMPENSADO" if generated is not None else "PENDIENTE",
                    "preflight_status": "PENDIENTE",
                    "execution_status": "PENDING",
                    "blocking": bool(blocking_reasons),
                    "blocking_reasons": blocking_reasons,
                    "coverage": coverage,
                    "original_gcode": operation.archivo_gcode,
                }
            )
        return {
            "schema_version": JOB_PLAN_SCHEMA,
            "plan_id": f"job-plan/{context.setup_id}/{_safe_face(context.face)}",
            "project_id": context.project_id,
            "setup_id": context.setup_id,
            "face": context.face,
            "placement_revision": setup.placement_revision,
            "active_map_id": None if active_map is None else active_map.get("map_id"),
            "active_map": active_map,
            "operations": operation_rows,
            "summary": {
                "operations_total": len(operation_rows),
                "operations_ready": sum(1 for item in operation_rows if not item["blocking"]),
                "generated_files": sum(1 for item in operation_rows if item.get("generated_file")),
                "tool_changes": tool_change_count,
                "distinct_tools": len(distinct_tools),
                "blocked_operations": sum(1 for item in operation_rows if item["blocking"]),
                "original_estimated_time_s": sum(float(item.get("original_estimated_time_s") or 0.0) for item in operation_rows),
                "estimated_time_s": sum(float(item.get("estimated_time_s") or 0.0) for item in operation_rows),
            },
            "manifest_path": self._existing_manifest_path(context),
            "created_at": _iso_now(),
            "updated_at": _iso_now(),
        }
