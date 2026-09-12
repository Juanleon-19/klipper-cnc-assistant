"""PR24 behavior must use the existing P0 authorities, with no hardware IO."""
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tests import test_job_service as fixtures
from tests import test_machine_runtime as runtime_fixtures
from klipper_cnc_assistant.application.errors import ApplicationError
from klipper_cnc_assistant.machine.physical_ownership import OwnerKind
from klipper_cnc_assistant.machine.runtime import MachineRuntimeError
from klipper_cnc_assistant.storage.job_run_store import JobRunConflict


class Pr24SafetyIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.JobServiceTest()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.service = self.fixture.job_service
        self.runtime = self.fixture.runtime
        self.adapter = self.fixture.adapter
        self.args = dict(project_id=self.fixture.project_id, setup_id=self.fixture.setup_id, face='superior')
        self.context = self.service._context(**self.args)
        self.run = self.service.prepare_run(**self.args)

    def test_auxiliary_z_speed_changes_invalidate_ready_reference(self):
        self.run['state'] = 'READY_TO_RESUME'
        self.service._save_run(self.context, self.run)
        for setting in ('z_clearance_feed_mm_min', 'reference_approach_z_feed_mm_min'):
            with self.subTest(setting=setting):
                old = getattr(self.runtime.config, setting)
                setattr(self.runtime.config, setting, old + 1)
                try:
                    with patch.object(self.service, '_start_worker') as worker:
                        with self.assertRaises(ApplicationError):
                            self.service.run_action(**self.args, action='continue')
                        worker.assert_not_called()
                finally:
                    setattr(self.runtime.config, setting, old)
        self.assertEqual(self.adapter.uploads, [])
        self.assertEqual(self.adapter.started, [])

    def test_cancel_dominates_new_transition_progress_callback(self):
        self.run['state'] = 'TOOL_CHANGE_CONFIRMED'
        self.run['current_operation_index'] = 1
        self.service._save_run(self.context, self.run)

        def interrupted_return(**kwargs):
            self.service.run_action(**self.args, action='cancel')
            kwargs['progress_callback']('RETURNING_TO_REFERENCE_SAFE_Z', {'feed_mm_min': 180.0})

        with patch.object(self.adapter, 'move_from_tool_change_to_reference_point', side_effect=interrupted_return):
            with self.assertRaises(JobRunConflict):
                self.service._measure_tool_reference(self.context, self.run)
        self.assertEqual(self.service._load_run(self.context)['state'], 'JOB_CANCELLED')
        self.assertEqual(self.adapter.probe_calls, 0)
        self.assertEqual(self.adapter.started, [])

    def test_settings_guard_uses_shared_physical_owner(self):
        self.service.assert_machine_settings_update_allowed()
        self.runtime.physical_ownership.acquire(OwnerKind.MESH, 'existing-mesh-worker')
        with self.assertRaises(ApplicationError):
            self.service.assert_machine_settings_update_allowed()

    def test_settings_guard_preserves_recovery_block(self):
        self.runtime.physical_ownership.enter_recovery('pending cleanup')
        with self.assertRaises(ApplicationError):
            self.service.assert_machine_settings_update_allowed()

    def test_settings_guard_checks_existing_mesh_worker(self):
        self.service.mesh_execution_service = Mock()
        self.service.mesh_execution_service.active_execution_snapshot.return_value = {'active': True}
        with self.assertRaises(ApplicationError):
            self.service.assert_machine_settings_update_allowed()

    def test_runtime_settings_recheck_shared_owner_before_persisting(self):
        settings_path = Path(self.fixture.tempdir.name) / 'settings.json'
        runtime = runtime_fixtures.MachineRuntime(runtime_fixtures.config(), settings_path=settings_path)
        runtime.physical_ownership.acquire(OwnerKind.JOB_EXECUTION, 'existing-job')
        with self.assertRaises(MachineRuntimeError):
            runtime.update_machine_settings({'z_clearance_feed_mm_min': 240.0})
        self.assertEqual(runtime.config.z_clearance_feed_mm_min, 180.0)
        self.assertFalse(settings_path.exists())

    def test_executable_legacy_jit_preserves_flatcam_modal_cutting_feeds(self):
        operation_id = self.run['operations'][0]['operation_id']
        self.fixture.project_service.upload_operation_gcode(
            project_id=self.fixture.project_id, operation_id=operation_id, filename='flatcam.gcode',
            content='G21\nG90\nG0 X10 Y10\nG1 X15 Y10 Z-0.050 F120\nG1 X20 Y10 F300\nG1 X20 Y20\n')
        self.fixture.project_service.analyze_operation(project_id=self.fixture.project_id, operation_id=operation_id)
        result = self.fixture.compensated_service.generate(self.fixture.project_id, operation_id, max_segment_mm=1.0)
        self.assertTrue(result['executable'])
        self.assertIsNotNone(result['metadata']['physical_reference_token'])
        feeds = {}
        for entry in result['metadata']['movement_trace']:
            feeds.setdefault(entry['line_number'], set()).add(entry['feed_mm_min'])
        self.assertEqual(feeds[4], {120.0})
        self.assertEqual(feeds[5], {300.0})
        self.assertEqual(feeds[6], {300.0})
        generated = self.fixture.repository.read_project_file(self.fixture.project_id, result['relative_path'])
        self.assertGreater(generated.count('F300.000'), 2)
        self.assertNotIn('F600', generated)
        self.assertNotIn('F1800', generated)
