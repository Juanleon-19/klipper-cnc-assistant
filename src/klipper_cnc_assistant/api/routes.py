from __future__ import annotations

from fastapi import APIRouter, Request
from starlette.datastructures import UploadFile

from klipper_cnc_assistant.application import ApplicationError

from .heightmap_schemas import (
    HeightMapConfigRequest,
    HeightMapImportRequest,
    HeightMapResponse,
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
    OperationResponse,
    ProjectCreateRequest,
    ProjectResponse,
    ProjectUpdateRequest,
    SystemInfoResponse,
    analysis_to_response,
    machine_session_to_response,
    operation_to_response,
    project_to_response,
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
            raise ApplicationError(
                "Debe enviar el archivo G-code en el campo 'archivo'."
            )
        try:
            filename = uploaded.filename or ""
            content = await uploaded.read()
        finally:
            await uploaded.close()
        return filename, content, True

    raise ApplicationError(
        "Tipo de contenido no soportado. Use JSON o multipart/form-data."
    )


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

    @router.post("/projects/{project_id}/operations", response_model=OperationResponse, status_code=201)
    def add_operation(project_id: str, payload: OperationCreateRequest, request: Request) -> OperationResponse:
        service = request.app.state.project_service
        operation = service.add_operation(
            project_id=project_id,
            nombre=payload.nombre,
            tipo=payload.tipo,
            cara=payload.cara,
            orden=payload.orden,
            herramienta=payload.herramienta,
        )
        return operation_to_response(operation)

    @router.delete("/projects/{project_id}/operations/{operation_id}", response_model=dict[str, str])
    def delete_operation(project_id: str, operation_id: str, request: Request) -> dict[str, str]:
        service = request.app.state.project_service
        service.delete_operation(project_id, operation_id)
        return {"detalle": "Operacion eliminada."}

    @router.delete("/projects/{project_id}/operations/{operation_id}/gcode", response_model=OperationResponse)
    def remove_gcode(project_id: str, operation_id: str, request: Request) -> OperationResponse:
        service = request.app.state.project_service
        operation = service.remove_operation_gcode(
            project_id=project_id,
            operation_id=operation_id,
        )
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

    @router.put("/projects/{project_id}/operations/{operation_id}/height-map/config", response_model=HeightMapResponse)
    def configure_height_map(project_id: str, operation_id: str, payload: HeightMapConfigRequest, request: Request) -> HeightMapResponse:
        service = request.app.state.height_map_service
        height_map = service.configure_map(
            project_id=project_id,
            operation_id=operation_id,
            filas=payload.filas,
            columnas=payload.columnas,
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
            escenario=payload.escenario,
            semilla=payload.semilla,
        )
        return height_map_to_response(height_map, service.build_surfaces(height_map))

    @router.post("/projects/{project_id}/operations/{operation_id}/height-map/import/json", response_model=HeightMapResponse)
    def import_height_map_json(project_id: str, operation_id: str, payload: HeightMapImportRequest, request: Request) -> HeightMapResponse:
        service = request.app.state.height_map_service
        height_map = service.import_json_map(
            project_id=project_id,
            operation_id=operation_id,
            content=payload.contenido,
        )
        return height_map_to_response(height_map, service.build_surfaces(height_map))

    @router.post("/projects/{project_id}/operations/{operation_id}/height-map/import/csv", response_model=HeightMapResponse)
    def import_height_map_csv(project_id: str, operation_id: str, payload: HeightMapImportRequest, request: Request) -> HeightMapResponse:
        service = request.app.state.height_map_service
        height_map = service.import_csv_map(
            project_id=project_id,
            operation_id=operation_id,
            content=payload.contenido,
        )
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
            rms_residuos_mm=statistics.rms_residuos_mm,
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
        height_map = service.update_sample(
            project_id=project_id,
            operation_id=operation_id,
            sample_id=sample_id,
            **update_kwargs,
        )
        return height_map_to_response(height_map, service.build_surfaces(height_map))

    @router.post("/projects/{project_id}/operations/{operation_id}/height-map/recalculate", response_model=HeightMapResponse)
    def recalculate_height_map(project_id: str, operation_id: str, request: Request) -> HeightMapResponse:
        service = request.app.state.height_map_service
        height_map = service.recalculate_map(project_id, operation_id)
        return height_map_to_response(height_map, service.build_surfaces(height_map))

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
