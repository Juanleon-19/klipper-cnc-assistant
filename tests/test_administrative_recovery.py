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
