import asyncio
import json
import os
import time

import websockets


MOONRAKER_WS = os.getenv(
    "MOONRAKER_WS",
    "ws://localhost:7125/websocket",
)


JOG_SPEED = 20.0
ACTIVE_TIME = 2.0
HORIZON = 0.250

VELOCITY_THRESHOLD = 0.01


class MoonrakerState:
    def __init__(self):
        self.live_position = None
        self.live_velocity = 0.0

    def update(self, status):
        motion_report = status.get("motion_report")

        if motion_report is None:
            return

        if "live_position" in motion_report:
            self.live_position = motion_report["live_position"]

        if "live_velocity" in motion_report:
            self.live_velocity = motion_report["live_velocity"]


async def send_gcode(
    websocket,
    script,
    request_id,
):
    request = {
        "jsonrpc": "2.0",
        "method": "printer.gcode.script",
        "params": {
            "script": script,
        },
        "id": request_id,
    }

    await websocket.send(
        json.dumps(request)
    )


async def receive_messages(
    websocket,
    state,
):
    async for message in websocket:
        data = json.loads(message)

        if data.get("method") == "notify_status_update":
            params = data.get("params", [])

            if params:
                status = params[0]

                if isinstance(status, dict):
                    state.update(status)

        elif "result" in data:
            result = data["result"]

            if isinstance(result, dict):
                status = result.get("status")

                if isinstance(status, dict):
                    state.update(status)


async def wait_for_initial_state(
    websocket,
    state,
):
    while state.live_position is None:
        message = await websocket.recv()
        data = json.loads(message)

        if data.get("id") != 1:
            continue

        result = data.get("result")

        if not isinstance(result, dict):
            continue

        status = result.get("status")

        if not isinstance(status, dict):
            continue

        state.update(status)


async def main():
    print("=" * 60)
    print("KLIPPER CNC ASSISTANT")
    print("EXPERIMENT 004 - CONTINUOUS JOG")
    print("=" * 60)

    segment_distance = (
        JOG_SPEED * HORIZON
    )

    feedrate = (
        JOG_SPEED * 60.0
    )

    expected_distance = (
        JOG_SPEED * ACTIVE_TIME
    )

    print("\n[TEST PARAMETERS]")

    print(
        f"Jog speed       : "
        f"{JOG_SPEED:.3f} mm/s"
    )

    print(
        f"Active time     : "
        f"{ACTIVE_TIME:.3f} s"
    )

    print(
        f"Motion horizon  : "
        f"{HORIZON * 1000:.1f} ms"
    )

    print(
        f"Segment distance: "
        f"{segment_distance:.3f} mm"
    )

    print(
        f"Expected nominal distance: "
        f"{expected_distance:.3f} mm"
    )

    print(
        "\n[WARNING] This experiment will move "
        "the X axis in the positive direction."
    )

    input(
        "\nVerify free X+ travel and press ENTER..."
    )

    state = MoonrakerState()

    async with websockets.connect(
        MOONRAKER_WS
    ) as websocket:

        print(
            "\n[INFO] WebSocket connected"
        )

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

        await websocket.send(
            json.dumps(subscription)
        )

        print(
            "[INFO] Waiting for initial state..."
        )

        await wait_for_initial_state(
            websocket,
            state,
        )

        initial_position = list(
            state.live_position
        )

        print("\n[INITIAL POSITION]")

        print(
            f"X = "
            f"{initial_position[0]:.3f} mm"
        )

        receiver = asyncio.create_task(
            receive_messages(
                websocket,
                state,
            )
        )

        script = (
            "SAVE_GCODE_STATE "
            "NAME=cnc_jog_segment\n"
            "G91\n"
            f"G1 X{segment_distance:.6f} "
            f"F{feedrate:.3f}\n"
            "RESTORE_GCODE_STATE "
            "NAME=cnc_jog_segment"
        )

        print("\n[JOG ACTIVE]")

        jog_start = time.perf_counter()

        next_segment_time = jog_start

        command_count = 0
        request_id = 100

        while True:
            now = time.perf_counter()

            elapsed = (
                now - jog_start
            )

            if elapsed >= ACTIVE_TIME:
                break

            if now >= next_segment_time:
                await send_gcode(
                    websocket,
                    script,
                    request_id,
                )

                request_id += 1
                command_count += 1

                next_segment_time += HORIZON

            print(
                "\r"
                f"X={state.live_position[0]:8.3f} mm  "
                f"V={state.live_velocity:8.3f} mm/s  "
                f"Commands={command_count}",
                end="",
                flush=True,
            )

            await asyncio.sleep(0.005)

        stop_request_time = (
            time.perf_counter()
        )

        stop_request_position = (
            state.live_position[0]
        )

        print(
            "\n\n[JOYSTICK RELEASED]"
        )

        print(
            "No additional motion segments "
            "will be sent."
        )

        motion_was_observed = (
            state.live_velocity
            > VELOCITY_THRESHOLD
        )

        while True:
            if (
                state.live_velocity
                > VELOCITY_THRESHOLD
            ):
                motion_was_observed = True

            print(
                "\r"
                f"X={state.live_position[0]:8.3f} mm  "
                f"V={state.live_velocity:8.3f} mm/s",
                end="",
                flush=True,
            )

            if (
                motion_was_observed
                and state.live_velocity
                <= VELOCITY_THRESHOLD
            ):
                break

            await asyncio.sleep(0.005)

        stop_observed_time = (
            time.perf_counter()
        )

        final_position = (
            state.live_position[0]
        )

        receiver.cancel()

        try:
            await receiver

        except asyncio.CancelledError:
            pass

        stopping_delay = (
            stop_observed_time
            - stop_request_time
        )

        additional_distance = (
            final_position
            - stop_request_position
        )

        total_distance = (
            final_position
            - initial_position[0]
        )

        print("\n\n[RESULTS]")

        print(
            f"Commands sent: "
            f"{command_count}"
        )

        print(
            f"Observed stopping delay: "
            f"{stopping_delay * 1000:.2f} ms"
        )

        print(
            "Additional displacement after release: "
            f"{additional_distance:.3f} mm"
        )

        print(
            f"Total measured displacement: "
            f"{total_distance:.3f} mm"
        )

        print(
            f"Final observed velocity: "
            f"{state.live_velocity:.3f} mm/s"
        )

        print(
            "\n[OK] Benchmark completed"
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        print(
            "\n\n[INFO] "
            "Experiment stopped by user"
        )
