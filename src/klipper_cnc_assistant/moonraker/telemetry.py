from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable

import websockets


class MoonrakerTelemetry:
    def __init__(
        self,
        websocket_url: str,
        machine_state,
        *,
        idle_ping_interval_s: float = 3.0,
        ping_timeout_s: float = 3.0,
        reconnect_delay_s: float = 1.0,
    ) -> None:
        self.websocket_url = websocket_url
        self.machine_state = machine_state
        self._idle_ping_interval_s = idle_ping_interval_s
        self._ping_timeout_s = ping_timeout_s
        self._reconnect_delay_s = reconnect_delay_s
        self._running = False
        self._state_callback: Callable[[str], None] | None = None
        self._snapshot_callback: Callable[[dict[str, object]], None] | None = None
        self._state = 'DISCONNECTED'
        self._last_message_at: float | None = None
        self._last_error: str | None = None
        self._reconnects = 0
        self._connected_once = False

    def set_state_callback(self, callback: Callable[[str], None] | None) -> None:
        self._state_callback = callback

    def set_snapshot_callback(self, callback: Callable[[dict[str, object]], None] | None) -> None:
        self._snapshot_callback = callback

    def snapshot(self) -> dict[str, object]:
        return {
            'state': self._state,
            'connected': self._state == 'CONNECTED',
            'last_message_at': self._last_message_at,
            'last_error': self._last_error,
            'reconnects': self._reconnects,
        }

    def _emit_state(self) -> None:
        if self._snapshot_callback is not None:
            self._snapshot_callback(self.snapshot())
        if self._state_callback is not None:
            self._state_callback(self._legacy_state())

    def _legacy_state(self) -> str:
        if self._state == 'CONNECTED':
            return 'LIVE'
        if self._state in {'STOPPED', 'DISCONNECTED'}:
            return 'DISCONNECTED'
        return self._state

    def _set_state(self, state: str, *, error: str | None = None) -> None:
        self._state = state
        if error is not None:
            self._last_error = error
        elif state == 'CONNECTED':
            self._last_error = None
        self._emit_state()

    async def _subscribe(self, websocket) -> None:
        request = {
            'jsonrpc': '2.0',
            'method': 'printer.objects.subscribe',
            'params': {
                'objects': {
                    'motion_report': ['live_position', 'live_velocity'],
                    'toolhead': ['position', 'homed_axes', 'axis_minimum', 'axis_maximum', 'max_velocity', 'max_accel'],
                    'gcode_move': ['gcode_position', 'position', 'absolute_coordinates', 'homing_origin'],
                }
            },
            'id': 1,
        }
        await websocket.send(json.dumps(request))

    def _process_motion_report(self, motion_report: dict[str, Any]) -> None:
        self.machine_state.update_motion(
            live_position=motion_report.get('live_position'),
            live_velocity=motion_report.get('live_velocity'),
            source='websocket',
        )

    def _process_toolhead(self, toolhead: dict[str, Any]) -> None:
        self.machine_state.update_toolhead(
            position=toolhead.get('position'),
            homed_axes=toolhead.get('homed_axes'),
            axis_minimum=toolhead.get('axis_minimum'),
            axis_maximum=toolhead.get('axis_maximum'),
            max_velocity=toolhead.get('max_velocity'),
            max_accel=toolhead.get('max_accel'),
        )

    def _process_gcode_move(self, gcode_move: dict[str, Any]) -> None:
        self.machine_state.update_gcode_move(
            gcode_position=gcode_move.get('gcode_position'),
            position=gcode_move.get('position'),
            absolute_coordinates=gcode_move.get('absolute_coordinates'),
            homing_origin=gcode_move.get('homing_origin'),
        )

    def _mark_message(self) -> None:
        self._last_message_at = time.monotonic()
        if self._state != 'CONNECTED':
            self._set_state('CONNECTED')
        else:
            self._emit_state()

    def _process_message(self, data: dict[str, Any]) -> None:
        self._mark_message()
        if data.get('id') == 1:
            result = data.get('result')
            if not isinstance(result, dict):
                return
            status = result.get('status')
            if not isinstance(status, dict):
                return
            motion_report = status.get('motion_report')
            if isinstance(motion_report, dict):
                self._process_motion_report(motion_report)
            toolhead = status.get('toolhead')
            if isinstance(toolhead, dict):
                self._process_toolhead(toolhead)
            gcode_move = status.get('gcode_move')
            if isinstance(gcode_move, dict):
                self._process_gcode_move(gcode_move)
            return
        if data.get('method') != 'notify_status_update':
            return
        params = data.get('params', [])
        if not params:
            return
        status = params[0]
        if not isinstance(status, dict):
            return
        motion_report = status.get('motion_report')
        if isinstance(motion_report, dict):
            self._process_motion_report(motion_report)
        toolhead = status.get('toolhead')
        if isinstance(toolhead, dict):
            self._process_toolhead(toolhead)
        gcode_move = status.get('gcode_move')
        if isinstance(gcode_move, dict):
            self._process_gcode_move(gcode_move)

    async def _recv_or_ping(self, websocket) -> str:
        try:
            return await asyncio.wait_for(websocket.recv(), timeout=self._idle_ping_interval_s)
        except asyncio.TimeoutError:
            pong = await websocket.ping()
            await asyncio.wait_for(pong, timeout=self._ping_timeout_s)
            return await asyncio.wait_for(websocket.recv(), timeout=self._idle_ping_interval_s)

    async def run(self) -> None:
        self._running = True
        while self._running:
            try:
                self._set_state('RECONNECTING' if self._connected_once else 'CONNECTING')
                async with websockets.connect(self.websocket_url, ping_interval=None) as websocket:
                    await self._subscribe(websocket)
                    self._connected_once = True
                    self._set_state('CONNECTED')
                    while self._running:
                        message = await self._recv_or_ping(websocket)
                        self._process_message(json.loads(message))
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if not self._running:
                    break
                if self._connected_once:
                    self._reconnects += 1
                    self._set_state('RECONNECTING', error=str(error))
                else:
                    self._set_state('ERROR', error=str(error))
                await asyncio.sleep(self._reconnect_delay_s)
        self._set_state('STOPPED')

    def stop(self) -> None:
        self._running = False
        self._set_state('STOPPED')
