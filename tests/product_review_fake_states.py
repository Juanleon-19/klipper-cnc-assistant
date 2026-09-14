"""Capture real API/JobService outputs with the existing fake machine boundary.

These JSONs are for browser presentation review only, never execution evidence
for simulated or physical printers. Every repository is a TemporaryDirectory.
"""
import json
from pathlib import Path
from tests.test_final_workflow_api import FinalWorkflowApiTest

OUT=Path('/tmp/kca-astra-review/fake-states');OUT.mkdir(exist_ok=True,parents=True)
def capture(test,name):
    response=test.client.get(test.root+'/execution/live',params=test.target)
    assert response.status_code==200,response.text
    (OUT/(name+'.json')).write_text(json.dumps(response.json(),indent=2))

test=FinalWorkflowApiTest();test.setUp()
try:
    test.hold_printing();capture(test,'running')
    test.post('/job-run/action',action='pause');test.join();capture(test,'paused')
    test.adapter.current_filename='synthetic-wrong-file.gcode'
    test.post('/job-run/action',action='resume');capture(test,'recovery')
    assert test.adapter.resume_calls==0
    test.post('/job-run/action',action='cancel');capture(test,'cancelled')
finally:
    test.client.close();test.doCleanups()

test=FinalWorkflowApiTest();test.setUp()
try:
    test.post('/job-run/prepare');capture(test,'ready')
    test.post('/job-run/start');test.join();capture(test,'spindle-stop')
    test.post('/job-run/action',action='confirm-spindle-stopped');test.join();capture(test,'tool-change')
    test.post('/job-run/action',action='confirm-tool-change');test.join();capture(test,'ready-to-resume')
    assert test.current()['state']=='READY_TO_RESUME'
    test.post('/job-run/action',action='cancel')
finally:
    test.client.close();test.doCleanups()
print('FAKE_API_STATES PASS: 8 snapshots; no hardware')
