import asyncio
import json
from typing import Optional


MESSAGE_TERMINATOR = b"\x03"

TRAPQ_REQUEST_ID = 801
QUERY_REQUEST_BASE = 9000


class KlipperSocketClient:
    def __init__(self, socket_path: str):
        self.socket_path = socket_path

        self.reader = None
        self.writer = None

        self.buffer = b""

    async def connect(self):
        self.reader, self.writer = (
            await asyncio.open_unix_connection(
                self.socket_path
            )
        )

    async def close(self):
        if self.writer is None:
            return

        self.writer.close()

        await self.writer.wait_closed()

    async def send(self, request: dict):
        payload = (
            json.dumps(request).encode("utf-8")
            + MESSAGE_TERMINATOR
        )

        self.writer.write(payload)

        await self.writer.drain()

    async def receive(self) -> dict:
        while MESSAGE_TERMINATOR not in self.buffer:
            chunk = await self.reader.read(4096)

            if not chunk:
                raise ConnectionError(
                    "Klipper API socket closed"
                )

            self.buffer += chunk

        raw_message, self.buffer = self.buffer.split(
            MESSAGE_TERMINATOR,
            1,
        )

        return json.loads(
            raw_message.decode("utf-8")
        )


class TrapQHorizon:
    def __init__(
        self,
        socket_path: str,
        query_interval: float = 0.050,
    ):
        self.socket_path = socket_path
        self.query_interval = query_interval

        self.client = KlipperSocketClient(
            socket_path
        )

        self.header = None

        self.print_time: Optional[float] = None
        self.trapq_end_time: Optional[float] = None

        self.live_position = None
        self.live_velocity: Optional[float] = None

        self._query_counter = 0

        self._receiver_task = None
        self._query_task = None

        self._running = False
        self._ready_event = asyncio.Event()

    @property
    def remaining_time(self) -> Optional[float]:
        if self.print_time is None:
            return None

        if self.trapq_end_time is None:
            return None

        return (
            self.trapq_end_time
            - self.print_time
        )

    @property
    def remaining_time_ms(self) -> Optional[float]:
        remaining = self.remaining_time

        if remaining is None:
            return None

        return remaining * 1000.0

    @property
    def x(self) -> Optional[float]:
        if not isinstance(
            self.live_position,
            list,
        ):
            return None

        if len(self.live_position) < 1:
            return None

        return float(
            self.live_position[0]
        )

    @property
    def y(self) -> Optional[float]:
        if not isinstance(
            self.live_position,
            list,
        ):
            return None

        if len(self.live_position) < 2:
            return None

        return float(
            self.live_position[1]
        )

    @property
    def z(self) -> Optional[float]:
        if not isinstance(
            self.live_position,
            list,
        ):
            return None

        if len(self.live_position) < 3:
            return None

        return float(
            self.live_position[2]
        )

    async def connect(self):
        await self.client.connect()

        await self._subscribe_trapq()

        self._running = True

        self._receiver_task = asyncio.create_task(
            self._receiver_loop()
        )

        self._query_task = asyncio.create_task(
            self._query_loop()
        )

    async def close(self):
        self._running = False

        tasks = [
            self._receiver_task,
            self._query_task,
        ]

        for task in tasks:
            if task is not None:
                task.cancel()

        for task in tasks:
            if task is None:
                continue

            try:
                await task

            except asyncio.CancelledError:
                pass

        await self.client.close()

    async def wait_ready(
        self,
        timeout: float = 5.0,
    ):
        await asyncio.wait_for(
            self._ready_event.wait(),
            timeout=timeout,
        )

    async def _subscribe_trapq(self):
        request = {
            "id": TRAPQ_REQUEST_ID,
            "method": "motion_report/dump_trapq",
            "params": {
                "name": "toolhead",
                "response_template": {
                    "source": "trapq_horizon",
                },
            },
        }

        await self.client.send(request)

        while True:
            data = await self.client.receive()

            if data.get("id") != TRAPQ_REQUEST_ID:
                continue

            if "error" in data:
                raise RuntimeError(
                    "TrapQ subscription failed: "
                    + json.dumps(
                        data["error"]
                    )
                )

            result = data.get(
                "result",
                {},
            )

            header = result.get(
                "header"
            )

            if not isinstance(
                header,
                list,
            ):
                raise RuntimeError(
                    "Invalid TrapQ header"
                )

            self.header = header

            return

    async def _query_loop(self):
        while self._running:
            self._query_counter += 1

            request_id = (
                QUERY_REQUEST_BASE
                + self._query_counter
            )

            request = {
                "id": request_id,
                "method": "objects/query",
                "params": {
                    "objects": {
                        "toolhead": [
                            "estimated_print_time",
                        ],
                        "motion_report": [
                            "live_position",
                            "live_velocity",
                        ],
                    },
                },
            }

            await self.client.send(request)

            await asyncio.sleep(
                self.query_interval
            )

    async def _receiver_loop(self):
        while self._running:
            data = await self.client.receive()

            if (
                data.get("source")
                == "trapq_horizon"
            ):
                self._handle_trapq_event(
                    data
                )

                continue

            response_id = data.get("id")

            if not isinstance(
                response_id,
                int,
            ):
                continue

            if response_id <= QUERY_REQUEST_BASE:
                continue

            self._handle_query_response(
                data
            )

    def _handle_trapq_event(
        self,
        data: dict,
    ):
        if self.header is None:
            return

        params = data.get("params")

        if not isinstance(
            params,
            dict,
        ):
            return

        trapq_data = params.get("data")

        if not isinstance(
            trapq_data,
            list,
        ):
            return

        for raw_row in trapq_data:
            row = dict(
                zip(
                    self.header,
                    raw_row,
                )
            )

            move_time = float(
                row["time"]
            )

            duration = float(
                row["duration"]
            )

            end_time = (
                move_time
                + duration
            )

            if (
                self.trapq_end_time is None
                or end_time
                > self.trapq_end_time
            ):
                self.trapq_end_time = (
                    end_time
                )

    def _handle_query_response(
        self,
        data: dict,
    ):
        result = data.get("result")

        if not isinstance(
            result,
            dict,
        ):
            return

        status = result.get("status")

        if not isinstance(
            status,
            dict,
        ):
            return

        toolhead = status.get("toolhead")

        motion_report = status.get(
            "motion_report"
        )

        if isinstance(
            toolhead,
            dict,
        ):
            estimated_print_time = (
                toolhead.get(
                    "estimated_print_time"
                )
            )

            if estimated_print_time is not None:
                self.print_time = float(
                    estimated_print_time
                )

        if isinstance(
            motion_report,
            dict,
        ):
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

            if isinstance(
                live_position,
                list,
            ):
                self.live_position = (
                    live_position
                )

            if live_velocity is not None:
                self.live_velocity = float(
                    live_velocity
                )

        if self.print_time is not None:
            self._ready_event.set()
