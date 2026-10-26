"""One temporary application, real services/runtime, in-memory physical transports."""
from dataclasses import replace
import time
import json
import os
import re
import asyncio
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests import test_api, test_machine_runtime as machine_fakes
from klipper_cnc_assistant.execution.job_service import MoonrakerJobAdapter
from klipper_cnc_assistant.machine.state import MachineState, MachinePosition, AxisLimits
from klipper_cnc_assistant.machine.config import MachineMode
from klipper_cnc_assistant.machine.recoverable_runtime import RecoverableMachineRuntime


class FunctionalJourneyTest(unittest.TestCase):
    def setUp(self):
        self.fixture = test_api.ApiTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.app, self.client = self.fixture.app, self.fixture.client
        self.project = self.fixture._create_project()
        self.operation = self.fixture._create_operation(self.project)
        self.fixture._upload_and_analyze_operation(self.project, self.operation,
            content='G21\nG90\nG0 X10 Y10 F120\nG1 X20 Y10 Z-0.050 F120\n')
        for index, tool in [(2, 'tool-label-v-bit-30'), (3, 'synthetic-tool-b')]:
            created = self.client.post(f'/api/projects/{self.project}/operations',
                json={'nombre': f'Synthetic operation {index}', 'tipo': 'aislamiento',
                      'cara': 'superior', 'orden': index, 'herramienta': 'V-bit 30'})
            self.assertEqual(created.status_code, 201, created.text)
            operation = created.json()['id']
            changed = self.client.patch(f'/api/projects/{self.project}/operations/{operation}',
                json={'nombre': f'Synthetic operation {index}', 'tool_id': tool,
                      'tool_reference_profile': 'long_tool' if index == 3 else 'standard'})
            self.assertEqual(changed.status_code, 200, changed.text)
            self.fixture._upload_and_analyze_operation(self.project, operation,
                content='G21\nG90\nG0 X10 Y10 F120\nG1 X20 Y10 Z-0.050 F120\n')
        self.repo = self.app.state.project_service.repository
        self.setup = self.repo.load_project(self.project).get_operation(self.operation).setup_id
        self.machine = MachineState(position=MachinePosition(4, 5, 31),
            x_limits=AxisLimits(0, 200), y_limits=AxisLimits(0, 200),
            z_limits=AxisLimits(0, 200), homed_axes='xyz', max_velocity=100,
            max_accel=500, live_velocity=0)
        self.machine.update_klippy('ready')
        self.machine.update_motion(live_position=(4, 5, 31), live_velocity=0)
        class Transport(machine_fakes.ReadyInfoClient):
            filename = ''
            started_at = None
            def __init__(self, machine):
                super().__init__(machine)
                self.started = []
            def get_analysis_status(self):
                return {'estimator_ready': False}
            def upload_file(self, *, local_path, remote_dir, checksum, print_file):
                assert print_file is False
                return {'item': {'path': remote_dir + '/' + local_path.name, 'root': 'gcodes'},
                        'print_started': False, 'print_queued': False}
            def start_print(self, path):
                self.started.append(path)
                self.filename = path
                self.started_at = time.monotonic()
                return {'started': path}
            def query_objects(self, objects):
                active = self.started_at is not None and time.monotonic() - self.started_at < 1.0
                state = 'printing' if active else ('complete' if self.started_at else 'standby')
                return {'webhooks': {'state': 'ready'},
                        'print_stats': {'state': state, 'filename': self.filename, 'print_duration': 1.0},
                        'virtual_sdcard': {'is_active': active, 'progress': .5 if active else 1.,
                                          'file_position': 100, 'file_size': 100},
                        'toolhead': {'homed_axes': self.machine.homed_axes, 'position': self.machine.position.as_tuple()},
                        'motion_report': {'live_position': self.machine.position.as_tuple(), 'live_velocity': 0.0}}
            def send_gcode(self, script, **kwargs):
                translated = script
                if 'G91' in script:
                    position = self.machine.position
                    for axis in 'XYZ':
                        translated = re.sub(r'\b' + axis + r'(-?\d+(?:\.\d+)?)',
                            lambda m: axis + str(getattr(position, axis.lower()) + float(m[1])), translated)
                result = super().send_gcode(translated, **kwargs)
                self.scripts[-1] = script
                time.sleep(.06)  # Fake controller/serial observation cadence.
                return result
        self.transport = Transport(self.machine)
        machine = self.machine
        self.pause_position_samples = threading.Event()
        self.position_samples_paused = threading.Event()
        pause_samples, paused = self.pause_position_samples, self.position_samples_paused
        class Telemetry(machine_fakes.PassiveTelemetry):
            async def run(self):
                while not self._stop.is_set():
                    # New simulator samples, including stationary XYZ; not a
                    # production timestamp override or authorizer replacement.
                    if pause_samples.is_set():
                        paused.set()
                    else:
                        machine.update_motion(live_position=machine.position.as_tuple(), live_velocity=0,
                                              source='websocket')
                    machine.update_klippy('ready')
                    if self._snapshot_callback:
                        self._snapshot_callback({'state': 'CONNECTED', 'last_message_at': time.monotonic(),
                                                 'last_error': None, 'reconnects': 0})
                    await asyncio.sleep(.05)
        class Driver(machine_fakes.ReconnectableDriver):
            def read_packet(self):
                packet = super().read_packet()
                return replace(packet, probe=machine.position.z <= 30)
        def observe(_client):
            self.machine.update_motion(live_position=self.machine.position.as_tuple(), live_velocity=0)
            return self.machine
        self.runtime = RecoverableMachineRuntime(
            machine_fakes.config(MachineMode.PHYSICAL, moonraker_url='http://fake.invalid',
                moonraker_ws='ws://fake.invalid', serial_port='/tmp/kca-fake-serial',
                reference_prep_z_mm=31, tool_change_clearance_z_mm=35,
                tool_change_work_z_mm=35, long_tool_change_clearance_z_mm=40),
            client_factory=lambda *a, **kw: self.transport,
            telemetry_factory=Telemetry, serial_factory=Driver,
            discovery=observe)
        real_exists = os.path.exists
        exists = patch('klipper_cnc_assistant.input.connection_manager.os.path.exists',
                       side_effect=lambda path: str(path) == '/tmp/kca-fake-serial' or real_exists(path))
        ports = patch('klipper_cnc_assistant.input.connection_manager.list_ports.comports', return_value=[
            SimpleNamespace(device='/tmp/kca-fake-serial', vid=0x1234, pid=0x5678,
                            serial_number='fake-journey', location='fake')])
        exists.start(); ports.start()
        self.addCleanup(exists.stop); self.addCleanup(ports.stop)
        self.addCleanup(self.runtime.stop)
        self.app.state.machine_runtime = self.runtime
        self.job = self.app.state.job_service
        self.job.runtime = self.runtime
        self.app.state.compensated_gcode_service.machine_runtime = self.runtime
        self.adapter = MoonrakerJobAdapter(self.runtime)
        self.job.adapter_factory = lambda _: self.adapter
        self.args = dict(project_id=self.project, setup_id=self.setup, face='superior')
        self.context = self.job._context(**self.args)

    def post(self, path, payload=None):
        response = self.client.post(path, json=payload or {})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_reset_connect_home_prepare_reference_mesh_execution(self):
        self.post('/api/machine/connect')
        # Synthetic completed history, never copied from productive data.
        run = self.job.prepare_run(**self.args)
        op = run['operations'][0]
        op.update(execution_status='COMPLETED', observed_printing=True,
                  finished_at='2026-01-01T00:00:00Z', remote_file='synthetic/old.gcode',
                  moonraker_filename='synthetic/old.gcode')
        run.update(state='SPINDLE_STOP_REQUIRED', start_attempt=dict(status='sent',
            may_have_been_sent=True, operation_id=op['operation_id'], remote_file=op['remote_file']))
        self.job._save_run(self.context, run)
        self.transport.filename = ''
        self.runtime.physical_ownership.enter_recovery('Synthetic retired operation')
        before = self.job._run_file(self.context).read_bytes()
        rejected = self.client.post(f'/api/projects/{self.project}/setups/{self.setup}/reset-preparation', json={})
        self.assertEqual(rejected.status_code, 400)
        self.assertIn('recuperar controles', rejected.text)
        self.assertEqual(before, self.job._run_file(self.context).read_bytes())
        self.post('/api/machine/recover-idle-controls')
        # Interrupt administrative preparation before any project/map write.
        with self.assertRaises(OSError):
            with self.job.administrative_mutation('reset_setup_map', allow_started_runs=True) as guard:
                guard.begin_mutation(project_id=self.project, setup_id=self.setup)
                raise OSError('synthetic administrative interruption')
        journal = json.loads(self.job.administrative_pending_path.read_text())
        scripts = list(self.transport.scripts)
        recovered = self.post('/api/machine/administrative-recovery/' + journal['id'])
        self.assertTrue(recovered['physical_recovery_required'])
        self.assertEqual(self.transport.scripts, scripts)
        self.assertEqual(before, self.job._run_file(self.context).read_bytes())
        self.assertTrue(self.runtime.physical_ownership.snapshot()['recovery_pending'])
        self.post('/api/machine/recover-idle-controls')
        root = f'/api/projects/{self.project}'
        result = self.post(root + f'/setups/{self.setup}/reset-preparation', {'motivo': 'synthetic restart'})
        self.assertEqual(result['execution_reset']['current_runs_cleared'], 1)
        self.assertEqual(self.runtime.snapshot()['state'], 'DISCONNECTED')
        self.assertEqual(len(self.job.history(**self.args)), 1)
        local_base = root + f'/operations/{self.operation}/physical-map'
        local_config = dict(grid_mode='manual', rows=2, columns=2, margin_mm=0, safe_z_mm=5,
            edge_margin_left_mm=0, edge_margin_right_mm=0, edge_margin_bottom_mm=0, edge_margin_top_mm=0)
        preview = self.post(local_base + '/preview', local_config)['payload']
        local_config['preview_fingerprint'] = preview['mesh_fingerprint']
        local = self.post(local_base + '/validate', local_config)
        self.assertTrue(local['local_coverage_valid'])
        self.assertEqual([i['code'] for i in local['blocking_issues']], ['TRANSFORM_UNAVAILABLE'])
        self.assertIsNone(self.runtime._client)
        self.post('/api/machine/connect')
        self.post('/api/machine/home')
        self.pause_position_samples.set()
        self.assertTrue(self.position_samples_paused.wait(2))
        self.machine.live_position_updated_at -= 60
        self.assertFalse(self.runtime.snapshot()['safety']['telemetry_recent'])
        self.post('/api/machine/prepare-position', {'tool_reference_profile': 'standard'})
        self.pause_position_samples.clear()
        self.assertEqual(self.machine.position.z, 35)
        self.post('/api/machine/manual-control', {'enabled': True})
        from klipper_cnc_assistant.input.command_mapper import CommandMapper
        from klipper_cnc_assistant.input.serial_driver import ControllerPacket
        packet = ControllerPacket(direction='RIGHT', joystick_button=False,
            external_button=False, probe=False, x=900, y=512)
        deadline = time.monotonic() + 2
        while not self.runtime._ready_for_jog and time.monotonic() < deadline:
            time.sleep(.02)
        self.assertTrue(self.runtime._ready_for_jog)
        before_x = self.machine.position.x
        self.runtime._handle_controller_packet(packet, CommandMapper().map(packet))
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and (self.machine.position.x == before_x or self.runtime._active_operation):
            time.sleep(.02)
        self.assertGreater(self.machine.position.x, before_x)
        ref = root + f'/operations/{self.operation}/reference-session'
        self.post(ref + '/probe-and-capture')
        saved = self.repo.load_project(self.project).get_setup(self.setup).preparacion
        self.assertEqual(saved.origen_trabajo.x_mm, self.machine.position.x)
        self.assertIsNotNone(saved.physical_reference)
        base = root + f'/operations/{self.operation}/physical-map'
        config = dict(grid_mode='manual', rows=2, columns=2, margin_mm=0, safe_z_mm=5,
            edge_margin_left_mm=0, edge_margin_right_mm=0,
            edge_margin_bottom_mm=0, edge_margin_top_mm=0)
        bad = dict(config, margin_mm=12, edge_margin_left_mm=12, edge_margin_right_mm=12,
                   edge_margin_bottom_mm=12, edge_margin_top_mm=12)
        bad_preview = self.post(base + '/preview', bad)['payload']
        bad['preview_fingerprint'] = bad_preview['mesh_fingerprint']
        self.assertFalse(self.post(base + '/validate', bad)['coverage_valid'])
        from klipper_cnc_assistant.application.errors import NotFoundError
        with self.assertRaises(NotFoundError):
            self.app.state.physical_map_service.get_active(self.project, self.operation)
        preview = self.post(base + '/preview', config)['payload']
        config['preview_fingerprint'] = preview['mesh_fingerprint']
        self.assertTrue(self.post(base + '/validate', config)['coverage_valid'])
        mesh = self.post(base + '/plan-from-reference', config)['payload']
        self.post(root + f"/physical-maps/{mesh['map_id']}/execute-all")
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            mesh = self.app.state.physical_map_service.get_active(self.project, self.operation)
            if mesh['status'] in {'MESH_COMPLETE', 'MESH_FAILED', 'MESH_PAUSED'}:
                break
            time.sleep(.1)
        self.assertEqual(mesh['status'], 'MESH_COMPLETE', mesh)
        self.assertEqual(mesh['validation']['status'], 'VALID')
        for worker in self.app.state.mesh_execution_service.application_workers():
            worker.join(5)
            self.assertFalse(worker.is_alive())
        target = dict(setup_id=self.setup, face='superior')
        prepared = self.post(root + '/job-run/prepare', target)
        self.assertEqual(prepared['state'], 'JOB_READY', prepared['checks'])
        self.post(root + '/job-run/start', dict(target, spindle_prepared=True))
        worker = self.job._threads.get((self.project, self.setup, 'superior'))
        if worker:
            worker.join(10)
            self.assertFalse(worker.is_alive())
        current = self.job._load_run(self.context)
        self.assertEqual(current['state'], 'READY_TO_RESUME', current)
        self.assertEqual(len(self.transport.started), 1)
        self.post(root + '/job-run/action', dict(target, action='continue'))
        worker = self.job._threads.get((self.project, self.setup, 'superior'))
        if worker:
            worker.join(10)
            self.assertFalse(worker.is_alive())
        current = self.job._load_run(self.context)
        self.assertEqual(current['state'], 'SPINDLE_STOP_REQUIRED', current)
        self.assertEqual(len(self.transport.started), 2)
        self.assertEqual([op['execution_status'] for op in current['operations'][:2]], ['COMPLETED', 'COMPLETED'])
        for action, expected in [('confirm-spindle-stopped', 'TOOL_CHANGE_REQUIRED'),
                                 ('confirm-tool-change', 'READY_TO_RESUME'),
                                 ('continue', 'JOB_COMPLETE')]:
            self.post(root + '/job-run/action', dict(target, action=action))
            worker = self.job._threads.get((self.project, self.setup, 'superior'))
            if worker:
                worker.join(20)
                self.assertFalse(worker.is_alive())
            current = self.job._load_run(self.context)
            self.assertEqual(current['state'], expected, current)
        self.assertEqual(len(self.transport.started), 3)
        self.assertEqual(len(set(self.transport.started)), 3)
        self.assertFalse(any('M5' in script or 'M3' in script for script in self.transport.scripts))
        self.assertTrue(current['operations'][0]['generated_file'])
