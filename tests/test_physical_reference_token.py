import copy
from dataclasses import replace
import threading
import unittest
from unittest.mock import patch

from tests import test_job_service as fixtures
from klipper_cnc_assistant.application.errors import ApplicationError
from klipper_cnc_assistant.domain.models import FlipAxis
from klipper_cnc_assistant.machine.physical_reference import PhysicalReferenceError, require_current_reference


class PhysicalReferenceTokenTest(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.JobServiceTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.service = fixture.job_service
        self.runtime = fixture.runtime
        self.adapter = fixture.adapter
        self.maps = fixture.physical_map_service
        self.repository = fixture.repository
        self.args = dict(project_id=fixture.project_id, setup_id=fixture.setup_id, face='superior')
        self.context = self.service._context(**self.args)
        self.run = self.service.prepare_run(**self.args)
        self.operation = self.run['operations'][0]
        self.operation_id = self.operation['operation_id']
        self.map = self.maps.get_active(fixture.project_id, self.operation_id)
        self.token = copy.deepcopy(self.operation['physical_reference_token'])

    def current(self):
        return require_current_reference(self.repository, self.runtime, self.fixture.project_id,
                                         self.operation_id, self.maps.get_active(self.fixture.project_id, self.operation_id))

    def ready(self):
        self.run['state'] = 'READY_TO_RESUME'
        self.service._save_run(self.context, self.run)

    def reject_continue(self):
        with patch.object(self.service, '_start_worker') as worker:
            with self.assertRaises(ApplicationError):
                self.service.run_action(**self.args, action='continue')
        worker.assert_not_called()
        self.assertEqual(self.adapter.started, [])
        self.assertEqual(self.adapter.uploads, [])

    def test_A_serial_generation_change_rejects_ready_continue(self):
        self.ready()
        self.runtime.reference_serial_generation += 1
        self.reject_continue()

    def test_A_runtime_session_change_rejects_ready_continue(self):
        self.ready()
        self.runtime.current_physical_session_id = lambda: 'different-runtime#serial-1'
        self.reject_continue()

    def test_B_same_tool_new_installation_rejects_previous_measurement(self):
        self.ready()
        self.operation['installation_revision'] = 'new-installation'
        self.service._save_run(self.context, self.run)
        self.reject_continue()

    def test_C_context_changes_reject_reference(self):
        for key, value in (('setup_id', 'different-setup'), ('face', 'inferior'), ('placement_revision', 'placement-2')):
            with self.subTest(field=key):
                payload = self.repository.load_height_map_payload(self.fixture.project_id, self.map['map_id'])
                payload['tool_references'][self.operation['tool_key']]['physical_reference_token'] = copy.deepcopy(
                    self.map['tool_references'][self.operation['tool_key']]['physical_reference_token'])
                payload['tool_references'][self.operation['tool_key']]['physical_reference_token'][key] = value
                self.repository.save_height_map_payload(self.fixture.project_id, self.map['map_id'], payload)
                with self.assertRaises(PhysicalReferenceError):
                    self.current()
        payload = self.repository.load_height_map_payload(self.fixture.project_id, self.map['map_id'])
        payload['tool_references'][self.operation['tool_key']]['physical_reference_token'] = copy.deepcopy(
            self.map['tool_references'][self.operation['tool_key']]['physical_reference_token'])
        self.repository.save_height_map_payload(self.fixture.project_id, self.map['map_id'], payload)
        project = self.repository.load_project(self.fixture.project_id)
        setup = project.get_setup(self.fixture.setup_id)
        self.repository.save_project(project.replace_setup(replace(setup, placement_revision='placement-2')))
        with self.assertRaises((ApplicationError, PhysicalReferenceError)):
            self.current()

    def test_D_physical_configuration_change_rejects_reference(self):
        self.runtime.config.probe_step_mm = 0.2
        with self.assertRaises(PhysicalReferenceError):
            self.current()
        with self.assertRaises(ApplicationError):
            self.fixture.compensated_service.generate(self.fixture.project_id, self.operation_id)

    def test_C_actual_setup_and_face_changes_reject_reference(self):
        original = self.repository.load_project(self.fixture.project_id)
        operation = original.get_operation(self.operation_id)
        for changed in (replace(operation, setup_id='different-setup'), replace(operation, cara='inferior')):
            with self.subTest(setup=changed.setup_id, face=changed.cara):
                if changed.setup_id != operation.setup_id:
                    setup = original.get_setup(operation.setup_id)
                    project = replace(original, montajes=original.montajes + (replace(setup, id='different-setup', orden=1),))
                else:
                    project = replace(original, configuracion_alineacion=replace(original.configuracion_alineacion, doble_cara=True, eje_volteo=FlipAxis.X))
                self.repository.save_project(project.replace_operation(changed))
                with self.assertRaises(PhysicalReferenceError):
                    require_current_reference(self.repository, self.runtime, self.fixture.project_id, self.operation_id, self.map)
        self.repository.save_project(original)

    def test_tool_reference_profile_change_rejects_reference(self):
        project = self.repository.load_project(self.fixture.project_id)
        operation = project.get_operation(self.operation_id)
        self.repository.save_project(project.replace_operation(replace(operation, tool_reference_profile='long_tool')))
        with self.assertRaises(PhysicalReferenceError):
            self.current()

    def test_D_probe_profile_configuration_change_rejects_reference(self):
        payload = copy.deepcopy(self.map)
        payload['probe_config']['probe_step_mm'] = 0.2
        self.repository.save_height_map_payload(self.fixture.project_id, self.map['map_id'], payload)
        with self.assertRaises(PhysicalReferenceError):
            self.current()

    def test_E_legacy_reference_is_readable_but_not_executable(self):
        payload = copy.deepcopy(self.map)
        reference = payload['tool_references'][self.operation['tool_key']]
        reference.pop('physical_reference_token')
        reference['valid'] = True
        self.repository.save_height_map_payload(self.fixture.project_id, self.map['map_id'], payload)
        self.assertTrue(self.maps.get_by_id(self.fixture.project_id, self.map['map_id'])['tool_references'][self.operation['tool_key']]['valid'])
        with self.assertRaises(ApplicationError):
            self.fixture.compensated_service.generate(self.fixture.project_id, self.operation_id)
        preview = self.fixture.compensated_service.generate(self.fixture.project_id, self.operation_id, require_tool_reference=False)
        self.assertFalse(preview['executable'])
        self.assertFalse(preview['metadata']['executable'])
        self.assertIsNone(preview['metadata']['physical_reference_token'])

    def test_F_session_change_during_probe_does_not_publish_token(self):
        payload = copy.deepcopy(self.map)
        payload['tool_references'][self.operation['tool_key']].pop('physical_reference_token')
        self.repository.save_height_map_payload(self.fixture.project_id, self.map['map_id'], payload)
        original = self.adapter.probe_tool_reference
        entered, release = threading.Event(), threading.Event()
        def probe(**args):
            entered.set()
            self.assertTrue(release.wait(3))
            return original(**args)
        errors = []
        def measure():
            try:
                self.service._measure_tool_reference(self.context, self.run)
            except ApplicationError as error:
                errors.append(error)
        with patch.object(self.adapter, 'probe_tool_reference', side_effect=probe):
            worker = threading.Thread(target=measure)
            worker.start()
            try:
                self.assertTrue(entered.wait(3))
                self.runtime.reference_serial_generation += 1
            finally:
                release.set()
                worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(errors), 1)
        reference = self.maps.get_by_id(self.fixture.project_id, self.map['map_id'])['tool_references'][self.operation['tool_key']]
        self.assertNotIn('physical_reference_token', reference)
        self.assertNotEqual(self.service._load_run(self.context)['state'], 'READY_TO_RESUME')

    def test_G_session_change_during_jit_rejects_artifact_before_publication(self):
        generator = self.fixture.compensated_service
        original = generator._build_compensated_lines
        def generate(*args):
            result = original(*args)
            self.runtime.reference_serial_generation += 1
            return result
        with patch.object(generator, '_build_compensated_lines', side_effect=generate):
            with self.assertRaises(ApplicationError):
                self.service._execute_next_operation(self.context, self.run)
        self.assertEqual(self.adapter.uploads, [])
        self.assertEqual(self.adapter.started, [])
        directory = self.repository.project_dir(self.fixture.project_id) / 'generated' / 'compensated'
        self.assertEqual(list(directory.glob('*.gcode')), [])

    def test_session_change_while_persisting_reference_cannot_mint_new_session_evidence(self):
        original = self.fixture.reference_service.capture_physical_z_reference
        def capture(*args, **kwargs):
            result = original(*args, **kwargs)
            self.runtime.reference_serial_generation += 1
            return result
        with patch.object(self.fixture.reference_service, 'capture_physical_z_reference', side_effect=capture):
            with self.assertRaises(ApplicationError):
                self.service._measure_tool_reference(self.context, self.run)
        self.assertEqual(self.maps.get_by_id(self.fixture.project_id, self.map['map_id'])['tool_references'][self.operation['tool_key']]['physical_reference_token'], self.token)
        with self.assertRaises(PhysicalReferenceError):
            self.current()

    def test_H_same_session_context_allows_measure_jit_start_and_resume(self):
        self.service._measure_tool_reference(self.context, self.run)
        self.assertEqual(self.run['state'], 'READY_TO_RESUME')
        token = self.current()
        self.assertEqual(self.run['operations'][0]['physical_reference_token'], token)
        self.service._execute_next_operation(self.context, self.run)
        self.assertEqual(len(self.adapter.started), 1)
        current = self.service._load_run(self.context)
        current['state'] = 'OPERATION_PAUSED'
        current['operations'][0]['execution_status'] = 'PAUSED'
        self.service._save_run(self.context, current)
        self.adapter.state = 'paused'
        self.adapter._printing_seen = True
        with patch.object(self.service, '_start_worker'):
            result = self.service.run_action(**self.args, action='resume')
        self.assertEqual(self.adapter.resume_calls, 1)
        self.assertEqual(result['state'], 'OPERATION_RUNNING')

    def test_start_revalidates_after_upload_session_change(self):
        original = self.adapter.upload_file
        def upload(**args):
            result = original(**args)
            self.runtime.reference_serial_generation += 1
            return result
        with patch.object(self.adapter, 'upload_file', side_effect=upload):
            with self.assertRaises(ApplicationError):
                self.service._execute_next_operation(self.context, self.run)
        self.assertEqual(len(self.adapter.uploads), 1)
        self.assertEqual(self.adapter.started, [])

    def test_resume_revalidates_after_identity_query(self):
        self.run['state'] = 'OPERATION_PAUSED'
        self.operation.update(remote_file='expected/A.gcode', execution_status='PAUSED')
        self.service._save_run(self.context, self.run)
        self.adapter.current_filename = 'expected/A.gcode'
        self.adapter.state = 'paused'
        original = self.adapter.print_status
        def status():
            result = original()
            self.runtime.reference_serial_generation += 1
            return result
        with patch.object(self.adapter, 'print_status', side_effect=status):
            result = self.service.run_action(**self.args, action='resume')
        self.assertEqual(self.adapter.resume_calls, 0)
        self.assertEqual(result['state'], 'RECOVERY_REQUIRED')

    def test_I_cancel_and_recovery_never_refresh_old_token(self):
        self.runtime.reference_serial_generation += 1
        before = self.maps.get_by_id(self.fixture.project_id, self.map['map_id'])['tool_references']
        self.service.run_action(**self.args, action='cancel')
        self.runtime.reconcile_physical_ownership()
        after = self.maps.get_by_id(self.fixture.project_id, self.map['map_id'])['tool_references']
        self.assertEqual(before, after)
        with self.assertRaises(PhysicalReferenceError):
            self.current()

    def test_recovery_does_not_adopt_print_with_stale_reference(self):
        self.operation.update(remote_file='expected/A.gcode', execution_status='PAUSED')
        self.adapter.current_filename = 'expected/A.gcode'
        self.adapter.state = 'paused'
        self.runtime.reference_serial_generation += 1
        result = self.service._recover_active_print_if_possible(self.context, self.run)
        self.assertIsNone(result)
        self.assertEqual(self.run['state'], 'RECOVERY_REQUIRED')
        self.assertEqual(self.adapter.resume_calls, 0)
        with self.assertRaises(PhysicalReferenceError):
            self.current()

    def test_job_start_with_stale_ready_run_never_launches_worker(self):
        self.runtime.reference_serial_generation += 1
        with patch.object(self.service, '_start_worker') as worker:
            with self.assertRaises(ApplicationError):
                self.service.start_run(**self.args)
        worker.assert_not_called()
        self.assertEqual(self.adapter.started, [])

    def test_reference_context_change_rejects_previous_measurement(self):
        self.fixture.reference_service.capture_physical_z_reference(
            self.fixture.project_id, self.operation_id,
            position={'x_mm': 100, 'y_mm': 100, 'z_mm': 7}, machine_label='test',
            homed_axes='xyz', session_id=self.runtime.current_physical_session_id(),
        )
        with self.assertRaises(PhysicalReferenceError):
            self.current()
