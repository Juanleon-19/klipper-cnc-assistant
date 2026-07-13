#!/usr/bin/env python3

import _thread
import asyncio
import os
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from klipper_cnc_assistant.input.command_mapper import (
    CommandMapper,
    ControllerCommand,
)
from klipper_cnc_assistant.input.serial_driver import (
    SerialDriver,
)
from klipper_cnc_assistant.jog.controller import JogController
from klipper_cnc_assistant.jog.manual import ManualJogController
from klipper_cnc_assistant.jog.profiles import (
    JogMode,
    JogProfile,
    get_jog_profile,
)
from klipper_cnc_assistant.machine.discovery import discover_machine
from klipper_cnc_assistant.moonraker.client import MoonrakerClient
from klipper_cnc_assistant.moonraker.telemetry import MoonrakerTelemetry


MOONRAKER_URL = os.getenv(
    "MOONRAKER_URL",
    "http://localhost:7126",
)

MOONRAKER_WS = os.getenv(
    "MOONRAKER_WS",
    "ws://localhost:7126/websocket",
)

SERIAL_PORT = os.getenv(
    "SERIAL_PORT",
    "/dev/ttyUSB0",
)


def _env_float(
    name: str,
    default: float,
) -> float:
    value = os.getenv(name)
    if value is None:
        return default

    return float(value)


@dataclass(frozen=True)
class ProbeConfig:
    step_distance: float = _env_float(
        "PROBE_STEP_DISTANCE",
        0.050,
    )
    lower_speed: float = _env_float(
        "PROBE_LOWER_SPEED",
        1.000,
    )
    retract_distance: float = _env_float(
        "PROBE_RETRACT_DISTANCE",
        1.000,
    )
    retract_speed: float = _env_float(
        "PROBE_RETRACT_SPEED",
        2.000,
    )
    settle_tolerance: float = _env_float(
        "PROBE_SETTLE_TOLERANCE",
        0.020,
    )
    velocity_tolerance: float = _env_float(
        "PROBE_VELOCITY_TOLERANCE",
        0.050,
    )
    move_timeout: float = _env_float(
        "PROBE_MOVE_TIMEOUT",
        5.000,
    )

    def __post_init__(self) -> None:
        for field_name in (
            "step_distance",
            "lower_speed",
            "retract_distance",
            "retract_speed",
            "settle_tolerance",
            "velocity_tolerance",
            "move_timeout",
        ):
            if getattr(self, field_name) <= 0:
                raise ValueError(
                    f"{field_name} must be positive"
                )


class ProbeSequenceState(Enum):
    IDLE = "IDLE"
    PREPARE = "PREPARE"
    LOWERING = "LOWERING"
    CONTACT_DETECTED = "CONTACT_DETECTED"
    RETRACTING = "RETRACTING"
    COMPLETE = "COMPLETE"
    ABORTED = "ABORTED"


@dataclass
class PendingMove:
    axis: str
    target: float
    speed: float
    started_at: float
    label: str


def run_telemetry(
    telemetry,
    failure,
):
    try:
        asyncio.run(telemetry.run())
    except BaseException as error:
        failure.append(error)
        _thread.interrupt_main()


def cycle_mode(
    current_mode: JogMode,
) -> JogMode:
    order = (
        JogMode.FINE,
        JogMode.NORMAL,
        JogMode.COARSE,
    )

    current_index = order.index(
        current_mode
    )

    return order[
        (current_index + 1) % len(order)
    ]


def format_profile(
    mode: JogMode,
) -> str:
    profile = get_jog_profile(mode)

    return (
        f"{mode.name} "
        f"({profile.distance:.3f} mm at "
        f"{profile.speed:.3f} mm/s)"
    )


def report_manual_mode(
    mode: JogMode,
) -> None:
    print(
        f"[MODE] Active manual mode: "
        f"{format_profile(mode)}"
    )


def is_cardinal_request(
    command: ControllerCommand,
) -> bool:
    return (
        (command.jog_x != 0 and command.jog_y == 0)
        or (command.jog_y != 0 and command.jog_x == 0)
    )


def move_from_command(
    command: ControllerCommand,
    manual: ManualJogController,
):
    if command.jog_x:
        return manual.move(
            axis="x",
            direction=command.jog_x,
        )

    if command.jog_y:
        return manual.move(
            axis="y",
            direction=command.jog_y,
        )

    return None


def copy_discovered_state(
    target_machine,
    discovered_machine,
) -> None:
    target_machine.update_motion(
        live_position=(
            discovered_machine.position.x,
            discovered_machine.position.y,
            discovered_machine.position.z,
        )
    )
    target_machine.homed_axes = (
        discovered_machine.homed_axes
    )
    target_machine.x_limits = (
        discovered_machine.x_limits
    )
    target_machine.y_limits = (
        discovered_machine.y_limits
    )
    target_machine.z_limits = (
        discovered_machine.z_limits
    )
    target_machine.max_velocity = (
        discovered_machine.max_velocity
    )
    target_machine.max_accel = (
        discovered_machine.max_accel
    )


class ProbeSequence:
    def __init__(
        self,
        moonraker_client: MoonrakerClient,
        jog_controller: JogController,
        machine_state,
        config: ProbeConfig,
    ) -> None:
        self.client = moonraker_client
        self.jog = jog_controller
        self.machine = machine_state
        self.config = config

        self.state = (
            ProbeSequenceState.IDLE
        )
        self.pending_move: Optional[
            PendingMove
        ] = None

        self.start_x: Optional[float] = None
        self.start_y: Optional[float] = None
        self.contact_z: Optional[float] = None
        self.abort_reason: Optional[str] = None

    @property
    def active(self) -> bool:
        return self.state not in {
            ProbeSequenceState.IDLE,
            ProbeSequenceState.COMPLETE,
            ProbeSequenceState.ABORTED,
        }

    def start(self) -> bool:
        if self.active:
            return False

        self.pending_move = None
        self.start_x = None
        self.start_y = None
        self.contact_z = None
        self.abort_reason = None

        self._transition(
            ProbeSequenceState.PREPARE
        )
        return True

    def abort(
        self,
        reason: str,
    ) -> None:
        if self.state == ProbeSequenceState.ABORTED:
            return

        self.abort_reason = reason
        self.pending_move = None
        self._transition(
            ProbeSequenceState.ABORTED
        )
        print(
            f"[PROBE][ABORTED] {reason}"
        )

    def update(
        self,
        command: ControllerCommand,
    ) -> None:
        if self.state == ProbeSequenceState.IDLE:
            return

        if self.state == ProbeSequenceState.COMPLETE:
            self._complete_sequence()
            return

        if self.state == ProbeSequenceState.ABORTED:
            return

        try:
            if (
                self.state
                == ProbeSequenceState.PREPARE
            ):
                self._handle_prepare(
                    command
                )
                return

            if (
                self.state
                == ProbeSequenceState.LOWERING
            ):
                self._handle_lowering(
                    command
                )
                return

            if (
                self.state
                == ProbeSequenceState.CONTACT_DETECTED
            ):
                self._handle_contact_detected()
                return

            if (
                self.state
                == ProbeSequenceState.RETRACTING
            ):
                self._handle_retracting()
                return

        except Exception as error:
            self.abort(str(error))

    def _transition(
        self,
        new_state: ProbeSequenceState,
    ) -> None:
        if self.state == new_state:
            return

        print(
            f"[PROBE][STATE] "
            f"{self.state.value} -> "
            f"{new_state.value}"
        )
        self.state = new_state

    def _refresh_machine_state(
        self,
    ) -> None:
        refreshed = discover_machine(
            self.client
        )
        copy_discovered_state(
            self.machine,
            refreshed,
        )

    def _issue_move(
        self,
        axis: str,
        distance: float,
        speed: float,
        label: str,
    ) -> None:
        result = self.jog.move_relative(
            axis=axis,
            distance=distance,
            speed=speed,
        )
        self.pending_move = PendingMove(
            axis=axis,
            target=result["target"],
            speed=speed,
            started_at=time.monotonic(),
            label=label,
        )
        print(
            f"[PROBE][MOVE] {label}: "
            f"{axis.upper()} "
            f"{result['effective_distance']:+.3f} mm "
            f"at {speed:.3f} mm/s "
            f"-> target {result['target']:.3f}"
        )

    def _pending_move_finished(
        self,
    ) -> bool:
        if self.pending_move is None:
            return True

        snapshot = (
            self.machine.get_motion_snapshot()
        )
        current = snapshot[
            self.pending_move.axis
        ]
        velocity = abs(
            snapshot["velocity"]
        )

        if (
            time.monotonic()
            - self.pending_move.started_at
            > self.config.move_timeout
        ):
            raise RuntimeError(
                f"{self.pending_move.label} timed out "
                "waiting for telemetry confirmation"
            )

        if (
            abs(
                current
                - self.pending_move.target
            )
            <= self.config.settle_tolerance
            and velocity
            <= self.config.velocity_tolerance
        ):
            print(
                f"[PROBE][SETTLED] "
                f"{self.pending_move.label}: "
                f"{self.pending_move.axis.upper()}="
                f"{current:.3f}"
            )
            self.pending_move = None
            return True

        return False

    def _handle_prepare(
        self,
        command: ControllerCommand,
    ) -> None:
        if command.probe_triggered:
            raise RuntimeError(
                "Probe input is already active before lowering"
            )

        self._refresh_machine_state()

        if not self.machine.axis_is_homed("z"):
            raise RuntimeError(
                "Axis Z must be homed before probing"
            )

        if (
            self.machine.z_limits.maximum
            <= self.machine.z_limits.minimum
        ):
            raise RuntimeError(
                "Discovered Z limits are invalid"
            )

        snapshot = (
            self.machine.get_motion_snapshot()
        )
        self.start_x = snapshot["x"]
        self.start_y = snapshot["y"]

        print(
            "[PROBE][PREPARE] "
            f"Start XY: "
            f"X={self.start_x:.3f} "
            f"Y={self.start_y:.3f} "
            f"Z={snapshot['z']:.3f}"
        )
        print(
            "[PROBE][PREPARE] "
            f"Z limits: "
            f"{self.machine.z_limits.minimum:.3f} .. "
            f"{self.machine.z_limits.maximum:.3f}"
        )

        self._transition(
            ProbeSequenceState.LOWERING
        )
        self._request_next_lower_step()

    def _request_next_lower_step(
        self,
    ) -> None:
        snapshot = (
            self.machine.get_motion_snapshot()
        )
        current_z = snapshot["z"]
        min_z = self.machine.z_limits.minimum
        remaining = current_z - min_z

        if remaining <= self.config.settle_tolerance:
            raise RuntimeError(
                "Reached minimum Z limit without probe contact"
            )

        step_distance = min(
            self.config.step_distance,
            remaining,
        )

        self._issue_move(
            axis="z",
            distance=-step_distance,
            speed=self.config.lower_speed,
            label="Lowering step",
        )

    def _handle_lowering(
        self,
        command: ControllerCommand,
    ) -> None:
        if command.probe_triggered:
            self._transition(
                ProbeSequenceState.CONTACT_DETECTED
            )
            return

        if not self._pending_move_finished():
            return

        self._request_next_lower_step()

    def _handle_contact_detected(
        self,
    ) -> None:
        if not self._pending_move_finished():
            return

        snapshot = (
            self.machine.get_motion_snapshot()
        )
        self.contact_z = snapshot["z"]

        print(
            "[PROBE][CONTACT] "
            f"Captured point: "
            f"X={self.start_x:.3f} "
            f"Y={self.start_y:.3f} "
            f"Z={self.contact_z:.3f}"
        )

        current_z = snapshot["z"]
        max_z = self.machine.z_limits.maximum
        available_retract = max_z - current_z

        if (
            available_retract
            <= self.config.settle_tolerance
        ):
            raise RuntimeError(
                "Cannot retract because Z is already at its maximum limit"
            )

        retract_distance = min(
            self.config.retract_distance,
            available_retract,
        )

        self._transition(
            ProbeSequenceState.RETRACTING
        )
        self._issue_move(
            axis="z",
            distance=retract_distance,
            speed=self.config.retract_speed,
            label="Safe retract",
        )

    def _handle_retracting(
        self,
    ) -> None:
        if not self._pending_move_finished():
            return

        self._transition(
            ProbeSequenceState.COMPLETE
        )

    def _complete_sequence(
        self,
    ) -> None:
        print(
            "[PROBE][COMPLETE] "
            f"Point saved: "
            f"X={self.start_x:.3f} "
            f"Y={self.start_y:.3f} "
            f"Z={self.contact_z:.3f}"
        )
        self.pending_move = None
        self._transition(
            ProbeSequenceState.IDLE
        )


def main() -> None:
    client = MoonrakerClient(
        MOONRAKER_URL
    )
    machine = discover_machine(client)

    server_info = client.get_server_info()
    if server_info.get("klippy_state") != "ready":
        raise RuntimeError(
            "Klipper is not ready"
        )

    telemetry = MoonrakerTelemetry(
        websocket_url=MOONRAKER_WS,
        machine_state=machine,
    )
    telemetry_failure = []
    telemetry_thread = threading.Thread(
        target=run_telemetry,
        args=(telemetry, telemetry_failure),
        daemon=True,
    )
    telemetry_thread.start()

    driver = SerialDriver(
        port=SERIAL_PORT
    )
    mapper = CommandMapper()
    jog = JogController(
        moonraker_client=client,
        machine_state=machine,
    )
    manual = ManualJogController(
        jog_controller=jog,
        mode=JogMode.FINE,
    )
    probe_sequence = ProbeSequence(
        moonraker_client=client,
        jog_controller=jog,
        machine_state=machine,
        config=ProbeConfig(),
    )

    try:
        driver.open()

        print("=" * 60)
        print(
            "EXPERIMENT 008 - MANUAL JOG + PROBE SEQUENCE"
        )
        print("=" * 60)
        print(f"Moonraker HTTP: {MOONRAKER_URL}")
        print(f"Moonraker WS:   {MOONRAKER_WS}")
        print(f"Serial:         {SERIAL_PORT}")
        print(
            f"Homed axes:     "
            f"{machine.homed_axes or 'none'}"
        )
        report_manual_mode(manual.mode)
        print(
            "[PROBE] Lower step: "
            f"{probe_sequence.config.step_distance:.3f} mm "
            f"at {probe_sequence.config.lower_speed:.3f} mm/s"
        )
        print(
            "[PROBE] Retract:    "
            f"{probe_sequence.config.retract_distance:.3f} mm "
            f"at {probe_sequence.config.retract_speed:.3f} mm/s"
        )
        print(
            "Manual movement remains discrete and cardinal only."
        )
        input(
            "Center the joystick, verify the machine is clear, then press ENTER... "
        )

        ready_for_jog = False
        previous_command = ControllerCommand()

        while True:
            if telemetry_failure:
                raise RuntimeError(
                    "Moonraker telemetry stopped: "
                    f"{telemetry_failure[0]}"
                )

            packet = driver.read_packet()
            command = mapper.map(packet)

            if (
                command.probe_triggered
                and not previous_command.probe_triggered
            ):
                print(
                    "[PROBE][INPUT] Probe signal rising edge"
                )

            if (
                command.joystick_pressed
                and not previous_command.joystick_pressed
            ):
                if probe_sequence.active:
                    print(
                        "[MODE] Joystick button ignored while probe sequence is active"
                    )
                else:
                    next_mode = cycle_mode(
                        manual.mode
                    )
                    manual.set_mode(
                        next_mode
                    )
                    report_manual_mode(
                        manual.mode
                    )

            if (
                command.probe_request
                and not previous_command.probe_request
            ):
                if probe_sequence.active:
                    print(
                        "[PROBE] External button ignored because a probe sequence is already active"
                    )
                else:
                    print(
                        "[PROBE] External button rising edge"
                    )
                    probe_sequence.start()
                    ready_for_jog = False

            probe_sequence.update(command)

            if probe_sequence.state == ProbeSequenceState.ABORTED:
                raise RuntimeError(
                    "Probe sequence aborted: "
                    f"{probe_sequence.abort_reason}"
                )

            if probe_sequence.active:
                ready_for_jog = False
            elif packet.direction == "CENTER":
                ready_for_jog = True
            elif (
                ready_for_jog
                and is_cardinal_request(command)
            ):
                result = move_from_command(
                    command,
                    manual,
                )
                ready_for_jog = False
                profile = get_jog_profile(
                    manual.mode
                )
                print(
                    f"[MOVE] {result['axis'].upper()} "
                    f"{result['effective_distance']:+.3f} mm "
                    f"at {result['speed']:.3f} mm/s "
                    f"[{manual.mode.name} "
                    f"{profile.distance:.3f} mm]"
                )

            previous_command = command

    finally:
        driver.close()
        telemetry.stop()
        telemetry_thread.join(
            timeout=2.0
        )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(
            "\n[STOPPED] Experiment stopped safely"
        )
    except Exception as error:
        print(
            f"\n[STOPPED] Experiment aborted: {error}"
        )
