"""Isolated Firefox E2E: fake hardware, real backend and built frontend.

Run with PYTHONPATH=src:. MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false.
Requires geckodriver/Firefox. No production data, URLs or services are used.
"""
import base64
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import threading
import time
from unittest.mock import patch

import requests
import uvicorn
from tests.test_reference_execution_handoff import RealMeshWorkerHandoffTest

os.environ.update(MACHINE_MODE='simulated', MACHINE_AUTO_CONNECT='false')
ARTIFACTS = Path('/tmp/kca-execution-fix-validation/browser')
ARTIFACTS.mkdir(parents=True, exist_ok=True)
DRIVER = 'http://127.0.0.1:18798'
SITE = 'http://127.0.0.1:18797'
fixture = RealMeshWorkerHandoffTest()
server = driver = session = None


def wait_for(fn, timeout=30):
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        try:
            result = fn()
            if result:
                return result
        except Exception as exc:
            last = exc
        time.sleep(.2)
    raise AssertionError(f'Timed out: {last}')


def call(method, path, payload=None):
    response = requests.request(method, DRIVER + path, json=payload, timeout=60)
    response.raise_for_status()
    return response.json()['value']


def js(script):
    return call('POST', '/session/' + session + '/execute/sync', {'script': script, 'args': []})


def click(label):
    return js('const b=[...document.querySelectorAll("button,a")].find(b=>b.textContent.trim()===' + json.dumps(label) + '); if(!b) return false; b.click(); return true;')


def text():
    return js('return document.body.innerText')


try:
    fixture.setUp()  # actual mesh worker/finally; transports and descent are fakes
    project = fixture.repo.load_project(fixture.project_id)
    op = project.get_operation(fixture.operation_id)
    fixture.repo.save_project(replace(project, operaciones=tuple(
        op if i == 0 else replace(op, id=f'browser-operation-{i}', nombre=f'Operación {i + 1}', orden=i)
        for i in range(5))))
    payload = fixture.maps.get_active(fixture.project_id, fixture.operation_id)
    payload["operation_ids"] = [o.id for o in fixture.repo.load_project(fixture.project_id).operaciones]
    fixture.repo.save_height_map_payload(fixture.project_id, payload["map_id"], payload)
    fixture.job.prepare_run(**fixture.args)
    app = fixture.fixture.app
    app.state.frontend_dist_dir = Path('frontend/dist').resolve()
    app.router.on_startup.clear()
    app.router.on_shutdown.clear()
    requests_seen = []
    app.middleware_stack = None  # fixture TestClient already built its ASGI stack

    @app.middleware('http')
    async def record(request, next_call):
        if request.method != 'GET':
            requests_seen.append((request.method, request.url.path))
        return await next_call(request)

    original_session = fixture.runtime.current_physical_session_id
    fixture.runtime.current_physical_session_id = lambda: 'browser-previous-session-negative'
    original_snapshot = fixture.runtime.snapshot
    def fresh_fake_snapshot():
        fixture.runtime._machine.update_motion(live_position=(4, 5, 31), live_velocity=0, source="websocket")
        fixture.runtime._last_websocket_message_at = time.monotonic()
        fixture.runtime._last_telemetry_at = time.monotonic()
        return original_snapshot()
    fixture.runtime.snapshot = fresh_fake_snapshot
    fixture.runtime.refresh_observed_state = fresh_fake_snapshot
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=18797, log_level='warning'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    wait_for(lambda: server.started)
    driver_log = (ARTIFACTS / 'geckodriver.log').open('w')
    driver = subprocess.Popen(['geckodriver', '--host', '127.0.0.1', '--port', '18798'], stdout=driver_log, stderr=subprocess.STDOUT)
    wait_for(lambda: requests.get(DRIVER + '/status', timeout=1).ok)
    result = call('POST', '/session', {'capabilities': {'alwaysMatch': {'browserName': 'firefox', 'webSocketUrl': True, 'moz:firefoxOptions': {'args': ['-headless']}}}})
    session = result['sessionId']
    call('POST', '/session/' + session + '/window/rect', {'width': 1440, 'height': 1000})
    call('POST', '/session/' + session + '/url', {'url': SITE})
    wait_for(lambda: click('Abrir'))
    wait_for(lambda: click('Ejecución'))
    wait_for(lambda: 'SESIÓN ANTERIOR' in text())
    js('for(const d of document.querySelectorAll("details")) d.open=true;')
    body = text()
    assert 'NO PUEDE INICIAR' in body, body
    assert js('return [...document.querySelectorAll("span")].find(e=>e.textContent==="Con cobertura").parentElement.innerText.includes("5")')
    assert js('return [...document.querySelectorAll("span")].find(e=>e.textContent==="Bloqueos de archivo/geometría").parentElement.innerText.includes("0")')
    run_path = fixture.job._existing_run_file(fixture.context)
    before, writes = run_path.read_bytes(), len(requests_seen)
    js('for(const d of document.querySelectorAll("details")) d.open=true;')
    assert click('Actualizar inspección')
    time.sleep(1)
    assert run_path.read_bytes() == before
    assert len(requests_seen) == writes
    from websockets.sync.client import connect
    def viewport(width):
        with connect(result['capabilities']['webSocketUrl']) as ws:
            ws.send(json.dumps({'id': 1, 'method': 'browsingContext.getTree', 'params': {}}))
            tree = json.loads(ws.recv())
            context = tree['result']['contexts'][0]['context']
            ws.send(json.dumps({'id': 2, 'method': 'browsingContext.setViewport', 'params': {'context': context, 'viewport': {'width': width, 'height': 844}}}))
            assert json.loads(ws.recv())['type'] == 'success'
        assert js('return innerWidth') == width
    for width in (1440, 390):
        viewport(width)
        js('document.querySelector(".execution-console-v2").scrollIntoView({block: "start"});')
        time.sleep(.3)
        assert js('return document.documentElement.scrollWidth <= innerWidth + 1'), f'overflow {width}'
        (ARTIFACTS / f'blocked-{width}.png').write_bytes(base64.b64decode(call('GET', '/session/' + session + '/screenshot')))
    # Separate valid-session fixture scenario; no production reference is modified.
    fixture.runtime.current_physical_session_id = original_session
    assert click('Actualizar inspección')
    wait_for(lambda: 'PUEDE INICIAR' in text() and 'NO PUEDE INICIAR' not in text())
    assert js('return [...document.querySelectorAll("button")].find(b=>b.textContent==="Iniciar trabajo").disabled')
    js('const l=[...document.querySelectorAll("label")].find(l=>l.textContent.includes("Confirmo que el spindle está preparado")); l.querySelector("input").click();')
    assert not js('return [...document.querySelectorAll("button")].find(b=>b.textContent==="Iniciar trabajo").disabled')
    with patch.object(fixture.job, '_start_worker'):
        assert click('Iniciar trabajo')
        wait_for(lambda: any(p.endswith('/job-run/start') for _, p in requests_seen))
        wait_for(lambda: fixture.job._load_run(fixture.context).get('started_at'))
    assert fixture.runtime._perform_probe_descent.call_count == 1
    assert fixture.capture.client.scripts == []
    (ARTIFACTS / 'result.json').write_text(json.dumps({'result': 'PASS', 'geometry_covered': 5, 'geometry_blocked': 0, 'read_only_inspection': True, 'explicit_spindle': True, 'widths': [1440, 390], 'requests': requests_seen}, indent=2))
    print('BROWSER_E2E PASS', flush=True)
except Exception:
    if session:
        (ARTIFACTS / 'failure.txt').write_text(text())
        (ARTIFACTS / 'failure.png').write_bytes(base64.b64decode(call('GET', '/session/' + session + '/screenshot')))
    raise
finally:
    if session:
        call('DELETE', '/session/' + session)
    if driver:
        driver.terminate()
        driver.wait(timeout=15)
    if server:
        server.should_exit = True
        thread.join(timeout=10)
    fixture.doCleanups()
