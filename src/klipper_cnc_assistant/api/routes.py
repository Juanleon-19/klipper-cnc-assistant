from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from starlette.datastructures import UploadFile

from klipper_cnc_assistant.application import ApplicationError
from klipper_cnc_assistant.application.physical_map_service import PhysicalExclusion, PhysicalMeshConfig
from klipper_cnc_assistant.heightmap import ExclusionZone, ProbeRegion

from .heightmap_schemas import (
    CompensationPreviewResponse,
    HeightMapConfigRequest,
    HeightMapImportRequest,
    HeightMapResponse,
    PhysicalMapPlanRequest,
    PhysicalMapPointUpdateRequest,
    PhysicalMapResponse,
    HeightMapSampleUpdateRequest,
    HeightMapSimulationRequest,
    HeightMapStatisticsResponse,
    height_map_to_response,
)
from .schemas import (
    GCodeUploadRequest,
    HealthResponse,
    MachineSessionResponse,
    OperationAnalysisResponse,
    OperationCreateRequest,
    OperationMoveRequest,
    OperationUpdateRequest,
    ProjectPermanentDeleteRequest,
    OperationResponse,
    ProjectCreateRequest,
    ProjectResponse,
    ProjectUpdateRequest,
    ReferencePointResponse,
    ReferenceSessionResponse,
    ReferenceStepResponse,
    ReferenceWorkOriginRequest,
    ReferenceZRequest,
    SetupCreateRequest,
    SetupResetRequest,
    SetupResponse,
    SetupUpdateRequest,
    SystemInfoResponse,
    analysis_to_response,
    machine_session_to_response,
    operation_to_response,
    project_to_response,
    setup_to_response,
)


async def _parse_gcode_upload_request(request: Request) -> tuple[str, str | bytes, bool]:
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        payload = GCodeUploadRequest.model_validate(await request.json())
        return payload.nombre_archivo, payload.contenido, False

    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        uploaded = form.get("archivo")
        if not isinstance(uploaded, UploadFile):
            raise ApplicationError("Debe enviar el archivo G-code en el campo 'archivo'.")
        try:
            filename = uploaded.filename or ""
            content = await uploaded.read()
        finally:
            await uploaded.close()
        return filename, content, True

    raise ApplicationError("Tipo de contenido no soportado. Use JSON o multipart/form-data.")


def _probe_region_from_request(payload) -> ProbeRegion:
    return ProbeRegion(
        min_x_mm=payload.probe_region.min_x_mm,
        min_y_mm=payload.probe_region.min_y_mm,
        max_x_mm=payload.probe_region.max_x_mm,
        max_y_mm=payload.probe_region.max_y_mm,
    )


def _exclusion_zones_from_request(payload) -> tuple[ExclusionZone, ...]:
    return tuple(
        ExclusionZone(
            id=item.id,
            nombre=item.nombre,
            min_x_mm=item.min_x_mm,
            min_y_mm=item.min_y_mm,
            max_x_mm=item.max_x_mm,
            max_y_mm=item.max_y_mm,
        )
        for item in payload.exclusion_zones
    )


def _reference_session_to_response(payload: dict[str, object]) -> ReferenceSessionResponse:
    return ReferenceSessionResponse(
        estado=str(payload["estado"]),
        machine_reference=payload["machine_reference"],
        origen_maquina=ReferencePointResponse(**payload["origen_maquina"]),
        origen_material=ReferencePointResponse(**payload["origen_material"]),
        origen_gcode=ReferencePointResponse(**payload["origen_gcode"]),
        origen_trabajo=payload.get("origen_trabajo"),
        referencia_z=payload.get("referencia_z"),
        pasos=[ReferenceStepResponse(**step) for step in payload["pasos"]],
        compensacion_previsualizada_en=payload.get("compensacion_previsualizada_en"),
        analysis_stale=bool(payload.get("analysis_stale", False)),
        lista_para_compensacion=bool(payload.get("lista_para_compensacion", False)),
        bloqueos_compensacion=list(payload.get("bloqueos_compensacion", [])),
        motivo_invalidacion=payload.get("motivo_invalidacion"),
    )


def _reject_active_motion(request: Request) -> None:
    runtime = getattr(request.app.state, "machine_runtime", None)
    if runtime is None:
        return
    snapshot = runtime.snapshot()
    state = str(snapshot.get("state") or "")
    if state in {"RUNNING", "MOVING", "PROBING", "REFERENCE_ARMED", "MESH_PROBING"}:
        raise ApplicationError("No se puede reiniciar o eliminar mientras existe movimiento físico activo. Pause o cancele de forma segura primero.")


def build_router() -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/health", response_model=HealthResponse)
    def health(request: Request) -> HealthResponse:
        service = request.app.state.system_status_service
        return HealthResponse(**service.get_health())

    @router.get("/system/info", response_model=SystemInfoResponse)
    def system_info(request: Request) -> SystemInfoResponse:
        service = request.app.state.system_status_service
        return SystemInfoResponse(**service.get_system_info())

    @router.get("/projects", response_model=list[ProjectResponse])
    def list_projects(request: Request) -> list[ProjectResponse]:
        service = request.app.state.project_service
        return [project_to_response(project) for project in service.list_projects()]

    @router.post("/projects", response_model=ProjectResponse, status_code=201)
    def create_project(payload: ProjectCreateRequest, request: Request) -> ProjectResponse:
        service = request.app.state.project_service
        project = service.create_project(
            nombre=payload.nombre,
            ancho_mm=payload.material.ancho_mm,
            alto_mm=payload.material.alto_mm,
            espesor_mm=payload.material.espesor_mm,
            doble_cara=payload.doble_cara,
            eje_volteo=payload.eje_volteo,
            agujeros_alineacion=[item.model_dump() for item in payload.agujeros_alineacion],
        )
        return project_to_response(project)

    @router.put("/projects/{project_id}", response_model=ProjectResponse)
    def update_project(project_id: str, payload: ProjectUpdateRequest, request: Request) -> ProjectResponse:
        service = request.app.state.project_service
        project = service.update_project(
            project_id=project_id,
            nombre=payload.nombre,
            ancho_mm=payload.material.ancho_mm,
            alto_mm=payload.material.alto_mm,
            espesor_mm=payload.material.espesor_mm,
            doble_cara=payload.doble_cara,
            eje_volteo=payload.eje_volteo,
            agujeros_alineacion=[item.model_dump() for item in payload.agujeros_alineacion],
        )
        return project_to_response(project)

    @router.get("/projects/{project_id}", response_model=ProjectResponse)
    def get_project(project_id: str, request: Request) -> ProjectResponse:
        service = request.app.state.project_service
        project = service.get_project(project_id)
        return project_to_response(project)

    @router.post("/projects/{project_id}/continue", response_model=dict[str, str | None])
    def continue_project(project_id: str, request: Request) -> dict[str, str | None]:
        return request.app.state.project_service.continue_project_step(project_id)

    @router.post("/projects/{project_id}/archive", response_model=ProjectResponse)
    def archive_project(project_id: str, request: Request) -> ProjectResponse:
        return project_to_response(request.app.state.project_service.archive_project(project_id))

    @router.post("/projects/{project_id}/trash", response_model=ProjectResponse)
    def trash_project(project_id: str, request: Request) -> ProjectResponse:
        _reject_active_motion(request)
        return project_to_response(request.app.state.project_service.trash_project(project_id))

    @router.post("/projects/{project_id}/restore", response_model=ProjectResponse)
    def restore_project(project_id: str, request: Request) -> ProjectResponse:
        return project_to_response(request.app.state.project_service.restore_project(project_id))

    @router.delete("/projects/{project_id}/permanent", response_model=dict[str, str])
    def permanently_delete_project(project_id: str, payload: ProjectPermanentDeleteRequest, request: Request) -> dict[str, str]:
        _reject_active_motion(request)
        request.app.state.project_service.permanently_delete_project(project_id, confirm_name=payload.confirm_name)
        return {"detalle": "Proyecto eliminado permanentemente."}

    @router.post("/projects/{project_id}/setups", response_model=SetupResponse, status_code=201)
    def add_setup(project_id: str, payload: SetupCreateRequest, request: Request) -> SetupResponse:
        service = request.app.state.project_service
        return setup_to_response(
            service.add_setup(project_id=project_id, nombre=payload.nombre)
        )

    @router.patch("/projects/{project_id}/setups/{setup_id}", response_model=SetupResponse)
    def update_setup(project_id: str, setup_id: str, payload: SetupUpdateRequest, request: Request) -> SetupResponse:
        service = request.app.state.project_service
        return setup_to_response(
            service.update_setup(
                project_id=project_id,
                setup_id=setup_id,
                nombre=payload.nombre,
            )
        )

    @router.post("/projects/{project_id}/setups/{setup_id}/reset-reference", response_model=dict[str, object])
    def reset_setup_reference(project_id: str, setup_id: str, payload: SetupResetRequest, request: Request) -> dict[str, object]:
        _reject_active_motion(request)
        return request.app.state.physical_map_service.reset_reference(project_id=project_id, setup_id=setup_id, reason=payload.motivo, user_session=payload.session)

    @router.post("/projects/{project_id}/setups/{setup_id}/reset-map", response_model=dict[str, object])
    def reset_setup_map(project_id: str, setup_id: str, payload: SetupResetRequest, request: Request) -> dict[str, object]:
        _reject_active_motion(request)
        return request.app.state.physical_map_service.reset_map(project_id=project_id, setup_id=setup_id, reason=payload.motivo, user_session=payload.session)

    @router.post("/projects/{project_id}/setups/{setup_id}/reset-preparation", response_model=dict[str, object])
    def reset_setup_preparation(project_id: str, setup_id: str, payload: SetupResetRequest, request: Request) -> dict[str, object]:
        _reject_active_motion(request)
        result = request.app.state.physical_map_service.reset_preparation(project_id=project_id, setup_id=setup_id, reason=payload.motivo, user_session=payload.session)
        machine_session_service = getattr(request.app.state, "machine_session_service", None)
        if machine_session_service is not None:
            result["machine_session"] = machine_session_to_response(machine_session_service.reset_session()).model_dump()
        runtime = getattr(request.app.state, "machine_runtime", None)
        if runtime is not None:
            result["runtime"] = runtime.reset_physical_session()
        return result

    @router.post("/projects/{project_id}/operations", response_model=OperationResponse, status_code=201)
    def add_operation(project_id: str, payload: OperationCreateRequest, request: Request) -> OperationResponse:
        service = request.app.state.project_service
        operation = service.add_operation(
            project_id=project_id,
            nombre=payload.nombre,
            tipo=payload.tipo,
            cara=payload.cara,
            orden=payload.orden,
            setup_id=payload.setup_id,
            tool_id=payload.tool_id,
            herramienta=payload.herramienta,
        )
        return operation_to_response(operation)

    @router.patch("/projects/{project_id}/operations/{operation_id}", response_model=OperationResponse)
    def update_operation(project_id: str, operation_id: str, payload: OperationUpdateRequest, request: Request) -> OperationResponse:
        service = request.app.state.project_service
        operation = service.update_operation(
            project_id=project_id,
            operation_id=operation_id,
            nombre=payload.nombre,
            tool_id=payload.tool_id,
            herramienta=payload.herramienta,
        )
        return operation_to_response(operation)

    @router.post("/projects/{project_id}/operations/{operation_id}/duplicate", response_model=OperationResponse, status_code=201)
    def duplicate_operation(project_id: str, operation_id: str, request: Request) -> OperationResponse:
        service = request.app.state.project_service
        return operation_to_response(
            service.duplicate_operation(project_id=project_id, operation_id=operation_id)
        )

    @router.post("/projects/{project_id}/operations/{operation_id}/move", response_model=OperationResponse)
    def move_operation(project_id: str, operation_id: str, payload: OperationMoveRequest, request: Request) -> OperationResponse:
        service = request.app.state.project_service
        return operation_to_response(
            service.move_operation(
                project_id=project_id,
                operation_id=operation_id,
                direction=payload.direccion,
            )
        )

    @router.delete("/projects/{project_id}/operations/{operation_id}", response_model=dict[str, str])
    def delete_operation(project_id: str, operation_id: str, request: Request) -> dict[str, str]:
        service = request.app.state.project_service
        service.delete_operation(project_id, operation_id)
        return {"detalle": "Operacion eliminada."}

    @router.delete("/projects/{project_id}/operations/{operation_id}/gcode", response_model=OperationResponse)
    def remove_gcode(project_id: str, operation_id: str, request: Request) -> OperationResponse:
        service = request.app.state.project_service
        operation = service.remove_operation_gcode(project_id=project_id, operation_id=operation_id)
        return operation_to_response(operation)

    @router.post("/projects/{project_id}/operations/{operation_id}/gcode", response_model=OperationResponse)
    async def upload_gcode(project_id: str, operation_id: str, request: Request) -> OperationResponse:
        service = request.app.state.project_service
        filename, payload, is_binary = await _parse_gcode_upload_request(request)
        if is_binary:
            operation = service.upload_operation_gcode_bytes(
                project_id=project_id,
                operation_id=operation_id,
                filename=filename,
                content_bytes=payload,
            )
        else:
            operation = service.upload_operation_gcode(
                project_id=project_id,
                operation_id=operation_id,
                filename=filename,
                content=payload,
            )
        return operation_to_response(operation)

    @router.post("/projects/{project_id}/operations/{operation_id}/analyze", response_model=OperationAnalysisResponse)
    def analyze_operation(project_id: str, operation_id: str, request: Request) -> OperationAnalysisResponse:
        service = request.app.state.project_service
        operation = service.analyze_operation(project_id, operation_id)
        return analysis_to_response(operation.analisis)

    @router.get("/projects/{project_id}/operations/{operation_id}/analysis", response_model=OperationAnalysisResponse)
    def get_operation_analysis(project_id: str, operation_id: str, request: Request) -> OperationAnalysisResponse:
        service = request.app.state.project_service
        analysis = service.get_operation_analysis(project_id, operation_id)
        return analysis_to_response(analysis)

    @router.get("/projects/{project_id}/operations/{operation_id}/reference-session", response_model=ReferenceSessionResponse)
    def get_reference_session(project_id: str, operation_id: str, request: Request) -> ReferenceSessionResponse:
        service = request.app.state.reference_session_service
        return _reference_session_to_response(service.get_session(project_id, operation_id))

    @router.post("/projects/{project_id}/operations/{operation_id}/reference-session/machine-reference", response_model=ReferenceSessionResponse)
    def confirm_machine_reference(project_id: str, operation_id: str, request: Request) -> ReferenceSessionResponse:
        service = request.app.state.reference_session_service
        return _reference_session_to_response(service.confirm_machine_reference(project_id, operation_id))

    @router.post("/projects/{project_id}/operations/{operation_id}/reference-session/work-origin", response_model=ReferenceSessionResponse)
    def confirm_work_origin(project_id: str, operation_id: str, payload: ReferenceWorkOriginRequest, request: Request) -> ReferenceSessionResponse:
        service = request.app.state.reference_session_service
        if payload.x_mm is None or payload.y_mm is None:
            raise ApplicationError("Debe indicar X e Y para confirmar el origen de trabajo en simulacion.")
        return _reference_session_to_response(
            service.confirm_work_origin(project_id, operation_id, x_mm=payload.x_mm, y_mm=payload.y_mm)
        )

    @router.post("/projects/{project_id}/operations/{operation_id}/reference-session/z-reference", response_model=ReferenceSessionResponse)
    def confirm_z_reference(project_id: str, operation_id: str, payload: ReferenceZRequest, request: Request) -> ReferenceSessionResponse:
        service = request.app.state.reference_session_service
        if payload.x_mm is None or payload.y_mm is None or payload.z_mm is None:
            raise ApplicationError("Debe indicar X, Y y Z para confirmar la referencia Z en simulacion.")
        return _reference_session_to_response(
            service.confirm_z_reference(project_id, operation_id, x_mm=payload.x_mm, y_mm=payload.y_mm, z_mm=payload.z_mm)
        )

    @router.post("/projects/{project_id}/operations/{operation_id}/reference-session/physical-work-origin", response_model=ReferenceSessionResponse)
    def capture_physical_work_origin(project_id: str, operation_id: str, request: Request) -> ReferenceSessionResponse:
        reference_service = request.app.state.reference_session_service
        runtime = request.app.state.machine_runtime
        position = runtime.capture_current_position()
        snapshot = runtime.snapshot()
        return _reference_session_to_response(reference_service.capture_physical_work_origin(project_id, operation_id, position=position, machine_label=str(snapshot["moonraker"].get("url") or "physical"), homed_axes=snapshot["klipper"].get("homed_axes"), session_id=snapshot.get("started_at")))

    @router.post("/projects/{project_id}/operations/{operation_id}/reference-session/physical-z-reference", response_model=ReferenceSessionResponse)
    def capture_physical_z_reference(project_id: str, operation_id: str, request: Request) -> ReferenceSessionResponse:
        reference_service = request.app.state.reference_session_service
        runtime = request.app.state.machine_runtime
        position = runtime.capture_current_position()
        snapshot = runtime.snapshot()
        return _reference_session_to_response(reference_service.capture_physical_z_reference(project_id, operation_id, position=position, machine_label=str(snapshot["moonraker"].get("url") or "physical"), homed_axes=snapshot["klipper"].get("homed_axes"), session_id=snapshot.get("started_at")))

    @router.post("/projects/{project_id}/operations/{operation_id}/reference-session/physical-z-reference-from-probe", response_model=ReferenceSessionResponse)
    def capture_physical_z_reference_from_probe(project_id: str, operation_id: str, request: Request) -> ReferenceSessionResponse:
        reference_service = request.app.state.reference_session_service
        runtime = request.app.state.machine_runtime
        position = runtime.last_probe_position()
        snapshot = runtime.snapshot()
        return _reference_session_to_response(reference_service.capture_physical_z_reference(project_id, operation_id, position=position, machine_label=str(snapshot["moonraker"].get("url") or "physical"), homed_axes=snapshot["klipper"].get("homed_axes"), session_id=snapshot.get("started_at")))

    @router.post("/projects/{project_id}/operations/{operation_id}/physical-map/suggest", response_model=dict[str, object])
    def suggest_physical_map(project_id: str, operation_id: str, payload: PhysicalMapPlanRequest, request: Request) -> dict[str, object]:
        service = request.app.state.physical_map_service
        return service.suggest_mesh_config(
            project_id=project_id,
            operation_id=operation_id,
            config=PhysicalMeshConfig(
                grid_mode="suggested",
                rows=payload.rows,
                columns=payload.columns,
                edge_margin_left_mm=payload.edge_margin_left_mm,
                edge_margin_right_mm=payload.edge_margin_right_mm,
                edge_margin_bottom_mm=payload.edge_margin_bottom_mm,
                edge_margin_top_mm=payload.edge_margin_top_mm,
                exclusions=tuple(PhysicalExclusion(**exclusion.model_dump()) for exclusion in payload.exclusions),
                max_spacing_mm=payload.max_spacing_mm,
                margin_mm=payload.margin_mm,
                safe_z_mm=payload.safe_z_mm,
                probe_step_mm=payload.probe_step_mm,
                probe_feed_mm_min=payload.probe_feed_mm_min,
                retract_mm=payload.retract_mm,
            ),
        )


    @router.post("/projects/{project_id}/operations/{operation_id}/physical-map/preview", response_model=PhysicalMapResponse)
    def preview_physical_map(project_id: str, operation_id: str, payload: PhysicalMapPlanRequest, request: Request) -> PhysicalMapResponse:
        service = request.app.state.physical_map_service
        plan = service.preview_mesh(
            project_id=project_id,
            operation_id=operation_id,
            config=PhysicalMeshConfig(
                grid_mode=payload.grid_mode,
                rows=payload.rows,
                columns=payload.columns,
                edge_margin_left_mm=payload.edge_margin_left_mm,
                edge_margin_right_mm=payload.edge_margin_right_mm,
                edge_margin_bottom_mm=payload.edge_margin_bottom_mm,
                edge_margin_top_mm=payload.edge_margin_top_mm,
                exclusions=tuple(PhysicalExclusion(**exclusion.model_dump()) for exclusion in payload.exclusions),
                max_spacing_mm=payload.max_spacing_mm,
                margin_mm=payload.margin_mm,
                safe_z_mm=payload.safe_z_mm,
                probe_step_mm=payload.probe_step_mm,
                probe_feed_mm_min=payload.probe_feed_mm_min,
                retract_mm=payload.retract_mm,
            ),
        )
        return PhysicalMapResponse(payload=plan)

    @router.post("/projects/{project_id}/operations/{operation_id}/physical-map/plan-from-reference", response_model=PhysicalMapResponse)
    def plan_physical_map_from_reference(project_id: str, operation_id: str, payload: PhysicalMapPlanRequest, request: Request) -> PhysicalMapResponse:
        runtime = request.app.state.machine_runtime
        reference_service = request.app.state.reference_session_service
        physical_map_service = request.app.state.physical_map_service
        probe = runtime.last_probe_position()
        snapshot = runtime.snapshot()
        machine_label = str(snapshot["moonraker"].get("url") or "physical")
        homed_axes = snapshot["klipper"].get("homed_axes")
        session_id = snapshot.get("started_at")
        reference_service.capture_physical_work_origin(project_id, operation_id, position=probe, machine_label=machine_label, homed_axes=homed_axes, session_id=session_id)
        reference_service.capture_physical_z_reference(project_id, operation_id, position=probe, machine_label=machine_label, homed_axes=homed_axes, session_id=session_id)
        plan = physical_map_service.capture_reference_and_plan(
            project_id=project_id,
            operation_id=operation_id,
            machine_origin_x=probe["x_mm"],
            machine_origin_y=probe["y_mm"],
            reference_z=probe["z_mm"],
            machine_position=probe,
            homed_axes=homed_axes,
            machine_label=machine_label,
            session_id=session_id,
            config=PhysicalMeshConfig(
                grid_mode=payload.grid_mode,
                rows=payload.rows,
                columns=payload.columns,
                edge_margin_left_mm=payload.edge_margin_left_mm,
                edge_margin_right_mm=payload.edge_margin_right_mm,
                edge_margin_bottom_mm=payload.edge_margin_bottom_mm,
                edge_margin_top_mm=payload.edge_margin_top_mm,
                exclusions=tuple(PhysicalExclusion(**exclusion.model_dump()) for exclusion in payload.exclusions),
                max_spacing_mm=payload.max_spacing_mm,
                margin_mm=payload.margin_mm,
                safe_z_mm=payload.safe_z_mm,
                probe_step_mm=payload.probe_step_mm,
                probe_feed_mm_min=payload.probe_feed_mm_min,
                retract_mm=payload.retract_mm,
            ),
        )
        return PhysicalMapResponse(payload=plan)

    @router.get("/projects/{project_id}/operations/{operation_id}/physical-map", response_model=PhysicalMapResponse)
    def get_physical_map(project_id: str, operation_id: str, request: Request) -> PhysicalMapResponse:
        service = request.app.state.physical_map_service
        return PhysicalMapResponse(payload=service.get_active(project_id, operation_id))


    @router.get("/projects/{project_id}/operations/{operation_id}/physical-map/history", response_model=list[dict[str, object]])
    def get_physical_map_history(project_id: str, operation_id: str, request: Request) -> list[dict[str, object]]:
        return request.app.state.physical_map_service.history(project_id=project_id, operation_id=operation_id)

    @router.post("/projects/{project_id}/physical-maps/{map_id:path}/repeat", response_model=PhysicalMapResponse)
    def repeat_physical_map(project_id: str, map_id: str, request: Request) -> PhysicalMapResponse:
        _reject_active_motion(request)
        payload = request.app.state.physical_map_service.repeat_measurement(project_id=project_id, map_id=map_id)
        return PhysicalMapResponse(payload=payload)

    @router.post("/projects/{project_id}/physical-maps/{map_id:path}/execute-next", response_model=PhysicalMapResponse)
    def execute_next_physical_map_point(project_id: str, map_id: str, request: Request) -> PhysicalMapResponse:
        service = request.app.state.physical_map_service
        runtime = request.app.state.machine_runtime
        payload = service.get_by_id(project_id, map_id)
        if payload.get("status") in {"CANCELLED", "MESH_COMPLETE"}:
            raise ApplicationError("La malla no está en un estado ejecutable.")
        point = service.next_pending_point(project_id, map_id)
        result = runtime.probe_mesh_point(point, probe_config=payload.get("probe_config"))
        updated = service.record_point(
            project_id=project_id,
            map_id=map_id,
            point_index=int(point["index"]),
            z_measured=float(result["z_measured"]),
            status="MEASURED",
            duration_s=float(result["duration_s"]),
            error=None,
        )
        return PhysicalMapResponse(payload=updated)

    @router.post("/projects/{project_id}/physical-maps/{map_id:path}/execute-all", response_model=PhysicalMapResponse)
    def execute_all_physical_map_points(project_id: str, map_id: str, request: Request) -> PhysicalMapResponse:
        updated = request.app.state.mesh_execution_service.start_all(
            project_id=project_id,
            map_id=map_id,
            runtime=request.app.state.machine_runtime,
        )
        return PhysicalMapResponse(payload=updated)

    @router.get("/projects/{project_id}/physical-maps/{map_id:path}/log", response_model=dict[str, object])
    def get_physical_map_log(project_id: str, map_id: str, request: Request) -> dict[str, object]:
        return request.app.state.physical_map_service.execution_log(project_id=project_id, map_id=map_id)

    @router.post("/projects/{project_id}/physical-maps/{map_id:path}/points/{point_index}", response_model=PhysicalMapResponse)
    def update_physical_map_point(project_id: str, map_id: str, point_index: int, payload: PhysicalMapPointUpdateRequest, request: Request) -> PhysicalMapResponse:
        service = request.app.state.physical_map_service
        updated = service.record_point(
            project_id=project_id,
            map_id=map_id,
            point_index=point_index,
            z_measured=payload.z_measured,
            status=payload.status,
            attempts=payload.attempts,
            duration_s=payload.duration_s,
            error=payload.error,
        )
        return PhysicalMapResponse(payload=updated)

    @router.post("/projects/{project_id}/physical-maps/{map_id:path}/pause", response_model=PhysicalMapResponse)
    def pause_physical_map(project_id: str, map_id: str, request: Request) -> PhysicalMapResponse:
        return PhysicalMapResponse(payload=request.app.state.physical_map_service.mark_status(project_id=project_id, map_id=map_id, status="MESH_PAUSED"))

    @router.post("/projects/{project_id}/physical-maps/{map_id:path}/resume", response_model=PhysicalMapResponse)
    def resume_physical_map(project_id: str, map_id: str, request: Request) -> PhysicalMapResponse:
        updated = request.app.state.mesh_execution_service.resume(
            project_id=project_id,
            map_id=map_id,
            runtime=request.app.state.machine_runtime,
        )
        return PhysicalMapResponse(payload=updated)

    @router.post("/projects/{project_id}/physical-maps/{map_id:path}/cancel", response_model=PhysicalMapResponse)
    def cancel_physical_map(project_id: str, map_id: str, request: Request) -> PhysicalMapResponse:
        return PhysicalMapResponse(payload=request.app.state.physical_map_service.mark_status(project_id=project_id, map_id=map_id, status="CANCELLED"))

    @router.get("/projects/{project_id}/operations/{operation_id}/physical-map/height-map", response_model=HeightMapResponse)
    def get_physical_map_as_height_map(project_id: str, operation_id: str, request: Request) -> HeightMapResponse:
        physical_service = request.app.state.physical_map_service
        height_service = request.app.state.height_map_service
        payload = physical_service.get_active(project_id, operation_id)
        height_map = height_service._deserialize_map(payload["height_map"])
        return height_map_to_response(height_map, height_service.build_surfaces(height_map))

    @router.put("/projects/{project_id}/operations/{operation_id}/height-map/config", response_model=HeightMapResponse)
    def configure_height_map(project_id: str, operation_id: str, payload: HeightMapConfigRequest, request: Request) -> HeightMapResponse:
        service = request.app.state.height_map_service
        height_map = service.configure_map(
            project_id=project_id,
            operation_id=operation_id,
            filas=payload.filas,
            columnas=payload.columnas,
            probe_region=_probe_region_from_request(payload),
            exclusion_zones=_exclusion_zones_from_request(payload),
        )
        return height_map_to_response(height_map, service.build_surfaces(height_map))

    @router.post("/projects/{project_id}/operations/{operation_id}/height-map/simulate", response_model=HeightMapResponse)
    def simulate_height_map(project_id: str, operation_id: str, payload: HeightMapSimulationRequest, request: Request) -> HeightMapResponse:
        service = request.app.state.height_map_service
        height_map = service.generate_simulated_map(
            project_id=project_id,
            operation_id=operation_id,
            filas=payload.filas,
            columnas=payload.columnas,
            superficie_simulada=payload.superficie_simulada,
            repeticion_simulacion=payload.repeticion_simulacion,
            probe_region=_probe_region_from_request(payload),
            exclusion_zones=_exclusion_zones_from_request(payload),
        )
        return height_map_to_response(height_map, service.build_surfaces(height_map))

    @router.post("/projects/{project_id}/operations/{operation_id}/height-map/import/json", response_model=HeightMapResponse)
    def import_height_map_json(project_id: str, operation_id: str, payload: HeightMapImportRequest, request: Request) -> HeightMapResponse:
        service = request.app.state.height_map_service
        height_map = service.import_json_map(project_id=project_id, operation_id=operation_id, content=payload.contenido)
        return height_map_to_response(height_map, service.build_surfaces(height_map))

    @router.post("/projects/{project_id}/operations/{operation_id}/height-map/import/csv", response_model=HeightMapResponse)
    def import_height_map_csv(project_id: str, operation_id: str, payload: HeightMapImportRequest, request: Request) -> HeightMapResponse:
        service = request.app.state.height_map_service
        height_map = service.import_csv_map(project_id=project_id, operation_id=operation_id, content=payload.contenido)
        return height_map_to_response(height_map, service.build_surfaces(height_map))

    @router.get("/projects/{project_id}/operations/{operation_id}/height-map", response_model=HeightMapResponse)
    def get_height_map(project_id: str, operation_id: str, request: Request) -> HeightMapResponse:
        service = request.app.state.height_map_service
        height_map = service.get_map(project_id, operation_id)
        return height_map_to_response(height_map, service.build_surfaces(height_map))

    @router.get("/projects/{project_id}/operations/{operation_id}/height-map/statistics", response_model=HeightMapStatisticsResponse)
    def get_height_map_statistics(project_id: str, operation_id: str, request: Request) -> HeightMapStatisticsResponse:
        service = request.app.state.height_map_service
        statistics = service.get_statistics(project_id, operation_id)
        return HeightMapStatisticsResponse(
            cantidad_puntos=statistics.cantidad_puntos,
            cantidad_puntos_incluidos=statistics.cantidad_puntos_incluidos,
            cantidad_puntos_faltantes=statistics.cantidad_puntos_faltantes,
            cantidad_puntos_atipicos=statistics.cantidad_puntos_atipicos,
            altura_min_mm=statistics.altura_min_mm,
            altura_max_mm=statistics.altura_max_mm,
            rango_alturas_mm=statistics.rango_alturas_mm,
            valor_referencia_mm=statistics.valor_referencia_mm,
            desviacion_rms_respecto_plano_mm=statistics.desviacion_rms_respecto_plano_mm,
            residuo_maximo_mm=statistics.residuo_maximo_mm,
            ancho_cubierto_mm=statistics.ancho_cubierto_mm,
            alto_cubierto_mm=statistics.alto_cubierto_mm,
        )

    @router.patch("/projects/{project_id}/operations/{operation_id}/height-map/samples/{sample_id}", response_model=HeightMapResponse)
    def update_height_map_sample(project_id: str, operation_id: str, sample_id: str, payload: HeightMapSampleUpdateRequest, request: Request) -> HeightMapResponse:
        service = request.app.state.height_map_service
        update_kwargs = {}
        if "z_mm" in payload.model_fields_set:
            update_kwargs["z_mm"] = payload.z_mm
        if "incluida" in payload.model_fields_set:
            update_kwargs["incluida"] = payload.incluida
        if "observacion" in payload.model_fields_set:
            update_kwargs["observacion"] = payload.observacion
        height_map = service.update_sample(project_id=project_id, operation_id=operation_id, sample_id=sample_id, **update_kwargs)
        return height_map_to_response(height_map, service.build_surfaces(height_map))

    @router.post("/projects/{project_id}/operations/{operation_id}/height-map/recalculate", response_model=HeightMapResponse)
    def recalculate_height_map(project_id: str, operation_id: str, request: Request) -> HeightMapResponse:
        service = request.app.state.height_map_service
        height_map = service.recalculate_map(project_id, operation_id)
        return height_map_to_response(height_map, service.build_surfaces(height_map))

    @router.post("/projects/{project_id}/operations/{operation_id}/height-map/validate", response_model=ReferenceSessionResponse)
    def validate_height_map(project_id: str, operation_id: str, request: Request) -> ReferenceSessionResponse:
        service = request.app.state.reference_session_service
        return _reference_session_to_response(service.mark_map_validated(project_id, operation_id))

    @router.post("/projects/{project_id}/operations/{operation_id}/compensation-preview", response_model=dict[str, object])
    def compensation_preview(project_id: str, operation_id: str, request: Request) -> dict[str, object]:
        service = request.app.state.reference_session_service
        result = service.build_compensation_preview(project_id, operation_id)
        preview = CompensationPreviewResponse(**result["preview"])
        session = _reference_session_to_response(result["session"])
        return {"session": session.model_dump(), "preview": preview.model_dump()}

    @router.post("/projects/{project_id}/operations/{operation_id}/compensated-gcode/generate", response_model=dict[str, object])
    def generate_compensated_gcode(project_id: str, operation_id: str, request: Request) -> dict[str, object]:
        return request.app.state.compensated_gcode_service.generate(project_id, operation_id)

    @router.get("/projects/{project_id}/generated/{file_path:path}")
    def download_generated_file(project_id: str, file_path: str, request: Request) -> FileResponse:
        target = request.app.state.compensated_gcode_service.resolve_generated_file(project_id, file_path)
        return FileResponse(target, media_type="text/plain", filename=target.name)


    @router.post("/projects/{project_id}/operations/{operation_id}/execution/preflight", response_model=dict[str, object])
    def execution_preflight(project_id: str, operation_id: str, request: Request) -> dict[str, object]:
        project = request.app.state.project_service.get_project(project_id)
        operation = project.get_operation(operation_id)
        runtime = request.app.state.machine_runtime.snapshot()
        physical_service = request.app.state.physical_map_service
        generated_dir = request.app.state.physical_map_service.repository.project_dir(project_id) / "generated" / "compensated"
        generated_files = sorted(generated_dir.glob(f"{operation_id}_*_compensated.gcode"), key=lambda item: item.stat().st_mtime, reverse=True) if generated_dir.exists() else []
        checks: list[dict[str, object]] = []
        def add(name: str, ok: bool, detail: str) -> None:
            checks.append({"name": name, "ok": ok, "detail": detail})
        mode_ok = runtime.get("mode") == "PHYSICAL"
        add("modo_fisico", mode_ok, "MACHINE_MODE=physical" if mode_ok else "El servicio no está en modo físico.")
        add("runtime_conectado", bool(runtime.get("moonraker", {}).get("http_connected")), "Moonraker HTTP conectado." if runtime.get("moonraker", {}).get("http_connected") else "Conecte MachineRuntime.")
        add("klipper_ready", bool(runtime.get("klipper", {}).get("ready")), "Klipper ready." if runtime.get("klipper", {}).get("ready") else "Klipper no está ready.")
        homed_axes = str(runtime.get("klipper", {}).get("homed_axes") or "")
        add("homing", set("xyz").issubset(set(homed_axes)), f"homed_axes={homed_axes}" if homed_axes else "Falta homing XYZ.")
        try:
            physical_map = physical_service.get_active(project_id, operation_id)
            validation = physical_map.get("validation") or {}
            map_ok = bool(physical_map.get("source") == "MEASURED" and (physical_map.get("status") in {"MESH_COMPLETE", "MAP_READY"} or physical_map.get("map_ready_state") == "MAP_READY"))
            validation_ok = bool(validation.get("status") == "VALID" and validation.get("sufficient") is True)
            tool_references = physical_map.get("tool_references") or {}
            requested_key = operation.tool_id or operation.herramienta or "sin-herramienta"
            requested_reference = tool_references.get(requested_key) if isinstance(tool_references, dict) else None
            reference_ok = isinstance(requested_reference, dict) and bool(requested_reference.get("valid"))
            if not reference_ok:
                for reference in tool_references.values():
                    if not isinstance(reference, dict) or not reference.get("valid"):
                        continue
                    if operation.tool_id and reference.get("tool_id") == operation.tool_id:
                        reference_ok = True
                        break
                    if operation.herramienta and reference.get("tool_name") == operation.herramienta:
                        reference_ok = True
                        break
            validation_detail = "Cobertura validada."
            if validation.get("status") == "INVALID":
                issues = validation.get("issues") or []
                if isinstance(issues, list) and issues and isinstance(issues[0], dict):
                    first = issues[0]
                    validation_detail = (
                        "Mapa insuficiente para la trayectoria. "
                        f"Primer punto fuera: línea/segmento {first.get('segment_index', '-')}, "
                        f"X={float(first.get('x_mm', 0.0)):.3f}, "
                        f"Y={float(first.get('y_mm', 0.0)):.3f}, "
                        f"distancia={float(first.get('distance_mm', 0.0)):.3f} mm."
                    )
                else:
                    validation_detail = "La cobertura del mapa físico no cubre todas las trayectorias."
            add("mapa_medido", map_ok, str(physical_map.get("status") or physical_map.get("map_ready_state") or "sin mapa"))
            add("cobertura_mapa", validation_ok, validation_detail)
            add("referencia_herramienta", reference_ok, "Referencia Z de herramienta disponible." if reference_ok else f"Falta referencia Z para la herramienta {requested_key}.")
        except Exception as error:
            add("mapa_medido", False, str(error))
            add("referencia_herramienta", False, "No se pudo verificar referencia sin mapa medido.")
        add("archivo_compensado", bool(generated_files), generated_files[0].name if generated_files else "Genere G-code compensado antes de ejecutar.")
        ready = all(item["ok"] for item in checks)
        return {"state": "READY_TO_EXECUTE" if ready else "PREFLIGHT", "ready": ready, "checks": checks, "generated_file": generated_files[0].as_posix() if generated_files else None}

    @router.post("/projects/{project_id}/operations/{operation_id}/execution/{action}", response_model=dict[str, object])
    def execution_action(project_id: str, operation_id: str, action: str, request: Request) -> dict[str, object]:
        allowed = {"upload", "confirm-file", "confirm-tool", "confirm-spindle", "start", "pause", "resume", "cancel"}
        if action not in allowed:
            raise ApplicationError(f"Acción de ejecución no soportada: {action}.")
        preflight = execution_preflight(project_id, operation_id, request)
        if action in {"upload", "start"} and not preflight["ready"]:
            raise ApplicationError("Preflight incompleto: no se puede continuar. Revise modo físico, homing, mapa, referencia y archivo compensado.")
        if action == "start":
            raise ApplicationError("Inicio real bloqueado durante desarrollo. Requiere confirmación física supervisada desde la prueba integral.")
        state_by_action = {
            "upload": "UPLOADING",
            "confirm-file": "PREFLIGHT",
            "confirm-tool": "PREFLIGHT",
            "confirm-spindle": "READY_TO_EXECUTE",
            "pause": "PAUSED",
            "resume": "RUNNING",
            "cancel": "CANCELLED",
        }
        return {"state": state_by_action.get(action, "PREFLIGHT"), "action": action, "detail": f"Acción {action} registrada para operación {operation_id}.", "preflight": preflight}

    @router.delete("/projects/{project_id}/operations/{operation_id}/height-map", response_model=dict[str, str])
    def delete_height_map(project_id: str, operation_id: str, request: Request) -> dict[str, str]:
        service = request.app.state.height_map_service
        service.delete_map(project_id, operation_id)
        return {"detalle": "Mapa de alturas eliminado."}

    @router.get("/machine/session", response_model=MachineSessionResponse)
    def get_machine_session(request: Request) -> MachineSessionResponse:
        service = request.app.state.machine_session_service
        session = service.get_status()
        return machine_session_to_response(session)

    return router
