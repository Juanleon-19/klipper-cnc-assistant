from __future__ import annotations

from fastapi import APIRouter, Request

from klipper_cnc_assistant.application import ApplicationError

from .machine_schemas import (
    DiagnosticModeRequest,
    EmergencyStopRequest,
    JogModeRequest,
    MachineInitializationRequest,
    MachineRuntimeResponse,
    ManualControlRequest,
)


def build_machine_router() -> APIRouter:
    router = APIRouter(prefix="/api/machine", tags=["machine"])

    def runtime(request: Request):
        return request.app.state.machine_runtime

    @router.get("/runtime", response_model=MachineRuntimeResponse)
    def get_runtime(request: Request) -> MachineRuntimeResponse:
        return MachineRuntimeResponse(**runtime(request).snapshot())

    @router.post("/connect", response_model=MachineRuntimeResponse)
    def connect(request: Request) -> MachineRuntimeResponse:
        return MachineRuntimeResponse(**runtime(request).connect())

    @router.post("/disconnect", response_model=MachineRuntimeResponse)
    def disconnect(request: Request) -> MachineRuntimeResponse:
        return MachineRuntimeResponse(**runtime(request).disconnect())

    @router.post("/diagnostic-mode", response_model=MachineRuntimeResponse)
    def diagnostic_mode(payload: DiagnosticModeRequest, request: Request) -> MachineRuntimeResponse:
        return MachineRuntimeResponse(**runtime(request).set_diagnostic_mode(payload.enabled))

    @router.post("/initialize", response_model=MachineRuntimeResponse)
    def initialize(payload: MachineInitializationRequest, request: Request) -> MachineRuntimeResponse:
        return MachineRuntimeResponse(**runtime(request).initialize(payload.target_z_mm))

    @router.post("/manual-control", response_model=MachineRuntimeResponse)
    def manual_control(payload: ManualControlRequest, request: Request) -> MachineRuntimeResponse:
        return MachineRuntimeResponse(**runtime(request).enable_manual_control(payload.enabled))

    @router.post("/jog-mode", response_model=MachineRuntimeResponse)
    def jog_mode(payload: JogModeRequest, request: Request) -> MachineRuntimeResponse:
        return MachineRuntimeResponse(**runtime(request).change_jog_mode(payload.mode))

    @router.post("/probe/request", response_model=MachineRuntimeResponse)
    def request_probe(request: Request) -> MachineRuntimeResponse:
        return MachineRuntimeResponse(**runtime(request).request_probe())

    @router.post("/probe/confirm", response_model=MachineRuntimeResponse)
    def confirm_probe(request: Request) -> MachineRuntimeResponse:
        return MachineRuntimeResponse(**runtime(request).confirm_probe())

    @router.post("/cancel", response_model=MachineRuntimeResponse)
    def cancel(request: Request) -> MachineRuntimeResponse:
        return MachineRuntimeResponse(**runtime(request).cancel_operation())

    @router.post("/safe-stop", response_model=MachineRuntimeResponse)
    def safe_stop(request: Request) -> MachineRuntimeResponse:
        return MachineRuntimeResponse(**runtime(request).cancel_operation())

    @router.post("/emergency", response_model=MachineRuntimeResponse)
    def emergency(payload: EmergencyStopRequest, request: Request) -> MachineRuntimeResponse:
        if not payload.confirm:
            raise ApplicationError("Confirme explícitamente la emergencia Klipper M112 antes de enviarla.")
        return MachineRuntimeResponse(**runtime(request).emergency_stop())

    return router
