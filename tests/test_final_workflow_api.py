"""Real API routes and workers, with existing in-memory physical boundaries."""
import threading
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

from klipper_cnc_assistant.api import create_app
from tests import test_job_service


class FinalWorkflowApiTest(unittest.TestCase):
    def setUp(self):
        fixture = test_job_service.JobServiceTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.service = fixture.job_service
        self.adapter = fixture.adapter
        app = create_app(data_dir=fixture.repository.base_dir)
        for name, value in dict(project_service=fixture.project_service,
                                physical_map_service=fixture.physical_map_service,
                                compensated_gcode_service=fixture.compensated_service,
                                reference_session_service=fixture.reference_service,
                                job_service=self.service, machine_runtime=fixture.runtime).items():
            setattr(app.state, name, value)
        self.client = TestClient(app)
        self.target = {'setup_id': fixture.setup_id, 'face': 'superior'}
        self.root = f'/api/projects/{fixture.project_id}'

    def post(self, suffix, **extra):
        response = self.client.post(self.root + suffix, json=dict(self.target, **extra))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def current(self):
        response = self.client.get(self.root + '/job-run', params=self.target)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def join(self):
        thread = self.service._threads.get((self.fixture.project_id, self.fixture.setup_id, 'superior'))
        if thread:
            thread.join(5)
            self.assertFalse(thread.is_alive())

    def test_api_multi_tool_workflow_requires_human_continue_after_probe(self):
        self.assertIsNone(self.current())
        self.post('/job-run/prepare')
        self.post('/job-run/start')
        self.join()
        self.assertEqual(self.current()['state'], 'SPINDLE_STOP_REQUIRED')
        for index in (2, 3):
            self.post('/job-run/action', action='confirm-spindle-stopped')
            self.join()
            self.assertEqual(self.current()['state'], 'TOOL_CHANGE_REQUIRED')
            self.post('/job-run/action', action='confirm-tool-change')
            self.join()
            ready = self.current()
            self.assertEqual(ready['state'], 'READY_TO_RESUME')
            self.assertEqual(len(self.adapter.started), index)
            self.assertTrue(ready['operations'][index]['physical_reference_token'])
            blocked = self.client.put('/api/machine/settings', json={'z_clearance_feed_mm_min': 99})
            self.assertEqual(blocked.status_code, 400, blocked.text)
            self.post('/job-run/action', action='continue')
            self.join()
        self.assertEqual(self.current()['state'], 'JOB_COMPLETE')
        self.assertEqual(len(self.adapter.started), 4)
        self.assertEqual(self.adapter.probe_calls, 2)
        self.assertEqual(self.adapter.spindle_stops, 0)

    def hold_printing(self):
        entered = threading.Event()
        start = self.adapter.start_file
        self.original_start = start
        save = self.service._save_run
        def observed(context, run):
            result = save(context, run)
            if run['state'] == 'OPERATION_RUNNING':
                entered.set()
            return result
        self.service._save_run = observed
        def printing(*args, **kwargs):
            result = start(*args, **kwargs)
            self.adapter.state = 'printing'
            return result
        self.adapter.start_file = printing
        self.post('/job-run/prepare')
        self.post('/job-run/start')
        self.assertTrue(entered.wait(5))

    def test_api_pause_resume_and_cancel_existing_correct_print(self):
        self.hold_printing()
        paused = self.post('/job-run/action', action='pause')
        self.assertEqual(paused['state'], 'OPERATION_PAUSED')
        self.join()
        self.adapter.start_file = self.original_start
        self.post('/job-run/action', action='resume')
        self.assertEqual(self.adapter.resume_calls, 1)
        self.join()
        cancelled = self.post('/job-run/action', action='cancel')
        self.assertEqual(cancelled['state'], 'JOB_CANCELLED')
        self.assertEqual(self.current()['state'], 'JOB_CANCELLED')

    def test_api_wrong_print_file_enters_recovery_without_resume(self):
        self.hold_printing()
        self.post('/job-run/action', action='pause')
        self.join()
        self.adapter.current_filename = 'wrong/B.gcode'
        recovered = self.post('/job-run/action', action='resume')
        self.assertEqual(recovered['state'], 'RECOVERY_REQUIRED')
        self.assertEqual(self.adapter.resume_calls, 0)
        self.post('/job-run/action', action='cancel')

    def test_api_cancel_during_jit_prevents_upload_and_start(self):
        entered, release = threading.Event(), threading.Event()
        generate = self.fixture.compensated_service.generate
        def jit(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError('JIT barrier timed out')
            return generate(*args, **kwargs)
        with patch.object(self.fixture.compensated_service, 'generate', side_effect=jit):
            self.post('/job-run/prepare')
            self.post('/job-run/start')
            try:
                self.assertTrue(entered.wait(5))
                self.post('/job-run/action', action='cancel')
            finally:
                release.set()
                self.join()
        self.assertEqual(self.current()['state'], 'JOB_CANCELLED')
        self.assertEqual(self.adapter.uploads, [])
        self.assertEqual(self.adapter.started, [])
