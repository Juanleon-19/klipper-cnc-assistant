"""Worker transition tests: real persistence/finally, fake physical transport only."""
from copy import deepcopy
import threading
import time
import unittest
from unittest.mock import patch
from tests import test_mesh_preparation
from klipper_cnc_assistant.application.errors import ApplicationError
from klipper_cnc_assistant.execution.mesh_execution_service import MeshExecutionService


class MeshWorkflowStatesTest(unittest.TestCase):
    def setUp(self):
        self.fixture = test_mesh_preparation.MeshPreparationTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        self.assertTrue(f.validate()['coverage_valid'])
        response = f.post('/plan-from-reference')
        self.assertEqual(response.status_code, 200, response.text)
        self.payload = response.json()['payload']
        self.maps = f.capture.fixture.app.state.physical_map_service
        self.mesh = MeshExecutionService(self.maps)
        self.runtime = f.runtime
        self.args = dict(project_id=f.project_id, map_id=self.payload['map_id'])
        self.probes = []
        self.entered, self.release, self.finished = threading.Event(), threading.Event(), threading.Event()
        self.block_point = False
        self.epoch = self.runtime.physical_ownership.session
        original = self.mesh._run
        def run(*args):
            try:
                original(*args)
            finally:
                self.finished.set()
        def probe(point, *, permit, **kwargs):
            self.runtime.physical_ownership.validate(permit)
            self.probes.append(point['index'])
            self.entered.set()
            if self.block_point:
                if not self.release.wait(5):
                    raise RuntimeError('test failed to release fake point')
            self.runtime._machine.update_motion(live_position=(point['x_machine'], point['y_machine'], 31), live_velocity=0)
            return dict(z_measured=30., duration_s=.001)
        def fresh_fake_snapshot():
            # The fake transport has no heartbeat thread. Supply fresh observations
            # at its read boundary, including after a deliberately blocked point.
            self.runtime._machine.update_motion(live_position=(4, 5, 31), live_velocity=0, source='websocket')
            self.runtime._last_websocket_message_at = time.monotonic()
            self.runtime._last_telemetry_at = time.monotonic()
            self.runtime._last_packet_at = time.monotonic()
            return original_snapshot()
        original_snapshot = self.runtime.snapshot
        for mock in [patch.object(self.mesh, '_run', side_effect=run),
                     patch.object(self.runtime, 'snapshot', side_effect=fresh_fake_snapshot),
                     patch.object(self.runtime, 'probe_mesh_point', side_effect=probe),
                     patch.object(self.runtime, 'refresh_observed_state', side_effect=fresh_fake_snapshot)]:
            mock.start(); self.addCleanup(mock.stop)
        self.addCleanup(self.release.set)

    def get(self):
        return self.maps.get_by_id(**self.args)

    def start(self):
        self.finished.clear()
        return self.mesh.start_all(**self.args, runtime=self.runtime)

    def wait(self):
        self.assertTrue(self.finished.wait(10), 'worker/finally did not finish')
        return self.get()

    def test_start_without_arm_blocks(self):
        payload = self.get(); payload['armed'] = False
        self.maps._save(self.args['project_id'], self.args['map_id'], payload)
        with self.assertRaises(ApplicationError): self.start()
        self.assertEqual(self.probes, [])

    def test_pause_during_point_and_resume_never_repeats_measured(self):
        self.block_point = True
        self.start(); self.assertTrue(self.entered.wait(3))
        self.mesh.pause(**self.args)
        self.assertEqual(self.get()['execution']['point_state'], 'MESH_PAUSING')
        self.release.set()
        paused = self.wait()
        self.assertEqual(paused['status'], 'MESH_PAUSED')
        self.assertEqual(len(self.probes), 1)
        self.assertEqual(self.runtime.physical_ownership.session, self.epoch)
        self.finished.clear()
        self.mesh.resume(**self.args, runtime=self.runtime)
        self.assertEqual(self.wait()['status'], 'MESH_COMPLETE')
        self.assertEqual(len(self.probes), len(set(self.probes)))

    def test_pause_between_points(self):
        original = self.mesh._probe_one_point
        def after(*args, **kwargs):
            original(*args, **kwargs)
            self.mesh.pause(**self.args)
        with patch.object(self.mesh, '_probe_one_point', side_effect=after):
            self.start(); self.assertEqual(self.wait()['status'], 'MESH_PAUSED')
        self.assertEqual(len(self.probes), 1)

    def test_resume_changed_session_blocks(self):
        self.mesh.start_next(**self.args, runtime=self.runtime); self.wait()
        self.runtime.current_physical_session_id = lambda: 'different-session'
        with self.assertRaises(ValueError): self.mesh.resume(**self.args, runtime=self.runtime)
        self.assertEqual(len(self.probes), 1)

    def test_cancel_finishes_only_current_point(self):
        self.block_point = True
        self.start(); self.assertTrue(self.entered.wait(3))
        self.mesh.cancel(**self.args, runtime=self.runtime)
        self.release.set()
        payload = self.wait()
        self.assertEqual(payload['status'], 'CANCELLED')
        self.assertEqual(len(self.probes), 1)
        self.assertEqual(self.runtime.physical_ownership.session, self.epoch)
        self.assertFalse(self.runtime.physical_ownership.snapshot()['active'], self.runtime.physical_ownership.snapshot())

    def test_partial_map_cannot_rearm_with_different_acquisition_token(self):
        self.mesh.start_next(**self.args, runtime=self.runtime); self.wait()
        payload = self.get()
        payload['armed_reference_token'] = {'session': 'another-acquisition'}
        self.maps._save(self.args['project_id'], self.args['map_id'], payload)
        result = self.fixture.post('/arm-readiness')
        self.assertEqual(result.status_code, 200, result.text)
        self.assertIn('ACQUISITION_REFERENCE_CHANGED', [r['code'] for r in result.json()['blocking_reasons']])
        self.assertEqual(self.fixture.post('/plan-from-reference').status_code, 400)
        self.assertEqual(self.get(), payload)

    def test_retry_all_filters_failed_without_pending_or_measured(self):
        payload = self.get()
        pending = [p for p in payload['points'] if p['status'] == 'PENDING']
        targets = pending[:2]
        targets[0]['status'] = 'FAILED'; targets[1]['status'] = 'RETRY_REQUIRED'
        self.maps._save(self.args['project_id'], self.args['map_id'], payload)
        self.mesh.retry_failed(**self.args, runtime=self.runtime)
        payload = self.wait()
        self.assertEqual(self.probes, [p['index'] for p in targets])
        self.assertEqual(payload['execution']['retry_result']['recovered'], 2)
        self.assertEqual(payload['status'], 'MESH_PAUSED')

    def test_retry_individual_filters_exact_point(self):
        payload = self.get()
        pending = [p for p in payload['points'] if p['status'] == 'PENDING']
        for p in pending[:2]: p['status'] = 'FAILED'
        self.maps._save(self.args['project_id'], self.args['map_id'], payload)
        self.mesh.retry_failed(**self.args, runtime=self.runtime, point_index=pending[1]['index'])
        self.wait()
        self.assertEqual(self.probes, [pending[1]['index']])

    def test_rebuild_preserves_valid_reference_zero_margins_and_requires_steps(self):
        token = deepcopy(self.payload['armed_reference_token'])
        self.start(); self.wait()
        rebuilt = self.maps.repeat_measurement(**self.args, runtime=self.runtime)
        self.assertTrue(rebuilt['reference_preserved'])
        self.assertEqual(rebuilt['edge_margins'], self.payload['edge_margins'])
        self.assertFalse(rebuilt['armed'])
        self.assertEqual(next(iter(rebuilt['tool_references'].values()))['physical_reference_token'], token)
        self.assertTrue(self.get()['archived_at'])
        self.assertFalse(any(p['attempts'] for p in rebuilt['points']))
        with self.assertRaises(ApplicationError):
            self.mesh.start_all(project_id=self.args['project_id'], map_id=rebuilt['map_id'], runtime=self.runtime)

    def test_rebuilt_map_rearms_without_additional_reference_probe(self):
        rebuilt = self.maps.repeat_measurement(**self.args, runtime=self.runtime)
        self.assertTrue(self.fixture.validate()['coverage_valid'])
        response = self.fixture.post('/plan-from-reference')
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()['payload']
        self.assertEqual(payload['map_id'], rebuilt['map_id'])
        self.assertEqual(payload['status'], 'MESH_PLANNED')
        self.assertEqual(payload['armed_reference_token'], self.payload['armed_reference_token'])
        self.args['map_id'] = payload['map_id']
        self.start()
        self.assertEqual(self.wait()['status'], 'MESH_COMPLETE')
        self.assertEqual(self.runtime._perform_probe_descent.call_count, 1)

    def test_rebuild_does_not_revive_previous_session_reference(self):
        self.runtime.current_physical_session_id = lambda: 'new-session'
        rebuilt = self.maps.repeat_measurement(**self.args, runtime=self.runtime)
        self.assertFalse(rebuilt['reference_preserved'])
        self.assertFalse(rebuilt['armed'])
        self.assertFalse(rebuilt['tool_references'])

    def test_modified_persisted_geometry_blocks_start(self):
        payload = self.get()
        payload['points'][1]['x_local'] += 1
        self.maps._save(self.args['project_id'], self.args['map_id'], payload)
        with self.assertRaises(ApplicationError): self.start()
        self.assertEqual(self.probes, [])

    def test_resume_during_recovery_blocks(self):
        self.mesh.start_next(**self.args, runtime=self.runtime); self.wait()
        self.runtime.physical_ownership.enter_recovery('test uncertainty')
        with self.assertRaises(ApplicationError): self.mesh.resume(**self.args, runtime=self.runtime)
        self.assertEqual(len(self.probes), 1)

    def test_api_e2e_preview_validate_arm_start_pause_resume_rebuild(self):
        f = self.fixture
        f.capture.fixture.app.state.mesh_execution_service = self.mesh
        path = f"/api/projects/{f.project_id}/physical-maps/{self.payload['map_id']}"
        self.block_point = True
        response = f.client.post(path + '/execute-all')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(self.entered.wait(5))
        response = f.client.post(path + '/pause')
        self.assertEqual(response.status_code, 200, response.text)
        self.release.set()
        self.assertEqual(self.wait()['status'], 'MESH_PAUSED')
        self.finished.clear()
        response = f.client.post(path + '/resume')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.wait()['status'], 'MESH_COMPLETE')
        response = f.client.post(path + '/repeat')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['payload']['reference_preserved'])
        self.assertFalse(response.json()['payload']['armed'])
        self.assertEqual(self.runtime._perform_probe_descent.call_count, 1)
        self.assertEqual(f.capture.client.scripts, [])

    def test_pause_and_worker_progress_are_serialized(self):
        self.block_point = True
        self.start(); self.assertTrue(self.entered.wait(5))
        pause_entered, finish_pause = threading.Event(), threading.Event()
        original = self.maps._set_execution_state
        errors = []
        def intercept(*args, **kwargs):
            if kwargs.get('point_state') == 'MESH_PAUSING':
                pause_entered.set()
                if not finish_pause.wait(5): raise AssertionError('pause barrier timeout')
            return original(*args, **kwargs)
        def pause():
            try: self.mesh.pause(**self.args)
            except Exception as error: errors.append(error)
        def progress():
            try: self.maps.update_execution_state(**self.args, worker_active=True, point_state='POINT_RETRACT')
            except Exception as error: errors.append(error)
        with patch.object(self.maps, '_set_execution_state', side_effect=intercept):
            pausing = threading.Thread(target=pause)
            pausing.start()
            self.assertTrue(pause_entered.wait(5))
            progressing = threading.Thread(target=progress)
            progressing.start()
            finish_pause.set()
            pausing.join(5); progressing.join(5)
        self.assertEqual(errors, [])
        self.assertTrue(self.get()['execution']['pause_requested'])
        self.release.set()
        self.assertEqual(self.wait()['status'], 'MESH_PAUSED')
        self.assertEqual(len(self.probes), 1)
