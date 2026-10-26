"""Arrival must follow observed motion, not the already queued G-code endpoint."""
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from tests.test_machine_runtime import config, physical_runtime_with_machine
from klipper_cnc_assistant.machine.state import MachineState, MachinePosition, AxisLimits
from klipper_cnc_assistant.machine.runtime import MachineRuntimeError

class ArrivalObservationTest(unittest.TestCase):
    def setUp(self):
        machine = MachineState(MachinePosition(100, 0, 115), AxisLimits(0, 200),
            AxisLimits(0, 200), AxisLimits(0, 200), 'xyz', 100, 500)
        self.runtime, self.client = physical_runtime_with_machine(machine, cfg=config())
        self.clock = 100.0
        self.machine = machine

    def frame(self, *, velocity=None, offset=0, missing=False):
        elapsed = self.clock - 100
        x = max(0, 100 - 10 * elapsed)
        result = {'live_position': {'x': x + offset, 'y': 0, 'z': 115},
            'gcode_position': {'x': 0, 'y': 0, 'z': 115},
            'gcode_move_position': {'x': offset, 'y': 0, 'z': 115},
            'live_position_age_s': 0, 'gcode_position_age_s': 0,
            'gcode_move_position_age_s': 0,
            'velocity': (10 if x > 0 else 0) if velocity is None else velocity,
            'velocity_age_s': 0, 'velocity_updated_at': self.clock}
        if missing: result['live_position'] = None
        return result

    def wait(self, **kwargs):
        def sleep(seconds): self.clock += seconds
        with patch('klipper_cnc_assistant.machine.runtime.time', SimpleNamespace(monotonic=lambda:self.clock,sleep=sleep)), \
             patch.object(self.machine, 'get_motion_snapshot', side_effect=lambda:self.frame(**kwargs)), \
             patch.object(self.runtime, '_assert_safety_for_connection'), \
             patch.object(self.runtime, '_refresh_machine_best_effort'):
            return self.runtime._wait_for_targets({'x':0}, 'tool_change_xy', operation_timeout_s=20, coordinate_frame='gcode_position')

    def test_queued_endpoint_does_not_start_settle_timer_while_still_travelling(self):
        result = self.wait()
        self.assertGreaterEqual(result['elapsed_s'], 10)
        self.assertEqual(result['observed_velocity_mm_s'], 0)
        self.assertEqual(self.client.scripts, [])

    def test_live_coordinate_offset_is_applied_before_testing_arrival(self):
        result = self.wait(offset=4)
        self.assertGreaterEqual(result['elapsed_s'], 10)
        self.assertAlmostEqual(result['observed_position']['x'],0)

    def test_missing_live_position_never_confirms_commanded_endpoint(self):
        with self.assertRaises(MachineRuntimeError): self.wait(missing=True,velocity=0)

    def test_real_velocity_at_arrival_still_fails_closed(self):
        with self.assertRaisesRegex(MachineRuntimeError,'velocidad no se estabilizó'):
            self.wait(velocity=10)

    def test_unknown_transform_never_confirms_arrival(self):
        frame=self.frame(velocity=0)
        frame['gcode_move_position']=None
        with self.assertRaises(MachineRuntimeError):
            self.runtime._targets_reached(frame,{'x':0},coordinate_frame='gcode_position',label='test')

    def test_stale_live_or_offset_cannot_confirm_arrival(self):
        for key in ('live_position_age_s','gcode_position_age_s','gcode_move_position_age_s'):
            for age in (None, 3.0, float('nan')):
                with self.subTest(key=key, age=age):
                    self.clock=111
                    frame=self.frame(velocity=0);frame[key]=age
                    reached, _, _, _, observed_age=self.runtime._targets_reached(frame,{'x':0},coordinate_frame='gcode_position',label='test')
                    self.assertTrue(self.runtime._frame_is_stale(observed_age))
                    self.assertFalse(reached)

    def test_nonfinite_live_position_rejected(self):
        frame=self.frame(velocity=0);frame['live_position']['x']=float('nan')
        with self.assertRaises(MachineRuntimeError):
            self.runtime._targets_reached(frame,{'x':0},coordinate_frame='gcode_position',label='test')
