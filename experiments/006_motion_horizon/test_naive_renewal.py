import asyncio
import os
import time

from klipper_cnc_assistant.jog.continuous import (
    ContinuousJogController,
)

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


TEST_VELOCITY = 10.0
ACTIVE_TIME = 1.0

STOP_VELOCITY_THRESHOLD = 0.01


async def wait_until_stopped(
    machine,
    timeout=3.0,
):
    start = time.monotonic()

    while True:
        state = (
            machine.get_motion_snapshot()
        )

        if (
            abs(state["velocity"])
            <= STOP_VELOCITY_THRESHOLD
        ):
            return time.monotonic()

        if (
            time.monotonic() - start
            > timeout
        ):
            raise TimeoutError(
                "Machine did not stop "
                "within timeout"
            )

        await asyncio.sleep(
            0.005
        )


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

    continuous = ContinuousJogController(
        jog_controller=jog,
        machine_state=machine,
        horizon=0.100,
        renewal_ratio=0.50,
    )

    telemetry = MoonrakerTelemetry(
        websocket_url=moonraker_ws,
        machine_state=machine,
    )

    telemetry_task = asyncio.create_task(
        telemetry.run()
    )

    controller_task = asyncio.create_task(
        continuous.run()
    )

    await asyncio.sleep(
        0.25
    )

    initial = (
        machine.get_motion_snapshot()
    )

    print("=" * 60)
    print("KLIPPER CNC ASSISTANT")
    print("CONTINUOUS JOG STOP TEST")
    print("=" * 60)

    print(
        f"\nInitial X: "
        f"{initial['x']:.3f} mm"
    )

    print(
        f"Velocity : "
        f"{TEST_VELOCITY:.3f} mm/s"
    )

    print(
        f"Active   : "
        f"{ACTIVE_TIME:.3f} s"
    )

    input(
        "\nVerify free X+ travel "
        "and press ENTER..."
    )

    continuous.set_velocity(
        "x",
        TEST_VELOCITY,
    )

    await asyncio.sleep(
        ACTIVE_TIME
    )

    release_state = (
        machine.get_motion_snapshot()
    )

    release_time = time.monotonic()

    continuous.stop_axis(
        "x"
    )

    print(
        "\n[INPUT RELEASED]"
    )

    print(
        f"X = "
        f"{release_state['x']:.3f} mm"
    )

    print(
        f"V = "
        f"{release_state['velocity']:.3f} mm/s"
    )

    stopped_time = await wait_until_stopped(
        machine
    )

    final_state = (
        machine.get_motion_snapshot()
    )

    stopping_delay = (
        stopped_time - release_time
    )

    additional_displacement = (
        final_state["x"]
        - release_state["x"]
    )

    total_displacement = (
        final_state["x"]
        - initial["x"]
    )

    print(
        "\n[RESULTS]"
    )

    print(
        f"Stopping delay: "
        f"{stopping_delay * 1000:.2f} ms"
    )

    print(
        f"Additional displacement: "
        f"{additional_displacement:.3f} mm"
    )

    print(
        f"Total displacement: "
        f"{total_displacement:.3f} mm"
    )

    print(
        f"Final velocity: "
        f"{final_state['velocity']:.3f} mm/s"
    )

    continuous.shutdown()
    telemetry.stop()

    controller_task.cancel()
    telemetry_task.cancel()

    await asyncio.gather(
        controller_task,
        telemetry_task,
        return_exceptions=True,
    )


if __name__ == "__main__":
    asyncio.run(
        main()
    )
