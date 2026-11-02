import json
import unittest
from unittest.mock import patch

from tests import test_administrative_exclusion as fixtures
from klipper_cnc_assistant.execution.administrative_recovery import recover, inventory, status
from klipper_cnc_assistant.application.errors import ApplicationError
from klipper_cnc_assistant.machine.physical_ownership import OwnershipError, OwnerKind, PhysicalMachineCoordinator


class AdministrativeRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AdministrativeResetTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.service = self.fixture.service
        self.runtime = self.fixture.runtime
        self.path = self.service.administrative_pending_path

    def fail(self, partial=False):
        def mutation(guard):
            if partial:
                (self.path.parent / 'partial-evidence.json').write_text('{}')
            raise OSError('synthetic disk failure')
        with self.assertRaises(OSError):
            self.fixture.reset(reset_dependencies=mutation)
        return json.loads(self.path.read_text())['id']

    def test_partial_failure_is_preserved_and_recovery_does_not_release_physical_state(self):
        identity = self.fail(partial=True)
        before = inventory(self.path.parent); original = self.path.read_bytes()
        result = recover(self.service, identity)
        self.assertEqual(result['outcome'], 'PARTIAL_PRESERVED_RETRY_RESET')
        self.assertEqual(inventory(self.path.parent), before)
        history = self.path.parent / 'administrative-transactions' / identity
        self.assertEqual((history / 'original.json').read_bytes(), original)
        owner = self.runtime.physical_ownership.snapshot()
        self.assertTrue(owner['recovery_pending'])
        self.assertFalse(owner['administrative_incomplete'])
        self.assertEqual(recover(self.service, identity)['status'], 'already_reconciled')
        self.assertEqual(self.fixture.fixture.adapter.started, [])
        self.assertTrue(self.fixture.path.exists())

    def _interrupted_after_current_run_archived(self):
        """Create a real checkpointed archive alongside unrelated job evidence."""
        from klipper_cnc_assistant.execution.administrative_guard import AdministrativeGuard
        other = self.service.repository.projects_dir / 'proj_other' / 'reports' / 'jobs' / 'other-setup' / 'superior' / 'current_run.json'
        other.parent.mkdir(parents=True, exist_ok=True)
        payload = json.loads(self.fixture.path.read_text())
        operation = payload['operations'][0]
        operation.update(remote_file='other/previous-file.gcode',
                         moonraker_filename='other/previous-file.gcode')
        payload.update(project_id='proj_other', setup_id='other-setup',
                       state='JOB_CANCELLED')
        other.write_text(json.dumps(payload))
        original_other = other.read_bytes()

        original_checkpoint = AdministrativeGuard.checkpoint
        def interrupted(guard, phase):
            if phase == 'FAILED_WITH_VERIFIED_FILES':
                # Reproduce the real failure path: the prior durable checkpoint
                # survives when the failure checkpoint cannot be written.
                raise OSError('synthetic unavailable failure checkpoint')
            result = original_checkpoint(guard, phase)
            if phase == 'CURRENT_RUN_ARCHIVED':
                raise OSError('synthetic interruption after archive checkpoint')
            return result
        with patch.object(AdministrativeGuard, 'checkpoint', interrupted):
            with self.assertRaisesRegex(OSError, 'after archive checkpoint'):
                self.fixture.reset()
        journal = json.loads(self.path.read_text())
        self.assertEqual(journal['phase'], 'CURRENT_RUN_ARCHIVED')
        self.assertFalse(self.fixture.path.exists())
        self.assertEqual(other.read_bytes(), original_other)
        self.assertTrue(self.runtime.physical_ownership.snapshot()['administrative_incomplete'])
        return journal, other, original_other

    def _interrupted_after_run_removed_before_checkpoint(self):
        """Actual last-write gap: current_run removed, journal still DEPENDENCIES_RESET."""
        from klipper_cnc_assistant.execution.administrative_guard import AdministrativeGuard
        checkpoint = AdministrativeGuard.checkpoint

        def interrupt(guard, phase):
            if phase in {'CURRENT_RUN_ARCHIVED', 'FAILED_WITH_VERIFIED_FILES'}:
                raise OSError('synthetic crash between run unlink and checkpoint')
            return checkpoint(guard, phase)

        with patch.object(AdministrativeGuard, 'checkpoint', interrupt):
            with self.assertRaisesRegex(OSError, 'between run unlink and checkpoint'):
                self.fixture.reset()
        journal = json.loads(self.path.read_text())
        self.assertEqual(journal['phase'], 'DEPENDENCIES_RESET')
        self.assertEqual(journal['completed_steps'], ['HISTORY_SAVED', 'DEPENDENCIES_RESET'])
        self.assertFalse(self.fixture.path.exists())
        self.assertTrue(list((self.fixture.path.parent / 'history').glob('*__preparation-reset_*.json')))
        self.assertTrue(self.runtime.physical_ownership.snapshot()['administrative_incomplete'])
        return journal

    def test_uncheckpointed_retirement_reconciles_only_verified_archived_run(self):
        journal = self._interrupted_after_run_removed_before_checkpoint()
        from klipper_cnc_assistant.execution.administrative_recovery import digest
        current = inventory(self.path.parent)
        expected = journal['verified_after']
        self.assertEqual(set(expected) - set(current), {
            str(self.fixture.path.relative_to(self.path.parent))})
        self.assertTrue(all(expected[k] == v for k, v in current.items()))
        original = self.path.read_bytes()
        result = recover(self.service, journal['id'])
        self.assertEqual(result['outcome'], 'CURRENT_RUN_RETIRED_WITH_VERIFIED_ARCHIVE')
        self.assertEqual(result['status'], 'reconciled')
        self.assertFalse(self.fixture.path.exists())
        self.assertEqual(inventory(self.path.parent), current)
        self.assertFalse(self.path.exists())
        self.assertEqual((self.path.parent / 'administrative-transactions' /
                          journal['id'] / 'original.json').read_bytes(), original)
        self.assertEqual(digest(original), digest((self.path.parent / 'administrative-transactions' /
                                                   journal['id'] / 'original.json').read_bytes()))
        self.assertTrue(self.runtime.physical_ownership.snapshot()['recovery_pending'])
        self.assertFalse(self.runtime.physical_ownership.snapshot()['administrative_incomplete'])
        self.assertEqual(self.fixture.fixture.adapter.started, [])
        self.assertEqual(self.fixture.fixture.adapter.cancel_calls, 0)
        self.assertEqual(recover(self.service, journal['id'])['status'], 'already_reconciled')

    def test_uncheckpointed_retirement_rejects_unrelated_write(self):
        journal = self._interrupted_after_run_removed_before_checkpoint()
        unexpected = self.path.parent / 'unrelated.json'
        unexpected.write_text('{"unexpected": true}')
        with self.assertRaisesRegex(ApplicationError, 'checkpoint'):
            recover(self.service, journal['id'])
        self.assertTrue(self.path.exists())
        self.assertTrue(self.runtime.physical_ownership.snapshot()['administrative_incomplete'])

    def test_uncheckpointed_retirement_rejects_changed_archival_identity(self):
        journal = self._interrupted_after_run_removed_before_checkpoint()
        # An otherwise identical inventory cannot authorize a different run.
        journal['evidence']['runs'][0]['run_id'] = 'unrelated-run'
        self.path.write_text(json.dumps(journal))
        with self.assertRaisesRegex(ApplicationError, 'checkpoint'):
            recover(self.service, journal['id'])
        self.assertTrue(self.path.exists())

    def test_uncheckpointed_retirement_rejects_unarchived_extra_run(self):
        journal = self._interrupted_after_run_removed_before_checkpoint()
        # No arbitrary file deletion, even another current_run, is accepted.
        other = self.path.parent / 'projects' / 'other' / 'reports' / 'jobs' / 'another' / 'superior' / 'current_run.json'
        other.parent.mkdir(parents=True, exist_ok=True)
        other.write_text('{"run_id": "unrelated"}')
        with self.assertRaisesRegex(ApplicationError, 'checkpoint'):
            recover(self.service, journal['id'])
        self.assertTrue(self.path.exists())

    def _uncheckpointed_zero_run_project_reset(self, *, with_unrelated_run=False):
        from dataclasses import replace
        from klipper_cnc_assistant.domain.models import OperationPreparation
        from klipper_cnc_assistant.execution.administrative_guard import AdministrativeGuard

        # The real reported case: an earlier successful recovery already
        # retired the run; this new reset has zero runs and changed only
        # project.json before reconnecting / losing its dependency checkpoint.
        if with_unrelated_run:
            other = (self.path.parent / 'projects' / 'unrelated-project'
                     / 'reports' / 'jobs' / 'other-setup' / 'superior' / 'current_run.json')
            other.parent.mkdir(parents=True, exist_ok=True)
            payload = json.loads(self.fixture.path.read_text())
            payload['project_id'] = 'unrelated-project'
            payload['setup_id'] = 'other-setup'
            payload['state'] = 'RECOVERY_REQUIRED'
            payload['operations'][0]['remote_file'] = 'other/previous-job.gcode'
            other.write_text(json.dumps(payload), encoding='utf-8')
        self.fixture.path.unlink()
        repo = self.service.repository
        def reset_project(guard):
            project = repo.load_project(self.fixture.fixture.project_id)
            setup = project.get_setup(self.fixture.fixture.setup_id)
            changed = replace(
                setup, placement_revision='placement-2',
                active_reference_id=None, active_map_id=None,
                last_prepared_at=None, preparation_status='sin_iniciar',
                preparacion=OperationPreparation(motivo_invalidacion='Preparación reiniciada.'),
            )
            repo.save_project(project.replace_setup(changed))
        checkpoint = AdministrativeGuard.checkpoint
        def interrupted(guard, phase):
            if phase in {'DEPENDENCIES_RESET', 'FAILED_WITH_VERIFIED_FILES'}:
                raise OSError('simulated connection race after project reset')
            return checkpoint(guard, phase)
        with patch.object(AdministrativeGuard, 'checkpoint', interrupted):
            with self.assertRaisesRegex(OSError, 'connection race'):
                self.fixture.reset(reset_dependencies=reset_project)
        journal = json.loads(self.path.read_text())
        self.assertEqual(journal['phase'], 'HISTORY_SAVED')
        self.assertEqual(journal['evidence']['runs'], [])
        self.assertEqual(journal['before'], journal['verified_after'])
        current = inventory(self.path.parent)
        self.assertEqual(
            {x for x in current if current[x] != journal['verified_after'][x]},
            {f'projects/{self.fixture.fixture.project_id}/project.json'})
        return journal

    def _partial_reset_attestation(self):
        from klipper_cnc_assistant.execution.administrative_recovery import digest
        pending = self.path.read_bytes()
        raw = (self.service.repository.projects_dir /
               self.fixture.fixture.project_id / 'project.json').read_bytes()
        data = json.loads(raw)
        operations = json.dumps(
            data['operaciones'], sort_keys=True, separators=(',', ':'), ensure_ascii=True
        ).encode('utf-8')
        return {
            'journal_sha256': digest(pending),
            'project_sha256': digest(raw),
            'operations_sha256': digest(operations),
            'operation_count': len(data['operaciones']),
            'acknowledgement': 'ACEPTO_CONCILIAR_RESET_PARCIAL_SIN_PREIMAGEN',
        }

    def test_explicit_attested_partial_project_reset_preserves_every_data_file(self):
        journal = self._uncheckpointed_zero_run_project_reset()
        attestation = self._partial_reset_attestation()
        before = inventory(self.path.parent)
        raw_journal = self.path.read_bytes()
        result = recover(self.service, journal['id'], partial_reset_attestation=attestation)
        self.assertEqual(result['status'], 'reconciled')
        self.assertEqual(result['outcome'], 'MANUAL_PARTIAL_PROJECT_RESET_ATTESTED')
        self.assertEqual(inventory(self.path.parent), before)
        self.assertFalse(self.path.exists())
        path = self.path.parent / 'administrative-transactions' / journal['id']
        self.assertEqual((path / 'original.json').read_bytes(), raw_journal)
        receipt = json.loads((path / 'resolution.json').read_text())
        self.assertEqual(receipt['audit']['verification'], 'OPERATOR_ATTESTED_NO_EXACT_PREIMAGE')
        self.assertEqual(receipt['audit']['project_observed_sha256'], attestation['project_sha256'])
        self.assertEqual(receipt['audit']['operations_sha256'], attestation['operations_sha256'])
        self.assertTrue(self.runtime.physical_ownership.snapshot()['recovery_pending'])
        self.assertFalse(self.runtime.physical_ownership.snapshot()['administrative_incomplete'])

    def test_attested_zero_run_reset_preserves_unrelated_run_with_empty_remote_filename(self):
        """Reported failure: other run has an expected filename; Moonraker standby has none."""
        journal = self._uncheckpointed_zero_run_project_reset(with_unrelated_run=True)
        attestation = self._partial_reset_attestation()
        other = (self.path.parent / 'projects' / 'unrelated-project'
                 / 'reports' / 'jobs' / 'other-setup' / 'superior' / 'current_run.json')
        foreign_before = other.read_bytes()
        data_before = inventory(self.path.parent)
        remote = self.service._observe_administrative_remote()
        remote.update(filename='', state='standby', is_active_observed=False,
                      live_velocity=[0.0, 0.0, 0.0])
        from types import SimpleNamespace
        with self.assertRaisesRegex(ApplicationError, 'Moonraker no tiene un archivo seleccionado'):
            self.service._assert_administrative_print_identity(
                json.loads(other.read_text()), SimpleNamespace(remote=remote, check=lambda: None))
        with patch.object(self.service, '_observe_administrative_remote', return_value=remote) as observe:
            result = recover(self.service, journal['id'], partial_reset_attestation=attestation)
            observe.assert_called_once()
        self.assertEqual(result['status'], 'reconciled')
        self.assertEqual(result['outcome'], 'MANUAL_PARTIAL_PROJECT_RESET_ATTESTED')
        self.assertEqual(other.read_bytes(), foreign_before)
        self.assertEqual(inventory(self.path.parent), data_before)
        self.assertFalse(self.path.exists())
        self.assertTrue(self.runtime.physical_ownership.snapshot()['recovery_pending'])
        self.assertFalse(self.runtime.physical_ownership.snapshot()['administrative_incomplete'])

    def test_attested_zero_run_reset_rejects_unrelated_printing(self):
        journal = self._uncheckpointed_zero_run_project_reset(with_unrelated_run=True)
        attestation = self._partial_reset_attestation()
        self.fixture.fixture.adapter.state = 'printing'
        before = inventory(self.path.parent)
        with self.assertRaisesRegex(ApplicationError, 'Moonraker'):
            recover(self.service, journal['id'], partial_reset_attestation=attestation)
        self.assertEqual(inventory(self.path.parent), before)
        self.assertTrue(self.path.exists())
        self.assertTrue(self.runtime.physical_ownership.snapshot()['administrative_incomplete'])

    def test_unattested_partial_project_reset_is_not_automatically_accepted(self):
        journal = self._uncheckpointed_zero_run_project_reset()
        with self.assertRaisesRegex(ApplicationError, 'checkpoint'):
            recover(self.service, journal['id'])
        self.assertTrue(self.path.exists())
        self.assertTrue(self.runtime.physical_ownership.snapshot()['administrative_incomplete'])

    def test_partial_reset_attestation_rejects_wrong_hash_or_acknowledgement(self):
        journal = self._uncheckpointed_zero_run_project_reset()
        good = self._partial_reset_attestation()
        for field, bad in [
            ('project_sha256', '0' * 64),
            ('journal_sha256', '1' * 64),
            ('operations_sha256', '2' * 64),
            ('operation_count', 100),
            ('acknowledgement', 'wrong'),
        ]:
            attempt = dict(good, **{field: bad})
            with self.subTest(field=field):
                with self.assertRaisesRegex(ApplicationError, 'checkpoint'):
                    recover(self.service, journal['id'], partial_reset_attestation=attempt)
                self.assertTrue(self.path.exists())

    def test_partial_reset_attestation_rejects_extra_modification(self):
        journal = self._uncheckpointed_zero_run_project_reset()
        attestation = self._partial_reset_attestation()
        extra = self.path.parent / 'unrelated.json'
        extra.write_text('{"unrelated": true}')
        with self.assertRaisesRegex(ApplicationError, 'checkpoint'):
            recover(self.service, journal['id'], partial_reset_attestation=attestation)
        self.assertTrue(self.path.exists())

    def test_partial_reset_attestation_rejects_new_run(self):
        journal = self._uncheckpointed_zero_run_project_reset()
        attestation = self._partial_reset_attestation()
        run = self.fixture.path
        run.write_text('{"unexpected": true}')
        with self.assertRaisesRegex(ApplicationError, 'checkpoint'):
            recover(self.service, journal['id'], partial_reset_attestation=attestation)
        self.assertTrue(self.path.exists())

    def test_fully_archived_reset_reconciles_with_unrelated_unselected_run(self):
        journal, other, original = self._interrupted_after_current_run_archived()
        result = recover(self.service, journal['id'])
        self.assertEqual(result['status'], 'reconciled')
        self.assertEqual(result['outcome'], 'PARTIAL_PRESERVED_RETRY_RESET')
        self.assertEqual(other.read_bytes(), original)
        self.assertFalse(self.path.exists())
        self.assertTrue(self.runtime.physical_ownership.snapshot()['recovery_pending'])
        self.assertFalse(self.runtime.physical_ownership.snapshot()['administrative_incomplete'])
        self.assertEqual(self.fixture.fixture.adapter.started, [])
        self.assertEqual(self.fixture.fixture.adapter.cancel_calls, 0)

    def test_fully_archived_reset_rejects_uncheckpointed_unrelated_change(self):
        journal, other, _original = self._interrupted_after_current_run_archived()
        other.write_text(other.read_text() + ' ')
        with self.assertRaisesRegex(ApplicationError, 'checkpoint'):
            recover(self.service, journal['id'])
        self.assertTrue(self.path.exists())
        self.assertTrue(self.runtime.physical_ownership.snapshot()['administrative_incomplete'])

    def test_http_failure_retains_journal(self):
        identity = self.fail(); original = self.path.read_bytes()
        with patch.object(self.service, '_observe_administrative_remote', side_effect=ApplicationError('Moonraker no verificable')):
            with self.assertRaisesRegex(ApplicationError, 'no verificable'): recover(self.service, identity)
        self.assertEqual(self.path.read_bytes(), original)

    def test_changed_files_are_not_adopted(self):
        identity = self.fail()
        (self.path.parent / 'unknown.json').write_text('{}')
        with self.assertRaisesRegex(ApplicationError, 'checkpoint'): recover(self.service, identity)
        self.assertTrue(self.path.exists())

    def test_session_change_blocks_recovery(self):
        identity = self.fail()
        def observe():
            self.runtime.current_physical_session_id = lambda: 'different'
        with patch.object(self.service, '_observe_administrative_remote', side_effect=observe):
            with self.assertRaisesRegex(ApplicationError, 'sesión'): recover(self.service, identity)
        self.assertTrue(self.path.exists())

    def test_busy_unknown_lock_blocks_recovery(self):
        identity = self.fail()
        with patch.object(self.service, '_movement_lock_state', return_value=None):
            with self.assertRaisesRegex(ApplicationError, 'movement_lock'): recover(self.service, identity)
        self.assertTrue(self.path.exists())

    def test_producer_or_revision_change_cannot_be_cleared(self):
        c = PhysicalMachineCoordinator()
        permit = c.acquire(OwnerKind.JOB_EXECUTION, 'synthetic/owner')
        c.administration_failed('pending')
        with self.assertRaises(OwnershipError): c.begin_administrative_recovery(expected_revision=c.snapshot()['revision'])
        self.assertTrue(c.snapshot()['producer_active'])
        self.assertIsNotNone(permit)
        identity = self.fail()
        def observe():
            self.runtime.physical_ownership.enter_recovery('concurrent cancellation')
        with patch.object(self.service, '_observe_administrative_remote', side_effect=observe):
            with self.assertRaises(OwnershipError): recover(self.service, identity)
        self.assertTrue(self.path.exists())

    def test_restart_preserves_recovery_evidence(self):
        identity = self.fail()
        self.runtime.physical_ownership = PhysicalMachineCoordinator()
        self.runtime.physical_ownership.administration_failed('startup found journal')
        # Fixture runtime binds its session to the coordinator dynamically.
        recover(self.service, identity)
        self.assertTrue(self.runtime.physical_ownership.snapshot()['recovery_pending'])

    def test_legacy_requires_verified_import(self):
        self.path.write_text(json.dumps({'id':'a'*32, 'action':'reset_setup_map','status':'PENDING'}))
        self.runtime.physical_ownership.administration_failed('legacy')
        self.assertEqual(status(self.service)['phase'], 'LEGACY_UNKNOWN')
        with self.assertRaisesRegex(ApplicationError, 'evidencia local'): recover(self.service, 'a'*32)
        self.assertTrue(self.path.exists())

    def test_failure_before_data_writes_can_reconcile(self):
        with self.assertRaises(OSError):
            with self.service.administrative_mutation('reset_setup_map', allow_started_runs=True) as guard:
                guard.begin_mutation(project_id=self.fixture.fixture.project_id)
                raise OSError('before handler')
        identity = json.loads(self.path.read_text())['id']
        self.assertEqual(recover(self.service, identity)['outcome'], 'NO_DATA_CHANGE')

    def test_history_write_failure_keeps_pending(self):
        identity = self.fail()
        with patch('klipper_cnc_assistant.execution.administrative_recovery.atomic_json', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): recover(self.service, identity)
        self.assertTrue(self.path.exists())
        self.assertTrue(self.runtime.physical_ownership.snapshot()['administrative_incomplete'])

    def test_retry_after_archive_before_coordinator_completion(self):
        identity = self.fail()
        with patch.object(self.runtime.physical_ownership, 'finish_administrative_recovery', side_effect=OSError('interrupted after archive')):
            with self.assertRaises(OSError): recover(self.service, identity)
        self.assertFalse(self.path.exists())
        self.assertEqual(status(self.service)['id'], identity)
        self.assertTrue(self.runtime.physical_ownership.snapshot()['administrative_incomplete'])
        self.assertEqual(recover(self.service, identity)['status'], 'reconciled')
        self.assertFalse(self.runtime.physical_ownership.snapshot()['administrative_incomplete'])

    def test_non_cooperative_producer_is_never_stopped_by_recovery(self):
        identity = self.fail()
        with patch.object(self.service, '_threads', {'retained': type('Worker', (), {'is_alive': lambda s: True})()}):
            with self.assertRaisesRegex(ApplicationError, 'Supervisor'): recover(self.service, identity)
        self.assertTrue(self.path.exists())

    def test_api_recovery_is_explicit_and_status_is_read_only(self):
        from tests.test_api import ApiTest
        api = ApiTest(); api.setUp(); self.addCleanup(api.doCleanups)
        api.app.state.job_service = self.service
        identity = self.fail(); before = self.path.read_bytes()
        response = api.client.get('/api/machine/administrative-recovery')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['id'], identity)
        self.assertEqual(self.path.read_bytes(), before)
        response = api.client.post('/api/machine/administrative-recovery/' + identity)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['physical_recovery_required'])

    def test_legacy_backup_proof_checks_all_current_dependencies(self):
        import tempfile, shutil, hashlib
        from pathlib import Path
        from klipper_cnc_assistant.execution.administrative_recovery import import_legacy_evidence
        # Independent synthetic legacy layout; never points at production.
        with tempfile.TemporaryDirectory() as td:
            data=Path(td)/'data'; backup=Path(td)/'backup'
            project=data/'projects'/'p'/'project.json'; project.parent.mkdir(parents=True)
            project.write_text(json.dumps({'montajes':[{'id':'s','active_map_id':None}]}))
            pending=data/'administrative-reset.pending.json'
            pending.write_text(json.dumps({'id':'b'*32,'action':'reset_setup_map','evidence':{'project_id':'p','setup_id':'s'}}))
            saved=backup/'data/projects/p/project.json'; saved.parent.mkdir(parents=True); shutil.copyfile(project,saved)
            (backup/'baseline.json').write_text(json.dumps([{'path':'data/projects/p/project.json','sha256':hashlib.sha256(saved.read_bytes()).hexdigest()}]))
            report=project.parent/'reports/mesh-validation/derived.json'
            report.parent.mkdir(parents=True); report.write_text('{}')
            saved_report=backup/'data/projects/p/reports/mesh-validation/derived.json'
            saved_report.parent.mkdir(parents=True); shutil.copyfile(report,saved_report)
            baseline=json.loads((backup/'baseline.json').read_text())
            baseline.append({'path':'data/projects/p/reports/mesh-validation/derived.json','sha256':hashlib.sha256(saved_report.read_bytes()).hexdigest()})
            (backup/'baseline.json').write_text(json.dumps(baseline))
            report.write_text('{"new_validation":true}')
            original=project.read_bytes(); project.write_text('{"changed":true}')
            with self.assertRaises((ApplicationError, KeyError)): import_legacy_evidence(data,backup)
            project.write_bytes(original)
            result=import_legacy_evidence(data,backup)
            self.assertEqual(result['dependencies'],2)
            self.assertTrue(pending.exists())
            with self.assertRaises(ApplicationError): import_legacy_evidence(data,backup)

    def test_recovered_reset_retry_keeps_one_administrative_history(self):
        identity = self.fail(partial=True)
        history = self.fixture.path.parent / 'history'
        originals = {p.name: p.read_bytes() for p in history.glob('*.json')}
        recover(self.service, identity)
        self.service._observe_administrative_remote()  # Explicit fresh fake idle evidence.
        owner = self.runtime.physical_ownership
        snapshot = owner.snapshot()
        owner.reconcile_quiescent(expected_session=snapshot['session'],
            expected_revision=snapshot['revision'], quiescent=True)
        self.fixture.reset()
        self.assertFalse(self.fixture.path.exists())
        self.assertEqual({p.name: p.read_bytes() for p in history.glob('*.json')}, originals)
        self.assertFalse(self.path.exists())
