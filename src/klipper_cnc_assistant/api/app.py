from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from klipper_cnc_assistant.application import (
    ApplicationError,
    MachineSessionService,
    NotFoundError,
    ProjectService,
)
from klipper_cnc_assistant.domain import (
    DomainError,
    ProjectValidationError,
)
from klipper_cnc_assistant.storage import JsonProjectRepository

from .routes import build_router


def create_app(
    *,
    data_dir: Path | None = None,
) -> FastAPI:
    resolved_data_dir = data_dir or Path(
        os.getenv("KCA_DATA_DIR", "data")
    )
    repository = JsonProjectRepository(resolved_data_dir)
    project_service = ProjectService(repository)
    machine_session_service = MachineSessionService()

    app = FastAPI(
        title="Klipper CNC Assistant API",
        version="0.1.0",
        description=(
            "API inicial para proyectos PCB, "
            "analisis de G-code y sesion de maquina simulada."
        ),
    )
    app.state.project_service = project_service
    app.state.machine_session_service = machine_session_service
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

    return app
