import threading
import unittest
from unittest.mock import Mock, patch

from tests import test_job_service as fixtures
from klipper_cnc_assistant.execution.job_service import MoonrakerJobAdapter
from klipper_cnc_assistant.storage.job_run_store import JobRunConflict


class JobCancelIdentityTest(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.JobServiceTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.service = fixture.job_service
        self.adapter = fixture.adapter
        self.args = dict(project_id=fixture.project_id, setup_id=fixture.setup_id, face='superior')
        self.context = self.service._context(**self.args)
        self.run = self.service.prepare_run(**self.args)
        self.path = self.service._run_file(self.context)

    def load(self):
        return self.service._load_run(self.context)

    def paused(self):
        self.run['state'] = 'OPERATION_PAUSED'
        operation = self.run['operations'][0]
        operation.update(remote_file='expected/A.gcode', execution_status='PAUSED')
        self.run['current_operation_id'] = operation['operation_id']
        self.service._save_run(self.context, self.run)
        self.adapter.state = 'paused'
        self.adapter.current_filename = 'expected/A.gcode'

    def blocked_execution(self, *, after_send):
        entered, release = threading.Event(), threading.Event()
        errors = []
        original = self.adapter.start_file
        def blocked(remote_path, *, before_send):
            if after_send:
                original(remote_path, before_send=before_send)
            entered.set()
            if not release.wait(5):
                raise AssertionError('Test barrier timed out')
            if not after_send:
                original(remote_path, before_send=before_send)
        self.adapter.start_file = blocked
        def worker():
            try:
                self.service._execute_next_operation(self.context, self.run)
            except JobRunConflict:
                pass
            except BaseException as error:
                errors.append(error)
        thread = threading.Thread(target=worker)
        thread.start()
        try:
            self.assertTrue(entered.wait(5))
            cancelled = self.service.run_action(**self.args, action='cancel')
            self.assertEqual(cancelled['state'], 'JOB_CANCELLED')
        finally:
            release.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])

    def test_A_cancel_before_start_prevents_remote_start(self):
        self.blocked_execution(after_send=False)
        self.assertEqual(self.adapter.started, [])
        self.assertEqual(self.load()['state'], 'JOB_CANCELLED')
        self.assertNotIn('start_attempt', self.load())

    def test_B_eta_loaded_before_cancel_cannot_change_cancelled_domain(self):
        def stale_eta(run, *_args):
            self.service.run_action(**self.args, action='cancel')
            run['eta_ratio_ema'] = 4
            return {'available': True}
        with patch.object(self.service, '_build_eta_snapshot', side_effect=stale_eta):
            live = self.service.live_execution(**self.args)
        self.assertEqual(self.load()['state'], 'JOB_CANCELLED')
        self.assertNotIn('eta_ratio_ema', self.load())
        self.assertEqual(live['run']['status'], 'JOB_CANCELLED')

    def test_C_old_callback_after_cancel_is_rejected(self):
        self.service.run_action(**self.args, action='cancel')
        before = self.path.read_bytes()
        self.run['state'] = 'OPERATION_RUNNING'
        with self.assertRaises(JobRunConflict):
            self.service._save_run(self.context, self.run)
        self.assertEqual(self.path.read_bytes(), before)

    def test_D_old_callback_cannot_modify_new_run(self):
        self.service.run_action(**self.args, action='cancel')
        newer = self.service.prepare_run(**self.args)
        self.assertNotEqual(newer['run_id'], self.run['run_id'])
        before = self.path.read_bytes()
        with self.assertRaises(JobRunConflict):
            self.service._save_run(self.context, self.run)
        self.assertEqual(self.path.read_bytes(), before)

    def test_E_paused_other_file_cannot_resume(self):
        self.paused()
        self.adapter.current_filename = 'other/B.gcode'
        result = self.service.run_action(**self.args, action='resume')
        self.assertEqual(self.adapter.resume_calls, 0)
        self.assertEqual(result['state'], 'RECOVERY_REQUIRED')

    def test_F_identity_query_failure_sends_no_remote_action(self):
        self.paused()
        with patch.object(self.adapter, 'print_status', side_effect=RuntimeError('offline')):
            result = self.service.run_action(**self.args, action='resume')
        self.assertEqual(result['state'], 'RECOVERY_REQUIRED')
        self.assertEqual(self.adapter.resume_calls + self.adapter.pause_calls + self.adapter.cancel_calls, 0)

    def test_identity_query_failure_blocks_pause_cancel_and_start(self):
        self.paused()
        with patch.object(self.adapter, 'print_status', side_effect=RuntimeError('offline')):
            self.service.run_action(**self.args, action='pause')
            self.service.run_action(**self.args, action='cancel')
        self.assertEqual(self.adapter.pause_calls + self.adapter.cancel_calls, 0)
        newer = self.service.prepare_run(**self.args)
        # A new isolated owner simulates an explicit, reconciled next session.
        from klipper_cnc_assistant.machine.physical_ownership import PhysicalMachineCoordinator
        self.fixture.runtime.physical_ownership = PhysicalMachineCoordinator()
        self.service._physical_leases.clear()
        with patch.object(self.adapter, 'print_status', side_effect=RuntimeError('offline')):
            with self.assertRaises(Exception):
                self.service._execute_next_operation(self.context, newer)
        self.assertEqual(self.adapter.started, [])
        self.assertEqual(self.adapter.uploads, [])

    def test_G_correct_paused_file_can_resume(self):
        self.paused()
        with patch.object(self.service, '_start_worker'):
            result = self.service.run_action(**self.args, action='resume')
        self.assertEqual(self.adapter.resume_calls, 1)
        self.assertEqual(result['state'], 'OPERATION_RUNNING')

    def test_legacy_job_paused_checks_identity_before_resume(self):
        self.paused()
        self.run['state'] = 'JOB_PAUSED'
        self.service._save_run(self.context, self.run)
        self.adapter.current_filename = 'other/B.gcode'
        result = self.service.run_action(**self.args, action='resume')
        self.assertEqual(self.adapter.resume_calls, 0)
        self.assertEqual(result['state'], 'RECOVERY_REQUIRED')

    def test_H_cancel_after_start_records_dispatch_and_reconciliation(self):
        self.blocked_execution(after_send=True)
        self.assertEqual(len(self.adapter.started), 1)
        current = self.load()
        self.assertEqual(current['state'], 'JOB_CANCELLED')
        self.assertTrue(current['start_attempt']['may_have_been_sent'])
        self.assertEqual(current['start_attempt']['status'], 'sent')
        self.assertEqual(current['recovery_state'], 'CANCEL_RECONCILIATION_REQUIRED')

    def test_product_adapter_upload_never_starts_print(self):
        runtime = Mock()
        adapter = MoonrakerJobAdapter(runtime)
        local = self.path.parent / 'test.gcode'
        local.write_text('G90\n')
        adapter.upload_file(local_path=local, **self.args)
        self.assertIs(runtime._client.upload_file.call_args.kwargs['print_file'], False)
        runtime._client.start_print.assert_not_called()

    def test_cancel_and_pause_reject_other_file(self):
        self.paused()
        self.adapter.current_filename = 'other/B.gcode'
        self.service.run_action(**self.args, action='pause')
        self.service.run_action(**self.args, action='cancel')
        self.assertEqual(self.adapter.pause_calls + self.adapter.cancel_calls, 0)
        self.assertEqual(self.load()['state'], 'JOB_CANCELLED')

    def test_watcher_never_accepts_paused_other_file(self):
        self.paused()
        self.run['state'] = 'OPERATION_RUNNING'
        self.service._save_run(self.context, self.run)
        self.adapter.current_filename = 'other/B.gcode'
        self.service._watch_operation(self.context, self.run)
        self.assertEqual(self.load()['state'], 'RECOVERY_REQUIRED')

    def test_watcher_rejects_wrong_file_running_complete_cancelled_and_error(self):
        self.paused()
        for state in ('printing', 'complete', 'cancelled', 'error'):
            run = self.load()
            run['state'] = 'OPERATION_RUNNING'
            self.service._save_run(self.context, run)
            self.adapter.status_sequence = [{'state': state, 'filename': 'other/B.gcode'}]
            self.service._watch_operation(self.context, run)
            self.assertEqual(self.load()['state'], 'RECOVERY_REQUIRED')

    def test_cancel_during_preflight_cannot_create_replacement(self):
        original = self.service._build_run_checks
        def checks(*args):
            self.service.run_action(**self.args, action='cancel')
            return original(*args)
        with patch.object(self.service, '_build_run_checks', side_effect=checks):
            with self.assertRaises(JobRunConflict):
                self.service.prepare_run(**self.args)
        self.assertEqual(self.load()['run_id'], self.run['run_id'])
        self.assertEqual(self.load()['state'], 'JOB_CANCELLED')

    def test_upload_response_for_different_file_never_starts(self):
        with patch.object(self.adapter, 'upload_file', return_value={
                'item': {'path': 'other/B.gcode', 'root': 'gcodes'}, 'print_started': False}):
            with self.assertRaises(Exception):
                self.service._execute_next_operation(self.context, self.run)
        self.assertEqual(self.adapter.started, [])

    def test_product_adapter_revalidates_after_last_callback(self):
        from types import SimpleNamespace
        from klipper_cnc_assistant.machine.motion_authorization import MotionAuthorizer
        from klipper_cnc_assistant.machine.physical_ownership import PhysicalMachineCoordinator, OwnerKind, OwnershipError
        from tests.test_motion_authorization import observed_machine
        coordinator = PhysicalMachineCoordinator()
        permit = coordinator.acquire(OwnerKind.JOB_EXECUTION, 'test')
        machine = observed_machine(coordinator)
        client = Mock()
        runtime = SimpleNamespace(_client=client, require_physical_access=lambda: None,
            _refresh_machine=lambda: None,
            _motion_authorizer=lambda: MotionAuthorizer(coordinator, machine))
        adapter = MoonrakerJobAdapter(runtime)
        adapter.permit = permit
        with self.assertRaises(OwnershipError):
            adapter.start_file('A.gcode', before_send=lambda: coordinator.request_cancel(permit))
        client.start_print.assert_not_called()

    def test_api_reports_stale_domain_revision_as_conflict(self):
        from tests.test_api import ApiTest
        fixture = ApiTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        with patch.object(fixture.app.state.job_service, 'run_action', side_effect=JobRunConflict('stale')):
            response = fixture.client.post('/api/projects/test/job-run/action',
                json={'setup_id': 'setup', 'face': 'superior', 'action': 'resume'})
        self.assertEqual(response.status_code, 409)

    def test_recovery_cannot_adopt_different_operation_file(self):
        self.paused()
        self.run['operations'][1]['remote_file'] = 'next/B.gcode'
        self.service._save_run(self.context, self.run)
        self.adapter.state = 'printing'
        self.adapter.current_filename = 'next/B.gcode'
        self.assertIsNone(self.service._recover_active_print_if_possible(self.context, self.run))
        self.assertEqual(self.load()['state'], 'RECOVERY_REQUIRED')
        self.assertEqual(self.load()['current_operation_index'], 0)
