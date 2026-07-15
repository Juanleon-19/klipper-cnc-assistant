from __future__ import annotations

from pydantic import BaseModel, Field


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
    arduino: dict
    controller: dict
    safety: dict
    last_command: str | None
    last_movement: dict | None
    last_error: str | None
    last_probe_result: dict | None
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


class EmergencyStopRequest(BaseModel):
    confirm: bool = False
