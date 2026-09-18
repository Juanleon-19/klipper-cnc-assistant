import unittest
from unittest.mock import patch

from tests import test_joystick_fresh_observation as joystick
from tests.test_machine_runtime import config, MotionClient
from tests.test_motion_authorization import observed_machine
from tests.physical_fakes import FakePhysicalAccess
from klipper_cnc_assistant.machine.config import MachineMode
from klipper_cnc_assistant.machine.physical_ownership import PhysicalMachineCoordinator, OwnerKind
from klipper_cnc_assistant.machine.recoverable_runtime import RecoverableMachineRuntime
from klipper_cnc_assistant.machine.runtime import MachineRuntimeError, MachineRuntimeState


class ResponsivenessTest(unittest.TestCase):
    def test_fresh_tap_needs_no_http_or_hold(self):
        fixture = joystick.JoystickFreshObservationTest()
        fixture.setUp()
        fixture.machine.update_klippy('ready')
        fixture.machine.update_motion((10, 10, 10), 0)
        fixture.packet()
        fixture.client.get_server_info.assert_not_called()
        fixture.client.query_objects.assert_not_called()
        self.assertEqual(len(fixture.client.scripts), 1)
        self.assertEqual(fixture.runtime._last_movement['target'], 11)

    def test_first_missing_completion_queries_without_fixed_sleep(self):
        fixture = joystick.JoystickFreshObservationTest()
        fixture.setUp()
        fixture.machine.update_motion((10, 10, 10), 0)
        def observe():
            fixture.machine.update_motion((10, 10, 9.95), 0)
        with patch.object(fixture.runtime, '_refresh_machine_best_effort', side_effect=observe) as refresh:
            with patch('klipper_cnc_assistant.machine.runtime.time.sleep', side_effect=AssertionError('unnecessary sleep')):
                fixture.runtime._wait_for_axis('z', 9.95, 'paso de sonda', start_position=10)
        refresh.assert_called_once()

    def test_already_confirmed_position_requires_no_poll(self):
        fixture = joystick.JoystickFreshObservationTest()
        fixture.setUp()
        fixture.machine.update_motion((10, 10, 9.95), 0)
        with patch.object(fixture.runtime, '_refresh_machine_best_effort') as refresh:
            fixture.runtime._wait_for_axis('z', 9.95, 'paso de sonda', start_position=10)
        refresh.assert_not_called()

    def test_next_probe_step_refreshes_expired_ready_state(self):
        fixture = joystick.JoystickFreshObservationTest()
        fixture.setUp()
        context = fixture.runtime._begin_motion_operation('test')
        try:
            frame = fixture.runtime._fresh_motion_context('probe_step')
            self.assertEqual(frame.frame.position, (40, 10, 10))
            fixture.client.query_objects.assert_called_once()
        finally:
            fixture.runtime._finish_operation_context(context)
            fixture.runtime._movement_lock.release()


class IdleRecoveryTest(unittest.TestCase):
    def setUp(self):
        owner = PhysicalMachineCoordinator()
        self.runtime = RecoverableMachineRuntime(config(MachineMode.PHYSICAL),
            process_lock=FakePhysicalAccess(), coordinator=owner)
        self.runtime._machine = observed_machine(owner)
        self.client = MotionClient(self.runtime._machine)
        self.runtime._client = self.client
        self.runtime._state = MachineRuntimeState.CANCELLED
        self.runtime._last_error = 'Malla cancelada por el operador.'
        self.permit = owner.acquire(OwnerKind.MESH, 'mesh-test')

    def test_retired_worker_and_observed_idle_recover_without_gcode(self):
        self.runtime.physical_ownership.retire(self.permit)
        result = self.runtime.recover_idle_controls()
        self.assertEqual(result['state'], 'DIAGNOSTIC')
        self.assertFalse(result['recovery_pending'])
        self.assertFalse(result['controller']['manual_enabled'])
        self.assertEqual(self.client.scripts, [])
        self.assertEqual(self.runtime.physical_ownership.snapshot()['kind'], 'IDLE')

    def test_active_producer_prevents_recovery_without_query(self):
        with patch.object(self.client, 'query_objects') as query:
            with self.assertRaises(MachineRuntimeError):
                self.runtime.recover_idle_controls()
            query.assert_not_called()

    def test_live_child_prevents_recovery(self):
        self.runtime.physical_ownership.delegate(self.permit, 'point')
        self.runtime.physical_ownership.retire(self.permit)
        with self.assertRaises(MachineRuntimeError):
            self.runtime.recover_idle_controls()

    def test_moving_machine_cannot_recover(self):
        self.runtime.physical_ownership.retire(self.permit)
        self.runtime._machine.update_motion(live_velocity=1)
        with self.assertRaises(MachineRuntimeError):
            self.runtime.recover_idle_controls()
        self.assertTrue(self.runtime.physical_ownership.snapshot()['recovery_pending'])
        self.assertEqual(self.client.scripts, [])

    def test_failed_observation_cannot_recover(self):
        self.runtime.physical_ownership.retire(self.permit)
        with patch.object(self.client, 'query_objects', side_effect=TimeoutError('offline')):
            with self.assertRaises(MachineRuntimeError):
                self.runtime.recover_idle_controls()
        self.assertEqual(self.runtime._state, MachineRuntimeState.CANCELLED)

    def test_api_publishes_recovery_and_can_clear_only_observed_idle(self):
        from tests import test_api
        fixture = test_api.ApiTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.app.state.machine_runtime = self.runtime
        self.runtime.physical_ownership.retire(self.permit)
        before = fixture.client.get('/api/machine/status')
        self.assertTrue(before.json()['recovery_pending'])
        result = fixture.client.post('/api/machine/recover-idle-controls')
        self.assertEqual(result.status_code, 200, result.text)
        self.assertFalse(result.json()['recovery_pending'])
        self.assertEqual(result.json()['state'], 'DIAGNOSTIC')
        self.assertEqual(self.client.scripts, [])
