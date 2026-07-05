import asyncio
import os

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


DISPLAY_PERIOD = 0.05


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


async def display_live_state(
    machine,
):
    while True:
        state = (
            machine.get_motion_snapshot()
        )

        print(
            "\r"
            f"X={state['x']:8.3f} mm  "
            f"Y={state['y']:8.3f} mm  "
            f"Z={state['z']:8.3f} mm  "
            f"V={state['velocity']:8.3f} mm/s",
            end="",
            flush=True,
        )

        await asyncio.sleep(
            DISPLAY_PERIOD
        )


async def run():
    moonraker_url = os.getenv(
        "MOONRAKER_URL",
        "http://localhost:7125",
    )

    moonraker_ws = os.getenv(
        "MOONRAKER_WS",
        "ws://localhost:7125/websocket",
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

    telemetry = MoonrakerTelemetry(
        websocket_url=moonraker_ws,
        machine_state=machine,
    )

    print(
        "\n[LIVE MACHINE STATE]"
    )

    print(
        "Press CTRL+C to stop.\n"
    )

    telemetry_task = asyncio.create_task(
        telemetry.run()
    )

    display_task = asyncio.create_task(
        display_live_state(
            machine
        )
    )

    try:
        await asyncio.gather(
            telemetry_task,
            display_task,
        )

    finally:
        telemetry.stop()

        telemetry_task.cancel()
        display_task.cancel()

        await asyncio.gather(
            telemetry_task,
            display_task,
            return_exceptions=True,
        )


def main():
    try:
        asyncio.run(run())

    except KeyboardInterrupt:
        print(
            "\n\n[INFO] "
            "Klipper CNC Assistant stopped"
        )


if __name__ == "__main__":
    main()
