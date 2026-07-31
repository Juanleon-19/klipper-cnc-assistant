from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable

from serial.tools import list_ports

from .serial_driver import ControllerPacket, SerialDriver


class ArduinoConnectionState(StrEnum):
    DISCONNECTED = "DISCONNECTED"
    DISCOVERING = "DISCOVERING"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    DEGRADED = "DEGRADED"
    RETRY_WAIT = "RETRY_WAIT"
    STOPPED = "STOPPED"


@dataclass(frozen=True)
class UsbIdentity:
    port: str
    vid: int | None = None
    pid: int | None = None
    serial_number: str | None = None
    product: str | None = None
    manufacturer: str | None = None

    @property
    def exact(self) -> bool:
        return self.vid is not None and self.pid is not None and bool(self.serial_number)

    def snapshot(self) -> dict[str, object]:
        return {
            "port": self.port,
            "vid": self.vid,
            "pid": self.pid,
            "serial_number": self.serial_number,
            "product": self.product,
            "manufacturer": self.manufacturer,
            "exact_identity": self.exact,
        }


@dataclass
class ArduinoConnectionSnapshot:
    state: str
    generation: int
    configured_port: str | None
    connected_port: str | None
    usb_identity: dict[str, object] | None
    known_identity: dict[str, object] | None
    reconnects: int
    rejected_devices: int
    retry_wait_s: float | None
    last_error: str | None
    thread_alive: bool
    open: bool


class ArduinoConnectionManager:
    def __init__(
        self,
        *,
        configured_port: str | None,
        baudrate: int,
        startup_delay: float,
        driver_factory: Callable[..., SerialDriver] = SerialDriver,
        on_packet: Callable[[ControllerPacket, int], None] | None = None,
        on_session_started: Callable[[int, UsbIdentity | None], None] | None = None,
        on_session_lost: Callable[[str], None] | None = None,
        on_state_change: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        self._configured_port = configured_port
        self._baudrate = baudrate
        self._startup_delay = startup_delay
        self._driver_factory = driver_factory
        self._on_packet = on_packet
        self._on_session_started = on_session_started
        self._on_session_lost = on_session_lost
        self._on_state_change = on_state_change
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._reconnect_requested = threading.Event()
        self._thread: threading.Thread | None = None
        self._driver: SerialDriver | None = None
        self._state = ArduinoConnectionState.DISCONNECTED
        self._generation = 0
        self._reconnects = 0
        self._rejected_devices = 0
        self._retry_wait_s: float | None = None
        self._last_error: str | None = None
        self._connected_port: str | None = None
        self._connected_identity: UsbIdentity | None = None
        self._known_identity: UsbIdentity | None = None

    @property
    def thread(self) -> threading.Thread | None:
        return self._thread

    @property
    def driver(self) -> SerialDriver | None:
        return self._driver

    def start(self) -> None:
        state_snapshot: dict[str, object] | None = None
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._reconnect_requested.clear()
            state_snapshot = self._set_state_locked(ArduinoConnectionState.DISCOVERING)
            thread = threading.Thread(target=self._run, name="arduino-connection", daemon=True)
            self._thread = thread
            thread.start()
        self._emit_state_change(state_snapshot)

    def stop(self) -> None:
        state_snapshot: dict[str, object] | None = None
        with self._lock:
            self._stop.set()
            self._reconnect_requested.set()
            driver = self._driver
        if driver is not None:
            driver.close()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)
        with self._lock:
            self._driver = None
            self._thread = None
            self._connected_port = None
            self._connected_identity = None
            self._retry_wait_s = None
            state_snapshot = self._set_state_locked(ArduinoConnectionState.STOPPED)
        self._emit_state_change(state_snapshot)

    def request_reconnect(self) -> None:
        with self._lock:
            if self._state == ArduinoConnectionState.STOPPED:
                return
            self._reconnect_requested.set()
            driver = self._driver
        if driver is not None:
            driver.close()

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return self._snapshot_locked()

    def _run(self) -> None:
        backoff = 0.5
        while not self._stop.is_set():
            try:
                port, identity = self._resolve_target()
                self._emit_state_change(self._transition_state(ArduinoConnectionState.CONNECTING))
                driver = self._driver_factory(
                    port=port,
                    baudrate=self._baudrate,
                    startup_delay=self._startup_delay,
                )
                driver.open()
                with self._lock:
                    self._driver = driver
                    self._connected_port = port
                    self._connected_identity = identity or self._port_identity(port)
                    if self._connected_identity is not None and self._connected_identity.exact:
                        self._known_identity = self._connected_identity
                    self._generation += 1
                    self._reconnects = max(0, self._generation - 1)
                    self._retry_wait_s = None
                    self._last_error = None
                    generation = self._generation
                    connected_identity = self._connected_identity
                    self._reconnect_requested.clear()
                    state_snapshot = self._set_state_locked(ArduinoConnectionState.CONNECTED)
                self._emit_state_change(state_snapshot)
                if self._on_session_started is not None:
                    self._on_session_started(generation, connected_identity)
                backoff = 0.5
                while not self._stop.is_set() and not self._reconnect_requested.is_set():
                    packet = driver.read_packet()
                    if self._on_packet is not None:
                        self._on_packet(packet, generation)
                if self._stop.is_set():
                    break
                raise RuntimeError("Reconexión serial solicitada.")
            except Exception as error:
                message = str(error)
                with self._lock:
                    driver = self._driver
                if driver is not None:
                    driver.close()
                state_snapshot = None
                with self._lock:
                    self._driver = None
                    self._connected_port = None
                    self._connected_identity = None
                    self._last_error = message
                    if not self._stop.is_set():
                        state_snapshot = self._set_state_locked(ArduinoConnectionState.DEGRADED)
                self._emit_state_change(state_snapshot)
                if self._on_session_lost is not None and not self._stop.is_set():
                    self._on_session_lost(message)
                if self._stop.is_set():
                    break
                self._retry(backoff)
                backoff = min(backoff * 2.0, 5.0)
        state_snapshot: dict[str, object] | None = None
        with self._lock:
            self._driver = None
            self._connected_port = None
            self._connected_identity = None
            self._retry_wait_s = None
            state_snapshot = self._set_state_locked(ArduinoConnectionState.STOPPED)
        self._emit_state_change(state_snapshot)

    def _retry(self, seconds: float) -> None:
        state_snapshot: dict[str, object] | None = None
        with self._lock:
            self._retry_wait_s = seconds
            state_snapshot = self._set_state_locked(ArduinoConnectionState.RETRY_WAIT)
        self._emit_state_change(state_snapshot)
        deadline = time.monotonic() + seconds
        while not self._stop.is_set() and time.monotonic() < deadline:
            if self._reconnect_requested.wait(timeout=0.1):
                break
        state_snapshot = None
        with self._lock:
            self._retry_wait_s = None
            if not self._stop.is_set():
                state_snapshot = self._set_state_locked(ArduinoConnectionState.DISCOVERING)
        self._emit_state_change(state_snapshot)

    def _resolve_target(self) -> tuple[str, UsbIdentity | None]:
        with self._lock:
            configured_port = self._configured_port
            known_identity = self._known_identity
        if known_identity is not None and known_identity.exact:
            ports = list(list_ports.comports())
            for info in ports:
                if (
                    info.vid == known_identity.vid
                    and info.pid == known_identity.pid
                    and getattr(info, "serial_number", None) == known_identity.serial_number
                ):
                    return info.device, UsbIdentity(
                        port=info.device,
                        vid=info.vid,
                        pid=info.pid,
                        serial_number=getattr(info, "serial_number", None),
                        product=getattr(info, "product", None),
                        manufacturer=getattr(info, "manufacturer", None),
                    )
            with self._lock:
                self._rejected_devices += sum(
                    1
                    for info in ports
                    if not (
                        info.vid == known_identity.vid
                        and info.pid == known_identity.pid
                        and getattr(info, "serial_number", None) == known_identity.serial_number
                    )
                )
            raise RuntimeError(
                "No se encontró el Arduino previamente conocido por VID/PID/número de serie."
            )
        if configured_port is None:
            raise RuntimeError("No hay SERIAL_PORT configurado para el Arduino.")
        if not os.path.exists(configured_port):
            raise RuntimeError(
                f"El puerto configurado {configured_port} no está disponible. No se seleccionará otro dispositivo automáticamente."
            )
        return configured_port, self._port_identity(configured_port)

    def _port_identity(self, port: str) -> UsbIdentity | None:
        for info in list_ports.comports():
            if info.device == port:
                return UsbIdentity(
                    port=info.device,
                    vid=info.vid,
                    pid=info.pid,
                    serial_number=getattr(info, "serial_number", None),
                    product=getattr(info, "product", None),
                    manufacturer=getattr(info, "manufacturer", None),
                )
        return None

    def _snapshot_locked(self) -> dict[str, object]:
        thread_alive = self._thread is not None and self._thread.is_alive()
        driver = self._driver
        snapshot = ArduinoConnectionSnapshot(
            state=self._state.value,
            generation=self._generation,
            configured_port=self._configured_port,
            connected_port=self._connected_port,
            usb_identity=None if self._connected_identity is None else self._connected_identity.snapshot(),
            known_identity=None if self._known_identity is None else self._known_identity.snapshot(),
            reconnects=self._reconnects,
            rejected_devices=self._rejected_devices,
            retry_wait_s=self._retry_wait_s,
            last_error=self._last_error,
            thread_alive=thread_alive,
            open=bool(driver is not None and driver.diagnostics.open),
        )
        return snapshot.__dict__

    def _set_state_locked(self, state: ArduinoConnectionState) -> dict[str, object] | None:
        self._state = state
        if self._on_state_change is None:
            return None
        return self._snapshot_locked()

    def _transition_state(self, state: ArduinoConnectionState) -> dict[str, object] | None:
        with self._lock:
            return self._set_state_locked(state)

    def _emit_state_change(self, snapshot: dict[str, object] | None) -> None:
        if snapshot is None or self._on_state_change is None:
            return
        self._on_state_change(dict(snapshot))
