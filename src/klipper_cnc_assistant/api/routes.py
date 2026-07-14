from __future__ import annotations

from fastapi import APIRouter, Request

from .schemas import (
    GCodeUploadRequest,
    HealthResponse,
    MachineSessionResponse,
    OperationAnalysisResponse,
    OperationCreateRequest,
    OperationResponse,
    ProjectCreateRequest,
    ProjectResponse,
    analysis_to_response,
    machine_session_to_response,
    operation_to_response,
    project_to_response,
)


def build_router() -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(estado="ok")

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

    @router.post("/projects/{project_id}/operations/{operation_id}/gcode", response_model=OperationResponse)
    def upload_gcode(project_id: str, operation_id: str, payload: GCodeUploadRequest, request: Request) -> OperationResponse:
        service = request.app.state.project_service
        operation = service.upload_operation_gcode(
            project_id=project_id,
            operation_id=operation_id,
            filename=payload.nombre_archivo,
            content=payload.contenido,
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

    @router.get("/machine/session", response_model=MachineSessionResponse)
    def get_machine_session(request: Request) -> MachineSessionResponse:
        service = request.app.state.machine_session_service
        session = service.get_status()
        return machine_session_to_response(session)

    return router
