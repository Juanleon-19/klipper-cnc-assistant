from __future__ import annotations

import errno
import logging
import os
import re
import threading
from dataclasses import dataclass
from enum import StrEnum
from typing import Callable

from serial.tools import list_ports

from .serial_driver import (
    ControllerPacket,
    SerialDriver,
    SerialProtocolStaleError,
    SerialReadCancelled,
    SerialExclusiveRequiredError,
)


logger = logging.getLogger(__name__)


class ArduinoConnectionState(StrEnum):
    DISCONNECTED = "DISCONNECTED"
    DISCOVERING = "DISCOVERING"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    DEGRADED = "DEGRADED"
    RETRY_WAIT = "RETRY_WAIT"
    STOPPED = "STOPPED"


class ArduinoConnectionErrorCode(StrEnum):
    PORT_ABSENT = "PORT_ABSENT"
    OPEN_BUSY = "OPEN_BUSY"
    OPEN_FAILED = "OPEN_FAILED"
    READ_HANGUP_OR_CONCURRENT_ACCESS = "READ_HANGUP_OR_CONCURRENT_ACCESS"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    IDENTITY_UNVERIFIABLE = "IDENTITY_UNVERIFIABLE"
    EXCLUSIVE_REQUIRED = "EXCLUSIVE_REQUIRED"
    PROTOCOL_STALE = "PROTOCOL_STALE"
    STOP_REQUESTED = "STOP_REQUESTED"
    RECONNECT_REQUESTED = "RECONNECT_REQUESTED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ArduinoConnectionStopError(RuntimeError):
    """The owner thread did not terminate, so its manager cannot be replaced."""


class _ConnectionAttemptError(RuntimeError):
    def __init__(self, code: ArduinoConnectionErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class UsbIdentity:
    port: str
    vid: int | None = None
    pid: int | None = None
    serial_number: str | None = None
    product: str | None = None
    manufacturer: str | None = None
    location: str | None = None

    @property
    def exact(self) -> bool:
        return self.vid is not None and self.pid is not None and _unique_serial(self.serial_number)

    def snapshot(self) -> dict[str, object]:
        return {
            "port": self.port,
            "vid": self.vid,
            "pid": self.pid,
            "serial_number": self.serial_number,
            "product": self.product,
            "manufacturer": self.manufacturer,
            "location": self.location,
            "exact_identity": self.exact,
        }


@dataclass(frozen=True)
class SerialSessionFailure:
    code: str
    message: str
    exception_class: str
    errno: int | None
    phase: str
    manager_epoch: int
    generation: int
    configured_port: str | None
    resolved_port: str | None
    thread_id: int
    session_received_packet: bool

    def snapshot(self) -> dict[str, object]:
        return self.__dict__.copy()


@dataclass
class ArduinoConnectionSnapshot:
    state: str
    manager_epoch: int
    generation: int
    session_generation: int
    configured_port: str | None
    resolved_port: str | None
    connected_port: str | None
    usb_identity: dict[str, object] | None
    known_identity: dict[str, object] | None
    reconnects: int
    rejected_devices: int
    retry_wait_s: float | None
    last_error: str | None
    last_session_error: str | None
    last_session_error_class: str | None
    last_session_error_phase: str | None
    last_session_exception_class: str | None
    last_session_errno: int | None
    last_session_failure: dict[str, object] | None
    session_received_packet: bool
    thread_alive: bool
    thread_id: int | None
    open: bool


class ArduinoConnectionManager:
    """Own one serial driver and its descriptor from one worker thread.

    External threads may only request cancellation. The owner worker publishes the
    driver before ``open()``, performs all reads, and closes it exactly once in the
    attempt's ``finally`` block. A manager whose worker cannot stop remains the
    effective owner and must not be replaced by MachineRuntime.
    """

    def __init__(
        self,
        *,
        configured_port: str | None,
        baudrate: int,
        startup_delay: float,
        manager_epoch: int = 0,
        known_identity: UsbIdentity | None = None,
        physical_mode: bool = True,
        driver_factory: Callable[..., SerialDriver] = SerialDriver,
        on_packet: Callable[[int, ControllerPacket, int], None] | None = None,
        on_session_started: Callable[[int, int, UsbIdentity | None], None] | None = None,
        on_session_lost: Callable[[int, int, str, dict[str, object]], None] | None = None,
        on_state_change: Callable[[int, dict[str, object]], None] | None = None,
        stop_timeout: float = 3.0,
    ) -> None:
        self._configured_port = configured_port
        self._physical_mode = physical_mode
        self._baudrate = baudrate
        self._startup_delay = startup_delay
        self._manager_epoch = manager_epoch
        self._driver_factory = driver_factory
        self._on_packet = on_packet
        self._on_session_started = on_session_started
        self._on_session_lost = on_session_lost
        self._on_state_change = on_state_change
        self._stop_timeout = stop_timeout
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._driver: SerialDriver | None = None
        self._state = ArduinoConnectionState.DISCONNECTED
        self._generation = 0
        self._reconnects = 0
        self._reconnect_request_id = 0
        self._rejected_devices = 0
        self._retry_wait_s: float | None = None
        self._last_failure: SerialSessionFailure | None = None
        self._last_logged_failure_signature: tuple[object, ...] | None = None
        self._resolved_port: str | None = None
        self._connected_port: str | None = None
        self._connected_identity: UsbIdentity | None = None
        self._known_identity = known_identity
        self._session_received_packet = False
        self._owner_thread_id: int | None = None

    @property
    def manager_epoch(self) -> int:
        return self._manager_epoch

    @property
    def thread(self) -> threading.Thread | None:
        with self._lock:
            return self._thread

    @property
    def driver(self) -> SerialDriver | None:
        with self._lock:
            return self._driver

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            if self._state == ArduinoConnectionState.STOPPED:
                raise RuntimeError("Un ArduinoConnectionManager detenido no puede reiniciarse.")
            self._stop.clear()
            self._wake.clear()
            state_snapshot = self._set_state_locked(ArduinoConnectionState.DISCOVERING)
            thread = threading.Thread(
                target=self._run,
                name=f"arduino-connection-{self._manager_epoch}",
                daemon=True,
            )
            self._thread = thread
            thread.start()
        self._emit_state_change(state_snapshot)

    def stop(self) -> None:
        with self._lock:
            thread = self._thread
            if thread is None or not thread.is_alive():
                self._thread = None
                self._driver = None
                self._connected_port = None
                self._connected_identity = None
                self._retry_wait_s = None
                state_snapshot = self._set_state_locked(ArduinoConnectionState.STOPPED)
                driver = None
            else:
                self._stop.set()
                self._wake.set()
                driver = self._driver
                state_snapshot = None
        if driver is not None:
            self._cancel_driver_read(driver)
        if thread is not None and thread.is_alive():
            thread.join(timeout=self._stop_timeout)
        if thread is not None and thread.is_alive():
            self._record_failure(
                ArduinoConnectionErrorCode.STOP_REQUESTED,
                ArduinoConnectionStopError("El hilo serial no terminó dentro del timeout de stop."),
                phase="stop",
                session_received_packet=self._session_received_packet,
            )
            self._emit_state_change(self._snapshot_for_callback())
            raise ArduinoConnectionStopError(
                "El manager Arduino conserva ownership porque su hilo serial sigue vivo."
            ) from None
        with self._lock:
            self._thread = None
            if self._state != ArduinoConnectionState.STOPPED:
                state_snapshot = self._set_state_locked(ArduinoConnectionState.STOPPED)
        self._emit_state_change(state_snapshot)

    def request_reconnect(self) -> bool:
        with self._lock:
            if self._state == ArduinoConnectionState.STOPPED:
                return False
            self._reconnect_request_id += 1
            self._wake.set()
            driver = self._driver
        if driver is not None:
            self._cancel_driver_read(driver)
        return True

    @staticmethod
    def _cancel_driver_read(driver: SerialDriver) -> bool:
        cancel = getattr(driver, "cancel_read", None)
        if cancel is None:
            return False
        try:
            return bool(cancel())
        except Exception:
            return False

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            return self._snapshot_locked()

    def _run(self) -> None:
        backoff = 0.5
        with self._lock:
            self._owner_thread_id = threading.get_ident()
        try:
            while not self._stop.is_set():
                attempt_request_id = self._begin_attempt()
                driver: SerialDriver | None = None
                phase = "discovery"
                session_confirmed = False
                generation = self._generation
                identity: UsbIdentity | None = None
                try:
                    port, resolved_port, identity = self._resolve_target()
                    self._raise_if_cancelled(attempt_request_id)
                    self._emit_state_change(self._transition_state(ArduinoConnectionState.CONNECTING))
                    phase = "open"
                    driver = self._driver_factory(
                        port=port,
                        baudrate=self._baudrate,
                        startup_delay=self._startup_delay,
                        require_exclusive=self._physical_mode,
                    )
                    with self._lock:
                        if self._driver is not None:
                            raise RuntimeError("Ya existe un driver serial publicado en este manager.")
                        self._driver = driver
                        self._resolved_port = resolved_port
                    self._raise_if_cancelled(attempt_request_id)
                    driver.open()
                    self._raise_if_cancelled(attempt_request_id)
                    if self._physical_mode and (
                        getattr(driver.diagnostics, "exclusive_requested", None) is not True
                        or getattr(driver.diagnostics, "exclusive_supported", None) is not True
                    ):
                        raise SerialExclusiveRequiredError("El driver no confirmó apertura serial exclusiva.")

                    phase = "first_packet"
                    packet = driver.read_packet()
                    self._raise_if_cancelled(attempt_request_id)
                    if self._physical_mode:
                        _, current_port, current_identity = self._resolve_target()
                        if current_port != resolved_port or self._identity_mismatch(identity, current_identity):
                            raise _ConnectionAttemptError(
                                ArduinoConnectionErrorCode.IDENTITY_MISMATCH,
                                "La identidad o el destino serial cambió durante la apertura; conexión rechazada.",
                            )
                    with self._lock:
                        self._raise_if_cancelled_locked(attempt_request_id)
                        self._validate_or_record_identity_locked(identity)
                        self._generation += 1
                        generation = self._generation
                        self._reconnects = max(0, generation - 1)
                        self._connected_port = resolved_port
                        self._connected_identity = identity
                        self._retry_wait_s = None
                        self._last_failure = None
                        self._session_received_packet = True
                        session_confirmed = True
                        state_snapshot = self._set_state_locked(ArduinoConnectionState.CONNECTED)
                    backoff = 0.5
                    self._emit_state_change(state_snapshot)
                    if self._on_session_started is not None:
                        self._on_session_started(self._manager_epoch, generation, identity)
                    if self._on_packet is not None:
                        self._on_packet(self._manager_epoch, packet, generation)

                    phase = "read"
                    while True:
                        self._raise_if_cancelled(attempt_request_id)
                        packet = driver.read_packet()
                        self._raise_if_cancelled(attempt_request_id)
                        if self._on_packet is not None:
                            self._on_packet(self._manager_epoch, packet, generation)
                except Exception as error:
                    cancellation = self._cancellation_code(attempt_request_id)
                    code = cancellation or self._classify_error(error, phase)
                    failure = self._record_failure(
                        code,
                        error,
                        phase=phase,
                        generation=generation,
                        session_received_packet=session_confirmed,
                    )
                finally:
                    if driver is not None:
                        try:
                            driver.close()
                        except Exception as close_error:
                            logger.warning(
                                "ARDUINO_SERIAL_CLOSE_FAILED manager_epoch=%s generation=%s error_class=%s error=%s",
                                self._manager_epoch,
                                generation,
                                type(close_error).__name__,
                                str(close_error),
                            )
                    with self._lock:
                        if self._driver is driver:
                            self._driver = None

                if failure.code == ArduinoConnectionErrorCode.STOP_REQUESTED.value:
                    break

                with self._lock:
                    self._connected_port = None
                    self._connected_identity = None
                    self._session_received_packet = False
                if session_confirmed and self._on_session_lost is not None:
                    self._on_session_lost(
                        self._manager_epoch,
                        generation,
                        failure.message,
                        failure.snapshot(),
                    )

                if failure.code == ArduinoConnectionErrorCode.RECONNECT_REQUESTED.value:
                    self._emit_state_change(self._transition_state(ArduinoConnectionState.DISCOVERING))
                    continue

                self._emit_state_change(self._transition_state(ArduinoConnectionState.DEGRADED))
                self._retry(backoff, attempt_request_id)
                backoff = min(backoff * 2.0, 5.0)
        finally:
            with self._lock:
                self._driver = None
                self._connected_port = None
                self._connected_identity = None
                self._session_received_packet = False
                self._retry_wait_s = None
                state_snapshot = self._set_state_locked(ArduinoConnectionState.STOPPED)
            self._emit_state_change(state_snapshot)

    def _begin_attempt(self) -> int:
        with self._lock:
            attempt_request_id = self._reconnect_request_id
            self._wake.clear()
            self._retry_wait_s = None
            state_snapshot = self._set_state_locked(ArduinoConnectionState.DISCOVERING)
        self._emit_state_change(state_snapshot)
        return attempt_request_id

    def _retry(self, seconds: float, attempt_request_id: int) -> None:
        with self._lock:
            if self._stop.is_set() or self._reconnect_request_id != attempt_request_id:
                wait = False
                state_snapshot = None
            else:
                self._retry_wait_s = seconds
                state_snapshot = self._set_state_locked(ArduinoConnectionState.RETRY_WAIT)
                wait = True
        self._emit_state_change(state_snapshot)
        if wait:
            self._wake.wait(timeout=seconds)
        with self._lock:
            self._retry_wait_s = None
            if self._stop.is_set():
                state_snapshot = None
            else:
                state_snapshot = self._set_state_locked(ArduinoConnectionState.DISCOVERING)
        self._emit_state_change(state_snapshot)

    def _resolve_target(self) -> tuple[str, str, UsbIdentity | None]:
        with self._lock:
            configured_port = self._configured_port
            known_identity = self._known_identity
        if configured_port is None:
            with self._lock:
                self._resolved_port = None
            raise _ConnectionAttemptError(
                ArduinoConnectionErrorCode.PORT_ABSENT,
                "No hay SERIAL_PORT configurado para el Arduino.",
            )
        if not os.path.exists(configured_port):
            with self._lock:
                self._resolved_port = None
            raise _ConnectionAttemptError(
                ArduinoConnectionErrorCode.PORT_ABSENT,
                f"El puerto configurado {configured_port} no está disponible. "
                "No se seleccionará otro dispositivo automáticamente.",
            )
        resolved_port = os.path.realpath(configured_port)
        with self._lock:
            self._resolved_port = resolved_port
        identity = self._port_identity(configured_port)
        if self._physical_mode and not self._identity_verifiable(identity, configured_port, resolved_port):
            with self._lock:
                self._rejected_devices += 1
            raise _ConnectionAttemptError(
                ArduinoConnectionErrorCode.IDENTITY_UNVERIFIABLE,
                "No se puede verificar VID/PID y serie USB, o VID/PID/location del by-path configurado.",
            )
        if known_identity is not None:
            mismatch = self._identity_mismatch(known_identity, identity)
            if mismatch is not None:
                with self._lock:
                    self._rejected_devices += 1
                raise _ConnectionAttemptError(
                    ArduinoConnectionErrorCode.IDENTITY_MISMATCH,
                    mismatch,
                )
        return configured_port, resolved_port, identity

    @staticmethod
    def _identity_verifiable(identity, configured_port, resolved_port) -> bool:
        if identity is None or not (
            type(identity.vid) is int and 0 < identity.vid <= 0xFFFF
            and type(identity.pid) is int and 0 <= identity.pid <= 0xFFFF
        ):
            return False
        return identity.exact or (
            os.path.dirname(configured_port) == "/dev/serial/by-path"
            and configured_port != resolved_port and _unique_serial(identity.location)
        )

    def _port_identity(self, port: str) -> UsbIdentity | None:
        canonical_port = os.path.realpath(port)
        for info in list_ports.comports():
            if os.path.realpath(info.device) == canonical_port:
                return UsbIdentity(
                    port=canonical_port,
                    vid=info.vid,
                    pid=info.pid,
                    serial_number=getattr(info, "serial_number", None),
                    product=getattr(info, "product", None),
                    manufacturer=getattr(info, "manufacturer", None),
                    location=getattr(info, "location", None),
                )
        return None

    @staticmethod
    def _identity_mismatch(expected: UsbIdentity, observed: UsbIdentity | None) -> str | None:
        if observed is None:
            return "No fue posible validar la identidad del dispositivo en el path configurado."
        if expected.vid is not None and observed.vid != expected.vid:
            return "El VID del dispositivo no coincide con la identidad observada previamente."
        if expected.pid is not None and observed.pid != expected.pid:
            return "El PID del dispositivo no coincide con la identidad observada previamente."
        if _unique_serial(expected.serial_number) and observed.serial_number != expected.serial_number:
            return "El número de serie USB no coincide con la identidad observada previamente."
        if expected.location and observed.location != expected.location:
            return "La ubicación USB no coincide con el path observado previamente."
        return None

    def _validate_or_record_identity_locked(self, identity: UsbIdentity | None) -> None:
        if self._known_identity is None:
            if identity is not None:
                self._known_identity = identity
            return
        mismatch = self._identity_mismatch(self._known_identity, identity)
        if mismatch is not None:
            self._rejected_devices += 1
            raise _ConnectionAttemptError(ArduinoConnectionErrorCode.IDENTITY_MISMATCH, mismatch)

    def _raise_if_cancelled(self, attempt_request_id: int) -> None:
        with self._lock:
            self._raise_if_cancelled_locked(attempt_request_id)

    def _raise_if_cancelled_locked(self, attempt_request_id: int) -> None:
        if self._stop.is_set():
            raise _ConnectionAttemptError(
                ArduinoConnectionErrorCode.STOP_REQUESTED,
                "Detención serial solicitada.",
            )
        if self._reconnect_request_id != attempt_request_id:
            raise _ConnectionAttemptError(
                ArduinoConnectionErrorCode.RECONNECT_REQUESTED,
                "Reconexión serial solicitada.",
            )

    def _cancellation_code(self, attempt_request_id: int) -> ArduinoConnectionErrorCode | None:
        with self._lock:
            if self._stop.is_set():
                return ArduinoConnectionErrorCode.STOP_REQUESTED
            if self._reconnect_request_id != attempt_request_id:
                return ArduinoConnectionErrorCode.RECONNECT_REQUESTED
        return None

    @staticmethod
    def _classify_error(error: Exception, phase: str) -> ArduinoConnectionErrorCode:
        if isinstance(error, _ConnectionAttemptError):
            return error.code
        if isinstance(error, SerialExclusiveRequiredError):
            return ArduinoConnectionErrorCode.EXCLUSIVE_REQUIRED
        if isinstance(error, SerialReadCancelled):
            return ArduinoConnectionErrorCode.READ_HANGUP_OR_CONCURRENT_ACCESS
        if isinstance(error, SerialProtocolStaleError):
            return ArduinoConnectionErrorCode.PROTOCOL_STALE
        error_errno = _exception_errno(error)
        message = str(error).lower()
        if error_errno == errno.ENOENT:
            return ArduinoConnectionErrorCode.PORT_ABSENT
        if phase == "open":
            if error_errno in {errno.EACCES, errno.EAGAIN, errno.EBUSY} or any(
                marker in message
                for marker in ("resource busy", "permission denied", "exclusive", "could not exclusively lock")
            ):
                return ArduinoConnectionErrorCode.OPEN_BUSY
            return ArduinoConnectionErrorCode.OPEN_FAILED
        if "device reports readiness to read but returned no data" in message:
            return ArduinoConnectionErrorCode.READ_HANGUP_OR_CONCURRENT_ACCESS
        if phase in {"first_packet", "read"}:
            return ArduinoConnectionErrorCode.READ_HANGUP_OR_CONCURRENT_ACCESS
        return ArduinoConnectionErrorCode.INTERNAL_ERROR

    def _record_failure(
        self,
        code: ArduinoConnectionErrorCode,
        error: Exception,
        *,
        phase: str,
        generation: int | None = None,
        session_received_packet: bool,
    ) -> SerialSessionFailure:
        with self._lock:
            failure = SerialSessionFailure(
                code=code.value,
                message=_short_error(error),
                exception_class=type(error).__name__,
                errno=_exception_errno(error),
                phase=phase,
                manager_epoch=self._manager_epoch,
                generation=self._generation if generation is None else generation,
                configured_port=self._configured_port,
                resolved_port=self._resolved_port,
                thread_id=threading.get_ident(),
                session_received_packet=session_received_packet,
            )
            self._last_failure = failure
            signature = (
                failure.code,
                failure.phase,
                failure.message,
                failure.resolved_port,
                failure.generation if failure.session_received_packet else None,
            )
            should_log = signature != self._last_logged_failure_signature
            if should_log:
                self._last_logged_failure_signature = signature
        if not should_log:
            return failure
        log = (
            logger.info
            if code in {
                ArduinoConnectionErrorCode.STOP_REQUESTED,
                ArduinoConnectionErrorCode.RECONNECT_REQUESTED,
            }
            else logger.warning
        )
        log(
            "ARDUINO_SERIAL_SESSION_LOST code=%s phase=%s manager_epoch=%s generation=%s "
            "configured_port=%s resolved_port=%s thread_id=%s session_received_packet=%s "
            "exception_class=%s errno=%s error=%s",
            failure.code,
            failure.phase,
            failure.manager_epoch,
            failure.generation,
            failure.configured_port,
            failure.resolved_port,
            failure.thread_id,
            failure.session_received_packet,
            failure.exception_class,
            failure.errno,
            failure.message,
        )
        return failure

    def _snapshot_locked(self) -> dict[str, object]:
        thread_alive = self._thread is not None and self._thread.is_alive()
        driver = self._driver
        failure = self._last_failure
        snapshot = ArduinoConnectionSnapshot(
            state=self._state.value,
            manager_epoch=self._manager_epoch,
            generation=self._generation,
            session_generation=self._generation,
            configured_port=self._configured_port,
            resolved_port=self._resolved_port,
            connected_port=self._connected_port,
            usb_identity=None if self._connected_identity is None else self._connected_identity.snapshot(),
            known_identity=None if self._known_identity is None else self._known_identity.snapshot(),
            reconnects=self._reconnects,
            rejected_devices=self._rejected_devices,
            retry_wait_s=self._retry_wait_s,
            last_error=None if failure is None else failure.message,
            last_session_error=None if failure is None else failure.message,
            last_session_error_class=None if failure is None else failure.code,
            last_session_error_phase=None if failure is None else failure.phase,
            last_session_exception_class=None if failure is None else failure.exception_class,
            last_session_errno=None if failure is None else failure.errno,
            last_session_failure=None if failure is None else failure.snapshot(),
            session_received_packet=self._session_received_packet,
            thread_alive=thread_alive,
            thread_id=self._owner_thread_id,
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

    def _snapshot_for_callback(self) -> dict[str, object] | None:
        if self._on_state_change is None:
            return None
        with self._lock:
            return self._snapshot_locked()

    def _emit_state_change(self, snapshot: dict[str, object] | None) -> None:
        if snapshot is None or self._on_state_change is None:
            return
        self._on_state_change(self._manager_epoch, dict(snapshot))


def _unique_serial(value: str | None) -> bool:
    normalized = "" if value is None else value.strip().lower()
    return normalized not in {"", "0", "0000", "none", "unknown"}


def _exception_errno(error: BaseException) -> int | None:
    value = getattr(error, "errno", None)
    if isinstance(value, int):
        return value
    args = getattr(error, "args", ())
    if len(args) >= 2 and isinstance(args[0], int):
        return args[0]
    for arg in args:
        if isinstance(arg, OSError) and isinstance(arg.errno, int):
            return arg.errno
    match = re.search(r"\[Errno\s+(\d+)\]", str(error), flags=re.IGNORECASE)
    if match is not None:
        return int(match.group(1))
    return None


def _short_error(error: BaseException) -> str:
    message = str(error).strip() or type(error).__name__
    return message[:500]
