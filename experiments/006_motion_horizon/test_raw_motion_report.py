import asyncio
import json
import os
import time

import websockets


OBSERVATION_TIME = 3.0

TEST_DISTANCE = 10.0
TEST_SPEED = 10.0


async def send_test_move():
    import requests

    moonraker_url = os.getenv(
        "MOONRAKER_URL",
        "http://localhost:7125",
    )

    script = (
        "SAVE_GCODE_STATE "
        "NAME=raw_motion_report_test\n"
        "G91\n"
        f"G1 X{TEST_DISTANCE:.6f} "
        f"F{TEST_SPEED * 60.0:.3f}\n"
        "RESTORE_GCODE_STATE "
        "NAME=raw_motion_report_test"
    )

    response = requests.post(
        f"{moonraker_url}/printer/gcode/script",
        json={
            "script": script,
        },
        timeout=5.0,
    )

    response.raise_for_status()


async def main():
    websocket_url = os.getenv(
        "MOONRAKER_WS",
        "ws://localhost:7125/websocket",
    )

    print("=" * 70)
    print("EXPERIMENT 006")
    print("RAW MOTION_REPORT WEBSOCKET DIAGNOSTIC")
    print("=" * 70)

    print(
        f"\nDistance : "
        f"{TEST_DISTANCE:.3f} mm"
    )

    print(
        f"Speed    : "
        f"{TEST_SPEED:.3f} mm/s"
    )

    input(
        "\nVerify free X+ travel "
        "and press ENTER..."
    )

    async with websockets.connect(
        websocket_url
    ) as websocket:

        request = {
            "jsonrpc": "2.0",
            "method": "printer.objects.subscribe",
            "params": {
                "objects": {
                    "motion_report": [
                        "live_position",
                        "live_velocity",
                    ]
                }
            },
            "id": 1,
        }

        await websocket.send(
            json.dumps(request)
        )

        print(
            "\n[WEBSOCKET CONNECTED]"
        )

        initial_message = await websocket.recv()

        initial_time = time.monotonic()

        initial_data = json.loads(
            initial_message
        )

        print(
            "\n[INITIAL MESSAGE]"
        )

        print(
            json.dumps(
                initial_data,
                indent=2,
            )
        )

        await send_test_move()

        command_time = time.monotonic()

        print(
            "\n[COMMAND SENT]"
        )

        print(
            "\n[RAW MOTION_REPORT EVENTS]"
        )

        event_count = 0

        last_event_time = None

        while True:
            elapsed = (
                time.monotonic()
                - command_time
            )

            if elapsed >= OBSERVATION_TIME:
                break

            try:
                message = await asyncio.wait_for(
                    websocket.recv(),
                    timeout=(
                        OBSERVATION_TIME
                        - elapsed
                    ),
                )

            except asyncio.TimeoutError:
                break

            receive_time = time.monotonic()

            data = json.loads(
                message
            )

            if (
                data.get("method")
                != "notify_status_update"
            ):
                continue

            params = data.get(
                "params",
                [],
            )

            if not params:
                continue

            status = params[0]

            if not isinstance(
                status,
                dict,
            ):
                continue

            motion_report = status.get(
                "motion_report"
            )

            if not isinstance(
                motion_report,
                dict,
            ):
                continue

            event_count += 1

            event_elapsed = (
                receive_time
                - command_time
            )

            if last_event_time is None:
                interval_ms = None
            else:
                interval_ms = (
                    receive_time
                    - last_event_time
                ) * 1000.0

            last_event_time = receive_time

            live_position = (
                motion_report.get(
                    "live_position"
                )
            )

            live_velocity = (
                motion_report.get(
                    "live_velocity"
                )
            )

            print(
                f"\n[EVENT {event_count:02d}]"
            )

            print(
                f"t = "
                f"{event_elapsed:.6f} s"
            )

            if interval_ms is None:
                print(
                    "dt = first event"
                )
            else:
                print(
                    f"dt = "
                    f"{interval_ms:.3f} ms"
                )

            print(
                f"live_position = "
                f"{live_position}"
            )

            print(
                f"live_velocity = "
                f"{live_velocity}"
            )

            print(
                "raw = "
                + json.dumps(
                    motion_report
                )
            )

        total_time = (
            time.monotonic()
            - initial_time
        )

        print(
            "\n[RESULTS]"
        )

        print(
            f"motion_report events: "
            f"{event_count}"
        )

        print(
            f"Observation time: "
            f"{total_time:.3f} s"
        )


if __name__ == "__main__":
    asyncio.run(
        main()
    )
