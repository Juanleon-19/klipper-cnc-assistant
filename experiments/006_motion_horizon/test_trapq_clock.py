import asyncio
import json
import os
import time


KLIPPER_SOCKET = os.getenv(
    "KLIPPER_SOCKET",
    "/tmp/klippy_uds",
)

TEST_DISTANCE = 10.0
TEST_SPEED = 10.0

OBSERVATION_TIME = 3.0
QUERY_INTERVAL = 0.050

TRAPQ_REQUEST_ID = 701
GCODE_REQUEST_ID = 702
QUERY_REQUEST_BASE = 1000

MESSAGE_TERMINATOR = b"\x03"


class KlipperAPIClient:
    def __init__(self, socket_path):
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

    async def send(self, request):
        payload = (
            json.dumps(request).encode("utf-8")
            + MESSAGE_TERMINATOR
        )

        self.writer.write(payload)

        await self.writer.drain()

    async def receive(self):
        while (
            MESSAGE_TERMINATOR
            not in self.buffer
        ):
            chunk = await self.reader.read(
                4096
            )

            if not chunk:
                raise ConnectionError(
                    "Klipper API socket closed"
                )

            self.buffer += chunk

        raw_message, self.buffer = (
            self.buffer.split(
                MESSAGE_TERMINATOR,
                1,
            )
        )

        return json.loads(
            raw_message.decode("utf-8")
        )


def build_test_move():
    return "\n".join(
        [
            (
                "SAVE_GCODE_STATE "
                "NAME=trapq_clock_test"
            ),
            "G91",
            (
                f"G1 X{TEST_DISTANCE:.6f} "
                f"F{TEST_SPEED * 60.0:.3f}"
            ),
            (
                "RESTORE_GCODE_STATE "
                "NAME=trapq_clock_test"
            ),
        ]
    )


def parse_row(
    header,
    raw_row,
):
    return dict(
        zip(
            header,
            raw_row,
        )
    )


async def subscribe_trapq(
    client,
):
    request = {
        "id": TRAPQ_REQUEST_ID,
        "method": (
            "motion_report/dump_trapq"
        ),
        "params": {
            "name": "toolhead",
            "response_template": {
                "source": "trapq_clock",
            },
        },
    }

    await client.send(
        request
    )

    while True:
        data = await client.receive()

        if (
            data.get("id")
            != TRAPQ_REQUEST_ID
        ):
            continue

        if "error" in data:
            raise RuntimeError(
                "trapq subscription failed: "
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
                "Invalid trapq header"
            )

        return header


async def query_motion_state(
    client,
    request_id,
):
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

    await client.send(
        request
    )


async def send_gcode(
    client,
    script,
):
    request = {
        "id": GCODE_REQUEST_ID,
        "method": "gcode/script",
        "params": {
            "script": script,
        },
    }

    await client.send(
        request
    )

    while True:
        data = await client.receive()

        if (
            data.get("id")
            != GCODE_REQUEST_ID
        ):
            continue

        if "error" in data:
            raise RuntimeError(
                "G-code failed: "
                + json.dumps(
                    data["error"]
                )
            )

        return


async def main():
    print("=" * 70)
    print("EXPERIMENT 006")
    print("KLIPPER PRINT-TIME / TRAPQ CORRELATION TEST")
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

    print(
        f"Query interval : "
        f"{QUERY_INTERVAL * 1000.0:.1f} ms"
    )

    input(
        "\nVerify at least 15 mm of "
        "free X+ travel and press ENTER..."
    )

    client = KlipperAPIClient(
        KLIPPER_SOCKET
    )

    await client.connect()

    try:
        header = await subscribe_trapq(
            client
        )

        print(
            "\n[TRAPQ SUBSCRIBED]"
        )

        print(
            "Header:"
        )

        for index, field in enumerate(
            header
        ):
            print(
                f"  {index}: {field}"
            )

        await asyncio.sleep(
            0.250
        )

        send_start = time.monotonic()

        await send_gcode(
            client,
            build_test_move(),
        )

        send_end = time.monotonic()

        print(
            "\n[GCODE SCRIPT COMPLETED]"
        )

        print(
            f"API call duration: "
            f"{(
                send_end - send_start
            ) * 1000.0:.3f} ms"
        )

        observation_start = (
            time.monotonic()
        )

        trapq_end_time = None

        query_counter = 0

        next_query_time = (
            observation_start
        )

        samples = []

        print(
            "\n[LIVE PRINT-TIME CORRELATION]"
        )

        while True:
            now = time.monotonic()

            elapsed = (
                now - observation_start
            )

            if (
                elapsed
                >= OBSERVATION_TIME
            ):
                break

            if now >= next_query_time:
                query_counter += 1

                request_id = (
                    QUERY_REQUEST_BASE
                    + query_counter
                )

                await query_motion_state(
                    client,
                    request_id,
                )

                next_query_time = (
                    now + QUERY_INTERVAL
                )

            try:
                data = await asyncio.wait_for(
                    client.receive(),
                    timeout=0.010,
                )

            except asyncio.TimeoutError:
                continue

            if (
                data.get("source")
                == "trapq_clock"
            ):
                params = data.get(
                    "params"
                )

                if not isinstance(
                    params,
                    dict,
                ):
                    continue

                trapq_data = params.get(
                    "data"
                )

                if not isinstance(
                    trapq_data,
                    list,
                ):
                    continue

                for raw_row in trapq_data:
                    row = parse_row(
                        header,
                        raw_row,
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
                        trapq_end_time is None
                        or end_time
                        > trapq_end_time
                    ):
                        trapq_end_time = (
                            end_time
                        )

                print(
                    "\n[TRAPQ UPDATE]"
                )

                print(
                    f"trapq_end = "
                    f"{trapq_end_time:.6f}"
                )

                continue

            response_id = data.get(
                "id"
            )

            if not isinstance(
                response_id,
                int,
            ):
                continue

            if (
                response_id
                <= QUERY_REQUEST_BASE
            ):
                continue

            result = data.get(
                "result"
            )

            if not isinstance(
                result,
                dict,
            ):
                continue

            status = result.get(
                "status"
            )

            if not isinstance(
                status,
                dict,
            ):
                continue

            toolhead = status.get(
                "toolhead"
            )

            motion_report = status.get(
                "motion_report"
            )

            if not isinstance(
                toolhead,
                dict,
            ):
                continue

            if not isinstance(
                motion_report,
                dict,
            ):
                continue

            estimated_print_time = (
                toolhead.get(
                    "estimated_print_time"
                )
            )

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

            if (
                estimated_print_time
                is None
            ):
                continue

            if (
                not isinstance(
                    live_position,
                    list,
                )
                or not live_position
            ):
                continue

            print_time = float(
                estimated_print_time
            )

            x_position = float(
                live_position[0]
            )

            if live_velocity is None:
                velocity_text = "None"

            else:
                velocity_text = (
                    f"{float(live_velocity):.3f}"
                )

            if trapq_end_time is None:
                remaining_horizon = None

                remaining_text = (
                    "unknown"
                )

            else:
                remaining_horizon = (
                    trapq_end_time
                    - print_time
                )

                remaining_text = (
                    f"{remaining_horizon * 1000.0:.3f} ms"
                )

            samples.append(
                {
                    "print_time": print_time,
                    "trapq_end": trapq_end_time,
                    "remaining": remaining_horizon,
                    "x": x_position,
                    "velocity": live_velocity,
                }
            )

            print(
                f"print_time="
                f"{print_time:12.6f}  "
                f"trapq_end="
                f"{trapq_end_time if trapq_end_time is not None else 0.0:12.6f}  "
                f"remaining="
                f"{remaining_text:>14}  "
                f"X="
                f"{x_position:8.3f}  "
                f"V="
                f"{velocity_text}"
            )

        print(
            "\n[RESULTS]"
        )

        print(
            f"Samples collected: "
            f"{len(samples)}"
        )

        if trapq_end_time is None:
            print(
                "Trapq end time was not observed."
            )

            return

        valid_remaining = [
            sample["remaining"]
            for sample in samples
            if (
                sample["remaining"]
                is not None
            )
        ]

        if not valid_remaining:
            print(
                "No correlated print-time samples."
            )

            return

        first_remaining = (
            valid_remaining[0]
        )

        last_remaining = (
            valid_remaining[-1]
        )

        minimum_remaining = min(
            valid_remaining
        )

        maximum_remaining = max(
            valid_remaining
        )

        print(
            f"Trapq end time: "
            f"{trapq_end_time:.6f}"
        )

        print(
            f"First remaining horizon: "
            f"{first_remaining * 1000.0:.3f} ms"
        )

        print(
            f"Last remaining horizon: "
            f"{last_remaining * 1000.0:.3f} ms"
        )

        print(
            f"Minimum remaining horizon: "
            f"{minimum_remaining * 1000.0:.3f} ms"
        )

        print(
            f"Maximum remaining horizon: "
            f"{maximum_remaining * 1000.0:.3f} ms"
        )

        print(
            "\n[INTERPRETATION TARGET]"
        )

        print(
            "trapq_end and "
            "toolhead.estimated_print_time"
        )

        print(
            "should share Klipper's "
            "print-time domain."
        )

        print(
            "\nExpected during motion:"
        )

        print(
            "remaining horizon decreases "
            "toward zero."
        )

        print(
            "\n[OK] print-time correlation "
            "test completed"
        )

    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(
        main()
    )
