"""Cancelled incomplete emissions: temporary storage and fake physical transport only."""
from copy import deepcopy
import json
import unittest
from unittest.mock import patch
from tests import test_administrative_remote_identity as identity_tests
from klipper_cnc_assistant.application.errors import ApplicationError


class CancelledStandbyResetTest(unittest.TestCase):
    def setUp(self):
        self.f = identity_tests.AdministrativeIdentityTest(); self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.s, self.run = self.f.s, self.f.run
        second = deepcopy(self.run['operations'][0])
        second.update(operation_id='second', name='Synthetic milling',
            remote_file='synthetic/second.gcode', moonraker_filename='synthetic/second.gcode',
            execution_status='RUNNING', observed_printing=True, finished_at=None,
            completed_at=None, progress=.17, moonraker_state='printing')
        self.run.update(state='JOB_CANCELLED', current_operation_index=1,
            operations=[self.run['operations'][0], second],
            cancel_remote_status='identity_or_transport_error_requires_reconciliation',
            recovery_state='CANCEL_RECONCILIATION_REQUIRED',
            start_attempt=dict(id='attempt-second', status='sent', may_have_been_sent=True,
                operation_id='second', remote_file=second['remote_file']))
        self.f.remote["live_position"] = [10.0, 10.0, 20.0]
        self.save()
        self.confirmation = dict(run_id=self.run['run_id'], revision=self.run['revision'],
                                abandon_incomplete=True)

    def save(self): self.f.f.path.write_text(json.dumps(self.run))

    def reset(self, **kwargs):
        return self.f.f.reset(abandoned_run=self.confirmation,
            reset_dependencies=kwargs.pop('reset_dependencies', lambda guard: {'reset': True}), **kwargs)

    def rejected(self):
        before=self.f.f.path.read_bytes()
        with self.assertRaises(ApplicationError): self.reset()
        self.assertEqual(self.f.f.path.read_bytes(),before)
        self.assertFalse(self.s.administrative_pending_path.exists())
        self.assertEqual(self.f.f.fixture.adapter.started,[])
        self.assertEqual(self.f.f.fixture.adapter.cancel_calls,0)

    def test_real_case_preserves_incomplete_evidence_and_resets_together(self):
        def reset_dependencies(guard):
            self.assertTrue(self.f.f.path.exists())
            self.assertTrue(self.s.runtime.physical_ownership.snapshot()['administrative_active'])
            return {'reset':True}
        result=self.reset(reset_dependencies=reset_dependencies)
        self.assertEqual(result['current_runs_cleared'],1)
        archived=json.loads(next((self.f.f.path.parent/'history').glob('*.json')).read_text())
        for key in ['operations','start_attempt','cancel_remote_status','recovery_state']:
            self.assertEqual(archived[key],self.run[key])
        self.assertEqual(archived.get('completed_at'),self.run.get('completed_at'))
        self.assertEqual(archived['administrative_closure']['outcome'],'INCOMPLETE_UNVERIFIED')
        self.assertEqual(archived['administrative_closure']['remote_observation']['filename'],'')
        self.assertEqual(self.f.f.fixture.adapter.started,[])
        self.assertEqual(self.f.f.fixture.adapter.cancel_calls,0)

    def test_confirmation_required_and_bound(self):
        for changes in [dict(abandon_incomplete=False),dict(run_id='other'),dict(revision=-1)]:
            original=dict(self.confirmation);self.confirmation.update(changes)
            self.rejected();self.confirmation=original
        with self.assertRaises(ApplicationError): self.f.f.reset()

    def test_different_active_unknown_remote_rejected(self):
        original=dict(self.f.remote)
        for change in [dict(filename='synthetic/other.gcode'),dict(filename=None),
                       dict(state='printing'),dict(is_active_observed=True),
                       dict(connected=False),dict(live_velocity=None),
                       dict(live_velocity=1),dict(live_velocity=False),dict(klipper_state='shutdown'),
                       dict(is_active=True),dict(file_path='synthetic/other.gcode')]:
            with self.subTest(change=change):
                self.f.remote=dict(original,**change); self.rejected()

    def test_ambiguous_or_mismatched_emission_rejected(self):
        original=deepcopy(self.run)
        for status in ['sending','dispatching','uncertain']:
            self.run=deepcopy(original);self.run['start_attempt']['status']=status
            self.save();self.rejected()
        for key,value in [('operation_id','other'),('remote_file','other.gcode'),('may_have_been_sent',False)]:
            self.run=deepcopy(original);self.run['start_attempt'][key]=value
            self.save();self.rejected()

    def test_non_cancelled_or_unobserved_or_completed_operation_rejected(self):
        original=deepcopy(self.run)
        for changes in [dict(observed_printing=False),dict(finished_at='done'),
                        dict(execution_status='PENDING'),dict(moonraker_filename='other')]:
            self.run=deepcopy(original);self.run['operations'][1].update(changes)
            self.save();self.rejected()
        self.run=deepcopy(original);self.run['state']='RECOVERY_REQUIRED'
        self.save();self.rejected()

    def test_missing_position_or_http_failure_rejected(self):
        self.f.remote['live_position']=None;self.rejected()
        with patch.object(self.f.f.fixture.adapter,'print_status',side_effect=OSError('fake HTTP')):
            self.rejected()

    def test_retained_owner_or_lock_blocks(self):
        from klipper_cnc_assistant.machine.physical_ownership import OwnerKind
        c=self.s.runtime.physical_ownership
        c.acquire(OwnerKind.JOB_EXECUTION,'fake-owner')
        before=c.snapshot();self.rejected();self.assertEqual(c.snapshot(),before)

    def test_changed_session_or_revision_before_history_blocks(self):
        original=self.s._observe_administrative_remote
        def changed_session():
            remote=original()
            self.s.runtime.current_physical_session_id=lambda:'changed-session'
            return remote
        with patch.object(self.s,'_observe_administrative_remote',side_effect=changed_session):
            self.rejected()

    def test_cas_change_preserves_concurrent_run(self):
        from klipper_cnc_assistant.storage.job_run_store import JobRunConflict
        def concurrent(guard):
            current=json.loads(self.f.f.path.read_text()); current['revision']+=1
            self.f.f.path.write_text(json.dumps(current))
        with self.assertRaises(JobRunConflict): self.reset(reset_dependencies=concurrent)
        self.assertTrue(self.f.f.path.exists())
        self.assertTrue(self.s.administrative_pending_path.exists())

    def test_partial_failure_recovery_retry_keeps_single_history(self):
        from klipper_cnc_assistant.execution.administrative_recovery import recover
        before=self.f.f.path.read_bytes()
        def fail(guard): raise OSError('synthetic persistence failure')
        with self.assertRaises(OSError): self.reset(reset_dependencies=fail)
        self.assertEqual(self.f.f.path.read_bytes(),before)
        journal=json.loads(self.s.administrative_pending_path.read_text())
        self.assertEqual(journal['evidence']['abandoned_run'],self.confirmation)
        self.assertEqual(journal['phase'],'FAILED_WITH_VERIFIED_FILES')
        original=self.s.administrative_pending_path.read_bytes()
        recover(self.s,journal['id'])
        c=self.s.runtime.physical_ownership
        # Fake-only explicit fresh-idle reconciliation, never productive ownership.
        c.reconcile_quiescent(expected_session=c.session,quiescent=True)
        self.assertEqual(c.snapshot()['kind'],'IDLE')
        self.reset()
        self.assertEqual(len(list((self.f.f.path.parent/'history').glob('*.json'))),1)
        evidence=self.s.administrative_pending_path.parent/'administrative-transactions'/journal['id']/'original.json'
        self.assertEqual(evidence.read_bytes(),original)
        with self.assertRaises(ApplicationError): self.reset()
        self.assertEqual(len(list((self.f.f.path.parent/'history').glob('*.json'))),1)

    def test_remote_changes_at_commit_blocks_without_removing_current(self):
        before=self.f.f.path.read_bytes()
        def changed(guard): self.f.remote['filename']='synthetic/other.gcode'
        with self.assertRaises(ApplicationError): self.reset(reset_dependencies=changed)
        self.assertEqual(self.f.f.path.read_bytes(),before)
        self.assertTrue(self.s.administrative_pending_path.exists())

    def test_cannot_abandon_via_archive_or_without_dependency_reset(self):
        with self.assertRaises(ApplicationError):
            self.f.f.reset(abandoned_run=self.confirmation)
        with self.assertRaises(ApplicationError):
            self.s.archive_stale_run(project_id=self.f.f.fixture.project_id,
                setup_id=self.f.f.fixture.setup_id,face='superior')
        self.assertTrue(self.f.f.path.exists())


class CancelledStandbyApiTest(unittest.TestCase):
    def test_full_reset_real_services_fake_transport(self):
        from tests import test_functional_journey as journey
        f=journey.FunctionalJourneyTest(); f.setUp(); self.addCleanup(f.doCleanups)
        f.post('/api/machine/connect')
        run=f.job.prepare_run(**f.args)
        run['operations'][0].update(execution_status='COMPLETED',observed_printing=True,
            finished_at='2026-01-01T00:00:00Z',remote_file='synthetic/first.gcode',
            moonraker_filename='synthetic/first.gcode')
        second=run['operations'][1]
        second.update(execution_status='RUNNING',observed_printing=True,
            finished_at=None,remote_file='synthetic/second.gcode',
            moonraker_filename='synthetic/second.gcode')
        run.update(state='JOB_CANCELLED',current_operation_index=1,
            cancel_remote_status='identity_or_transport_error_requires_reconciliation',
            start_attempt=dict(id='attempt-second',status='sent',may_have_been_sent=True,
                operation_id=second['operation_id'],remote_file=second['remote_file']))
        path=f.job._run_file(f.context); path.write_text(json.dumps(run))
        f.transport.filename=''
        url=f'/api/projects/{f.project}/setups/{f.setup}/reset-preparation'
        before=path.read_bytes(); scripts=list(f.transport.scripts)
        self.assertEqual(f.client.post(url,json={}).status_code,400)
        self.assertEqual(path.read_bytes(),before)
        payload={'abandoned_run':dict(run_id=run['run_id'],revision=run['revision'],abandon_incomplete=True)}
        result=f.post(url,payload)
        self.assertEqual(result['execution_reset']['current_runs_cleared'],1)
        self.assertFalse(path.exists())
        self.assertEqual(f.runtime.snapshot()['state'],'DISCONNECTED')
        setup=f.repo.load_project(f.project).get_setup(f.setup)
        self.assertIsNone(setup.preparacion.physical_reference)
        saved=json.loads(next((path.parent/'history').glob('*.json')).read_text())
        self.assertEqual(saved['operations'],run['operations'])
        self.assertEqual(saved['start_attempt'],run['start_attempt'])
        self.assertEqual(f.transport.scripts,scripts)
        self.assertEqual(f.transport.started,[])
        live=f.client.get(f'/api/projects/{f.project}/execution/live',params={'setup_id':f.setup,'face':'superior'})
        self.assertEqual(live.status_code,200)
        self.assertIsNone(live.json()['job_run'])
        self.assertEqual(f.client.post(url,json=payload).status_code,400)
        self.assertEqual(len(list((path.parent/'history').glob('*.json'))),1)
