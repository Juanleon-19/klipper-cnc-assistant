from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse

from klipper_cnc_assistant import __version__
from klipper_cnc_assistant.application import (
    ApplicationError,
    CompensatedGCodeService,
    HeightMapService,
    JobService,
    MachineSessionService,
    MeshExecutionService,
    NotFoundError,
    PhysicalMapService,
    ProjectService,
    ReferenceSessionService,
    SystemStatusService,
)
from klipper_cnc_assistant.domain import DomainError, ProjectValidationError
from klipper_cnc_assistant.machine.config import load_machine_runtime_config
from klipper_cnc_assistant.machine.runtime import MachineRuntime, MachineRuntimeError
from klipper_cnc_assistant.storage import JsonProjectRepository

from .machine_routes import build_machine_router
from .routes import build_router


def create_app(
    *,
    data_dir: Path | None = None,
    frontend_dist_dir: Path | None = None,
) -> FastAPI:
    resolved_data_dir = data_dir or Path(os.getenv("KCA_DATA_DIR", "data"))
    resolved_frontend_dist = frontend_dist_dir or Path(
        os.getenv("KCA_FRONTEND_DIST", "frontend/dist")
    )
    repository = JsonProjectRepository(resolved_data_dir)
    machine_session_service = MachineSessionService()
    machine_runtime = MachineRuntime(load_machine_runtime_config(), settings_path=resolved_data_dir / "machine_runtime_settings.json")
    machine_session_service.machine_mode = "fisico" if machine_runtime.config.mode.value == "physical" else "simulado"
    project_service = ProjectService(repository)
    height_map_service = HeightMapService(repository)
    physical_map_service = PhysicalMapService(repository)
    mesh_execution_service = MeshExecutionService(physical_map_service)
    compensated_gcode_service = CompensatedGCodeService(repository, physical_map_service)
    reference_session_service = ReferenceSessionService(
        repository,
        height_map_service,
        machine_session_service,
        physical_map_service,
    )
    job_service = JobService(
        repository,
        physical_map_service,
        reference_session_service,
        compensated_gcode_service,
        machine_runtime,
    )
    system_status_service = SystemStatusService(
        repository,
        machine_session_service,
    )

    app = FastAPI(
        title="Klipper CNC Assistant API",
        version=__version__,
        description=(
            "API web para proyectos PCB, analisis de G-code, "
            "diagnostico y sesion de maquina fisica o simulada."
        ),
    )
    app.state.project_service = project_service
    app.state.height_map_service = height_map_service
    app.state.physical_map_service = physical_map_service
    app.state.mesh_execution_service = mesh_execution_service
    app.state.compensated_gcode_service = compensated_gcode_service
    app.state.job_service = job_service
    app.state.machine_session_service = machine_session_service
    app.state.machine_runtime = machine_runtime
    app.state.reference_session_service = reference_session_service
    app.state.system_status_service = system_status_service
    app.state.frontend_dist_dir = resolved_frontend_dist
    app.include_router(build_router())
    app.include_router(build_machine_router())

    @app.on_event("startup")
    async def start_machine_runtime() -> None:
        machine_runtime.start()

    @app.on_event("shutdown")
    async def stop_machine_runtime() -> None:
        machine_runtime.stop()

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_request, exc: RequestValidationError) -> JSONResponse:
        translated: list[str] = []
        structured: list[dict[str, object]] = []
        for error in exc.errors():
            location = ".".join(str(item) for item in error["loc"] if item != "body") or "solicitud"
            error_type = error.get("type", "")
            received = error.get("input")
            if error_type in {"missing"}:
                message = "campo obligatorio."
                expected = "valor presente"
                solution = "Complete el campo antes de enviar."
            elif error_type in {"float_parsing", "int_parsing", "finite_number", "float_type", "int_type"}:
                message = "debe ser un numero valido."
                expected = "numero JSON finito"
                solution = "Use un numero con punto decimal si necesita decimales."
            elif error_type == "string_too_short":
                message = "texto demasiado corto."
                expected = "texto no vacio"
                solution = "Ingrese un texto valido."
            else:
                message = "valor invalido."
                expected = "valor compatible con el esquema"
                solution = "Revise el valor indicado y vuelva a intentar."
            translated.append(f"{location}: {message}")
            structured.append({
                "campo": location,
                "valor_recibido": received,
                "valor_esperado": expected,
                "causa": message.rstrip("."),
                "solucion": solution,
                "accion_recomendada": "Corregir el campo y reenviar la solicitud.",
            })
        detail = "Solicitud invalida. " + " ".join(translated)
        return JSONResponse(status_code=422, content={"detalle": detail.strip(), "errores": structured})

    @app.exception_handler(NotFoundError)
    async def handle_not_found(_request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detalle": str(exc)})

    async def handle_application_error(_request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detalle": str(exc)})

    app.add_exception_handler(ApplicationError, handle_application_error)
    app.add_exception_handler(DomainError, handle_application_error)
    app.add_exception_handler(MachineRuntimeError, handle_application_error)
    app.add_exception_handler(ProjectValidationError, handle_application_error)
    app.add_exception_handler(ValueError, handle_application_error)

    _register_frontend_routes(app, resolved_frontend_dist)
    return app


def _register_frontend_routes(app: FastAPI, frontend_dist_dir: Path) -> None:
    index_file = frontend_dist_dir / "index.html"
    if not index_file.exists():
        return

    def _resolve_asset(candidate_path: str) -> FileResponse:
        if candidate_path.startswith(("api/", "docs", "redoc", "openapi.json")):
            raise HTTPException(status_code=404)
        target = (frontend_dist_dir / candidate_path).resolve()
        root = frontend_dist_dir.resolve()
        if target.is_file() and (target == root or root in target.parents):
            return FileResponse(target)
        return FileResponse(index_file)

    @app.get("/", include_in_schema=False)
    async def frontend_index() -> FileResponse:
        return FileResponse(index_file)

    @app.get("/{full_path:path}", include_in_schema=False)
    async def frontend_app(full_path: str) -> FileResponse:
        return _resolve_asset(full_path)
