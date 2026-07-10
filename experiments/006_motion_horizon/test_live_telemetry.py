import asyncio
import os
import time

from klipper_cnc_assistant.jog.controller import (
    JogController,
)

from klipper_cnc_assistant.machine.discovery import (
    discover_machine,
)

from klipper_cnc_assistant.moonraker.client import (
    MoonrakerClient,
)

from klipper_cnc_assistant.moonraker.telemetry import (
    MoonrakerTelemetry,
)


TEST_AXIS = "x"
TEST_DISTANCE = 10.0
TEST_VELOCITY = 10.0

SAMPLE_PERIOD = 0.010
OBSERVATION_TIME = 2.0


async def main():
    moonraker_url = os.getenv(
        "MOONRAKER_URL",
        "http://localhost:7125",
    )

    moonraker_ws = os.getenv(
        "MOONRAKER_WS",
        "ws://localhost:7125/websocket",
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

    telemetry = MoonrakerTelemetry(
        websocket_url=moonraker_ws,
        machine_state=machine,
    )

    telemetry_task = asyncio.create_task(
        telemetry.run()
    )

    await asyncio.sleep(0.500)

    initial = machine.get_motion_snapshot()

    print("=" * 60)
    print("EXPERIMENT 006")
    print("LIVE TELEMETRY DIAGNOSTIC")
    print("=" * 60)

    print(
        f"\nInitial X : "
        f"{initial['x']:.3f} mm"
    )

    print(
        f"Distance  : "
        f"{TEST_DISTANCE:.3f} mm"
    )

    print(
        f"Velocity  : "
        f"{TEST_VELOCITY:.3f} mm/s"
    )

    input(
        "\nVerify free X+ travel "
        "and press ENTER..."
    )

    start_time = time.monotonic()

    jog.move_relative(
        axis=TEST_AXIS,
        distance=TEST_DISTANCE,
        speed=TEST_VELOCITY,
    )

    print("\n[LIVE TELEMETRY]")

    while True:
        elapsed = (
            time.monotonic()
            - start_time
        )

        state = (
            machine.get_motion_snapshot()
        )

        print(
            f"t={elapsed:7.3f} s  "
            f"X={state['x']:8.3f} mm  "
            f"V={state['velocity']:8.3f} mm/s"
        )

        if elapsed >= OBSERVATION_TIME:
            break

        await asyncio.sleep(
            SAMPLE_PERIOD
        )

    final = machine.get_motion_snapshot()

    print("\n[RESULTS]")

    print(
        f"Initial X : "
        f"{initial['x']:.3f} mm"
    )

    print(
        f"Final X   : "
        f"{final['x']:.3f} mm"
    )

    print(
        f"Measured displacement: "
        f"{final['x'] - initial['x']:.3f} mm"
    )

    telemetry.stop()
    telemetry_task.cancel()

    await asyncio.gather(
        telemetry_task,
        return_exceptions=True,
    )


if __name__ == "__main__":
    asyncio.run(
        main()
    )

