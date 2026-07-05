import asyncio
import json
import os
import time

import requests
import websockets


MOONRAKER_URL = os.getenv(
    "MOONRAKER_URL",
    "http://localhost:7125",
)

MOONRAKER_WS = os.getenv(
    "MOONRAKER_WS",
    "ws://localhost:7125/websocket",
)


MOVE_DISTANCE = 10.0
MOVE_SPEED = 20.0

VELOCITY_THRESHOLD = 0.01


def send_gcode(script):
    response = requests.post(
        f"{MOONRAKER_URL}/printer/gcode/script",
        json={
            "script": script,
        },
        timeout=5,
    )

    response.raise_for_status()

    return response.json()


async def main():
    print("=" * 60)
    print("KLIPPER CNC ASSISTANT")
    print("EXPERIMENT 003 - BASIC MOTION")
    print("=" * 60)

    print("\n[INFO] Connecting to Moonraker WebSocket...")

    async with websockets.connect(MOONRAKER_WS) as websocket:
        print("[OK] WebSocket connected")

        subscription = {
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

        await websocket.send(json.dumps(subscription))

        print("[OK] Motion telemetry subscription sent")

        initial_position = None

        while initial_position is None:
            message = await websocket.recv()
            data = json.loads(message)

            if data.get("id") == 1 and "result" in data:
                status = data["result"].get("status", {})

                motion_report = status.get(
                    "motion_report",
                    {},
                )

                initial_position = motion_report.get(
                    "live_position"
                )

        print("\n[INITIAL POSITION]")

        print(
            f"X = {initial_position[0]:.3f} mm\n"
            f"Y = {initial_position[1]:.3f} mm\n"
            f"Z = {initial_position[2]:.3f} mm"
        )

        print(
            f"\n[TEST] Relative X movement: "
            f"{MOVE_DISTANCE:.1f} mm"
        )

        print(
            f"[TEST] Requested speed: "
            f"{MOVE_SPEED:.1f} mm/s"
        )

        input(
            "\nPress ENTER to execute the movement..."
        )

        feedrate = MOVE_SPEED * 60.0

        script = (
            "SAVE_GCODE_STATE NAME=cnc_motion_test\n"
            "G91\n"
            f"G1 X{MOVE_DISTANCE} F{feedrate}\n"
            "RESTORE_GCODE_STATE NAME=cnc_motion_test"
        )

        command_time = time.perf_counter()

        send_gcode(script)

        print("\n[COMMAND SENT]")
        print("[INFO] Waiting for motion...\n")

        motion_started = False

        start_time = None
        stop_time = None

        max_velocity = 0.0

        live_position = initial_position
        live_velocity = 0.0

        final_position = initial_position

        while True:
            message = await websocket.recv()
            data = json.loads(message)

            if data.get("method") != "notify_status_update":
                continue

            status = data["params"][0]

            if "motion_report" not in status:
                continue

            motion_report = status["motion_report"]

            if "live_position" in motion_report:
                live_position = motion_report["live_position"]

            if "live_velocity" in motion_report:
                live_velocity = motion_report["live_velocity"]

            final_position = live_position

            max_velocity = max(
                max_velocity,
                live_velocity,
            )

            now = time.perf_counter()

            print(
                f"\r"
                f"X={live_position[0]:8.3f} mm  "
                f"V={live_velocity:8.3f} mm/s",
                end="",
                flush=True,
            )

            if (
                not motion_started
                and live_velocity > VELOCITY_THRESHOLD
            ):
                motion_started = True
                start_time = now

            elif (
                motion_started
                and live_velocity <= VELOCITY_THRESHOLD
            ):
                stop_time = now
                break

        command_latency = start_time - command_time
        motion_duration = stop_time - start_time

        measured_distance = (
            final_position[0]
            - initial_position[0]
        )

        print("\n\n[RESULTS]")

        print(
            f"Command-to-observed-motion latency: "
            f"{command_latency * 1000:.2f} ms"
        )

        print(
            f"Observed motion duration: "
            f"{motion_duration:.3f} s"
        )

        print(
            f"Maximum live velocity: "
            f"{max_velocity:.3f} mm/s"
        )

        print(
            f"Measured X displacement: "
            f"{measured_distance:.3f} mm"
        )

        print("\n[OK] Experiment completed")


if __name__ == "__main__":
    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        print("\n\n[INFO] Experiment stopped")

    except requests.RequestException as error:
        print("\n[ERROR] Moonraker HTTP request failed")
        print(error)
