"""Manual HTTP smoke against a running isolated simulated instance.

Not part of unittest discovery. Never targets a physical-mode app.
"""
import requests,json,re
from pathlib import Path
import argparse
from urllib.parse import urlsplit
parser=argparse.ArgumentParser(description='Smoke test against an isolated simulated app only.')
parser.add_argument('--base-url', required=True)
parser.add_argument('--data-dir', required=True, type=Path)
args=parser.parse_args()
base=args.base_url.rstrip('/')
assert urlsplit(base).hostname in {'127.0.0.1', 'localhost'}, 'Only localhost is allowed'
assert str(args.data_dir.resolve()).startswith('/tmp/'), 'Use isolated temporary storage'
steps=[]
def api(method,path,payload=None,expected=200):
    r=requests.request(method,base+path,json=payload,timeout=15)
    assert r.status_code==expected,(method,path,r.status_code,r.text)
    steps.append({'method':method,'path':path,'status':r.status_code})
    return r.json()
health=api('GET','/api/health');assert health['modo_maquina']=='simulado'
runtime=api('GET','/api/machine/status');assert runtime['mode']=='SIMULATED';assert not runtime['moonraker']['http_connected'];assert not runtime['arduino']['open']
project=api('POST','/api/projects',{'nombre':'Cierre API simulado','material':{'ancho_mm':80,'alto_mm':50,'espesor_mm':1.6}},expected=201)
p=project['id'];s=project['montajes'][0]['id'];root=f'/api/projects/{p}';target={'setup_id':s,'face':'superior'}
api('POST',root+'/open')
op=api('POST',root+'/operations',{'nombre':'Aislamiento API','tipo':'aislamiento','cara':'superior','orden':0,'herramienta':'V-bit 30','tool_id':'vbit-30','setup_id':s},expected=201)
o=op['id'];oproot=root+'/operations/'+o
api('POST',oproot+'/gcode',{'nombre_archivo':'simulated.nc','contenido':'G21\nG90\nG0 X10 Y10\nG1 X20 Y10 Z-0.05 F120\nG1 X20 Y20 F240\n'})
analysis=api('POST',oproot+'/analyze');assert 120 in analysis['avances_mm_min'] and 240 in analysis['avances_mm_min']
data=args.data_dir/'projects'/p
def tree():return {str(x.relative_to(data)):x.read_bytes() for x in data.rglob('*') if x.is_file()}
before=tree()
assert api('GET',root+'/job-run?setup_id='+s+'&face=superior') is None
api('GET',root);api('GET','/api/projects');api('GET',root+'/job-plan?setup_id='+s+'&face=superior');api('GET',root+'/execution/live?setup_id='+s+'&face=superior')
assert tree()==before
api('POST',oproot+'/reference-session/machine-reference')
api('POST',oproot+'/reference-session/work-origin',{'x_mm':0,'y_mm':0})
api('POST',oproot+'/reference-session/z-reference',{'x_mm':0,'y_mm':0,'z_mm':0})
m=api('POST',oproot+'/height-map/simulate',{'filas':3,'columnas':3,'superficie_simulada':'inclinada','repeticion_simulacion':4,'probe_region':{'min_x_mm':2,'min_y_mm':2,'max_x_mm':78,'max_y_mm':48},'exclusion_zones':[]})
api('POST',oproot+'/height-map/validate')
preview=api('POST',oproot+'/compensation-preview');assert preview['preview']
before=tree();api('GET',oproot+'/height-map');api('GET',oproot+'/height-map/statistics');api('GET',oproot+'/reference-session');assert tree()==before
plan=api('POST',root+'/job-plan',target);assert plan['generation_id']
run=api('POST',root+'/job-run/prepare',target);assert run['state']=='JOB_VALIDATING';assert not run['ready']
api('POST',root+'/job-run/start',target,expected=400)
api('POST',oproot+'/compensated-gcode/generate?mode=legacy',expected=404)
api('POST','/api/machine/probe/confirm',{'project_id':p,'operation_id':o},expected=400)
api('PUT','/api/machine/settings',{'z_clearance_feed_mm_min':180})
run=api('POST',root+'/job-run/action',dict(target,action='cancel'));assert run['state']=='JOB_CANCELLED'
api('POST',root+'/job-run/action',dict(target,action='continue'),expected=409)
assert api('GET',root+'/job-run?setup_id='+s+'&face=superior')['state']=='JOB_CANCELLED'
print(json.dumps({'project_id':p,'operation_id':o,'steps':steps,'legacy_preview':True,'simulated_map':True,'native_jobrun_blocked':True},indent=2))
print('API_END_TO_END PASS',len(steps),'requests; native printing/probe intentionally blocked')
