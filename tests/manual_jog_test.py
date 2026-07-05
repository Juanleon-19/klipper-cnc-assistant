import os
import time

from klipper_cnc_assistant.machine.discovery import (
    discover_machine,
)

from klipper_cnc_assistant.moonraker.client import (
    MoonrakerClient,
)

from klipper_cnc_assistant.jog.controller import (
    JogController,
    JogError,
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

    jog = JogController(
        moonraker_client=client,
        machine_state=machine,
    )

    print("=" * 60)
    print("KLIPPER CNC ASSISTANT")
    print("MANUAL JOG TEST")
    print("=" * 60)

    print(
        f"\nCurrent X: "
        f"{machine.position.x:.3f} mm"
    )

    print(
        "\nThis test will request:"
    )

    print(
        "Axis     : X"
    )

    print(
        "Distance : +5.000 mm"
    )

    print(
        "Speed    : 10.000 mm/s"
    )

    input(
        "\nVerify free X+ travel "
        "and press ENTER..."
    )

    try:
        result = jog.move_relative(
            axis="x",
            distance=5.0,
            speed=10.0,
        )

    except JogError as error:
        print(
            f"\n[JOG ERROR]\n{error}"
        )

        return

    print("\n[COMMAND ACCEPTED]")

    print(
        f"Current position : "
        f"{result['current_position']:.3f} mm"
    )

    print(
        f"Requested target : "
        f"{result['requested_target']:.3f} mm"
    )

    print(
        f"Applied target   : "
        f"{result['target']:.3f} mm"
    )

    print(
        f"Effective move   : "
        f"{result['effective_distance']:.3f} mm"
    )

    print(
        f"Limit applied    : "
        f"{result['limit_applied']}"
    )

    time.sleep(1.0)

    updated_machine = discover_machine(
        client
    )

    print("\n[FINAL POSITION]")

    print(
        f"X = "
        f"{updated_machine.position.x:.3f} mm"
    )


if __name__ == "__main__":
    main()

