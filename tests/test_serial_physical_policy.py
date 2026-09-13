"""Physical serial policy tested entirely through synthetic discovery and IO."""
from contextlib import contextmanager
from dataclasses import replace
import errno
import threading
import unittest
from unittest.mock import patch

from tests import test_connection_manager as fixtures
from tests import test_machine_runtime as runtime_fixtures
from tests import test_physical_integration as protocol_fixtures
from klipper_cnc_assistant.input.connection_manager import ArduinoConnectionManager, UsbIdentity
from klipper_cnc_assistant.input.serial_driver import SerialDriver, SerialExclusiveRequiredError


CONFIGURED = '/dev/serial/by-path/test-controller'
DEVICE = '/dev/ttyUSB-test'
DISCOVERY = 'klipper_cnc_assistant.input.connection_manager.'
SERIAL = 'klipper_cnc_assistant.input.serial_driver.'


class PolicyPort:
    def __init__(self, *, exclusive=True, read_error=None):
        self.exclusive = exclusive
        self.is_open = True
        self.close_calls = 0
        self.read_calls = 0
        self.read_error = read_error
        self.buffer = bytearray(protocol_fixtures.packet(0))
        self.cancelled = threading.Event()
        self.owner = threading.get_ident()

    def read(self, size):
        self.read_calls += 1
        if self.buffer:
            result = bytes(self.buffer[:size])
            del self.buffer[:size]
            return result
        if self.read_error:
            raise self.read_error
        self.cancelled.wait(0.01)
        return b''

    def cancel_read(self):
        self.cancelled.set()

    def close(self):
        assert threading.get_ident() == self.owner, 'Descriptor closed outside owner worker'
        self.close_calls += 1
        self.is_open = False


class PhysicalSerialPolicyTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.ports = []
        self.packets = []

    @staticmethod
    def identity(**changes):
        return replace(fixtures.FakePortInfo(device=DEVICE, vid=0x1234, pid=0x5678,
                                             location='2-1:1.0'), **changes)

    def manager(self, **changes):
        options = dict(configured_port=CONFIGURED, baudrate=115200, startup_delay=0,
                       manager_epoch=17, physical_mode=True,
                       on_packet=lambda epoch, packet, generation: self.packets.append((epoch, generation)))
        options.update(changes)
        return ArduinoConnectionManager(**options)

    def serial(self, **options):
        self.calls.append(options)
        port = PolicyPort(exclusive=options.get('exclusive', False))
        self.ports.append(port)
        return port

    @contextmanager
    def environment(self, identities, *, available=True, serial_factory=None):
        exists = available if callable(available) else lambda _path: available
        with patch(DISCOVERY + 'os.path.exists', side_effect=exists), patch(
            DISCOVERY + 'os.path.realpath', side_effect=lambda path: DEVICE if path == CONFIGURED else path
        ), patch(DISCOVERY + 'list_ports.comports', return_value=identities), patch(
            SERIAL + 'serial.Serial', side_effect=serial_factory or self.serial
        ):
            yield

    def run_until(self, manager, predicate):
        manager.start()
        self.addCleanup(manager.stop)
        fixtures.ConnectionManagerTest()._wait_for(predicate)
        return manager.snapshot()

    def rejected(self, manager):
        snapshot = self.run_until(manager, lambda: manager.snapshot()['state'] == 'RETRY_WAIT')
        manager.stop()
        self.assertEqual(snapshot['generation'], 0)
        self.assertFalse(snapshot['open'])
        self.assertFalse(snapshot['session_received_packet'])
        self.assertEqual(self.packets, [])
        return snapshot

    def test_A_physical_verified_by_path_and_exclusive_connects(self):
        manager = self.manager()
        with self.environment([self.identity()]):
            snapshot = self.run_until(manager, lambda: manager.snapshot()['generation'] == 1)
            manager.stop()
        self.assertEqual(snapshot['state'], 'CONNECTED')
        self.assertEqual(snapshot['usb_identity']['location'], '2-1:1.0')
        self.assertEqual(self.packets, [(17, 1)])
        self.assertTrue(self.calls[0]['exclusive'])
        self.assertEqual(self.calls[0]['port'], CONFIGURED)
        self.assertEqual(self.ports[0].close_calls, 1)

    def test_A_physical_configured_raw_port_with_exact_usb_identity_connects(self):
        manager = self.manager(configured_port=DEVICE)
        with self.environment([self.identity(serial_number='test-unique')]):
            self.run_until(manager, lambda: manager.snapshot()['generation'] == 1)
            manager.stop()
        self.assertTrue(self.calls[0]['exclusive'])

    def test_B_physical_unverifiable_initial_identity_never_opens(self):
        cases = ([], [self.identity(vid=None)], [self.identity(pid=None)],
                 [self.identity(vid=0)], [self.identity(location=None)],
                 [self.identity(location='', serial_number='0')])
        for metadata in cases:
            with self.subTest(metadata=metadata), self.environment(metadata):
                snapshot = self.rejected(self.manager())
                self.assertEqual(snapshot['last_session_error_class'], 'IDENTITY_UNVERIFIABLE')
        self.assertEqual(self.calls, [])

    def test_B_raw_tty_location_without_unique_series_is_not_sufficient(self):
        with self.environment([self.identity()]):
            self.rejected(self.manager(configured_port=DEVICE))
        self.assertEqual(self.calls, [])

    def test_C_physical_pinned_vid_pid_location_and_series_mismatches_never_open(self):
        known = UsbIdentity(port=DEVICE, vid=0x1234, pid=0x5678,
                            serial_number='test-unique', location='2-1:1.0')
        for change in ({'vid': 0x4321}, {'pid': 0x8765}, {'location': '2-2:1.0'}, {'serial_number': 'other'}):
            observed = {'serial_number': 'test-unique', **change}
            with self.subTest(change=change), self.environment([self.identity(**observed)]):
                snapshot = self.rejected(self.manager(known_identity=known))
                self.assertEqual(snapshot['last_session_error_class'], 'IDENTITY_MISMATCH')
        self.assertEqual(self.calls, [])

    def test_D_physical_unsupported_exclusive_keyword_has_no_shared_fallback(self):
        def unsupported(**options):
            self.calls.append(options)
            raise TypeError("unexpected keyword argument 'exclusive'")
        with self.environment([self.identity()], serial_factory=unsupported):
            snapshot = self.rejected(self.manager())
        self.assertEqual(snapshot['last_session_error_class'], 'EXCLUSIVE_REQUIRED')
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(self.calls[0]['exclusive'])

    def test_D_physical_unsupported_os_never_opens(self):
        with patch(SERIAL + 'os.name', 'nt'), patch(SERIAL + 'serial.Serial') as opening:
            with self.assertRaises(SerialExclusiveRequiredError):
                SerialDriver(port=DEVICE).open()
        opening.assert_not_called()

    def test_D_physical_exclusive_lock_feature_failure_has_no_fallback(self):
        def unsupported(**options):
            self.calls.append(options)
            raise OSError(errno.ENOTSUP, 'synthetic exclusive lock unsupported')
        with self.environment([self.identity()], serial_factory=unsupported):
            self.rejected(self.manager())
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(self.calls[0]['exclusive'])

    def test_D_custom_driver_without_exclusive_evidence_cannot_publish_session(self):
        factory = fixtures.ScriptedFactory([[fixtures.PACKET]])
        def unverified(**options):
            driver = factory(**options)
            driver.diagnostics.exclusive_supported = False
            return driver
        with self.environment([self.identity()]):
            self.rejected(self.manager(driver_factory=unverified))
        self.assertEqual(factory.instances[0].read_thread_ids, [])
        self.assertEqual(factory.instances[0].close_calls, 1)

    def test_D_physical_ignored_exclusive_feature_closes_once_without_read(self):
        def ignored(**options):
            port = self.serial(**options)
            port.exclusive = False
            return port
        with self.environment([self.identity()], serial_factory=ignored):
            self.rejected(self.manager())
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.ports[0].read_calls, 0)
        self.assertEqual(self.ports[0].close_calls, 1)

    def test_D_rejected_descriptor_cannot_be_reused_by_read_packet(self):
        port = PolicyPort(exclusive=False)
        driver = SerialDriver(port=DEVICE, startup_delay=0)
        with patch(SERIAL + 'serial.Serial', return_value=port):
            with self.assertRaises(SerialExclusiveRequiredError):
                driver.open()
            with self.assertRaises(SerialExclusiveRequiredError):
                driver.read_packet()
            driver.close()
        self.assertEqual(port.read_calls, 0)
        self.assertEqual(port.close_calls, 1)

    def test_E_explicit_nonphysical_manager_allows_safe_shared_test_fallback(self):
        def unsupported(**options):
            if 'exclusive' in options:
                self.calls.append(options)
                raise TypeError("unexpected keyword argument 'exclusive'")
            return self.serial(**options)
        with self.environment([], serial_factory=unsupported):
            manager = self.manager(physical_mode=False)
            self.run_until(manager, lambda: manager.snapshot()['generation'] == 1)
            manager.stop()
        self.assertEqual(len(self.calls), 2)
        self.assertNotIn('exclusive', self.calls[1])

    def test_E_simulated_runtime_connect_does_not_discover_or_open_devices(self):
        runtime = runtime_fixtures.MachineRuntime(runtime_fixtures.config())
        with patch(DISCOVERY + 'list_ports.comports') as discover, patch(SERIAL + 'serial.Serial') as opening:
            self.assertEqual(runtime.connect()['mode'], 'SIMULATED')
        discover.assert_not_called()
        opening.assert_not_called()

    def test_F_valid_hotplug_after_missing_device_recovers(self):
        available = threading.Event()
        with self.environment([self.identity()], available=lambda _path: available.is_set()):
            manager = self.manager()
            self.run_until(manager, lambda: manager.snapshot()['state'] == 'RETRY_WAIT')
            self.assertEqual(self.calls, [])
            available.set()
            fixtures.ConnectionManagerTest()._wait_for(lambda: manager.snapshot()['generation'] == 1)
            manager.stop()
        self.assertEqual(self.packets, [(17, 1)])
        self.assertEqual(len(self.calls), 1)

    def test_G_distractor_never_selected_when_configured_device_missing(self):
        for available in (False, True):
            with self.subTest(available=available), self.environment([self.identity(device='/dev/ttyUSB-distractor')], available=available):
                self.rejected(self.manager())
        self.assertEqual(self.calls, [])

    def test_H_physical_ebadf_reconnect_keeps_exclusive_owner_and_generation(self):
        def broken_once(**options):
            port = self.serial(**options)
            if len(self.calls) == 1:
                port.read_error = OSError(errno.EBADF, 'synthetic descriptor failure')
            return port
        with self.environment([self.identity()], serial_factory=broken_once):
            manager = self.manager()
            self.run_until(manager, lambda: manager.snapshot()['generation'] == 2)
            manager.stop()
        self.assertEqual(self.packets, [(17, 1), (17, 2)])
        self.assertTrue(all(call['exclusive'] for call in self.calls))
        self.assertTrue(all(port.close_calls == 1 for port in self.ports))

    def test_identity_change_during_open_does_not_publish_session(self):
        with self.environment([self.identity()]), patch(DISCOVERY + 'list_ports.comports', side_effect=[
            [self.identity()], [self.identity(vid=0x4321)]
        ]):
            self.rejected(self.manager())
        self.assertEqual(self.ports[0].close_calls, 1)
