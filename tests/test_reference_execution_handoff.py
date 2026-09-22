"""Normal UI reference -> mesh -> JobRun, using fake physical boundaries only."""
from copy import deepcopy
from dataclasses import replace
import unittest
from unittest.mock import patch

from tests import test_probe_and_capture
from tests.test_job_service import FakeAdapter
from klipper_cnc_assistant.application.errors import ApplicationError
from klipper_cnc_assistant.machine.physical_reference import require_current_reference, PhysicalReferenceError


class ReferenceExecutionHandoffTest(unittest.TestCase):
    def setUp(self):
        capture = test_probe_and_capture.ProbeAndCaptureTest()
        capture.setUp()
        self.addCleanup(capture.doCleanups)
        self.capture = capture
        self.fixture = capture.fixture
        self.repo = capture.service.repository
        self.runtime = capture.runtime
        self.project_id, self.operation_id = capture.project_id, capture.operation_id
        self.fixture._upload_and_analyze_operation(self.project_id, self.operation_id,
            content="G21\nG90\nG0 X10 Y10 F120\nG1 X20 Y10 Z-0.050 F120\n")
        self.job = self.fixture.app.state.job_service
        self.maps = self.fixture.app.state.physical_map_service
        self.job.runtime = self.runtime
        self.job.compensated_gcode_service.machine_runtime = self.runtime
        self.adapter = FakeAdapter(self.runtime)
        self.job.adapter_factory = lambda runtime: self.adapter
        # Only in-memory transports; the fake descent never emits G-code.
        response = self.fixture.client.post(capture.url + '/probe-and-capture')
        self.assertEqual(response.status_code, 200, response.text)
        project = self.repo.load_project(self.project_id)
        self.setup_id = project.get_operation(self.operation_id).setup_id
        self.canonical = deepcopy(project.get_setup(self.setup_id).preparacion.physical_reference)
        self.token = self.canonical['reference']['physical_reference_token']
        self.tool = self.token['tool_id']
        base = f'/api/projects/{self.project_id}/operations/{self.operation_id}/physical-map'
        config = dict(grid_mode='manual', rows=2, columns=2, margin_mm=0,
                      edge_margin_left_mm=0, edge_margin_right_mm=0,
                      edge_margin_bottom_mm=0, edge_margin_top_mm=0)
        response = self.fixture.client.post(base + '/plan-from-reference', json=config)
        self.assertEqual(response.status_code, 200, response.text)
        self.map = response.json()['payload']
        for point in self.map['points']:
            if point['status'] == 'MEASURED':
                continue
            self.maps.record_point(project_id=self.project_id, map_id=self.map['map_id'],
                                   point_index=point['index'], z_measured=30.0,
                                   attempts=1, duration_s=.01)
        self.map = self.maps.get_active(self.project_id, self.operation_id)
        self.assertEqual(self.map['status'], 'MESH_COMPLETE')
        self.assertEqual(self.map['validation']['status'], 'VALID')
        self.args = dict(project_id=self.project_id, setup_id=self.setup_id, face='superior')
        self.context = self.job._context(**self.args)

    def current(self):
        return require_current_reference(self.repo, self.runtime, self.project_id,
                                         self.operation_id, self.maps.get_active(self.project_id, self.operation_id),
                                         expected_token=self.token)

    def reference_check(self, run):
        return next(c for c in run['checks'] if c['name'] == 'referencia_inicial')

    def save_map(self):
        self.repo.save_height_map_payload(self.project_id, self.map['map_id'], self.map)

    def assert_blocked(self):
        with self.assertRaises(PhysicalReferenceError):
            self.current()
        run = self.job.prepare_run(**self.args)
        self.assertFalse(self.reference_check(run)['ok'])
        self.assertNotIn('Falta referencia Z', self.reference_check(run)['detail'])
        with patch.object(self.job, '_start_worker') as worker:
            with self.assertRaises(ApplicationError):
                self.job.start_run(**self.args)
        worker.assert_not_called()
        self.assertEqual(self.adapter.uploads, [])
        self.assertEqual(self.adapter.started, [])

    def test_same_capture_survives_mesh_plan_prepare_start_and_jit(self):
        self.assertEqual(self.current(), self.token)
        plan = self.job.get_plan(**self.args)
        self.assertEqual(plan['operations'][0]['reference_status'], 'LISTA')
        self.assertEqual(plan['operations'][0]['tool_installation_calibration']['physical_reference_token'], self.token)
        run = self.job.prepare_run(**self.args)
        self.assertTrue(self.reference_check(run)['ok'], run['checks'])
        self.assertEqual(run['state'], 'JOB_READY', run['checks'])
        with patch.object(self.job, '_start_worker'):
            started = self.job.start_run(**self.args)
        self.assertEqual(started['state'], 'JOB_STARTING')
        self.job._execute_next_operation(self.context, started)
        self.assertEqual(len(self.adapter.started), 1)
        self.assertEqual(self.current(), self.token)
        operation = started['operations'][0]
        self.assertEqual(operation['physical_reference_token'], self.token)
        import json
        metadata = json.loads(self.repo.read_project_file(self.project_id, operation['generated_metadata_path']))
        self.assertEqual(metadata['physical_reference_token'], self.token)
        self.assertEqual(self.capture.client.scripts, [])

    def test_same_z_without_token_is_blocked(self):
        self.map['tool_references'][self.tool].pop('physical_reference_token')
        self.save_map()
        self.assert_blocked()

    def test_different_session_blocks_even_cached_ready_plan(self):
        self.job.prepare_run(**self.args)
        self.runtime.current_physical_session_id = lambda: 'new-session'
        self.assert_blocked()

    def test_start_rechecks_session_without_reprepare(self):
        self.assertEqual(self.job.prepare_run(**self.args)['state'], 'JOB_READY')
        self.runtime.current_physical_session_id = lambda: 'restarted-session'
        with patch.object(self.job, '_start_worker') as worker:
            with self.assertRaises(ApplicationError):
                self.job.start_run(**self.args)
        worker.assert_not_called()
        self.assertEqual(self.adapter.started, [])

    def test_different_tool_cannot_inherit_initial_measurement(self):
        project = self.repo.load_project(self.project_id)
        operation = project.get_operation(self.operation_id)
        self.repo.save_project(project.replace_operation(replace(operation, tool_id='another-tool')))
        self.assert_blocked()

    def test_tampered_measurement_is_blocked(self):
        for field, value in [('reference_revision', 'changed'), ('measured_at', '2026-09-20T00:00:00+00:00'),
                             ('physical_session_id', 'changed'), ('measured_position', dict(x_mm=4, y_mm=5, z_mm=30.1))]:
            with self.subTest(field=field):
                self.map['tool_references'][self.tool] = deepcopy(self.canonical['reference'])
                self.map['tool_references'][self.tool]['physical_reference_token'][field] = value
                self.save_map()
                self.assert_blocked()

    def test_placement_change_blocks_saved_z(self):
        project = self.repo.load_project(self.project_id)
        setup = project.get_setup(self.setup_id)
        self.repo.save_project(project.replace_setup(replace(setup, placement_revision='new-placement')))
        self.assert_blocked()

    def test_map_probe_profile_change_requires_new_binding(self):
        self.map['probe_config']['probe_step_mm'] = .234
        self.save_map()
        self.assert_blocked()

    def test_canonical_evidence_missing_is_not_reconstructed_from_map(self):
        project = self.repo.load_project(self.project_id)
        setup = project.get_setup(self.setup_id)
        self.repo.save_project(project.replace_setup(replace(setup,
            preparacion=replace(setup.preparacion, physical_reference=None))))
        self.assert_blocked()
