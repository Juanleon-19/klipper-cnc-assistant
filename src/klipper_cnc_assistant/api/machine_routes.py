from __future__ import annotations

from fastapi import APIRouter, Request

from klipper_cnc_assistant.application import ApplicationError

from .machine_schemas import (
    DiagnosticModeRequest,
    EmergencyStopRequest,
    JogModeRequest,
    MachineInitializationRequest,
    ToolProfileRequest,
    MachineRuntimeResponse,
    MachineSettingsRequest,
    ManualControlRequest,
    ProbeContextRequest,
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

    @router.get("/settings", response_model=dict[str, float | None])
    def get_settings(request: Request) -> dict[str, float | None]:
        return runtime(request).machine_settings()

    @router.put("/settings", response_model=dict[str, float | None])
    def update_settings(payload: MachineSettingsRequest, request: Request) -> dict[str, float | None]:
        request.app.state.job_service.assert_machine_settings_update_allowed()
        return runtime(request).update_machine_settings(payload.model_dump(exclude_unset=True))

    @router.post("/connect", response_model=MachineRuntimeResponse)
    def connect(request: Request) -> MachineRuntimeResponse:
        return MachineRuntimeResponse(**runtime(request).connect())

    @router.post("/disconnect", response_model=MachineRuntimeResponse)
    def disconnect(request: Request) -> MachineRuntimeResponse:
        return MachineRuntimeResponse(**runtime(request).disconnect())

    @router.post("/reconnect-arduino", response_model=MachineRuntimeResponse)
    def reconnect_arduino(request: Request) -> MachineRuntimeResponse:
        return MachineRuntimeResponse(**runtime(request).reconnect_arduino())

    @router.post("/reconnect-runtime", response_model=MachineRuntimeResponse)
    def reconnect_runtime(request: Request) -> MachineRuntimeResponse:
        return MachineRuntimeResponse(**runtime(request).reconnect_runtime())

    @router.post("/diagnostic-mode", response_model=MachineRuntimeResponse)
    def diagnostic_mode(payload: DiagnosticModeRequest, request: Request) -> MachineRuntimeResponse:
        return MachineRuntimeResponse(**runtime(request).set_diagnostic_mode(payload.enabled))

    @router.get("/administrative-recovery")
    def administrative_recovery_status(request: Request):
        from klipper_cnc_assistant.execution.administrative_recovery import status
        return status(request.app.state.job_service)

    @router.post("/administrative-recovery/{transaction_id}")
    def recover_administrative_transaction(transaction_id: str, request: Request):
        from klipper_cnc_assistant.execution.administrative_recovery import recover
        from klipper_cnc_assistant.machine.physical_ownership import OwnershipError
        from klipper_cnc_assistant.application.errors import ApplicationError
        try:
            return recover(request.app.state.job_service, transaction_id)
        except OwnershipError as error:
            raise ApplicationError(str(error)) from error

    @router.post("/recover-idle-controls", response_model=MachineRuntimeResponse)
    def recover_idle_controls(request: Request) -> MachineRuntimeResponse:
        return MachineRuntimeResponse(**runtime(request).recover_idle_controls())

    @router.post("/initialize", response_model=MachineRuntimeResponse)
    def initialize(payload: MachineInitializationRequest, request: Request) -> MachineRuntimeResponse:
        request.app.state.job_service.assert_machine_settings_update_allowed()
        return MachineRuntimeResponse(**runtime(request).initialize(payload.target_z_mm))

    @router.post("/home", response_model=MachineRuntimeResponse)
    def home(request: Request) -> MachineRuntimeResponse:
        request.app.state.job_service.assert_machine_settings_update_allowed()
        return MachineRuntimeResponse(**runtime(request).home_xyz())

    @router.post("/prepare-position", response_model=MachineRuntimeResponse)
    def prepare_position(payload: ToolProfileRequest, request: Request) -> MachineRuntimeResponse:
        request.app.state.job_service.assert_machine_settings_update_allowed()
        return MachineRuntimeResponse(**runtime(request).prepare_position(payload.tool_reference_profile))

    @router.post("/manual-control", response_model=MachineRuntimeResponse)
    def manual_control(payload: ManualControlRequest, request: Request) -> MachineRuntimeResponse:
        return MachineRuntimeResponse(**runtime(request).enable_manual_control(payload.enabled))

    @router.post("/tool-change-position", response_model=MachineRuntimeResponse)
    def tool_change_position(request: Request, payload: ToolProfileRequest | None = None) -> MachineRuntimeResponse:
        request.app.state.job_service.assert_machine_settings_update_allowed()
        profile = "standard" if payload is None else payload.tool_reference_profile
        return MachineRuntimeResponse(**runtime(request).move_to_tool_change_position(profile))

    @router.post("/jog-mode", response_model=MachineRuntimeResponse)
    def jog_mode(payload: JogModeRequest, request: Request) -> MachineRuntimeResponse:
        return MachineRuntimeResponse(**runtime(request).change_jog_mode(payload.mode))

    @router.post("/probe/request", response_model=MachineRuntimeResponse)
    def request_probe(request: Request) -> MachineRuntimeResponse:
        return MachineRuntimeResponse(**runtime(request).request_probe())

    @router.post("/probe/confirm", response_model=MachineRuntimeResponse)
    def confirm_probe(request: Request, payload: ProbeContextRequest | None = None) -> MachineRuntimeResponse:
        context = None if payload is None else request.app.state.reference_session_service.probe_capture_context(payload.project_id, payload.operation_id)
        return MachineRuntimeResponse(**runtime(request).confirm_probe(reference_context=context))

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
