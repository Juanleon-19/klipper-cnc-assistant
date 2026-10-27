"""Regression for a completed run and explicit empty Moonraker standby."""
import json
import unittest
from unittest.mock import patch

from tests import test_administrative_exclusion as fixtures
from klipper_cnc_assistant.application.errors import ApplicationError
from klipper_cnc_assistant.machine.physical_ownership import OwnerKind


class AdministrativeIdentityTest(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.AdministrativeResetTest()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.s = self.f.service
        self.run = self.f.run
        op = self.run['operations'][0]
        op.update(remote_file='synthetic/completed.gcode', execution_status='COMPLETED',
                  observed_printing=True, finished_at='2026-01-01T00:00:00Z',
                  moonraker_filename='synthetic/completed.gcode')
        self.run['start_attempt'] = dict(status='sent', may_have_been_sent=True,
            operation_id=op['operation_id'], remote_file=op['remote_file'])
        self.f.path.write_text(json.dumps(self.run))  # Temporary fixture only; includes journal evidence.
        self.remote = dict(self.f.fixture.adapter.print_status(), state='standby', filename='')
        self.patch = patch.object(self.f.fixture.adapter, 'print_status', side_effect=lambda: dict(self.remote))
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def rejected(self, message=None):
        before = self.f.path.read_bytes()
        with self.assertRaises(ApplicationError) as error:
            self.f.reset()
        if message:
            self.assertIn(message, str(error.exception))
        self.assertEqual(before, self.f.path.read_bytes())
        self.assertFalse(self.s.administrative_pending_path.exists())
        self.assertEqual(self.f.fixture.adapter.started, [])
        self.assertEqual(self.f.fixture.adapter.cancel_calls, 0)

    def test_completed_empty_standby_archives_without_faking_identity(self):
        before = json.loads(self.f.path.read_text())
        result = self.f.reset()
        self.assertEqual(result['current_runs_cleared'], 1)
        history = list(self.f.path.parent.joinpath('history').glob('*.json'))
        archived = json.loads(history[0].read_text())
        self.assertEqual(archived['operations'][0]['remote_file'], before['operations'][0]['remote_file'])
        self.assertEqual(archived['operations'][0]['execution_status'], 'COMPLETED')
        self.assertEqual(archived['start_attempt'], before['start_attempt'])
        self.assertEqual(self.f.fixture.adapter.started, [])
        self.assertEqual(self.f.fixture.adapter.cancel_calls, 0)

    def test_different_nonempty_file_blocks(self):
        self.remote['filename'] = 'synthetic/other.gcode'
        self.rejected('archivo distinto')

    def test_missing_or_invalid_filename_is_unverifiable(self):
        for value in [None, 5, ' ', '../other']:
            with self.subTest(value=value):
                self.remote['filename'] = value
                self.rejected('no verificable')

    def test_empty_requires_standby(self):
        self.remote['state'] = 'complete'
        self.rejected('No hay evidencia suficiente')

    def test_empty_requires_completed_local_evidence(self):
        op = self.run['operations'][0]
        for key, bad in [('observed_printing', False), ('finished_at', None),
                         ('execution_status', 'PENDING'), ('moonraker_filename', 'other')]:
            original = op[key]
            op[key] = bad
            self.f.path.write_text(json.dumps(self.run))  # Temporary fixture only; includes journal evidence.
            self.rejected('No hay evidencia suficiente')
            op[key] = original

    def test_ambiguous_attempt_blocks_even_with_matching_file(self):
        self.remote['filename'] = 'synthetic/completed.gcode'
        for state in ['sending', 'dispatching', 'uncertain']:
            self.run['start_attempt']['status'] = state
            self.f.path.write_text(json.dumps(self.run))  # Temporary fixture only; includes journal evidence.
            self.rejected('ambiguo')

    def test_attempt_binding_required(self):
        self.run['start_attempt']['operation_id'] = 'different'
        self.f.path.write_text(json.dumps(self.run))  # Temporary fixture only; includes journal evidence.
        self.rejected('No hay evidencia suficiente')

    def test_unknown_remote_shutdown_active_and_no_homing_block(self):
        valid = dict(self.remote)
        for changes in [{'connected': False}, {'klipper_state': 'shutdown'},
                        {'is_active_observed': None}, {'is_active_observed': True},
                        {'live_velocity': 1.0}]:
            self.remote = dict(valid, **changes)
            self.rejected()

    def test_retained_owner_and_recovery_block(self):
        coordinator = self.f.runtime.physical_ownership
        coordinator.acquire(OwnerKind.JOB_EXECUTION, 'synthetic-owner')
        self.rejected()
        coordinator.enter_recovery('synthetic uncertainty')
        before = coordinator.snapshot()
        self.rejected()
        self.assertEqual(before, coordinator.snapshot())

    def test_matching_filename_remains_supported(self):
        self.remote['filename'] = 'synthetic/completed.gcode'
        self.assertEqual(self.f.reset()['current_runs_cleared'], 1)

    def test_archive_preserves_dependencies_and_allows_explicit_new_preparation(self):
        self.run['updated_at'] = '2020-01-01T00:00:00+00:00'
        self.f.path.write_text(json.dumps(self.run))
        project_dir = self.f.fixture.repository.project_dir(self.f.fixture.project_id)
        protected = {p: p.read_bytes() for p in project_dir.rglob('*')
                     if p.is_file() and 'reports' not in p.relative_to(project_dir).parts}
        result = self.s.archive_stale_run(project_id=self.f.fixture.project_id,
            setup_id=self.f.fixture.setup_id, face='superior')
        archived = json.loads((project_dir / result['archive_path']).read_text())
        self.assertEqual(archived['operations'], self.run['operations'])
        self.assertEqual(archived['start_attempt'], self.run['start_attempt'])
        for path, content in protected.items():
            self.assertEqual(path.read_bytes(), content)
        new = self.s.prepare_run(project_id=self.f.fixture.project_id,
            setup_id=self.f.fixture.setup_id, face='superior')
        self.assertNotEqual(new['run_id'], self.run['run_id'])
        self.assertEqual(self.f.fixture.adapter.started, [])
        self.assertEqual(self.f.fixture.adapter.probe_calls, 0)

    def test_cancelled_run_with_completed_print_evidence_can_be_archived(self):
        self.run['state'] = 'JOB_CANCELLED'
        self.run['recovery_state'] = 'CANCEL_RECONCILIATION_REQUIRED'
        self.f.path.write_text(json.dumps(self.run))
        result = self.f.reset()
        self.assertEqual(result['current_runs_cleared'], 1)
        history = json.loads(next((self.f.path.parent / 'history').glob('*.json')).read_text())
        self.assertEqual(history['state'], 'JOB_CANCELLED')
        self.assertEqual(history['operations'][0]['execution_status'], 'COMPLETED')

    def test_cancelled_run_without_confirmed_completion_stays_blocked(self):
        self.run['state'] = 'JOB_CANCELLED'
        self.run['operations'][0]['observed_printing'] = False
        self.f.path.write_text(json.dumps(self.run))
        self.rejected('No hay evidencia suficiente')

    def test_cancelled_archive_then_new_attempt_preserves_map(self):
        self.run['state'] = 'JOB_CANCELLED'
        self.f.path.write_text(json.dumps(self.run))
        project_dir = self.f.fixture.repository.project_dir(self.f.fixture.project_id)
        protected = {p: p.read_bytes() for p in project_dir.rglob('*')
                     if p.is_file() and 'reports' not in p.relative_to(project_dir).parts}
        result = self.s.archive_stale_run(project_id=self.f.fixture.project_id,
            setup_id=self.f.fixture.setup_id, face='superior')
        self.assertTrue(result['can_start_new_run'])
        for path, content in protected.items():
            self.assertEqual(path.read_bytes(), content)
        new = self.s.prepare_run(project_id=self.f.fixture.project_id,
            setup_id=self.f.fixture.setup_id, face='superior')
        self.assertNotEqual(new['run_id'], self.run['run_id'])
        self.assertTrue(all(op['execution_status'] == 'PENDING' for op in new['operations']))
        self.assertFalse(new.get('start_attempt'))
        self.assertEqual(self.f.fixture.adapter.started, [])

    def test_logical_reset_without_homing_still_requires_observed_idle(self):
        self.remote['homed_axes_observed'] = ''
        self.assertEqual(self.f.reset()['current_runs_cleared'], 1)
        self.assertEqual(self.f.fixture.adapter.started, [])

    def test_cancelled_archive_rejects_uncertain_attempt_and_retained_owner(self):
        self.run['state'] = 'JOB_CANCELLED'
        self.run['start_attempt']['status'] = 'uncertain'
        self.f.path.write_text(json.dumps(self.run))
        before = self.f.path.read_bytes()
        with self.assertRaises(ApplicationError):
            self.s.archive_stale_run(project_id=self.f.fixture.project_id,
                setup_id=self.f.fixture.setup_id, face='superior')
        self.assertEqual(before, self.f.path.read_bytes())
        self.f.runtime.physical_ownership.acquire(OwnerKind.JOB_EXECUTION, 'retained')
        with self.assertRaises(ApplicationError):
            self.s.archive_stale_run(project_id=self.f.fixture.project_id,
                setup_id=self.f.fixture.setup_id, face='superior')
        self.assertEqual(before, self.f.path.read_bytes())

    def test_history_retry_does_not_duplicate_same_revision(self):
        context = self.f.context
        first = self.s._write_archived_run(context, self.run, suffix='preparation-reset')
        before = first.read_bytes()
        second = self.s._write_archived_run(context, dict(self.run, updated_at='different'), suffix='preparation-reset')
        self.assertEqual(first, second)
        self.assertEqual(before, second.read_bytes())
        self.assertEqual(len(list(first.parent.glob('*.json'))), 1)
