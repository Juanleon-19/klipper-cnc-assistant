"""Deterministic serial/worker races; no real transports or sleeps."""
import threading
import unittest
from unittest.mock import patch

from tests import test_joystick_fresh_observation as fixtures
from klipper_cnc_assistant.input.command_mapper import ControllerCommand
from klipper_cnc_assistant.input.serial_driver import ControllerPacket
from klipper_cnc_assistant.machine.runtime import MachineRuntimeError
from klipper_cnc_assistant.jog.profiles import JogMode


class JoystickWorkerTest(unittest.TestCase):
    def setUp(self):
        helper = fixtures.JoystickFreshObservationTest()
        helper.setUp()
        self.runtime, self.client = helper.runtime, helper.client
        self.response = helper.response
        self.entered, self.release = threading.Event(), threading.Event()
        def reply(_objects):
            self.entered.set()
            if not self.release.wait(3):
                raise TimeoutError('test did not release HTTP')
            return self.response
        self.client.query_objects.side_effect = reply
        self.addCleanup(self.finish)

    def finish(self):
        self.release.set()
        worker = self.runtime._manual_thread
        if worker is not None:
            worker.join(3)
            self.assertFalse(worker.is_alive())

    def packet(self, direction='RIGHT', probe=False):
        self.runtime._handle_controller_packet(
            ControllerPacket(direction=direction, joystick_button=False,
                             external_button=False, probe=probe, x=900, y=512),
            ControllerCommand(jog_x=1 if direction == 'RIGHT' else 0, probe_triggered=probe))

    def start(self):
        self.packet()
        self.assertTrue(self.entered.wait(1))
        self.assertTrue(self.runtime._manual_thread.is_alive())

    def test_serial_keeps_reading_and_busy_intents_are_never_queued(self):
        self.start()
        before = self.runtime._packet_sequence
        for _ in range(100):
            self.packet('CENTER', probe=True)
            self.packet()
        self.assertEqual(self.runtime._packet_sequence, before + 200)
        self.packet('CENTER', probe=True)
        self.assertTrue(self.runtime.get_live_probe_state(require_fresh=True)['raw_value'])
        self.client.query_objects.assert_called_once()
        self.finish()
        self.assertEqual(len(self.client.scripts), 1)
        self.packet()  # a center read during the move cannot arm another move
        self.assertIsNone(self.runtime._manual_thread)
        self.assertEqual(len(self.client.scripts), 1)
        self.packet('CENTER')
        self.packet()
        self.finish()
        self.assertEqual(len(self.client.scripts), 2)

    def test_cancellation_during_http_wait_prevents_emission(self):
        self.start()
        self.runtime.cancel_operation()
        self.finish()
        self.assertEqual(self.client.scripts, [])
        self.assertFalse(self.runtime._movement_lock.locked())

    def test_disconnect_cannot_remove_transport_from_active_worker(self):
        self.start()
        with self.assertRaises(MachineRuntimeError):
            self.runtime.disconnect()
        self.assertIs(self.runtime._client, self.client)

    def test_fresh_serial_packets_cannot_rejuvenate_old_intent(self):
        self.start()
        self.runtime._active_operation.started_at -= 10
        self.packet('CENTER')
        self.finish()
        self.assertEqual(self.client.scripts, [])
        self.assertIn('toque del joystick caducó', self.runtime._last_error)

    def test_reconnect_while_waiting_prevents_emission(self):
        self.start()
        self.runtime._serial_generation += 1
        self.finish()
        self.assertEqual(self.client.scripts, [])

    def test_mode_change_does_not_enlarge_an_already_accepted_step(self):
        self.start()
        self.runtime._manual.set_mode(JogMode.COARSE)
        self.finish()
        self.assertEqual(self.runtime._last_movement['effective_distance'], 1)
        self.assertEqual(self.runtime._last_movement['speed'], 10)

    def test_worker_start_failure_releases_lease(self):
        with patch.object(threading.Thread, 'start', side_effect=RuntimeError('no thread')):
            with self.assertRaisesRegex(RuntimeError, 'no thread'):
                self.packet()
        self.assertFalse(self.runtime._movement_lock.locked())
        self.assertIsNone(self.runtime._active_operation)
        self.assertIsNone(self.runtime._manual_thread)
        self.assertEqual(self.runtime.physical_ownership.snapshot()['kind'], 'IDLE')
