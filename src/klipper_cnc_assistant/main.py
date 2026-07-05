import os

from klipper_cnc_assistant.machine.discovery import (
    discover_machine,
)

from klipper_cnc_assistant.moonraker.client import (
    MoonrakerClient,
    MoonrakerError,
)


def print_axis(
    name,
    limits,
):
    print(
        f"{name}: "
        f"{limits.minimum:8.3f} -> "
        f"{limits.maximum:8.3f} mm  "
        f"travel={limits.travel:8.3f} mm"
    )


def main():
    moonraker_url = os.getenv(
        "MOONRAKER_URL",
        "http://localhost:7125",
    )

    print("=" * 60)
    print("KLIPPER CNC ASSISTANT")
    print("=" * 60)

    print(
        f"\n[MOONRAKER]\n"
        f"{moonraker_url}"
    )

    client = MoonrakerClient(
        moonraker_url
    )

    try:
        server_info = (
            client.get_server_info()
        )

        klippy_state = server_info.get(
            "klippy_state"
        )

        print(
            f"\n[KLIPPER]\n"
            f"State: {klippy_state}"
        )

        if klippy_state != "ready":
            print(
                "\n[ERROR] "
                "Klipper is not ready"
            )

            return

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
            f"\n[ERROR]\n{error}"
        )

        return

    print("\n[MACHINE POSITION]")

    print(
        f"X = {machine.position.x:8.3f} mm"
    )

    print(
        f"Y = {machine.position.y:8.3f} mm"
    )

    print(
        f"Z = {machine.position.z:8.3f} mm"
    )

    print("\n[MACHINE AXES]")

    print_axis(
        "X",
        machine.x_limits,
    )

    print_axis(
        "Y",
        machine.y_limits,
    )

    print_axis(
        "Z",
        machine.z_limits,
    )

    print("\n[MACHINE STATE]")

    print(
        f"Homed axes : "
        f"{machine.homed_axes or 'none'}"
    )

    print(
        f"Fully homed: "
        f"{machine.is_homed}"
    )

    print(
        f"Max velocity: "
        f"{machine.max_velocity:.3f} mm/s"
    )

    print(
        f"Max accel   : "
        f"{machine.max_accel:.3f} mm/s^2"
    )

    print(
        "\n[OK] Machine discovery completed"
    )


if __name__ == "__main__":
    main()

