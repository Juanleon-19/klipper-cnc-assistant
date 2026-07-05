import asyncio
import json
import os

import websockets


MOONRAKER_WS = os.getenv(
    "MOONRAKER_WS",
    "ws://localhost:7125/websocket",
)


async def main():
    print("=" * 55)
    print("KLIPPER CNC ASSISTANT")
    print("EXPERIMENT 002 - WEBSOCKET POSITION")
    print("=" * 55)

    print(f"\n[INFO] Connecting to: {MOONRAKER_WS}")

    async with websockets.connect(MOONRAKER_WS) as websocket:
        print("[OK] WebSocket connected")

        subscription = {
            "jsonrpc": "2.0",
            "method": "printer.objects.subscribe",
            "params": {
                "objects": {
                    "gcode_move": ["position"],
                    "toolhead": ["position"],
                    "motion_report": [
                        "live_position",
                        "live_velocity",
                    ],
                }
            },
            "id": 1,
        }

        await websocket.send(json.dumps(subscription))

        print("[OK] Subscription sent")
        print("[INFO] Waiting for position updates...\n")

        gcode_position = None
        toolhead_position = None
        live_position = None
        live_velocity = None

        while True:
            message = await websocket.recv()
            data = json.loads(message)

            status = None

            if data.get("id") == 1 and "result" in data:
                status = data["result"].get("status", {})
                print("[INITIAL STATE]")

            elif data.get("method") == "notify_status_update":
                status = data["params"][0]

            if status is None:
                continue

            if "gcode_move" in status:
                gcode_position = status["gcode_move"].get(
                    "position",
                    gcode_position,
                )

            if "toolhead" in status:
                toolhead_position = status["toolhead"].get(
                    "position",
                    toolhead_position,
                )

            if "motion_report" in status:
                live_position = status["motion_report"].get(
                    "live_position",
                    live_position,
                )

                live_velocity = status["motion_report"].get(
                    "live_velocity",
                    live_velocity,
                )

            print("\033[H\033[J", end="")

            print("=" * 55)
            print("KLIPPER CNC ASSISTANT - LIVE POSITION")
            print("=" * 55)

            if gcode_position is not None:
                print(
                    "\nGCODE"
                    f"\nX = {gcode_position[0]:10.3f} mm"
                    f"\nY = {gcode_position[1]:10.3f} mm"
                    f"\nZ = {gcode_position[2]:10.3f} mm"
                )

            if toolhead_position is not None:
                print(
                    "\n\nTOOLHEAD"
                    f"\nX = {toolhead_position[0]:10.3f} mm"
                    f"\nY = {toolhead_position[1]:10.3f} mm"
                    f"\nZ = {toolhead_position[2]:10.3f} mm"
                )

            if live_position is not None:
                print(
                    "\n\nLIVE MOTION"
                    f"\nX = {live_position[0]:10.3f} mm"
                    f"\nY = {live_position[1]:10.3f} mm"
                    f"\nZ = {live_position[2]:10.3f} mm"
                )

            if live_velocity is not None:
                print(
                    f"\nVelocity = {live_velocity:10.3f} mm/s"
                )


if __name__ == "__main__":
    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        print("\n\n[INFO] Experiment stopped")
