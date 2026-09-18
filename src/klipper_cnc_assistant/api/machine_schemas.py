from __future__ import annotations

from pydantic import BaseModel as PydanticBaseModel, ConfigDict, Field


class BaseModel(PydanticBaseModel):
    model_config = ConfigDict(allow_inf_nan=False)


class MachineRuntimeResponse(BaseModel):
    mode: str
    mode_label: str
    state: str
    health: str
    started_at: str
    application: dict
    moonraker: dict
    klipper: dict
    preparation: dict = Field(default_factory=dict)
    tool_change: dict = Field(default_factory=dict)
    settings: dict[str, float] = Field(default_factory=dict)
    arduino: dict
    probe_live: dict = Field(default_factory=dict)
    last_probe_failure: dict | None = None
    controller: dict
    safety: dict
    last_command: str | None
    last_movement: dict | None
    last_error: str | None
    last_probe_result: dict | None
    active_operation: dict | None = None
    recovery_pending: bool = False
    recovery_reason: str | None = None
    initialization_steps: list[dict]
    events: list[dict]


class DiagnosticModeRequest(BaseModel):
    enabled: bool = True


class ManualControlRequest(BaseModel):
    enabled: bool = True


class JogModeRequest(BaseModel):
    mode: str = Field(pattern="^(fine|normal|coarse)$")


class MachineInitializationRequest(BaseModel):
    target_z_mm: float | None = None


class MachineSettingsRequest(BaseModel):
    reference_prep_z_mm: float | None = Field(default=None, gt=0)
    long_tool_change_clearance_z_mm: float | None = Field(default=None, gt=0)
    # Alias transitorio de lectura/escritura para clientes anteriores al hotfix.
    long_tool_reference_prep_z_mm: float | None = Field(default=None, gt=0)
    z_clearance_feed_mm_min: float | None = Field(default=None, gt=0)
    reference_approach_z_feed_mm_min: float | None = Field(default=None, gt=0)
    # Alias transitorio: al leerlo se aplica a ambos feeds Z canónicos.
    reference_prep_z_feed_mm_min: float | None = Field(default=None, gt=0)
    move_total_timeout_s: float | None = Field(default=None, gt=0)
    no_progress_timeout_s: float | None = Field(default=None, gt=0)
    position_tolerance_mm: float | None = Field(default=None, gt=0)
    velocity_tolerance_mm_s: float | None = Field(default=None, gt=0)
    reference_probe_step_mm: float | None = Field(default=None, gt=0)
    reference_probe_feed_mm_min: float | None = Field(default=None, gt=0)
    reference_probe_retract_mm: float | None = Field(default=None, gt=0)
    reference_probe_retract_feed_mm_min: float | None = Field(default=None, gt=0)


class ProbeContextRequest(BaseModel):
    project_id: str = Field(min_length=1)
    operation_id: str = Field(min_length=1)


class EmergencyStopRequest(BaseModel):
    confirm: bool = False
