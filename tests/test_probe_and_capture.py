"""Probe + persistence use a single request and fake physical boundaries."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import unittest

from tests import test_api, test_machine_runtime
from klipper_cnc_assistant.machine.runtime import MachineRuntimeState, ProbeResult
from klipper_cnc_assistant.machine.physical_ownership import OwnerKind


class ProbeAndCaptureTest(unittest.TestCase):
    def setUp(self):
        fixture = test_api.ApiTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.project_id = fixture._create_project()
        self.operation_id = fixture._create_operation(self.project_id)
        self.service = fixture.app.state.reference_session_service
        self.runtime, self.client = test_machine_runtime.ReferencePointMoveTest()._runtime()
        self.runtime._state = MachineRuntimeState.WAITING_FOR_XY_REFERENCE
        fixture.app.state.machine_runtime = self.runtime
        self.url = f'/api/projects/{self.project_id}/operations/{self.operation_id}/reference-session'
        self.descent = patch.object(self.runtime, '_perform_probe_descent', side_effect=self.measure)
        self.descent.start()
        self.addCleanup(self.descent.stop)

    def measure(self, **kwargs):
        self.runtime._machine.update_motion(live_position=(4, 5, 31), live_velocity=0)
        return ProbeResult(4, 5, 30, datetime.now(timezone.utc).isoformat())

    def saved(self):
        project = self.service.repository.load_project(self.project_id)
        operation = project.get_operation(self.operation_id)
        return project.get_setup(operation.setup_id).preparacion

    def test_old_split_requests_reproduce_expired_probe(self):
        context = self.service.probe_capture_context(self.project_id, self.operation_id)
        self.runtime.confirm_probe(reference_context=context)
        self.runtime._last_probe_result.captured_at = (datetime.now(timezone.utc) - timedelta(seconds=22)).isoformat()
        response = self.fixture.client.post(self.url + '/physical-z-reference-from-probe')
        self.assertEqual(response.status_code, 400)
        self.assertIn('obsoleta', response.text)
        self.assertIsNone(self.saved().referencia_z)

    def test_one_request_saves_contact_z_and_xy_before_response(self):
        response = self.fixture.client.post(self.url + '/probe-and-capture')
        self.assertEqual(response.status_code, 200, response.text)
        saved = self.saved()
        self.assertEqual((saved.origen_trabajo.x_mm, saved.origen_trabajo.y_mm), (4, 5))
        self.assertEqual(saved.referencia_z.z_mm, 30)  # never retracted Z=31
        self.assertEqual(saved.referencia_z.fuente, 'MEASURED')
        self.assertEqual(saved.referencia_z.sesion, self.runtime.current_physical_session_id())
        self.assertEqual(saved.referencia_z.confirmado_en, saved.origen_trabajo.confirmado_en)
        self.assertEqual(self.runtime.physical_ownership.snapshot()['kind'], 'IDLE')
        self.assertEqual(self.client.scripts, [])  # synthetic descent only

    def test_slow_response_serialization_happens_after_persistence(self):
        original = self.service.get_session
        def build(*args):
            self.assertEqual(self.saved().referencia_z.z_mm, 30)
            self.runtime._last_probe_result.captured_at = (datetime.now(timezone.utc) - timedelta(seconds=22)).isoformat()
            return original(*args)
        with patch.object(self.service, 'get_session', side_effect=build):
            response = self.fixture.client.post(self.url + '/probe-and-capture')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.saved().referencia_z.z_mm, 30)

    def test_failed_probe_does_not_save_either_reference(self):
        self.runtime._perform_probe_descent.side_effect = RuntimeError('probe failed')
        with self.assertRaisesRegex(RuntimeError, 'probe failed'):
            self.service.probe_and_capture_reference(self.project_id, self.operation_id, runtime=self.runtime)
        self.assertIsNone(self.saved().origen_trabajo)
        self.assertIsNone(self.saved().referencia_z)

    def test_expired_probe_still_rejected_by_combined_endpoint(self):
        def old(**kwargs):
            probe = self.measure(**kwargs)
            probe.captured_at = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
            return probe
        self.runtime._perform_probe_descent.side_effect = old
        response = self.fixture.client.post(self.url + '/probe-and-capture')
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn('obsoleta', response.text)
        self.assertIsNone(self.saved().referencia_z)

    def test_job_owner_prevents_probing(self):
        self.runtime.physical_ownership.acquire(OwnerKind.JOB_EXECUTION, 'job')
        response = self.fixture.client.post(self.url + '/probe-and-capture')
        self.assertEqual(response.status_code, 400, response.text)
        self.runtime._perform_probe_descent.assert_not_called()

    def test_cancel_before_persistence_rejects_save(self):
        original = self.runtime.capture_probe_reference_observation
        def cancelled(**kwargs):
            result = original(**kwargs)
            self.runtime.cancel_operation()
            return result
        with patch.object(self.runtime, 'capture_probe_reference_observation', side_effect=cancelled):
            response = self.fixture.client.post(self.url + '/probe-and-capture')
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIsNone(self.saved().referencia_z)
        self.assertFalse(self.runtime.physical_ownership.snapshot()['producer_active'])

    def test_project_context_change_during_probe_rejects_save(self):
        def changed(**kwargs):
            self.service.capture_physical_work_origin(self.project_id, self.operation_id,
                position={'x_mm': 70, 'y_mm': 80, 'z_mm': 90}, machine_label='fake', homed_axes='xyz')
            return self.measure(**kwargs)
        self.runtime._perform_probe_descent.side_effect = changed
        response = self.fixture.client.post(self.url + '/probe-and-capture')
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn('contexto', response.text)
        self.assertEqual(self.saved().origen_trabajo.x_mm, 70)
        self.assertIsNone(self.saved().referencia_z)

    def test_persistence_failure_cannot_partially_save_xy(self):
        with patch.object(self.service.repository, 'save_project', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                self.service.probe_and_capture_reference(self.project_id, self.operation_id, runtime=self.runtime)
        self.assertIsNone(self.saved().origen_trabajo)
        self.assertIsNone(self.saved().referencia_z)
        self.assertEqual(self.runtime.physical_ownership.snapshot()['kind'], 'IDLE')

    def test_uncertain_probe_preserves_recovery_and_retires_producer(self):
        def uncertain(**kwargs):
            self.runtime._active_operation.emitted = True
            self.runtime._active_operation.quiescent = False
            raise RuntimeError('uncertain')
        self.runtime._perform_probe_descent.side_effect = uncertain
        with self.assertRaisesRegex(RuntimeError, 'uncertain'):
            self.service.probe_and_capture_reference(self.project_id, self.operation_id, runtime=self.runtime)
        owner = self.runtime.physical_ownership.snapshot()
        self.assertEqual(owner['kind'], 'RECOVERY')
        self.assertFalse(owner['producer_active'])
        self.assertIsNone(self.saved().referencia_z)
