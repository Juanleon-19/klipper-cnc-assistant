from __future__ import annotations

from fastapi import APIRouter, Request

from klipper_cnc_assistant.application import ApplicationError

from .machine_schemas import (
    DiagnosticModeRequest,
    EmergencyStopRequest,
    JogModeRequest,
    MachineInitializationRequest,
    MachineRuntimeResponse,
    MachineSettingsRequest,
    ManualControlRequest,
)


def build_machine_router() -> APIRouter:
    router = APIRouter(prefix="/api/machine", tags=["machine"])

    def runtime(request: Request):
        return request.app.state.machine_runtime

    @router.get("/runtime", response_model=MachineRuntimeResponse)
    def get_runtime(request: Request) -> MachineRuntimeResponse:
        return MachineRuntimeResponse(**runtime(request).snapshot())

    @router.get("/status", response_model=MachineRuntimeResponse)
    def get_status(request: Request) -> MachineRuntimeResponse:
        return MachineRuntimeResponse(**runtime(request).snapshot())

    @router.get("/settings", response_model=dict[str, float])
    def get_settings(request: Request) -> dict[str, float]:
        return runtime(request).machine_settings()

    @router.put("/settings", response_model=dict[str, float])
    def update_settings(payload: MachineSettingsRequest, request: Request) -> dict[str, float]:
        return runtime(request).update_machine_settings(payload.model_dump(exclude_unset=True))

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

    @router.post("/tool-change-position", response_model=MachineRuntimeResponse)
    def tool_change_position(request: Request) -> MachineRuntimeResponse:
        return MachineRuntimeResponse(**runtime(request).move_to_tool_change_position())

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
