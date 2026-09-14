"""Manual Firefox WebDriver/BiDi review harness, fixed isolated localhost targets.

Start geckodriver on 18766 and product_review_server.py on 18765 first.
Commands: start, click LABEL, tour, visual_fakes, runtime_fakes, stop.
Fake states are produced by product_review_fake_states.py. Runtime fixtures
replace browser GET responses only and block writes while they are installed.
Artifacts and session state stay under /tmp/kca-astra-review.
"""
import requests, json, sys, time, base64
from pathlib import Path

ROOT = Path('/tmp/kca-astra-review')
DRIVER = 'http://127.0.0.1:18766'
def call(method, path, payload=None):
    r=requests.request(method, DRIVER+path, json=payload, timeout=60)
    if not r.ok: print(r.text,flush=True)
    r.raise_for_status()
    return r.json()['value']
if sys.argv[1]=='start':
    v=call('POST','/session',{'capabilities':{'alwaysMatch':{'browserName':'firefox','webSocketUrl':True,'moz:firefoxOptions':{'args':['-headless']}}}})
    (ROOT/'browser-session.json').write_text(json.dumps(v))
v=json.loads((ROOT/'browser-session.json').read_text()); sid=v['sessionId']; base='/session/'+sid
def js(code): return call('POST',base+'/execute/sync',{'script':code,'args':[]})
action=sys.argv[1]
if action=='runtime_fakes':
    native=requests.get('http://127.0.0.1:18765/api/machine/status',timeout=10).json()
    assert native['mode']=='SIMULATED' and not native['arduino']['open']
    js('window.originalFetch??=window.fetch;window.fetch=(...args)=>{const url=String(args[0]);if(args[1]?.method&&! ["GET","HEAD"].includes(args[1].method.toUpperCase()))return Promise.reject(Error("Read-only visual fixture"));return url==="/api/machine/status"?Promise.resolve(new Response(JSON.stringify(window.reviewRuntime),{status:200,headers:{"Content-Type":"application/json"}})):window.originalFetch(...args);};')
    for state in ('DISCONNECTED','CONNECTED','ERROR'):
        runtime=json.loads(json.dumps(native));runtime['mode']='PHYSICAL';runtime['state']=state
        runtime['last_error']='Error sintético de conexión para revisión visual' if state=='ERROR' else None
        runtime['moonraker'].update(http_connected=state=='CONNECTED',websocket_connected=state=='CONNECTED',websocket_state=state)
        runtime['klipper'].update(ready=state=='CONNECTED',state='ready' if state=='CONNECTED' else 'disconnected',homed_axes='xyz' if state=='CONNECTED' else '')
        js('window.reviewRuntime='+json.dumps(runtime)+';[...document.querySelectorAll("button")].find(b=>b.textContent.includes("Sistema")).click();')
        time.sleep(.6)
        js('[...document.querySelectorAll("button")].find(b=>b.textContent==="Actualizar runtime").click();')
        time.sleep(1.4)
        js('[...document.querySelectorAll("button")].find(b=>b.textContent.includes("Proyectos")).click();')
        time.sleep(.7)
        js('[...document.querySelectorAll("button")].find(b=>b.textContent==="Referencia").click();')
        time.sleep(.7)
        js('document.querySelector(".workspace-view-panel").scrollIntoView();window.scrollBy(0,-document.querySelector(".topbar--app").clientHeight);')
        (ROOT/f'runtime-fake-{state}.json').write_text(json.dumps(js('return {text:document.body.innerText,buttons:[...document.querySelectorAll("button")].map(b=>({text:b.textContent,disabled:b.disabled}))}'),ensure_ascii=False,indent=2))
        (ROOT/f'runtime-fake-{state}.png').write_bytes(base64.b64decode(call('GET',base+'/screenshot')))
    js('[...document.querySelectorAll("summary")].find(b=>b.textContent.includes("Z segura")||b.textContent.includes("Configuración de movimientos"))?.click();')
    js('document.querySelectorAll(".workspace-view-panel details").forEach(d=>d.open=true);')
    (ROOT/'runtime-settings-fake.json').write_text(json.dumps(js('return {text:document.body.innerText}'),ensure_ascii=False,indent=2))
    js('const l=[...document.querySelectorAll("label")].find(e=>e.textContent.startsWith("Z de aproximación a referencia"));l?.scrollIntoView();window.scrollBy(0,-document.querySelector(".topbar--app").clientHeight);')
    (ROOT/'runtime-settings-fake.png').write_bytes(base64.b64decode(call('GET',base+'/screenshot')))
    js('window.fetch=window.originalFetch;delete window.originalFetch;delete window.reviewRuntime;location.reload();')
    print('RUNTIME_VISUAL_FIXTURES PASS; backend remained SIMULATED',flush=True);sys.exit()
if action=='visual_fakes':
    from websockets.sync.client import connect
    with connect(v['capabilities']['webSocketUrl']) as ws:
        def response(identifier):
            while True:
                item=json.loads(ws.recv())
                if item.get('id')==identifier:return item
        ws.send(json.dumps({'id':10,'method':'browsingContext.getTree','params':{}}))
        context=response(10)['result']['contexts'][0]['context']
        for width,height in [(1440,900),(390,844)]:
            ws.send(json.dumps({'id':11,'method':'browsingContext.setViewport','params':{'context':context,'viewport':{'width':width,'height':height}}}));response(11)
            for name in ('running','paused','recovery','cancelled','ready','spindle-stop','tool-change','ready-to-resume'):
                fixture=json.loads((ROOT/'fake-states'/(name+'.json')).read_text())
                js('window.reviewLive='+json.dumps(fixture)+';window.originalFetch??=window.fetch;window.fetch=(...args)=>String(args[0]).includes("/execution/live?")?Promise.resolve(new Response(JSON.stringify(window.reviewLive),{status:200,headers:{"Content-Type":"application/json"}})):window.originalFetch(...args);')
                time.sleep(1.3)
                observed=js('return {state:document.body.innerText,buttons:[...document.querySelectorAll(".workspace-view-panel button")].map(b=>({label:b.textContent,disabled:b.disabled})),viewport:[innerWidth,innerHeight],width:document.documentElement.scrollWidth}')
                assert fixture['run']['status'] in observed['state'],name
                js('document.querySelector(".workspace-view-panel").scrollIntoView();window.scrollBy(0,-document.querySelector(".topbar--app").clientHeight);')
                (ROOT/f'fake-{width}-{name}.json').write_text(json.dumps(observed,ensure_ascii=False,indent=2))
                (ROOT/f'fake-{width}-{name}.png').write_bytes(base64.b64decode(call('GET',base+'/screenshot')))
    js('window.fetch=window.originalFetch;delete window.originalFetch;delete window.reviewLive;')
    print('FAKE_BROWSER_STATES PASS 16/16',flush=True);sys.exit()
if action=='steps':
    for step in json.loads(Path(sys.argv[2]).read_text()):
        if 'click' in step:
            js('const b=[...document.querySelectorAll("button,a,summary")].find(b=>b.textContent.trim()==='+json.dumps(step['click'])+');if(!b)throw Error("missing button");if(b.disabled)throw Error("disabled button");b.click();')
            time.sleep(1)
        if 'js' in step:print(json.dumps(js(step['js']),ensure_ascii=False),flush=True)
        if 'wait' in step:time.sleep(step['wait'])
        if 'snapshot' in step:
            stem=step['snapshot']
            (ROOT/(stem+'.json')).write_text(json.dumps(js('return {viewport:[innerWidth,innerHeight],width:document.documentElement.scrollWidth,text:document.body.innerText,buttons:[...document.querySelectorAll("button")].map(b=>({text:b.textContent,disabled:b.disabled})),inputs:[...document.querySelectorAll("input,select")].map(e=>({label:e.getAttribute("aria-label"),type:e.type,value:e.value,text:e.closest("label")?.textContent}))}'),ensure_ascii=False,indent=2))
            (ROOT/(stem+'.png')).write_bytes(base64.b64decode(call('GET',base+'/screenshot')))
        if 'fake_state' in step:
            fixture=json.loads((ROOT/'fake-states'/(step['fake_state']+'.json')).read_text())
            js('window.reviewLive='+json.dumps(fixture)+';window.originalFetch??=window.fetch;window.fetch=(...args)=>String(args[0]).includes("/execution/live?")?Promise.resolve(new Response(JSON.stringify(window.reviewLive),{status:200,headers:{"Content-Type":"application/json"}})):window.originalFetch(...args);')
            time.sleep(1.5)
    sys.exit()
if action=='record':
    from websockets.sync.client import connect
    with connect(v['capabilities']['webSocketUrl']) as ws, (ROOT/'browser-events.jsonl').open('a') as out:
        ws.send(json.dumps({'id':1,'method':'session.subscribe','params':{'events':['log.entryAdded','network.responseCompleted','network.fetchError']}}))
        for message in ws:
            out.write(message+'\n');out.flush()
    sys.exit()
if action=='tour':
    for width,height in [(1440,900),(390,844)]:
        from websockets.sync.client import connect
        with connect(v['capabilities']['webSocketUrl']) as ws:
            def response(identifier):
                while True:
                    item=json.loads(ws.recv())
                    if item.get('id')==identifier:return item
            ws.send(json.dumps({'id':2,'method':'browsingContext.getTree','params':{}}))
            tree=response(2); context=tree['result']['contexts'][0]['context']
            ws.send(json.dumps({'id':3,'method':'browsingContext.setViewport','params':{'context':context,'viewport':{'width':width,'height':height}}}))
            print(response(3),flush=True)
        for label in ['Archivo','Trayectoria','Referencia','Mapa de alturas','Ejecución']:
            js('const b=[...document.querySelectorAll("button")].find(b=>b.textContent.trim()==='+json.dumps(label)+');b.click();b.scrollIntoView({block:"start"});')
            time.sleep(1.5)
            stem=f'{width}-{label.replace(" ","-")}'
            (ROOT/(stem+'.json')).write_text(json.dumps(js('return {viewport:[innerWidth,innerHeight],width:document.documentElement.scrollWidth,text:document.body.innerText,overflow:[...document.querySelectorAll("body *")].filter(e=>{let r=e.getBoundingClientRect();return r.width>innerWidth+2 && getComputedStyle(e).position!=="fixed"}).map(e=>({tag:e.tagName,cls:e.className,width:e.getBoundingClientRect().width})).slice(0,20)}'),ensure_ascii=False,indent=2))
            (ROOT/(stem+'.png')).write_bytes(base64.b64decode(call('GET',base+'/screenshot')))
    sys.exit()
if action=='start':
    call('POST',base+'/window/rect',{'width':1440,'height':1038})
    call('POST',base+'/url',{'url':'http://127.0.0.1:18765'})
    time.sleep(2)
elif action=='js':
    print(json.dumps(js(sys.argv[2]),ensure_ascii=False,indent=2)); sys.exit()
elif action=='click':
    label=sys.argv[2]
    print(js('const b=[...document.querySelectorAll("button,a")].find(b=>b.textContent.trim()==='+json.dumps(label)+'); if(!b) throw Error("missing button"); b.click(); return b.disabled;'))
    time.sleep(1)
elif action=='size':
    w,h=map(int,sys.argv[2:4]); call('POST',base+'/window/rect',{'width':w,'height':h+138}); time.sleep(1)
    actual=js('return [innerWidth,innerHeight]'); call('POST',base+'/window/rect',{'width':w,'height':h+138+h-actual[1]})
elif action=='shot':
    (ROOT/(sys.argv[2]+'.png')).write_bytes(base64.b64decode(call('GET',base+'/screenshot')))
elif action=='stop':
    call('DELETE',base); sys.exit()
print(json.dumps(js('return {viewport:[innerWidth,innerHeight],width:document.documentElement.scrollWidth,text:document.body.innerText,buttons:[...document.querySelectorAll("button")].map(b=>({text:b.textContent,disabled:b.disabled,title:b.title}))}'),ensure_ascii=False,indent=2))
