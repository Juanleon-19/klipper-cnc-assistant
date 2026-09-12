from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

from klipper_cnc_assistant.input.serial_driver import SerialDriver, SerialReadCancelled


class FakeSerialPort:
    def __init__(self) -> None:
        self.is_open = True
        self.close_calls = 0
        self.cancel_calls = 0

    def read(self, _size: int) -> bytes:
        return b""

    def reset_input_buffer(self) -> None:
        pass

    def cancel_read(self) -> None:
        self.cancel_calls += 1

    def close(self) -> None:
        self.close_calls += 1
        self.is_open = False


class SerialDriverLifecycleTest(unittest.TestCase):
    def test_posix_open_requests_exclusive_access(self) -> None:
        opened = FakeSerialPort()
        calls: list[dict[str, object]] = []

        def serial_factory(**kwargs):
            calls.append(kwargs)
            return opened

        with patch("klipper_cnc_assistant.input.serial_driver.serial.Serial", side_effect=serial_factory):
            driver = SerialDriver(port="/dev/ttyUSB1", startup_delay=0)
            driver.open()
            driver.close()

        self.assertTrue(calls[0]["exclusive"])
        self.assertTrue(driver.diagnostics.exclusive_requested)
        self.assertTrue(driver.diagnostics.exclusive_supported)
        self.assertEqual(opened.close_calls, 1)

    def test_open_falls_back_when_exclusive_keyword_is_unsupported(self) -> None:
        opened = FakeSerialPort()
        calls: list[dict[str, object]] = []

        def serial_factory(**kwargs):
            calls.append(kwargs)
            if "exclusive" in kwargs:
                raise TypeError("unexpected keyword argument 'exclusive'")
            return opened

        with patch("klipper_cnc_assistant.input.serial_driver.serial.Serial", side_effect=serial_factory):
            driver = SerialDriver(port="/dev/ttyUSB1", startup_delay=0)
            driver.open()
            driver.close()

        self.assertEqual(len(calls), 2)
        self.assertNotIn("exclusive", calls[1])
        self.assertFalse(driver.diagnostics.exclusive_supported)

    def test_cancel_read_interrupts_startup_delay_without_closing_from_caller(self) -> None:
        opened = FakeSerialPort()
        driver = SerialDriver(port="/dev/ttyUSB1", startup_delay=30)
        errors: list[BaseException] = []

        with patch("klipper_cnc_assistant.input.serial_driver.serial.Serial", return_value=opened):
            thread = threading.Thread(target=lambda: self._capture_open(driver, errors))
            started_at = time.monotonic()
            thread.start()
            while not driver.diagnostics.open and time.monotonic() - started_at < 1:
                threading.Event().wait(0.005)
            self.assertTrue(driver.cancel_read())
            thread.join(timeout=0.5)

        self.assertFalse(thread.is_alive())
        self.assertIsInstance(errors[0], SerialReadCancelled)
        self.assertEqual(opened.close_calls, 0)
        driver.close()
        self.assertEqual(opened.close_calls, 1)

    @staticmethod
    def _capture_open(driver: SerialDriver, errors: list[BaseException]) -> None:
        try:
            driver.open()
        except BaseException as error:
            errors.append(error)


if __name__ == "__main__":
    unittest.main()
