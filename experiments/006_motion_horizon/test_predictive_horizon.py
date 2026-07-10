import asyncio
import os
import time

from klipper_cnc_assistant.jog.controller import (
    JogController,
    JogError,
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

from predictive_horizon import (
    PredictiveMotionHorizon,
)


TEST_AXIS = "x"
TEST_VELOCITY = 10.0
ACTIVE_TIME = 1.0

TARGET_HORIZON = 0.100
RENEWAL_HORIZON = 0.050

CONTROL_PERIOD = 0.005

STOP_VELOCITY_THRESHOLD = 0.01
SETTLE_TIME = 0.250
POSITION_TOLERANCE = 0.001

MOTION_TIMEOUT = 5.0


async def wait_for_motion_and_settle(
    machine,
    initial_position,
    timeout=MOTION_TIMEOUT,
):
    start_time = time.monotonic()

    motion_observed = False
    motion_start_time = None

    stable_since = None
    previous_position = None

    while True:
        now = time.monotonic()

        state = (
            machine.get_motion_snapshot()
        )

        position = state[TEST_AXIS]
        velocity = state["velocity"]

        position_changed = (
            abs(
                position
                - initial_position
            )
            > POSITION_TOLERANCE
        )

        velocity_active = (
            abs(velocity)
            > STOP_VELOCITY_THRESHOLD
        )

        if not motion_observed:
            if (
                position_changed
                or velocity_active
            ):
                motion_observed = True
                motion_start_time = now

                print(
                    "\n[MOTION OBSERVED]"
                )

                print(
                    f"X = "
                    f"{position:.3f} mm"
                )

                print(
                    f"V = "
                    f"{velocity:.3f} mm/s"
                )

        else:
            velocity_stopped = (
                abs(velocity)
                <= STOP_VELOCITY_THRESHOLD
            )

            if previous_position is None:
                position_stable = False
            else:
                position_stable = (
                    abs(
                        position
                        - previous_position
                    )
                    <= POSITION_TOLERANCE
                )

            if (
                velocity_stopped
                and position_stable
            ):
                if stable_since is None:
                    stable_since = now

                elif (
                    now - stable_since
                    >= SETTLE_TIME
                ):
                    return {
                        "motion_start_time": (
                            motion_start_time
                        ),
                        "settled_time": now,
                    }

            else:
                stable_since = None

        previous_position = position

        if (
            now - start_time
            > timeout
        ):
            if not motion_observed:
                raise TimeoutError(
                    "No machine motion was observed "
                    "within timeout"
                )

            raise TimeoutError(
                "Machine did not settle "
                "within timeout"
            )

        await asyncio.sleep(
            0.005
        )


async def run_predictive_control(
    machine,
    jog,
    horizon,
    active_event,
):
    state = (
        machine.get_motion_snapshot()
    )

    horizon.reset(
        state[TEST_AXIS]
    )

    command_count = 0

    while active_event.is_set():
        if horizon.needs_extension():
            extension = (
                horizon.calculate_extension(
                    TEST_VELOCITY
                )
            )

            if abs(extension) > 1e-9:
                try:
                    result = jog.move_relative(
                        axis=TEST_AXIS,
                        distance=extension,
                        speed=abs(
                            TEST_VELOCITY
                        ),
                    )

                except JogError:
                    active_event.clear()
                    raise

                effective_distance = result[
                    "effective_distance"
                ]

                horizon.register_extension(
                    distance=effective_distance,
                    velocity=TEST_VELOCITY,
                )

                command_count += 1

                state = (
                    machine.get_motion_snapshot()
                )

                print(
                    f"[EXTEND {command_count:02d}] "
                    f"observed="
                    f"{state[TEST_AXIS]:.3f} "
                    f"distance="
                    f"{effective_distance:.3f} "
                    f"remaining="
                    f"{horizon.remaining_time() * 1000:.1f} ms "
                    f"planned="
                    f"{horizon.get_planned_position():.3f}"
                )

        await asyncio.sleep(
            CONTROL_PERIOD
        )

    return command_count


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

    horizon = PredictiveMotionHorizon(
        target_time=TARGET_HORIZON,
        renewal_time=RENEWAL_HORIZON,
    )

    telemetry = MoonrakerTelemetry(
        websocket_url=moonraker_ws,
        machine_state=machine,
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

    print("=" * 60)
    print("EXPERIMENT 006")
    print("PREDICTIVE MOTION HORIZON TEST")
    print("=" * 60)

    print(
        f"\nInitial X : "
        f"{initial_state['x']:.3f} mm"
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

    print(
        f"Settle    : "
        f"{SETTLE_TIME * 1000:.1f} ms"
    )

    input(
        "\nVerify free X+ travel "
        "and press ENTER..."
    )

    test_start_time = time.monotonic()

    active_event = asyncio.Event()
    active_event.set()

    controller_task = asyncio.create_task(
        run_predictive_control(
            machine,
            jog,
            horizon,
            active_event,
        )
    )

    await asyncio.sleep(
        ACTIVE_TIME
    )

    release_state = (
        machine.get_motion_snapshot()
    )

    release_time = time.monotonic()

    active_event.clear()

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

    command_count = await controller_task

    motion_result = (
        await wait_for_motion_and_settle(
            machine=machine,
            initial_position=initial_state[
                TEST_AXIS
            ],
        )
    )

    motion_start_time = motion_result[
        "motion_start_time"
    ]

    settled_time = motion_result[
        "settled_time"
    ]

    final_state = (
        machine.get_motion_snapshot()
    )

    command_to_motion_latency = (
        motion_start_time
        - test_start_time
    )

    stopping_delay = (
        settled_time
        - release_time
        - SETTLE_TIME
    )

    stopping_delay = max(
        0.0,
        stopping_delay,
    )

    additional_displacement = (
        final_state["x"]
        - release_state["x"]
    )

    total_displacement = (
        final_state["x"]
        - initial_state["x"]
    )

    planned_position = (
        horizon.get_planned_position()
    )

    planning_error = (
        planned_position
        - final_state["x"]
    )

    print(
        "\n[RESULTS]"
    )

    print(
        f"Commands sent: "
        f"{command_count}"
    )

    print(
        f"Command-to-motion latency: "
        f"{command_to_motion_latency * 1000:.2f} ms"
    )

    print(
        f"Estimated stopping delay: "
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
        f"Planned final position: "
        f"{planned_position:.3f} mm"
    )

    print(
        f"Observed final position: "
        f"{final_state['x']:.3f} mm"
    )

    print(
        f"Planning error: "
        f"{planning_error:.3f} mm"
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
