import ast
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from tests import test_physical_integration as physical_fixtures
from tests import test_api as api_fixtures
from klipper_cnc_assistant.application import ApplicationError, PhysicalMapService
from klipper_cnc_assistant.application.physical_map_service import PhysicalMeshConfig
from klipper_cnc_assistant.execution import MeshExecutionService
from klipper_cnc_assistant.execution.mesh_execution_service import ProbeThreadOwnership
from klipper_cnc_assistant.machine.physical_ownership import OwnerKind, OwnershipError


class UnifiedMeshProbeTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.repository, _, self.project, self.operation = physical_fixtures.PhysicalIntegrationTest()._physical_project(directory.name)
        self.maps = PhysicalMapService(self.repository)
        self.plan = self.maps.capture_reference_and_plan(
            project_id=self.project.id, operation_id=self.operation.id,
            machine_origin_x=5.0, machine_origin_y=6.0, reference_z=1.5,
            machine_position={'x_mm': 5.0, 'y_mm': 6.0, 'z_mm': 1.5},
            homed_axes='xyz', machine_label='test', session_id='session',
            config=PhysicalMeshConfig(grid_mode='manual', rows=2, columns=2),
        )
        self.worker = MeshExecutionService(self.maps)
        self.runtime = physical_fixtures.StatefulMeshRuntime()
        self.args = dict(project_id=self.project.id, map_id=self.plan['map_id'], runtime=self.runtime)
        self.addCleanup(lambda: self.worker.wait_until_idle(timeout_s=3))

    def payload(self):
        return self.maps.get_by_id(self.project.id, self.plan['map_id'])

    def test_A_B_archived_map_rejected_identically_without_probe(self):
        payload = self.payload()
        payload['archived_at'] = '2026-09-12T00:00:00Z'
        self.repository.save_height_map_payload(self.project.id, self.plan['map_id'], payload)
        messages = []
        for start in (self.worker.start_next, self.worker.start_all):
            with self.assertRaises(ApplicationError) as error:
                start(**self.args)
            messages.append(str(error.exception))
        self.assertEqual(messages[0], messages[1])
        self.assertIn('archivada', messages[0])
        self.assertEqual(self.runtime.calls, [])

    def test_C_next_makes_one_attempt_and_pauses_remaining_points(self):
        initial_attempts = sum(p['attempts'] for p in self.payload()['points'])
        self.worker.start_next(**self.args)
        self.assertTrue(self.worker.wait_until_idle(timeout_s=3))
        payload = self.payload()
        self.assertEqual(len(self.runtime.calls), 1)
        self.assertEqual(sum(p['attempts'] for p in payload['points']), initial_attempts + 1)
        self.assertEqual(payload['status'], 'MESH_PAUSED')
        self.assertFalse(payload['execution']['worker_active'])
        self.assertFalse(payload['execution'].get('last_error'))
        self.assertEqual(self.runtime.physical_ownership.snapshot()['kind'], 'IDLE')

    def test_D_last_point_uses_normal_completion(self):
        pending = [p for p in self.plan['points'] if p['status'] == 'PENDING']
        for point in pending[:-1]:
            self.maps.record_point(project_id=self.project.id, map_id=self.plan['map_id'],
                                   point_index=point['index'], z_measured=1.5)
        self.worker.start_next(**self.args)
        self.assertTrue(self.worker.wait_until_idle(timeout_s=3))
        self.assertEqual(len(self.runtime.calls), 1)
        self.assertEqual(self.payload()['status'], 'MESH_COMPLETE')
        self.assertFalse(self.payload()['execution']['worker_active'])

    def test_E_next_cancel_uses_existing_worker_cancel_and_cleanup(self):
        point = self.maps.next_pending_point(self.project.id, self.plan['map_id'])
        runtime = physical_fixtures.BlockingMeshRuntime(block_point_index=point['index'], fail_on_cancel=True)
        self.args['runtime'] = runtime
        self.worker.start_next(**self.args)
        try:
            self.assertTrue(runtime.entered.wait(2))
            self.assertEqual(runtime.physical_ownership.snapshot()['kind'], 'MESH')
            response = self.worker.cancel(**self.args)
            self.assertEqual(response['execution']['point_state'], 'MESH_CANCELING')
        finally:
            runtime.release.set()
        self.assertTrue(self.worker.wait_until_idle(timeout_s=3))
        self.assertEqual(self.payload()['status'], 'CANCELLED')
        self.assertGreaterEqual(runtime.cancel_calls, 1)
        self.assertEqual(runtime.calls, [])

    def test_F_recovery_blocks_next_and_all_identically(self):
        self.runtime.physical_ownership.enter_recovery('cleanup pending')
        messages = []
        for start in (self.worker.start_next, self.worker.start_all):
            with self.assertRaises((ApplicationError, OwnershipError)) as error:
                start(**self.args)
            messages.append(str(error.exception))
        self.assertEqual(messages[0], messages[1])
        self.assertEqual(self.runtime.calls, [])

    def test_G_incompatible_job_owner_blocks_both(self):
        self.runtime.physical_ownership.acquire(OwnerKind.JOB_EXECUTION, 'job')
        for start in (self.worker.start_next, self.worker.start_all):
            with self.assertRaises((ApplicationError, OwnershipError)):
                start(**self.args)
        self.assertEqual(self.runtime.calls, [])
        self.assertEqual(self.runtime.physical_ownership.snapshot()['owner_id'], 'job')

    def test_F_live_probe_cleanup_blocks_next_and_all_identically(self):
        release = threading.Event()
        thread = threading.Thread(target=release.wait, daemon=True)
        thread.start()
        self.worker._probe_threads[(self.project.id, self.plan['map_id'])] = ProbeThreadOwnership(
            thread=thread, finished=threading.Event(), point_index=1, cleanup_pending=True,
        )
        try:
            messages = []
            for start in (self.worker.start_next, self.worker.start_all):
                with self.assertRaises(ApplicationError) as error:
                    start(**self.args)
                messages.append(str(error.exception))
            self.assertEqual(messages[0], messages[1])
            self.assertEqual(self.runtime.calls, [])
        finally:
            release.set()
            thread.join(2)

    def test_H_endpoint_delegates_to_service_without_runtime_bypass(self):
        fixture = api_fixtures.ApiTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.app.state.machine_runtime.probe_mesh_point = Mock(side_effect=AssertionError('bypass'))
        with patch.object(fixture.app.state.mesh_execution_service, 'start_next',
                          side_effect=ApplicationError('service reached')) as start:
            response = fixture.client.post('/api/projects/test/physical-maps/test-map/execute-next')
        self.assertEqual(response.status_code, 400)
        start.assert_called_once_with(project_id='test', map_id='test-map', runtime=fixture.app.state.machine_runtime)
        fixture.app.state.machine_runtime.probe_mesh_point.assert_not_called()
        source = Path(__file__).parents[1] / 'src/klipper_cnc_assistant/api/routes.py'
        endpoint = next(n for n in ast.walk(ast.parse(source.read_text()))
                        if isinstance(n, ast.FunctionDef) and n.name == 'execute_next_physical_map_point')
        self.assertFalse(any(isinstance(n, ast.Attribute) and n.attr == 'probe_mesh_point' for n in ast.walk(endpoint)))

    def test_failure_is_one_attempt_without_automatic_retry(self):
        initial_attempts = sum(p['attempts'] for p in self.payload()['points'])
        self.runtime.unexpected = True
        self.runtime.probe_mesh_point = Mock(wraps=self.runtime.probe_mesh_point)
        self.worker.start_next(**self.args)
        self.assertTrue(self.worker.wait_until_idle(timeout_s=3))
        self.assertEqual(sum(p['attempts'] for p in self.payload()['points']), initial_attempts + 1)
        self.runtime.probe_mesh_point.assert_called_once()
        self.assertEqual(self.payload()['status'], 'MESH_PAUSED')

    def test_http_request_returns_while_worker_retains_owner(self):
        point = self.maps.next_pending_point(self.project.id, self.plan['map_id'])
        runtime = physical_fixtures.BlockingMeshRuntime(block_point_index=point['index'])
        self.args['runtime'] = runtime
        self.worker.start_next(**self.args)
        try:
            self.assertTrue(runtime.entered.wait(2))
            self.assertEqual(runtime.physical_ownership.snapshot()['kind'], 'MESH')
            self.assertTrue(self.worker.active_execution_snapshot()['active'])
        finally:
            runtime.release.set()
        self.assertTrue(self.worker.wait_until_idle(timeout_s=3))
