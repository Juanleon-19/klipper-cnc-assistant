from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import serial


HEADER = 0xAA


DIRECTIONS = {
    0: "CENTER",
    1: "UP",
    2: "DOWN",
    3: "LEFT",
    4: "RIGHT",
    5: "UP_LEFT",
    6: "UP_RIGHT",
    7: "DOWN_LEFT",
    8: "DOWN_RIGHT",
}


@dataclass(frozen=True)
class ControllerPacket:
    direction: str
    joystick_button: bool
    external_button: bool
    probe: bool
    x: int
    y: int


class SerialProtocolError(Exception):
    pass


class SerialDriver:
    def __init__(
        self,
        port: str = "/dev/ttyUSB0",
        baudrate: int = 115200,
        timeout: float = 1.0,
    ) -> None:
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self._serial: Optional[serial.Serial] = None

    def open(self) -> None:
        if self._serial is not None and self._serial.is_open:
            return

        self._serial = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            timeout=self.timeout,
        )

    def close(self) -> None:
        if self._serial is not None:
            self._serial.close()
            self._serial = None

    @staticmethod
    def _checksum(packet: bytes) -> int:
        value = 0
        for b in packet[:7]:
            value ^= b
        return value

    def read_packet(self) -> ControllerPacket:
        if self._serial is None or not self._serial.is_open:
            self.open()

        assert self._serial is not None

        while True:
            header = self._serial.read(1)

            if not header:
                continue

            if header[0] != HEADER:
                continue

            payload = self._serial.read(7)

            if len(payload) != 7:
                continue

            packet = bytes([HEADER]) + payload

            if self._checksum(packet) != packet[7]:
                raise SerialProtocolError("Checksum mismatch")

            direction_id = packet[1]
            flags = packet[2]

            x = packet[3] | (packet[4] << 8)
            y = packet[5] | (packet[6] << 8)

            return ControllerPacket(
                direction=DIRECTIONS.get(direction_id, "UNKNOWN"),
                joystick_button=bool(flags & 0x01),
                external_button=bool(flags & 0x02),
                probe=bool(flags & 0x04),
                x=x,
                y=y,
            )
