from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum


class MachineMode(StrEnum):
    SIMULATED = "simulated"
    PHYSICAL = "physical"


class SpindleControlMode(StrEnum):
    MANUAL = "manual"
    AUTO = "auto"


@dataclass(frozen=True)
class MachineRuntimeConfig:
    mode: MachineMode
    auto_connect: bool
    moonraker_url: str | None
    moonraker_ws: str | None
    serial_port: str | None
    serial_baudrate: int
    safe_z_mm: float
    reference_prep_z_mm: float
    reference_prep_z_feed_mm_min: float
    tool_change_z_mm: float
    tool_change_clearance_z_mm: float
    tool_change_work_z_mm: float
    tool_change_z_positive_up: bool
    tool_change_z_feed_mm_min: float
    tool_change_x_mm: float
    tool_change_y_mm: float
    moonraker_request_timeout_s: float
    home_timeout_s: float
    telemetry_fresh_timeout_s: float
    serial_fresh_timeout_s: float
    serial_startup_delay_s: float
    settle_tolerance_mm: float
    velocity_tolerance_mm_s: float
    move_timeout_s: float
    move_minimum_timeout_s: float
    move_timeout_factor: float
    move_settle_margin_s: float
    no_progress_timeout_s: float
    settle_timeout_s: float
    stable_samples: int
    probe_step_mm: float
    probe_open_stable_ms: float
    probe_lower_speed_mm_s: float
    probe_retract_mm: float
    probe_retract_speed_mm_s: float
    long_tool_reference_prep_z_mm: float = 115.0
    spindle_control_mode: SpindleControlMode = SpindleControlMode.MANUAL
    safe_z_is_configured: bool = False

    @property
    def mode_label(self) -> str:
        return "FISICO" if self.mode is MachineMode.PHYSICAL else "SIMULADO"


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "si", "sí"}


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return float(value)


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return int(value)


def load_machine_runtime_config() -> MachineRuntimeConfig:
    raw_mode = os.getenv("MACHINE_MODE", "simulated").strip().lower()
    mode = MachineMode.PHYSICAL if raw_mode == "physical" else MachineMode.SIMULATED
    safe_z_env = os.getenv("MACHINE_SAFE_Z")
    return MachineRuntimeConfig(
        mode=mode,
        spindle_control_mode=SpindleControlMode(os.getenv("SPINDLE_CONTROL_MODE", "manual").strip().lower() or "manual"),
        auto_connect=_env_bool("MACHINE_AUTO_CONNECT", False),
        moonraker_url=os.getenv("MOONRAKER_URL") or None,
        moonraker_ws=os.getenv("MOONRAKER_WS") or None,
        serial_port=os.getenv("SERIAL_PORT") or None,
        serial_baudrate=_env_int("SERIAL_BAUDRATE", 115200),
        safe_z_mm=_env_float("MACHINE_SAFE_Z", 10.0),
        reference_prep_z_mm=_env_float("REFERENCE_PREP_Z_MM", 115.0),
        long_tool_reference_prep_z_mm=_env_float(
            "LONG_TOOL_REFERENCE_PREP_Z_MM",
            _env_float("REFERENCE_PREP_Z_MM", 115.0),
        ),
        reference_prep_z_feed_mm_min=_env_float("REFERENCE_PREP_Z_FEED_MM_MIN", 180.0),
        tool_change_z_mm=_env_float("TOOL_CHANGE_Z_MM", 115.0),
        tool_change_clearance_z_mm=_env_float("TOOL_CHANGE_CLEARANCE_Z_MM", _env_float("TOOL_CHANGE_Z_MM", 115.0)),
        tool_change_work_z_mm=_env_float("TOOL_CHANGE_WORK_Z_MM", _env_float("TOOL_CHANGE_Z_MM", 115.0)),
        tool_change_z_positive_up=_env_bool("TOOL_CHANGE_Z_POSITIVE_UP", True),
        tool_change_z_feed_mm_min=_env_float("TOOL_CHANGE_Z_FEED_MM_MIN", 180.0),
        tool_change_x_mm=_env_float("TOOL_CHANGE_X_MM", 0.0),
        tool_change_y_mm=_env_float("TOOL_CHANGE_Y_MM", 0.0),
        moonraker_request_timeout_s=_env_float("MOONRAKER_REQUEST_TIMEOUT", 2.0),
        home_timeout_s=_env_float("MACHINE_HOME_TIMEOUT", 120.0),
        telemetry_fresh_timeout_s=_env_float("TELEMETRY_STALE_TIMEOUT", _env_float("MACHINE_TELEMETRY_FRESH_TIMEOUT", 2.0)),
        serial_fresh_timeout_s=_env_float("MACHINE_SERIAL_FRESH_TIMEOUT", 2.0),
        serial_startup_delay_s=_env_float("SERIAL_STARTUP_DELAY", 2.0),
        settle_tolerance_mm=_env_float("MACHINE_POSITION_TOLERANCE_MM", _env_float("MACHINE_SETTLE_TOLERANCE", 0.05)),
        velocity_tolerance_mm_s=_env_float("MACHINE_VELOCITY_TOLERANCE_MM_S", _env_float("MACHINE_VELOCITY_TOLERANCE", 0.02)),
        move_timeout_s=_env_float("MACHINE_MOVE_TOTAL_TIMEOUT", _env_float("MACHINE_MOVE_TIMEOUT", 180.0)),
        move_minimum_timeout_s=_env_float("MACHINE_MOVE_MINIMUM_TIMEOUT", 180.0),
        move_timeout_factor=_env_float("MACHINE_MOVE_TIMEOUT_FACTOR", 1.5),
        move_settle_margin_s=_env_float("MACHINE_MOVE_SETTLE_MARGIN", 10.0),
        no_progress_timeout_s=_env_float("MACHINE_NO_PROGRESS_TIMEOUT", 60.0),
        settle_timeout_s=_env_float("MACHINE_SETTLE_TIMEOUT", 5.0),
        stable_samples=_env_int("MACHINE_STABLE_SAMPLES", 3),
        probe_step_mm=_env_float("PROBE_STEP_DISTANCE", 0.05),
        probe_open_stable_ms=_env_float("PROBE_OPEN_STABLE_MS", 50.0),
        probe_lower_speed_mm_s=_env_float("PROBE_LOWER_SPEED", 1.0),
        probe_retract_mm=_env_float("PROBE_RETRACT_DISTANCE", 1.0),
        probe_retract_speed_mm_s=_env_float("PROBE_RETRACT_SPEED", 1.0),
        safe_z_is_configured=safe_z_env is not None and bool(str(safe_z_env).strip()),
    )
