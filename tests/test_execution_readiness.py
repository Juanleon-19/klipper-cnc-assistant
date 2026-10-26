"""Execution lifecycle regressions, using only temporary data and explicit fakes."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import patch

from tests import test_job_service
from klipper_cnc_assistant.application.errors import ApplicationError
from klipper_cnc_assistant.execution.job_service import RUN_ACTIVE_STATES, RUN_WAITING_STATES
from klipper_cnc_assistant.machine.physical_reference import require_current_reference


class ExecutionReadinessTest(unittest.TestCase):
    def setUp(self):
        self.fixture = test_job_service.JobServiceTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        f = self.fixture
        self.job, self.repo, self.runtime = f.job_service, f.repository, f.runtime
        self.args = dict(project_id=f.project_id, setup_id=f.setup_id, face='superior')
        self.context = self.job._context(**self.args)
        self.path = self.job._existing_run_file(self.context)

    def test_prepare_rejects_all_active_waiting_and_terminal_states_without_writes(self):
        run = self.job.prepare_run(**self.args)
        for state in RUN_ACTIVE_STATES | RUN_WAITING_STATES | {'TOOL_CHANGE_CONFIRMED', 'JOB_ERROR', 'JOB_COMPLETE', 'JOB_CANCELLED'}:
            with self.subTest(state=state):
                # Independent store snapshot per case; the service under test
                # must not write any part of it, even checks or revisions.
                run = self.job.run_store.load(self.path)
                run['state'] = state
                self.job.run_store._write(self.path, run)
                before = self.path.read_bytes()
                with self.assertRaises(ApplicationError):
                    self.job.prepare_run(**self.args)
                self.assertEqual(self.path.read_bytes(), before)

    def tree(self):
        return {str(p): p.read_bytes() for p in self.repo.base_dir.rglob('*') if p.is_file()}

    def test_diagnostic_with_and_without_run_is_read_only_and_cached(self):
        before = self.tree()
        owner = self.runtime.physical_ownership.snapshot()
        with patch.object(self.job, '_build_plan', wraps=self.job._build_plan) as build:
            first = self.job.inspect_execution(**self.args)
            self.job.inspect_execution(**self.args)
            self.assertEqual(build.call_count, 1)
        self.assertFalse(self.path.exists())
        self.assertEqual(self.tree(), before)
        self.assertEqual(self.runtime.physical_ownership.snapshot(), owner)
        self.assertFalse(first['readiness']['can_start'])
        self.assertTrue(first['readiness']['conditions_ready'])
        self.job.prepare_run(**self.args)
        before = self.tree()
        diagnostic = self.job.inspect_execution(**self.args)
        self.assertTrue(diagnostic['readiness']['can_start'])
        self.assertEqual(self.tree(), before)
        self.assertEqual(self.runtime.physical_ownership.snapshot(), owner)

    def test_future_tool_references_do_not_block_initial_operation(self):
        run = self.job.prepare_run(**self.args)
        self.assertEqual(run['state'], 'JOB_READY')
        self.assertTrue(any(o['reference_status'] != 'LISTA' for o in run['operations'][2:]))
        assessment = self.job.inspect_execution(**self.args)['readiness']
        self.assertTrue(assessment['can_start'], assessment)
        self.assertFalse(assessment['blocking_reasons'])

    def test_changed_session_reports_exact_reason_without_repreparing(self):
        self.job.prepare_run(**self.args)
        before = self.path.read_bytes()
        self.runtime.reference_serial_generation += 1
        diagnostic = self.job.inspect_execution(**self.args)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(diagnostic['readiness']['can_start'])
        self.assertEqual(diagnostic['readiness']['reference']['status'], 'SESIÓN ANTERIOR')
        self.assertIn('REFERENCE_SESSION_CHANGED', [r['code'] for r in diagnostic['readiness']['blocking_reasons']])
        self.assertEqual(diagnostic['plan']['summary']['blocked_operations'], 0)
        with self.assertRaises(ApplicationError):
            self.job.start_run(**self.args, spindle_prepared=True)

    def test_missing_or_stale_telemetry_and_recovery_block_even_with_valid_reference(self):
        self.job.prepare_run(**self.args)
        for value in (False, None):
            self.runtime.snapshot_payload['safety']['telemetry_recent'] = value
            result = self.job.inspect_execution(**self.args)['readiness']
            self.assertFalse(result['can_start'])
            self.assertIn('TELEMETRIA_VIGENTE', [r['code'] for r in result['blocking_reasons']])
        self.runtime.snapshot_payload['safety']['telemetry_recent'] = True
        self.runtime.physical_ownership.enter_recovery('uncertain')
        self.assertFalse(self.job.inspect_execution(**self.args)['readiness']['can_start'])

    def test_start_requires_explicit_human_confirmation_without_side_effects(self):
        self.job.prepare_run(**self.args)
        before, owner = self.path.read_bytes(), self.runtime.physical_ownership.snapshot()
        for value in (False, None, 'true', 1):
            with self.subTest(value=value), self.assertRaises(ApplicationError):
                self.job.start_run(**self.args, spindle_prepared=value)
            self.assertEqual(self.path.read_bytes(), before)
            self.assertEqual(self.runtime.physical_ownership.snapshot(), owner)
        with patch.object(self.job, '_start_worker'):
            run = self.job.start_run(**self.args, spindle_prepared=True)
        self.assertEqual(run['state'], 'JOB_STARTING')
        self.assertIn('prepared', run['spindle_confirmations'])

    def test_a_a_b_b_uses_single_map_and_probes_new_installation_only_once(self):
        f, j = self.fixture, self.job
        project = self.repo.load_project(f.project_id)
        ops = sorted(project.operations_for_setup(f.setup_id), key=lambda o:o.orden)
        project = project.replace_operation(replace(ops[3], tool_id=ops[2].tool_id,
            herramienta=ops[2].herramienta, tool_reference_profile=ops[2].tool_reference_profile))
        self.repo.save_project(project)
        initial_map = f.physical_map_service.get_active(f.project_id, ops[0].id)
        run = j.prepare_run(**self.args)
        initial_token = deepcopy(run['operations'][0]['physical_reference_token'])
        with patch.object(j, '_start_worker'):
            j.start_run(**self.args, spindle_prepared=True)
            j._run_worker(self.context)
            self.assertEqual(j.get_run(**self.args)['state'], 'READY_TO_RESUME')
            self.assertEqual(len(f.adapter.started), 1)
            j.run_action(**self.args, action='continue')
            j._run_worker(self.context)
            run = j.get_run(**self.args)
            self.assertEqual(run['state'], 'SPINDLE_STOP_REQUIRED')
            self.assertEqual(len(f.adapter.started), 2)
            self.assertEqual(f.adapter.probe_calls, 0)
            self.assertEqual(run['operations'][1]['physical_reference_token'], initial_token)
            j.run_action(**self.args, action='confirm-spindle-stopped')
            j._run_worker(self.context)
            self.assertEqual(j.get_run(**self.args)['state'], 'TOOL_CHANGE_REQUIRED')
            j.run_action(**self.args, action='confirm-tool-change')
            j._run_worker(self.context)
            run = j.get_run(**self.args)
            self.assertEqual(run['state'], 'READY_TO_RESUME')
            self.assertEqual(f.adapter.probe_calls, 1)
            token_b = deepcopy(run['operations'][2]['physical_reference_token'])
            self.assertNotEqual(initial_token['installation_id'], token_b['installation_id'])
            j.run_action(**self.args, action='continue')
            j._run_worker(self.context)
            self.assertEqual(j.get_run(**self.args)['state'], 'READY_TO_RESUME')
            self.assertEqual(len(f.adapter.started), 3)
            j.run_action(**self.args, action='continue')
            j._run_worker(self.context)
        run = j.get_run(**self.args)
        self.assertEqual(run['state'], 'JOB_COMPLETE', run.get('last_watcher_error'))
        self.assertEqual(len(f.adapter.started), 4)
        self.assertEqual(f.adapter.probe_calls, 1)
        self.assertEqual(run['operations'][3]['physical_reference_token'], token_b)
        self.assertEqual(run['operations'][3]['installation_revision'], token_b['installation_id'])
        final_map = f.physical_map_service.get_active(f.project_id, ops[3].id)
        for key in ('map_id', 'height_map', 'points', 'acquisition_reference_z', 'acquisition_tool_id'):
            self.assertEqual(final_map[key], initial_map[key], key)
        self.assertEqual(len(list(self.repo.project_dir(f.project_id).glob('maps/**/height_map.json'))), 1)
        self.assertEqual(self.runtime.physical_ownership.snapshot()['kind'], 'IDLE')

    def test_changed_installation_cannot_inherit_reference(self):
        run = self.job.prepare_run(**self.args)
        run['operations'][0]['installation_revision'] = 'another-installation'
        self.job._save_run(self.context, run)
        assessment = self.job.inspect_execution(**self.args)['readiness']
        self.assertFalse(assessment['can_start'])
        self.assertEqual(assessment['reference']['code'], 'REFERENCE_INSTALLATION_CHANGED')

    def test_recovery_messages_distinguish_causes(self):
        run = self.job.prepare_run(**self.args)
        assessment = self.job.inspect_execution(**self.args)['readiness']
        run['state'] = 'RECOVERY_REQUIRED'
        run['operations'][0]['remote_file'] = 'expected.gcode'
        status = {'print_state': 'printing', 'filename': 'wrong.gcode'}
        self.assertEqual(self.job._recovery_diagnostic(run,status,assessment,False)['code'], 'PRINT_IDENTITY_MISMATCH')
        status['print_state'] = 'standby'
        self.assertEqual(self.job._recovery_diagnostic(run,status,assessment,False)['code'], 'MOONRAKER_STANDBY')
        run['state'] = 'OPERATION_RUNNING'
        status['print_state'] = 'printing'; status['filename'] = 'expected.gcode'
        self.assertEqual(self.job._recovery_diagnostic(run,status,assessment,False)['code'], 'SUPERVISOR_MISSING')
