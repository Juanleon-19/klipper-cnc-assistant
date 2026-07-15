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
                    ],
                    "toolhead": [
                        "position",
                        "homed_axes",
                        "axis_minimum",
                        "axis_maximum",
                        "max_velocity",
                        "max_accel",
                    ],
                    "gcode_move": [
                        "gcode_position",
                        "position",
                        "absolute_coordinates",
                        "homing_origin",
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

    def _process_toolhead(
        self,
        toolhead,
    ):
        self.machine_state.update_toolhead(
            position=toolhead.get("position"),
            homed_axes=toolhead.get("homed_axes"),
            axis_minimum=toolhead.get("axis_minimum"),
            axis_maximum=toolhead.get("axis_maximum"),
            max_velocity=toolhead.get("max_velocity"),
            max_accel=toolhead.get("max_accel"),
        )


    def _process_gcode_move(
        self,
        gcode_move,
    ):
        self.machine_state.update_gcode_move(
            gcode_position=gcode_move.get("gcode_position"),
            position=gcode_move.get("position"),
            absolute_coordinates=gcode_move.get("absolute_coordinates"),
            homing_origin=gcode_move.get("homing_origin"),
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

            toolhead = status.get("toolhead")

            if isinstance(toolhead, dict):
                self._process_toolhead(toolhead)

            gcode_move = status.get("gcode_move")

            if isinstance(gcode_move, dict):
                self._process_gcode_move(gcode_move)

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

        toolhead = status.get("toolhead")

        if isinstance(toolhead, dict):
            self._process_toolhead(toolhead)

        gcode_move = status.get("gcode_move")

        if isinstance(gcode_move, dict):
            self._process_gcode_move(gcode_move)

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
