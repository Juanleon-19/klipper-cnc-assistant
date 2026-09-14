"""Independent analytical oracles; no runtime, sockets or physical data."""
import math
import random
import unittest
from dataclasses import replace
from types import SimpleNamespace

from klipper_cnc_assistant.heightmap import (
    HeightGrid, HeightSample, ProbeRegion, SampleQuality, compute_height_map, interpolate_height,
)
from klipper_cnc_assistant.heightmap.models import ExclusionZone
from klipper_cnc_assistant.heightmap.coverage import DOMAIN_TOLERANCE_MM
from klipper_cnc_assistant.application.compensated_gcode_service import (
    CompensatedGCodeService, ReferenceFrame, PcbCoordinates, compensate_cut_point,
)
from klipper_cnc_assistant.gcode import analyze_gcode_text
from klipper_cnc_assistant.application.errors import ApplicationError


def surface(fn, *, rows=7, columns=9, bounds=(-4., -3., 12., 9.)):
    x0,y0,x1,y1=bounds
    dx=(x1-x0)/(columns-1) if columns>1 else 0.
    dy=(y1-y0)/(rows-1) if rows>1 else 0.
    samples=[HeightSample(f'{r}:{c}', x0+c*dx, y0+r*dy, fn(x0+c*dx,y0+r*dy),
                         r,c,'synthetic',SampleQuality.VALIDA)
             for r in range(rows) for c in range(columns)]
    return compute_height_map(proyecto_id='synthetic',operacion_id='test',version=1,
        fuente_datos='simulado',superficie_simulada=None,repeticion_simulacion=None,
        etiqueta_simulada=True,grid=HeightGrid(rows,columns,x1-x0,y1-y0,dx,dy),
        probe_region=ProbeRegion(x0,y0,x1,y1),exclusion_zones=(),muestras=samples)


def legacy(text, height_map=None, spacing=.25):
    operation=SimpleNamespace(id='synthetic',nombre='Synthetic',analisis=analyze_gcode_text(text))
    return CompensatedGCodeService(None,None)._build_compensated_lines(
        operation, height_map or surface(lambda x,y:.01*x+.02*y), spacing, ReferenceFrame(100,200,3))


class ProductNumericsTest(unittest.TestCase):
    def test_legacy_rejects_incomplete_arcs_instead_of_dropping_moves(self):
        for arc in ('G2 X10 R5', 'G3 X10 R5', 'G2 X10', 'G3 X10'):
            with self.subTest(arc=arc), self.assertRaisesRegex(ApplicationError,'incompleto'):
                legacy('G21\nG90\nG1 Z-.1 F120\n'+arc+'\n')

    def test_legacy_rejects_critical_imported_commands(self):
        for command in ('G28','G92 X0'):
            with self.subTest(command=command), self.assertRaisesRegex(ApplicationError,'errores críticos'):
                legacy('G21\nG90\nG1 Z-.1 F120\n'+command+'\n')

    def value(self,m,x,y):
        result=interpolate_height(m,x_mm=x,y_mm=y)
        self.assertEqual(result.estado,'ok')
        self.assertTrue(math.isfinite(result.valor_mm))
        return result.valor_mm

    def test_constant_nodes_centers_edges_corners(self):
        m=surface(lambda x,y:2.5)
        for row in range(13):
            for col in range(17):
                self.assertEqual(self.value(m,-4+col,-3+row),2.5)

    def test_plane_2000_deterministic_points(self):
        f=lambda x,y:.037*x-.021*y+1.23
        m=surface(f); rng=random.Random(13092026)
        for _ in range(2000):
            x,y=rng.uniform(-4,12),rng.uniform(-3,9)
            self.assertAlmostEqual(self.value(m,x,y),f(x,y),delta=2e-14)

    def test_quadratic_error_matches_analytical_bilinear_error(self):
        # Linear interpolation error for A*x² is A*(x-x0)*(x1-x).
        f=lambda x,y:.002*x*x+.003*y*y+.001*x*y
        m=surface(f); rng=random.Random(9)
        for _ in range(1000):
            x,y=rng.uniform(-4,12),rng.uniform(-3,9)
            u,v=(x+4)%2,(y+3)%2
            expected_error=.002*u*(2-u)+.003*v*(2-v)
            self.assertAlmostEqual(self.value(m,x,y)-f(x,y),expected_error,delta=2e-15)
            self.assertLessEqual(expected_error,.005)

    def test_continuity_both_axes_at_every_internal_cell_boundary(self):
        m=surface(lambda x,y:.002*x*x+.003*y*y+.001*x*y)
        eps=1e-9
        for x in range(-2,12,2):
            for y in (-2.7,.3,2.9,8.6):
                self.assertLess(abs(self.value(m,x-eps,y)-self.value(m,x+eps,y)),1e-9)
        for y in range(-1,9,2):
            for x in (-3.7,.3,5.9,11.6):
                self.assertLess(abs(self.value(m,x,y-eps)-self.value(m,x,y+eps)),1e-9)

    def test_boundary_tolerance_is_clamping_not_extrapolation(self):
        m=surface(lambda x,y:x+2*y); eps=DOMAIN_TOLERANCE_MM
        for x,y,ox,oy in [(-4,2,-1,0),(12,2,1,0),(3,-3,0,-1),(3,9,0,1),
                           (-4,-3,-1,-1),(12,9,1,1),(-4,9,-1,1),(12,-3,1,-1)]:
            with self.subTest(x=x,y=y):
                self.assertEqual(self.value(m,x,y),x+2*y)
                self.assertAlmostEqual(self.value(m,x+ox*eps/4,y+oy*eps/4),x+2*y)
                self.value(m,x-ox*eps/4,y-oy*eps/4)
                result=interpolate_height(m,x_mm=x+ox*eps*2,y_mm=y+oy*eps*2)
                self.assertEqual(result.estado,'fuera de dominio')
                self.assertIsNone(result.valor_mm)

    def test_degenerate_row_column_and_point(self):
        for rows,cols,bounds in [(1,9,(-4,0,12,0)),(7,1,(0,-3,0,9)),(1,1,(2,3,2,3))]:
            m=surface(lambda x,y:2*x+3*y+4,rows=rows,columns=cols,bounds=bounds)
            for t in (0,.1,.5,.9,1):
                x=bounds[0]+t*(bounds[2]-bounds[0]); y=bounds[1]+t*(bounds[3]-bounds[1])
                self.assertAlmostEqual(self.value(m,x,y),2*x+3*y+4)

    def test_missing_and_excluded_corners_are_insufficient(self):
        m=surface(lambda x,y:0,rows=2,columns=2)
        for sample in (replace(m.muestras[0],z_mm=None),replace(m.muestras[0],incluida=False)):
            result=interpolate_height(replace(m,muestras=(sample,)+m.muestras[1:]),x_mm=0,y_mm=0)
            self.assertEqual(result.estado,'insuficiente')
            self.assertIsNone(result.valor_mm)

    def test_exclusion_is_rejected(self):
        m=surface(lambda x,y:0)
        m=replace(m,exclusion_zones=(ExclusionZone('fixture','fixture',0,0,2,2),))
        self.assertEqual(interpolate_height(m,x_mm=1,y_mm=1).estado,'fuera de dominio')

    def test_sign_and_coordinate_frames(self):
        for delta in (.7,-.4):
            result=compensate_cut_point(pcb=PcbCoordinates(2,3),programmed_z_mm=-.15,
                surface_map=surface(lambda x,y:delta),reference_frame=ReferenceFrame(100,200,8),uses_surface_map=True)
            self.assertAlmostEqual(result.machine.z_mm,8+delta-.15)
            self.assertEqual((result.machine.x_mm,result.machine.y_mm),(102,203))
            self.assertEqual(result.delta_z_mm,delta)

    def test_auxiliary_z_has_no_map_delta(self):
        result=compensate_cut_point(pcb=PcbCoordinates(200,300),programmed_z_mm=5,
            surface_map=surface(lambda x,y:9),reference_frame=ReferenceFrame(100,200,8),uses_surface_map=False)
        self.assertEqual(result.machine.z_mm,13)

    def test_generated_horizontal_path_feed_order_endpoints_and_precision(self):
        text='G21\nG90\nG0 X0 Y0\nG1 Z-0.1 F120 ; plunge\nG1 X4 (copper) F240\nX8\nG1 Y4 F360\nG1 Z2 F90\n'
        lines,preview=legacy(text)
        trace=preview['trace']
        self.assertEqual(sorted(set(p['feed_mm_min'] for p in trace if p['feed_mm_min'])),[90,120,240,360])
        self.assertEqual([p['line_number'] for p in trace],sorted(p['line_number'] for p in trace))
        self.assertEqual(len(lines)-8,len(trace))
        for line_number,end in [(5,(4,0)),(6,(8,0)),(7,(8,4))]:
            pts=[p for p in trace if p['line_number']==line_number]
            self.assertEqual((pts[-1]['pcb_x_mm'],pts[-1]['pcb_y_mm']),end)
            for p in pts:
                self.assertAlmostEqual(p['final_z_mm'],3+.01*p['pcb_x_mm']+.02*p['pcb_y_mm']-.1)
        emitted=analyze_gcode_text('\n'.join(lines))
        self.assertEqual(emitted.cantidad_movimientos,len(trace))
        for seg,p in zip(emitted.segmentos_vista_previa,trace):
            self.assertAlmostEqual(seg.z_mm,p['final_z_mm'],delta=.0000051)

    def test_absolute_relative_and_inches_equivalence(self):
        variants=['G21\nG90\nG1 Z-2.54 F254\nG1 X2.54\nG1 X5.08\n',
                  'G21\nG91\nG1 Z-2.54 F254\nG1 X2.54\nG1 X2.54\n',
                  'G20\nG90\nG1 Z-0.1 F10\nG1 X0.1\nG1 X0.2\n']
        outputs=[legacy(text)[0] for text in variants]
        self.assertEqual(outputs[0],outputs[1]); self.assertEqual(outputs[0],outputs[2])

    def test_negative_coordinates_are_not_reflected(self):
        _,preview=legacy('G21\nG90\nG0 X-2 Y-2\nG1 Z-.1 F120\nG1 X-1 Y-1\n')
        self.assertEqual(preview['trace'][-1]['machine_x_mm'],99)
        self.assertEqual(preview['trace'][-1]['machine_y_mm'],199)


if __name__=='__main__':
    unittest.main()
