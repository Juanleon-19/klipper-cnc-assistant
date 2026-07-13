#!/usr/bin/env python3

import _thread
import asyncio
import os
import threading

from klipper_cnc_assistant.input.command_mapper import (
    CommandMapper,
    ControllerCommand,
)
from klipper_cnc_assistant.input.serial_driver import (
    SerialDriver,
    SerialProtocolError,
)
from klipper_cnc_assistant.jog.controller import (
    JogController,
    JogError,
)
from klipper_cnc_assistant.jog.manual import (
    ManualJogController,
)
from klipper_cnc_assistant.jog.profiles import JogMode
from klipper_cnc_assistant.machine.discovery import (
    discover_machine,
)
from klipper_cnc_assistant.moonraker.client import (
    MoonrakerClient,
    MoonrakerError,
)
from klipper_cnc_assistant.moonraker.telemetry import (
    MoonrakerTelemetry,
)


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


def run_telemetry(
    telemetry,
    failure,
):
    try:
        asyncio.run(telemetry.run())
    except BaseException as error:
        failure.append(error)
        _thread.interrupt_main()


def report_button_events(
    command,
    previous_command,
):
    if command.joystick_pressed and not previous_command.joystick_pressed:
        print("[EVENT] Joystick button pressed")

    if command.probe_request != previous_command.probe_request:
        edge = "rising" if command.probe_request else "falling"
        print(f"[EVENT] External button {edge} edge")

    if command.probe_triggered and not previous_command.probe_triggered:
        print("[EVENT] Probe triggered")


def move_from_command(
    command,
    manual,
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


def main():
    client = MoonrakerClient(MOONRAKER_URL)
    machine = discover_machine(client)

    server_info = client.get_server_info()
    if server_info.get("klippy_state") != "ready":
        raise RuntimeError("Klipper is not ready")

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

    driver = SerialDriver(port=SERIAL_PORT)
    mapper = CommandMapper()
    jog = JogController(
        moonraker_client=client,
        machine_state=machine,
    )
    manual = ManualJogController(
        jog_controller=jog,
        mode=JogMode.FINE,
    )

    try:
        driver.open()

        print("=" * 60)
        print("EXPERIMENT 007 - ARDUINO DISCRETE MANUAL JOG")
        print("=" * 60)
        print(f"Moonraker: {MOONRAKER_URL}")
        print(f"Serial: {SERIAL_PORT}")
        print(f"Homed axes: {machine.homed_axes or 'none'}")
        print("Profile: FINE (0.100 mm at 2.000 mm/s)")
        print("Only CENTER -> X+/X-/Y+/Y- transitions request motion.")
        input("Center the joystick, verify the machine is clear, then press ENTER... ")

        ready_for_jog = False
        previous_command = ControllerCommand()

        while True:
            if telemetry_failure:
                raise RuntimeError(
                    f"Moonraker telemetry stopped: {telemetry_failure[0]}"
                )

            packet = driver.read_packet()
            command = mapper.map(packet)
            report_button_events(command, previous_command)

            if packet.direction == "CENTER":
                ready_for_jog = True
            elif (
                ready_for_jog
                and (
                    (command.jog_x != 0 and command.jog_y == 0)
                    or (command.jog_y != 0 and command.jog_x == 0)
                )
            ):
                result = move_from_command(command, manual)
                ready_for_jog = False
                print(
                    f"[MOVE] {result['axis'].upper()} "
                    f"{result['effective_distance']:+.3f} mm "
                    f"at {result['speed']:.3f} mm/s"
                )

            previous_command = command

    finally:
        driver.close()
        telemetry.stop()
        telemetry_thread.join(timeout=2.0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[STOPPED] Experiment stopped safely")
    except (
        JogError,
        MoonrakerError,
        SerialProtocolError,
        RuntimeError,
        OSError,
        ValueError,
    ) as error:
        print(f"\n[STOPPED] Experiment aborted: {error}")
