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

TRAPQ_REQUEST_ID = 601
GCODE_REQUEST_ID = 602

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


def build_long_move():
    return "\n".join(
        [
            "SAVE_GCODE_STATE NAME=trapq_long_move",
            "G91",
            (
                f"G1 X{TEST_DISTANCE:.6f} "
                f"F{TEST_SPEED * 60.0:.3f}"
            ),
            "RESTORE_GCODE_STATE NAME=trapq_long_move",
        ]
    )


def build_segmented_move():
    commands = [
        "SAVE_GCODE_STATE NAME=trapq_segmented_move",
        "G91",
    ]

    for _ in range(10):
        commands.append(
            (
                "G1 X1.000000 "
                f"F{TEST_SPEED * 60.0:.3f}"
            )
        )

    commands.append(
        "RESTORE_GCODE_STATE "
        "NAME=trapq_segmented_move"
    )

    return "\n".join(commands)


async def subscribe_trapq(client):
    request = {
        "id": TRAPQ_REQUEST_ID,
        "method": "motion_report/dump_trapq",
        "params": {
            "name": "toolhead",
            "response_template": {
                "source": "trapq_test",
            },
        },
    }

    await client.send(request)

    while True:
        data = await client.receive()

        if data.get("id") != TRAPQ_REQUEST_ID:
            continue

        if "error" in data:
            raise RuntimeError(
                "trapq subscription failed: "
                + json.dumps(data["error"])
            )

        result = data.get("result", {})

        header = result.get("header")

        if not isinstance(header, list):
            raise RuntimeError(
                "Invalid trapq header"
            )

        return header


async def send_gcode(client, script):
    request = {
        "id": GCODE_REQUEST_ID,
        "method": "gcode/script",
        "params": {
            "script": script,
        },
    }

    await client.send(request)

    while True:
        data = await client.receive()

        if data.get("id") != GCODE_REQUEST_ID:
            continue

        if "error" in data:
            raise RuntimeError(
                "G-code failed: "
                + json.dumps(data["error"])
            )

        return


def parse_row(header, raw_row):
    return dict(
        zip(
            header,
            raw_row,
        )
    )


def format_vector(vector):
    return (
        "["
        + ", ".join(
            f"{float(value):.3f}"
            for value in vector
        )
        + "]"
    )


async def observe_trapq(
    client,
    header,
):
    start_time = time.monotonic()

    event_count = 0
    row_count = 0

    rows = []

    while True:
        elapsed = (
            time.monotonic()
            - start_time
        )

        remaining = (
            OBSERVATION_TIME
            - elapsed
        )

        if remaining <= 0:
            break

        try:
            data = await asyncio.wait_for(
                client.receive(),
                timeout=remaining,
            )

        except asyncio.TimeoutError:
            break

        if data.get("source") != "trapq_test":
            continue

        params = data.get("params")

        if not isinstance(params, dict):
            continue

        trapq_data = params.get("data")

        if not isinstance(trapq_data, list):
            continue

        event_count += 1

        print(
            f"\n[TRAPQ EVENT {event_count:02d}]"
        )

        print(
            f"rows = {len(trapq_data)}"
        )

        for raw_row in trapq_data:
            row = parse_row(
                header,
                raw_row,
            )

            row_count += 1

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

            row["end_time"] = end_time

            rows.append(row)

            print(
                f"\n  [ROW {row_count:03d}]"
            )

            print(
                f"  time           = "
                f"{move_time:.6f}"
            )

            print(
                f"  duration       = "
                f"{duration:.6f}"
            )

            print(
                f"  end_time       = "
                f"{end_time:.6f}"
            )

            print(
                f"  start_velocity = "
                f"{float(row['start_velocity']):.6f}"
            )

            print(
                f"  acceleration   = "
                f"{float(row['acceleration']):.6f}"
            )

            print(
                "  start_position = "
                + format_vector(
                    row["start_position"]
                )
            )

            print(
                "  direction      = "
                + format_vector(
                    row["direction"]
                )
            )

    return {
        "event_count": event_count,
        "row_count": row_count,
        "rows": rows,
    }


def summarize_result(result):
    rows = result["rows"]

    print(
        f"\nTrapq events : "
        f"{result['event_count']}"
    )

    print(
        f"Trapq rows   : "
        f"{result['row_count']}"
    )

    if not rows:
        print(
            "No trapq rows observed."
        )

        return

    first_time = min(
        float(row["time"])
        for row in rows
    )

    last_end = max(
        float(row["end_time"])
        for row in rows
    )

    trajectory_span = (
        last_end
        - first_time
    )

    print(
        f"First trapq time : "
        f"{first_time:.6f}"
    )

    print(
        f"Last trapq end   : "
        f"{last_end:.6f}"
    )

    print(
        f"Trajectory span  : "
        f"{trajectory_span:.6f} s"
    )


async def run_test(
    title,
    script,
):
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)

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

        print("Header:")

        for index, field in enumerate(
            header
        ):
            print(
                f"  {index}: {field}"
            )

        await asyncio.sleep(0.250)

        send_start = time.monotonic()

        await send_gcode(
            client,
            script,
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

        result = await observe_trapq(
            client,
            header,
        )

        print(
            "\n[TEST RESULTS]"
        )

        summarize_result(result)

        return result

    finally:
        await client.close()


async def main():
    print("=" * 70)
    print("EXPERIMENT 006")
    print("RAW KLIPPER TRAPQ DIAGNOSTIC")
    print("=" * 70)

    print(
        f"\nKlipper API socket:"
    )

    print(KLIPPER_SOCKET)

    print(
        "\nThis test compares:"
    )

    print(
        "A: one 10 mm move"
    )

    print(
        "B: ten 1 mm moves"
    )

    print(
        f"\nVelocity: "
        f"{TEST_SPEED:.3f} mm/s"
    )

    print(
        "\nTotal requested X+ travel: "
        "20 mm"
    )

    input(
        "\nVerify at least 25 mm of "
        "free X+ travel and press ENTER..."
    )

    long_result = await run_test(
        title=(
            "TEST A — ONE LONG MOVE"
        ),
        script=build_long_move(),
    )

    await asyncio.sleep(1.0)

    segmented_result = await run_test(
        title=(
            "TEST B — TEN SHORT MOVES"
        ),
        script=build_segmented_move(),
    )

    print()
    print("=" * 70)
    print("COMPARISON")
    print("=" * 70)

    print(
        f"\nLong move rows      : "
        f"{long_result['row_count']}"
    )

    print(
        f"Segmented move rows : "
        f"{segmented_result['row_count']}"
    )

    print(
        "\n[OK] trapq diagnostic completed"
    )


if __name__ == "__main__":
    asyncio.run(main())
