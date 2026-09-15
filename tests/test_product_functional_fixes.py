"""Analytical contracts for the Astra corrections; synthetic inputs only."""
import math
import tempfile
import unittest
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.test_product_numerics import surface, legacy
from klipper_cnc_assistant.application.compensated_gcode_service import CompensatedGCodeService, ReferenceFrame
from klipper_cnc_assistant.application.adaptive_compensation import generate_adaptive_gcode
from klipper_cnc_assistant.application.time_estimation_service import TimeEstimationService
from klipper_cnc_assistant.application.errors import ApplicationError
from klipper_cnc_assistant.gcode import analyze_gcode_text
from klipper_cnc_assistant.gcode.models import ModalState
from klipper_cnc_assistant.gcode.tokenizer import tokenize_gcode
from klipper_cnc_assistant.application.time_estimation_service import _parse_line
from klipper_cnc_assistant.heightmap.compensation import build_compensation_preview
from klipper_cnc_assistant.storage import safe_persistence as store


class RampAndPreviewContracts(unittest.TestCase):
    def assert_ramp(self, start, end, fn, relative=False, motion='G1'):
        x0,y0,z0 = start; x1,y1,z1 = end
        mode='G91' if relative else 'G90'
        coords=(x1-x0,y1-y0,z1-z0) if relative else end
        text=f'G21\nG90\nG0 X{x0} Y{y0} Z{z0}\n{mode}\n{motion} X{coords[0]} Y{coords[1]} Z{coords[2]} F123.456\n'
        m=surface(fn,bounds=(-10,-10,20,20))
        lines,generated=legacy(text,m,.25)
        pts=[p for p in generated['trace'] if p['line_number']==5]
        self.assertGreater(len(pts),1)
        length=math.hypot(x1-x0,y1-y0)
        for p in pts:
            t=math.hypot(p['pcb_x_mm']-x0,p['pcb_y_mm']-y0)/length
            expected_programmed=z0+t*(z1-z0)
            self.assertAlmostEqual(p['programmed_z_mm'],expected_programmed,delta=2e-14)
            delta=fn(p['pcb_x_mm'],p['pcb_y_mm']) if motion=='G1' and z1<0 else 0
            self.assertAlmostEqual(p['final_z_mm'],3+delta+expected_programmed,delta=2e-14)
            self.assertEqual(p['feed_mm_min'],123.456)
        self.assertEqual((pts[-1]['pcb_x_mm'],pts[-1]['pcb_y_mm']),end[:2])
        self.assertAlmostEqual(pts[-1]['programmed_z_mm'],z1,delta=2e-14)
        self.assertEqual(pts[-1]['programmed_z_mm'],analyze_gcode_text(text).segmentos_vista_previa[-1].z_mm)
        # Actual formatted G-code, not just metadata, must obey the same contract.
        emitted=analyze_gcode_text('\n'.join(lines)).segmentos_vista_previa
        for seg,p in zip(emitted,generated['trace']):
            self.assertAlmostEqual(seg.z_mm,p['final_z_mm'],delta=5.01e-6)
        return pts

    def test_descending_ramp_former_point_nine_mm_error(self):
        pts=self.assert_ramp((0,0,-.1),(10,0,-1.1),lambda x,y:0)
        p=next(p for p in pts if p['pcb_x_mm']==1)
        self.assertAlmostEqual(p['programmed_z_mm'],-.2,delta=1e-14)

    def test_ascending_xy_z_ramp_constant_map(self):
        self.assert_ramp((-4,-3,-1.1),(12,9,-.1),lambda x,y:.2)

    def test_descending_relative_ramp_sloped_map(self):
        self.assert_ramp((12,9,-.1),(-4,-3,-1.1),lambda x,y:.01*x+.02*y,True)

    def test_ascending_relative_ramp_sloped_map(self):
        self.assert_ramp((-4,-3,-1.1),(12,9,-.1),lambda x,y:-.02*x+.03*y,True)

    def test_g0_and_positive_auxiliary_ramps_keep_programmed_z(self):
        for motion in ('G0','G1'):
            for relative in (False,True):
                with self.subTest(motion=motion,relative=relative):
                    self.assert_ramp((-4,-3,2),(12,9,5),lambda x,y:.2,relative,motion)

    def test_pure_z_and_horizontal_moves_keep_endpoints(self):
        _,p=legacy('G21\nG90\nG1 Z-.1 F120\nG1 X4\nG1 Z-.7 F60\n')
        self.assertEqual(sum(p['line_number']==5 for p in p['trace']),1)
        self.assertAlmostEqual(p['trace'][-1]['programmed_z_mm'],-.7)
        self.assertEqual(p['trace'][-1]['feed_mm_min'],60)

    def test_preview_equals_generated_at_points_edges_and_cell_crossings(self):
        text='G21\nG90\nG0 X-4 Y-3\nG1 Z-.1 F120\nG1 X12 Z-1.1\nG1 Y9 Z-.1\nG1 X-4\nG1 Y-3\nG1 Z2\n'
        m=surface(lambda x,y:.01*x-.02*y)
        preview=build_compensation_preview(analysis=analyze_gcode_text(text),height_map=m,reference_z_mm=3)
        _,generated=legacy(text,m,preview['paso_muestreo_virtual_mm'])
        points={(seg['numero_linea'],p['x_mm'],p['y_mm']):p for seg in preview['segmentos'] for p in seg['puntos'][1:]}
        for p in generated['trace']:
            shown=points[p['line_number'],p['pcb_x_mm'],p['pcb_y_mm']]
            self.assertAlmostEqual(shown['z_compensada_mm'],p['final_z_mm'],delta=1e-14)
            self.assertEqual(shown['z_original_mm'],p['programmed_z_mm'])

    def test_helices_both_directions_and_modal_z_rejected_with_line(self):
        for motion in ('G2','G3'):
            for mode,z in (('G90','-.2'),('G91','-.1')):
                text=f'G21\n{mode}\nG1 Z-.1 F120\n{motion} X10 I5 J0 Z{z}\n'
                a=analyze_gcode_text(text)
                self.assertTrue(a.analisis_incompleto)
                self.assertIn('helicoidal',a.incidencias[-1].mensaje)
                with self.assertRaisesRegex(ApplicationError,f'Línea 4, {motion}.*helicoidal') as cm:
                    legacy(text)
                self.assertIn('No se generó G-code compensado',str(cm.exception))

    def test_planar_ij_arcs_keep_direction_feed_and_endpoint(self):
        for motion,ysign in (('G2',1),('G3',-1)):
            text=f'G21\nG90\nG1 Z-.1 F120\n{motion} X10 I5 J0\n'
            _,p=legacy(text,surface(lambda x,y:.2,bounds=(-10,-10,20,20)))
            arc=[p for p in p['trace'] if p['line_number']==4]
            self.assertEqual((arc[-1]['pcb_x_mm'],arc[-1]['pcb_y_mm']),(10,0))
            self.assertTrue(any(p['pcb_y_mm']*ysign>1 for p in arc))
            self.assertTrue(all(p['feed_mm_min']==120 for p in arc))
            self.assertTrue(all(abs(p['final_z_mm']-3.1)<1e-14 for p in arc))


class UnitRegimeContracts(unittest.TestCase):
    def adaptive(self,text):
        return generate_adaptive_gcode(original_text=text,height_map=surface(lambda x,y:0,bounds=(-30,-30,30,30)),
            reference_frame=ReferenceFrame(0,0,3),max_z_error_mm=.01,operation_id='test',operation_name='test',min_segment_length_mm=.05)

    def test_changes_after_geometry_rejected_across_pipeline(self):
        for first,second in (('G21','G20'),('G20','G21')):
            for block in (second, f'G1 X.2 F20 {second}',f'{second} G1 X.2 F20'):
                text=f'{first}\nG1 X.1 Z-.1 F10\n{block}\n'
                with self.subTest(text=text):
                    a=analyze_gcode_text(text)
                    self.assertTrue(a.tiene_errores_criticos)
                    self.assertEqual(a.incidencias[0].linea,3)
                    self.assertEqual(a.incidencias[0].comando,second)
                    for run in (lambda:legacy(text),lambda:self.adaptive(text),
                                lambda:TimeEstimationService._estimate_internal(None,text)):
                        with self.assertRaisesRegex(ApplicationError,'cambio de unidades'):
                            run()

    def test_block_unit_word_order_preamble_supported_every_parser(self):
        for code,scale in (('G20',25.4),('G21',1.0)):
            for text in (f'{code}\nG1 X.1 Z-.01 F10\n',f'G1 X.1 Z-.01 F10 {code}\n'):
                a=analyze_gcode_text(text);self.assertFalse(a.tiene_errores_criticos)
                self.assertAlmostEqual(a.segmentos_vista_previa[-1].fin_x_mm,.1*scale)
                self.assertAlmostEqual(a.avances_mm_min[0],10*scale)
                state=ModalState();segments=[_parse_line(line=line,state=state)['segment'] for line in tokenize_gcode(text)]
                self.assertAlmostEqual(state.x_mm,.1*scale)
                self.assertTrue(any(segments))
                output=self.adaptive(text)['output']
                parsed=analyze_gcode_text(output)
                self.assertFalse(parsed.analisis_incompleto)
                self.assertAlmostEqual(parsed.segmentos_vista_previa[-1].fin_x_mm,.1*scale,delta=.0002)

    def test_repeated_same_units_allowed_but_conflicting_block_rejected(self):
        for code in ('G20','G21'):
            legacy(f'{code}\nG1 Z-.01 F10\nG1 X.1 {code}\n')
        with self.assertRaisesRegex(ApplicationError,'contradictorias'):
            legacy('G20 G21\nG1 Z-.1 F10\n')


class SnapshotRetentionContracts(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        for key,value in (('_snapshots',OrderedDict()),('_snapshot_lru',OrderedDict()),('_snapshot_bytes',0),('_snapshot_peak_count',0),('_active_paths',{})):
            p=patch.object(store,key,value);p.start();self.addCleanup(p.stop)

    def remember(self,index,revision=0,size=0):
        path=self.root/f'{index}.json';payload={'storage_revision':revision,'a':0,'b':0,'data':'x'*size}
        store.atomic_json(path,payload);store.remember_snapshot(path,payload)
        return path,payload

    def test_stress_global_count_is_bounded_across_threads(self):
        def worker(index):
            self.remember(index)
            with store._registry_guard:
                self.assertLessEqual(len(store._snapshot_lru),store.SNAPSHOT_MAX_COUNT)
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(worker,range(store.SNAPSHOT_MAX_COUNT*2)))
        self.assertEqual(store.snapshot_stats()['count'],store.SNAPSHOT_MAX_COUNT)
        self.assertEqual(store.snapshot_stats()['peak_count'],store.SNAPSHOT_MAX_COUNT)

    def test_actual_payload_bytes_and_per_path_history_bounded(self):
        with patch.object(store,'SNAPSHOT_MAX_BYTES',4096):
            for i in range(80):self.remember(i,size=500)
            self.assertLessEqual(store.snapshot_stats()['bytes'],4096)
        for revision in range(100):self.remember('history',revision)
        self.assertEqual(len(store._snapshots[str(self.root/'history.json')]),store.SNAPSHOT_MAX_PER_PATH)

    def test_deleted_resources_release_entries(self):
        for i in range(100):
            path,_=self.remember(i);path.unlink();store.forget_snapshots(path)
        self.assertEqual(store.snapshot_stats()['count'],0)
        for i in range(30):self.remember(i)
        for path in self.root.glob('*.json'):path.unlink()
        store.prune_snapshots()
        self.assertEqual(store.snapshot_stats()['count'],0)
        self.assertEqual(store.snapshot_stats()['bytes'],0)

    def test_active_transaction_base_survives_pressure_and_merges(self):
        path,base=self.remember('active')
        with store.storage_lock(path),patch.object(store,'SNAPSHOT_MAX_COUNT',4):
            for i in range(30):self.remember(i)
            merged=store.merge_snapshot(path,dict(base,a=1),dict(base,b=2,storage_revision=1))
            self.assertEqual((merged['a'],merged['b']),(1,2))
            self.assertIn(0,store._snapshots[str(path)])
            self.assertLessEqual(store.snapshot_stats()['count'],4)

    def test_all_protected_capacity_skips_admission_without_losing_base(self):
        path,base=self.remember('active')
        with store.storage_lock(path),patch.object(store,'SNAPSHOT_MAX_COUNT',1):
            self.remember('extra')
            self.assertEqual(store.snapshot_stats()['count'],1)
            self.assertIn(str(path),store._snapshots)

    def test_evicted_or_oversized_base_conflicts_never_overwrites(self):
        path,base=self.remember('old')
        with patch.object(store,'SNAPSHOT_MAX_COUNT',1):self.remember('new')
        with self.assertRaises(store.PersistenceConflict):
            store.merge_snapshot(path,dict(base,a=1),dict(base,b=2,storage_revision=1))
        with patch.object(store,'SNAPSHOT_MAX_BYTES',20):
            large,payload=self.remember('large',size=100)
        self.assertNotIn(str(large),store._snapshots)
        with self.assertRaises(store.PersistenceConflict):
            store.merge_snapshot(large,dict(payload,a=1),dict(payload,b=2,storage_revision=1))

    def test_lock_registry_releases_inactive_paths(self):
        for i in range(100):
            with store.storage_lock(self.root/f'lock-{i}'):
                with store.storage_lock(self.root/f'lock-{i}'):pass
        self.assertFalse(store._active_paths)
        self.assertFalse(any(str(self.root) in key for key in store._locks))


class PersistedProjectContracts(unittest.TestCase):
    def setUp(self):
        from tests import test_job_service
        self.fixture=test_job_service.JobServiceTest();self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.repo=self.fixture.repository
        self.project=self.repo.load_project(self.fixture.project_id)
        self.op=self.project.operations_for_setup(self.fixture.setup_id)[0]

    def test_physical_reference_preview_matches_generation_same_project_tool_and_map(self):
        from klipper_cnc_assistant.application import HeightMapService, ReferenceSessionService, MachineSessionService
        session=MachineSessionService();session.machine_mode='fisico'  # state adapter only; no runtime/transport
        reference=ReferenceSessionService(self.repo,HeightMapService(self.repo),session,self.fixture.physical_map_service)
        shown=reference.build_compensation_preview(self.project.id,self.op.id)['preview']
        generated=self.fixture.compensated_service.generate(self.project.id,self.op.id,
            max_segment_mm=shown['paso_muestreo_virtual_mm'],require_tool_reference=False)
        points={(seg['numero_linea'],p['x_mm'],p['y_mm']):p for seg in shown['segmentos'] for p in seg['puntos'][1:]}
        for p in generated['preview']['trace']:
            self.assertAlmostEqual(points[p['line_number'],p['pcb_x_mm'],p['pcb_y_mm']]['z_compensada_mm'],p['final_z_mm'],delta=1e-14)
        # Frame selection must prefer THIS measured tool over stale preparation Z.
        active=self.fixture.physical_map_service.get_active(self.project.id,self.op.id)
        active['tool_references'][self.op.tool_id]['reference_z']=3.0
        with patch.object(self.fixture.physical_map_service,'get_active',return_value=active):
            shown=reference.build_compensation_preview(self.project.id,self.op.id)['preview']
            generated=self.fixture.compensated_service.generate(self.project.id,self.op.id,require_tool_reference=False)
        self.assertEqual(shown['z_referencia_mm'],3.0)
        self.assertAlmostEqual(shown['segmentos'][-1]['puntos'][-1]['z_compensada_mm'],generated['preview']['trace'][-1]['final_z_mm'])

    def test_cached_analysis_cannot_hide_helices_or_unit_changes_before_publication(self):
        # Keep a valid saved analysis and replace only its original synthetic file.
        original_path=self.repo.project_dir(self.project.id)/self.op.archivo_gcode
        for text,reason in [('G21\nG1 X10 Y10 Z-.1 F120\nG2 X20 I5 Z-.2\n','helicoidal'),
                            ('G21\nG1 X10 Y10 Z-.1 F120\nG20\n','cambio de unidades')]:
            original_path.write_text(text)
            before=list((self.repo.project_dir(self.project.id)/'generated').rglob('*'))
            with self.assertRaisesRegex(ApplicationError,reason):
                self.fixture.compensated_service.generate(self.project.id,self.op.id,require_tool_reference=False)
            self.assertEqual(before,list((self.repo.project_dir(self.project.id)/'generated').rglob('*')))

    def test_repository_roundtrip_preserves_programmed_start_z(self):
        self.assertIsNotNone(self.op.analisis.segmentos_vista_previa[-1].inicio_z_mm)
        loaded=self.repo.load_project(self.repo.save_project(self.project).id)
        self.assertEqual(loaded.get_operation(self.op.id).analisis.segmentos_vista_previa,self.op.analisis.segmentos_vista_previa)

    def test_repository_deleted_project_forgets_snapshots(self):
        root=self.repo.project_dir(self.project.id)
        self.assertTrue(any(root==Path(key).parent or root in Path(key).parents for key in store._snapshots))
        self.repo.delete_project_storage(self.project.id)
        self.assertFalse(any(root in Path(key).parents for key in store._snapshots))
