"""Signal tests: only owned subprocesses, temporary data and fake transports.
Run with an approved Python environment from this isolated worktree.
No systemctl, no hardware, no outbound connections from children.
"""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request

CHILD = r'''
import asyncio, json, os, threading, time
from pathlib import Path
import uvicorn
from klipper_cnc_assistant.api import create_app
from klipper_cnc_assistant.machine.physical_ownership import OwnerKind
root=Path(os.environ['TEST_ROOT']); mode=os.environ['TEST_CASE']
app=create_app(data_dir=root/'data')
runtime=app.state.machine_runtime
class Manager:
 def stop(self): (root/'manager_closed').write_text('closed')
class Session:
 def close(self): (root/'http_closed').write_text('closed')
class Client: session=Session()
@app.on_event('startup')
async def retain():
 runtime.physical_ownership.acquire(OwnerKind.JOB_EXECUTION, 'synthetic/setup/face')
 runtime.physical_ownership.enter_recovery('synthetic retained root')
 runtime._connection_manager=Manager(); runtime._client=Client()
 (root/'data/current_run.json').write_text(json.dumps({'state':'SPINDLE_STOP_REQUIRED','operations':['COMPLETED']+['PENDING']*4}))
 if mode=='stuck':
  shutdown=app.state.application_shutdown
  shutdown.producer_timeout=0.05; shutdown.cleanup_timeout=0.05
  class Producer:
   def request_application_shutdown(self): pass
   def application_workers(self): return (worker,)
  def work():
   while not (root/'release').exists(): time.sleep(.01)
  worker=threading.Thread(target=work,daemon=True);worker.start()
  shutdown.producers+=(Producer(),)
@app.get('/synthetic-hold')
async def hold():
 (root/'request_entered').write_text('entered')
 while not (root/'release').exists(): await asyncio.sleep(.01)
 return {'released':True}
uvicorn.run(app, host='127.0.0.1', port=int(os.environ['TEST_PORT']), lifespan='on')
'''


def wait_for(predicate, seconds=15):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.01)
    raise AssertionError('Timed out waiting for isolated test condition')


def run_case(source, label, case):
    with tempfile.TemporaryDirectory(prefix='kca-signal-case-') as temp:
        root = Path(temp)
        (root/'child.py').write_text(CHILD)
        (root/'sitecustomize.py').write_text("import socket\ndef denied(*a,**kw):\n raise AssertionError('Outbound network forbidden')\nsocket.socket.connect=denied\nsocket.socket.connect_ex=denied\n")
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
        env={k:v for k,v in os.environ.items() if not k.startswith(('MACHINE_','MOONRAKER_','SERIAL_','KCA_'))}
        env.update(MACHINE_MODE='simulated',MACHINE_AUTO_CONNECT='false',PYTHONDONTWRITEBYTECODE='1',PYTHONWARNINGS='ignore',PYTHONPATH=f'{root}:{source}/src',TEST_ROOT=str(root),TEST_CASE=case,TEST_PORT=str(port))
        with (root/'log').open('w+') as log:
            proc=subprocess.Popen([sys.executable,str(root/'child.py')],env=env,cwd=root,stdout=log,stderr=log)
            sent=False; request=None; request_errors=[]
            try:
                def ready():
                    if proc.poll() is not None: raise AssertionError('Child exited during startup')
                    try:
                        with urllib.request.urlopen(f'http://127.0.0.1:{port}/openapi.json',timeout=.2):return True
                    except OSError:return False
                wait_for(ready)
                before=(root/'data/current_run.json').read_bytes()
                if case=='http':
                    def get():
                        try:
                            with urllib.request.urlopen(f'http://127.0.0.1:{port}/synthetic-hold',timeout=10) as response:response.read()
                        except Exception as error:request_errors.append(str(error))
                    request=threading.Thread(target=get);request.start()
                    wait_for(lambda:(root/'request_entered').exists())
                start=time.monotonic()
                proc.send_signal(signal.SIGTERM);sent=True
                if case=='http':
                    time.sleep(.4)  # representative pre-lifespan observation window, not 90s
                    assert proc.poll() is None
                    assert not (root/'manager_closed').exists()
                    (root/'release').touch()
                proc.wait(timeout=15)
                if request:request.join(2)
                assert not request_errors,request_errors
                log.seek(0);output=log.read()
                closed=(root/'manager_closed').exists()
                failed='Application shutdown failed' in output
                assert (root/'data/current_run.json').read_bytes()==before
                assert proc.returncode==-signal.SIGTERM,proc.returncode
                assert closed==(label=='approved' and case!='stuck'),output
                assert failed==(label=='old' or case=='stuck'),output
                result=dict(version=label,case=case,returncode=proc.returncode,exit_s=round(time.monotonic()-start,3),callback_failed=failed,manager_closed=closed,http_closed=(root/'http_closed').exists(),data_unchanged=True,pre_lifespan_wait_observed=case=='http',outbound_network='DENIED')
                print(json.dumps(result),flush=True)
                return result
            finally:
                (root/'release').touch()
                if proc.poll() is None:
                    if not sent: proc.send_signal(signal.SIGTERM)
                    proc.wait(timeout=20)  # no force-kill fallback
                if request:request.join(2)


def main():
    stage=Path.cwd()
    with tempfile.TemporaryDirectory(prefix='kca-old-code-') as temp:
        old=Path(temp)
        archive=old/'source.tar'
        with archive.open('wb') as target:
            subprocess.run(['git','archive','b0dbcdc691a145b4b086dcd9cc2dcf1795e10880','src'],stdout=target,check=True)
        with tarfile.open(archive) as tar:tar.extractall(old,filter='data')
        results=[run_case(source,label,case) for source,label,cases in [(old,'old',('idle','http')),(stage,'approved',('idle','http','stuck'))] for case in cases]
        Path('auditorias/deploy-runbook-20261020/SIGNAL_RESULTS.json').write_text(json.dumps(results,indent=2)+'\n')

if __name__=='__main__':main()
