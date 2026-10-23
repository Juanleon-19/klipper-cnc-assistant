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

    def test_local_preview_without_reference_has_one_physical_blocker(self):
        project = self.repo.load_project(self.project_id)
        setup = project.setup_for_operation(self.operation_id)
        self.repo.save_project(project.replace_setup(replace(setup, preparacion=replace(
            setup.preparacion, origen_trabajo=None, referencia_z=None, physical_reference=None))))
        report = self.validate()
        self.assertTrue(report['local_coverage_valid'])
        self.assertFalse(report['physical_limits_valid'])
        self.assertFalse(report['coverage_valid'])
        self.assertEqual([i['code'] for i in report['blocking_issues']], ['TRANSFORM_UNAVAILABLE'])
        self.assertEqual(self.post('/plan-from-reference').status_code, 400)
        self.assertEqual(self.capture.client.scripts, [])

    def test_stale_physical_context_does_not_masquerade_as_local_geometry_error(self):
        from klipper_cnc_assistant.machine.state import AxisLimits
        self.runtime._machine.x_limits = AxisLimits(0, 50)
        report = self.validate()
        self.assertTrue(report['local_coverage_valid'])
        self.assertFalse(report['physical_limits_valid'])
        self.assertEqual([i['code'] for i in report['blocking_issues']], ['REFERENCE_INVALID'])
        self.assertEqual(self.post('/plan-from-reference').status_code, 400)

    def test_retract_outside_travel_blocks_with_valid_reference(self):
        self.config.update(probe_profile_source='map_override', probe_step_mm=.05,
                           probe_feed_mm_min=40, retract_mm=500)
        report = self.validate()
        self.assertFalse(report['physical_limits_valid'])
        self.assertIn('RETRACT_OUTSIDE_LIMITS', [i['code'] for i in report['blocking_issues']])
        self.assertEqual(self.post('/plan-from-reference').status_code, 400)

    def test_real_transformed_point_outside_reports_coordinate_and_limit(self):
        from klipper_cnc_assistant.machine.state import AxisLimits
        self.runtime._machine.x_limits = AxisLimits(0, 50)
        self.assertEqual(self.client.post(self.capture.url + '/probe-and-capture').status_code, 200)
        report = self.validate()
        outside = [i for i in report['blocking_issues'] if i['code'] == 'MESH_POINT_OUTSIDE_LIMITS']
        self.assertTrue(outside, report)
        self.assertEqual(outside[0]['limit_max_mm'], 50)
        self.assertGreater(outside[0]['coordinate_mm'], 50)
        self.assertEqual(self.post('/plan-from-reference').status_code, 400)

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

    def test_uncompensated_rapid_outside_map_matches_execution_coverage_and_arms(self):
        from copy import deepcopy
        from klipper_cnc_assistant.heightmap.coverage import build_coverage_report, segment_uses_surface_map
        self.config.update(edge_margin_left_mm=5, edge_margin_bottom_mm=5)
        self.capture.fixture._upload_and_analyze_operation(self.project_id, self.operation_id,
            content='G21\nG90\nG0 X0 Y0 Z2\nG0 X10 Y10\nG1 Z-0.05 F120\nG1 X20 Y10\n')
        report = self.validate()
        self.assertTrue(report['coverage_valid'], report)
        preview = deepcopy(self.preview())
        for p in preview['points']:
            if p['status'] != 'EXCLUDED': p.update(status='MEASURED', z_measured=0., delta_z=0.)
        maps = self.capture.fixture.app.state.physical_map_service
        hm = maps.height_map_from_payload(maps._height_map_payload_from_points(preview))
        op = self.repo.load_project(self.project_id).get_operation(self.operation_id)
        execution = build_coverage_report(height_map=hm, operations=((op.id, op.nombre, op.analisis),))
        self.assertEqual(len(execution.issues), 0)
        self.assertEqual(report['segments_checked'], sum(map(segment_uses_surface_map, op.analisis.segmentos_vista_previa)))
        self.assertEqual(self.post('/plan-from-reference').status_code, 200)

    def test_cut_crosses_unsupported_cell_without_touching_exclusion(self):
        self.config['exclusions'] = [dict(id='corner', name='Missing corner', shape='rectangle', enabled=True,
            x_min_mm=39, x_max_mm=41, y_min_mm=24, y_max_mm=26)]
        report = self.validate()
        self.assertFalse(report['coverage_valid'])
        self.assertTrue(any('celda con esquina' in i['reason'] for i in report['blocking_issues']), report)
        self.assertEqual(self.post('/plan-from-reference').status_code, 400)

    def project_bytes(self):
        root = self.repo.project_dir(self.project_id)
        return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}

    def test_owner_acquired_after_readiness_returns_400_without_writes(self):
        from unittest.mock import patch
        from klipper_cnc_assistant.machine.physical_ownership import OwnerKind
        self.validate(); before = self.project_bytes()
        owner = self.runtime.physical_ownership
        acquire = owner.acquire
        def race(*args, **kwargs):
            acquire(OwnerKind.RUNTIME_MOTION, 'other-actor')
            return acquire(*args, **kwargs)
        with patch.object(owner, 'acquire', side_effect=race):
            response = self.post('/plan-from-reference')
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn('Verifique reposo/recuperación', response.text)
        self.assertEqual(before, self.project_bytes())
        self.assertEqual(self.capture.client.scripts, [])
        self.assertEqual(owner.snapshot()['owner_id'], 'other-actor')

    def test_ownership_revision_race_even_if_free_again_blocks_arm(self):
        from unittest.mock import patch
        from klipper_cnc_assistant.machine.physical_ownership import OwnerKind
        self.validate(); before = self.project_bytes()
        owner = self.runtime.physical_ownership; acquire = owner.acquire
        def race(*args, **kwargs):
            permit = acquire(OwnerKind.RUNTIME_MOTION, 'other-actor')
            owner.release(permit, quiescent=True)
            return acquire(*args, **kwargs)
        with patch.object(owner, 'acquire', side_effect=race):
            response = self.post('/plan-from-reference')
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(before, self.project_bytes())

    def test_recovery_during_arm_disarms_and_closes_producer(self):
        from unittest.mock import patch
        self.validate()
        maps = self.capture.fixture.app.state.physical_map_service
        original = maps.plan_from_saved_reference
        def race(**kwargs):
            payload = original(**kwargs)
            self.runtime.physical_ownership.enter_recovery('test recovery during arm')
            return payload
        with patch.object(maps, 'plan_from_saved_reference', side_effect=race):
            response = self.post('/plan-from-reference')
        self.assertEqual(response.status_code, 400, response.text)
        self.assertFalse(maps.get_active(self.project_id, self.operation_id).get('armed'))
        owner = self.runtime.physical_ownership.snapshot()
        self.assertTrue(owner['recovery_pending'])
        self.assertFalse(owner['producer_active'])
        self.assertEqual(self.capture.client.scripts, [])

    def test_release_failure_returns_400_and_disarms_without_rewriting_evidence(self):
        from unittest.mock import patch
        from klipper_cnc_assistant.machine.physical_ownership import OwnershipError
        self.validate()
        evidence = self.repo.load_project(self.project_id).get_setup('setup-main').preparacion.physical_reference
        with patch.object(self.runtime.physical_ownership, 'release', side_effect=OwnershipError('test cleanup failure')):
            response = self.post('/plan-from-reference')
        self.assertEqual(response.status_code, 400, response.text)
        maps = self.capture.fixture.app.state.physical_map_service
        self.assertFalse(maps.get_active(self.project_id, self.operation_id)['armed'])
        self.assertEqual(evidence, self.repo.load_project(self.project_id).get_setup('setup-main').preparacion.physical_reference)
        self.assertTrue(self.runtime.physical_ownership.snapshot()['recovery_pending'])
        self.assertFalse(self.runtime.physical_ownership.snapshot()['producer_active'])
        self.assertEqual(self.capture.client.scripts, [])

    def test_release_failure_does_not_mask_original_application_error(self):
        from unittest.mock import patch
        from klipper_cnc_assistant.application.errors import ApplicationError
        from klipper_cnc_assistant.machine.physical_ownership import OwnershipError
        self.validate()
        maps = self.capture.fixture.app.state.physical_map_service
        with patch.object(maps, 'plan_from_saved_reference', side_effect=ApplicationError('original failure')), \
             patch.object(self.runtime.physical_ownership, 'release', side_effect=OwnershipError('cleanup failure')):
            response = self.post('/plan-from-reference')
        self.assertEqual(response.status_code, 400)
        self.assertIn('original failure', response.text)
        self.assertTrue(self.runtime.physical_ownership.snapshot()['recovery_pending'])

    def test_rebuild_ownership_race_returns_400_without_writes(self):
        from unittest.mock import patch
        self.validate()
        payload = self.post('/plan-from-reference').json()['payload']
        before = self.project_bytes()
        owner = self.runtime.physical_ownership; acquire = owner.acquire
        def race(*args, **kwargs):
            owner.enter_recovery('test race at rebuild')
            return acquire(*args, **kwargs)
        with patch.object(owner, 'acquire', side_effect=race):
            response = self.client.post(f"/api/projects/{self.project_id}/physical-maps/{payload['map_id']}/repeat")
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(before, self.project_bytes())
        self.assertEqual(self.capture.client.scripts, [])

    def test_rebuild_cleanup_failure_is_controlled_and_new_version_unarmed(self):
        from unittest.mock import patch
        from klipper_cnc_assistant.machine.physical_ownership import OwnershipError
        self.validate()
        payload = self.post('/plan-from-reference').json()['payload']
        with patch.object(self.runtime.physical_ownership, 'release', side_effect=OwnershipError('cleanup failure')):
            response = self.client.post(f"/api/projects/{self.project_id}/physical-maps/{payload['map_id']}/repeat")
        self.assertEqual(response.status_code, 400, response.text)
        maps = self.capture.fixture.app.state.physical_map_service
        self.assertFalse(maps.get_active(self.project_id, self.operation_id)['armed'])
        self.assertTrue(self.runtime.physical_ownership.snapshot()['recovery_pending'])
        self.assertFalse(self.runtime.physical_ownership.snapshot()['producer_active'])
        self.assertEqual(self.capture.client.scripts, [])

    def test_runtime_status_exposes_current_ownership_revision_read_only(self):
        from klipper_cnc_assistant.machine.physical_ownership import OwnerKind
        owner = self.runtime.physical_ownership
        before = owner.snapshot()
        self.assertEqual(self.client.get('/api/machine/status').json()['physical_owner'], before)
        permit = owner.acquire(OwnerKind.MESH, 'test-owner')
        try:
            current = self.client.get('/api/machine/status').json()['physical_owner']
            self.assertEqual(current['owner_id'], 'test-owner')
            self.assertGreater(current['revision'], before['revision'])
            self.assertEqual(current, owner.snapshot())
        finally:
            owner.release(permit, quiescent=True)
