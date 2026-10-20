"""All fixtures are temporary and all transports are fakes."""
import json
import threading
import unittest
from unittest.mock import Mock, patch

from tests import test_job_service as job_fixture
from tests import test_api as api_fixture
from klipper_cnc_assistant.application.errors import ApplicationError
from klipper_cnc_assistant.machine.physical_ownership import OwnerKind, OwnershipError, PhysicalMachineCoordinator
from klipper_cnc_assistant.storage.job_run_store import JobRunConflict


class AdministrativeResetTest(unittest.TestCase):
    def setUp(self):
        self.fixture = job_fixture.JobServiceTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        self.service, self.runtime = f.job_service, f.runtime
        self.runtime._movement_lock = threading.Lock()
        self.runtime._active_operation = None
        self.context = self.service._context(f.project_id, f.setup_id, 'superior')
        self.run = self.service.prepare_run(project_id=f.project_id, setup_id=f.setup_id, face='superior')
        self.run['state'] = 'SPINDLE_STOP_REQUIRED'
        self.service._save_run(self.context, self.run)
        self.path = self.service._run_file(self.context)

    def reset(self, **kwargs):
        return self.service.reset_runs_for_preparation(project_id=self.fixture.project_id,
                                                       setup_id=self.fixture.setup_id, **kwargs)

    def test_free_reset_preserves_history(self):
        result = self.reset()
        self.assertEqual(result['current_runs_cleared'], 1)
        self.assertFalse(self.path.exists())
        self.assertTrue(list(self.path.parent.joinpath('history').glob('*.json')))
        self.assertFalse(self.service.administrative_pending_path.exists())

    def test_partial_failure_preserves_current_and_blocks_reconciliation(self):
        before = self.path.read_bytes()
        def fail(guard):
            raise OSError('synthetic dependency failure')
        with self.assertRaises(OSError):
            self.reset(reset_dependencies=fail)
        self.assertEqual(before, self.path.read_bytes())
        self.assertTrue(self.service.administrative_pending_path.exists())
        coordinator = self.runtime.physical_ownership
        with self.assertRaises(OwnershipError):
            coordinator.reconcile_quiescent(expected_session=coordinator.snapshot()['session'], quiescent=True)
        with self.assertRaises(ApplicationError):
            self.service.assert_administration_available()

    def test_reservation_excludes_new_producer(self):
        def callback(guard):
            with self.assertRaises(OwnershipError):
                self.runtime.physical_ownership.acquire(OwnerKind.JOB_EXECUTION, 'racer')
            with self.assertRaises(ApplicationError):
                self.service.assert_administration_available()
        self.reset(reset_dependencies=callback)
        self.assertEqual(self.fixture.adapter.started, [])

    def test_owner_race_before_reservation_preserves_current(self):
        original = self.service._assert_preparation_reset_safe
        before = self.path.read_bytes()
        def observe():
            value = original()
            self.runtime.physical_ownership.acquire(OwnerKind.JOB_EXECUTION, 'racer')
            return value
        with patch.object(self.service, '_assert_preparation_reset_safe', observe):
            with self.assertRaises(ApplicationError):
                self.reset()
        self.assertEqual(before, self.path.read_bytes())

    def test_session_change_during_mutation_preserves_current(self):
        before = self.path.read_bytes()
        def callback(guard):
            self.runtime.current_physical_session_id = lambda: 'changed-session'
        with self.assertRaises(ApplicationError):
            self.reset(reset_dependencies=callback)
        self.assertEqual(before, self.path.read_bytes())
        self.assertTrue(self.service.administrative_pending_path.exists())

    def test_lock_unknown_busy_and_error_fail_closed(self):
        for lock in (None, Mock(locked=lambda: None), Mock(locked=Mock(side_effect=RuntimeError('unobservable')))):
            self.runtime._movement_lock = lock
            self.assertFalse(self.service._archive_ownership_clear())
        lock = threading.Lock(); lock.acquire()
        self.runtime._movement_lock = lock
        self.assertFalse(self.service._archive_ownership_clear())
        lock.release()
        self.assertTrue(self.service._archive_ownership_clear())

    def test_incomplete_remote_or_http_failure_preserves_current(self):
        valid = self.fixture.adapter.print_status()
        cases = [{'homed_axes_observed': ''}, {'is_active_observed': None},
                 {'live_velocity': float('nan')}, {'state': 'unknown'},
                 {'connected': False}]
        before = self.path.read_bytes()
        for changes in cases:
            with patch.object(self.fixture.adapter, 'print_status', return_value=dict(valid, **changes)):
                with self.assertRaises(ApplicationError):
                    self.reset()
            self.assertEqual(before, self.path.read_bytes())
        with patch.object(self.fixture.adapter, 'print_status', side_effect=OSError('HTTP failed')):
            with self.assertRaises(ApplicationError):
                self.reset()
        self.assertEqual(before, self.path.read_bytes())

    def test_cancellation_cas_during_reset_never_removes_current(self):
        def cancel(guard):
            self.service.run_store.cancel(self.path, self.run['run_id'])
        with self.assertRaises(JobRunConflict):
            self.reset(reset_dependencies=cancel)
        current = self.service.run_store.load(self.path)
        self.assertEqual(current['state'], 'JOB_CANCELLED')
        self.assertTrue(self.service.administrative_pending_path.exists())
        self.assertEqual(self.fixture.adapter.cancel_calls, 0)

    def test_multiple_faces_archived_only_after_dependencies(self):
        other = self.path.parent.parent / 'inferior' / 'current_run.json'
        other.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(self.run, run_id='second-face')
        other.write_text(json.dumps(payload))
        def callback(guard):
            self.assertTrue(self.path.exists())
            self.assertTrue(other.exists())
        result = self.reset(reset_dependencies=callback)
        self.assertEqual(result['current_runs_cleared'], 2)
        self.assertFalse(other.exists())
        self.assertEqual(len(list(self.path.parent.parent.glob('*/history/*.json'))), 2)

    def test_partial_removal_failure_keeps_history_and_pending_journal(self):
        other = self.path.parent.parent / 'inferior' / 'current_run.json'
        other.parent.mkdir(parents=True, exist_ok=True)
        other.write_text(json.dumps(dict(self.run, run_id='second-face')))
        remove = self.service.run_store.remove
        calls = []
        def fail_second(path, run):
            calls.append(path)
            if len(calls) == 2:
                raise OSError('synthetic second removal failure')
            return remove(path, run)
        with patch.object(self.service.run_store, 'remove', fail_second):
            with self.assertRaises(OSError):
                self.reset()
        self.assertEqual(len(list(self.path.parent.parent.glob('*/history/*.json'))), 2)
        self.assertEqual(sum(p.exists() for p in (other, self.path)), 1)
        self.assertTrue(self.service.administrative_pending_path.exists())
        self.assertTrue(self.runtime.physical_ownership.snapshot()['recovery_pending'])

    def test_unknown_ownership_fails_closed(self):
        with patch.object(self.runtime.physical_ownership, 'snapshot', return_value={}):
            self.assertFalse(self.service._archive_ownership_clear())
        with patch.object(self.runtime.physical_ownership, 'snapshot', side_effect=RuntimeError('unknown')):
            self.assertFalse(self.service._archive_ownership_clear())

    def test_pending_journal_survives_service_restart(self):
        def fail(guard):
            raise OSError('synthetic failure')
        with self.assertRaises(OSError):
            self.reset(reset_dependencies=fail)
        self.runtime.physical_ownership = PhysicalMachineCoordinator()
        service = job_fixture.JobService(self.service.repository, self.service.physical_map_service,
            self.service.reference_service, self.service.compensated_gcode_service,
            self.runtime, adapter_factory=self.service.adapter_factory)
        coordinator = self.runtime.physical_ownership
        with self.assertRaises(OwnershipError):
            coordinator.reconcile_quiescent(expected_session=coordinator.session, quiescent=True)
        with self.assertRaises(ApplicationError):
            service.assert_administration_available()
        self.assertTrue(self.path.exists())

    def test_expired_remote_observation_never_mutates(self):
        before = self.path.read_bytes()
        with patch('klipper_cnc_assistant.execution.administrative_guard.time.monotonic', side_effect=[100., 110.]):
            with self.assertRaises(ApplicationError):
                self.reset()
        self.assertEqual(before, self.path.read_bytes())
        self.assertFalse(self.service.administrative_pending_path.exists())

    def test_inflight_execution_write_blocks_administration(self):
        coordinator = self.runtime.physical_ownership
        token = coordinator.begin_job_update()
        before = self.path.read_bytes()
        try:
            with self.assertRaises(ApplicationError):
                self.reset()
        finally:
            coordinator.end_job_update(token)
        self.assertEqual(before, self.path.read_bytes())

    def test_administration_blocks_prepare_before_any_write(self):
        before = self.path.read_bytes()
        with self.service.administrative_mutation('test', allow_started_runs=True):
            with self.assertRaises(ApplicationError):
                self.service.prepare_run(project_id=self.fixture.project_id,
                    setup_id=self.fixture.setup_id, face='superior')
        self.assertEqual(before, self.path.read_bytes())

    def test_retained_owner_and_recovery_preserve_run(self):
        permit = self.service._job_permit(self.context)
        for recovery in (False, True):
            if recovery:
                self.runtime.physical_ownership.request_cancel(permit)
            before = self.path.read_bytes()
            owner = self.runtime.physical_ownership.snapshot()
            with self.assertRaises(ApplicationError):
                self.reset()
            self.assertEqual(before, self.path.read_bytes())
            self.assertEqual(owner, self.runtime.physical_ownership.snapshot())
            self.assertEqual(self.fixture.adapter.started, [])
            self.assertEqual(self.fixture.adapter.cancel_calls, 0)

    def test_missing_movement_lock_is_not_free(self):
        del self.runtime._movement_lock
        self.assertFalse(self.service._archive_ownership_clear())

    def test_shutdown_or_unknown_remote_preserves_run(self):
        for state in ('shutdown', None):
            self.fixture.adapter.print_status = lambda: {
                'connected': True, 'klipper_state': state, 'state': 'complete',
                'is_active': False, 'live_velocity': 0.0}
            before = self.path.read_bytes()
            with self.assertRaises(ApplicationError):
                self.reset()
            self.assertEqual(before, self.path.read_bytes())


class AdministrativeRoutesTest(unittest.TestCase):
    def test_reset_reference_and_map_reject_retained_job(self):
        f = api_fixture.ApiTest(); f.setUp()
        self.addCleanup(f.doCleanups)
        project = f._create_project()
        setup = f.client.get(f'/api/projects/{project}').json()['montajes'][0]['id']
        operation = f._create_operation(project)
        f._upload_and_analyze_operation(project, operation)
        f.app.state.physical_map_service.capture_reference_and_plan(
            project_id=project, operation_id=operation,
            machine_origin_x=10.0, machine_origin_y=10.0, reference_z=0.1,
            machine_position={'x_mm': 10.0, 'y_mm': 10.0, 'z_mm': 0.1},
            homed_axes='xyz', machine_label='fake', session_id='fake-session',
            config=api_fixture.PhysicalMeshConfig(rows=3, columns=3))
        repository = f.app.state.project_service.repository
        before = repository.project_dir(project).joinpath('project.json').read_bytes()
        files_before = {p: p.read_bytes() for p in repository.project_dir(project).rglob('*') if p.is_file()}
        runtime = f.app.state.machine_runtime
        runtime.physical_ownership.acquire(OwnerKind.JOB_EXECUTION, 'retained-test-job')
        for action in ('reset-reference', 'reset-map', 'reset-preparation'):
            response = f.client.post(f'/api/projects/{project}/setups/{setup}/{action}', json={})
            self.assertEqual(response.status_code, 400, response.text)
            self.assertEqual(before, repository.project_dir(project).joinpath('project.json').read_bytes())
            self.assertEqual(files_before, {p: p.read_bytes() for p in repository.project_dir(project).rglob('*') if p.is_file()})
