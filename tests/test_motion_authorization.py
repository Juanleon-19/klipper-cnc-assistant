import time
import unittest
from unittest.mock import Mock, patch

from klipper_cnc_assistant.machine.state import MachineState, MachinePosition, AxisLimits
from klipper_cnc_assistant.machine.physical_ownership import PhysicalMachineCoordinator, OwnerKind, OwnershipError
from klipper_cnc_assistant.machine.motion_authorization import MotionAuthorizer, MotionRequirements, MotionAuthorizationError
from klipper_cnc_assistant.jog.controller import JogController


def observed_machine(coordinator):
    machine = MachineState(MachinePosition(10, 10, 10), AxisLimits(0, 100),
                           AxisLimits(0, 100), AxisLimits(0, 100), 'xyz', 100, 500)
    machine.bind_physical_session(coordinator.session)
    machine.update_motion((10, 10, 10), 0, 'fake')
    machine.update_toolhead(position=(10, 10, 10))
    machine.update_gcode_move(gcode_position=(10, 10, 10), position=(10, 10, 10))
    machine.update_klippy('ready')
    return machine


class MotionAuthorizationTest(unittest.TestCase):
    def setUp(self):
        self.coordinator = PhysicalMachineCoordinator()
        self.permit = self.coordinator.acquire(OwnerKind.RUNTIME_MOTION, 'test')
        self.machine = observed_machine(self.coordinator)
        self.authorizer = MotionAuthorizer(self.coordinator, self.machine)
        self.client = Mock()
        self.jog = JogController(self.client, self.machine, authorizer=self.authorizer)

    def test_commanded_fresh_does_not_make_live_fresh(self):
        self.machine.live_position_updated_at = time.monotonic() - 10
        self.machine.update_toolhead(position=(20, 20, 20))
        with self.assertRaises(MotionAuthorizationError):
            self.jog.move_relative('x', 1, 1, permit=self.permit)
        self.client.send_gcode.assert_not_called()

    def test_context_expires_between_calculation_and_dispatch(self):
        calculate = self.jog.calculate_target
        clock = {'now': time.monotonic()}
        def expire(*args, **kwargs):
            result = calculate(*args, **kwargs)
            clock['now'] += 3.0
            return result
        with patch('klipper_cnc_assistant.machine.motion_authorization.time.monotonic', side_effect=lambda: clock['now']):
            with patch.object(self.jog, 'calculate_target', side_effect=expire):
                with self.assertRaises(MotionAuthorizationError):
                    self.jog.move_relative('x', 1, 1, permit=self.permit)
        self.client.send_gcode.assert_not_called()

    def test_old_context_not_revived_by_new_sample(self):
        with patch('klipper_cnc_assistant.machine.motion_authorization.time.monotonic', return_value=time.monotonic()):
            context = self.authorizer.require_context(self.permit, 'move')
        self.machine.update_motion((10, 10, 10), 0)
        with patch('klipper_cnc_assistant.machine.motion_authorization.time.monotonic', return_value=context.frame.timestamp + 3):
            with self.assertRaises(MotionAuthorizationError):
                self.authorizer.dispatch(context, self.client.send_gcode)
        self.client.send_gcode.assert_not_called()

    def test_missing_invalid_or_nonfinite_frame_is_rejected(self):
        for position, timestamp in [(None, time.monotonic()), (MachinePosition(float('nan'), 0, 0), time.monotonic()),
                                    (MachinePosition(0, float('inf'), 0), time.monotonic()),
                                    (MachinePosition(0, 0, 0), None), (MachinePosition(0, 0, 0), float('nan'))]:
            with self.subTest(position=position, timestamp=timestamp):
                self.machine.live_position = position
                self.machine.live_position_updated_at = timestamp
                with self.assertRaises(MotionAuthorizationError):
                    self.authorizer.require_context(self.permit, 'move')

    def test_only_actual_gcode_frame_update_refreshes_its_timestamp(self):
        stamp = self.machine.gcode_position_updated_at
        self.machine.update_gcode_move(position=(30, 30, 30))
        self.assertEqual(self.machine.gcode_position_updated_at, stamp)
        self.assertGreaterEqual(self.machine.gcode_move_position_updated_at, stamp)

    def test_valid_job_child_can_dispatch_and_old_session_cannot(self):
        self.coordinator.release(self.permit, quiescent=True)
        root = self.coordinator.acquire(OwnerKind.JOB_EXECUTION, 'job')
        child = self.coordinator.delegate(root, 'tool-change')
        self.jog.move_relative('z', 1, 1, permit=child)
        self.client.send_gcode.assert_called_once()
        self.coordinator.release(child, quiescent=True)
        with self.assertRaises(OwnershipError):
            self.jog.move_relative('z', 1, 1, permit=child)

    def test_homing_does_not_require_a_position_but_requires_ready_session(self):
        self.machine.live_position = None
        self.machine.homed_axes = ''
        self.authorizer.require_context(self.permit, 'home', MotionRequirements(frame=None, homed_axes=''))
        self.machine.update_klippy('shutdown')
        with self.assertRaises(MotionAuthorizationError):
            self.authorizer.require_context(self.permit, 'home', MotionRequirements(frame=None, homed_axes=''))
