from __future__ import annotations

import _thread
import asyncio
import json
import logging
import math
import threading
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable

from klipper_cnc_assistant.input.command_mapper import CommandMapper, ControllerCommand
from klipper_cnc_assistant.input.connection_manager import ArduinoConnectionManager, ArduinoConnectionState, UsbIdentity
from klipper_cnc_assistant.input.serial_driver import ControllerPacket, SerialDriver, SerialProtocolError
from klipper_cnc_assistant.jog.controller import JogController, JogError
from klipper_cnc_assistant.jog.manual import ManualJogController
from klipper_cnc_assistant.jog.profiles import JogMode, get_jog_profile
from klipper_cnc_assistant.machine.discovery import discover_machine
from klipper_cnc_assistant.moonraker.client import MoonrakerClient, MoonrakerError, MoonrakerTimeout
from klipper_cnc_assistant.moonraker.telemetry import MoonrakerTelemetry

from .config import MachineMode, MachineRuntimeConfig


logger = logging.getLogger(__name__)


class MachineRuntimeState(StrEnum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    DIAGNOSTIC = "DIAGNOSTIC"
    READY_FOR_HOME = "READY_FOR_HOME"
    HOMING = "HOMING"
    HOMED = "HOMED"
    WAITING_SAFE_Z = "WAITING_SAFE_Z"
    MOVING_TO_SAFE_Z = "MOVING_TO_SAFE_Z"
    MOVING_TO_CENTER = "MOVING_TO_CENTER"
    WAITING_FOR_XY_REFERENCE = "WAITING_FOR_XY_REFERENCE"
    REFERENCE_ARMED = "REFERENCE_ARMED"
    PROBING_REFERENCE = "PROBING_REFERENCE"
    REFERENCE_CAPTURED = "REFERENCE_CAPTURED"
    MESH_PLANNED = "MESH_PLANNED"
    MESH_READY = "MESH_READY"
    MESH_PROBING = "MESH_PROBING"
    MESH_PAUSED = "MESH_PAUSED"
    MESH_COMPLETE = "MESH_COMPLETE"
    MAP_VALIDATING = "MAP_VALIDATING"
    MAP_READY = "MAP_READY"
    DEGRADED = "DEGRADED"
    ERROR = "ERROR"
    CANCELLED = "CANCELLED"
    STOPPING = "STOPPING"


class MachineHealth(StrEnum):
    HEALTHY = "HEALTHY"
    WARNING = "WARNING"
    ERROR = "ERROR"
    OFFLINE = "OFFLINE"


class MachineRuntimeError(RuntimeError):
    pass


@dataclass
class OperationContext:
    operation_id: str
    operation_type: str
    generation: int
    cancel_event: threading.Event
    started_at: float


@dataclass
class RuntimeCounters:
    valid_packets: int = 0
    invalid_packets: int = 0
    checksum_errors: int = 0
    disconnects: int = 0


@dataclass
class RuntimeEvent:
    timestamp: str
    level: str
    message: str


@dataclass
class InitializationStep:
    name: str
    status: str
    detail: str
    timestamp: str


@dataclass
class ProbeResult:
    x_mm: float
    y_mm: float
    z_mm: float
    captured_at: str


@dataclass(frozen=True)
class ProbeMotionProfile:
    source: str
    probe_step_mm: float
    probe_feed_mm_min: float
    probe_speed_mm_s: float
    retract_mm: float
    retract_feed_mm_min: float
    retract_speed_mm_s: float
    probe_open_stable_ms: float
    settle_tolerance_mm: float

    def to_payload(self, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(overrides or {})
        payload["source"] = self.source
        payload["effective_probe_step_mm"] = self.probe_step_mm
        payload["effective_probe_feed_mm_min"] = self.probe_feed_mm_min
        payload["effective_retract_mm"] = self.retract_mm
        payload["effective_retract_feed_mm_min"] = self.retract_feed_mm_min
        payload["effective_probe_open_stable_ms"] = self.probe_open_stable_ms
        payload["effective_settle_tolerance_mm"] = self.settle_tolerance_mm
        return payload


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return utc_now().isoformat()


def _run_telemetry(telemetry: MoonrakerTelemetry, failures: list[BaseException]) -> None:
    try:
        asyncio.run(telemetry.run())
    except BaseException as error:
        failures.append(error)
        _thread.interrupt_main()


def _cycle_mode(current_mode: JogMode) -> JogMode:
    order = (JogMode.FINE, JogMode.NORMAL, JogMode.COARSE)
    return order[(order.index(current_mode) + 1) % len(order)]


def _is_cardinal(command: ControllerCommand) -> bool:
    return (command.jog_x != 0 and command.jog_y == 0) or (command.jog_y != 0 and command.jog_x == 0)


AUXILIARY_REFERENCE_XY_FEED_MM_MIN = 1800.0
AUXILIARY_DEFAULT_TRAVEL_FEED_MM_MIN = 600.0

def calculate_safe_probe_z(reference_z: float, clearance_mm: float, axis_direction: int, z_limits: Any) -> float:
    """Return an absolute Z that is clearance away from the measured surface.

    ``axis_direction`` is +1 when increasing machine Z retracts from the PCB and
    -1 for the inverse kinematic convention.  The result must remain farther
    from the surface after clamping to discovered Klipper limits.
    """
    if clearance_mm <= 0 or axis_direction not in {-1, 1}:
        raise MachineRuntimeError("La separación segura de sonda o el sentido Z es inválido.")
    requested = reference_z + axis_direction * clearance_mm
    target = min(max(requested, z_limits.minimum), z_limits.maximum)
    if (target - reference_z) * axis_direction <= 0:
        raise MachineRuntimeError(
            f"No existe Z segura alejándose de la referencia: inicio={reference_z:.3f}, objetivo={requested:.3f}, límites={z_limits.minimum:.3f}..{z_limits.maximum:.3f}."
        )
    return target


class MachineRuntime:
    def __init__(
        self,
        config: MachineRuntimeConfig,
        *,
        client_factory: Callable[[str], MoonrakerClient] = MoonrakerClient,
        telemetry_factory: Callable[[str, Any], MoonrakerTelemetry] = MoonrakerTelemetry,
        serial_factory: Callable[..., SerialDriver] = SerialDriver,
        discovery: Callable[[MoonrakerClient], Any] = discover_machine,
        settings_path: Path | None = None,
    ) -> None:
        self._settings_path = settings_path
        self.config = self._load_persisted_config(config)
        self._client_factory = client_factory
        self._telemetry_factory = telemetry_factory
        self._serial_factory = serial_factory
        self._discovery = discovery
        self._lock = threading.RLock()
        self._movement_lock = threading.Lock()
        self._operation_generation = 0
        self._active_operation: OperationContext | None = None
        self._serial_stop = threading.Event()
        self._started_at = utc_now()
        self._state = MachineRuntimeState.DISCONNECTED
        self._client: MoonrakerClient | None = None
        self._machine = None
        self._telemetry: MoonrakerTelemetry | None = None
        self._telemetry_thread: threading.Thread | None = None
        self._telemetry_failures: list[BaseException] = []
        self._driver: SerialDriver | None = None
        self._connection_manager: ArduinoConnectionManager | None = None
        self._serial_thread: threading.Thread | None = None
        self._mapper = CommandMapper()
        self._jog: JogController | None = None
        self._manual: ManualJogController | None = None
        self._manual_enabled = False
        self._diagnostic_input_only = True
        self._ready_for_jog = False
        self._previous_command = ControllerCommand()
        self._last_packet: ControllerPacket | None = None
        self._last_command = ControllerCommand()
        self._last_packet_at: float | None = None
        self._last_telemetry_at: float | None = None
        self._last_command_text: str | None = None
        self._last_movement: dict[str, Any] | None = None
        self._last_error: str | None = None
        self._last_probe_result: ProbeResult | None = None
        self._probe_requested = False
        self._initialization_steps: list[InitializationStep] = []
        self._events: list[RuntimeEvent] = []
        self._counters = RuntimeCounters()
        self._serial_generation = 0
        self._packet_sequence = 0
        self._probe_raw = False
        self._probe_filtered = False
        self._probe_filtered_since: float | None = None
        self._probe_raw_since: float | None = None
        self._last_probe_failure: dict[str, Any] | None = None
        self._telemetry_state = "DISCONNECTED"
        self._telemetry_reconnects = 0
        self._last_websocket_message_at: float | None = None
        self._last_http_observation_at: float | None = None
        self._last_http_error: str | None = None
        self._last_klippy_state: str | None = None
        self._last_websocket_error: str | None = None


    _MACHINE_SETTINGS_FIELDS = {
        "reference_prep_z_mm",
        "long_tool_change_clearance_z_mm",
        "z_clearance_feed_mm_min",
        "reference_approach_z_feed_mm_min",
        "move_timeout_s",
        "no_progress_timeout_s",
        "settle_tolerance_mm",
        "velocity_tolerance_mm_s",
        "probe_step_mm",
        "probe_lower_speed_mm_s",
        "probe_retract_mm",
        "probe_retract_speed_mm_s",
    }

    def _load_persisted_config(self, config: MachineRuntimeConfig) -> MachineRuntimeConfig:
        if self._settings_path is None or not self._settings_path.exists():
            return config
        try:
            payload = json.loads(self._settings_path.read_text())
        except Exception:
            return config
        if not isinstance(payload, dict):
            return config
        external_mapping = {
            "reference_prep_z_mm": "reference_prep_z_mm",
            "long_tool_change_clearance_z_mm": "long_tool_change_clearance_z_mm",
            "z_clearance_feed_mm_min": "z_clearance_feed_mm_min",
            "reference_approach_z_feed_mm_min": "reference_approach_z_feed_mm_min",
            "move_total_timeout_s": "move_timeout_s",
            "no_progress_timeout_s": "no_progress_timeout_s",
            "position_tolerance_mm": "settle_tolerance_mm",
            "velocity_tolerance_mm_s": "velocity_tolerance_mm_s",
            "reference_probe_step_mm": "probe_step_mm",
            "reference_probe_feed_mm_min": "probe_lower_speed_mm_s",
            "reference_probe_retract_mm": "probe_retract_mm",
            "reference_probe_retract_feed_mm_min": "probe_retract_speed_mm_s",
        }
        overrides = {field: payload[field] for field in self._MACHINE_SETTINGS_FIELDS if field in payload}
        for external, field in external_mapping.items():
            if external in payload:
                value = payload[external]
                overrides[field] = float(value) / 60.0 if external in {"reference_probe_feed_mm_min", "reference_probe_retract_feed_mm_min"} else value
        if (
            "long_tool_change_clearance_z_mm" not in payload
            and "long_tool_reference_prep_z_mm" in payload
        ):
            overrides["long_tool_change_clearance_z_mm"] = payload["long_tool_reference_prep_z_mm"]
        legacy_reference_z_feed = payload.get("reference_prep_z_feed_mm_min")
        if legacy_reference_z_feed is not None:
            if "z_clearance_feed_mm_min" not in payload:
                overrides["z_clearance_feed_mm_min"] = legacy_reference_z_feed
            if "reference_approach_z_feed_mm_min" not in payload:
                overrides["reference_approach_z_feed_mm_min"] = legacy_reference_z_feed
        if "move_timeout_s" in overrides:
            overrides.setdefault("move_minimum_timeout_s", overrides["move_timeout_s"])
        return replace(config, **overrides) if overrides else config

    def machine_settings(self) -> dict[str, float]:
        return {
            "reference_prep_z_mm": self.config.reference_prep_z_mm,
            "long_tool_change_clearance_z_mm": self.config.long_tool_change_clearance_z_mm,
            "z_clearance_feed_mm_min": self.config.z_clearance_feed_mm_min,
            "reference_approach_z_feed_mm_min": self.config.reference_approach_z_feed_mm_min,
            "move_total_timeout_s": self.config.move_timeout_s,
            "no_progress_timeout_s": self.config.no_progress_timeout_s,
            "position_tolerance_mm": self.config.settle_tolerance_mm,
            "velocity_tolerance_mm_s": self.config.velocity_tolerance_mm_s,
            "reference_probe_step_mm": self.config.probe_step_mm,
            "reference_probe_feed_mm_min": self.config.probe_lower_speed_mm_s * 60.0,
            "reference_probe_retract_mm": self.config.probe_retract_mm,
            "reference_probe_retract_feed_mm_min": self.config.probe_retract_speed_mm_s * 60.0,
        }

    def effective_probe_profile_payload(self, probe_config: dict[str, Any] | None = None) -> dict[str, Any]:
        overrides = dict(probe_config or {})
        profile = self._reference_probe_profile() if probe_config is None else self._resolve_probe_profile(probe_config)
        return profile.to_payload(overrides)

    def update_machine_settings(self, payload: dict[str, Any]) -> dict[str, float]:
        normalized_payload = dict(payload)
        if (
            normalized_payload.get("long_tool_change_clearance_z_mm") is None
            and normalized_payload.get("long_tool_reference_prep_z_mm") is not None
        ):
            normalized_payload["long_tool_change_clearance_z_mm"] = normalized_payload[
                "long_tool_reference_prep_z_mm"
            ]
        legacy_reference_z_feed = normalized_payload.get("reference_prep_z_feed_mm_min")
        if legacy_reference_z_feed is not None:
            if normalized_payload.get("z_clearance_feed_mm_min") is None:
                normalized_payload["z_clearance_feed_mm_min"] = legacy_reference_z_feed
            if normalized_payload.get("reference_approach_z_feed_mm_min") is None:
                normalized_payload["reference_approach_z_feed_mm_min"] = legacy_reference_z_feed
        mapping = {
            "reference_prep_z_mm": "reference_prep_z_mm",
            "long_tool_change_clearance_z_mm": "long_tool_change_clearance_z_mm",
            "z_clearance_feed_mm_min": "z_clearance_feed_mm_min",
            "reference_approach_z_feed_mm_min": "reference_approach_z_feed_mm_min",
            "move_total_timeout_s": "move_timeout_s",
            "no_progress_timeout_s": "no_progress_timeout_s",
            "position_tolerance_mm": "settle_tolerance_mm",
            "velocity_tolerance_mm_s": "velocity_tolerance_mm_s",
            "reference_probe_step_mm": "probe_step_mm",
            "reference_probe_feed_mm_min": "probe_lower_speed_mm_s",
            "reference_probe_retract_mm": "probe_retract_mm",
            "reference_probe_retract_feed_mm_min": "probe_retract_speed_mm_s",
        }
        overrides: dict[str, float] = {}
        for external, field in mapping.items():
            if external not in normalized_payload or normalized_payload[external] is None:
                continue
            value = float(normalized_payload[external])
            if external in {"reference_probe_feed_mm_min", "reference_probe_retract_feed_mm_min"}:
                value /= 60.0
            if value <= 0:
                raise MachineRuntimeError(f"{external} debe ser mayor que cero.")
            overrides[field] = value
        if "move_timeout_s" in overrides:
            overrides["move_minimum_timeout_s"] = overrides["move_timeout_s"]
        candidate = replace(self.config, **overrides)
        self._validate_reference_preparation_settings(candidate)
        self._validate_tool_change_clearance_settings(candidate)
        self.config = candidate
        if self._settings_path is not None:
            self._settings_path.parent.mkdir(parents=True, exist_ok=True)
            self._settings_path.write_text(json.dumps(self.machine_settings(), indent=2, sort_keys=True))
        return self.machine_settings()

    def reference_preparation_z(self, tool_reference_profile: str | None = None) -> float:
        """Return the single approach Z used at the reference point.

        ``tool_reference_profile`` remains accepted for API compatibility, but
        the physical tool profile no longer changes reference preparation.
        """
        target = float(self.config.reference_prep_z_mm)
        self._validate_reference_preparation_settings(self.config)
        if self._machine is not None:
            self._validate_machine_target(z=target, label="Z de aproximación al punto de referencia")
        return target

    def _validate_reference_preparation_settings(self, config: MachineRuntimeConfig) -> None:
        standard = float(config.reference_prep_z_mm)
        if self._machine is not None:
            if standard < self._machine.z_limits.minimum or standard > self._machine.z_limits.maximum:
                raise MachineRuntimeError(
                    "reference_prep_z_mm fuera de límites Klipper "
                    f"{self._machine.z_limits.minimum:.3f}..{self._machine.z_limits.maximum:.3f} mm."
                )

    def tool_change_clearance_z(self, tool_change_profile: str | None = None) -> float:
        profile = str(tool_change_profile or "standard")
        if profile == "standard":
            target = float(self.config.tool_change_clearance_z_mm)
        elif profile == "long_tool":
            target = float(self.config.long_tool_change_clearance_z_mm)
        else:
            raise MachineRuntimeError(f"Perfil de cambio de herramienta no soportado: {profile}.")
        self._validate_tool_change_clearance_settings(self.config)
        if self._machine is not None:
            self._validate_machine_target(z=target, label=f"Z de despeje del perfil de cambio {profile}")
        return target

    def _validate_tool_change_clearance_settings(self, config: MachineRuntimeConfig) -> None:
        standard = float(config.tool_change_clearance_z_mm)
        long_tool = float(config.long_tool_change_clearance_z_mm)
        if self._machine is not None:
            for field, value in (
                ("tool_change_clearance_z_mm", standard),
                ("long_tool_change_clearance_z_mm", long_tool),
            ):
                if value < self._machine.z_limits.minimum or value > self._machine.z_limits.maximum:
                    raise MachineRuntimeError(
                        f"{field} fuera de límites Klipper "
                        f"{self._machine.z_limits.minimum:.3f}..{self._machine.z_limits.maximum:.3f} mm."
                    )
        if config.tool_change_z_positive_up and long_tool < standard:
            raise MachineRuntimeError(
                "long_tool_change_clearance_z_mm debe ser igual o mayor que tool_change_clearance_z_mm "
                "porque aumentar Z aleja la herramienta de la superficie."
            )
        if not config.tool_change_z_positive_up and long_tool > standard:
            raise MachineRuntimeError(
                "long_tool_change_clearance_z_mm debe ser igual o menor que tool_change_clearance_z_mm "
                "porque disminuir Z aleja la herramienta de la superficie en esta máquina."
            )

    def _reference_probe_profile(self) -> ProbeMotionProfile:
        return ProbeMotionProfile(
            source="machine_reference_profile",
            probe_step_mm=float(self.config.probe_step_mm),
            probe_feed_mm_min=float(self.config.probe_lower_speed_mm_s) * 60.0,
            probe_speed_mm_s=float(self.config.probe_lower_speed_mm_s),
            retract_mm=float(self.config.probe_retract_mm),
            retract_feed_mm_min=float(self.config.probe_retract_speed_mm_s) * 60.0,
            retract_speed_mm_s=float(self.config.probe_retract_speed_mm_s),
            probe_open_stable_ms=float(self.config.probe_open_stable_ms),
            settle_tolerance_mm=float(self.config.settle_tolerance_mm),
        )

    def _resolve_probe_profile(self, probe_config: dict[str, Any] | None) -> ProbeMotionProfile:
        reference_profile = self._reference_probe_profile()
        if not probe_config:
            return reference_profile
        override_step = self._probe_config_float(probe_config, "probe_step_mm")
        override_feed = self._probe_config_float(probe_config, "probe_feed_mm_min")
        override_retract = self._probe_config_float(probe_config, "retract_mm")
        explicit_source = probe_config.get("source")
        source = str(explicit_source).strip() if explicit_source is not None else ""
        if not source:
            source = "map_override" if any(value is not None for value in (override_step, override_feed, override_retract)) else "machine_reference_profile"
        if source not in {"machine_reference_profile", "map_override"}:
            raise MachineRuntimeError("El perfil de sonda del mapa es inválido.")
        if source == "machine_reference_profile":
            return reference_profile
        if override_step is None or override_feed is None or override_retract is None:
            raise MachineRuntimeError("El override del mapa debe definir paso, velocidad y retracto de sonda.")
        return ProbeMotionProfile(
            source="map_override",
            probe_step_mm=float(override_step),
            probe_feed_mm_min=float(override_feed),
            probe_speed_mm_s=float(override_feed) / 60.0,
            retract_mm=float(override_retract),
            retract_feed_mm_min=reference_profile.retract_feed_mm_min,
            retract_speed_mm_s=reference_profile.retract_speed_mm_s,
            probe_open_stable_ms=reference_profile.probe_open_stable_ms,
            settle_tolerance_mm=reference_profile.settle_tolerance_mm,
        )

    def start(self) -> None:
        with self._lock:
            if self.config.mode is MachineMode.SIMULATED:
                self._state = MachineRuntimeState.READY_FOR_HOME
                self._event("info", "Runtime iniciado en modo SIMULADO.")
                return
            self._state = MachineRuntimeState.DISCONNECTED
        if self.config.auto_connect:
            self.connect()

    def stop(self) -> None:
        with self._lock:
            self._state = MachineRuntimeState.STOPPING
            self._manual_enabled = False
            self._serial_stop.set()
            telemetry = self._telemetry
            manager = self._connection_manager
        if telemetry is not None:
            telemetry.stop()
        if manager is not None:
            manager.stop()
        if self._telemetry_thread is not None:
            self._telemetry_thread.join(timeout=2.0)
        with self._lock:
            self._client = None
            self._machine = None
            self._telemetry = None
            self._driver = None
            self._connection_manager = None
            self._serial_thread = None
            self._jog = None
            self._manual = None
            self._state = MachineRuntimeState.DISCONNECTED
            self._event("info", "Runtime detenido.")

    def current_physical_session_id(self) -> str:
        with self._lock:
            started = self._started_at.isoformat()
            generation = self._serial_generation
        return f"{started}#serial-{generation}"

    def reconnect_arduino(self) -> dict[str, Any]:
        self._require_physical_ready()
        with self._lock:
            if self.config.mode is MachineMode.SIMULATED:
                raise MachineRuntimeError("Arduino no disponible en modo SIMULADO.")
            if self._state in {MachineRuntimeState.STOPPING, MachineRuntimeState.DISCONNECTED}:
                raise MachineRuntimeError("Runtime detenido; no se puede reconectar Arduino.")
            if self._active_operation is not None:
                raise MachineRuntimeError("No se puede reconectar Arduino durante una operación física activa.")
            manager = self._connection_manager
            if manager is None:
                raise MachineRuntimeError("Arduino no inicializado.")
            self._prepare_for_new_serial_session()
            self._event("warning", "Reconexión manual de Arduino solicitada; el movimiento permanece bloqueado.")
        manager.request_reconnect()
        return self.snapshot()

    def _prepare_for_new_serial_session(self) -> None:
        with self._lock:
            self._manual_enabled = False
            self._diagnostic_input_only = True
            self._ready_for_jog = False
            self._probe_requested = False
            self._previous_command = ControllerCommand()
            self._last_command = ControllerCommand()
            self._last_packet = None
            self._last_packet_at = None
            self._last_command_text = None
            self._last_probe_result = None
            self._last_probe_failure = None
            self._packet_sequence = 0
            self._probe_raw = False
            self._probe_filtered = False
            self._probe_filtered_since = None
            self._probe_raw_since = None

    def _build_connection_manager(self) -> ArduinoConnectionManager:
        return ArduinoConnectionManager(
            configured_port=self.config.serial_port,
            baudrate=self.config.serial_baudrate,
            startup_delay=self.config.serial_startup_delay_s,
            driver_factory=self._serial_factory,
            on_packet=self._handle_controller_packet_from_manager,
            on_session_started=self._on_serial_session_started,
            on_session_lost=self._on_serial_session_lost,
            on_state_change=self._on_connection_state,
        )

    def _on_connection_state(self, snapshot: dict[str, object]) -> None:
        state = str(snapshot.get("state") or ArduinoConnectionState.DISCONNECTED)
        last_error = snapshot.get("last_error")
        with self._lock:
            manager = self._connection_manager
            if manager is not None:
                self._driver = manager.driver
                self._serial_thread = manager.thread
            if last_error:
                self._last_error = str(last_error)
            if state == ArduinoConnectionState.CONNECTED:
                if self._client is not None and self._state in {MachineRuntimeState.CONNECTING, MachineRuntimeState.DEGRADED}:
                    self._state = MachineRuntimeState.DIAGNOSTIC
            elif state in {ArduinoConnectionState.DEGRADED, ArduinoConnectionState.RETRY_WAIT}:
                if self._client is not None and self._state not in {MachineRuntimeState.STOPPING, MachineRuntimeState.DISCONNECTED, MachineRuntimeState.ERROR}:
                    self._state = MachineRuntimeState.DEGRADED

    def _on_serial_session_started(self, generation: int, identity: UsbIdentity | None) -> None:
        self._prepare_for_new_serial_session()
        with self._lock:
            self._serial_generation = generation
            manager = self._connection_manager
            if manager is not None:
                self._driver = manager.driver
                self._serial_thread = manager.thread
            if self._client is not None:
                self._state = MachineRuntimeState.DIAGNOSTIC
            self._last_error = None
            if generation > 1:
                message = "Arduino reconectado en modo diagnóstico; el movimiento sigue bloqueado hasta nueva habilitación explícita."
            else:
                message = "Arduino conectado en modo diagnóstico; el movimiento sigue bloqueado hasta nueva habilitación explícita."
            if identity is not None and not identity.exact:
                message += " Sin número de serie USB; la reconexión automática queda limitada al puerto configurado."
            self._event("info", message)

    def _on_serial_session_lost(self, message: str) -> None:
        self._prepare_for_new_serial_session()
        with self._lock:
            self._counters.disconnects += 1
            self._driver = None
            if self._client is not None and self._state not in {MachineRuntimeState.STOPPING, MachineRuntimeState.DISCONNECTED, MachineRuntimeState.ERROR}:
                self._state = MachineRuntimeState.DEGRADED
            self._last_error = message
            self._event("warning", f"Arduino degradado: {message}")

    def _handle_controller_packet_from_manager(self, packet: ControllerPacket, generation: int) -> None:
        with self._lock:
            if generation != self._serial_generation:
                return
        command = self._mapper.map(packet)
        self._handle_controller_packet(packet, command)

    def connect(self) -> dict[str, Any]:
        telemetry_thread: threading.Thread | None = None
        with self._lock:
            if self.config.mode is MachineMode.SIMULATED:
                self._state = MachineRuntimeState.READY_FOR_HOME
                self._event("info", "Conexión simulada confirmada.")
                return self.snapshot()
            self._require_physical_config()
            if self._client is not None:
                return self.snapshot()
            self._state = MachineRuntimeState.CONNECTING
        try:
            assert self.config.moonraker_url is not None
            assert self.config.moonraker_ws is not None
            client = self._client_factory(self.config.moonraker_url, timeout=self.config.moonraker_request_timeout_s)
            server_info = client.get_server_info()
            klippy_state = str(server_info.get("klippy_state") or "unknown")
            if klippy_state != "ready":
                raise MachineRuntimeError("Klipper no está ready.")
            machine = self._discovery(client)
            self._attach_telemetry_tracking(machine)
            telemetry = self._telemetry_factory(self.config.moonraker_ws, machine)
            if hasattr(telemetry, "set_snapshot_callback"):
                telemetry.set_snapshot_callback(self._on_telemetry_state)
            elif hasattr(telemetry, "set_state_callback"):
                telemetry.set_state_callback(self._on_telemetry_state)
            connection_manager = self._build_connection_manager()
            telemetry_thread = threading.Thread(target=_run_telemetry, args=(telemetry, self._telemetry_failures), daemon=True)
            with self._lock:
                self._client = client
                self._machine = machine
                self._telemetry = telemetry
                self._connection_manager = connection_manager
                self._jog = JogController(client, machine)
                self._manual = ManualJogController(self._jog, mode=JogMode.FINE)
                self._state = MachineRuntimeState.DIAGNOSTIC
                self._diagnostic_input_only = True
                self._serial_stop.clear()
                self._reset_live_probe_stability()
                self._telemetry_thread = telemetry_thread
                self._last_http_observation_at = time.monotonic()
                self._last_http_error = None
                self._last_klippy_state = klippy_state
                self._last_websocket_error = None
            telemetry_thread.start()
            connection_manager.start()
            deadline = time.monotonic() + max(self.config.serial_startup_delay_s + 1.5, 1.5)
            snapshot = connection_manager.snapshot()
            while time.monotonic() < deadline:
                snapshot = connection_manager.snapshot()
                with self._lock:
                    self._driver = connection_manager.driver
                    self._serial_thread = connection_manager.thread
                if snapshot["state"] in {ArduinoConnectionState.CONNECTED, ArduinoConnectionState.DEGRADED, ArduinoConnectionState.RETRY_WAIT}:
                    break
                time.sleep(0.05)
            if snapshot["state"] == ArduinoConnectionState.CONNECTED:
                with self._lock:
                    self._last_telemetry_at = time.monotonic()
                    self._event("info", "Moonraker, Klipper y Arduino conectados en modo diagnóstico.")
                return self.snapshot()
            with self._lock:
                self._state = MachineRuntimeState.DEGRADED
                self._last_error = str(snapshot.get("last_error") or "Arduino aún no disponible; Moonraker y Klipper permanecen activos mientras continúa la reconexión serial.")
                self._event("warning", "Moonraker y Klipper conectados; Arduino aún no disponible. Se mantiene reconexión serial sin habilitar movimiento.")
            return self.snapshot()
        except Exception as error:
            manager = None
            telemetry = None
            thread_to_join = telemetry_thread
            with self._lock:
                manager = self._connection_manager
                telemetry = self._telemetry
                if self._telemetry_thread is not None:
                    thread_to_join = self._telemetry_thread
            if manager is not None:
                manager.stop()
            if telemetry is not None:
                telemetry.stop()
            if thread_to_join is not None:
                thread_to_join.join(timeout=2.0)
            with self._lock:
                self._connection_manager = None
                self._driver = None
                self._serial_thread = None
                self._client = None
                self._machine = None
                self._telemetry = None
                self._jog = None
                self._manual = None
                self._telemetry_thread = None
                self._state = MachineRuntimeState.ERROR
                self._last_error = str(error)
                self._last_http_error = str(error)
                self._event("error", str(error))
            raise

    def disconnect(self) -> dict[str, Any]:
        self.stop()
        return self.snapshot()

    def reset_physical_session(self) -> dict[str, Any]:
        with self._lock:
            active_operation = self._active_operation
        if active_operation is not None or self._movement_lock.locked():
            raise MachineRuntimeError(
                "No se puede reiniciar la sesión física mientras una operación o movimiento conserva el control de la máquina."
            )
        lock_acquired = self._movement_lock.acquire(blocking=False)
        if not lock_acquired:
            raise MachineRuntimeError("No se pudo reiniciar: movement_lock sigue ocupado.")
        try:
            self.stop()
        finally:
            self._movement_lock.release()
        with self._lock:
            self._active_operation = None
            self._manual_enabled = False
            self._diagnostic_input_only = True
            self._ready_for_jog = False
            self._previous_command = ControllerCommand()
            self._last_packet = None
            self._last_command = ControllerCommand()
            self._last_packet_at = None
            self._last_command_text = None
            self._last_movement = None
            self._last_error = None
            self._last_probe_result = None
            self._probe_requested = False
            self._initialization_steps = []
            self._serial_stop = threading.Event()
            self._state = MachineRuntimeState.DISCONNECTED
            self._event("warning", "Sesión física reiniciada; Arduino desconectado y paquetes anteriores invalidados.")
        return self.snapshot()

    def set_diagnostic_mode(self, enabled: bool) -> dict[str, Any]:
        with self._lock:
            self._diagnostic_input_only = enabled
            if enabled:
                self._manual_enabled = False
                if self.config.mode is MachineMode.PHYSICAL and self._client is not None:
                    self._state = MachineRuntimeState.DIAGNOSTIC
            self._event("info", "Modo diagnóstico activado." if enabled else "Modo diagnóstico desactivado.")
        return self.snapshot()

    def enable_manual_control(self, enabled: bool) -> dict[str, Any]:
        self._require_physical_ready()
        with self._lock:
            if enabled:
                self._assert_safety_for_motion()
                self._manual_enabled = True
                self._diagnostic_input_only = False
                self._state = MachineRuntimeState.WAITING_FOR_XY_REFERENCE
                self._event("info", "Control manual habilitado.")
            else:
                self._manual_enabled = False
                self._state = MachineRuntimeState.READY_FOR_HOME
                self._event("info", "Control manual deshabilitado.")
        return self.snapshot()

    def change_jog_mode(self, mode: str) -> dict[str, Any]:
        self._require_physical_ready()
        selected = JogMode(mode.lower())
        with self._lock:
            if self._manual is None:
                raise MachineRuntimeError("Control manual no inicializado.")
            self._manual.set_mode(selected)
            self._event("info", f"Modo de jog cambiado a {selected.name}.")
        return self.snapshot()

    def initialize(self, target_z_mm: float | None = None) -> dict[str, Any]:
        """Prepare the machine in strict HOME → configured absolute Z → center order."""
        self._require_physical_ready()
        if not self._movement_lock.acquire(blocking=False):
            raise MachineRuntimeError("Ya hay un movimiento u operación física activa.")
        context = self._begin_operation_context("preparation")
        started = time.monotonic()
        center_x: float | None = None
        center_y: float | None = None
        configured_z = self.config.reference_prep_z_mm if target_z_mm is None else float(target_z_mm)
        try:
            with self._lock:
                self._state = MachineRuntimeState.HOMING
                self._manual_enabled = False
                self._diagnostic_input_only = True
                self._initialization_steps = []
            self._step("PREPARATION_HOME_START", "ok", "Homing solicitado antes de la preparación.")
            self._log_preparation_transition("PREPARATION_HOME_START", target_z=configured_z, center_x=center_x, center_y=center_y, started=started)
            self._step("verificar_modo_fisico", "ok", "Modo físico confirmado.")
            self._assert_safety_for_connection()
            self._step("verificar_conexion", "ok", "Moonraker y Klipper están conectados.")
            self._assert_serial_thread_visible()
            self._wait_for_serial_recent()
            self._step("verificar_arduino", "ok", "Arduino con paquetes válidos recientes.")

            self._send_script("G28", label="homing")
            self._step("homing_solicitado", "ok", "G28 enviado; la finalización se confirma por toolhead.homed_axes y velocidad cero.")
            self._wait_for_homing({"x", "y", "z"})
            # This is a mandatory post-home HTTP observation, not cached state.
            self._refresh_machine()
            machine = self._machine
            if machine is None:
                raise MachineRuntimeError("No hay estado de máquina descubierto.")
            homed_snapshot = machine.get_motion_snapshot()
            missing = sorted(axis for axis in ("x", "y", "z") if not machine.axis_is_homed(axis))
            if missing:
                raise MachineRuntimeError("Homing incompleto; faltan ejes: " + ", ".join(axis.upper() for axis in missing) + ".")
            with self._lock:
                self._state = MachineRuntimeState.HOMED
            self._step("homing_confirmado", "ok", f"Klipper reporta homed_axes={machine.homed_axes}.")
            self._step("PREPARATION_HOME_DONE", "ok", f"Home confirmado con Z observada={float(homed_snapshot["z"]):.3f} mm.")
            self._log_preparation_transition("PREPARATION_HOME_DONE", target_z=configured_z, center_x=center_x, center_y=center_y, observed=homed_snapshot, started=started)

            # preparation_z is an absolute machine coordinate.  It is intentionally
            # independent of mesh safe-Z, probing clearance, retract and PCB reference Z.
            self._validate_machine_target(z=configured_z, label="Z de preparación")
            center_x = (machine.x_limits.minimum + machine.x_limits.maximum) / 2.0
            center_y = (machine.y_limits.minimum + machine.y_limits.maximum) / 2.0
            self._validate_machine_target(x=center_x, y=center_y, label="centro de máquina")
            self._step("actualizar_limites", "ok", f"Límites Klipper X={machine.x_limits.minimum:.3f}..{machine.x_limits.maximum:.3f} Y={machine.y_limits.minimum:.3f}..{machine.y_limits.maximum:.3f} Z={machine.z_limits.minimum:.3f}..{machine.z_limits.maximum:.3f}.")
            self._step("calcular_centro", "ok", f"Centro real calculado X={center_x:.3f} Y={center_y:.3f}.")

            with self._lock:
                self._state = MachineRuntimeState.MOVING_TO_SAFE_Z
            self._step("PREPARATION_Z_START", "ok", f"Moviendo únicamente Z a la coordenada absoluta configurada {configured_z:.3f} mm.")
            self._log_preparation_transition("PREPARATION_Z_START", target_z=configured_z, center_x=center_x, center_y=center_y, observed=homed_snapshot, started=started)
            self._move_absolute(
                z=configured_z,
                label="z_preparacion_referencia",
                feed_mm_min=self._reference_target_z_feed(
                    current_z=float(homed_snapshot["z"]),
                    target_z=configured_z,
                ),
            )
            self._refresh_machine()
            z_snapshot = machine.get_motion_snapshot()
            if abs(float(z_snapshot["z"]) - configured_z) > self.config.settle_tolerance_mm:
                raise MachineRuntimeError(f"Z de preparación no alcanzada: objetivo {configured_z:.3f} mm, observada {float(z_snapshot["z"]):.3f} mm.")
            self._step("z_segura_confirmada", "ok", f"Z de preparación alcanzada: {configured_z:.3f} mm.")
            self._step("PREPARATION_Z_DONE", "ok", f"Z observada={float(z_snapshot["z"]):.3f} mm; X/Y continúan bloqueados hasta esta confirmación.")
            self._log_preparation_transition("PREPARATION_Z_DONE", target_z=configured_z, center_x=center_x, center_y=center_y, observed=z_snapshot, started=started)

            with self._lock:
                self._state = MachineRuntimeState.MOVING_TO_CENTER
            self._step("PREPARATION_CENTER_START", "ok", f"Moviendo X/Y al centro X={center_x:.3f} Y={center_y:.3f}.")
            self._log_preparation_transition("PREPARATION_CENTER_START", target_z=configured_z, center_x=center_x, center_y=center_y, observed=z_snapshot, started=started)
            self._move_absolute(
                x=center_x,
                y=center_y,
                label="xy_centro",
                feed_mm_min=AUXILIARY_REFERENCE_XY_FEED_MM_MIN,
            )
            self._refresh_machine()
            center_snapshot = machine.get_motion_snapshot()
            if abs(float(center_snapshot["x"]) - center_x) > self.config.settle_tolerance_mm or abs(float(center_snapshot["y"]) - center_y) > self.config.settle_tolerance_mm:
                raise MachineRuntimeError(f"Centro no alcanzado: objetivo X={center_x:.3f} Y={center_y:.3f}; observada X={float(center_snapshot["x"]):.3f} Y={float(center_snapshot["y"]):.3f}.")
            self._step("centro_confirmado", "ok", f"Máquina preparada en X={center_x:.3f} Y={center_y:.3f} Z={configured_z:.3f} mm.")
            self._step("PREPARATION_CENTER_DONE", "ok", f"Centro confirmado X={float(center_snapshot["x"]):.3f} Y={float(center_snapshot["y"]):.3f}.")
            self._log_preparation_transition("PREPARATION_CENTER_DONE", target_z=configured_z, center_x=center_x, center_y=center_y, observed=center_snapshot, started=started)
            with self._lock:
                self._state = MachineRuntimeState.WAITING_FOR_XY_REFERENCE
                self._event("info", "Inicialización física completada; posicione X/Y del origen 0,0 y arme la referencia.")
            return self.snapshot()
        except Exception as error:
            self._log_preparation_transition("PREPARATION_FAILED", target_z=configured_z, center_x=center_x, center_y=center_y, error=str(error), started=started)
            with self._lock:
                cancelled = str(error) == "Preparación cancelada por el operador."
                self._state = MachineRuntimeState.CANCELLED if cancelled else MachineRuntimeState.ERROR
                self._last_error = str(error)
                self._step("PREPARATION_CANCELLED" if cancelled else "PREPARATION_FAILED", "warning" if cancelled else "error", str(error))
                self._step("abortar", "warning" if cancelled else "error", str(error))
                self._event("warning" if cancelled else "error", str(error))
            raise
        finally:
            self._finish_operation_context(context)
            self._movement_lock.release()

    def move_to_tool_change_position(self, tool_change_profile: str = "standard") -> dict[str, Any]:
        self._require_physical_ready()
        if not self._movement_lock.acquire(blocking=False):
            raise MachineRuntimeError("Ya hay un movimiento u operación física activa.")
        context = self._begin_operation_context("tool_change")
        try:
            with self._lock:
                self._manual_enabled = False
                self._diagnostic_input_only = True
                self._state = MachineRuntimeState.MOVING_TO_SAFE_Z
            self._assert_safety_for_motion()
            self._refresh_machine()
            if self._telemetry_status() != "LIVE":
                raise MachineRuntimeError("La telemetría Moonraker debe estar LIVE antes de mover a cambio de herramienta.")
            machine = self._machine
            if machine is None:
                raise MachineRuntimeError("No hay estado de máquina descubierto.")
            target_x = float(self.config.tool_change_x_mm)
            target_y = float(self.config.tool_change_y_mm)
            work_z = float(self.config.tool_change_work_z_mm)
            self._validate_machine_target(x=target_x, y=target_y, label="posición XY de cambio de herramienta")
            self._validate_machine_target(z=work_z, label="Z de trabajo de cambio de herramienta")
            frame_snapshot = machine.get_motion_snapshot()
            current_gcode, _frame_age = self._frame_position(frame_snapshot, "gcode_position", label="tool_change_clearance")
            current_gcode_z = float(current_gcode["z"])
            configured_clearance = self.tool_change_clearance_z(tool_change_profile)
            clearance_target = self._tool_change_clearance_target(
                current_gcode_z,
                tool_change_profile=tool_change_profile,
            )
            self._validate_machine_target(z=clearance_target, label="Z de despeje para cambio de herramienta")
            if abs(clearance_target - current_gcode_z) > self.config.settle_tolerance_mm:
                self._move_absolute(
                    z=clearance_target,
                    label="tool_change_clearance_z",
                    feed_mm_min=self.config.z_clearance_feed_mm_min,
                    coordinate_frame="gcode_position",
                )
            with self._lock:
                self._state = MachineRuntimeState.MOVING_TO_CENTER
            self._move_absolute(
                x=target_x,
                y=target_y,
                label="tool_change_xy",
                feed_mm_min=AUXILIARY_DEFAULT_TRAVEL_FEED_MM_MIN,
                coordinate_frame="gcode_position",
            )
            self._refresh_machine_best_effort()
            xy_snapshot = self._machine.get_motion_snapshot() if self._machine is not None else frame_snapshot
            current_after_xy, _age_after_xy = self._frame_position(xy_snapshot, "gcode_position", label="tool_change_work_z")
            if abs(float(current_after_xy["z"]) - work_z) > self.config.settle_tolerance_mm:
                self._move_absolute(
                    z=work_z,
                    label="tool_change_work_z",
                    feed_mm_min=self.config.tool_change_z_feed_mm_min,
                    coordinate_frame="gcode_position",
                )
            with self._lock:
                self._state = MachineRuntimeState.WAITING_FOR_XY_REFERENCE
                self._event(
                    "info",
                    "Máquina en posición segura para cambio de herramienta "
                    f"con perfil {tool_change_profile} y despeje configurado {configured_clearance:.3f} mm.",
                )
            snapshot = self.snapshot()
            snapshot["tool_change_move"] = {
                "profile": tool_change_profile,
                "configured_clearance_z_mm": configured_clearance,
                "effective_clearance_z_mm": clearance_target,
            }
            return snapshot
        except Exception as error:
            with self._lock:
                self._state = MachineRuntimeState.ERROR
                self._last_error = str(error)
                self._event("error", str(error))
            raise
        finally:
            self._finish_operation_context(context)
            self._movement_lock.release()

    def go_to_reference_point(
        self,
        *,
        reference_x: float,
        reference_y: float,
        tool_reference_profile: str = "standard",
    ) -> dict[str, Any]:
        """Move to a saved CNC reference point without probing or changing it."""
        self._require_physical_ready()
        if not self._movement_lock.acquire(blocking=False):
            raise MachineRuntimeError("Ya hay un movimiento u operación física activa.")
        with self._lock:
            preserve_reference_captured = self._state is MachineRuntimeState.REFERENCE_CAPTURED
        context = self._begin_operation_context("reference_move")
        preparation_z = self.reference_preparation_z()
        try:
            with self._lock:
                self._manual_enabled = False
                self._diagnostic_input_only = True
                self._state = MachineRuntimeState.MOVING_TO_SAFE_Z
            self._assert_safety_for_motion()
            self._refresh_machine()
            if self._telemetry_status() != "LIVE":
                raise MachineRuntimeError("La telemetría Moonraker debe estar LIVE antes de mover al punto de referencia.")
            machine = self._machine
            if machine is None:
                raise MachineRuntimeError("No hay estado de máquina descubierto.")
            missing = sorted(axis for axis in ("x", "y", "z") if not machine.axis_is_homed(axis))
            if missing:
                raise MachineRuntimeError("Falta homing de ejes: " + ", ".join(axis.upper() for axis in missing) + ".")
            probe = self.get_live_probe_state(require_fresh=True)
            if probe["filtered_triggered"]:
                raise MachineRuntimeError("No se puede mover al punto de referencia: la sonda está TRIGGERED.")
            self._validate_machine_target(z=preparation_z, label="Z de preparación de referencia")
            self._validate_machine_target(x=float(reference_x), y=float(reference_y), label="punto de referencia CNC")
            current_snapshot = machine.get_motion_snapshot()
            self._event("info", f"REFERENCE_MOVE_SAFE_Z: moviendo Z a preparación {preparation_z:.3f} mm.")
            self._move_absolute(
                z=preparation_z,
                label="reference_move_safe_z",
                feed_mm_min=self._reference_target_z_feed(
                    current_z=float(current_snapshot["z"]),
                    target_z=preparation_z,
                ),
            )
            with self._lock:
                self._state = MachineRuntimeState.MOVING_TO_CENTER
            self._event("info", f"REFERENCE_MOVE_XY: moviendo a X={float(reference_x):.3f} Y={float(reference_y):.3f} mm.")
            self._move_absolute(
                x=float(reference_x),
                y=float(reference_y),
                label="reference_move_xy",
                feed_mm_min=AUXILIARY_REFERENCE_XY_FEED_MM_MIN,
            )
            with self._lock:
                self._state = (
                    MachineRuntimeState.REFERENCE_CAPTURED
                    if preserve_reference_captured
                    else MachineRuntimeState.WAITING_FOR_XY_REFERENCE
                )
                self._event("info", "REFERENCE_MOVE_COMPLETE: máquina ubicada en el punto de referencia.")
            return {
                "accepted": True,
                "reference_x": float(reference_x),
                "reference_y": float(reference_y),
                "preparation_z": preparation_z,
                "tool_reference_profile": tool_reference_profile,
                "final_state": "REFERENCE_MOVE_COMPLETE",
                "message": "Máquina ubicada en el punto de referencia.",
            }
        except Exception as error:
            with self._lock:
                cancelled = "cancelada por el operador" in str(error).lower()
                self._state = MachineRuntimeState.CANCELLED if cancelled else MachineRuntimeState.ERROR
                self._last_error = str(error)
                self._event("warning" if cancelled else "error", f"{'REFERENCE_MOVE_CANCELLED' if cancelled else 'REFERENCE_MOVE_FAILED'}: {error}")
            raise
        finally:
            self._finish_operation_context(context)
            self._movement_lock.release()

    def move_from_tool_change_to_reference_point(
        self,
        *,
        reference_x: float,
        reference_y: float,
        tool_change_profile: str = "standard",
        progress_callback: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Leave the tool-change station safely, then approach the reference.

        The installed (incoming) tool determines only the clearance used before
        XY travel.  Once XY has reached the saved reference, every tool uses the
        same reference preparation Z before probing.
        """
        self._require_physical_ready()
        if not self._movement_lock.acquire(blocking=False):
            raise MachineRuntimeError("Ya hay un movimiento u operación física activa.")
        context = self._begin_operation_context("tool_change_to_reference")
        try:
            with self._lock:
                self._manual_enabled = False
                self._diagnostic_input_only = True
                self._state = MachineRuntimeState.MOVING_TO_SAFE_Z
            self._assert_safety_for_motion()
            self._refresh_machine()
            if self._telemetry_status() != "LIVE":
                raise MachineRuntimeError(
                    "La telemetría Moonraker debe estar LIVE antes de salir del cambio de herramienta."
                )
            machine = self._machine
            if machine is None:
                raise MachineRuntimeError("No hay estado de máquina descubierto.")
            missing = sorted(axis for axis in ("x", "y", "z") if not machine.axis_is_homed(axis))
            if missing:
                raise MachineRuntimeError(
                    "Falta homing de ejes: " + ", ".join(axis.upper() for axis in missing) + "."
                )
            probe = self.get_live_probe_state(require_fresh=True)
            if probe["filtered_triggered"]:
                raise MachineRuntimeError(
                    "No se puede salir del cambio de herramienta: la sonda está TRIGGERED."
                )

            configured_clearance = self.tool_change_clearance_z(tool_change_profile)
            preparation_z = self.reference_preparation_z()
            self._validate_machine_target(
                x=float(reference_x),
                y=float(reference_y),
                label="punto de referencia CNC",
            )
            frame_snapshot = machine.get_motion_snapshot()
            current_gcode, _frame_age = self._frame_position(
                frame_snapshot,
                "gcode_position",
                label="tool_change_exit_clearance",
            )
            current_gcode_z = float(current_gcode["z"])
            clearance_target = self._tool_change_clearance_target(
                current_gcode_z,
                tool_change_profile=tool_change_profile,
            )
            self._validate_machine_target(
                z=clearance_target,
                label="Z de despeje para salir del cambio de herramienta",
            )
            self._notify_transition_progress(
                progress_callback,
                "RETURNING_TO_REFERENCE_SAFE_Z",
                target_z_mm=clearance_target,
                feed_mm_min=self.config.z_clearance_feed_mm_min,
            )
            if abs(clearance_target - current_gcode_z) > self.config.settle_tolerance_mm:
                self._event(
                    "info",
                    f"TOOL_CHANGE_EXIT_CLEARANCE: perfil {tool_change_profile}, Z={clearance_target:.3f} mm.",
                )
                self._move_absolute(
                    z=clearance_target,
                    label="tool_change_exit_clearance_z",
                    feed_mm_min=self.config.z_clearance_feed_mm_min,
                    coordinate_frame="gcode_position",
                )
            self._notify_transition_progress(
                progress_callback,
                "RETURNING_TO_REFERENCE_XY",
                target_x_mm=float(reference_x),
                target_y_mm=float(reference_y),
                clearance_z_mm=clearance_target,
                feed_mm_min=AUXILIARY_REFERENCE_XY_FEED_MM_MIN,
            )
            with self._lock:
                self._state = MachineRuntimeState.MOVING_TO_CENTER
            self._event(
                "info",
                "TOOL_CHANGE_TO_REFERENCE_XY: "
                f"moviendo a X={float(reference_x):.3f} Y={float(reference_y):.3f} mm.",
            )
            self._move_absolute(
                x=float(reference_x),
                y=float(reference_y),
                label="tool_change_to_reference_xy",
                feed_mm_min=AUXILIARY_REFERENCE_XY_FEED_MM_MIN,
            )
            self._notify_transition_progress(
                progress_callback,
                "MOVING_TO_REFERENCE",
                target_z_mm=preparation_z,
                feed_mm_min=self.config.reference_approach_z_feed_mm_min,
            )
            self._event(
                "info",
                f"REFERENCE_APPROACH_Z: moviendo Z a preparación {preparation_z:.3f} mm.",
            )
            self._move_absolute(
                z=preparation_z,
                label="reference_move_prep_z",
                feed_mm_min=self.config.reference_approach_z_feed_mm_min,
            )
            self._notify_transition_progress(
                progress_callback,
                "REFERENCE_APPROACH_CONFIRMED",
                target_z_mm=preparation_z,
                feed_mm_min=self.config.reference_approach_z_feed_mm_min,
            )
            with self._lock:
                self._state = MachineRuntimeState.WAITING_FOR_XY_REFERENCE
                self._event(
                    "info",
                    "TOOL_CHANGE_REFERENCE_READY: herramienta ubicada para sondear la referencia.",
                )
            return {
                "accepted": True,
                "reference_x": float(reference_x),
                "reference_y": float(reference_y),
                "tool_change_profile": tool_change_profile,
                "tool_change_clearance_z_mm": configured_clearance,
                "effective_clearance_z_mm": clearance_target,
                "preparation_z": preparation_z,
                "z_clearance_feed_mm_min": self.config.z_clearance_feed_mm_min,
                "reference_approach_z_feed_mm_min": self.config.reference_approach_z_feed_mm_min,
                "final_state": "TOOL_CHANGE_REFERENCE_READY",
                "message": "Herramienta ubicada en el punto de referencia y lista para sondeo.",
            }
        except Exception as error:
            with self._lock:
                cancelled = "cancelada por el operador" in str(error).lower()
                self._state = MachineRuntimeState.CANCELLED if cancelled else MachineRuntimeState.ERROR
                self._last_error = str(error)
                self._event(
                    "warning" if cancelled else "error",
                    f"{'TOOL_CHANGE_REFERENCE_CANCELLED' if cancelled else 'TOOL_CHANGE_REFERENCE_FAILED'}: {error}",
                )
            raise
        finally:
            self._finish_operation_context(context)
            self._movement_lock.release()

    def request_probe(self) -> dict[str, Any]:
        self._require_physical_ready()
        with self._lock:
            if self._state not in {MachineRuntimeState.WAITING_FOR_XY_REFERENCE, MachineRuntimeState.REFERENCE_ARMED, MachineRuntimeState.REFERENCE_CAPTURED}:
                raise MachineRuntimeError("La referencia solo puede armarse después de homing, Z segura y movimiento al centro.")
            self._probe_requested = True
            self._manual_enabled = False
            self._diagnostic_input_only = True
            self._state = MachineRuntimeState.REFERENCE_ARMED
            self._event("info", "REFERENCE_ARMED: pulse el botón externo para sondear la referencia.")
        return self.snapshot()

    def confirm_probe(self) -> dict[str, Any]:
        self._require_physical_ready()
        if not self._movement_lock.acquire(blocking=False):
            raise MachineRuntimeError("Ya hay un movimiento u operación física activa.")
        context = self._begin_operation_context("reference_z")
        try:
            with self._lock:
                if self._state in {MachineRuntimeState.WAITING_FOR_XY_REFERENCE, MachineRuntimeState.REFERENCE_CAPTURED}:
                    self._probe_requested = True
                    self._state = MachineRuntimeState.REFERENCE_ARMED
                    self._event("info", "REFERENCE_ARMED: sondeo iniciado desde la pantalla.")
                elif self._state != MachineRuntimeState.REFERENCE_ARMED:
                    raise MachineRuntimeError("La referencia debe estar armada antes de sondear.")
                if not self._probe_requested:
                    self._probe_requested = True
                self._manual_enabled = False
                self._diagnostic_input_only = True
                self._state = MachineRuntimeState.PROBING_REFERENCE
            probe = self._perform_probe_descent(label="reference_probe", profile=self._reference_probe_profile())
            with self._lock:
                self._last_probe_result = probe
                self._probe_requested = False
                self._state = MachineRuntimeState.REFERENCE_CAPTURED
                self._event("info", f"Sonda de referencia capturada X={probe.x_mm:.3f} Y={probe.y_mm:.3f} Z={probe.z_mm:.3f}.")
            return self.snapshot()
        except Exception as error:
            with self._lock:
                self._state = MachineRuntimeState.ERROR
                self._last_error = str(error)
                self._event("error", str(error))
            raise
        finally:
            self._finish_operation_context(context)
            self._movement_lock.release()

    def probe_mesh_point(self, point: dict[str, Any], probe_config: dict[str, Any] | None = None, progress_callback: Callable[[str, dict[str, Any]], None] | None = None) -> dict[str, Any]:
        self._require_physical_ready()
        if not self._movement_lock.acquire(blocking=False):
            raise MachineRuntimeError("Ya hay un movimiento u operación física activa.")
        context = self._begin_operation_context("mesh")
        started = time.monotonic()
        try:
            with self._lock:
                self._state = MachineRuntimeState.MESH_PROBING
                self._manual_enabled = False
                self._diagnostic_input_only = True
            self._assert_safety_for_motion()
            self._refresh_machine()
            machine = self._machine
            if machine is None:
                raise MachineRuntimeError("No hay estado de máquina descubierto.")
            start_snapshot = machine.get_motion_snapshot()
            safe_z = self._mesh_safe_z(machine, probe_config=probe_config)
            self._notify_probe_progress(progress_callback, "POINT_MOVE_SAFE_Z", safe_z_mm=safe_z, initial_z_mm=float(start_snapshot["z"]))
            self._move_absolute(
                z=safe_z,
                label="mesh_z_segura",
                feed_mm_min=AUXILIARY_DEFAULT_TRAVEL_FEED_MM_MIN,
            )
            safe_observed = machine.get_motion_snapshot()
            self._notify_probe_progress(progress_callback, "POINT_CONFIRM_SAFE_Z", safe_z_mm=safe_z, observed_z_mm=float(safe_observed["z"]))
            with self._lock:
                xy_sequence = self._packet_sequence
            self._notify_probe_progress(progress_callback, "POINT_MOVE_XY", x_mm=float(point["x_machine"]), y_mm=float(point["y_machine"]), safe_z_mm=safe_z, observed_z_mm=float(safe_observed["z"]))
            self._move_absolute(
                x=float(point["x_machine"]),
                y=float(point["y_machine"]),
                label=f"mesh_xy_{point['index']}",
                feed_mm_min=AUXILIARY_DEFAULT_TRAVEL_FEED_MM_MIN,
            )
            xy_observed = machine.get_motion_snapshot()
            self._notify_probe_progress(progress_callback, "POINT_CONFIRM_XY", x_mm=float(xy_observed["x"]), y_mm=float(xy_observed["y"]), observed_z_mm=float(xy_observed["z"]))
            probe = self._perform_probe_descent(
                label=f"mesh_probe_{point['index']}",
                profile=self._resolve_probe_profile(probe_config),
                open_after_sequence=xy_sequence,
                progress_callback=progress_callback,
            )
            with self._lock:
                self._state = MachineRuntimeState.MESH_READY
            return {
                "index": point["index"],
                "z_measured": probe.z_mm,
                "duration_s": time.monotonic() - started,
                "probe": probe.__dict__,
            }
        except Exception as error:
            with self._lock:
                self._state = MachineRuntimeState.ERROR
                self._last_error = str(error)
                self._event("error", str(error))
            raise
        finally:
            self._finish_operation_context(context)
            self._movement_lock.release()

    def _reset_live_probe_stability(self) -> None:
        """Reset timestamps once for a new serial connection, not per packet."""
        with self._lock:
            self._probe_raw_since = None
            self._probe_filtered_since = None

    def get_live_probe_state(self, *, require_fresh: bool = False, require_stable: bool = False) -> dict[str, Any]:
        """Return the current logical Arduino probe state, never an old failure message."""
        with self._lock:
            raw = self._probe_raw
            filtered = self._probe_filtered
            packet_at = self._last_packet_at
            changed_at = self._probe_filtered_since
            sequence = self._packet_sequence
        now = time.monotonic()
        age = None if packet_at is None else now - packet_at
        stable_ms = 0.0 if changed_at is None else max(0.0, now - changed_at) * 1000.0
        fresh = age is not None and age <= self.config.serial_fresh_timeout_s
        state = "STALE" if not fresh else ("TRIGGERED" if filtered else "OPEN")
        payload = {"packet_age_s": age, "raw_value": raw, "filtered_triggered": filtered, "display_state": state, "fresh": fresh, "changed_at_monotonic": changed_at, "stable_for_ms": stable_ms, "required_stable_ms": self.config.probe_open_stable_ms, "packet_sequence": sequence}
        if require_fresh and not fresh:
            raise MachineRuntimeError(f"Sonda Arduino obsoleta: edad_paquete_s={age}.")
        if require_stable and stable_ms < self.config.probe_open_stable_ms:
            raise MachineRuntimeError(f"Sonda Arduino aún no estable: estable_ms={stable_ms:.1f}, requerido_ms={self.config.probe_open_stable_ms:.1f}.")
        return payload

    def mesh_retry_readiness(self) -> dict[str, Any]:
        """Validate current probe and position before a retry can start a worker."""
        self._require_physical_ready()
        probe = self.get_live_probe_state(require_fresh=True, require_stable=True)
        if probe["filtered_triggered"]:
            raise MachineRuntimeError("No se puede reintentar: sonda actual TRIGGERED.")
        self._refresh_machine()
        snapshot = self.snapshot()
        position = (snapshot.get("klipper") or {}).get("position") or {}
        age = position.get("live_position_age_s")
        if age is None or age > self.config.telemetry_fresh_timeout_s:
            age_text = "sin dato" if age is None else f"{age:.0f} s"
            raise MachineRuntimeError(f"No se puede reintentar: posición Moonraker obsoleta, edad {age_text}.")
        return {"probe_state": probe["display_state"], "probe_age_ms": round(float(probe["packet_age_s"]) * 1000.0), "telemetry_state": self._telemetry_status(), "position_age_ms": round(float(age) * 1000.0)}

    def _require_fresh_open_probe(self, *, after_sequence: int, stage: str, progress_callback=None) -> dict[str, Any]:
        """Require a new, recent and stable OPEN controller sample before motion."""
        deadline = time.monotonic() + 1.0
        required_stable_ms = self.config.probe_open_stable_ms
        while time.monotonic() < deadline:
            self._raise_if_cancelled()
            live_probe = self.get_live_probe_state()
            sequence = int(live_probe["packet_sequence"])
            raw = live_probe["raw_value"]
            filtered = live_probe["filtered_triggered"]
            age = live_probe["packet_age_s"]
            stable_ms = float(live_probe["stable_for_ms"])
            open_ok = not raw and not filtered
            fresh_ok = age is not None and age <= self.config.serial_fresh_timeout_s
            stable_ok = stable_ms >= required_stable_ms
            detail = {"probe_raw": raw, "probe_filtered": filtered, "last_packet_age_s": age, "open_stable_ms": stable_ms, "packet_sequence": sequence, "open_ok": open_ok, "fresh_ok": fresh_ok, "stable_ok": stable_ok, "required_stable_ms": required_stable_ms, "observed_stable_ms": stable_ms}
            # Unit runtimes have no serial driver; production always requires a post-motion packet.
            if not isinstance(self._driver, SerialDriver) and open_ok and fresh_ok and stable_ok:
                self._notify_probe_progress(progress_callback, stage, **detail)
                return detail
            if sequence > after_sequence and open_ok and fresh_ok and stable_ok:
                self._notify_probe_progress(progress_callback, stage, **detail)
                return detail
            time.sleep(0.01)
        failure = {"timestamp": _iso_now(), "stage": stage, "raw_value_at_failure": raw, "filtered_at_failure": filtered, "packet_age_at_failure_s": age, "open_ok": open_ok, "fresh_ok": fresh_ok, "stable_ok": stable_ok, "required_stable_ms": required_stable_ms, "observed_stable_ms": stable_ms, "error": f"Sonda no OPEN fresca y estable antes de {stage}: open_ok={open_ok}, fresh_ok={fresh_ok}, stable_ok={stable_ok}, required_stable_ms={required_stable_ms:.1f}, observed_stable_ms={stable_ms:.1f}, raw={raw}, filtrada={filtered}, edad_paquete_s={age}."}
        with self._lock:
            self._last_probe_failure = failure
        raise MachineRuntimeError(failure["error"])

    def _perform_probe_descent(
        self,
        *,
        label: str,
        profile: ProbeMotionProfile,
        open_after_sequence: int | None = None,
        progress_callback: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> ProbeResult:
        self._assert_safety_for_motion()
        self._refresh_machine()
        machine = self._machine
        jog = self._jog
        if machine is None or jog is None:
            raise MachineRuntimeError("No hay control físico inicializado.")
        if not machine.axis_is_homed("z"):
            raise MachineRuntimeError("Z debe tener homing antes de sondear.")
        with self._lock:
            packet_sequence = self._packet_sequence
        self._require_fresh_open_probe(
            after_sequence=packet_sequence - 1 if open_after_sequence is None else open_after_sequence,
            stage="POINT_VERIFY_PROBE_OPEN",
            progress_callback=progress_callback,
        )
        start = machine.get_motion_snapshot()
        start_x = float(start["x"])
        start_y = float(start["y"])
        self._notify_probe_progress(
            progress_callback,
            "POINT_DESCENT_STARTED",
            source=profile.source,
            probe_step_mm=profile.probe_step_mm,
            probe_feed_mm_min=profile.probe_feed_mm_min,
            retract_mm=profile.retract_mm,
        )
        while True:
            self._raise_if_cancelled()
            probe_state = self.get_live_probe_state(require_fresh=True)
            if probe_state["filtered_triggered"]:
                break
            snapshot = machine.get_motion_snapshot()
            current_z = float(snapshot["z"])
            remaining = current_z - machine.z_limits.minimum
            if remaining <= profile.settle_tolerance_mm:
                raise MachineRuntimeError("Se alcanzó el límite mínimo Z sin contacto de sonda.")
            step = min(profile.probe_step_mm, remaining)
            command_started_at = time.monotonic()
            self._notify_probe_progress(progress_callback, "POINT_LOWER_STEP", step_mm=step, feed_mm_min=profile.probe_feed_mm_min, command_started_at=command_started_at)
            result = jog.move_relative("z", -step, profile.probe_speed_mm_s)
            with self._lock:
                self._last_movement = result
                self._last_command_text = f"{label}_lower_step"
            self._wait_for_axis("z", float(result["target"]), "paso de sonda", start_position=current_z)
            observed = machine.get_motion_snapshot()
            command_completed_at = time.monotonic()
            self._notify_probe_progress(
                progress_callback,
                "POINT_CONFIRM_STEP",
                step_mm=step,
                z_mm=float(observed["z"]),
                command_started_at=command_started_at,
                command_completed_at=command_completed_at,
                command_duration_s=command_completed_at - command_started_at,
            )
        snapshot = machine.get_motion_snapshot()
        contact_z = float(snapshot["z"])
        self._notify_probe_progress(progress_callback, "POINT_CONTACT_DETECTED", z_mm=contact_z)
        retract_available = machine.z_limits.maximum - contact_z
        if retract_available <= profile.settle_tolerance_mm:
            raise MachineRuntimeError("No hay margen Z para retraer después del contacto.")
        retract = min(profile.retract_mm, retract_available)
        self._notify_probe_progress(progress_callback, "POINT_RETRACT", retract_mm=retract, feed_mm_min=profile.retract_feed_mm_min)
        with self._lock:
            retract_sequence = self._packet_sequence
        command_started_at = time.monotonic()
        result = jog.move_relative("z", retract, profile.retract_speed_mm_s)
        with self._lock:
            self._last_movement = result
            self._last_command_text = f"{label}_retract"
        self._wait_for_axis("z", float(result["target"]), "retracto de sonda", start_position=contact_z)
        retract_snapshot = machine.get_motion_snapshot()
        command_completed_at = time.monotonic()
        self._notify_probe_progress(
            progress_callback,
            "POINT_CONFIRM_RETRACT",
            retract_mm=retract,
            z_mm=float(retract_snapshot["z"]),
            command_started_at=command_started_at,
            command_completed_at=command_completed_at,
            command_duration_s=command_completed_at - command_started_at,
        )
        self._require_fresh_open_probe(
            after_sequence=retract_sequence,
            stage="POINT_VERIFY_PROBE_OPEN_AFTER_RETRACT",
            progress_callback=progress_callback,
        )
        return ProbeResult(x_mm=start_x, y_mm=start_y, z_mm=contact_z, captured_at=_iso_now())

    def _begin_operation_context(self, operation_type: str) -> OperationContext:
        with self._lock:
            previous = self._active_operation
            self._operation_generation += 1
            context = OperationContext(
                operation_id=f"{operation_type}-{self._operation_generation}",
                operation_type=operation_type,
                generation=self._operation_generation,
                cancel_event=threading.Event(),
                started_at=time.monotonic(),
            )
            self._active_operation = context
            if previous is not None:
                logger.warning("OPERATION_CONTEXT_REPLACED operation_type=%s operation_id=%s previous_id=%s cancel_event_is_set=%s movement_lock=%s worker_alive=%s", operation_type, context.operation_id, previous.operation_id, previous.cancel_event.is_set(), self._movement_lock.locked(), False)
            logger.info("OPERATION_CONTEXT_CREATED operation_type=%s operation_id=%s generation=%s cancel_event_is_set=%s movement_lock=%s worker_alive=%s", operation_type, context.operation_id, context.generation, False, self._movement_lock.locked(), False)
            return context

    def _finish_operation_context(self, context: OperationContext) -> None:
        with self._lock:
            if self._active_operation is context:
                self._active_operation = None
            logger.info("OPERATION_CONTEXT_FINISHED operation_type=%s operation_id=%s generation=%s cancel_event_is_set=%s movement_lock=%s worker_alive=%s", context.operation_type, context.operation_id, context.generation, context.cancel_event.is_set(), self._movement_lock.locked(), False)

    def _active_operation_snapshot(self) -> dict[str, Any] | None:
        with self._lock:
            context = self._active_operation
            if context is None:
                return None
            return {"operation_id": context.operation_id, "operation_type": context.operation_type, "generation": context.generation, "cancel_event_is_set": context.cancel_event.is_set()}

    def cancel_operation(self) -> dict[str, Any]:
        with self._lock:
            context = self._active_operation
            if context is not None:
                context.cancel_event.set()
                logger.info("OPERATION_CANCEL_REQUESTED operation_type=%s operation_id=%s generation=%s cancel_event_is_set=%s movement_lock=%s worker_alive=%s", context.operation_type, context.operation_id, context.generation, True, self._movement_lock.locked(), False)
            self._probe_requested = False
            self._manual_enabled = False
            self._state = MachineRuntimeState.CANCELLED if self._client is not None else MachineRuntimeState.DISCONNECTED
            label = "Operación física" if context is None else ({"preparation": "Preparación", "reference_z": "Referencia Z", "reference_move": "Movimiento al punto de referencia", "mesh": "Malla", "tool_change": "Movimiento de cambio"}.get(context.operation_type, "Operación física"))
            self._event("warning", f"{label} cancelada por el operador.")
        return self.snapshot()

    def _raise_if_cancelled(self) -> None:
        with self._lock:
            context = self._active_operation
        if context is not None and context.cancel_event.is_set():
            label = {"preparation": "Preparación", "reference_z": "Referencia Z", "reference_move": "Movimiento al punto de referencia", "mesh": "Malla", "tool_change": "Movimiento de cambio"}.get(context.operation_type, "Operación física")
            raise MachineRuntimeError(f"{label} cancelada por el operador.")

    def _notify_probe_progress(self, callback: Callable[[str, dict[str, Any]], None] | None, state: str, **detail: Any) -> None:
        if callback is None:
            return
        try:
            callback(state, detail)
        except Exception:
            pass

    def emergency_stop(self) -> dict[str, Any]:
        with self._lock:
            client = self._client
            self._manual_enabled = False
            self._state = MachineRuntimeState.ERROR
            self._last_error = "Emergencia solicitada por el operador."
            self._event("error", self._last_error)
        if self.config.mode is MachineMode.PHYSICAL and client is not None:
            client.send_gcode("M112")
            with self._lock:
                self._last_command_text = "M112"
        return self.snapshot()

    def refresh_observed_state(self) -> dict[str, Any]:
        """Refresh Klipper state over HTTP; this never sends G-code."""
        self._require_physical_ready()
        self._refresh_machine()
        return self.snapshot()

    def capture_current_position(self) -> dict[str, float]:
        observation = self.capture_reference_observation()
        return dict(observation["position"])

    def capture_reference_observation(self) -> dict[str, Any]:
        return self._observe_reference_position(use_last_probe=False)

    def last_probe_position(self) -> dict[str, float]:
        observation = self.capture_probe_reference_observation()
        return dict(observation["position"])

    def capture_probe_reference_observation(self) -> dict[str, Any]:
        return self._observe_reference_position(use_last_probe=True)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            machine_snapshot = self._machine.get_motion_snapshot() if self._machine is not None else None
            serial_age = None if self._last_packet_at is None else now - self._last_packet_at
            telemetry_age = None if self._last_telemetry_at is None else now - self._last_telemetry_at
            profile = get_jog_profile(self._manual.mode) if self._manual is not None else get_jog_profile(JogMode.FINE)
            safety = self._safety_snapshot(serial_age=serial_age, telemetry_age=telemetry_age)
            health = self._health_from_safety(safety)
            websocket_state = self._normalize_telemetry_transport_state(self._telemetry_state)
            position_age = self._position_age_s(machine_snapshot)
            last_websocket_message_age = None if self._last_websocket_message_at is None else max(0.0, now - self._last_websocket_message_at)
            last_http_observation_age = None if self._last_http_observation_at is None else max(0.0, now - self._last_http_observation_at)
            return {
                "mode": self.config.mode.value.upper(),
                "mode_label": self.config.mode_label,
                "state": self._state.value,
                "health": health.value,
                "started_at": self._started_at.isoformat(),
                "application": {"api_active": True, "mode": self.config.mode.value, "uptime_s": max(0.0, (utc_now() - self._started_at).total_seconds())},
                "moonraker": {
                    "url": self.config.moonraker_url,
                    "ws": self.config.moonraker_ws,
                    "http_connected": self._client is not None,
                    "http_state": self._http_status(now=now),
                    "websocket_connected": websocket_state == "CONNECTED",
                    "websocket_state": websocket_state,
                    "telemetry_state": self._telemetry_status(),
                    "last_websocket_message_age_s": last_websocket_message_age,
                    "last_position_age_s": position_age,
                    "last_http_observation_age_s": last_http_observation_age,
                    "last_http_error": self._last_http_error,
                    "last_websocket_error": self._last_websocket_error,
                    "last_error": self._last_error,
                    "reconnects": self._telemetry_reconnects,
                    "klippy_state": self._last_klippy_state,
                },
                "klipper": {
                    "ready": self._client is not None and self._last_klippy_state == "ready" and self._state not in {MachineRuntimeState.ERROR, MachineRuntimeState.DISCONNECTED},
                    "state": self._last_klippy_state,
                    "position": machine_snapshot,
                    "homed_axes": None if self._machine is None else self._machine.homed_axes,
                    "limits": None if self._machine is None else {
                        "x": {"min": self._machine.x_limits.minimum, "max": self._machine.x_limits.maximum},
                        "y": {"min": self._machine.y_limits.minimum, "max": self._machine.y_limits.maximum},
                        "z": {"min": self._machine.z_limits.minimum, "max": self._machine.z_limits.maximum},
                    },
                    "max_velocity": None if self._machine is None else self._machine.max_velocity,
                    "max_z_velocity": None if self._machine is None else self._machine.max_z_velocity,
                    "max_accel": None if self._machine is None else self._machine.max_accel,
                },
                "preparation": {
                    "reference_prep_z_mm": self.config.reference_prep_z_mm,
                    "z_clearance_feed_mm_min": self.config.z_clearance_feed_mm_min,
                    "z_clearance_speed_mm_s": self.config.z_clearance_feed_mm_min / 60.0,
                    "reference_approach_z_feed_mm_min": self.config.reference_approach_z_feed_mm_min,
                    "reference_approach_z_speed_mm_s": self.config.reference_approach_z_feed_mm_min / 60.0,
                    "reference_prep_xy_feed_mm_min": AUXILIARY_REFERENCE_XY_FEED_MM_MIN,
                    "reference_prep_xy_speed_mm_s": AUXILIARY_REFERENCE_XY_FEED_MM_MIN / 60.0,
                    "center_x_mm": None if self._machine is None else (self._machine.x_limits.minimum + self._machine.x_limits.maximum) / 2.0,
                    "center_y_mm": None if self._machine is None else (self._machine.y_limits.minimum + self._machine.y_limits.maximum) / 2.0,
                    "target": None if self._machine is None else {
                        "x_mm": (self._machine.x_limits.minimum + self._machine.x_limits.maximum) / 2.0,
                        "y_mm": (self._machine.y_limits.minimum + self._machine.y_limits.maximum) / 2.0,
                        "z_mm": self.config.reference_prep_z_mm,
                    },
                    "z_positive_up": self.config.tool_change_z_positive_up,
                    "sequence": ["HOME", "MOVE_Z_PREP", "MOVE_XY_CENTER", "WAITING_FOR_REFERENCE"],
                },
                "tool_change": {
                    "x_mm": self.config.tool_change_x_mm,
                    "y_mm": self.config.tool_change_y_mm,
                    "z_mm": self.config.tool_change_z_mm,
                    "clearance_z_mm": self.config.tool_change_clearance_z_mm,
                    "long_tool_clearance_z_mm": self.config.long_tool_change_clearance_z_mm,
                    "profiles": {
                        "standard": {"label": "Estándar", "clearance_z_mm": self.config.tool_change_clearance_z_mm},
                        "long_tool": {"label": "Herramienta larga", "clearance_z_mm": self.config.long_tool_change_clearance_z_mm},
                    },
                    "work_z_mm": self.config.tool_change_work_z_mm,
                    "z_positive_up": self.config.tool_change_z_positive_up,
                    "z_feed_mm_min": self.config.tool_change_z_feed_mm_min,
                    "z_speed_mm_s": self.config.tool_change_z_feed_mm_min / 60.0,
                    "clearance_z_feed_mm_min": self.config.z_clearance_feed_mm_min,
                    "clearance_z_speed_mm_s": self.config.z_clearance_feed_mm_min / 60.0,
                    "xy_feed_mm_min": AUXILIARY_DEFAULT_TRAVEL_FEED_MM_MIN,
                    "xy_speed_mm_s": AUXILIARY_DEFAULT_TRAVEL_FEED_MM_MIN / 60.0,
                },
                "settings": self.machine_settings(),
                "arduino": self._arduino_snapshot(now=now, serial_age=serial_age),
                "probe_live": self.get_live_probe_state(),
                "last_probe_failure": self._last_probe_failure,
                "controller": {
                    "direction": self._last_packet.direction if self._last_packet else "CENTER",
                    "x": self._last_packet.x if self._last_packet else None,
                    "y": self._last_packet.y if self._last_packet else None,
                    "joystick_centered": (self._last_packet.direction == "CENTER") if self._last_packet else True,
                    "joystick_button": self._last_command.joystick_pressed,
                    "external_button": self._last_command.probe_request,
                    "probe": self._last_command.probe_triggered,
                    "jog_mode": self._manual.mode.name if self._manual else "FINE",
                    "jog_distance_mm": profile.distance,
                    "jog_speed_mm_s": profile.speed,
                    "manual_enabled": self._manual_enabled,
                    "diagnostic_input_only": self._diagnostic_input_only,
                    "probe_requested": self._probe_requested,
                },
                "safety": safety,
                "last_command": self._last_command_text,
                "last_movement": self._last_movement,
                "last_error": self._last_error,
                "last_probe_result": None if self._last_probe_result is None else self._last_probe_result.__dict__,
                "active_operation": self._active_operation_snapshot(),
                "initialization_steps": [step.__dict__ for step in self._initialization_steps],
                "events": [event.__dict__ for event in self._events[-30:]],
            }

    def _serial_loop(self) -> None:
        if self._driver is not None:
            self._driver.diagnostics.thread_active = True
        while not self._serial_stop.is_set():
            try:
                if self._driver is None:
                    time.sleep(0.1)
                    continue
                packet = self._driver.read_packet()
                command = self._mapper.map(packet)
                self._handle_controller_packet(packet, command)
            except SerialProtocolError as error:
                with self._lock:
                    self._counters.invalid_packets += 1
                    self._counters.checksum_errors += 1
                    self._last_error = str(error)
            except Exception as error:
                if self._driver is not None:
                    self._driver.diagnostics.last_exception = str(error)
                with self._lock:
                    self._counters.disconnects += 1
                    self._last_error = str(error)
                    self._state = MachineRuntimeState.DEGRADED
                time.sleep(0.25)
        if self._driver is not None:
            self._driver.diagnostics.thread_active = False

    def _handle_controller_packet(self, packet: ControllerPacket, command: ControllerCommand) -> None:
        with self._lock:
            self._last_packet = packet
            self._last_command = command
            now = time.monotonic()
            self._last_packet_at = now
            self._counters.valid_packets += 1
            self._packet_sequence += 1
            # ControllerPacket.probe is already the logical contact bit: false=OPEN, true=TRIGGERED.
            # Do not apply active-low inversion again in the runtime.
            raw = bool(packet.probe)
            # Keep timestamps for logical state transitions, never for the
            # last packet. The first valid packet initializes this session.
            if self._probe_raw_since is None or self._probe_raw != raw:
                self._probe_raw = raw
                self._probe_raw_since = now
            if self._probe_filtered_since is None or self._probe_filtered != raw:
                self._probe_filtered = raw
                self._probe_filtered_since = now
            if not self._probe_filtered and self._last_error and self._last_error.startswith("Sonda no OPEN fresca y estable"):
                self._last_error = None
            manual = self._manual
            diagnostic_only = self._diagnostic_input_only
            manual_enabled = self._manual_enabled
            previous = self._previous_command
        if command.joystick_pressed and not previous.joystick_pressed and manual is not None and not diagnostic_only:
            manual.set_mode(_cycle_mode(manual.mode))
        if command.probe_request and not previous.probe_request:
            start_probe = False
            with self._lock:
                if self._state == MachineRuntimeState.REFERENCE_ARMED and self._probe_requested:
                    start_probe = True
                    self._event("warning", "Botón externo: inicio de sondeo de referencia.")
                else:
                    self._event("warning", "Botón externo ignorado: la referencia no está armada.")
            if start_probe:
                threading.Thread(target=self._confirm_probe_from_button, daemon=True).start()
        if not diagnostic_only and manual_enabled and _is_cardinal(command):
            with self._lock:
                can_jog = self._ready_for_jog
            if can_jog:
                self._manual_move(command)
                with self._lock:
                    self._ready_for_jog = False
        elif packet.direction == "CENTER":
            with self._lock:
                self._ready_for_jog = True
        with self._lock:
            self._previous_command = command

    def _manual_move(self, command: ControllerCommand) -> None:
        if self._manual is None:
            return
        if not self._movement_lock.acquire(blocking=False):
            return
        try:
            self._assert_safety_for_motion()
            if command.jog_x:
                result = self._manual.move("x", command.jog_x)
            elif command.jog_y:
                result = self._manual.move("y", command.jog_y)
            else:
                return
            with self._lock:
                self._last_movement = result
                self._last_command_text = "manual_jog"
        except (JogError, MachineRuntimeError) as error:
            with self._lock:
                self._last_error = str(error)
                self._state = MachineRuntimeState.DEGRADED
        finally:
            self._movement_lock.release()

    def _require_physical_config(self) -> None:
        if self.config.mode is not MachineMode.PHYSICAL:
            return
        missing = [
            name
            for name, value in (
                ("MOONRAKER_URL", self.config.moonraker_url),
                ("MOONRAKER_WS", self.config.moonraker_ws),
                ("SERIAL_PORT", self.config.serial_port),
            )
            if not value
        ]
        if missing:
            raise MachineRuntimeError("Modo físico requiere configuración explícita: " + ", ".join(missing) + ".")

    def _require_physical_ready(self) -> None:
        if self.config.mode is not MachineMode.PHYSICAL:
            raise MachineRuntimeError("Esta operación requiere MACHINE_MODE=physical.")
        if self._client is None:
            raise MachineRuntimeError("Conecte Moonraker/Klipper/Arduino antes de usar controles físicos.")

    def _assert_serial_thread_visible(self) -> None:
        if self._driver is None or self._serial_thread is None:
            raise MachineRuntimeError("Arduino no inicializado.")
        if not self._serial_thread.is_alive():
            raise MachineRuntimeError("Hilo serial inactivo; revise puerto, permisos y excepciones.")

    def _assert_serial_recent(self) -> None:
        with self._lock:
            last_packet_at = self._last_packet_at
        if last_packet_at is None:
            raise MachineRuntimeError("Arduino sin paquetes válidos; puerto abierto no es suficiente para autorizar movimiento.")
        age = time.monotonic() - last_packet_at
        if age > self.config.serial_fresh_timeout_s:
            raise MachineRuntimeError(f"Arduino obsoleto; último paquete válido hace {age:.2f} s.")

    def _wait_for_serial_recent(self) -> None:
        start = time.monotonic()
        timeout = max(self.config.serial_fresh_timeout_s, self.config.serial_startup_delay_s + 1.0)
        while time.monotonic() - start <= timeout:
            try:
                self._assert_serial_recent()
                return
            except MachineRuntimeError:
                time.sleep(0.05)
        self._assert_serial_recent()

    def _confirm_probe_from_button(self) -> None:
        try:
            self.confirm_probe()
        except Exception as error:
            with self._lock:
                self._state = MachineRuntimeState.ERROR
                self._last_error = str(error)
                self._event("error", str(error))

    def _assert_safety_for_connection(self) -> None:
        if self._telemetry_failures:
            raise MachineRuntimeError(f"Telemetría Moonraker detenida: {self._telemetry_failures[-1]}")

    def _assert_safety_for_motion(self) -> None:
        self._assert_safety_for_connection()
        self._assert_serial_recent()
        if self._machine is None:
            raise MachineRuntimeError("No hay estado de máquina.")
        if not self._machine.is_homed:
            raise MachineRuntimeError("Falta homing de ejes antes de autorizar movimiento.")

    def _refresh_machine(self) -> None:
        if self._client is None:
            raise MachineRuntimeError("Moonraker no está conectado.")
        try:
            if hasattr(self._client, "get_server_info"):
                server_info = self._client.get_server_info()
                klippy_state = str(server_info.get("klippy_state") or "unknown")
                if klippy_state != "ready":
                    raise MachineRuntimeError("Klipper no está ready.")
            else:
                klippy_state = "ready"
            refreshed = self._discovery(self._client)
        except Exception as error:
            with self._lock:
                self._last_http_error = str(error)
                self._last_klippy_state = locals().get("klippy_state", self._last_klippy_state)
            raise
        observed_at = time.monotonic()
        with self._lock:
            if self._machine is None:
                self._attach_telemetry_tracking(refreshed)
                self._machine = refreshed
                if self._jog is not None:
                    self._jog.machine = refreshed
            else:
                commanded = refreshed.commanded_position or refreshed.position
                self._machine.update_toolhead(
                    position=commanded.as_tuple(),
                    homed_axes=refreshed.homed_axes,
                    axis_minimum=(refreshed.x_limits.minimum, refreshed.y_limits.minimum, refreshed.z_limits.minimum),
                    axis_maximum=(refreshed.x_limits.maximum, refreshed.y_limits.maximum, refreshed.z_limits.maximum),
                    max_velocity=refreshed.max_velocity,
                    max_accel=refreshed.max_accel,
                    max_z_velocity=refreshed.max_z_velocity,
                )
                if refreshed.live_position is not None:
                    self._machine.update_motion(live_position=refreshed.live_position.as_tuple(), live_velocity=refreshed.live_velocity, source=refreshed.live_position_source)
                self._machine.update_gcode_move(
                    gcode_position=None if refreshed.gcode_position is None else refreshed.gcode_position.as_tuple(),
                    position=None if refreshed.gcode_move_position is None else refreshed.gcode_move_position.as_tuple(),
                    absolute_coordinates=refreshed.absolute_coordinates,
                    homing_origin=None if refreshed.homing_origin is None else refreshed.homing_origin.as_tuple(),
                )
            self._last_telemetry_at = observed_at
            self._last_http_observation_at = observed_at
            self._last_http_error = None
            self._last_klippy_state = klippy_state

    def _on_telemetry_state(self, state: str | dict[str, Any]) -> None:
        payload = state if isinstance(state, dict) else {"state": str(state)}
        transport_state = self._normalize_telemetry_transport_state(payload.get("state"))
        with self._lock:
            self._telemetry_state = transport_state
            reconnects = payload.get("reconnects")
            if reconnects is not None:
                self._telemetry_reconnects = int(reconnects)
            last_message_at = payload.get("last_message_at")
            if last_message_at is not None:
                self._last_websocket_message_at = float(last_message_at)
            last_error = payload.get("last_error")
            if last_error:
                self._last_websocket_error = str(last_error)
            elif transport_state == "CONNECTED":
                self._last_websocket_error = None

    def _telemetry_status(self) -> str:
        with self._lock:
            raw_state = str(self._telemetry_state or "DISCONNECTED").upper()
            machine_snapshot = None if self._machine is None else self._machine.get_motion_snapshot()
            last_http_observation_at = self._last_http_observation_at
        if raw_state == "STALE":
            return "STALE"
        transport_state = self._normalize_telemetry_transport_state(raw_state)
        if transport_state in {"DISCONNECTED", "ERROR", "STOPPED"}:
            return transport_state
        live_age = None if machine_snapshot is None else machine_snapshot.get("live_position_age_s")
        if last_http_observation_at is None or time.monotonic() - last_http_observation_at > self.config.telemetry_fresh_timeout_s:
            if live_age is None or float(live_age) > self.config.telemetry_fresh_timeout_s:
                return "STALE"
        if self._position_is_stale():
            return "STALE"
        return "LIVE"

    def _attach_telemetry_tracking(self, machine) -> None:
        original_update_motion = machine.update_motion
        original_update_toolhead = machine.update_toolhead
        original_update_gcode_move = machine.update_gcode_move

        def mark_telemetry() -> None:
            with self._lock:
                self._last_telemetry_at = time.monotonic()

        def update_motion_with_timestamp(*args, **kwargs):
            result = original_update_motion(*args, **kwargs)
            mark_telemetry()
            return result

        def update_toolhead_with_timestamp(*args, **kwargs):
            result = original_update_toolhead(*args, **kwargs)
            mark_telemetry()
            return result

        def update_gcode_move_with_timestamp(*args, **kwargs):
            result = original_update_gcode_move(*args, **kwargs)
            mark_telemetry()
            return result

        machine.update_motion = update_motion_with_timestamp
        machine.update_toolhead = update_toolhead_with_timestamp
        machine.update_gcode_move = update_gcode_move_with_timestamp

    def _send_script(self, script: str, *, label: str) -> None:
        if self._client is None:
            raise MachineRuntimeError("Moonraker no está conectado.")
        response: dict[str, Any] | None = None
        sent_at = _iso_now()
        try:
            response = self._client.send_gcode(script, timeout=self.config.moonraker_request_timeout_s)
        except MoonrakerTimeout as error:
            with self._lock:
                self._last_error = str(error)
                self._event("warning", f"Timeout HTTP enviando {label}; se comprobará el estado real de Klipper.")
        with self._lock:
            self._last_command_text = script
            if self._last_movement is not None and self._last_movement.get("label") == label:
                self._last_movement.update({"command_sent_at": sent_at, "moonraker_response": response})
            self._event("info", f"Comando físico enviado: {label}.")

    def _clear_resolved_transport_timeout(self, label: str) -> None:
        with self._lock:
            if self._last_error and "G-code request timed out" in self._last_error:
                self._event("info", f"Timeout HTTP de {label} resuelto por confirmación de estado Klipper.")
                self._last_error = None

    def _validate_machine_target(self, *, x: float | None = None, y: float | None = None, z: float | None = None, label: str) -> None:
        if self._machine is None:
            raise MachineRuntimeError("No hay estado de máquina.")
        checks = (
            ("X", x, self._machine.x_limits.minimum, self._machine.x_limits.maximum),
            ("Y", y, self._machine.y_limits.minimum, self._machine.y_limits.maximum),
            ("Z", z, self._machine.z_limits.minimum, self._machine.z_limits.maximum),
        )
        for axis, value, minimum, maximum in checks:
            if value is None:
                continue
            if not math.isfinite(value):
                raise MachineRuntimeError(f"{label}: {axis} debe ser un valor numérico finito.")
            if value < minimum or value > maximum:
                raise MachineRuntimeError(f"{label}: {axis}={value:.3f} mm fuera de límites Klipper {minimum:.3f}..{maximum:.3f} mm.")

    def _move_absolute(
        self,
        *,
        x: float | None = None,
        y: float | None = None,
        z: float | None = None,
        label: str,
        feed_mm_min: float = AUXILIARY_DEFAULT_TRAVEL_FEED_MM_MIN,
        coordinate_frame: str = "live_position",
    ) -> None:
        self._raise_if_cancelled()
        self._validate_machine_target(x=x, y=y, z=z, label=label)
        if self._machine is None:
            raise MachineRuntimeError("No hay telemetría de máquina.")
        requested_feed_mm_min = float(feed_mm_min)
        if requested_feed_mm_min <= 0:
            raise MachineRuntimeError(f"{label}: velocidad inválida F{requested_feed_mm_min:.3f}; debe ser positiva.")
        start_snapshot = self._machine.get_motion_snapshot()
        start_position, _start_age = self._frame_position(start_snapshot, coordinate_frame, label=label)
        targets = {axis: target for axis, target in (("x", x), ("y", y), ("z", z)) if target is not None}
        effective_feed_mm_min = self._effective_feed_mm_min(targets, requested_feed_mm_min)
        distance_mm = self._target_distance(start_position, targets)
        commanded_speed_mm_s = requested_feed_mm_min / 60.0
        effective_speed_mm_s = effective_feed_mm_min / 60.0
        expected_time_s = distance_mm / effective_speed_mm_s if effective_speed_mm_s > 0 else 0.0
        operation_timeout_s = self._operation_timeout_s(distance_mm=distance_mm, effective_feed_mm_min=effective_feed_mm_min)
        axes = []
        if x is not None:
            axes.append(f"X{x:.6f}")
        if y is not None:
            axes.append(f"Y{y:.6f}")
        if z is not None:
            axes.append(f"Z{z:.6f}")
        script = "SAVE_GCODE_STATE NAME=cnc_assistant_machine_move\nG90\nG1 " + " ".join(axes) + f" F{effective_feed_mm_min:.3f}\nRESTORE_GCODE_STATE NAME=cnc_assistant_machine_move"
        movement = {
            "label": label,
            "gcode": script,
            "command_sent_at": None,
            "moonraker_response": None,
            "coordinate_frame": coordinate_frame,
            "target_frame": coordinate_frame,
            "initial_position": {axis: float(start_position[axis]) for axis in ("x", "y", "z")},
            "target": targets,
            "direction": {axis: self._target_direction(float(start_position[axis]), target) for axis, target in targets.items()},
            "distance_mm": distance_mm,
            "requested_feed_mm_min": requested_feed_mm_min,
            "feed_mm_min": effective_feed_mm_min,
            "commanded_speed_mm_s": commanded_speed_mm_s,
            "effective_speed_mm_s": effective_speed_mm_s,
            "speed_mm_s": effective_speed_mm_s,
            "expected_time_s": expected_time_s,
            "timeout_s": operation_timeout_s,
            "no_progress_timeout_s": self.config.no_progress_timeout_s,
            "settle_timeout_s": self.config.settle_timeout_s,
            "position_tolerance_mm": self.config.settle_tolerance_mm,
            "velocity_tolerance_mm_s": self.config.velocity_tolerance_mm_s,
            "stable_samples_required": max(1, int(self.config.stable_samples)),
            "position_source": start_snapshot.get("source"),
            "live_position": start_snapshot.get("live_position"),
            "commanded_position": start_snapshot.get("commanded_position"),
            "gcode_position": start_snapshot.get("gcode_position"),
            "gcode_move_position": start_snapshot.get("gcode_move_position"),
            "homing_origin": start_snapshot.get("homing_origin"),
        }
        with self._lock:
            self._last_movement = movement
        self._send_script(script, label=label)
        result = self._wait_for_targets(targets, label, operation_timeout_s=operation_timeout_s, coordinate_frame=coordinate_frame)
        movement.update(result)
        with self._lock:
            self._last_movement = movement
        self._step(label, "ok", self._movement_step_detail(label, movement))

    def _effective_feed_mm_min(self, targets: dict[str, float], requested_feed_mm_min: float) -> float:
        if self._machine is None:
            return requested_feed_mm_min
        axis_limits_mm_s = []
        if any(axis in targets for axis in ("x", "y")):
            axis_limits_mm_s.append(float(self._machine.max_velocity))
        if "z" in targets:
            z_limit = self._machine.max_z_velocity if self._machine.max_z_velocity is not None else self._machine.max_velocity
            axis_limits_mm_s.append(float(z_limit))
        if not axis_limits_mm_s:
            return requested_feed_mm_min
        max_effective_feed = min(axis_limits_mm_s) * 60.0
        return min(requested_feed_mm_min, max_effective_feed)

    def _target_distance(self, start_position: dict[str, float], targets: dict[str, float]) -> float:
        return math.sqrt(sum((float(start_position[axis]) - target) ** 2 for axis, target in targets.items()))

    def _target_direction(self, start: float, target: float) -> int:
        delta = target - start
        if abs(delta) <= self.config.settle_tolerance_mm:
            return 0
        return 1 if delta > 0 else -1

    def _reference_target_z_feed(self, *, current_z: float, target_z: float) -> float:
        """Select the auxiliary feed from physical direction, not coordinate sign."""
        direction_away = 1.0 if self.config.tool_change_z_positive_up else -1.0
        moving_away = (float(target_z) - float(current_z)) * direction_away >= 0
        return float(
            self.config.z_clearance_feed_mm_min
            if moving_away
            else self.config.reference_approach_z_feed_mm_min
        )

    def _notify_transition_progress(
        self,
        callback: Callable[[str, dict[str, Any]], None] | None,
        stage: str,
        **payload: Any,
    ) -> None:
        if callback is not None:
            callback(stage, payload)

    def _operation_timeout_s(self, *, distance_mm: float, effective_feed_mm_min: float) -> float:
        effective_speed_mm_s = effective_feed_mm_min / 60.0
        expected_time_s = distance_mm / effective_speed_mm_s if effective_speed_mm_s > 0 else 0.0
        minimum_timeout_s = max(float(self.config.move_timeout_s), float(self.config.move_minimum_timeout_s))
        return max(minimum_timeout_s, expected_time_s * float(self.config.move_timeout_factor) + float(self.config.move_settle_margin_s))

    def _movement_step_detail(self, label: str, movement: dict[str, Any]) -> str:
        observed = movement.get("observed_position", {})
        target = movement.get("target", {})
        target_detail = ", ".join(f"{axis.upper()}={value:.3f}" for axis, value in target.items())
        observed_detail = ", ".join(
            f"{axis.upper()}={float(observed[axis]):.3f}"
            for axis in ("x", "y", "z")
            if axis in observed
        )
        return (
            f"{label}: objetivo {target_detail}; frame {movement.get('coordinate_frame', 'unknown')}; distancia {movement['distance_mm']:.3f} mm; "
            f"velocidad configurada {movement['requested_feed_mm_min']:.3f} mm/min; "
            f"velocidad efectiva {movement['effective_speed_mm_s']:.3f} mm/s; "
            f"estimado {movement['expected_time_s']:.3f} s; timeout {movement['timeout_s']:.3f} s; "
            f"observado {observed_detail}; resultado {movement.get('result', 'confirmado')}."
        )

    def _frame_position(self, snapshot: dict[str, Any], coordinate_frame: str, *, label: str) -> tuple[dict[str, float], float | None]:
        if coordinate_frame == "gcode_position":
            raw = snapshot.get("gcode_position")
            age = snapshot.get("gcode_position_age_s")
        elif coordinate_frame == "commanded_position":
            raw = snapshot.get("commanded_position")
            age = snapshot.get("commanded_position_age_s")
        elif coordinate_frame == "live_position":
            raw = snapshot.get("live_position")
            age = snapshot.get("live_position_age_s")
        else:
            raise MachineRuntimeError(f"{label}: frame de coordenadas no soportado: {coordinate_frame}.")
        if not isinstance(raw, dict):
            raise MachineRuntimeError(f"{label}: telemetría insuficiente; frame {coordinate_frame} no disponible.")
        return ({axis: float(raw[axis]) for axis in ("x", "y", "z")}, None if age is None else float(age))

    def _frame_is_stale(self, age_s: float | None) -> bool:
        return age_s is None or age_s > self.config.telemetry_fresh_timeout_s

    def _frame_offset_z(self, snapshot: dict[str, Any]) -> float | None:
        gcode_move = snapshot.get("gcode_move_position")
        gcode = snapshot.get("gcode_position")
        if isinstance(gcode_move, dict) and isinstance(gcode, dict):
            return float(gcode_move["z"]) - float(gcode["z"])
        homing_origin = snapshot.get("homing_origin")
        if isinstance(homing_origin, dict) and homing_origin.get("z") is not None:
            return float(homing_origin["z"])
        return None

    def _tool_change_clearance_target(
        self,
        current_gcode_z: float,
        *,
        tool_change_profile: str = "standard",
    ) -> float:
        configured_clearance_z = self.tool_change_clearance_z(tool_change_profile)
        safe_z = float(self.config.safe_z_mm)
        if self.config.tool_change_z_positive_up:
            minimum_clearance = max(configured_clearance_z, safe_z)
            return max(current_gcode_z, minimum_clearance)
        maximum_clearance = min(configured_clearance_z, safe_z)
        return min(current_gcode_z, maximum_clearance)

    def _targets_reached(self, snapshot: dict[str, Any], targets: dict[str, float], *, coordinate_frame: str, label: str) -> tuple[bool, bool, float, dict[str, float], float | None]:
        observed_position, frame_age = self._frame_position(snapshot, coordinate_frame, label=label)
        velocity = abs(float(snapshot["velocity"]))
        positions_ok = all(
            abs(float(observed_position[axis]) - target) <= self.config.settle_tolerance_mm
            for axis, target in targets.items()
        )
        stopped = velocity <= self.config.velocity_tolerance_mm_s
        return positions_ok and stopped, positions_ok, velocity, observed_position, frame_age

    def _remaining_distance(self, observed_position: dict[str, float], targets: dict[str, float]) -> float:
        return math.sqrt(sum((float(observed_position[axis]) - target) ** 2 for axis, target in targets.items()))

    def _distance_from_start(self, start_position: dict[str, float], observed_position: dict[str, float], targets: dict[str, float]) -> float:
        return math.sqrt(sum((float(observed_position[axis]) - float(start_position[axis])) ** 2 for axis in targets))

    def _wait_diagnostic(self, snapshot: dict[str, Any], targets: dict[str, float], *, coordinate_frame: str, trend: str | None = None) -> str:
        observed_position, frame_age = self._frame_position(snapshot, coordinate_frame, label="diagnostic")
        observed_z = observed_position.get("z")
        target_z = targets.get("z")
        error_mm = None if observed_z is None or target_z is None else observed_z - target_z
        observed_detail = ", ".join(f"{axis.upper()}={float(observed_position[axis]):.3f}" for axis in ("x", "y", "z"))
        homing_origin = snapshot.get("homing_origin") or {}
        offset_z = self._frame_offset_z(snapshot)
        return (
            f"target_frame={coordinate_frame}; observed_frame={coordinate_frame}; "
            f"target_z={None if target_z is None else f'{target_z:.3f}'}; "
            f"observed_z={None if observed_z is None else f'{observed_z:.3f}'}; "
            f"Posición observada: {observed_detail}; "
            f"homing_origin_z={None if homing_origin.get('z') is None else f'{float(homing_origin['z']):.3f}'}; "
            f"offset_z={None if offset_z is None else f'{offset_z:.3f}'}; "
            f"error_mm={None if error_mm is None else f'{error_mm:.3f}'}; "
            f"tendencia={trend or 'estable'}; "
            f"frame_age_s={None if frame_age is None else f'{frame_age:.3f}'}; "
            f"live_age_s={snapshot.get('live_position_age_s')}; "
            f"gcode_age_s={snapshot.get('gcode_position_age_s')}"
        )

    def _wait_for_targets(self, targets: dict[str, float], label: str, *, operation_timeout_s: float, coordinate_frame: str = "live_position") -> dict[str, Any]:
        if self._machine is None:
            raise MachineRuntimeError("No hay telemetría de máquina.")
        start = time.monotonic()
        stable_samples = 0
        required_stable_samples = max(1, int(self.config.stable_samples))
        start_snapshot = self._machine.get_motion_snapshot()
        last_snapshot = start_snapshot
        start_position, _start_age = self._frame_position(start_snapshot, coordinate_frame, label=label)
        previous_sample: dict[str, float] | None = None
        previous_offset_z: float | None = None
        last_progress_at = start
        reached_position_at: float | None = None
        away_required_samples = 5
        progress_epsilon = 0.005
        away_tolerance = 0.050
        consecutive_away_samples = 0
        last_refresh = start
        while time.monotonic() - start <= operation_timeout_s:
            self._raise_if_cancelled()
            self._assert_safety_for_connection()
            now = time.monotonic()
            if now - last_refresh >= 0.25:
                self._refresh_machine_best_effort()
                last_refresh = now
            last_snapshot = self._machine.get_motion_snapshot()
            reached, positions_ok, last_velocity, observed_position, frame_age = self._targets_reached(last_snapshot, targets, coordinate_frame=coordinate_frame, label=label)
            remaining = self._remaining_distance(observed_position, targets)
            current_offset_z = self._frame_offset_z(last_snapshot)
            previous_distance = None
            current_distance = None
            if previous_sample is not None and previous_offset_z == current_offset_z:
                previous_distance = math.sqrt(sum((targets[axis] - previous_sample[axis]) ** 2 for axis in targets))
                current_distance = math.sqrt(sum((targets[axis] - observed_position[axis]) ** 2 for axis in targets))
            with self._lock:
                if self._last_movement is not None and self._last_movement.get("label") == label:
                    self._last_movement.update({
                        "observed_position": {axis: float(observed_position[axis]) for axis in ("x", "y", "z")},
                        "observed_velocity_mm_s": last_velocity,
                        "position_source": coordinate_frame,
                        "coordinate_frame": coordinate_frame,
                        "target_frame": coordinate_frame,
                        "live_position_source": last_snapshot.get("live_position_source"),
                        "live_position": last_snapshot.get("live_position"),
                        "commanded_position": last_snapshot.get("commanded_position"),
                        "gcode_position": last_snapshot.get("gcode_position"),
                        "gcode_move_position": last_snapshot.get("gcode_move_position"),
                        "absolute_coordinates": last_snapshot.get("absolute_coordinates"),
                        "homing_origin": last_snapshot.get("homing_origin"),
                        "elapsed_s": now - start,
                        "no_progress_elapsed_s": now - last_progress_at,
                        "progress_remaining_mm": remaining,
                        "stable_samples": stable_samples,
                        "frame_age_s": frame_age,
                        "target_z": targets.get("z"),
                        "observed_z": observed_position.get("z"),
                        "offset_z": current_offset_z,
                        "previous_distance_mm": previous_distance,
                        "current_distance_mm": current_distance,
                        "consecutive_away_samples": consecutive_away_samples,
                    })
            if self._frame_is_stale(frame_age):
                stable_samples = 0
                time.sleep(0.05)
                continue
            if previous_sample is None or previous_offset_z != current_offset_z:
                previous_sample = observed_position
                previous_offset_z = current_offset_z
                consecutive_away_samples = 0
                last_progress_at = now
            elif previous_distance is not None and current_distance is not None:
                moving_by_velocity = abs(last_velocity) >= self.config.velocity_tolerance_mm_s
                observed_delta = max(abs(observed_position[axis] - previous_sample[axis]) for axis in targets)
                moving_by_position = observed_delta > self.config.settle_tolerance_mm
                if current_distance < previous_distance - progress_epsilon:
                    last_progress_at = now
                    consecutive_away_samples = 0
                elif current_distance > previous_distance + away_tolerance and (moving_by_velocity or moving_by_position):
                    consecutive_away_samples += 1
                    if consecutive_away_samples >= away_required_samples:
                        detail = self._wait_diagnostic(last_snapshot, targets, coordinate_frame=coordinate_frame, trend="alejandose")
                        if label == "z_preparacion_referencia":
                            with self._lock:
                                self._event("warning", f"{label}: detección de alejamiento tratada como diagnóstico; {detail}.")
                        else:
                            raise MachineRuntimeError(f"{label}: la posición se aleja del objetivo. {detail}.")
                elif moving_by_velocity or moving_by_position:
                    last_progress_at = now
                    consecutive_away_samples = 0
                previous_sample = observed_position
                previous_offset_z = current_offset_z
            if positions_ok:
                reached_position_at = reached_position_at or now
                if now - reached_position_at > self.config.settle_timeout_s:
                    detail = self._wait_diagnostic(last_snapshot, targets, coordinate_frame=coordinate_frame, trend="sin_estabilizar")
                    raise MachineRuntimeError(f"{label}: objetivo alcanzado pero la velocidad no se estabilizó. {detail}; velocidad={last_velocity:.3f} mm/s.")
            else:
                reached_position_at = None
            if reached:
                stable_samples += 1
            else:
                stable_samples = 0
            if stable_samples >= required_stable_samples:
                self._clear_resolved_transport_timeout(label)
                return {
                    "observed_position": {axis: float(observed_position[axis]) for axis in ("x", "y", "z")},
                    "observed_velocity_mm_s": last_velocity,
                    "position_source": coordinate_frame,
                    "coordinate_frame": coordinate_frame,
                    "target_frame": coordinate_frame,
                    "live_position": last_snapshot.get("live_position"),
                    "commanded_position": last_snapshot.get("commanded_position"),
                    "gcode_position": last_snapshot.get("gcode_position"),
                    "gcode_move_position": last_snapshot.get("gcode_move_position"),
                    "homing_origin": last_snapshot.get("homing_origin"),
                    "stable_samples": stable_samples,
                    "elapsed_s": time.monotonic() - start,
                    "progress_remaining_mm": remaining,
                    "result": "confirmado",
                }
            if now - last_progress_at > self.config.no_progress_timeout_s and remaining > self.config.settle_tolerance_mm:
                self._refresh_machine_best_effort()
                checked_snapshot = self._machine.get_motion_snapshot()
                checked_reached, _checked_positions_ok, checked_velocity, checked_position, checked_age = self._targets_reached(checked_snapshot, targets, coordinate_frame=coordinate_frame, label=label)
                checked_remaining = self._remaining_distance(checked_position, targets)
                if checked_reached and not self._frame_is_stale(checked_age):
                    self._clear_resolved_transport_timeout(label)
                    return {
                        "observed_position": {axis: float(checked_position[axis]) for axis in ("x", "y", "z")},
                        "observed_velocity_mm_s": checked_velocity,
                        "position_source": coordinate_frame,
                        "coordinate_frame": coordinate_frame,
                        "target_frame": coordinate_frame,
                        "live_position": checked_snapshot.get("live_position"),
                        "commanded_position": checked_snapshot.get("commanded_position"),
                        "gcode_position": checked_snapshot.get("gcode_position"),
                        "gcode_move_position": checked_snapshot.get("gcode_move_position"),
                        "homing_origin": checked_snapshot.get("homing_origin"),
                        "stable_samples": stable_samples,
                        "elapsed_s": time.monotonic() - start,
                        "progress_remaining_mm": checked_remaining,
                        "result": "reconciliado",
                    }
                detail = self._wait_diagnostic(checked_snapshot, targets, coordinate_frame=coordinate_frame, trend="sin_progreso")
                raise MachineRuntimeError(f"{label}: sin progreso durante {self.config.no_progress_timeout_s:.3f} s. {detail}.")
            time.sleep(0.05)
        self._refresh_machine_best_effort()
        final_snapshot = self._machine.get_motion_snapshot()
        reached, _positions_ok, final_velocity, final_position, final_age = self._targets_reached(final_snapshot, targets, coordinate_frame=coordinate_frame, label=label)
        if reached and not self._frame_is_stale(final_age):
            self._clear_resolved_transport_timeout(label)
            with self._lock:
                self._event("info", f"{label}: timeout de espera reconciliado por posición dentro de tolerancia y velocidad cero.")
            return {
                "observed_position": {axis: float(final_position[axis]) for axis in ("x", "y", "z")},
                "observed_velocity_mm_s": final_velocity,
                "position_source": coordinate_frame,
                "coordinate_frame": coordinate_frame,
                "target_frame": coordinate_frame,
                "live_position": final_snapshot.get("live_position"),
                "commanded_position": final_snapshot.get("commanded_position"),
                "gcode_position": final_snapshot.get("gcode_position"),
                "gcode_move_position": final_snapshot.get("gcode_move_position"),
                "homing_origin": final_snapshot.get("homing_origin"),
                "stable_samples": stable_samples,
                "elapsed_s": time.monotonic() - start,
                "progress_remaining_mm": self._remaining_distance(final_position, targets),
                "result": "reconciliado",
            }
        detail = self._wait_diagnostic(final_snapshot, targets, coordinate_frame=coordinate_frame, trend="timeout")
        raise MachineRuntimeError(
            f"Timeout esperando confirmación de {label} ({self._target_detail(targets)}) tras {operation_timeout_s:.3f} s. {detail}; velocidad={final_velocity:.3f} mm/s."
        )

    def _telemetry_is_stale(self, now: float) -> bool:
        machine = self._machine
        if machine is None:
            return True
        age = machine.get_motion_snapshot().get("live_position_age_s")
        return age is None or age > self.config.telemetry_fresh_timeout_s

    def _target_detail(self, targets: dict[str, float]) -> str:
        return ", ".join(f"{axis.upper()}={target:.3f}" for axis, target in targets.items())

    def _observed_detail(self, snapshot: dict[str, Any]) -> str:
        return ", ".join(f"{axis.upper()}={float(snapshot[axis]):.3f}" for axis in ("x", "y", "z"))

    def _wait_for_homing(self, required_axes: set[str]) -> None:
        if self._machine is None:
            raise MachineRuntimeError("No hay telemetría de máquina.")
        start = time.monotonic()
        while time.monotonic() - start <= self.config.home_timeout_s:
            self._raise_if_cancelled()
            self._assert_safety_for_connection()
            self._refresh_machine_best_effort()
            snapshot = self._machine.get_motion_snapshot()
            homed = set(str(self._machine.homed_axes))
            missing = required_axes - homed
            velocity = abs(float(snapshot["velocity"]))
            if not missing and velocity <= self.config.velocity_tolerance_mm_s:
                self._clear_resolved_transport_timeout("homing")
                return
            time.sleep(0.2)
        homed = set(str(self._machine.homed_axes))
        missing = sorted(required_axes - homed)
        raise MachineRuntimeError("Timeout de homing; faltan ejes: " + ", ".join(axis.upper() for axis in missing) + ".")

    def _refresh_machine_best_effort(self) -> None:
        try:
            self._refresh_machine()
        except Exception as error:
            with self._lock:
                self._last_error = str(error)

    def _normalize_telemetry_transport_state(self, state: object) -> str:
        normalized = str(state or "DISCONNECTED").upper()
        if normalized in {"LIVE", "STALE"}:
            return "CONNECTED"
        if normalized not in {"DISCONNECTED", "CONNECTING", "CONNECTED", "RECONNECTING", "ERROR", "STOPPED"}:
            return "ERROR"
        return normalized

    def _telemetry_transport_state(self) -> str:
        with self._lock:
            return self._normalize_telemetry_transport_state(self._telemetry_state)

    def _http_status(self, *, now: float | None = None) -> str:
        if self._client is None:
            return "DISCONNECTED"
        if self._last_http_observation_at is None:
            return "UNOBSERVED"
        current = time.monotonic() if now is None else now
        age = max(0.0, current - self._last_http_observation_at)
        if age > self.config.telemetry_fresh_timeout_s:
            return "STALE"
        return "AVAILABLE"

    def _position_age_s(self, machine_snapshot: dict[str, Any] | None) -> float | None:
        if machine_snapshot is None:
            return None
        ages = [
            machine_snapshot.get("live_position_age_s"),
            machine_snapshot.get("commanded_position_age_s"),
            machine_snapshot.get("gcode_position_age_s"),
        ]
        numeric = [float(age) for age in ages if isinstance(age, (int, float))]
        return min(numeric) if numeric else None

    def _position_is_stale(self) -> bool:
        machine_snapshot = None if self._machine is None else self._machine.get_motion_snapshot()
        age = self._position_age_s(machine_snapshot)
        return age is None or age > self.config.telemetry_fresh_timeout_s

    def _assert_finite_position(self, position: dict[str, float]) -> None:
        for axis in ("x_mm", "y_mm", "z_mm"):
            value = float(position[axis])
            if not math.isfinite(value):
                raise MachineRuntimeError(f"Posición observada inválida en {axis}: {value}.")

    def _observe_reference_position(self, *, use_last_probe: bool) -> dict[str, Any]:
        self._require_physical_ready()
        with self._lock:
            if self._state in {MachineRuntimeState.STOPPING, MachineRuntimeState.DISCONNECTED}:
                raise MachineRuntimeError("Runtime detenido; no se puede capturar una referencia física.")
            if self._active_operation is not None:
                raise MachineRuntimeError("Hay una operación física activa incompatible con la captura de referencia.")
            session_id = f"{self._started_at.isoformat()}#serial-{self._serial_generation}"
            last_probe = self._last_probe_result
        self._assert_safety_for_connection()
        self._refresh_machine()
        with self._lock:
            if self._active_operation is not None:
                raise MachineRuntimeError("Hay una operación física activa incompatible con la captura de referencia.")
            current_session = f"{self._started_at.isoformat()}#serial-{self._serial_generation}"
            machine = self._machine
            homed_axes = None if self._machine is None else self._machine.homed_axes
            last_probe = self._last_probe_result if use_last_probe else last_probe
        if current_session != session_id:
            raise MachineRuntimeError("La sesión física cambió durante la observación activa; repita la captura.")
        if self._last_klippy_state != "ready":
            raise MachineRuntimeError("Klipper no está ready para capturar una referencia física.")
        if machine is None:
            raise MachineRuntimeError("No hay posición física disponible.")
        missing = sorted(axis for axis in ("x", "y", "z") if not machine.axis_is_homed(axis))
        if missing:
            raise MachineRuntimeError("Falta homing de ejes: " + ", ".join(axis.upper() for axis in missing) + ".")
        machine_snapshot = machine.get_motion_snapshot()
        position_age = self._position_age_s(machine_snapshot)
        if position_age is None or position_age > self.config.telemetry_fresh_timeout_s:
            raise MachineRuntimeError("La posición observada está obsoleta; actualice Moonraker y repita la captura.")
        if use_last_probe:
            if last_probe is None:
                raise MachineRuntimeError("No hay resultado de sonda de un punto disponible.")
            position = {
                "x_mm": float(last_probe.x_mm),
                "y_mm": float(last_probe.y_mm),
                "z_mm": float(last_probe.z_mm),
            }
        else:
            position = {
                "x_mm": float(machine_snapshot["x"]),
                "y_mm": float(machine_snapshot["y"]),
                "z_mm": float(machine_snapshot["z"]),
            }
        self._assert_finite_position(position)
        return {
            "position": position,
            "session_id": current_session,
            "homed_axes": homed_axes,
            "machine_label": str(self.config.moonraker_url or "physical"),
            "websocket_state": self._telemetry_transport_state(),
            "http_state": self._http_status(),
            "position_age_s": position_age,
        }

    def _wait_for_axis(self, axis: str, target: float, label: str, *, start_position: float | None = None) -> None:
        if self._machine is None:
            raise MachineRuntimeError("No hay telemetría de máquina.")
        start = time.monotonic()
        last_refresh = start
        probe_step = label == "paso de sonda"
        while time.monotonic() - start <= self.config.move_timeout_s:
            self._raise_if_cancelled()
            self._assert_safety_for_connection()
            now = time.monotonic()
            if now - last_refresh >= 0.25 or self._telemetry_is_stale(now):
                self._refresh_machine_best_effort()
                last_refresh = now
            snapshot = self._machine.get_motion_snapshot()
            position = float(snapshot[axis])
            velocity = abs(float(snapshot["velocity"]))
            moved_enough = start_position is None or abs(position - start_position) > 0.001
            if abs(position - target) <= self.config.settle_tolerance_mm and velocity <= self.config.velocity_tolerance_mm_s and moved_enough:
                self._clear_resolved_transport_timeout(label)
                return
            if probe_step:
                with self._lock:
                    probe_triggered = self._last_command.probe_triggered
                if probe_triggered and velocity <= self.config.velocity_tolerance_mm_s and moved_enough:
                    self._clear_resolved_transport_timeout(label)
                    return
            time.sleep(0.05)
        self._refresh_machine_best_effort()
        snapshot = self._machine.get_motion_snapshot()
        position = float(snapshot[axis])
        velocity = abs(float(snapshot["velocity"]))
        moved_enough = start_position is None or abs(position - start_position) > 0.001
        if abs(position - target) <= self.config.settle_tolerance_mm and velocity <= self.config.velocity_tolerance_mm_s and moved_enough:
            self._clear_resolved_transport_timeout(label)
            return
        if probe_step:
            with self._lock:
                probe_triggered = self._last_command.probe_triggered
            if probe_triggered and velocity <= self.config.velocity_tolerance_mm_s and moved_enough:
                self._clear_resolved_transport_timeout(label)
                return
        raise MachineRuntimeError(f"Timeout esperando confirmación de {label}.")

    def _safe_z(self, machine, *, safe_z_mm: float | None = None) -> float:
        requested = self.config.safe_z_mm if safe_z_mm is None else safe_z_mm
        safe_z = min(max(requested, machine.z_limits.minimum), machine.z_limits.maximum)
        if safe_z < machine.z_limits.minimum or safe_z > machine.z_limits.maximum:
            raise MachineRuntimeError("Z segura fuera de límites descubiertos.")
        return safe_z

    def _mesh_safe_z(self, machine, *, probe_config: dict[str, Any] | None = None) -> float:
        clearance_z = self._probe_config_float(probe_config, "safe_z_mm")
        reference_z = self._probe_config_float(probe_config, "reference_z_mm")
        if clearance_z is None or reference_z is None:
            raise MachineRuntimeError("La malla física requiere referencia Z y separación segura explícitas.")
        # CNC actual: aumentar Z retrae de la PCB; esta semántica es clearance relativo, no Z absoluta.
        return calculate_safe_probe_z(reference_z, clearance_z, +1, machine.z_limits)

    def _probe_config_float(self, probe_config: dict[str, Any] | None, key: str) -> float | None:
        if not probe_config:
            return None
        value = probe_config.get(key)
        if value is None:
            return None
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        return numeric if numeric > 0 else None

    def _arduino_snapshot(self, *, now: float, serial_age: float | None) -> dict[str, Any]:
        connection_snapshot = self._connection_manager.snapshot() if self._connection_manager is not None else {
            "state": ArduinoConnectionState.DISCONNECTED,
            "generation": self._serial_generation,
            "configured_port": self.config.serial_port,
            "connected_port": None,
            "usb_identity": None,
            "known_identity": None,
            "reconnects": 0,
            "rejected_devices": 0,
            "retry_wait_s": None,
            "last_error": None,
            "thread_alive": False,
            "open": False,
        }
        driver_diagnostics = (
            self._driver.diagnostics.snapshot(now=now)
            if self._driver is not None
            else {
                "port": self.config.serial_port,
                "baudrate": self.config.serial_baudrate,
                "open": False,
                "thread_active": False,
                "bytes_received": 0,
                "packets_complete": 0,
                "valid_packets": 0,
                "invalid_packets": 0,
                "checksum_errors": 0,
                "sync_drops": 0,
                "partial_packets": 0,
                "reconnects": int(connection_snapshot.get("reconnects") or 0),
                "last_byte_age_s": None,
                "last_valid_packet_age_s": None,
                "last_invalid_packet_age_s": None,
                "last_exception": None,
            }
        )
        frequency = None
        if serial_age not in (None, 0):
            frequency = 1.0 / max(serial_age, 1e-6)
        reason = None
        if self._driver is None:
            reason = "Puerto serie no abierto."
        elif self._serial_thread is None or not self._serial_thread.is_alive():
            reason = "Hilo serial inactivo."
        elif self._last_packet_at is None:
            reason = "Puerto abierto sin paquetes válidos; revise puerto, baudrate, permisos, reinicio Arduino o protocolo."
        elif serial_age is not None and serial_age > self.config.serial_fresh_timeout_s:
            reason = f"Último paquete válido obsoleto ({serial_age:.2f} s)."
        return {
            **driver_diagnostics,
            **connection_snapshot,
            "connection_state": str(connection_snapshot.get("state") or ArduinoConnectionState.DISCONNECTED),
            "recent": serial_age is not None and serial_age <= self.config.serial_fresh_timeout_s,
            "valid_packets": self._counters.valid_packets,
            "runtime_invalid_packets": self._counters.invalid_packets,
            "runtime_checksum_errors": self._counters.checksum_errors,
            "runtime_disconnects": self._counters.disconnects,
            "packet_frequency_hz": frequency,
            "generation": self._serial_generation,
            "last_packet_age_s": serial_age,
            "last_packet": None if self._last_packet is None else self._last_packet.__dict__,
            "last_error": connection_snapshot.get("last_error") or self._last_error,
            "blocked_reason": reason,
        }

    def _safety_snapshot(self, *, serial_age: float | None, telemetry_age: float | None) -> dict[str, Any]:
        telemetry_recent = self.config.mode is MachineMode.SIMULATED or (telemetry_age is not None and telemetry_age <= self.config.telemetry_fresh_timeout_s)
        serial_recent = self.config.mode is MachineMode.SIMULATED or (serial_age is not None and serial_age <= self.config.serial_fresh_timeout_s)
        klipper_ready = self.config.mode is MachineMode.SIMULATED or self._client is not None
        homed = self.config.mode is MachineMode.SIMULATED or (self._machine is not None and self._machine.is_homed)
        movement_authorized = self._manual_enabled and telemetry_recent and serial_recent and klipper_ready and homed and self._state == MachineRuntimeState.WAITING_FOR_XY_REFERENCE
        reasons = []
        if not klipper_ready:
            reasons.append("Klipper/Moonraker no conectado.")
        if not telemetry_recent:
            reasons.append("Telemetría obsoleta.")
        if not serial_recent:
            reasons.append("Arduino obsoleto.")
        if not homed:
            reasons.append("Falta homing.")
        if self._state in {MachineRuntimeState.ERROR, MachineRuntimeState.PROBING_REFERENCE, MachineRuntimeState.HOMING}:
            reasons.append(f"Estado incompatible: {self._state.value}.")
        if not self._manual_enabled:
            reasons.append("Control manual no habilitado.")
        return {
            "telemetry_recent": telemetry_recent,
            "serial_recent": serial_recent,
            "klipper_ready": klipper_ready,
            "homed_axes_required": homed,
            "no_active_error": self._state is not MachineRuntimeState.ERROR,
            "no_incompatible_operation": self._state not in {MachineRuntimeState.PROBING_REFERENCE, MachineRuntimeState.HOMING},
            "movement_authorized": movement_authorized,
            "blocked_reason": " ".join(reasons) if reasons else None,
        }

    def _health_from_safety(self, safety: dict[str, Any]) -> MachineHealth:
        if self.config.mode is MachineMode.SIMULATED:
            return MachineHealth.HEALTHY
        if self._client is None:
            return MachineHealth.OFFLINE
        if self._state is MachineRuntimeState.ERROR:
            return MachineHealth.ERROR
        if not safety["telemetry_recent"] or not safety["serial_recent"]:
            return MachineHealth.WARNING
        return MachineHealth.HEALTHY

    def _log_preparation_transition(self, event: str, *, target_z: float, center_x: float | None, center_y: float | None, observed: dict[str, Any] | None = None, error: str | None = None, started: float | None = None) -> None:
        observed = observed or (self._machine.get_motion_snapshot() if self._machine is not None else {})
        with self._lock:
            telemetry_age = None if self._last_telemetry_at is None else time.monotonic() - self._last_telemetry_at
            homed_axes = None if self._machine is None else self._machine.homed_axes
            context = self._active_operation
            operation_type = None if context is None else context.operation_type
            operation_id = None if context is None else context.operation_id
            cancel_event_is_set = False if context is None else context.cancel_event.is_set()
            movement_lock = self._movement_lock.locked()
        logger.info(
            "%s operation_type=%s operation_id=%s cancel_event_is_set=%s movement_lock=%s worker_alive=%s target_z=%s observed_z=%s center_x=%s center_y=%s homed_axes=%s telemetry_age_s=%s elapsed_s=%.3f error=%s",
            event, operation_type, operation_id, cancel_event_is_set, movement_lock, False,
            target_z, observed.get("z"), center_x, center_y, homed_axes, telemetry_age,
            0.0 if started is None else time.monotonic() - started, error,
        )

    def _step(self, name: str, status: str, detail: str) -> None:
        with self._lock:
            self._initialization_steps.append(InitializationStep(name=name, status=status, detail=detail, timestamp=_iso_now()))

    def _event(self, level: str, message: str) -> None:
        self._events.append(RuntimeEvent(timestamp=_iso_now(), level=level, message=message))
        self._events = self._events[-100:]
