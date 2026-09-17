"""Joystick intent refreshes observations; all transports and ownership are fake."""
import re
import time
import unittest
from unittest.mock import Mock

from klipper_cnc_assistant.input.command_mapper import ControllerCommand
from klipper_cnc_assistant.input.serial_driver import ControllerPacket
from klipper_cnc_assistant.jog.controller import JogController
from klipper_cnc_assistant.jog.manual import ManualJogController
from klipper_cnc_assistant.machine.discovery import discover_machine
from klipper_cnc_assistant.machine.physical_ownership import OwnerKind
from klipper_cnc_assistant.machine.runtime import MachineRuntimeState
from klipper_cnc_assistant.machine.state import AxisLimits, MachinePosition, MachineState
from tests.test_machine_runtime import physical_runtime_with_machine


class JoystickFreshObservationTest(unittest.TestCase):
    def setUp(self):
        self.machine = MachineState(MachinePosition(10, 10, 10), AxisLimits(0, 100),
                                    AxisLimits(0, 100), AxisLimits(0, 200), 'xyz', 100, 500)
        self.runtime, self.client = physical_runtime_with_machine(self.machine)
        # The shared fake handles absolute moves; manual jog uses G91.
        absolute_send = self.client.send_gcode

        def send_relative(script, **kwargs):
            if '\nG91\n' in script:
                start = self.machine.get_motion_snapshot()
                absolute_script = re.sub(
                    r'\b([XYZ])(-?\d+(?:\.\d+)?)',
                    lambda m: f'{m[1]}{start[m[1].lower()] + float(m[2]):.6f}', script)
                result = absolute_send(absolute_script, **kwargs)
                self.client.scripts[-1] = script
                return result
            return absolute_send(script, **kwargs)

        self.client.send_gcode = send_relative
        self.runtime._jog = JogController(
            self.client, self.machine, authorizer=self.runtime._motion_authorizer(),
            before_send=self.runtime._mark_operation_emission)
        self.runtime._manual = ManualJogController(self.runtime._jog)
        self.runtime._manual_enabled = True
        self.runtime._diagnostic_input_only = False
        self.runtime._ready_for_jog = True
        self.runtime._state = MachineRuntimeState.DEGRADED
        self.runtime._last_error = 'Telemetría obsoleta.'
        self.machine.live_position_updated_at = time.monotonic() - 10
        self.machine.klippy_updated_at = time.monotonic() - 10
        self.response = {
            'toolhead': {'position': [40, 10, 10], 'homed_axes': 'xyz',
                         'axis_minimum': [0, 0, 0], 'axis_maximum': [100, 100, 200],
                         'max_velocity': 100, 'max_accel': 500},
            'motion_report': {'live_position': [40, 10, 10], 'live_velocity': 0},
            'gcode_move': {'position': [40, 10, 10], 'gcode_position': [40, 10, 10],
                           'absolute_coordinates': True, 'homing_origin': [0, 0, 0]},
        }
        self.client.get_server_info = Mock(return_value={'klippy_state': 'ready'})
        self.client.query_objects = Mock(return_value=self.response)
        self.runtime._discovery = discover_machine

    def packet(self, direction='RIGHT'):
        self.runtime._handle_controller_packet(
            ControllerPacket(direction=direction, joystick_button=False,
                             external_button=False, probe=False, x=900, y=512),
            ControllerCommand(jog_x=1 if direction == 'RIGHT' else 0))

    def assert_no_dispatch(self):
        self.assertEqual(self.client.scripts, [])
        self.assertFalse(self.runtime._movement_lock.locked())
        self.assertIsNone(self.runtime._active_operation)
        self.assertIsNotNone(self.runtime._last_error)

    def test_stale_idle_position_is_refreshed_before_calculating_target(self):
        self.packet()
        self.client.query_objects.assert_called_once()
        self.assertEqual(len(self.client.scripts), 1)
        self.assertAlmostEqual(self.runtime._last_movement['current_position'], 40)
        self.assertAlmostEqual(self.runtime._last_movement['target'], 41)
        self.assertNotIn('G28', self.client.scripts[0])
        self.assertIsNone(self.runtime._last_error)
        self.assertEqual(self.runtime._state, MachineRuntimeState.WAITING_FOR_XY_REFERENCE)
        self.assertFalse(self.runtime._movement_lock.locked())

    def test_held_stick_does_not_repeat_refresh_or_dispatch(self):
        self.packet()
        self.packet()
        self.client.query_objects.assert_called_once()
        self.assertEqual(len(self.client.scripts), 1)

    def test_success_preserves_captured_reference_state(self):
        self.runtime._state = MachineRuntimeState.REFERENCE_CAPTURED
        self.packet()
        self.assertEqual(len(self.client.scripts), 1)
        self.assertEqual(self.runtime._state, MachineRuntimeState.REFERENCE_CAPTURED)

    def test_fresh_position_with_stale_ready_state_also_gets_refreshed(self):
        self.machine.live_position_updated_at = time.monotonic()
        self.packet()
        self.client.get_server_info.assert_called_once()
        self.assertEqual(len(self.client.scripts), 1)

    def test_negative_y_jog_uses_refreshed_y_and_preserves_direction(self):
        self.runtime._handle_controller_packet(
            ControllerPacket(direction='DOWN', joystick_button=False,
                             external_button=False, probe=False, x=512, y=100),
            ControllerCommand(jog_y=-1))
        self.assertEqual(len(self.client.scripts), 1)
        self.assertAlmostEqual(self.runtime._last_movement['current_position'], 10)
        self.assertAlmostEqual(self.runtime._last_movement['target'], 9)
        self.assertIn('G1 Y-1.000000', self.client.scripts[0])

    def test_closed_process_access_blocks_http_and_dispatch(self):
        self.runtime._process_lock.close()
        self.packet()
        self.assert_no_dispatch()
        self.client.get_server_info.assert_not_called()

    def test_http_failure_releases_ownership_and_requires_center_before_retry(self):
        self.client.query_objects.side_effect = TimeoutError('offline')
        self.packet()
        self.assert_no_dispatch()
        self.assertIn('actualizar', self.runtime._last_error)
        self.client.query_objects.side_effect = None
        self.packet()
        self.assertEqual(self.client.query_objects.call_count, 1)
        self.packet('CENTER')
        self.packet()
        self.assertEqual(len(self.client.scripts), 1)

    def test_malformed_http_response_cannot_kill_packet_handler_or_dispatch(self):
        self.response['toolhead'] = {}
        self.packet()
        self.assert_no_dispatch()

    def test_missing_live_position_does_not_freshen_cached_position(self):
        self.response.pop('motion_report')
        previous = self.machine.live_position_updated_at
        self.packet()
        self.assert_no_dispatch()
        self.assertEqual(self.machine.live_position_updated_at, previous)

    def test_shutdown_remains_blocked(self):
        self.client.get_server_info.return_value = {'klippy_state': 'shutdown'}
        self.packet()
        self.assert_no_dispatch()
        self.client.query_objects.assert_not_called()

    def test_http_observation_of_unhomed_axes_remains_blocked(self):
        self.response['toolhead']['homed_axes'] = ''
        self.packet()
        self.assert_no_dispatch()
        self.assertIn('homing', self.runtime._last_error)

    def test_job_ownership_blocks_even_the_refresh(self):
        self.runtime.physical_ownership.acquire(OwnerKind.JOB_EXECUTION, 'job')
        self.packet()
        self.assert_no_dispatch()
        self.client.get_server_info.assert_not_called()
        self.assertEqual(self.runtime.physical_ownership.snapshot()['owner_id'], 'job')

    def test_cancel_during_http_wait_prevents_dispatch(self):
        def reply(_objects):
            self.runtime._active_operation.cancel_event.set()
            return self.response
        self.client.query_objects.side_effect = reply
        self.packet()
        self.assert_no_dispatch()
        self.assertIn('cancelada', self.runtime._last_error)

    def test_arduino_generation_change_during_http_wait_prevents_dispatch(self):
        def reply(_objects):
            self.runtime._serial_generation += 1
            return self.response
        self.client.query_objects.side_effect = reply
        self.packet()
        self.assert_no_dispatch()
        self.assertIn('sesión Arduino', self.runtime._last_error)

    def test_manual_disabled_during_http_wait_prevents_dispatch(self):
        def reply(_objects):
            self.runtime._manual_enabled = False
            return self.response
        self.client.query_objects.side_effect = reply
        self.packet()
        self.assert_no_dispatch()

    def test_slow_http_cannot_authorize_old_joystick_intent(self):
        def reply(_objects):
            self.runtime._last_packet_at = time.monotonic() - 10
            return self.response
        self.client.query_objects.side_effect = reply
        self.packet()
        self.assert_no_dispatch()
        self.assertIn('Arduino obsoleto', self.runtime._last_error)
