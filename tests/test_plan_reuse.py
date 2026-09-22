"""Content-dependent plans and persisted analysis; no hardware or timing assertions."""
from copy import deepcopy
from dataclasses import replace
import json
import os
import unittest
from unittest.mock import patch

from tests import test_job_service
from klipper_cnc_assistant.application.errors import ApplicationError
from klipper_cnc_assistant.gcode.analysis_cache import analysis_is_current
from klipper_cnc_assistant.storage.safe_persistence import PersistenceConflict


class PlanReuseTest(unittest.TestCase):
    def setUp(self):
        fixture = test_job_service.JobServiceTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.job, self.repo = fixture.job_service, fixture.repository
        self.args = dict(project_id=fixture.project_id, setup_id=fixture.setup_id, face='superior')
        self.context = self.job._context(**self.args)

    def test_repeated_reads_and_prepare_reuse_plan_without_project_loads(self):
        before = self.job.get_plan(**self.args)
        with patch.object(self.job, '_build_plan', wraps=self.job._build_plan) as build, \
             patch.object(self.repo, 'load_project', wraps=self.repo.load_project) as load:
            self.assertEqual(self.job.get_plan(**self.args), before)
            run = self.job.prepare_run(**self.args)
            self.assertTrue(run['ready'])
            self.job.prepare_run(**self.args)
        self.assertEqual(build.call_count, 0)
        self.assertEqual(load.call_count, 0)
        # Persisted identity survives a service-memory cache miss.
        self.job._plan_cache.clear()
        with patch.object(self.job, '_build_plan', wraps=self.job._build_plan) as build:
            self.job.get_plan(**self.args)
        build.assert_not_called()

    def test_live_target_checks_reuse_metadata_but_detect_same_mtime_changes(self):
        self.job._validate_execution_target(self.context)
        metadata = self.job._project_dependency_cache[self.fixture.project_id][1]
        self.assertTrue(all('analisis' not in op for op in metadata['operaciones']))
        path = self.repo.project_dir(self.fixture.project_id) / 'project.json'
        stat = path.stat()
        payload = json.loads(path.read_text())
        payload['montajes'] = []
        path.write_text(json.dumps(payload))
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        with self.assertRaises(ApplicationError):
            self.job._validate_execution_target(self.context)

    def test_first_read_loads_project_once_and_does_not_publish(self):
        root = self.repo.project_dir(self.fixture.project_id)
        with patch.object(self.repo, 'load_project', wraps=self.repo.load_project) as load:
            self.job.get_plan(**self.args)
        self.assertEqual(load.call_count, 1)
        self.assertFalse((root / 'reports' / 'jobs').exists())

    def assert_rebuild(self, mutate):
        self.job.get_plan(**self.args)
        mutate()
        with patch.object(self.job, '_build_plan', wraps=self.job._build_plan) as build:
            self.job.get_plan(**self.args)
        self.assertEqual(build.call_count, 1)

    def test_source_change_even_with_same_mtime_rebuilds_and_reanalyzes(self):
        project = self.repo.load_project(self.fixture.project_id)
        op = project.operaciones[0]
        path = self.repo.project_dir(project.id) / op.archivo_gcode
        before = path.stat()
        def mutate():
            path.write_text('G21\nG90\nG1 X900 Y900 Z-1 F120\n')
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assert_rebuild(mutate)
        self.assertTrue(self.job.get_plan(**self.args)['operations'][0]['blocking'])

    def test_operation_order_settings_and_project_revision_invalidate(self):
        def mutate():
            project = self.repo.load_project(self.fixture.project_id)
            op = project.operaciones[0]
            self.repo.save_project(project.replace_operation(replace(op, max_z_error_mm=.017)))
        self.assert_rebuild(mutate)

    def test_map_content_and_reference_invalidate(self):
        op = self.repo.load_project(self.fixture.project_id).operaciones[0]
        def mutate():
            payload = self.fixture.physical_map_service.get_active(self.fixture.project_id, op.id)
            next(iter(payload['tool_references'].values())).pop('physical_reference_token')
            self.repo.save_height_map_payload(self.fixture.project_id, payload['map_id'], payload)
        self.assert_rebuild(mutate)
        self.assertNotEqual(self.job.get_plan(**self.args)['operations'][0]['reference_status'], 'LISTA')

    def test_runtime_session_change_invalidates(self):
        self.assert_rebuild(lambda: setattr(self.fixture.runtime, 'reference_serial_generation', 99))
        self.assertFalse(self.job.prepare_run(**self.args)['ready'])

    def test_analysis_version_change_invalidates(self):
        self.job.get_plan(**self.args)
        with patch('klipper_cnc_assistant.gcode.CURRENT_ANALYSIS_VERSION', 'future-version'), \
             patch.object(self.job, '_build_plan', wraps=self.job._build_plan) as build:
            self.job.get_plan(**self.args)
        self.assertEqual(build.call_count, 1)

    def test_artifact_bytes_participate_in_identity(self):
        folder = self.repo.project_dir(self.fixture.project_id) / 'generated' / 'compensated'
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / 'orphan.gcode'
        path.write_text('G1 X1\n')
        self.assert_rebuild(lambda: path.write_text('G1 X2\n'))

    def test_changed_dependencies_during_build_are_not_published(self):
        original = self.job._build_plan
        def build(*args, **kwargs):
            plan = original(*args, **kwargs)
            self.fixture.runtime.reference_serial_generation += 1
            return plan
        with patch.object(self.job, '_build_plan', side_effect=build):
            with self.assertRaisesRegex(ApplicationError, 'contexto cambió'):
                self.job.prepare_run(**self.args)
        self.assertIsNone(self.job._load_plan(self.context))

    def test_returned_plan_cannot_mutate_shared_cache(self):
        plan = self.job.get_plan(**self.args)
        expected = deepcopy(plan)
        plan['operations'][0]['reference_status'] = 'CORRUPT'
        self.assertEqual(self.job.get_plan(**self.args), expected)

    def test_analysis_reused_only_for_current_source_and_version(self):
        service = self.fixture.project_service
        op = self.repo.load_project(self.fixture.project_id).operaciones[0]
        text = self.repo.read_project_file(self.fixture.project_id, op.archivo_gcode)
        self.assertTrue(analysis_is_current(op, text))
        self.assertFalse(analysis_is_current(op, text + 'G1 X500\n'))
        stale = replace(op, analisis=replace(op.analisis, analysis_version='old'))
        self.assertFalse(analysis_is_current(stale, text))
        with patch('klipper_cnc_assistant.application.services.analyze_gcode_text') as analyze:
            service.analyze_operation(self.fixture.project_id, op.id)
        analyze.assert_not_called()
        with patch('klipper_cnc_assistant.application.compensated_gcode_service.analyze_gcode_text') as analyze:
            self.fixture.compensated_service.generate(self.fixture.project_id, op.id)
        analyze.assert_not_called()

    def test_current_crlf_analysis_is_reused_without_normalizing_its_hash(self):
        service = self.fixture.project_service
        op = self.repo.load_project(self.fixture.project_id).operaciones[0]
        service.upload_operation_gcode(project_id=self.fixture.project_id, operation_id=op.id,
            filename='windows.nc', content='G21\r\nG90\r\nG0 X10 Y10 F120\r\nG1 X20 Y10 Z-0.05 F120\r\n')
        service.analyze_operation(self.fixture.project_id, op.id)
        operation = self.repo.load_project(self.fixture.project_id).get_operation(op.id)
        text, source_hash = self.repo.read_gcode_source(self.fixture.project_id, operation.archivo_gcode)
        self.assertEqual(source_hash, operation.sha256)
        self.assertNotIn('\r', text)
        self.assertTrue(analysis_is_current(operation, text, source_sha256=source_hash))
        with patch('klipper_cnc_assistant.application.services.analyze_gcode_text') as analyze:
            service.analyze_operation(self.fixture.project_id, op.id)
        analyze.assert_not_called()
        with patch('klipper_cnc_assistant.application.compensated_gcode_service.analyze_gcode_text') as analyze:
            self.fixture.compensated_service.generate(self.fixture.project_id, op.id)
        analyze.assert_not_called()

    def test_material_change_invalidates_material_dependent_analysis(self):
        service = self.fixture.project_service
        project = service.get_project(self.fixture.project_id)
        service.update_project(project_id=project.id, nombre=project.nombre, ancho_mm=1, alto_mm=1)
        self.assertTrue(all(op.analisis is None for op in service.get_project(project.id).operaciones))

    def test_setup_patch_preserves_analyses_and_rejects_stale_writers(self):
        project = self.repo.load_project(self.fixture.project_id)
        setup = project.get_setup(self.fixture.setup_id)
        path = self.repo.project_dir(project.id) / 'project.json'
        before = json.loads(path.read_text())['operaciones']
        self.repo.save_setup(project, replace(setup, active_map_id='new-map'))
        self.assertEqual(json.loads(path.read_text())['operaciones'], before)
        with self.assertRaises(PersistenceConflict):
            self.repo.save_setup(project, replace(setup, active_map_id='stale-map'))
        self.assertEqual(self.repo.load_project(project.id).get_setup(setup.id).active_map_id, 'new-map')
