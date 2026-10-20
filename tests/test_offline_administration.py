"""Offline administration with temporary storage; no hardware transports."""
import json
import unittest
from unittest.mock import Mock
from tests import test_api as fixtures
from klipper_cnc_assistant.machine.physical_ownership import OwnerKind


class OfflineAdministrationTest(unittest.TestCase):
    def setUp(self):
        f = fixtures.ApiTest(); f.setUp()
        self.addCleanup(f.doCleanups)
        self.f = f
        self.project = f._create_project()
        self.other = f._create_project()
        self.runtime = f.app.state.machine_runtime
        self.service = f.app.state.job_service
        snapshot = self.runtime.snapshot()
        snapshot.update(mode='PHYSICAL', state='DISCONNECTED')
        self.runtime.snapshot = lambda: snapshot
        self.adapter = Mock()
        self.adapter.print_status.return_value = {'connected': True, 'klipper_state': 'shutdown',
            'state': 'complete', 'is_active_observed': False, 'homed_axes_observed': '', 'live_velocity': 0}
        self.service.adapter_factory = lambda runtime: self.adapter

    def protect_project(self):
        setup = self.f.client.get(f'/api/projects/{self.project}').json()['montajes'][0]['id']
        path = self.f.data_dir / 'projects' / self.project / 'reports/jobs' / setup / 'superior/current_run.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'run_id': 'synthetic', 'revision': 1, 'state': 'SPINDLE_STOP_REQUIRED'}))
        self.runtime.physical_ownership.acquire(OwnerKind.JOB_EXECUTION, f'{self.project}/{setup}/superior')
        self.runtime.physical_ownership.enter_recovery('synthetic recovery')
        return path

    def test_open_shutdown_without_http(self):
        response = self.f.client.post(f'/api/projects/{self.project}/open')
        self.assertEqual(response.status_code, 200, response.text)
        self.adapter.print_status.assert_not_called()

    def test_edit_unrelated_project_during_recovery(self):
        path = self.protect_project(); before = path.read_bytes()
        response = self.f.client.post(f'/api/projects/{self.other}/operations', json={
            'nombre': 'Offline', 'tipo': 'aislamiento', 'cara': 'superior', 'orden': 0})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(before, path.read_bytes())
        self.adapter.print_status.assert_not_called()

    def test_open_protected_project_is_read_only(self):
        self.protect_project()
        directory = self.f.data_dir / 'projects' / self.project
        before = {p: p.read_bytes() for p in directory.rglob('*') if p.is_file()}
        owner = self.runtime.physical_ownership.snapshot()
        self.runtime.connect = Mock(side_effect=AssertionError('must not connect'))
        response = self.f.client.post(f'/api/projects/{self.project}/open')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(before, {p: p.read_bytes() for p in directory.rglob('*') if p.is_file()})
        self.assertEqual(owner, self.runtime.physical_ownership.snapshot())
        self.runtime.connect.assert_not_called()
        self.adapter.print_status.assert_not_called()

    def test_protected_project_writes_block_without_http(self):
        path = self.protect_project(); before = path.read_bytes()
        project_path = self.f.data_dir / 'projects' / self.project / 'project.json'
        project_before = project_path.read_bytes()
        for action in ('archive', 'trash'):
            response = self.f.client.post(f'/api/projects/{self.project}/{action}')
            self.assertEqual(response.status_code, 400, response.text)
        response = self.f.client.post(f'/api/projects/{self.project}/operations', json={
            'nombre': 'Forbidden', 'tipo': 'aislamiento', 'cara': 'superior', 'orden': 0})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(before, path.read_bytes())
        self.assertEqual(project_before, project_path.read_bytes())
        self.adapter.print_status.assert_not_called()

    def test_original_file_and_analysis_can_be_managed_offline(self):
        operation = self.f._create_operation(self.project)
        self.f._upload_and_analyze_operation(self.project, operation)
        self.adapter.print_status.assert_not_called()

    def test_shutdown_still_blocks_all_physical_resets(self):
        setup = self.f.client.get(f'/api/projects/{self.project}').json()['montajes'][0]['id']
        for action in ('reset-preparation', 'reset-reference', 'reset-map'):
            response = self.f.client.post(f'/api/projects/{self.project}/setups/{setup}/{action}', json={})
            self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self.adapter.print_status.call_count, 3)

    def test_persisted_run_without_owner_still_protects_project(self):
        path = self.protect_project()
        from klipper_cnc_assistant.machine.physical_ownership import PhysicalMachineCoordinator
        self.runtime.physical_ownership = PhysicalMachineCoordinator(uncertain=True)  # simulated restart
        response = self.f.client.post(f'/api/projects/{self.project}/archive')
        self.assertEqual(response.status_code, 400, response.text)
        self.assertTrue(path.exists())
        response = self.f.client.post(f'/api/projects/{self.other}/archive')
        self.assertEqual(response.status_code, 200, response.text)

    def test_unknown_owner_blocks_writes_but_not_open(self):
        self.runtime.physical_ownership.acquire(OwnerKind.RUNTIME_MOTION, 'unscoped')
        self.assertEqual(self.f.client.post(f'/api/projects/{self.project}/archive').status_code, 400)
        self.assertEqual(self.f.client.post(f'/api/projects/{self.project}/open').status_code, 200)

    def test_project_edit_excludes_same_project_execution_and_physical_reset(self):
        from klipper_cnc_assistant.execution.administrative_guard import project_mutation
        from klipper_cnc_assistant.machine.physical_ownership import OwnershipError
        c = self.runtime.physical_ownership
        before = c.snapshot()
        with project_mutation(self.service, self.project):
            with self.assertRaises(OwnershipError):
                c.begin_job_update(project_id=self.project)
            with self.assertRaises(OwnershipError):
                c.acquire(OwnerKind.JOB_EXECUTION, f'{self.project}/setup/superior')
            with self.assertRaises(OwnershipError):
                c.begin_administration(expected_revision=before['revision'])
            token = c.begin_job_update(project_id=self.other)
            c.end_job_update(token)  # unrelated cancellation/action remains available
        self.assertEqual(before, c.snapshot())

    def test_inflight_same_project_request_excludes_edit(self):
        from klipper_cnc_assistant.execution.administrative_guard import project_mutation
        from klipper_cnc_assistant.application.errors import ApplicationError
        c = self.runtime.physical_ownership
        token = c.begin_job_update(project_id=self.project)
        try:
            with self.assertRaises(ApplicationError):
                with project_mutation(self.service, self.project):
                    self.fail('must not edit')
            with project_mutation(self.service, self.other):
                pass
        finally:
            c.end_job_update(token)

    def test_prepared_or_ambiguous_terminal_run_protects_project(self):
        path = self.protect_project()
        from klipper_cnc_assistant.machine.physical_ownership import PhysicalMachineCoordinator
        self.runtime.physical_ownership = PhysicalMachineCoordinator()
        for state, attempt in [('JOB_READY', None), ('JOB_ERROR', {'status': 'uncertain', 'may_have_been_sent': True}), ('JOB_CANCELLED', {'status': 'sent', 'may_have_been_sent': True})]:
            path.write_text(json.dumps({'run_id': 'fake', 'state': state, 'start_attempt': attempt}))
            before = path.read_bytes()
            self.assertEqual(self.f.client.post(f'/api/projects/{self.project}/archive').status_code, 400)
            self.assertEqual(before, path.read_bytes())
