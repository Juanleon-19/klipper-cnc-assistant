"""Real discovery/runtime with fake HTTP only; never connect or send."""
from copy import deepcopy
import time
import unittest
from unittest.mock import Mock
from tests import test_machine_runtime as fixtures
from klipper_cnc_assistant.machine.discovery import discover_machine

class HTTPVelocityResidualTest(unittest.TestCase):
    def setUp(self):
        self.runtime, self.transport = fixtures.ReferencePointMoveTest()._runtime()
        self.status = {'toolhead': {'position':[4,5,30], 'homed_axes':'xyz',
            'axis_minimum':[0,0,0], 'axis_maximum':[100,100,200],
            'max_velocity':100, 'max_accel':500},
            'motion_report':{'live_position':[4,5,30], 'live_velocity':0}}
        self.client = Mock()
        self.client.get_server_info.return_value = {'klippy_state':'ready'}
        self.client.query_objects.side_effect = lambda _:deepcopy(self.status)
        self.runtime._client = self.client
        self.runtime._discovery = discover_machine

    def test_observed_negative_roundoff_is_zero_but_position_is_real(self):
        self.status['motion_report']['live_velocity'] = -5.551115123125783e-17
        snap = self.runtime.refresh_observed_state()
        self.assertEqual(snap['klipper']['position']['velocity'],0)
        self.assertEqual(snap['klipper']['position']['source'],'motion_report.live_position')
        self.client.send_gcode.assert_not_called()

    def test_missing_invalid_or_negative_velocity_blocks(self):
        for v in [None, float('nan'), float('inf'), -0.01, -1e-11]:
            with self.subTest(v=v):
                self.status['motion_report']['live_velocity'] = v
                with self.assertRaisesRegex(RuntimeError,'posición/velocidad'):
                    self.runtime.refresh_observed_state()
        self.client.send_gcode.assert_not_called()

    def test_missing_position_never_uses_toolhead_fallback(self):
        self.status['motion_report'].pop('live_position')
        with self.assertRaisesRegex(RuntimeError,'posición/velocidad'):
            self.runtime.refresh_observed_state()

    def test_stale_discovery_is_not_retimestamped(self):
        stale = discover_machine(self.client)
        stale.live_position_updated_at = time.monotonic()-30
        self.runtime._discovery = lambda _: stale
        with self.assertRaisesRegex(RuntimeError,'posición/velocidad'):
            self.runtime.refresh_observed_state()

    def test_http_failure_and_shutdown_block(self):
        self.client.query_objects.side_effect = RuntimeError('HTTP failed')
        with self.assertRaisesRegex(RuntimeError,'HTTP failed'):
            self.runtime.refresh_observed_state()
        self.client.get_server_info.return_value = {'klippy_state':'shutdown'}
        with self.assertRaisesRegex(RuntimeError,'ready'):
            self.runtime.refresh_observed_state()
        self.client.send_gcode.assert_not_called()

    def test_session_change_during_http_blocks(self):
        def query(_):
            self.runtime._serial_generation += 1
            return deepcopy(self.status)
        self.client.query_objects.side_effect = query
        with self.assertRaisesRegex(RuntimeError,'sesión física cambió'):
            self.runtime.refresh_observed_state()
        self.client.send_gcode.assert_not_called()

    def test_spindle_confirmation_needs_idle_velocity_and_homing(self):
        from types import SimpleNamespace
        from klipper_cnc_assistant.execution.job_service import JobService
        adapter = Mock()
        adapter.print_status.return_value = {'state':'complete','is_active':False}
        service = SimpleNamespace(runtime=self.runtime)
        self.status['motion_report']['live_velocity']=-5.551115123125783e-17
        JobService._validate_spindle_stop_confirmation(service, adapter)
        for velocity in [1., float('nan'), None]:
            self.status['motion_report']['live_velocity']=velocity
            with self.assertRaises(Exception):
                JobService._validate_spindle_stop_confirmation(service,adapter)
        self.status['motion_report']['live_velocity']=0
        self.status['toolhead']['homed_axes']=''
        with self.assertRaisesRegex(Exception,'homing'):
            JobService._validate_spindle_stop_confirmation(service,adapter)
        adapter.print_status.return_value={'state':'printing','is_active':True}
        with self.assertRaisesRegex(Exception,'imprimiendo'):
            JobService._validate_spindle_stop_confirmation(service,adapter)
        self.client.send_gcode.assert_not_called()
