"""Practical HTTP soak for the review launcher only (fixed isolated port)."""
import json
import time
from pathlib import Path
import requests

BASE='http://127.0.0.1:18765'
OUT=Path('/tmp/kca-astra-review/soak.json')
s=requests.Session()
requests_count=0
def api(method,path,payload=None):
    global requests_count
    r=s.request(method,BASE+path,json=payload,timeout=30)
    r.raise_for_status();requests_count+=1
    return r.json()

assert api('GET','/api/health')['modo_maquina']=='simulado'
runtime=api('GET','/api/machine/status')
assert runtime['mode']=='SIMULATED' and not runtime['arduino']['open']
projects=api('GET','/api/projects')
p=projects[0]; root='/api/projects/'+p['id']; op=root+'/operations/'+p['operaciones'][0]['id']
query='?setup_id='+p['montajes'][0]['id']+'&face=superior'
started=time.monotonic(); checkpoints=[]
for cycle in range(160):
    api('POST','/api/machine/connect')
    for _ in range(5):
        runtime=api('GET','/api/machine/status')
        assert runtime['mode']=='SIMULATED' and not runtime['arduino']['open']
        api('GET',root)
        api('GET',root+'/execution/live'+query)
    api('GET',op+'/height-map')
    api('GET',op+'/height-map/statistics')
    if cycle%5==0:
        api('POST',op+'/height-map/recalculate')
    api('POST','/api/machine/disconnect')
    if cycle in (9,39,79,119,159):
        m=api('GET','/api/review-metrics');m.update(cycle=cycle+1,elapsed_s=time.monotonic()-started)
        checkpoints.append(m);print(json.dumps(m),flush=True)
result={'cycles':160,'warmup_cycles':10,'requests':requests_count,'elapsed_s':time.monotonic()-started,
        'initial':checkpoints[0],'final':checkpoints[-1],'checkpoints':checkpoints,
        'scope':'same synthetic project/map; 5x status/project/live reads each cycle; map recalculation every 5 cycles'}
result['status']='PASS' if (result['final']['rss_kib']-result['initial']['rss_kib']<64*1024
    and result['final']['threads']<=result['initial']['threads']+2
    and result['final']['async_tasks']<=result['initial']['async_tasks']+2
    and all(m['snapshots']['count']<=m['snapshots']['max_count'] and m['snapshots']['bytes']<=m['snapshots']['max_bytes'] for m in checkpoints)) else 'FAIL'
result['snapshot_max_count']=max(m['snapshots']['peak_count'] for m in checkpoints)
result['snapshot_final_count']=result['final']['snapshots']['count']
OUT.write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
