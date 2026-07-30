from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import serial


HEADER = 0xAA
PACKET_SIZE = 8


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


@dataclass
class SerialDiagnostics:
    port: str
    baudrate: int
    open: bool = False
    thread_active: bool = False
    bytes_received: int = 0
    packets_complete: int = 0
    valid_packets: int = 0
    invalid_packets: int = 0
    checksum_errors: int = 0
    sync_drops: int = 0
    partial_packets: int = 0
    reconnects: int = 0
    opened_at: float | None = None
    last_byte_at: float | None = None
    last_valid_packet_at: float | None = None
    last_invalid_packet_at: float | None = None
    last_exception: str | None = None

    def snapshot(self, now: float | None = None) -> dict[str, object]:
        current = time.monotonic() if now is None else now
        return {
            "port": self.port,
            "baudrate": self.baudrate,
            "open": self.open,
            "thread_active": self.thread_active,
            "bytes_received": self.bytes_received,
            "packets_complete": self.packets_complete,
            "valid_packets": self.valid_packets,
            "invalid_packets": self.invalid_packets,
            "checksum_errors": self.checksum_errors,
            "sync_drops": self.sync_drops,
            "partial_packets": self.partial_packets,
            "reconnects": self.reconnects,
            "opened_at": self.opened_at,
            "last_byte_age_s": None if self.last_byte_at is None else current - self.last_byte_at,
            "last_valid_packet_age_s": None if self.last_valid_packet_at is None else current - self.last_valid_packet_at,
            "last_invalid_packet_age_s": None if self.last_invalid_packet_at is None else current - self.last_invalid_packet_at,
            "last_exception": self.last_exception,
        }


class SerialProtocolError(Exception):
    pass


class SerialDriver:
    def __init__(
        self,
        port: str = "/dev/ttyUSB0",
        baudrate: int = 115200,
        timeout: float = 1.0,
        startup_delay: float = 2.0,
    ) -> None:
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.startup_delay = startup_delay
        self._serial: Optional[serial.Serial] = None
        self.diagnostics = SerialDiagnostics(port=port, baudrate=baudrate)

    def open(self) -> None:
        if self._serial is not None and self._serial.is_open:
            return

        self._serial = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            timeout=self.timeout,
        )
        now = time.monotonic()
        self.diagnostics.open = True
        self.diagnostics.opened_at = now
        self.diagnostics.reconnects += 1
        self.diagnostics.last_exception = None
        if self.startup_delay > 0:
            time.sleep(self.startup_delay)
            self.reset_input_buffer()

    def reset_input_buffer(self) -> None:
        if self._serial is not None and self._serial.is_open:
            self._serial.reset_input_buffer()

    def close(self) -> None:
        if self._serial is not None:
            self._serial.close()
            self._serial = None
        self.diagnostics.open = False
        self.diagnostics.thread_active = False

    @staticmethod
    def _checksum(packet: bytes) -> int:
        value = 0
        for b in packet[:7]:
            value ^= b
        return value

    @staticmethod
    def _decode_packet(packet: bytes) -> ControllerPacket:
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

    def _read_exact_payload(self) -> bytes | None:
        assert self._serial is not None
        payload = self._serial.read(PACKET_SIZE - 1)
        if payload:
            now = time.monotonic()
            self.diagnostics.bytes_received += len(payload)
            self.diagnostics.last_byte_at = now
        if len(payload) != PACKET_SIZE - 1:
            self.diagnostics.partial_packets += 1
            return None
        return payload

    def read_packet(self) -> ControllerPacket:
        if self._serial is None or not self._serial.is_open:
            self.open()

        assert self._serial is not None

        while True:
            header = self._serial.read(1)

            if not header:
                continue

            now = time.monotonic()
            self.diagnostics.bytes_received += 1
            self.diagnostics.last_byte_at = now

            if header[0] != HEADER:
                self.diagnostics.sync_drops += 1
                continue

            payload = self._read_exact_payload()

            if payload is None:
                continue

            packet = bytes([HEADER]) + payload
            self.diagnostics.packets_complete += 1

            if self._checksum(packet) != packet[7]:
                self.diagnostics.invalid_packets += 1
                self.diagnostics.checksum_errors += 1
                self.diagnostics.last_invalid_packet_at = time.monotonic()
                self.diagnostics.last_exception = "Checksum mismatch"
                continue

            decoded = self._decode_packet(packet)
            self.diagnostics.valid_packets += 1
            self.diagnostics.last_valid_packet_at = time.monotonic()
            self.diagnostics.last_exception = None
            return decoded
