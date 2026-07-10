import asyncio
import os
import time

from trapq_horizon import TrapQHorizon


KLIPPER_SOCKET = os.getenv(
    "KLIPPER_SOCKET",
    "/tmp/klippy_uds",
)

TEST_DISTANCE = 10.0
TEST_SPEED = 10.0

OBSERVATION_TIME = 3.0


async def send_test_move(
    horizon: TrapQHorizon,
):
    script = "\n".join(
        [
            "SAVE_GCODE_STATE NAME=trapq_horizon_test",
            "G91",
            (
                f"G1 X{TEST_DISTANCE:.6f} "
                f"F{TEST_SPEED * 60.0:.3f}"
            ),
            (
                "RESTORE_GCODE_STATE "
                "NAME=trapq_horizon_test"
            ),
        ]
    )

    request = {
        "id": 10001,
        "method": "gcode/script",
        "params": {
            "script": script,
        },
    }

    await horizon.client.send(
        request
    )


def format_value(
    value,
    decimals=3,
):
    if value is None:
        return "None"

    return f"{value:.{decimals}f}"


async def main():
    print("=" * 70)
    print("EXPERIMENT 006")
    print("TRAPQ HORIZON COMPONENT TEST")
    print("=" * 70)

    print(
        "\nKlipper API socket:"
    )

    print(
        KLIPPER_SOCKET
    )

    print(
        f"\nDistance : "
        f"{TEST_DISTANCE:.3f} mm"
    )

    print(
        f"Velocity : "
        f"{TEST_SPEED:.3f} mm/s"
    )

    input(
        "\nVerify at least 15 mm of "
        "free X+ travel and press ENTER..."
    )

    horizon = TrapQHorizon(
        socket_path=KLIPPER_SOCKET,
        query_interval=0.050,
    )

    await horizon.connect()

    try:
        print(
            "\n[CONNECTED]"
        )

        await horizon.wait_ready()

        print(
            "\n[INITIAL STATE]"
        )

        print(
            f"Print time : "
            f"{format_value(horizon.print_time, 6)}"
        )

        print(
            f"TrapQ end  : "
            f"{format_value(horizon.trapq_end_time, 6)}"
        )

        print(
            f"Horizon    : "
            f"{format_value(horizon.remaining_time_ms)} ms"
        )

        print(
            f"X          : "
            f"{format_value(horizon.x)} mm"
        )

        print(
            f"Velocity   : "
            f"{format_value(horizon.live_velocity)} mm/s"
        )

        await send_test_move(
            horizon
        )

        print(
            "\n[COMMAND SENT]"
        )

        observation_start = (
            time.monotonic()
        )

        last_print_time = None

        print(
            "\n[LIVE HORIZON]"
        )

        while True:
            elapsed = (
                time.monotonic()
                - observation_start
            )

            if elapsed >= OBSERVATION_TIME:
                break

            current_print_time = (
                horizon.print_time
            )

            if (
                current_print_time is not None
                and current_print_time
                != last_print_time
            ):
                last_print_time = (
                    current_print_time
                )

                print(
                    f"print_time="
                    f"{format_value(horizon.print_time, 6):>12}  "
                    f"trapq_end="
                    f"{format_value(horizon.trapq_end_time, 6):>12}  "
                    f"horizon="
                    f"{format_value(horizon.remaining_time_ms):>9} ms  "
                    f"X="
                    f"{format_value(horizon.x):>8}  "
                    f"V="
                    f"{format_value(horizon.live_velocity):>8}"
                )

            await asyncio.sleep(
                0.010
            )

        print(
            "\n[FINAL STATE]"
        )

        print(
            f"Print time : "
            f"{format_value(horizon.print_time, 6)}"
        )

        print(
            f"TrapQ end  : "
            f"{format_value(horizon.trapq_end_time, 6)}"
        )

        print(
            f"Horizon    : "
            f"{format_value(horizon.remaining_time_ms)} ms"
        )

        print(
            f"X          : "
            f"{format_value(horizon.x)} mm"
        )

        print(
            f"Velocity   : "
            f"{format_value(horizon.live_velocity)} mm/s"
        )

        print(
            "\n[OK] TrapQ horizon component "
            "test completed"
        )

    finally:
        await horizon.close()


if __name__ == "__main__":
    asyncio.run(
        main()
    )
