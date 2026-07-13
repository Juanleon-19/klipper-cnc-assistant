import os

from klipper_cnc_assistant.input.serial_driver import SerialDriver
from klipper_cnc_assistant.input.command_mapper import CommandMapper

from klipper_cnc_assistant.jog.controller import JogError

from klipper_cnc_assistant.jog.manual import (
    ManualJogController,
)

from klipper_cnc_assistant.jog.profiles import (
    JogMode,
)

from klipper_cnc_assistant.machine.discovery import (
    discover_machine,
)

from klipper_cnc_assistant.moonraker.client import (
    MoonrakerClient,
    MoonrakerError,
)


def main():

    moonraker_url = os.getenv(
        "MOONRAKER_URL",
        "http://localhost:7125",
    )

    client = MoonrakerClient(
        moonraker_url
    )

    machine = discover_machine(
        client
    )

    from klipper_cnc_assistant.jog.controller import JogController

    jog = JogController(
        moonraker_client=client,
        machine_state=machine,
    )

    manual = ManualJogController(
        jog_controller=jog,
        mode=JogMode.NORMAL,
    )

    driver = SerialDriver()

    mapper = CommandMapper()

    driver.open()

    print("=" * 60)
    print("EXPERIMENT 007")
    print("ARDUINO MANUAL CONTROLLER")
    print("=" * 60)

    while True:

        packet = driver.read_packet()

        command = mapper.map(packet)

        try:

            if command.jog_x > 0:

                result = manual.move(
                    axis="x",
                    direction=1,
                )

                print(result)

            elif command.jog_x < 0:

                result = manual.move(
                    axis="x",
                    direction=-1,
                )

                print(result)

            elif command.jog_y > 0:

                result = manual.move(
                    axis="y",
                    direction=1,
                )

                print(result)

            elif command.jog_y < 0:

                result = manual.move(
                    axis="y",
                    direction=-1,
                )

                print(result)

        except (
            JogError,
            MoonrakerError,
        ) as error:

            print(error)


if __name__ == "__main__":
    main()
