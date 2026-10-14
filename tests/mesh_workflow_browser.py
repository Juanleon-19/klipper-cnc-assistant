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
from tests.test_mesh_preparation import MeshPreparationTest
from klipper_cnc_assistant.execution.mesh_execution_service import MeshExecutionService

os.environ.update(MACHINE_MODE='simulated', MACHINE_AUTO_CONNECT='false')
ARTIFACTS = Path('/tmp/kca-mesh-stabilization/browser')
ARTIFACTS.mkdir(parents=True, exist_ok=True)
DRIVER = 'http://127.0.0.1:18808'
SITE = 'http://127.0.0.1:18807'
fixture = MeshPreparationTest()
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


def field(label, value):
    return js('const l=[...document.querySelectorAll("label")].find(l=>l.textContent.startsWith(' + json.dumps(label) + ')); if(!l) return false; const i=l.querySelector("input"); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value").set.call(i,' + json.dumps(str(value)) + '); i.dispatchEvent(new Event("input",{bubbles:true})); return true;')


def enabled(label):
    return js('const b=[...document.querySelectorAll("button")].find(b=>b.textContent.trim()===' + json.dumps(label) + '); return !!b && !b.disabled;')


try:
    fixture.setUp()
    app = fixture.capture.fixture.app
    maps = app.state.physical_map_service
    mesh = MeshExecutionService(maps)
    app.state.mesh_execution_service = mesh
    runtime = fixture.runtime
    probes = []
    release = threading.Event()
    entered = threading.Event()
    def probe(point, *, permit, **kwargs):
        runtime.physical_ownership.validate(permit)
        probes.append(point['index'])
        entered.set()
        assert release.wait(15), 'Browser did not release fake point'
        runtime._machine.update_motion(live_position=(point['x_machine'], point['y_machine'], 31), live_velocity=0)
        return dict(z_measured=30., duration_s=.001)
    runtime.probe_mesh_point = probe
    original_snapshot = runtime.snapshot
    def fresh_fake_snapshot():
        runtime._machine.update_motion(live_position=(4, 5, 31), live_velocity=0, source="websocket")
        runtime._last_websocket_message_at = time.monotonic()
        runtime._last_telemetry_at = time.monotonic()
        runtime._last_packet_at = time.monotonic()  # heartbeat from the fake serial boundary
        return original_snapshot()
    runtime.snapshot = fresh_fake_snapshot
    runtime.refresh_observed_state = fresh_fake_snapshot
    app.state.frontend_dist_dir = Path('frontend/dist').resolve()
    app.router.on_startup.clear()
    app.router.on_shutdown.clear()
    app.middleware_stack = None
    requests_seen = []
    @app.middleware('http')
    async def record(request, next_call):
        if request.method != 'GET': requests_seen.append((request.method, request.url.path))
        return await next_call(request)
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=18807, log_level='warning'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    wait_for(lambda: server.started)
    driver_log = (ARTIFACTS / 'geckodriver.log').open('w')
    driver = subprocess.Popen(['geckodriver', '--host', '127.0.0.1', '--port', '18808'], stdout=driver_log, stderr=subprocess.STDOUT)
    wait_for(lambda: requests.get(DRIVER + '/status', timeout=1).ok)
    result = call('POST', '/session', {'capabilities': {'alwaysMatch': {'browserName': 'firefox', 'webSocketUrl': True, 'moz:firefoxOptions': {'args': ['-headless']}}}})
    session = result['sessionId']
    call('POST', '/session/' + session + '/window/rect', {'width': 1440, 'height': 1000})
    call('POST', '/session/' + session + '/url', {'url': SITE})
    wait_for(lambda: click('Abrir'))
    wait_for(lambda: click('Mapa de alturas'))
    wait_for(lambda: 'Usar parámetros generales' in text())
    assert 'Estos valores vienen de la configuración general' in text()
    assert js('return [...document.querySelectorAll("label")].find(l=>l.textContent.startsWith("Paso de sonda")).querySelector("input").disabled')
    assert click('Personalizar para esta malla')
    assert not js('return [...document.querySelectorAll("label")].find(l=>l.textContent.startsWith("Paso de sonda")).querySelector("input").disabled')
    assert click('Restaurar parámetros generales')
    field('Filas', 3); field('Columnas', 3); field('Retiro uniforme', 0); field('Z segura de traslado', 5)
    assert not enabled('Limpiar vista previa')
    assert click('1. Generar vista previa de malla')
    wait_for(lambda: enabled('2. Validar límites'))
    assert not enabled('3. Armar sondeo')
    assert click('2. Validar límites')
    wait_for(lambda: 'Cobertura válida:' in text())
    success_color = js('return getComputedStyle(document.querySelector("[data-severity=SUCCESS]")).color')
    assert click('3. Armar sondeo')
    wait_for(lambda: enabled('4. Iniciar sondeo automático'))
    assert click('4. Iniciar sondeo automático')
    wait_for(entered.is_set)
    wait_for(lambda: enabled('Pausar'))
    assert click('Pausar')
    wait_for(lambda: 'Pausando; se completará el punto actual.' in text())
    release.set()
    wait_for(lambda: enabled('Reanudar'))
    assert len(probes) == 1
    assert click('Reanudar')
    wait_for(lambda: maps.get_active(fixture.project_id, fixture.operation_id)['status'] == 'MESH_COMPLETE')
    wait_for(lambda: 'MESH_COMPLETE' in text())
    assert len(probes) == len(set(probes))
    assert click('Modificar y rehacer malla')
    wait_for(lambda: 'Mapa anterior archivado.' in text())
    assert not enabled('4. Iniciar sondeo automático')
    assert click('1. Generar vista previa de malla')
    wait_for(lambda: enabled('Limpiar vista previa'))
    active_id = maps.get_active(fixture.project_id, fixture.operation_id)['map_id']
    assert click('Limpiar vista previa')
    wait_for(lambda: 'Vista previa limpia.' in text())
    assert not enabled('Limpiar vista previa')
    assert maps.get_active(fixture.project_id, fixture.operation_id)['map_id'] == active_id
    info_color = js('return getComputedStyle(document.querySelector("[data-severity=INFO]")).color')
    assert info_color != success_color
    runtime.physical_ownership.enter_recovery('test-only uncertainty')
    assert click('1. Generar vista previa de malla')
    wait_for(lambda: 'Primero verifique reposo y recupere controles' in text())
    assert 'NO SE PUEDE ARMAR' in text()
    from websockets.sync.client import connect
    for width in (1440, 390):
        with connect(result['capabilities']['webSocketUrl']) as ws:
            ws.send(json.dumps({'id': 1, 'method': 'browsingContext.getTree', 'params': {}}))
            context = json.loads(ws.recv())['result']['contexts'][0]['context']
            ws.send(json.dumps({'id': 2, 'method': 'browsingContext.setViewport', 'params': {'context': context, 'viewport': {'width': width, 'height': 844}}}))
            assert json.loads(ws.recv())['type'] == 'success'
        time.sleep(.3)
        assert js('return document.documentElement.scrollWidth <= innerWidth + 1'), f'overflow {width}'
        js('document.querySelector("[data-severity=INFO]").scrollIntoView({block: "center"})')
        time.sleep(.2)
        (ARTIFACTS / f'mesh-{width}.png').write_bytes(base64.b64decode(call('GET', '/session/' + session + '/screenshot')))
    assert runtime._perform_probe_descent.call_count == 1
    assert fixture.capture.client.scripts == []
    (ARTIFACTS / 'result.json').write_text(json.dumps({'result': 'PASS', 'widths': [1440, 390], 'points': probes, 'requests': requests_seen, 'success_color': success_color, 'info_color': info_color}, indent=2))
    print('BROWSER_E2E PASS', flush=True)
except Exception:
    if session:
        (ARTIFACTS / 'failure.txt').write_text(text())
        (ARTIFACTS / 'failure.png').write_bytes(base64.b64decode(call('GET', '/session/' + session + '/screenshot')))
    raise
finally:
    if 'release' in globals(): release.set()
    if session: call('DELETE', '/session/' + session)
    if driver:
        driver.terminate(); driver.wait(timeout=15)
    if server:
        server.should_exit = True; thread.join(timeout=10)
    fixture.doCleanups()
