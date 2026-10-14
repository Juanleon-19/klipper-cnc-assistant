"""Preview/validate/arm API, using fake transports and canonical capture."""
from dataclasses import replace
import unittest
from tests import test_probe_and_capture


class MeshPreparationTest(unittest.TestCase):
    def setUp(self):
        self.capture = test_probe_and_capture.ProbeAndCaptureTest()
        self.capture.setUp()
        self.addCleanup(self.capture.doCleanups)
        f = self.capture.fixture
        self.client, self.repo, self.runtime = f.client, self.capture.service.repository, self.capture.runtime
        self.project_id, self.operation_id = self.capture.project_id, self.capture.operation_id
        f._upload_and_analyze_operation(self.project_id, self.operation_id, content='G21\nG90\nG0 X10 Y10\nG1 X20 Y10 Z-0.05 F120\n')
        self.assertEqual(self.client.post(self.capture.url + '/probe-and-capture').status_code, 200)
        self.base = f'/api/projects/{self.project_id}/operations/{self.operation_id}/physical-map'
        self.config = dict(rows=3, columns=3, safe_z_mm=5, edge_margin_left_mm=0, edge_margin_right_mm=0, edge_margin_bottom_mm=0, edge_margin_top_mm=0)

    def post(self, suffix, config=None):
        return self.client.post(self.base + suffix, json=config or self.config)

    def preview(self):
        result = self.post('/preview')
        self.assertEqual(result.status_code, 200, result.text)
        self.config['preview_fingerprint'] = result.json()['payload']['mesh_fingerprint']
        return result.json()['payload']

    def validate(self):
        self.preview()
        result = self.post('/validate')
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def test_preview_validate_arm_fingerprints_match(self):
        report = self.validate()
        self.assertTrue(report['coverage_valid'], report)
        self.assertEqual(report['operations_checked'], 1)
        self.assertGreater(report['segments_checked'], 0)
        result = self.post('/plan-from-reference')
        self.assertEqual(result.status_code, 200, result.text)
        payload = result.json()['payload']
        self.assertTrue(payload['armed'])
        self.assertEqual(payload['mesh_fingerprint'], report['mesh_fingerprint'])
        self.assertEqual(payload['preflight_validation']['mesh_fingerprint'], report['mesh_fingerprint'])
        self.assertEqual(self.capture.client.scripts, [])

    def test_arm_without_validation_blocks(self):
        self.preview()
        self.assertEqual(self.post('/plan-from-reference').status_code, 400)

    def test_changed_config_blocks_validation_and_arm(self):
        self.validate()
        self.config['rows'] = 4
        self.assertFalse(self.post('/validate').json()['coverage_valid'])
        self.assertEqual(self.post('/plan-from-reference').status_code, 400)

    def test_outside_toolpath_blocks(self):
        self.config['edge_margin_right_mm'] = 65
        report = self.validate()
        self.assertFalse(report['coverage_valid'])
        self.assertIn('TOOLPATH_OUTSIDE_SUPPORT', [i['code'] for i in report['blocking_issues']])
        self.assertEqual(self.post('/plan-from-reference').status_code, 400)

    def test_exclusion_between_endpoints_blocks(self):
        self.config['exclusions'] = [dict(id='hole', name='Hole', shape='rectangle', enabled=True, x_min_mm=14, x_max_mm=16, y_min_mm=9, y_max_mm=11)]
        report = self.validate()
        self.assertFalse(report['coverage_valid'])
        self.assertTrue(any('segmento cruza' in i['reason'] for i in report['blocking_issues']))
        self.assertEqual(self.post('/plan-from-reference').status_code, 400)

    def test_recovery_shown_before_arm_without_bypass(self):
        self.validate()
        self.runtime.physical_ownership.enter_recovery('fake uncertainty')
        result = self.post('/arm-readiness')
        self.assertEqual(result.status_code, 200, result.text)
        self.assertFalse(result.json()['can_arm'])
        self.assertEqual(result.json()['blocking_reasons'][0]['code'], 'PHYSICAL_RECOVERY_REQUIRED')
        self.assertEqual(self.post('/plan-from-reference').status_code, 400)

    def test_changed_source_blocks(self):
        self.validate()
        operation = self.repo.load_project(self.project_id).get_operation(self.operation_id)
        path = self.repo.project_dir(self.project_id) / operation.archivo_gcode
        path.write_text('G1 X100 Y100 Z-1 F120\n')
        self.assertEqual(self.post('/plan-from-reference').status_code, 400)
        report = self.post('/validate').json()
        self.assertFalse(report['coverage_valid'])

    def test_safe_z_and_machine_points_respect_limits(self):
        self.config['safe_z_mm'] = 500
        report = self.validate()
        self.assertFalse(report['coverage_valid'])
        self.assertIn('SAFE_Z_OUTSIDE_LIMITS', [i['code'] for i in report['blocking_issues']])

    def test_circle_crossing_between_vertices_blocks_arm(self):
        self.config['exclusions'] = [dict(id='circle', name='Hole', shape='circle', enabled=True, center_x_mm=15, center_y_mm=10, radius_mm=1)]
        report = self.validate()
        self.assertFalse(report['coverage_valid'])
        self.assertTrue(any('segmento cruza' in i['reason'] for i in report['blocking_issues']))
        self.assertEqual(self.post('/plan-from-reference').status_code, 400)

    def test_missing_analysis_of_another_operation_blocks(self):
        project = self.repo.load_project(self.project_id)
        operation = project.get_operation(self.operation_id)
        self.repo.save_project(replace(project, operaciones=(*project.operaciones, replace(operation, id='unanalysed', orden=99, analisis=None, archivo_gcode=None))))
        report = self.validate()
        self.assertFalse(report['coverage_valid'])
        self.assertEqual(report['operations_checked'], 2)
        self.assertTrue(any(i.get('operation_id') == 'unanalysed' for i in report['blocking_issues']))

    def test_arm_inspection_is_read_only(self):
        self.validate()
        root = self.repo.project_dir(self.project_id)
        def snapshot():
            return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
        before = snapshot()
        owner = self.runtime.physical_ownership.snapshot()
        self.assertTrue(self.post('/arm-readiness').json()['can_arm'])
        self.assertEqual(snapshot(), before)
        self.assertEqual(self.runtime.physical_ownership.snapshot(), owner)

    def test_runtime_ownership_blocks_arm_without_mutation(self):
        from klipper_cnc_assistant.machine.physical_ownership import OwnerKind
        self.validate()
        permit = self.runtime.physical_ownership.acquire(OwnerKind.MESH, 'another-worker')
        try:
            self.assertEqual(self.post('/plan-from-reference').status_code, 400)
            self.assertIn('PHYSICAL_OWNER_ACTIVE', [r['code'] for r in self.post('/arm-readiness').json()['blocking_reasons']])
        finally:
            self.runtime.physical_ownership.release(permit, quiescent=True)

    def test_probe_profile_changes_invalidate_validation(self):
        self.validate()
        self.config.update(probe_profile_source='map_override', probe_step_mm=.05, probe_feed_mm_min=40, retract_mm=1)
        self.assertEqual(self.post('/plan-from-reference').status_code, 400)
