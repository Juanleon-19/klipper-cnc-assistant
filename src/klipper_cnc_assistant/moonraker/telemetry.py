import asyncio
import json

import websockets


class MoonrakerTelemetry:
    def __init__(
        self,
        websocket_url,
        machine_state,
    ):
        self.websocket_url = websocket_url

        self.machine_state = machine_state

        self._running = False

    async def _subscribe(
        self,
        websocket,
    ):
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

    def _process_motion_report(
        self,
        motion_report,
    ):
        live_position = motion_report.get(
            "live_position"
        )

        live_velocity = motion_report.get(
            "live_velocity"
        )

        self.machine_state.update_motion(
            live_position=live_position,
            live_velocity=live_velocity,
        )

    def _process_message(
        self,
        data,
    ):
        if data.get("id") == 1:
            result = data.get("result")

            if not isinstance(result, dict):
                return

            status = result.get("status")

            if not isinstance(status, dict):
                return

            motion_report = status.get(
                "motion_report"
            )

            if isinstance(
                motion_report,
                dict,
            ):
                self._process_motion_report(
                    motion_report
                )

            return

        if (
            data.get("method")
            != "notify_status_update"
        ):
            return

        params = data.get("params", [])

        if not params:
            return

        status = params[0]

        if not isinstance(status, dict):
            return

        motion_report = status.get(
            "motion_report"
        )

        if isinstance(
            motion_report,
            dict,
        ):
            self._process_motion_report(
                motion_report
            )

    async def run(self):
        self._running = True

        async with websockets.connect(
            self.websocket_url
        ) as websocket:

            await self._subscribe(
                websocket
            )

            async for message in websocket:
                if not self._running:
                    break

                data = json.loads(message)

                self._process_message(
                    data
                )

    def stop(self):
        self._running = False
