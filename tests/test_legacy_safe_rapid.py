from types import SimpleNamespace
from dataclasses import replace
import unittest
from tests.test_adaptive_compensation import build_height_map
from klipper_cnc_assistant.application.compensated_gcode_service import CompensatedGCodeService,ReferenceFrame
from klipper_cnc_assistant.gcode import analyze_gcode_text

class LegacySafeRapidTest(unittest.TestCase):
    def generate(self,source,*,safe=2,delta=0,height_map=None):
        service=CompensatedGCodeService.__new__(CompensatedGCodeService)
        service.machine_runtime=None
        operation=SimpleNamespace(id='op',nombre='fake',analisis=analyze_gcode_text(source))
        return service._build_compensated_lines(operation,height_map or build_height_map(lambda x,y:delta),2,ReferenceFrame(0,0,10),
            clearance_settings=dict(configured_safe_z_mm=safe,configured_safe_z_source='test',rapid_clearance_margin_mm=.05))

    def test_safe_xy_retains_g0_and_cut_keeps_feed(self):
        lines,preview=self.generate('G21\nG90\nG1 F25\nG0 Z2\nG0 X10 Y10\nG1 Z-1\nG1 Z0\nG0 Z2\n')
        self.assertTrue(any(x.startswith('G0 X10.00000 Y10.00000 Z12.00000 F25.000') for x in lines))
        self.assertTrue(any(x.startswith('G1 X10.00000 Y10.00000 Z9.00000 F25.000') for x in lines))
        self.assertTrue(all(x['feed_mm_min']==25 for x in preview['trace']))
        self.assertEqual(preview['trace'][0]['emitted_motion'],'G1') # start position not proven

    def test_unsafe_rapid_is_conservative(self):
        for path,safe,delta in [('G0 X10 Y10',3,0),('G0 X30 Y10',2,0),('G0 X10 Y10 Z3',2,0),('G0 X10 Y10',2,2.1),('G0 X10 Y10',None,0)]:
            with self.subTest(path=path,safe=safe,delta=delta):
                lines,preview=self.generate('G21\nG90\nG1 F25\nG0 Z2\n'+path+'\n',safe=safe,delta=delta)
                self.assertFalse(any(x.startswith('G0 ') for x in lines))
                self.assertTrue(preview['warnings'])

    def test_modal_relative_and_inches_preserve_work_feed(self):
        lines,preview=self.generate('G20\nG90\nG1 F1\nG0 Z0.1\nG91\nX0.1 Y0.1\nG1 Z-0.2\n',safe=2)
        self.assertTrue(any(x.startswith('G0 ') for x in lines))
        self.assertTrue(all(abs(x['feed_mm_min']-25.4)<1e-8 for x in preview['trace']))

    def test_exclusion_or_unsupported_cell_never_emit_rapid(self):
        from klipper_cnc_assistant.heightmap.models import ExclusionZone
        base=build_height_map(lambda x,y:0)
        excluded=replace(base, exclusion_zones=(ExclusionZone('hole','hole',4,4,6,6),))
        missing=replace(base,muestras=[replace(sample,z_mm=None) if sample.fila==1 and sample.columna==1 else sample for sample in base.muestras])
        for mesh in [excluded,missing]:
            lines,_=self.generate('G21\nG90\nG1 F25\nG0 Z2\nG0 X10 Y10\n',height_map=mesh)
            self.assertFalse(any(line.startswith('G0 ') for line in lines))

    def test_serialized_map_retains_exclusions(self):
        from dataclasses import asdict
        from klipper_cnc_assistant.heightmap.models import ExclusionZone
        mesh=replace(build_height_map(lambda x,y:0),exclusion_zones=(ExclusionZone('hole','hole',4,4,6,6),))
        service=CompensatedGCodeService.__new__(CompensatedGCodeService)
        restored=service._height_map_from_payload(asdict(mesh))
        self.assertEqual(restored.exclusion_zones,mesh.exclusion_zones)
