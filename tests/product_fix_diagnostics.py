"""Offline analytical regression metrics for the Astra functional corrections.

PYTHONPATH=src python tests/product_fix_diagnostics.py OUTPUT_DIRECTORY
No runtime, network or production data; all trajectories/maps are synthetic.
"""
import csv
import json
import math
import sys
import tempfile
from pathlib import Path

# Allow both module execution and the documented direct script invocation.
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from tests.test_product_numerics import surface, legacy
from klipper_cnc_assistant.gcode import analyze_gcode_text
from klipper_cnc_assistant.heightmap import interpolate_height
from klipper_cnc_assistant.storage import safe_persistence as store


def trajectory_scenarios():
    constant=lambda x,y:.2
    plane=lambda x,y:.01*x+.02*y
    cases={
        'horizontal': ((-4,-3,-.1),(12,-3,-.1),constant),
        'ascending_ramp': ((-4,-3,-1.1),(12,9,-.1),constant),
        'descending_ramp': ((12,9,-.1),(-4,-3,-1.1),constant),
        'cell_crossings_inclined_plane': ((-4,-3,-.2),(12,9,-.8),plane),
        'xmin_boundary_inclined_plane': ((-4,-3,-.8),(-4,9,-.2),plane),
        'xmax_boundary_inclined_plane': ((12,9,-.2),(12,-3,-.8),plane),
        'ymax_boundary_inclined_plane': ((12,9,-.8),(-4,9,-.2),plane),
        'former_0_9_mm_error': ((0,0,-.1),(10,0,-1.1),lambda x,y:0),
    }
    summaries={};rows=[]
    for name,(start,end,fn) in cases.items():
        x0,y0,z0=start;x1,y1,z1=end
        source=f'G21\nG90\nG0 X{x0} Y{y0}\nG1 Z{z0} F120\nG1 X{x1} Y{y1} Z{z1} F240\n'
        m=surface(fn)
        lines,preview=legacy(source,m,.1)
        emitted=analyze_gcode_text('\n'.join(lines)).segmentos_vista_previa
        pts=[];errors=[];programmed_errors=[]
        distance=math.hypot(x1-x0,y1-y0)
        for record,seg in zip(preview['trace'],emitted):
            if record['line_number']!=5:continue
            x=record['pcb_x_mm'];y=record['pcb_y_mm']
            t=math.hypot(x-x0,y-y0)/distance
            programmed=z0+t*(z1-z0)
            expected=3+fn(x,y)+programmed
            errors.append(abs(seg.z_mm-expected))
            programmed_errors.append(abs(record['programmed_z_mm']-programmed))
            row={'scenario':name,'t':t,'x_mm':x,'y_mm':y,'programmed_z_mm':programmed,
                 'map_delta_mm':fn(x,y),'expected_machine_z_mm':expected,'generated_z_mm':seg.z_mm}
            rows.append(row);pts.append(row)
        zs=[3+fn(x0,y0)+z0]+[p['generated_z_mm'] for p in pts]
        expected_zs=[3+fn(x0,y0)+z0]+[p['expected_machine_z_mm'] for p in pts]
        jumps=[b-a for a,b in zip(zs,zs[1:])]
        expected_jumps=[b-a for a,b in zip(expected_zs,expected_zs[1:])]
        last=emitted[-1]
        result={'samples':len(pts),'max_error_mm':max(errors),'max_programmed_z_error_mm':max(programmed_errors),
            'max_abs_delta_z_mm':max(map(abs,jumps)),
            'max_artificial_delta_z_mm':max(abs(a-b) for a,b in zip(jumps,expected_jumps)),
            'endpoint_error_mm':math.dist((last.fin_x_mm,last.fin_y_mm,last.z_mm),(100+x1,200+y1,3+fn(x1,y1)+z1)),
            'min_z_mm':min(zs),'max_z_mm':max(zs),'nonfinite':sum(not math.isfinite(z) for z in zs),
            'out_of_map':sum(interpolate_height(m,x_mm=p['x_mm'],y_mm=p['y_mm']).valor_mm is None for p in pts),
            'unexpected_direction_changes':sum(a*b < -1e-12 for a,b in zip(jumps,expected_jumps))}
        result['status']='PASS' if (result['max_error_mm']<=5.01e-6 and result['endpoint_error_mm']<=1e-5
            and result['max_programmed_z_error_mm']<1e-13 and result['max_artificial_delta_z_mm']<=1.01e-5
            and not result['nonfinite'] and not result['out_of_map'] and not result['unexpected_direction_changes']) else 'FAIL'
        summaries[name]=result
    return summaries,rows


def retention_stress():
    store.prune_snapshots()
    with tempfile.TemporaryDirectory() as temp:
        for index in range(store.SNAPSHOT_MAX_COUNT*2):
            p=Path(temp)/f'{index}.json';payload={'storage_revision':0,'points':[{'x':k,'z':k*.01} for k in range(50)]}
            store.atomic_json(p,payload);store.read_json_snapshot(p)
        maximum=store.snapshot_stats()
    store.prune_snapshots()
    final=store.snapshot_stats()
    return {'max':maximum,'final':final,'resources_created_deleted':store.SNAPSHOT_MAX_COUNT*2,
            'status':'PASS' if maximum['count']<=store.SNAPSHOT_MAX_COUNT and final['count']==0 else 'FAIL'}


def main(directory):
    directory.mkdir(parents=True,exist_ok=True)
    scenarios,rows=trajectory_scenarios()
    with (directory/'trajectory.csv').open('w') as output:
        writer=csv.DictWriter(output,fieldnames=list(rows[0]),lineterminator='\n');writer.writeheader();writer.writerows(rows)
    result={'trajectories':scenarios,'snapshots':retention_stress()}
    result['status']='PASS' if all(s['status']=='PASS' for s in scenarios.values()) and result['snapshots']['status']=='PASS' else 'FAIL'
    (directory/'diagnostics.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))
    assert result['status']=='PASS'


if __name__=='__main__':main(Path(sys.argv[1]))
