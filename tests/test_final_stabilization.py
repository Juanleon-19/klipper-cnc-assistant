"""Deterministic residual P1 regressions; all machine boundaries are fakes."""
import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import patch

from tests import test_api, test_job_service, test_machine_runtime
from klipper_cnc_assistant.application.errors import ApplicationError
from klipper_cnc_assistant.machine.physical_ownership import OwnerKind
from klipper_cnc_assistant.machine.runtime import MachineRuntimeError, MachineRuntimeState, ProbeResult


class FinalSettingsTest(unittest.TestCase):
    def test_owner_wins_between_settings_check_and_apply(self):
        runtime = test_machine_runtime.MachineRuntime(test_machine_runtime.config())
        before = runtime.config
        coordinator = runtime.physical_ownership
        acquire = coordinator.acquire
        def raced(kind, owner, **kwargs):
            acquire(OwnerKind.MESH, 'new-mesh-owner')
            return acquire(kind, owner, **kwargs)
        with patch.object(coordinator, 'acquire', side_effect=raced):
            with self.assertRaises(MachineRuntimeError):
                runtime.update_machine_settings({'z_clearance_feed_mm_min': 99})
        self.assertEqual(runtime.config, before)
        self.assertEqual(coordinator.snapshot()['owner_id'], 'new-mesh-owner')

    def test_nonfinite_and_nonpositive_runtime_settings(self):
        runtime = test_machine_runtime.MachineRuntime(test_machine_runtime.config())
        for value in (float('nan'), float('inf'), float('-inf'), 0, -1):
            with self.subTest(value=value), self.assertRaises((MachineRuntimeError, ValueError)):
                runtime.update_machine_settings({'z_clearance_feed_mm_min': value})
        for value in (float('nan'), float('inf'), float('-inf')):
            with self.subTest(config=value), self.assertRaises(ValueError):
                test_machine_runtime.config(probe_lower_speed_mm_s=value)

    def test_nonfinite_http_returns_422_not_500(self):
        fixture = test_api.ApiTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        for literal in ('NaN', 'Infinity', '-Infinity', '0', '-1'):
            response = fixture.client.put('/api/machine/settings',
                content='{"z_clearance_feed_mm_min":' + literal + '}',
                headers={'content-type': 'application/json'})
            self.assertEqual(response.status_code, 422, response.text)


class HistoricalProbeTest(unittest.TestCase):
    def setUp(self):
        self.runtime, self.client = test_machine_runtime.ReferencePointMoveTest()._runtime()
        self.runtime._state = MachineRuntimeState.REFERENCE_CAPTURED
        self.context = {'project_id': 'p', 'operation_id': 'op', 'setup_id': 's',
                        'face': 'superior', 'placement_revision': 'r', 'work_origin': None}
        self.probe = ProbeResult(4, 5, 30, datetime.now(timezone.utc).isoformat(),
                                self.runtime._probe_capture_evidence(self.context))

    def test_current_context_is_allowed_and_age_belongs_to_probe(self):
        self.runtime._last_probe_result = self.probe
        observation = self.runtime.capture_probe_reference_observation(expected_context=self.context)
        self.assertEqual(observation['position'], {'x_mm': 4, 'y_mm': 5, 'z_mm': 30})
        self.assertGreaterEqual(observation['position_age_s'], 0)

    def test_context_session_settings_or_frame_change_rejects(self):
        for key in ('operation_id', 'setup_id', 'face', 'placement_revision'):
            changed = dict(self.context, **{key: 'different'})
            with self.subTest(key=key), self.assertRaises(MachineRuntimeError):
                self.runtime._validate_probe_capture(self.probe, changed)
        self.runtime._serial_generation += 1
        with self.assertRaises(MachineRuntimeError):
            self.runtime._validate_probe_capture(self.probe, self.context)
        self.runtime._serial_generation -= 1
        self.runtime.config = replace(self.runtime.config, z_clearance_feed_mm_min=99)
        with self.assertRaises(MachineRuntimeError):
            self.runtime._validate_probe_capture(self.probe, self.context)

    def test_fresh_http_cannot_rejuvenate_old_or_unevidenced_probe(self):
        for captured, evidence in ((datetime.now(timezone.utc) - timedelta(seconds=10), self.probe.evidence),
                                   (datetime.now(timezone.utc), None)):
            self.runtime._last_probe_result = replace(self.probe, captured_at=captured.isoformat(), evidence=evidence)
            with self.assertRaises(MachineRuntimeError):
                self.runtime.capture_probe_reference_observation(expected_context=self.context)

    def test_session_change_during_probe_does_not_publish(self):
        self.runtime._state = MachineRuntimeState.REFERENCE_ARMED
        def descent(**kwargs):
            self.runtime._serial_generation += 1
            return self.probe
        with patch.object(self.runtime, '_perform_probe_descent', side_effect=descent):
            with self.assertRaises(MachineRuntimeError):
                self.runtime.confirm_probe(reference_context=self.context)
        self.assertIsNone(self.runtime._last_probe_result)


class FinalReadAndRunTest(unittest.TestCase):
    def setUp(self):
        fixture = test_job_service.JobServiceTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.service = fixture.job_service
        self.args = dict(project_id=fixture.project_id, setup_id=fixture.setup_id, face='superior')
        self.context = self.service._context(**self.args)

    def tree(self):
        root = self.fixture.repository.project_dir(self.fixture.project_id)
        return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob('*') if path.is_file()}

    def test_normal_reads_and_missing_run_do_not_publish_domain(self):
        before = self.tree()
        self.assertIsNone(self.service.get_run(**self.args))
        self.assertIsNone(self.service.live_execution(**self.args)['run']['run_id'])
        self.fixture.project_service.get_project(self.fixture.project_id)
        self.fixture.repository.list_projects()
        operation = self.fixture.project_service.get_project(self.fixture.project_id).operaciones[0]
        self.fixture.physical_map_service.get_active(self.fixture.project_id, operation.id)
        self.service.get_plan(**self.args)
        self.service.history(**self.args)
        self.assertEqual(self.tree(), before)
        with self.assertRaises(ApplicationError):
            self.service.start_run(**self.args)
        self.assertEqual(self.tree(), before)
        self.assertIsNotNone(self.service.prepare_run(**self.args))
        self.assertIsNotNone(self.service.get_run(**self.args))

    def test_http_get_run_returns_null_without_creating(self):
        fixture = test_api.ApiTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        project_id = fixture._create_project()
        setup_id = fixture.client.get('/api/projects/' + project_id).json()['montajes'][0]['id']
        root = fixture.data_dir / 'projects' / project_id
        before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
        response = fixture.client.get(f'/api/projects/{project_id}/job-run', params={'setup_id': setup_id, 'face': 'superior'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json())
        self.assertEqual({str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}, before)

    def test_persistent_standby_recovers_with_bounded_tolerance(self):
        run = self.service.prepare_run(**self.args)
        run['state'] = 'OPERATION_RUNNING'
        run['operations'][0]['remote_file'] = 'expected/A.gcode'
        self.service._save_run(self.context, run)
        standby = {'state': 'standby', 'filename': '', 'is_active': False}
        with patch('klipper_cnc_assistant.execution.job_service.time.monotonic', side_effect=[10, 11, 12.1]):
            for _ in range(3):
                self.assertTrue(self.service._reconcile_standby(self.context, run, standby))
        self.assertEqual(self.service.get_run(**self.args)['state'], 'RECOVERY_REQUIRED')
        self.assertTrue(self.fixture.runtime.physical_ownership.snapshot()['recovery_pending'])
        self.assertEqual(self.fixture.adapter.started, [])

    def test_transient_standby_resets_and_cancel_stays_terminal(self):
        run = self.service.prepare_run(**self.args)
        run['state'] = 'OPERATION_RUNNING'
        run['operations'][0]['remote_file'] = 'expected/A.gcode'
        self.service._save_run(self.context, run)
        standby = {'state': 'standby', 'filename': '', 'is_active': False}
        with patch('klipper_cnc_assistant.execution.job_service.time.monotonic', side_effect=[10, 20]):
            self.service._reconcile_standby(self.context, run, standby)
            self.assertFalse(self.service._reconcile_standby(self.context, run, {'state': 'printing'}))
            self.service._reconcile_standby(self.context, run, standby)
        self.assertEqual(self.service.get_run(**self.args)['state'], 'OPERATION_RUNNING')
        run['state'] = 'OPERATION_PAUSED'
        self.assertFalse(self.service._reconcile_standby(self.context, run, standby))
        self.assertEqual(self.service._standby_observations, {})
        run['state'] = 'OPERATION_RUNNING'
        cancelled = self.service.run_action(**self.args, action='cancel')
        self.assertFalse(self.service._reconcile_standby(self.context, cancelled, standby))
        self.assertEqual(self.service.get_run(**self.args)['state'], 'JOB_CANCELLED')

    def test_live_standby_reconciles_even_without_worker(self):
        run = self.service.prepare_run(**self.args)
        run['state'] = 'OPERATION_RUNNING'
        run['operations'][0]['remote_file'] = 'expected/A.gcode'
        self.service._save_run(self.context, run)
        self.fixture.adapter.state = 'standby'
        with patch('klipper_cnc_assistant.execution.job_service.time.monotonic', return_value=10):
            self.assertEqual(self.service.live_execution(**self.args)['run']['status'], 'OPERATION_RUNNING')
        with patch('klipper_cnc_assistant.execution.job_service.time.monotonic', return_value=12.1):
            self.assertEqual(self.service.live_execution(**self.args)['run']['status'], 'RECOVERY_REQUIRED')
        self.assertEqual(self.service.get_run(**self.args)['state'], 'RECOVERY_REQUIRED')

    def test_human_spindle_proof_bound_to_run_operation_and_session(self):
        run = self.service.prepare_run(**self.args)
        self.service._record_spindle_confirmation(self.context, run, 'prepared', 0)
        proof = run['spindle_confirmations']['prepared']
        self.assertEqual(proof['source'], 'human')
        self.assertEqual(proof['run_id'], run['run_id'])
        self.assertEqual(proof['operation_id'], run['operations'][0]['operation_id'])
        self.service._require_spindle_confirmation(self.context, run, 'prepared', 0)
        self.fixture.runtime.reference_serial_generation += 1
        with self.assertRaises(ApplicationError):
            self.service._require_spindle_confirmation(self.context, run, 'prepared', 0)

    def test_legacy_project_read_is_pure_explicit_migration_is_safe(self):
        path = self.fixture.repository.project_dir(self.fixture.project_id) / 'project.json'
        payload = json.loads(path.read_text())
        payload['version_esquema'] = '1.0'
        path.write_text(json.dumps(payload))
        before = path.read_bytes()
        self.fixture.repository.load_project(self.fixture.project_id)
        self.fixture.repository.list_projects()
        self.assertEqual(path.read_bytes(), before)
        migrated = self.fixture.repository.migrate_project(self.fixture.project_id)
        self.assertEqual(json.loads(path.read_text())['version_esquema'], migrated.version_esquema)
