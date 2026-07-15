from __future__ import annotations

import _thread
import asyncio
import json
import math
import threading
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable

from klipper_cnc_assistant.input.command_mapper import CommandMapper, ControllerCommand
from klipper_cnc_assistant.input.serial_driver import ControllerPacket, SerialDriver, SerialProtocolError
from klipper_cnc_assistant.jog.controller import JogController, JogError
from klipper_cnc_assistant.jog.manual import ManualJogController
from klipper_cnc_assistant.jog.profiles import JogMode, get_jog_profile
from klipper_cnc_assistant.machine.discovery import discover_machine
from klipper_cnc_assistant.moonraker.client import MoonrakerClient, MoonrakerError, MoonrakerTimeout
from klipper_cnc_assistant.moonraker.telemetry import MoonrakerTelemetry

from .config import MachineMode, MachineRuntimeConfig


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
        self._serial_stop = threading.Event()
        self._started_at = utc_now()
        self._state = MachineRuntimeState.DISCONNECTED
        self._client: MoonrakerClient | None = None
        self._machine = None
        self._telemetry: MoonrakerTelemetry | None = None
        self._telemetry_thread: threading.Thread | None = None
        self._telemetry_failures: list[BaseException] = []
        self._driver: SerialDriver | None = None
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


    _MACHINE_SETTINGS_FIELDS = {
        "reference_prep_z_mm",
        "reference_prep_z_feed_mm_min",
        "move_timeout_s",
        "no_progress_timeout_s",
        "settle_tolerance_mm",
        "velocity_tolerance_mm_s",
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
            "reference_prep_z_feed_mm_min": "reference_prep_z_feed_mm_min",
            "move_total_timeout_s": "move_timeout_s",
            "no_progress_timeout_s": "no_progress_timeout_s",
            "position_tolerance_mm": "settle_tolerance_mm",
            "velocity_tolerance_mm_s": "velocity_tolerance_mm_s",
        }
        overrides = {field: payload[field] for field in self._MACHINE_SETTINGS_FIELDS if field in payload}
        for external, field in external_mapping.items():
            if external in payload:
                overrides[field] = payload[external]
        if "move_timeout_s" in overrides:
            overrides.setdefault("move_minimum_timeout_s", overrides["move_timeout_s"])
        return replace(config, **overrides) if overrides else config

    def machine_settings(self) -> dict[str, float]:
        return {
            "reference_prep_z_mm": self.config.reference_prep_z_mm,
            "reference_prep_z_feed_mm_min": self.config.reference_prep_z_feed_mm_min,
            "move_total_timeout_s": self.config.move_timeout_s,
            "no_progress_timeout_s": self.config.no_progress_timeout_s,
            "position_tolerance_mm": self.config.settle_tolerance_mm,
            "velocity_tolerance_mm_s": self.config.velocity_tolerance_mm_s,
        }

    def update_machine_settings(self, payload: dict[str, Any]) -> dict[str, float]:
        mapping = {
            "reference_prep_z_mm": "reference_prep_z_mm",
            "reference_prep_z_feed_mm_min": "reference_prep_z_feed_mm_min",
            "move_total_timeout_s": "move_timeout_s",
            "no_progress_timeout_s": "no_progress_timeout_s",
            "position_tolerance_mm": "settle_tolerance_mm",
            "velocity_tolerance_mm_s": "velocity_tolerance_mm_s",
        }
        overrides: dict[str, float] = {}
        for external, field in mapping.items():
            if external not in payload or payload[external] is None:
                continue
            value = float(payload[external])
            if value <= 0:
                raise MachineRuntimeError(f"{external} debe ser mayor que cero.")
            overrides[field] = value
        if "reference_prep_z_mm" in overrides and self._machine is not None:
            z = overrides["reference_prep_z_mm"]
            if z < self._machine.z_limits.minimum or z > self._machine.z_limits.maximum:
                raise MachineRuntimeError(f"reference_prep_z_mm fuera de límites Klipper {self._machine.z_limits.minimum:.3f}..{self._machine.z_limits.maximum:.3f} mm.")
        if "move_timeout_s" in overrides:
            overrides["move_minimum_timeout_s"] = overrides["move_timeout_s"]
        self.config = replace(self.config, **overrides)
        if self._settings_path is not None:
            self._settings_path.parent.mkdir(parents=True, exist_ok=True)
            self._settings_path.write_text(json.dumps(self.machine_settings(), indent=2, sort_keys=True))
        return self.machine_settings()

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
            driver = self._driver
        if telemetry is not None:
            telemetry.stop()
        if driver is not None:
            driver.close()
        if self._telemetry_thread is not None:
            self._telemetry_thread.join(timeout=2.0)
        if self._serial_thread is not None:
            self._serial_thread.join(timeout=2.0)
        with self._lock:
            self._client = None
            self._machine = None
            self._telemetry = None
            self._driver = None
            self._jog = None
            self._manual = None
            self._state = MachineRuntimeState.DISCONNECTED
            self._event("info", "Runtime detenido.")

    def connect(self) -> dict[str, Any]:
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
            assert self.config.serial_port is not None
            client = self._client_factory(self.config.moonraker_url, timeout=self.config.moonraker_request_timeout_s)
            server_info = client.get_server_info()
            if server_info.get("klippy_state") != "ready":
                raise MachineRuntimeError("Klipper no está ready.")
            machine = self._discovery(client)
            self._attach_telemetry_tracking(machine)
            telemetry = self._telemetry_factory(self.config.moonraker_ws, machine)
            driver = self._serial_factory(port=self.config.serial_port, baudrate=self.config.serial_baudrate, startup_delay=self.config.serial_startup_delay_s)
            driver.open()
            telemetry_thread = threading.Thread(target=_run_telemetry, args=(telemetry, self._telemetry_failures), daemon=True)
            serial_thread = threading.Thread(target=self._serial_loop, daemon=True)
            with self._lock:
                self._client = client
                self._machine = machine
                self._telemetry = telemetry
                self._driver = driver
                self._jog = JogController(client, machine)
                self._manual = ManualJogController(self._jog, mode=JogMode.FINE)
                self._state = MachineRuntimeState.DIAGNOSTIC
                self._diagnostic_input_only = True
                self._serial_stop.clear()
                self._event("info", "Moonraker, Klipper y Arduino conectados en modo diagnóstico.")
            telemetry_thread.start()
            serial_thread.start()
            with self._lock:
                self._telemetry_thread = telemetry_thread
                self._serial_thread = serial_thread
                self._last_telemetry_at = time.monotonic()
            return self.snapshot()
        except Exception as error:
            with self._lock:
                self._state = MachineRuntimeState.ERROR
                self._last_error = str(error)
                self._event("error", str(error))
            raise

    def disconnect(self) -> dict[str, Any]:
        self.stop()
        return self.snapshot()

    def reset_physical_session(self) -> dict[str, Any]:
        self.stop()
        with self._lock:
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
        self._require_physical_ready()
        if not self._movement_lock.acquire(blocking=False):
            raise MachineRuntimeError("Ya hay un movimiento u operación física activa.")
        try:
            with self._lock:
                self._state = MachineRuntimeState.HOMING
                self._manual_enabled = False
                self._diagnostic_input_only = True
                self._initialization_steps = []
            self._step("verificar_modo_fisico", "ok", "Modo físico confirmado.")
            self._assert_safety_for_connection()
            self._step("verificar_conexion", "ok", "Moonraker y Klipper están conectados.")
            self._assert_serial_thread_visible()
            self._wait_for_serial_recent()
            self._step("verificar_arduino", "ok", "Arduino con paquetes válidos recientes.")

            target_z_mm = self.config.reference_prep_z_mm if target_z_mm is None else float(target_z_mm)
            self._send_script("G28", label="homing")
            self._step("homing_solicitado", "ok", "G28 enviado; la finalización se confirma por toolhead.homed_axes y velocidad cero.")
            self._wait_for_homing({"x", "y", "z"})
            self._refresh_machine()
            machine = self._machine
            if machine is None:
                raise MachineRuntimeError("No hay estado de máquina descubierto.")
            missing = sorted(axis for axis in ("x", "y", "z") if not machine.axis_is_homed(axis))
            if missing:
                raise MachineRuntimeError("Homing incompleto; faltan ejes: " + ", ".join(axis.upper() for axis in missing) + ".")
            with self._lock:
                self._state = MachineRuntimeState.HOMED
            self._step("homing_confirmado", "ok", f"Klipper reporta homed_axes={machine.homed_axes}.")

            current_snapshot = machine.get_motion_snapshot()
            self._validate_machine_target(z=target_z_mm, label="Z de preparación")
            if target_z_mm < float(current_snapshot["z"]):
                raise MachineRuntimeError(
                    f"Z de preparación {target_z_mm:.3f} mm queda por debajo de la Z actual {float(current_snapshot['z']):.3f} mm. No se puede confirmar que subir Z aleje la herramienta de la PCB."
                )
            center_x = (machine.x_limits.minimum + machine.x_limits.maximum) / 2.0
            center_y = (machine.y_limits.minimum + machine.y_limits.maximum) / 2.0
            self._validate_machine_target(x=center_x, y=center_y, label="centro de máquina")
            self._step("actualizar_limites", "ok", f"Límites Klipper X={machine.x_limits.minimum:.3f}..{machine.x_limits.maximum:.3f} Y={machine.y_limits.minimum:.3f}..{machine.y_limits.maximum:.3f} Z={machine.z_limits.minimum:.3f}..{machine.z_limits.maximum:.3f}.")
            self._step("calcular_centro", "ok", f"Centro real calculado X={center_x:.3f} Y={center_y:.3f}.")

            with self._lock:
                self._state = MachineRuntimeState.MOVING_TO_SAFE_Z
            self._move_absolute(z=target_z_mm, label="z_preparacion_referencia", feed_mm_min=self.config.reference_prep_z_feed_mm_min)
            self._step("z_segura_confirmada", "ok", f"Z de traslado segura alcanzada: {target_z_mm:.3f} mm.")

            with self._lock:
                self._state = MachineRuntimeState.MOVING_TO_CENTER
            self._move_absolute(x=center_x, y=center_y, label="xy_centro")
            self._refresh_machine()
            self._step("centro_confirmado", "ok", f"Máquina preparada en X={center_x:.3f} Y={center_y:.3f} Z={target_z_mm:.3f} mm.")
            with self._lock:
                self._state = MachineRuntimeState.WAITING_FOR_XY_REFERENCE
                self._event("info", "Inicialización física completada; posicione X/Y del origen 0,0 y arme la referencia.")
            return self.snapshot()
        except Exception as error:
            with self._lock:
                self._state = MachineRuntimeState.ERROR
                self._last_error = str(error)
                self._step("abortar", "error", str(error))
                self._event("error", str(error))
            raise
        finally:
            self._movement_lock.release()

    def move_to_tool_change_position(self) -> dict[str, Any]:
        self._require_physical_ready()
        if not self._movement_lock.acquire(blocking=False):
            raise MachineRuntimeError("Ya hay un movimiento u operación física activa.")
        try:
            with self._lock:
                self._manual_enabled = False
                self._diagnostic_input_only = True
                self._state = MachineRuntimeState.MOVING_TO_SAFE_Z
            self._assert_safety_for_motion()
            self._refresh_machine()
            machine = self._machine
            if machine is None:
                raise MachineRuntimeError("No hay estado de máquina descubierto.")
            target_x = float(self.config.tool_change_x_mm)
            target_y = float(self.config.tool_change_y_mm)
            target_z = float(self.config.tool_change_z_mm)
            self._validate_machine_target(z=target_z, label="Z de cambio de herramienta")
            self._validate_machine_target(x=target_x, y=target_y, label="posición XY de cambio de herramienta")
            self._move_absolute(z=target_z, label="tool_change_z_segura", feed_mm_min=self.config.tool_change_z_feed_mm_min)
            with self._lock:
                self._state = MachineRuntimeState.MOVING_TO_CENTER
            self._move_absolute(x=target_x, y=target_y, label="tool_change_xy")
            with self._lock:
                self._state = MachineRuntimeState.WAITING_FOR_XY_REFERENCE
                self._event("info", "Máquina en posición segura para cambio de herramienta.")
            return self.snapshot()
        except Exception as error:
            with self._lock:
                self._state = MachineRuntimeState.ERROR
                self._last_error = str(error)
                self._event("error", str(error))
            raise
        finally:
            self._movement_lock.release()

    def request_probe(self) -> dict[str, Any]:
        self._require_physical_ready()
        with self._lock:
            if self._state not in {MachineRuntimeState.WAITING_FOR_XY_REFERENCE, MachineRuntimeState.REFERENCE_ARMED}:
                raise MachineRuntimeError("La referencia solo puede armarse después de homing, Z segura y movimiento al centro.")
            self._probe_requested = True
            self._manual_enabled = False
            self._diagnostic_input_only = True
            self._state = MachineRuntimeState.REFERENCE_ARMED
            self._event("warning", "REFERENCE_ARMED: pulse el botón externo para sondear la referencia.")
        return self.snapshot()

    def confirm_probe(self) -> dict[str, Any]:
        self._require_physical_ready()
        if not self._movement_lock.acquire(blocking=False):
            raise MachineRuntimeError("Ya hay un movimiento u operación física activa.")
        try:
            with self._lock:
                if self._state != MachineRuntimeState.REFERENCE_ARMED:
                    raise MachineRuntimeError("La referencia debe estar armada antes de sondear.")
                if not self._probe_requested:
                    raise MachineRuntimeError("No existe REFERENCE_ARMED pendiente de botón externo.")
                if self._last_command.probe_triggered:
                    raise MachineRuntimeError("La sonda está activa antes de iniciar el descenso.")
                self._manual_enabled = False
                self._diagnostic_input_only = True
                self._state = MachineRuntimeState.PROBING_REFERENCE
            self._assert_safety_for_motion()
            self._refresh_machine()
            machine = self._machine
            jog = self._jog
            if machine is None or jog is None:
                raise MachineRuntimeError("No hay control físico inicializado.")
            if not machine.axis_is_homed("z"):
                raise MachineRuntimeError("Z debe tener homing antes de sondear.")
            start = machine.get_motion_snapshot()
            start_x = float(start["x"])
            start_y = float(start["y"])
            while True:
                with self._lock:
                    if self._last_command.probe_triggered:
                        break
                snapshot = machine.get_motion_snapshot()
                current_z = float(snapshot["z"])
                remaining = current_z - machine.z_limits.minimum
                if remaining <= self.config.settle_tolerance_mm:
                    raise MachineRuntimeError("Se alcanzó el límite mínimo Z sin contacto de sonda.")
                step = min(self.config.probe_step_mm, remaining)
                result = jog.move_relative("z", -step, self.config.probe_lower_speed_mm_s)
                with self._lock:
                    self._last_movement = result
                    self._last_command_text = "probe_lower_step"
                self._wait_for_axis("z", float(result["target"]), "paso de sonda")
            snapshot = machine.get_motion_snapshot()
            contact_z = float(snapshot["z"])
            retract_available = machine.z_limits.maximum - contact_z
            if retract_available <= self.config.settle_tolerance_mm:
                raise MachineRuntimeError("No hay margen Z para retraer después del contacto.")
            retract = min(self.config.probe_retract_mm, retract_available)
            result = jog.move_relative("z", retract, self.config.probe_retract_speed_mm_s)
            with self._lock:
                self._last_movement = result
                self._last_command_text = "probe_retract"
            self._wait_for_axis("z", float(result["target"]), "retracto de sonda")
            probe = ProbeResult(x_mm=start_x, y_mm=start_y, z_mm=contact_z, captured_at=_iso_now())
            with self._lock:
                self._last_probe_result = probe
                self._probe_requested = False
                self._state = MachineRuntimeState.REFERENCE_CAPTURED
                self._event("info", f"Sonda de referencia capturada X={start_x:.3f} Y={start_y:.3f} Z={contact_z:.3f}.")
            return self.snapshot()
        except Exception as error:
            with self._lock:
                self._state = MachineRuntimeState.ERROR
                self._last_error = str(error)
                self._event("error", str(error))
            raise
        finally:
            self._movement_lock.release()

    def probe_mesh_point(self, point: dict[str, Any]) -> dict[str, Any]:
        self._require_physical_ready()
        if not self._movement_lock.acquire(blocking=False):
            raise MachineRuntimeError("Ya hay un movimiento u operación física activa.")
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
            safe_z = self._safe_z(machine)
            self._move_absolute(z=safe_z, label="mesh_z_segura")
            self._move_absolute(x=float(point["x_machine"]), y=float(point["y_machine"]), label=f"mesh_xy_{point['index']}")
            probe = self._probe_current_position(label=f"mesh_probe_{point['index']}")
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
            self._movement_lock.release()

    def _probe_current_position(self, *, label: str) -> ProbeResult:
        self._assert_safety_for_motion()
        self._refresh_machine()
        machine = self._machine
        jog = self._jog
        if machine is None or jog is None:
            raise MachineRuntimeError("No hay control físico inicializado.")
        if not machine.axis_is_homed("z"):
            raise MachineRuntimeError("Z debe tener homing antes de sondear.")
        with self._lock:
            if self._last_command.probe_triggered:
                raise MachineRuntimeError("La sonda está activa antes de iniciar el descenso.")
        start = machine.get_motion_snapshot()
        start_x = float(start["x"])
        start_y = float(start["y"])
        while True:
            with self._lock:
                if self._last_command.probe_triggered:
                    break
            snapshot = machine.get_motion_snapshot()
            current_z = float(snapshot["z"])
            remaining = current_z - machine.z_limits.minimum
            if remaining <= self.config.settle_tolerance_mm:
                raise MachineRuntimeError("Se alcanzó el límite mínimo Z sin contacto de sonda.")
            step = min(self.config.probe_step_mm, remaining)
            result = jog.move_relative("z", -step, self.config.probe_lower_speed_mm_s)
            with self._lock:
                self._last_movement = result
                self._last_command_text = f"{label}_lower_step"
            self._wait_for_axis("z", float(result["target"]), "paso de sonda")
        snapshot = machine.get_motion_snapshot()
        contact_z = float(snapshot["z"])
        retract_available = machine.z_limits.maximum - contact_z
        if retract_available <= self.config.settle_tolerance_mm:
            raise MachineRuntimeError("No hay margen Z para retraer después del contacto.")
        retract = min(self.config.probe_retract_mm, retract_available)
        result = jog.move_relative("z", retract, self.config.probe_retract_speed_mm_s)
        with self._lock:
            self._last_movement = result
            self._last_command_text = f"{label}_retract"
        self._wait_for_axis("z", float(result["target"]), "retracto de sonda")
        return ProbeResult(x_mm=start_x, y_mm=start_y, z_mm=contact_z, captured_at=_iso_now())

    def cancel_operation(self) -> dict[str, Any]:
        with self._lock:
            self._probe_requested = False
            self._manual_enabled = False
            self._state = MachineRuntimeState.READY_FOR_HOME if self._client is not None else MachineRuntimeState.DISCONNECTED
            self._event("warning", "Operación física cancelada por el operador.")
        return self.snapshot()

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

    def capture_current_position(self) -> dict[str, float]:
        self._require_physical_ready()
        machine = self._machine
        if machine is None:
            raise MachineRuntimeError("No hay posición física disponible.")
        snapshot = machine.get_motion_snapshot()
        return {"x_mm": float(snapshot["x"]), "y_mm": float(snapshot["y"]), "z_mm": float(snapshot["z"])}

    def last_probe_position(self) -> dict[str, float]:
        self._require_physical_ready()
        if self._last_probe_result is None:
            raise MachineRuntimeError("No hay resultado de sonda de un punto disponible.")
        return {
            "x_mm": self._last_probe_result.x_mm,
            "y_mm": self._last_probe_result.y_mm,
            "z_mm": self._last_probe_result.z_mm,
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            machine_snapshot = self._machine.get_motion_snapshot() if self._machine is not None else None
            serial_age = None if self._last_packet_at is None else now - self._last_packet_at
            telemetry_age = None if self._last_telemetry_at is None else now - self._last_telemetry_at
            profile = get_jog_profile(self._manual.mode) if self._manual is not None else get_jog_profile(JogMode.FINE)
            safety = self._safety_snapshot(serial_age=serial_age, telemetry_age=telemetry_age)
            health = self._health_from_safety(safety)
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
                    "websocket_connected": self._telemetry_thread is not None and self._telemetry_thread.is_alive(),
                    "last_error": self._last_error,
                },
                "klipper": {
                    "ready": self._client is not None and self._state not in {MachineRuntimeState.ERROR, MachineRuntimeState.DISCONNECTED},
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
                    "reference_prep_z_feed_mm_min": self.config.reference_prep_z_feed_mm_min,
                    "reference_prep_z_speed_mm_s": self.config.reference_prep_z_feed_mm_min / 60.0,
                    "center_x_mm": None if self._machine is None else (self._machine.x_limits.minimum + self._machine.x_limits.maximum) / 2.0,
                    "center_y_mm": None if self._machine is None else (self._machine.y_limits.minimum + self._machine.y_limits.maximum) / 2.0,
                    "target": None if self._machine is None else {
                        "x_mm": (self._machine.x_limits.minimum + self._machine.x_limits.maximum) / 2.0,
                        "y_mm": (self._machine.y_limits.minimum + self._machine.y_limits.maximum) / 2.0,
                        "z_mm": self.config.reference_prep_z_mm,
                    },
                    "sequence": ["HOME", "MOVE_Z_PREP", "MOVE_XY_CENTER", "WAITING_FOR_REFERENCE"],
                },
                "tool_change": {
                    "x_mm": self.config.tool_change_x_mm,
                    "y_mm": self.config.tool_change_y_mm,
                    "z_mm": self.config.tool_change_z_mm,
                    "z_feed_mm_min": self.config.tool_change_z_feed_mm_min,
                    "z_speed_mm_s": self.config.tool_change_z_feed_mm_min / 60.0,
                },
                "arduino": self._arduino_snapshot(now=now, serial_age=serial_age),
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
            self._last_packet_at = time.monotonic()
            self._counters.valid_packets += 1
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
        refreshed = self._discovery(self._client)
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
                    self._machine.update_motion(live_position=refreshed.live_position.as_tuple(), live_velocity=refreshed.live_velocity)
                self._machine.update_gcode_move(
                    gcode_position=None if refreshed.gcode_position is None else refreshed.gcode_position.as_tuple(),
                    position=None if refreshed.gcode_move_position is None else refreshed.gcode_move_position.as_tuple(),
                    absolute_coordinates=refreshed.absolute_coordinates,
                    homing_origin=None if refreshed.homing_origin is None else refreshed.homing_origin.as_tuple(),
                )
            self._last_telemetry_at = time.monotonic()

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
            if value < minimum or value > maximum:
                raise MachineRuntimeError(f"{label}: {axis}={value:.3f} mm fuera de límites Klipper {minimum:.3f}..{maximum:.3f} mm.")

    def _move_absolute(self, *, x: float | None = None, y: float | None = None, z: float | None = None, label: str, feed_mm_min: float = 600.0) -> None:
        self._validate_machine_target(x=x, y=y, z=z, label=label)
        if self._machine is None:
            raise MachineRuntimeError("No hay telemetría de máquina.")
        requested_feed_mm_min = float(feed_mm_min)
        if requested_feed_mm_min <= 0:
            raise MachineRuntimeError(f"{label}: velocidad inválida F{requested_feed_mm_min:.3f}; debe ser positiva.")
        start_snapshot = self._machine.get_motion_snapshot()
        targets = {axis: target for axis, target in (("x", x), ("y", y), ("z", z)) if target is not None}
        effective_feed_mm_min = self._effective_feed_mm_min(targets, requested_feed_mm_min)
        distance_mm = self._target_distance(start_snapshot, targets)
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
            "initial_position": {axis: float(start_snapshot[axis]) for axis in ("x", "y", "z")},
            "target": targets,
            "direction": {axis: self._target_direction(float(start_snapshot[axis]), target) for axis, target in targets.items()},
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
        }
        with self._lock:
            self._last_movement = movement
        self._send_script(script, label=label)
        result = self._wait_for_targets(targets, label, operation_timeout_s=operation_timeout_s)
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

    def _target_distance(self, snapshot: dict[str, Any], targets: dict[str, float]) -> float:
        return math.sqrt(sum((float(snapshot[axis]) - target) ** 2 for axis, target in targets.items()))

    def _target_direction(self, start: float, target: float) -> int:
        delta = target - start
        if abs(delta) <= self.config.settle_tolerance_mm:
            return 0
        return 1 if delta > 0 else -1

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
            f"{label}: objetivo {target_detail}; distancia {movement['distance_mm']:.3f} mm; "
            f"velocidad configurada {movement['requested_feed_mm_min']:.3f} mm/min; "
            f"velocidad efectiva {movement['effective_speed_mm_s']:.3f} mm/s; "
            f"estimado {movement['expected_time_s']:.3f} s; timeout {movement['timeout_s']:.3f} s; "
            f"observado {observed_detail}; resultado {movement.get('result', 'confirmado')}."
        )

    def _targets_reached(self, snapshot: dict[str, Any], targets: dict[str, float]) -> tuple[bool, bool, float]:
        velocity = abs(float(snapshot["velocity"]))
        positions_ok = all(
            abs(float(snapshot[axis]) - target) <= self.config.settle_tolerance_mm
            for axis, target in targets.items()
        )
        stopped = velocity <= self.config.velocity_tolerance_mm_s
        return positions_ok and stopped, positions_ok, velocity

    def _remaining_distance(self, snapshot: dict[str, Any], targets: dict[str, float]) -> float:
        return math.sqrt(sum((float(snapshot[axis]) - target) ** 2 for axis, target in targets.items()))

    def _distance_from_start(self, start_snapshot: dict[str, Any], snapshot: dict[str, Any], targets: dict[str, float]) -> float:
        return math.sqrt(sum((float(snapshot[axis]) - float(start_snapshot[axis])) ** 2 for axis in targets))

    def _wait_for_targets(self, targets: dict[str, float], label: str, *, operation_timeout_s: float) -> dict[str, Any]:
        if self._machine is None:
            raise MachineRuntimeError("No hay telemetría de máquina.")
        start = time.monotonic()
        stable_samples = 0
        required_stable_samples = max(1, int(self.config.stable_samples))
        start_snapshot = self._machine.get_motion_snapshot()
        last_snapshot = start_snapshot
        previous_live_axis = {axis: float(start_snapshot[axis]) for axis in targets}
        best_remaining = self._remaining_distance(start_snapshot, targets)
        last_progress_at = start
        reached_position_at: float | None = None
        wrong_direction_samples = 0
        last_refresh = start
        while time.monotonic() - start <= operation_timeout_s:
            self._assert_safety_for_connection()
            now = time.monotonic()
            if now - last_refresh >= 0.25:
                self._refresh_machine_best_effort()
                last_refresh = now
            last_snapshot = self._machine.get_motion_snapshot()
            reached, positions_ok, last_velocity = self._targets_reached(last_snapshot, targets)
            remaining = self._remaining_distance(last_snapshot, targets)
            traveled = self._distance_from_start(start_snapshot, last_snapshot, targets)
            with self._lock:
                if self._last_movement is not None and self._last_movement.get("label") == label:
                    self._last_movement.update({
                        "observed_position": {axis: float(last_snapshot[axis]) for axis in ("x", "y", "z")},
                        "observed_velocity_mm_s": last_velocity,
                        "position_source": last_snapshot.get("source"),
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
                    })
            live_axis_changed = any(abs(float(last_snapshot[axis]) - previous_live_axis[axis]) >= 0.02 for axis in targets)
            moving_by_velocity = abs(last_velocity) >= self.config.velocity_tolerance_mm_s
            if traveled > self.config.settle_tolerance_mm and remaining > best_remaining + self.config.settle_tolerance_mm:
                wrong_direction_samples += 1
                previous_live_axis = {axis: float(last_snapshot[axis]) for axis in targets}
                if wrong_direction_samples >= max(3, required_stable_samples):
                    detail = self._observed_detail(last_snapshot)
                    raise MachineRuntimeError(f"{label}: la posición se aleja del objetivo. Observado {detail}; objetivo {self._target_detail(targets)}.")
            elif live_axis_changed or moving_by_velocity or remaining < best_remaining - self.config.settle_tolerance_mm:
                best_remaining = min(best_remaining, remaining)
                last_progress_at = now
                wrong_direction_samples = 0
                previous_live_axis = {axis: float(last_snapshot[axis]) for axis in targets}
            if positions_ok:
                reached_position_at = reached_position_at or now
                if now - reached_position_at > self.config.settle_timeout_s:
                    detail = self._observed_detail(last_snapshot)
                    raise MachineRuntimeError(f"{label}: objetivo alcanzado pero la velocidad no se estabilizó. Observado {detail}; velocidad={last_velocity:.3f} mm/s.")
            else:
                reached_position_at = None
            if reached and not self._telemetry_is_stale(now):
                stable_samples += 1
            else:
                stable_samples = 0
            if stable_samples >= required_stable_samples:
                self._clear_resolved_transport_timeout(label)
                return {
                    "observed_position": {axis: float(last_snapshot[axis]) for axis in ("x", "y", "z")},
                    "observed_velocity_mm_s": last_velocity,
                    "position_source": last_snapshot.get("source"),
                    "live_position": last_snapshot.get("live_position"),
                    "commanded_position": last_snapshot.get("commanded_position"),
                    "stable_samples": stable_samples,
                    "elapsed_s": time.monotonic() - start,
                    "progress_remaining_mm": remaining,
                    "result": "confirmado",
                }
            if now - last_progress_at > self.config.no_progress_timeout_s and remaining > self.config.settle_tolerance_mm:
                self._refresh_machine_best_effort()
                checked_snapshot = self._machine.get_motion_snapshot()
                checked_remaining = self._remaining_distance(checked_snapshot, targets)
                checked_reached, _checked_positions_ok, checked_velocity = self._targets_reached(checked_snapshot, targets)
                if checked_reached:
                    self._clear_resolved_transport_timeout(label)
                    return {
                        "observed_position": {axis: float(checked_snapshot[axis]) for axis in ("x", "y", "z")},
                        "observed_velocity_mm_s": checked_velocity,
                        "position_source": checked_snapshot.get("source"),
                        "live_position": checked_snapshot.get("live_position"),
                        "commanded_position": checked_snapshot.get("commanded_position"),
                        "stable_samples": stable_samples,
                        "elapsed_s": time.monotonic() - start,
                        "progress_remaining_mm": checked_remaining,
                        "result": "reconciliado",
                    }
                detail = self._observed_detail(checked_snapshot)
                raise MachineRuntimeError(f"{label}: sin progreso durante {self.config.no_progress_timeout_s:.3f} s. Observado {detail}; objetivo {self._target_detail(targets)}.")
            time.sleep(0.05)
        self._refresh_machine_best_effort()
        final_snapshot = self._machine.get_motion_snapshot()
        reached, _positions_ok, final_velocity = self._targets_reached(final_snapshot, targets)
        if reached:
            self._clear_resolved_transport_timeout(label)
            with self._lock:
                self._event("info", f"{label}: timeout de espera reconciliado por posición física dentro de tolerancia y velocidad cero.")
            return {
                "observed_position": {axis: float(final_snapshot[axis]) for axis in ("x", "y", "z")},
                "observed_velocity_mm_s": final_velocity,
                "position_source": final_snapshot.get("source"),
                "live_position": final_snapshot.get("live_position"),
                "commanded_position": final_snapshot.get("commanded_position"),
                "stable_samples": stable_samples,
                "elapsed_s": time.monotonic() - start,
                "progress_remaining_mm": self._remaining_distance(final_snapshot, targets),
                "result": "reconciliado",
            }
        raise MachineRuntimeError(
            f"Timeout esperando confirmación de {label} ({self._target_detail(targets)}) tras {operation_timeout_s:.3f} s. "
            f"Posición observada: {self._observed_detail(final_snapshot)}; velocidad={final_velocity:.3f} mm/s."
        )

    def _telemetry_is_stale(self, now: float) -> bool:
        with self._lock:
            last_telemetry_at = self._last_telemetry_at
        return last_telemetry_at is None or now - last_telemetry_at > self.config.telemetry_fresh_timeout_s

    def _target_detail(self, targets: dict[str, float]) -> str:
        return ", ".join(f"{axis.upper()}={target:.3f}" for axis, target in targets.items())

    def _observed_detail(self, snapshot: dict[str, Any]) -> str:
        return ", ".join(f"{axis.upper()}={float(snapshot[axis]):.3f}" for axis in ("x", "y", "z"))

    def _wait_for_homing(self, required_axes: set[str]) -> None:
        if self._machine is None:
            raise MachineRuntimeError("No hay telemetría de máquina.")
        start = time.monotonic()
        while time.monotonic() - start <= self.config.home_timeout_s:
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

    def _wait_for_axis(self, axis: str, target: float, label: str) -> None:
        if self._machine is None:
            raise MachineRuntimeError("No hay telemetría de máquina.")
        start = time.monotonic()
        while time.monotonic() - start <= self.config.move_timeout_s:
            self._assert_safety_for_connection()
            snapshot = self._machine.get_motion_snapshot()
            position = float(snapshot[axis])
            velocity = abs(float(snapshot["velocity"]))
            if abs(position - target) <= self.config.settle_tolerance_mm and velocity <= self.config.velocity_tolerance_mm_s:
                self._clear_resolved_transport_timeout(label)
                return
            time.sleep(0.05)
        raise MachineRuntimeError(f"Timeout esperando confirmación de {label}.")

    def _safe_z(self, machine) -> float:
        safe_z = min(max(self.config.safe_z_mm, machine.z_limits.minimum), machine.z_limits.maximum)
        if safe_z < machine.z_limits.minimum or safe_z > machine.z_limits.maximum:
            raise MachineRuntimeError("Z segura fuera de límites descubiertos.")
        return safe_z

    def _arduino_snapshot(self, *, now: float, serial_age: float | None) -> dict[str, Any]:
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
                "reconnects": 0,
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
            "recent": serial_age is not None and serial_age <= self.config.serial_fresh_timeout_s,
            "valid_packets": self._counters.valid_packets,
            "runtime_invalid_packets": self._counters.invalid_packets,
            "runtime_checksum_errors": self._counters.checksum_errors,
            "runtime_disconnects": self._counters.disconnects,
            "packet_frequency_hz": frequency,
            "last_packet": None if self._last_packet is None else self._last_packet.__dict__,
            "last_error": self._last_error,
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

    def _step(self, name: str, status: str, detail: str) -> None:
        with self._lock:
            self._initialization_steps.append(InitializationStep(name=name, status=status, detail=detail, timestamp=_iso_now()))

    def _event(self, level: str, message: str) -> None:
        self._events.append(RuntimeEvent(timestamp=_iso_now(), level=level, message=message))
        self._events = self._events[-100:]
