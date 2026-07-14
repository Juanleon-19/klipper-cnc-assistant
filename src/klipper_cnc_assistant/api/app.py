from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse

from klipper_cnc_assistant import __version__
from klipper_cnc_assistant.application import (
    ApplicationError,
    HeightMapService,
    MachineSessionService,
    NotFoundError,
    ProjectService,
    SystemStatusService,
)
from klipper_cnc_assistant.domain import DomainError, ProjectValidationError
from klipper_cnc_assistant.storage import JsonProjectRepository

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
    project_service = ProjectService(repository)
    height_map_service = HeightMapService(repository)
    system_status_service = SystemStatusService(
        repository,
        machine_session_service,
    )

    app = FastAPI(
        title="Klipper CNC Assistant API",
        version=__version__,
        description=(
            "API web para proyectos PCB, analisis de G-code, "
            "diagnostico y sesion de maquina simulada."
        ),
    )
    app.state.project_service = project_service
    app.state.height_map_service = height_map_service
    app.state.machine_session_service = machine_session_service
    app.state.system_status_service = system_status_service
    app.state.frontend_dist_dir = resolved_frontend_dist
    app.include_router(build_router())

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_request, exc: RequestValidationError) -> JSONResponse:
        details = []
        for error in exc.errors():
            location = ".".join(
                str(item)
                for item in error["loc"]
                if item != "body"
            )
            details.append(
                f"{location or 'solicitud'}: valor invalido."
            )
        detail = "Solicitud invalida. " + " ".join(details)
        return JSONResponse(status_code=422, content={"detalle": detail.strip()})

    @app.exception_handler(NotFoundError)
    async def handle_not_found(_request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detalle": str(exc)})

    async def handle_application_error(_request, exc: Exception) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detalle": str(exc)})

    app.add_exception_handler(ApplicationError, handle_application_error)
    app.add_exception_handler(DomainError, handle_application_error)
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
