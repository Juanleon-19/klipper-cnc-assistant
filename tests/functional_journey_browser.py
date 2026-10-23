"""React -> HTTP -> real services/runtime, with synthetic controller transports.

No intercepted frontend responses or physical connections. Uses the same journey
assertions as the backend regression. Firefox controls only isolated localhost.
"""
import json
import os
from pathlib import Path
import subprocess
import threading
import time

import requests
import uvicorn
from tests.test_functional_journey import FunctionalJourneyTest

os.environ.update(MACHINE_MODE='simulated', MACHINE_AUTO_CONNECT='false')
ARTIFACTS = Path('/tmp/kca-functional-journey-browser')
ARTIFACTS.mkdir(exist_ok=True)
SITE = 'http://127.0.0.1:18917'
DRIVER = 'http://127.0.0.1:18918'
fixture = FunctionalJourneyTest()
server = driver = session = None
responses = []
reject_preparation_once = False


def wait(fn, timeout=45):
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        try:
            result = fn()
            if result:
                return result
        except Exception as error:
            last = error
        time.sleep(.1)
    raise AssertionError(f'Browser timeout: {last}')


def command(method, path, payload=None):
    response = requests.request(method, DRIVER + path, json={} if method == 'POST' and payload is None else payload, timeout=60)
    response.raise_for_status()
    return response.json()['value']


def js(script):
    return command('POST', f'/session/{session}/execute/sync', {'script': script, 'args': []})


def click(label):
    return js('const b=[...document.querySelectorAll("button,a")].find(e=>e.textContent.trim()==='
              + json.dumps(label) + '); if(!b || b.disabled) return false; b.click(); return true;')


def field(label, value):
    return js('const l=[...document.querySelectorAll("label")].find(e=>e.textContent.startsWith('
              + json.dumps(label) + '));const i=l?.querySelector("input");if(!i)return false;'
              'Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value").set.call(i,'
              + json.dumps(str(value)) + ');i.dispatchEvent(new Event("input",{bubbles:true}));return true;')


def ui_post(path, payload=None):
    global reject_preparation_once
    index = len(responses)
    if path.endswith('/connect'):
        wait(lambda: click('Referencia'))
        wait(lambda: click('Conectar runtime'))
    elif path.endswith('/reset-preparation'):
        js('for(const d of document.querySelectorAll("details"))d.open=true;')
        def open_reset():
            try:
                return click('Reiniciar proceso')
            except requests.HTTPError:
                return command('GET', f'/session/{session}/alert/text')
        wait(open_reset)
        wait(lambda: command('GET', f'/session/{session}/alert/text'))
        command('POST', f'/session/{session}/alert/accept')
    elif path.endswith('/home'):
        wait(lambda: click('Realizar homing XYZ'))
    elif path.endswith('/prepare-position'):
        # A real HTTP rejection must reach the local step, not only App's banner.
        wait(lambda: 'La posición cacheada está obsoleta' in js('return document.body.innerText'))
        reject_preparation_once = True
        wait(lambda: click('Ir a posición de preparación'))
        wait(lambda: any(r['path'] == path and r['status'] == 400 for r in responses[index:]))
        wait(lambda: js('const b=[...document.querySelectorAll("button")].find(e=>e.textContent.trim()==="Ir a posición de preparación"); return b?.closest("article")?.textContent.includes("Observación simulada rechazada");'))
        index = len(responses)
        wait(lambda: click('Ir a posición de preparación'))
    elif path.endswith('/manual-control'):
        wait(lambda: click('Habilitar joystick X/Y'))
    elif path.endswith('/probe-and-capture'):
        wait(lambda: click('Guardar X/Y actual y medir referencia Z'))
    elif path.endswith('/preview'):
        wait(lambda: click('Mapa de alturas'))
        wait(lambda: field('Filas', 2))
        field('Columnas', 2); field('Retiro uniforme', payload.get('edge_margin_left_mm', 0)); field('Z segura de traslado', 5)
        wait(lambda: click('1. Generar vista previa de malla'))
    elif path.endswith('/validate'):
        wait(lambda: click('2. Validar límites'))
    elif path.endswith('/plan-from-reference'):
        wait(lambda: click('3. Armar sondeo'))
    elif path.endswith('/execute-all'):
        wait(lambda: click('4. Iniciar sondeo automático'))
    elif path.endswith('/prepare'):
        # Reload must preserve the saved reference/map and permit next phase.
        command('POST', f'/session/{session}/refresh')
        wait(lambda: click('Abrir'))
        wait(lambda: click('Ejecución'))
        wait(lambda: click('Preparar trabajo'))
    elif path.endswith('/start'):
        wait(lambda: js('const i=document.querySelector(".spindle-confirmation input");'
                        'if(!i||i.disabled)return false;if(!i.checked)i.click();return true;'))
        wait(lambda: click('Iniciar trabajo'))
    elif path.endswith('/action'):
        labels = {'confirm-spindle-stopped': 'Spindle detenido',
                  'confirm-tool-change': 'Herramienta cambiada', 'continue': 'Spindle preparado — continuar'}
        label = labels[payload['action']]
        wait(lambda: js('const b=[...document.querySelectorAll("button")].find(e=>e.textContent.trim()==='
            + json.dumps(label) + ');const i=b?.parentElement.querySelector("input[type=checkbox]");'
            'if(!i||i.disabled)return false;if(!i.checked)i.click();return true;'))
        wait(lambda: click(label))
    else:
        raise AssertionError(f'No UI mapping for {path}')
    result = wait(lambda: next((r for r in responses[index:] if r['path'] == path), None))
    assert result['status'] == 200, result
    if path.endswith('/prepare-position'):
        assert len([r for r in responses if r['path'] == path and r['status'] == 200]) == 1
    print('UI PASS', path.rsplit('/', 1)[-1], flush=True)
    return result['body']


try:
    fixture.setUp()
    app = fixture.app
    app.router.on_startup.clear()
    app.router.on_shutdown.clear()
    app.middleware_stack = None
    @app.middleware('http')
    async def record(request, next_call):
        global reject_preparation_once
        if request.method == 'POST' and request.url.path == '/api/machine/prepare-position' and reject_preparation_once:
            from starlette.responses import JSONResponse
            reject_preparation_once = False
            body = {'detail': 'Observación simulada rechazada'}
            responses.append({'path': request.url.path, 'status': 400, 'body': body})
            return JSONResponse(body, status_code=400)
        response = await next_call(request)
        if request.method == 'POST':
            body = b''.join([chunk async for chunk in response.body_iterator])
            from starlette.responses import Response
            responses.append({'path': request.url.path, 'status': response.status_code,
                              'body': json.loads(body)})
            return Response(body, status_code=response.status_code, headers=dict(response.headers))
        return response
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=18917, log_level='warning'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    wait(lambda: server.started)
    with (ARTIFACTS / 'driver.log').open('w') as driver_log:
        driver = subprocess.Popen(['geckodriver', '--host', '127.0.0.1', '--port', '18918'],
                                  stdout=driver_log, stderr=subprocess.STDOUT)
    wait(lambda: requests.get(DRIVER + '/status', timeout=1).ok)
    session = command('POST', '/session', {'capabilities': {'alwaysMatch': {
        'browserName': 'firefox', 'unhandledPromptBehavior': 'ignore', 'moz:firefoxOptions': {'args': ['-headless']}}}})['sessionId']
    command('POST', f'/session/{session}/window/rect', {'width': 1440, 'height': 1100})
    command('POST', f'/session/{session}/url', {'url': SITE})
    wait(lambda: click('Abrir'))
    fixture.post = ui_post
    fixture.test_reset_connect_home_prepare_reference_mesh_execution()
    wait(lambda: 'COMPLETADO' in js('return document.body.innerText'))
    (ARTIFACTS / 'result.json').write_text(json.dumps({'result': 'PASS',
        'actions': [{'path': r['path'], 'status': r['status']} for r in responses]}, indent=2))
    print('FUNCTIONAL_JOURNEY_BROWSER PASS', flush=True)
except Exception:
    if session:
        try:
            (ARTIFACTS / 'failure.txt').write_text(js('return document.body.innerText'))
        except Exception:
            pass
    raise
finally:
    if session:
        command('DELETE', f'/session/{session}')
    if driver:
        driver.terminate(); driver.wait(timeout=15)
    if server:
        server.should_exit = True; thread.join(timeout=10)
    fixture.doCleanups()
