import asyncio
import os
import time

from motion_horizon_controller import (
    MotionHorizonController,
)
from trapq_horizon import TrapQHorizon


KLIPPER_SOCKET = os.getenv(
    "KLIPPER_SOCKET",
    "/tmp/klippy_uds",
)

TEST_VELOCITY = 10.0
ACTIVE_TIME = 1.0

TARGET_HORIZON = 0.100
RENEWAL_INTERVAL = 0.010

SETTLE_TIME = 2.0


def fmt(value, decimals=3):
    if value is None:
        return "None"

    return f"{value:.{decimals}f}"


async def main():
    print("=" * 70)
    print("EXPERIMENT 006")
    print("SUBMITTED HORIZON CONTROLLER TEST")
    print("=" * 70)

    print(
        f"\nKlipper API socket:\n"
        f"{KLIPPER_SOCKET}"
    )

    print(
        f"\nVelocity       : "
        f"{TEST_VELOCITY:.3f} mm/s"
    )

    print(
        f"Active time    : "
        f"{ACTIVE_TIME:.3f} s"
    )

    print(
        f"Target horizon : "
        f"{TARGET_HORIZON * 1000.0:.1f} ms"
    )

    print(
        f"Renewal        : "
        f"{RENEWAL_INTERVAL * 1000.0:.1f} ms"
    )

    input(
        "\nVerify at least 20 mm of "
        "free X+ travel and press ENTER..."
    )

    horizon = TrapQHorizon(
        socket_path=KLIPPER_SOCKET,
        query_interval=0.050,
    )

    controller = MotionHorizonController(
        horizon=horizon,
        target_horizon=TARGET_HORIZON,
        renewal_interval=RENEWAL_INTERVAL,
    )

    await horizon.connect()

    try:
        await horizon.wait_ready()
        await controller.start()

        initial_x = horizon.x

        print("\n[INITIAL STATE]")
        print(f"X     : {fmt(initial_x)} mm")
        print(
            f"TrapQ : "
            f"{fmt(horizon.remaining_time_ms)} ms"
        )
        print(
            f"Control: "
            f"{fmt(controller.control_horizon_ms)} ms"
        )
        print(
            f"State : {controller.state.name}"
        )

        controller.activate(
            velocity=TEST_VELOCITY,
            direction=1.0,
        )

        print("\n[INPUT ACTIVE]")

        active_start = time.monotonic()

        last_print_time = None
        last_state = None

        while (
            time.monotonic() - active_start
            < ACTIVE_TIME
        ):
            state_changed = (
                controller.state != last_state
            )

            telemetry_changed = (
                horizon.print_time is not None
                and horizon.print_time
                != last_print_time
            )

            if (
                state_changed
                or telemetry_changed
            ):
                last_state = controller.state
                last_print_time = horizon.print_time

                print(
                    f"state="
                    f"{controller.state.name:<18}  "
                    f"trapq="
                    f"{fmt(horizon.remaining_time_ms):>9} ms  "
                    f"control="
                    f"{fmt(controller.control_horizon_ms):>9} ms  "
                    f"X="
                    f"{fmt(horizon.x):>8}  "
                    f"V="
                    f"{fmt(horizon.live_velocity):>8}  "
                    f"commands="
                    f"{controller.commands_sent}"
                )

            await asyncio.sleep(0.005)

        release_x = horizon.x
        release_velocity = horizon.live_velocity

        release_trapq_horizon = (
            horizon.remaining_time
        )

        release_control_horizon = (
            controller.control_horizon
        )

        controller.release()

        print("\n[INPUT RELEASED]")

        print(
            f"State   : "
            f"{controller.state.name}"
        )

        print(
            f"X       : "
            f"{fmt(release_x)} mm"
        )

        print(
            f"Velocity: "
            f"{fmt(release_velocity)} mm/s"
        )

        print(
            f"TrapQ horizon : "
            f"{fmt(horizon.remaining_time_ms)} ms"
        )

        control_horizon_ms = None

        if release_control_horizon is not None:
            control_horizon_ms = (
                release_control_horizon
                * 1000.0
            )

        print(
            f"Control horizon: "
            f"{fmt(control_horizon_ms)} ms"
        )

        await asyncio.sleep(SETTLE_TIME)

        final_x = horizon.x
        final_velocity = horizon.live_velocity

        total_displacement = None
        additional_displacement = None

        if (
            initial_x is not None
            and final_x is not None
        ):
            total_displacement = (
                final_x - initial_x
            )

        if (
            release_x is not None
            and final_x is not None
        ):
            additional_displacement = (
                final_x - release_x
            )

        trapq_stop_ms = None

        if release_trapq_horizon is not None:
            trapq_stop_ms = (
                max(
                    release_trapq_horizon,
                    0.0,
                )
                * 1000.0
            )

        print("\n[RESULTS]")

        print(
            f"Commands sent: "
            f"{controller.commands_sent}"
        )

        print(
            f"TrapQ stopping horizon: "
            f"{fmt(trapq_stop_ms)} ms"
        )

        print(
            f"Control stopping horizon: "
            f"{fmt(control_horizon_ms)} ms"
        )

        print(
            f"Additional displacement: "
            f"{fmt(additional_displacement)} mm"
        )

        print(
            f"Total displacement: "
            f"{fmt(total_displacement)} mm"
        )

        print(
            f"Final X: "
            f"{fmt(final_x)} mm"
        )

        print(
            f"Final velocity: "
            f"{fmt(final_velocity)} mm/s"
        )

        print(
            "\n[OK] Submitted horizon "
            "controller test completed"
        )

    finally:
        await controller.close()
        await horizon.close()


if __name__ == "__main__":
    asyncio.run(main())
