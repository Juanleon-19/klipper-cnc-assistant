from __future__ import annotations

import os
import threading
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
    exclusive_requested: bool = False
    exclusive_supported: bool | None = None

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
            "exclusive_requested": self.exclusive_requested,
            "exclusive_supported": self.exclusive_supported,
        }


class SerialProtocolError(Exception):
    pass


class SerialProtocolStaleError(SerialProtocolError):
    pass


class SerialReadCancelled(Exception):
    pass


class SerialDriver:
    def __init__(
        self,
        port: str = "/dev/ttyUSB0",
        baudrate: int = 115200,
        timeout: float = 1.0,
        startup_delay: float = 2.0,
        valid_packet_timeout: float = 2.0,
    ) -> None:
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.startup_delay = startup_delay
        self.valid_packet_timeout = valid_packet_timeout
        self._serial: Optional[serial.Serial] = None
        self._serial_lock = threading.RLock()
        self._cancel_requested = threading.Event()
        self.diagnostics = SerialDiagnostics(port=port, baudrate=baudrate)

    def open(self) -> None:
        with self._serial_lock:
            if self._serial is not None and self._serial.is_open:
                return
            if self._cancel_requested.is_set():
                raise SerialReadCancelled("Apertura serial cancelada.")

        options = {
            "port": self.port,
            "baudrate": self.baudrate,
            "timeout": self.timeout,
        }
        serial_port: serial.Serial
        if os.name == "posix":
            self.diagnostics.exclusive_requested = True
            try:
                serial_port = serial.Serial(**options, exclusive=True)
                self.diagnostics.exclusive_supported = True
            except TypeError as error:
                if "exclusive" not in str(error):
                    raise
                serial_port = serial.Serial(**options)
                self.diagnostics.exclusive_supported = False
        else:
            serial_port = serial.Serial(**options)
            self.diagnostics.exclusive_requested = False
            self.diagnostics.exclusive_supported = None

        with self._serial_lock:
            self._serial = serial_port
        now = time.monotonic()
        self.diagnostics.open = True
        self.diagnostics.opened_at = now
        self.diagnostics.reconnects += 1
        self.diagnostics.last_exception = None
        if self._cancel_requested.is_set():
            raise SerialReadCancelled("Apertura serial cancelada.")
        if self.startup_delay > 0:
            if self._cancel_requested.wait(timeout=self.startup_delay):
                raise SerialReadCancelled("Espera de arranque serial cancelada.")
            self.reset_input_buffer()

    def reset_input_buffer(self) -> None:
        with self._serial_lock:
            serial_port = self._serial
        if serial_port is not None and serial_port.is_open:
            serial_port.reset_input_buffer()

    def cancel_read(self) -> bool:
        """Wake a pending read without transferring descriptor ownership."""
        self._cancel_requested.set()
        with self._serial_lock:
            serial_port = self._serial
        if serial_port is None or not serial_port.is_open:
            return False
        cancel = getattr(serial_port, "cancel_read", None)
        if cancel is None:
            return False
        try:
            cancel()
        except Exception:
            return False
        return True

    def close(self) -> None:
        with self._serial_lock:
            serial_port = self._serial
            self._serial = None
        try:
            if serial_port is not None:
                serial_port.close()
        finally:
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
        with self._serial_lock:
            serial_port = self._serial
        if serial_port is None or not serial_port.is_open:
            if self._cancel_requested.is_set():
                raise SerialReadCancelled("Lectura serial cancelada.")
            raise serial.SerialException("El descriptor serial se cerró durante la lectura.")
        payload = serial_port.read(PACKET_SIZE - 1)
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
        started = time.monotonic()
        self.diagnostics.thread_active = True

        while True:
            if self._cancel_requested.is_set():
                raise SerialReadCancelled("Lectura serial cancelada.")
            if self.valid_packet_timeout > 0 and time.monotonic() - started >= self.valid_packet_timeout:
                message = "No se recibió un paquete válido dentro del tiempo de frescura serial."
                self.diagnostics.last_exception = message
                raise SerialProtocolStaleError(message)

            with self._serial_lock:
                serial_port = self._serial
            if serial_port is None or not serial_port.is_open:
                if self._cancel_requested.is_set():
                    raise SerialReadCancelled("Lectura serial cancelada.")
                raise serial.SerialException("El descriptor serial se cerró durante la lectura.")
            header = serial_port.read(1)

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
