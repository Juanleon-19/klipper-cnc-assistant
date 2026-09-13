import hashlib
import json
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from klipper_cnc_assistant.domain import MaterialBruto, ProyectoPCB
from klipper_cnc_assistant.storage import JsonProjectRepository
from klipper_cnc_assistant.storage.safe_persistence import (
    atomic_json, patch_metadata, PersistenceConflict, validate_artifact,
    validate_generation,
)
from tests import test_job_service as job_fixtures
from klipper_cnc_assistant.storage import safe_persistence


class SafePersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.repo = JsonProjectRepository(self.root)
        self.project = self.repo.save_project(ProyectoPCB(
            id='project-test', nombre='PCB', material=MaterialBruto(100, 100, 1.6)))

    def test_atomic_failure_preserves_destination_and_cleans_temp(self):
        path = self.root / 'critical.json'
        atomic_json(path, {'previous': True}, durable=True)
        with patch('klipper_cnc_assistant.storage.safe_persistence.os.fsync', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                atomic_json(path, {'new': True}, durable=True)
        self.assertEqual(json.loads(path.read_text()), {'previous': True})
        self.assertEqual(list(self.root.glob('.critical.json-*.tmp')), [])

    def _concurrent(self, first, second):
        errors = []
        first_done = threading.Event()
        def a():
            try:
                first()
            except Exception as error:
                errors.append(error)
            finally:
                first_done.set()
        def b():
            if not first_done.wait(5):
                errors.append(TimeoutError())
                return
            try:
                second()
            except Exception as error:
                errors.append(error)
        threads = [threading.Thread(target=a), threading.Thread(target=b)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])

    def test_stale_project_writers_preserve_independent_changes(self):
        a = self.repo.load_project(self.project.id)
        other_repo = JsonProjectRepository(self.root)
        b = other_repo.load_project(self.project.id)
        setup = replace(a.montajes[0], active_reference_id='measured-reference')
        self._concurrent(lambda: self.repo.save_project(a.replace_setup(setup)),
                         lambda: other_repo.save_project(replace(b, nombre='renamed')))
        current = self.repo.load_project(a.id)
        self.assertEqual(current.nombre, 'renamed')
        self.assertEqual(current.montajes[0].active_reference_id, 'measured-reference')
        self.assertEqual(current.storage_revision, 3)

    def test_same_project_field_conflict_rejected(self):
        a = self.repo.load_project(self.project.id)
        b = self.repo.load_project(self.project.id)
        self.repo.save_project(replace(a, nombre='first'))
        with self.assertRaises(PersistenceConflict):
            self.repo.save_project(replace(b, nombre='second'))
        self.assertEqual(self.repo.load_project(a.id).nombre, 'first')

    def test_map_updates_and_physical_token_survive_stale_writer(self):
        token = {'physical_session_id': 'real-session', 'reference_revision': 'new'}
        self.repo.save_height_map_payload(self.project.id, 'map', {'tool_references': {}, 'height_map': {'samples': []}})
        a = self.repo.load_height_map_payload(self.project.id, 'map')
        b = self.repo.load_height_map_payload(self.project.id, 'map')
        a['tool_references']['tool'] = {'valid': True, 'physical_reference_token': token}
        b['height_map']['samples'] = [{'x': 1, 'z': 2}]
        self._concurrent(lambda: self.repo.save_height_map_payload(self.project.id, 'map', a),
                         lambda: self.repo.save_height_map_payload(self.project.id, 'map', b))
        current = self.repo.load_height_map_payload(self.project.id, 'map')
        self.assertEqual(current['height_map']['samples'], [{'x': 1, 'z': 2}])
        self.assertEqual(current['tool_references']['tool']['physical_reference_token'], token)

    def test_legacy_project_and_map_normalize_on_first_write(self):
        path = self.repo.project_dir(self.project.id) / 'project.json'
        legacy = json.loads(path.read_text())
        legacy.pop('storage_revision')
        atomic_json(path, legacy)
        loaded = self.repo.load_project(self.project.id)
        self.assertEqual(loaded.storage_revision, 0)
        self.assertEqual(self.repo.save_project(replace(loaded, nombre='legacy updated')).storage_revision, 1)
        map_path = self.repo._height_map_file(self.project.id, 'legacy')
        atomic_json(map_path, {'valid': True, 'height_map': {}})
        payload = self.repo.load_height_map_payload(self.project.id, 'legacy')
        self.assertNotIn('physical_reference_token', payload)
        self.repo.save_height_map_payload(self.project.id, 'legacy', payload)
        self.assertEqual(payload['storage_revision'], 1)

    def test_concurrent_metadata_patches_preserve_identity(self):
        path = self.root / 'metadata.json'
        identity = {'generated_hash': 'hash', 'physical_reference_token': {'session': 'current'},
                    'provenance': {'source': 'original'}, 'generation_id': 'latest'}
        atomic_json(path, identity)
        self._concurrent(lambda: patch_metadata(path, {'time_estimate': {'seconds': 12}}),
                         lambda: patch_metadata(path, {'enrichment': {'analysis': 'done'}}))
        result = json.loads(path.read_text())
        self.assertEqual(result['time_estimate']['seconds'], 12)
        self.assertEqual(result['enrichment']['analysis'], 'done')
        for key, value in identity.items():
            self.assertEqual(result[key], value)
        with self.assertRaises(PersistenceConflict):
            patch_metadata(path, {'physical_reference_token': None})

    def test_metadata_writer_rereads_after_waiting_for_other_writer(self):
        path = self.root / 'metadata.json'
        atomic_json(path, {'generated_hash': 'immutable', 'enrichment': {}})
        first_writing, release_first, second_attempt = (threading.Event() for _ in range(3))
        errors = []
        real_lock, real_write = safe_persistence.storage_lock, safe_persistence.atomic_json
        @contextmanager
        def traced_lock(target):
            if threading.current_thread().name == 'patch-b':
                second_attempt.set()
            with real_lock(target):
                yield
        def blocked_write(target, payload, **kwargs):
            if threading.current_thread().name == 'patch-a':
                first_writing.set()
                if not release_first.wait(5):
                    raise TimeoutError('test publication barrier')
            real_write(target, payload, **kwargs)
        def writer(field):
            try:
                patch_metadata(path, {'enrichment': {'analysis': {field: True}}})
            except Exception as error:
                errors.append(error)
        a = threading.Thread(target=writer, args=('a',), name='patch-a')
        b = threading.Thread(target=writer, args=('b',), name='patch-b')
        with patch.object(safe_persistence, 'storage_lock', traced_lock), patch.object(safe_persistence, 'atomic_json', blocked_write):
            a.start()
            try:
                self.assertTrue(first_writing.wait(5))
                b.start()
                self.assertTrue(second_attempt.wait(5))
                self.assertEqual(json.loads(path.read_text())['enrichment'], {})
            finally:
                release_first.set()
                a.join(5)
                if b.ident is not None:
                    b.join(5)
        self.assertFalse(a.is_alive())
        self.assertFalse(b.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(json.loads(path.read_text())['enrichment']['analysis'], {'a': True, 'b': True})

    def test_tokens_cannot_be_merged_into_a_synthetic_identity(self):
        self.repo.save_height_map_payload(self.project.id, 'map', {
            'tool': {'physical_reference_token': {'session': 'old', 'reference': 'old'}}})
        a = self.repo.load_height_map_payload(self.project.id, 'map')
        b = self.repo.load_height_map_payload(self.project.id, 'map')
        a['tool']['physical_reference_token']['session'] = 'new'
        b['tool']['physical_reference_token']['reference'] = 'new'
        self.repo.save_height_map_payload(self.project.id, 'map', a)
        with self.assertRaises(PersistenceConflict):
            self.repo.save_height_map_payload(self.project.id, 'map', b)
        self.assertEqual(self.repo.load_height_map_payload(self.project.id, 'map')['tool']['physical_reference_token'],
                         {'session': 'new', 'reference': 'old'})

    def test_artifact_hash_mismatch_rejected(self):
        path = self.root / 'compensated.gcode'
        from klipper_cnc_assistant.storage.safe_persistence import atomic_write
        atomic_write(path, 'G1 X10 F300\n')
        with self.assertRaises(PersistenceConflict):
            validate_artifact(path, {'generated_hash': hashlib.sha256(b'other file').hexdigest()})
        validate_artifact(path, {'generated_hash': hashlib.sha256(path.read_bytes()).hexdigest()})

    def test_generation_mismatch_and_legacy_rejected_matching_allowed(self):
        plan = {'plan_id': 'job', 'generation_id': 'A'}
        with self.assertRaises(PersistenceConflict):
            validate_generation(plan, {'plan_id': 'job', 'generation_id': 'B'})
        with self.assertRaises(PersistenceConflict):
            validate_generation({'plan_id': 'legacy'}, {'plan_id': 'legacy'})
        validate_generation(plan, dict(plan))

    def test_job_service_reader_rebuilds_mismatched_generation(self):
        fixture = job_fixtures.JobServiceTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        context = fixture.job_service._context(fixture.project.id, fixture.setup_id, 'superior')
        plan = fixture.job_service.create_plan(project_id=fixture.project.id, setup_id=fixture.setup_id, face='superior')
        manifest_path = fixture.job_service._plan_dir(context) / 'job_manifest.json'
        manifest = json.loads(manifest_path.read_text())
        validate_generation(plan, manifest)
        self.assertIsNotNone(fixture.job_service._load_plan(context))
        manifest['generation_id'] = 'another-generation'
        atomic_json(manifest_path, manifest)
        self.assertIsNone(fixture.job_service._load_plan(context))

    def test_interrupted_plan_publication_is_not_accepted(self):
        fixture = job_fixtures.JobServiceTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        service = fixture.job_service
        context = service._context(fixture.project_id, fixture.setup_id, 'superior')
        service.create_plan(project_id=fixture.project_id, setup_id=fixture.setup_id, face='superior')
        from klipper_cnc_assistant.execution import job_service
        original = job_service.atomic_write
        def fail_plan(path, content, **kwargs):
            if Path(path).name == 'job_plan.json':
                raise OSError('publication interrupted after manifest')
            original(path, content, **kwargs)
        with patch.object(job_service, 'atomic_write', fail_plan):
            with self.assertRaises(OSError):
                service.create_plan(project_id=fixture.project_id, setup_id=fixture.setup_id, face='superior')
        self.assertIsNone(service._load_plan(context))

    def test_real_artifact_consumer_rejects_mismatched_metadata(self):
        fixture = job_fixtures.JobServiceTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        operation = fixture.repository.load_project(fixture.project_id).operations_for_setup(fixture.setup_id)[0]
        generated = fixture.compensated_service.generate(fixture.project_id, operation.id)
        path = fixture.repository.project_dir(fixture.project_id) / generated['relative_path']
        active_map = fixture.physical_map_service.get_active(fixture.project_id, operation.id)
        self.assertTrue(fixture.job_service._generated_artifact_is_current(
            fixture.project_id, operation, active_map, path, generated['metadata']))
        safe_persistence.atomic_write(path, 'G1 X999 F300\n')
        self.assertFalse(fixture.job_service._generated_artifact_is_current(
            fixture.project_id, operation, active_map, path, generated['metadata']))
        with self.assertRaises(PersistenceConflict):
            fixture.compensated_service.resolve_generated_file(fixture.project_id, generated['relative_path'])

    def test_fresh_jit_validates_published_metadata_not_only_returned_snapshot(self):
        fixture = job_fixtures.JobServiceTest()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        operation = fixture.repository.load_project(fixture.project_id).operations_for_setup(fixture.setup_id)[0]
        generated = fixture.compensated_service.generate(fixture.project_id, operation.id)
        context = fixture.job_service._context(fixture.project_id, fixture.setup_id, 'superior')
        fixture.job_service._validate_generated_pair(context, generated)
        metadata_path = fixture.repository.project_dir(fixture.project_id) / generated['metadata_path']
        metadata = json.loads(metadata_path.read_text())
        metadata['physical_reference_token'] = None
        atomic_json(metadata_path, metadata)
        with self.assertRaises(PersistenceConflict):
            fixture.job_service._validate_generated_pair(context, generated)
