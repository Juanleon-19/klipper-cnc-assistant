"""Real React + HTTP reset, fake transports and temporary project; no hardware."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time

import requests
import uvicorn
from tests.test_functional_journey import FunctionalJourneyTest

os.environ.update(MACHINE_MODE='simulated', MACHINE_AUTO_CONNECT='false')
site='http://127.0.0.1:18937'; webdriver='http://127.0.0.1:18938'
artifacts=Path(tempfile.mkdtemp(prefix='kca-cancelled-browser-'))
f=FunctionalJourneyTest(); server=driver=session=None
posts=[]
def wait(fn):
    deadline=time.monotonic()+45
    while time.monotonic()<deadline:
        try:
            value=fn()
            if value:return value
        except requests.RequestException:pass
        time.sleep(.1)
    raise AssertionError('Browser timeout')
def command(method,path,payload=None):
    r=requests.request(method,webdriver+path,json=payload,timeout=50)
    r.raise_for_status();return r.json()['value']
def js(script):return command('POST',f'/session/{session}/execute/sync',{'script':script,'args':[]})
def click(label):
    return js('const b=[...document.querySelectorAll("button,a")].find(e=>e.textContent.trim()==='+json.dumps(label)+');if(!b||b.disabled)return false;b.click();return true;')
def confirm_reset(accept):
    element=wait(lambda:js('return [...document.querySelectorAll("button")].filter(e=>e.textContent.trim()==="Reiniciar proceso" && !e.disabled).at(-1);'))
    identity=element['element-6066-11e4-a52e-4f735466cecf']
    command('POST',f'/session/{session}/element/{identity}/click',{})
    wait(lambda:command('GET',f'/session/{session}/alert/text'))
    command('POST',f'/session/{session}/alert/accept',{})
    text=wait(lambda:command('GET',f'/session/{session}/alert/text'))
    assert 'parcialmente mecanizada' in text,text
    command('POST',f'/session/{session}/alert/'+('accept' if accept else 'dismiss'),{})
try:
    f.setUp();f.post('/api/machine/connect')
    run=f.job.prepare_run(**f.args)
    first,second=run['operations'][:2]
    first.update(execution_status='COMPLETED',observed_printing=True,finished_at='2026-01-01T00:00:00Z',remote_file='synthetic/first.gcode',moonraker_filename='synthetic/first.gcode')
    second.update(execution_status='RUNNING',observed_printing=True,finished_at=None,remote_file='synthetic/second.gcode',moonraker_filename='synthetic/second.gcode')
    run.update(state='JOB_CANCELLED',current_operation_index=1,cancel_remote_status='identity_or_transport_error_requires_reconciliation',start_attempt=dict(id='second-attempt',status='sent',may_have_been_sent=True,operation_id=second['operation_id'],remote_file=second['remote_file']))
    path=f.job._run_file(f.context);path.write_text(json.dumps(run));before=path.read_bytes()
    scripts=list(f.transport.scripts)
    f.app.router.on_startup.clear();f.app.router.on_shutdown.clear();f.app.middleware_stack=None
    @f.app.middleware('http')
    async def record(request,next_call):
        response=await next_call(request)
        if request.method=='POST': posts.append((request.url.path,response.status_code))
        return response
    server=uvicorn.Server(uvicorn.Config(f.app,host='127.0.0.1',port=18937,log_level='warning'))
    thread=threading.Thread(target=server.run,daemon=True);thread.start();wait(lambda:server.started)
    with (artifacts/'driver.log').open('w') as log:
        driver=subprocess.Popen(['geckodriver','--host','127.0.0.1','--port','18938'],stdout=log,stderr=subprocess.STDOUT)
    wait(lambda:requests.get(webdriver+'/status',timeout=1).ok)
    session=command('POST','/session',{'capabilities':{'alwaysMatch':{'browserName':'firefox','unhandledPromptBehavior':'ignore','moz:firefoxOptions':{'args':['-headless']}}}})['sessionId']
    command('POST',f'/session/{session}/url',{'url':site})
    wait(lambda:click('Abrir'));wait(lambda:click('Ejecución'))
    wait(lambda:'Intento anterior finalizado' in js('return document.body.innerText'))
    wait(lambda:click('Referencia'));js('for(const d of document.querySelectorAll("details"))d.open=true;')
    confirm_reset(False)
    assert path.read_bytes()==before
    assert not any(p.endswith('/reset-preparation') for p,_ in posts)
    confirm_reset(True)
    wait(lambda:not path.exists())
    reset_posts=[p for p in posts if p[0].endswith('/reset-preparation')]
    assert len(reset_posts)==1 and reset_posts[0][1]==200,reset_posts
    saved=json.loads(next((path.parent/'history').glob('*.json')).read_text())
    assert saved['operations']==run['operations']
    assert saved['administrative_closure']['outcome']=='INCOMPLETE_UNVERIFIED'
    assert f.transport.scripts==scripts and not f.transport.started
    assert f.runtime.snapshot()['state']=='DISCONNECTED'
    command('POST',f'/session/{session}/refresh',{})
    wait(lambda:click('Abrir'));wait(lambda:click('Ejecución'))
    wait(lambda:'NO_EXECUTION' in js('return document.body.innerText'))
    (artifacts/'result.json').write_text(json.dumps({'result':'PASS','posts':posts},indent=2))
    print('CANCELLED_STANDBY_BROWSER PASS',artifacts,flush=True)
finally:
    if session:
        try: command('POST',f'/session/{session}/alert/dismiss',{})
        except requests.RequestException: pass
        try: (artifacts/'page.txt').write_text(js('return document.body.innerText'))
        except Exception:pass
        command('DELETE',f'/session/{session}')
    if driver:driver.terminate();driver.wait(timeout=15)
    if server:server.should_exit=True;thread.join(timeout=10)
    f.doCleanups()
