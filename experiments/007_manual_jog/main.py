#!/usr/bin/env python3

import time

from klipper_cnc_assistant.input.serial_driver import SerialDriver
from klipper_cnc_assistant.input.command_mapper import CommandMapper

from klipper_cnc_assistant.machine.state import MachineState
from klipper_cnc_assistant.moonraker.client import MoonrakerClient
from klipper_cnc_assistant.jog.controller import JogController


MOONRAKER_URL = "http://127.0.0.1:7125"

JOG_SPEED = 20.0
STEP_DISTANCE = 0.20


def main():

    print()
    print("========================================")
    print(" Klipper CNC Assistant")
    print(" Manual Jog")
    print("========================================")
    print()

    machine = MachineState()

    client = MoonrakerClient(
        MOONRAKER_URL,
    )

    jog = JogController(
        client,
        machine,
    )

    driver = SerialDriver()

    mapper = CommandMapper()

    driver.open()

    last_command = None

    while True:

        packet = driver.read_packet()

        command = mapper.map(packet)

        if command != last_command:

            print(command)

            last_command = command

            jog.set_continuous_state(
                x_dir=command.jog_x,
                y_dir=command.jog_y,
                speed=JOG_SPEED,
                step=STEP_DISTANCE,
            )

            if command.jog_x == 0 and command.jog_y == 0:
                jog.stop_continuous()

            if command.probe_request:
                print("Probe routine requested")

            if command.probe_triggered:
                print("Probe triggered")

            if command.joystick_pressed:
                print("Joystick button pressed")

        jog.update_continuous()

        time.sleep(0.01)


if __name__ == "__main__":
    main()


