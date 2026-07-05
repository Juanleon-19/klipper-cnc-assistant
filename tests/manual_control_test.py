import os

from klipper_cnc_assistant.jog.controller import (
    JogController,
    JogError,
)

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


MOVE_COMMANDS = {
    "x+": ("x", 1),
    "x-": ("x", -1),
    "y+": ("y", 1),
    "y-": ("y", -1),
    "z+": ("z", 1),
    "z-": ("z", -1),
}


MODE_COMMANDS = {
    "coarse": JogMode.COARSE,
    "normal": JogMode.NORMAL,
    "fine": JogMode.FINE,
}


def print_help():
    print("\n[COMMANDS]")

    print(
        "Motion : "
        "x+  x-  y+  y-  z+  z-"
    )

    print(
        "Modes  : "
        "coarse  normal  fine"
    )

    print(
        "Other  : "
        "position  help  quit"
    )


def print_position(
    client,
):
    machine = discover_machine(
        client
    )

    print(
        "\n[POSITION]"
    )

    print(
        f"X = {machine.position.x:8.3f} mm"
    )

    print(
        f"Y = {machine.position.y:8.3f} mm"
    )

    print(
        f"Z = {machine.position.z:8.3f} mm"
    )


def main():
    moonraker_url = os.getenv(
        "MOONRAKER_URL",
        "http://localhost:7125",
    )

    client = MoonrakerClient(
        moonraker_url
    )

    try:
        machine = discover_machine(
            client
        )

    except (
        MoonrakerError,
        RuntimeError,
        KeyError,
        TypeError,
        ValueError,
    ) as error:
        print(
            f"\n[CONNECTION ERROR]\n{error}"
        )

        return

    jog = JogController(
        moonraker_client=client,
        machine_state=machine,
    )

    manual = ManualJogController(
        jog_controller=jog,
        mode=JogMode.NORMAL,
    )

    print("=" * 60)
    print("KLIPPER CNC ASSISTANT")
    print("MANUAL CONTROL TEST")
    print("=" * 60)

    print(
        "\nCurrent mode: NORMAL"
    )

    print_help()

    print_position(
        client
    )

    while True:
        command = input(
            f"\n[{manual.mode.value.upper()}] > "
        )

        command = (
            command.strip().lower()
        )

        if not command:
            continue

        if command == "quit":
            print(
                "\n[INFO] "
                "Manual control stopped"
            )

            break

        if command == "help":
            print_help()

            continue

        if command == "position":
            print_position(
                client
            )

            continue

        if command in MODE_COMMANDS:
            mode = MODE_COMMANDS[
                command
            ]

            manual.set_mode(
                mode
            )

            print(
                f"\n[MODE] "
                f"{mode.value.upper()}"
            )

            continue

        if command in MOVE_COMMANDS:
            axis, direction = (
                MOVE_COMMANDS[command]
            )

            try:
                result = manual.move(
                    axis=axis,
                    direction=direction,
                )

            except (
                JogError,
                MoonrakerError,
                ValueError,
            ) as error:
                print(
                    f"\n[MOTION ERROR]\n"
                    f"{error}"
                )

                continue

            print(
                "\n[MOVE]"
            )

            print(
                f"Axis     : "
                f"{result['axis'].upper()}"
            )

            print(
                f"Distance : "
                f"{result['effective_distance']:+.3f} mm"
            )

            print(
                f"Speed    : "
                f"{result['speed']:.3f} mm/s"
            )

            print(
                f"Target   : "
                f"{result['target']:.3f} mm"
            )

            print(
                f"Limited  : "
                f"{result['limit_applied']}"
            )

            continue

        print(
            f"\n[UNKNOWN COMMAND] "
            f"{command}"
        )


if __name__ == "__main__":
    main()
