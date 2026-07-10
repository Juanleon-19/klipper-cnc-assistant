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

from submitted_trajectory import (
    SubmittedTrajectory,
)


TEST_AXIS = "x"

TEST_VELOCITY = 10.0
ACTIVE_TIME = 1.0

TARGET_HORIZON = 0.100
RENEWAL_HORIZON = 0.050

CONTROL_PERIOD = 0.005

POSITION_TOLERANCE = 0.001
VELOCITY_TOLERANCE = 0.01

FINAL_WAIT = 3.0


async def wait_for_motion_start(
    machine,
    initial_position,
):
    while True:
        state = (
            machine.get_motion_snapshot()
        )

        position_changed = (
            abs(
                state[TEST_AXIS]
                - initial_position
            )
            > POSITION_TOLERANCE
        )

        velocity_active = (
            abs(state["velocity"])
            > VELOCITY_TOLERANCE
        )

        if (
            position_changed
            or velocity_active
        ):
            return state

        await asyncio.sleep(
            CONTROL_PERIOD
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

    telemetry = MoonrakerTelemetry(
        websocket_url=moonraker_ws,
        machine_state=machine,
    )

    trajectory = SubmittedTrajectory(
        target_time=TARGET_HORIZON,
        renewal_time=RENEWAL_HORIZON,
    )

    telemetry_task = asyncio.create_task(
        telemetry.run()
    )

    await asyncio.sleep(
        0.500
    )

    initial_state = (
        machine.get_motion_snapshot()
    )

    initial_position = initial_state[
        TEST_AXIS
    ]

    print("=" * 60)
    print("EXPERIMENT 006")
    print("SUBMITTED TRAJECTORY MODEL TEST")
    print("=" * 60)

    print(
        f"\nInitial X : "
        f"{initial_position:.3f} mm"
    )

    print(
        f"Velocity  : "
        f"{TEST_VELOCITY:.3f} mm/s"
    )

    print(
        f"Active    : "
        f"{ACTIVE_TIME:.3f} s"
    )

    print(
        f"Target    : "
        f"{TARGET_HORIZON * 1000:.1f} ms"
    )

    print(
        f"Renewal   : "
        f"{RENEWAL_HORIZON * 1000:.1f} ms"
    )

    input(
        "\nVerify free X+ travel "
        "and press ENTER..."
    )

    trajectory.reset(
        velocity=TEST_VELOCITY
    )

    bootstrap_distance = (
        TEST_VELOCITY
        * TARGET_HORIZON
    )

    result = jog.move_relative(
        axis=TEST_AXIS,
        distance=bootstrap_distance,
        speed=TEST_VELOCITY,
    )

    trajectory.register_submission(
        result["effective_distance"]
    )

    command_count = 1

    print(
        "\n[BOOTSTRAP SENT]"
    )

    print(
        f"Distance = "
        f"{result['effective_distance']:.3f} mm"
    )

    motion_state = (
        await wait_for_motion_start(
            machine=machine,
            initial_position=initial_position,
        )
    )

    trajectory.register_motion_start()

    motion_start_time = time.monotonic()

    print(
        "\n[MOTION START DETECTED]"
    )

    print(
        f"X = "
        f"{motion_state[TEST_AXIS]:.3f} mm"
    )

    print(
        f"V = "
        f"{motion_state['velocity']:.3f} mm/s"
    )

    while (
        time.monotonic()
        - motion_start_time
        < ACTIVE_TIME
    ):
        if trajectory.needs_extension():
            extension = (
                trajectory.calculate_extension()
            )

            result = jog.move_relative(
                axis=TEST_AXIS,
                distance=extension,
                speed=TEST_VELOCITY,
            )

            effective_distance = result[
                "effective_distance"
            ]

            trajectory.register_submission(
                effective_distance
            )

            command_count += 1

            print(
                f"[EXTEND {command_count:02d}] "
                f"distance="
                f"{effective_distance:.3f} "
                f"submitted="
                f"{trajectory.submitted_distance():.3f} "
                f"consumed="
                f"{trajectory.consumed_distance():.3f} "
                f"remaining="
                f"{trajectory.remaining_time() * 1000:.1f} ms"
            )

        await asyncio.sleep(
            CONTROL_PERIOD
        )

    release_state = (
        machine.get_motion_snapshot()
    )

    print(
        "\n[INPUT RELEASED]"
    )

    print(
        f"X = "
        f"{release_state[TEST_AXIS]:.3f} mm"
    )

    print(
        f"V = "
        f"{release_state['velocity']:.3f} mm/s"
    )

    await asyncio.sleep(
        FINAL_WAIT
    )

    final_state = (
        machine.get_motion_snapshot()
    )

    additional_displacement = (
        final_state[TEST_AXIS]
        - release_state[TEST_AXIS]
    )

    total_displacement = (
        final_state[TEST_AXIS]
        - initial_position
    )

    print(
        "\n[RESULTS]"
    )

    print(
        f"Commands sent: "
        f"{command_count}"
    )

    print(
        f"Submitted distance: "
        f"{trajectory.submitted_distance():.3f} mm"
    )

    print(
        f"Estimated consumed distance: "
        f"{trajectory.consumed_distance():.3f} mm"
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
        f"Final X: "
        f"{final_state[TEST_AXIS]:.3f} mm"
    )

    print(
        f"Final velocity: "
        f"{final_state['velocity']:.3f} mm/s"
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
