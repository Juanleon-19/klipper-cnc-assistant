"""Offline product review; reports limitations without approving their semantics.

Run with PYTHONPATH=src:tests. All inputs are synthetic, no machine runtime.
"""
import csv
import gc
import json
import math
import random
import sys
import tempfile
import tracemalloc
import time
from pathlib import Path

from test_product_numerics import surface, legacy
from klipper_cnc_assistant.gcode import analyze_gcode_text
from klipper_cnc_assistant.heightmap import interpolate_height
from klipper_cnc_assistant.heightmap.compensation import build_compensation_preview
from klipper_cnc_assistant.storage import safe_persistence
from klipper_cnc_assistant.application.errors import ApplicationError


def main(output_dir):
    output_dir.mkdir(parents=True,exist_ok=True)
    f=lambda x,y:.002*x*x+.003*y*y+.001*x*y
    m=surface(f); rng=random.Random(13092026)
    errors=[]
    for _ in range(5000):
        x,y=rng.uniform(-4,12),rng.uniform(-3,9)
        errors.append(abs(interpolate_height(m,x_mm=x,y_mm=y).valor_mm-f(x,y)))
    code='G21\nG90\nG0 X0 Y0\nG1 Z-.1 F120\nG1 X12 F240\nG1 Y8\nG1 X0\nG1 Y0\n'
    lines,preview=legacy(code,m,.1)
    trace=preview['trace']
    (output_dir/'synthetic-original.gcode').write_text(code)
    (output_dir/'synthetic-compensated.gcode').write_text('\n'.join(lines)+'\n')
    with (output_dir/'trajectory.csv').open('w') as out:
        writer=csv.DictWriter(out,fieldnames=list(trace[0]),lineterminator='\n');writer.writeheader();writer.writerows(trace)
    cutting=[p for p in trace if p['line_number']>=5]
    z=[p['final_z_mm'] for p in cutting]
    metrics={'samples':len(z),'original_moves':analyze_gcode_text(code).cantidad_movimientos,
        'generated_moves':preview['emitted_points'],'surface_compensated_moves':sum(p['uses_surface_map'] for p in trace),
        'out_of_map':sum(interpolate_height(m,x_mm=p['pcb_x_mm'],y_mm=p['pcb_y_mm']).valor_mm is None for p in trace),
        'nonfinite':sum(not math.isfinite(v) for v in z),'min_z_mm':min(z),'max_z_mm':max(z),
        'max_consecutive_delta_z_mm':max(abs(b-a) for a,b in zip(z,z[1:])),
        'all_moves_max_consecutive_delta_z_mm':max(abs(b['final_z_mm']-a['final_z_mm']) for a,b in zip(trace,trace[1:]))}
    ramps={}
    for name,code in {
        'linear_ramp':'G21\nG90\nG1 Z-.1 F120\nG1 X10 Z-1.1\n',
        'helical_arc':'G21\nG90\nG1 Z-.1 F120\nG2 X10 I5 J0 Z-1.1\n',
        'unsupported_R_arc':'G21\nG90\nG1 Z-.1 F120\nG2 X10 R5\n',
        'units_after_axes':'G1 X1 F10 G20\n',
        'millimeters_after_axes':'G20\nG1 X.1 F10\nG1 X.2 F20 G21\n',
    }.items():
        a=analyze_gcode_text(code)
        try:
            _,p=legacy(code,surface(lambda x,y:0,bounds=(-10,-10,20,20)),1)
        except ApplicationError as error:
            ramps[name]={'input_moves':a.cantidad_movimientos,'preview_segments':len(a.segmentos_vista_previa),
                         'incomplete':a.analisis_incompleto,'rejected_explicitly':str(error)}
            continue
        ramps[name]={'input_moves':a.cantidad_movimientos,'preview_segments':len(a.segmentos_vista_previa),
            'incomplete':a.analisis_incompleto,'generated_moves':p['emitted_points'],
            'first_last_segment_sample':next((r for r in p['trace'] if r['line_number']==len(code.splitlines())),None),
            'last_sample':p['trace'][-1] if p['trace'] else None}
    # A bounded experiment proves retention after source paths disappear; not
    # attribution of the historical service peak. Isolated process and tempdir.
    tracemalloc.start();gc.collect();before=tracemalloc.get_traced_memory()[0]
    before_paths=len(safe_persistence._snapshots)
    with tempfile.TemporaryDirectory() as temp:
        for index in range(80):
            payload={'storage_revision':0,'points':[{'x':float(k),'z':k*.0001} for k in range(1000)]}
            path=Path(temp)/f'{index}.json'
            safe_persistence.atomic_json(path,payload)
            safe_persistence.read_json_snapshot(path)
        del payload
    safe_persistence.prune_snapshots()
    gc.collect();after=tracemalloc.get_traced_memory()[0]
    retention={'paths_before':before_paths,'paths_after':len(safe_persistence._snapshots),
        'retained_bytes_after_tempdir_removal':after-before,'new_paths':80,'points_per_snapshot':1000}
    tracemalloc.stop()
    # Physical map payloads contain delta; preview and generation now share machine Z.
    constant_delta=surface(lambda x,y:.2)
    preview_input='G21\nG90\nG1 Z-.1 F120\nG1 X4\n'
    generic=build_compensation_preview(analysis=analyze_gcode_text(preview_input),height_map=constant_delta,reference_z_mm=3)
    _,generated=legacy(preview_input,constant_delta)
    preview_frames={'map_delta_mm':.2,'reference_mm':3,'programmed_z_mm':-.1,
        'reference_session_preview_z_mm':generic['segmentos'][-1]['puntos'][-1]['z_compensada_mm'],
        'generated_machine_z_mm':generated['trace'][-1]['final_z_mm'],
        'expected_local_compensated_z_mm':.1}
    timings={}
    for size in (9,41,81):
        bench=surface(f,rows=size,columns=size)
        start=time.perf_counter()
        for i in range(1000):interpolate_height(bench,x_mm=(i%100)*.1,y_mm=2)
        timings[str(size*size)]=time.perf_counter()-start
    result={'quadratic':{'max_error_mm':max(errors),'mean_error_mm':sum(errors)/len(errors),'analytical_upper_bound_mm':.005},
            'trajectory':metrics,'counterexamples':ramps,'snapshot_retention':retention,
            'preview_reference_frames':preview_frames,'interpolation_1000_queries_seconds_by_nodes':timings}
    (output_dir/'diagnostics.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main(Path(sys.argv[1]))
