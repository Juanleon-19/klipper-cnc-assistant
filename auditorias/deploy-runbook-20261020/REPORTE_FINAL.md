# Runbook de transición de instalación — 2026-10-20

**DEPLOY_RUNBOOK_READY_FOR_EXTERNAL_AUDIT. Despliegue NO autorizado.**

Este documento sustituye la secuencia incompleta del predeploy anterior: no basta con llamar a stop y abortar la actualización al observar un timeout. La unidad actual puede enviar SIGKILL automáticamente. Antes de cualquier parada futura debe aprobarse cómo impedir esa escalada y cómo demostrar la seguridad independiente de la CNC.

## 1. Alcance y fuentes

Leídos completos los informes de preparación y safe-shutdown. Inspeccionados Git, unit/drop-in, systemd 255, Uvicorn instalado 0.51.0, código CLI/lifespan y procfs. Fuentes primarias locales: `uvicorn/server.py` (`handle_exit`, `capture_signals`, `shutdown`, `_wait_tasks_to_complete`), `uvicorn/lifespan/on.py:shutdown`, manuales instalados `systemd.kill(5)` y `systemd.service(5)`. No se consultaron endpoints productivos, ni siquiera health, y no se contactó Moonraker/Arduino/Klipper.

Trabajo aislado: rama `audit/deploy-runbook-20261020`, worktree `/home/impresora/kca-worktrees/deploy-runbook-20261020`, basado exactamente en `cece9559e8d212ce489f7cb4ec07bf8b998137d5`. Solo documentación y prueba independiente; no cambios de aplicación.

## 2. Instalación observada

| Elemento | Evidencia actual |
|---|---|
| Main remoto | cece9559e8d212ce489f7cb4ec07bf8b998137d5, comprobado por ls-remote |
| Checkout productivo | main, b0dbcdc691a145b4b086dcd9cc2dcf1795e10880 |
| Cambios locales | AGENTS.md modificado; .codex/ y graphify-out/ sin seguimiento, preservados |
| Servicio | active/running; PID 1363564; NRestarts=0; proceso iniciado 17 de octubre |
| Type / WorkingDirectory | simple / repositorio productivo |
| ExecStart | `.venv/bin/python -m klipper_cnc_assistant serve --host 127.0.0.1 --port 8000 --data-dir .../data` |
| ExecStop | No definido en unidad ni drop-in |
| KillMode / KillSignal | control-group / SIGTERM (15) |
| TimeoutStopSec | 90 segundos |
| TimeoutStopFailureMode | terminate |
| SendSIGKILL / FinalKillSignal | yes / SIGKILL (9) |
| Restart / RestartSec | on-failure / 3 segundos |
| Entorno relevante | physical, MACHINE_AUTO_CONNECT=false; no se reproducen identificadores seriales |
| Árbol | Un proceso en cgroup, siete hilos observados, sin procesos hijos |
| Esperas | ep_poll y futex; no permiten inferir por sí solas bloqueo ni reposo físico |
| Descriptores | 10 sockets, 2 anon_inode, 1 device, 1 archivo; cero tty/serial observados |
| Proxy | Tailscale Serve, tailnet solamente, `/` hacia localhost:8000 |

PID/FD/threads son observaciones puntuales, no certificados de ausencia de operaciones ni identidad semántica de workers. No se hizo inyección ni introspección mutante del proceso. El SHA comprobado corresponde al checkout; la identidad del proceso se apoya además en su ExecStart/WorkingDirectory, arranque y despliegue conocido, no en un hash de todos sus módulos ya cargados.

El virtualenv usa instalación editable que lee `src` del checkout. Python ya cargado y nuevos imports podrían mezclar versiones si se actualiza código en vivo. El frontend lee `frontend/dist`; un cambio de index/assets afecta solicitudes sin esperar restart. Por ello ambos se sustituyen solamente después de confirmar salida y exclusión.

No cambian pyproject.toml, package.json ni package-lock entre versión instalada y objetivo; no se requiere instalar dependencias. Código, venv, data y respaldos privados están separados lógicamente, aunque código/data/venv comparten el directorio de instalación. No copiar un worktree completo sobre producción.

## 3. Integridad y estado lógico leído desde archivos

Respaldo privado previo: 810/810 archivos verificados. Datos productivos: 535/535 coinciden con el manifiesto. JobRun: SPINDLE_STOP_REQUIRED; operación 1 COMPLETED y cuatro PENDING. Cero mapas candidatos a reconciliación automática de worker en el arranque. No se alteró ningún archivo de datos.

Ownership RECOVERY con productor retenido procede del informe anterior: **no se revalidó por API en esta fase**. Tampoco se confirmó ahora estado remoto, velocidad, homing o shutdown de Klipper. El operador debe mantener apagados CNC/Arduino; esa declaración previa no se sustituye por una comprobación física realizada por software. Todo ello se vuelve a evaluar bajo autorización de mantenimiento.

La diferencia Graphify histórica está documentada; no se consultó Graphify ni se restauró caché. El hash debe compararse contra su baseline posterior al incidente, no restaurarlo a la copia antigua.

## 4. Qué ocurre realmente al cerrar

1. Sin ExecStop, una parada de systemd inicia SIGTERM al cgroup conforme a KillMode=control-group. systemd también usa SIGCONT para permitir terminar procesos suspendidos; no equivale a un comando CNC.
2. Uvicorn marca should_exit, cierra listeners y pide cerrar conexiones. Espera respuestas/tareas antes del lifespan. `timeout_graceful_shutdown=None` en ambas versiones: esta espera no tiene plazo propio configurado.
3. El callback antiguo llama a runtime.stop y rechaza la raíz JOB_EXECUTION retenida antes del cleanup. Uvicorn registra `Application shutdown failed. Exiting.` y continúa su salida; el error NO prueba que siga vivo.
4. Uvicorn 0.51 restaura y vuelve a emitir la señal capturada. En los hijos probados, tanto cierre correcto como fallido terminan con returncode=-15. Por tanto, ni un exit signal TERM ni Result=success de systemd demuestran cleanup correcto.
5. Si el proceso no termina en 90s, con la unidad actual systemd puede enviar SIGKILL a los restantes procesos. `Restart=on-failure` no relanza un servicio detenido mediante una operación stop explícita; no usar señales externas como sustituto de ese control.
6. Al salir realmente un proceso, el kernel cierra sus FDs. Eso es distinto del cleanup de la aplicación: sockets o serial pueden cerrarse abruptamente, escrituras pendientes pueden perderse y la CNC remota puede seguir ejecutando un archivo. Si hay otro proceso con descriptores heredados, los recursos pueden persistir: comprobar cgroup, hijos y listeners, no solo MainPID.
7. Hilos daemon pueden desaparecer al terminar el proceso sin finalizar cleanup. Threads no-daemon, executor, handlers/IO o conexiones retenidas pueden prolongar la salida. La ausencia de tty en esta inspección no autoriza una transición futura.

El nuevo protocolo protege el cierre **local** después de entrar al lifespan; no detiene automáticamente la CNC, no libera la raíz física, no restaura tokens y no impone un límite previo al lifespan. Un worker no cooperativo produce fallo explícito y no cierra transportes; aun así Uvicorn puede salir después. Se debe comprobar el resultado, no asumirlo.

## 5. Pruebas aisladas de señales y timeouts

Reproducción incluida: `validate_signals.py`; resultados completos: `SIGNAL_RESULTS.json`. Extrae código antiguo desde Git a un temporal, usa el código aprobado del worktree, crea una aplicación simulada con raíz sintética retenida, manager/HTTP falsos y un registro temporal. Sitecustomize bloquea conexiones salientes en cada hijo. Solo el padre consulta puertos efímeros de sus hijos. No hay servicios de prueba systemd ni dependencias instaladas.

| Código / escenario | Señal y evidencia | Resultado |
|---|---|---|
| Antiguo, raíz retenida sin request | SIGTERM; salida -15 en 0.215s; callback fallido, sin cleanup | Reproducción PASS |
| Antiguo, request retenida | Tras 0.4s sigue vivo y no entró al cleanup; se libera la barrera fake; sale -15 | PASS |
| Aprobado, raíz retenida | SIGTERM; salida -15 en 0.265s; manager y HTTP cerrados | PASS |
| Aprobado, request retenida | Misma espera previa al lifespan; al liberar request hace cleanup y sale | PASS |
| Aprobado, productor no cooperativo | Plazo simulado 0.05s; callback falla, transportes no se cierran; salida -15 | FAIL-CLOSED comprobado |

**5/5 escenarios PASS**, bytes del registro conservados en todos. Los 15 bloques Bash del runbook pasaron `bash -n` sin ejecutarlos. Los helpers de respaldo/verificación se probaron sobre un árbol sintético: copia íntegra aceptada y modificación de datos rechazada. Tiempos orientativos del host, no SLA. No se envió SIGKILL ni se forzó ningún hijo. Las barreras se liberaron por archivos temporales para finalizar pruebas. La prueba de espera demuestra el orden y ausencia de timeout configurado, no una espera empírica infinita.

Comando reproducible desde este worktree:

```bash
MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONDONTWRITEBYTECODE=1 \
/home/impresora/klipper-cnc-assistant/.venv/bin/python \
  auditorias/deploy-runbook-20261020/validate_signals.py
```

Escalada real systemd, cierre de FD productivos, reposo remoto y efectos eléctricos al desconectar serial: **NOT_RUN**. Política de escalada establecida mediante configuración/manual instalado, no mediante un ensayo destructivo. Los 800 tests aprobados no se repitieron; no se modificó la aplicación.

## 6. Matriz GO/NO-GO para una ventana posterior

| Condición | Decisión |
|---|---|
| Commit o dependencias distintos a lo aprobado; local changes inesperados | NO-GO |
| Hash crítico distinto sin explicación/autorización | NO-GO; no restaurar datos automáticamente |
| Movimiento, impresión remota activa/ambigua o comando local en vuelo | NO-GO |
| Estado remoto inaccesible | NO-GO salvo aislamiento físico verificable y documentado, autorizado como alternativa específica; nunca inferir velocidad cero |
| Solo raíz lógica JOB retenida, sin worker/dispatch/hijo/operación y con aislamiento/seguridad independientes confirmados | Puede evaluarse salida antigua controlada, con aprobación explícita de la excepción de cleanup conocida |
| Shutdown antiguo falla como el caso conocido, proceso/cgroup completamente ausentes, datos idénticos y seguridad externa continúa | Gate de revisión humana; no continuar automáticamente; distinguir salida segura autorizada de cleanup exitoso |
| Timeout, proceso restante, nuevos errores, datos cambiados, otro listener o incertidumbre | ABORTAR; conservar guardia anti-escalada, no actualizar ni arrancar |
| SendSIGKILL=yes sin política de escalada explícitamente autorizada | NO-GO para esta propuesta no-forzada |
| No se autoriza la guardia temporal de systemd propuesta | NO-GO; no usar stop con esperanza de que termine antes de 90s |
| Objetivo arranca desconectado, datos íntegros, sin workers físicos inesperados | Instalación diagnóstica válida; NO autorización operativa CNC |

## 7. Runbook: comandos SOLO DOCUMENTADOS, NO ejecutados

No ejecutar como bloque continuo. Cada gate exige evidencia y aprobación. La guardia systemd propuesta cambia configuración temporal **solo en una ventana futura con autorización separada**; no se aplicó ahora. Si el auditor no la acepta, la transición queda bloqueada.

### P0. Autorizaciones y variables

Autorización A: inspección productiva durante ventana, incluida consulta read-only de runtime y, por separado, estado remoto/aislamiento físico. Autorización B: guardia temporal systemd y parada exclusiva del Assistant. Autorización C: aceptar evidencia de salida del proceso antiguo pese al error conocido; actualizar y arrancar software. Autorización D: rollback si hiciera falta. Reconciliación física NO incluida en ninguna de ellas.

Cerrar navegadores de operadores y suspender uso de APIs durante la ventana; no modificar Tailscale. Esto requiere coordinación humana y no es una barrera técnica contra usuarios no cooperativos. Sin exclusividad de mantenimiento, NO-GO.

```bash
KCA_REPO=/home/impresora/klipper-cnc-assistant
KCA_STAGE=/home/impresora/kca-worktrees/safe-application-shutdown-20261020
KCA_OLD=b0dbcdc691a145b4b086dcd9cc2dcf1795e10880
KCA_TARGET=cece9559e8d212ce489f7cb4ec07bf8b998137d5
KCA_UNIT=klipper-cnc-assistant.service
cd "$KCA_REPO"
```

### P1. Preflight read-only bajo autorización A

```bash
test "$(git branch --show-current)" = main
test "$(git rev-parse HEAD)" = "$KCA_OLD"
git status --short
git ls-remote origin refs/heads/main
git merge-base --is-ancestor "$KCA_OLD" "$KCA_TARGET"
git diff --name-only "$KCA_OLD..$KCA_TARGET"
systemctl cat "$KCA_UNIT"
systemctl show "$KCA_UNIT" -p MainPID -p ActiveState -p SubState -p NRestarts -p KillMode -p KillSignal -p SendSIGKILL -p TimeoutStopUSec -p Restart
KCA_OLD_PID=$(systemctl show "$KCA_UNIT" -p MainPID --value)
ps -p "$KCA_OLD_PID" -o pid,ppid,lstart,stat,nlwp,comm
pstree -p "$KCA_OLD_PID"
curl --max-time 5 -fsS http://127.0.0.1:8000/api/machine/status
```

Auditar de nuevo que machine/status es snapshot sin escritura en el SHA instalado; no GET execution/live, health, recovery ni acciones. Exigir active_operation ausente, hijos=0, dispatching=0 y evidencia de ausencia de workers físicos. producer_active puede ser una raíz lógica: no declararlo fantasma por antigüedad. Si no puede distinguirse de un productor vivo, ABORTAR.

**Estado CNC: procedimiento separado que hoy NO se ejecuta.** Tras autorización explícita para consultar el remoto, se puede observar, sin comandos:

```bash
curl --max-time 5 -fsS 'http://127.0.0.1:7126/printer/objects/query?webhooks=state,state_message&toolhead=homed_axes,position&motion_report=live_position,live_velocity&print_stats=state,filename&virtual_sdcard=is_active'
```

Campos ausentes/desactualizados no significan reposo. Exigir evidencia de archivo inactivo y sin movimiento, identidad compatible y confirmación del operador de spindle físicamente detenido. Si está apagada e inaccesible, no encender ni reconectar para este despliegue: requerir una autorización alternativa documentada de aislamiento de energía, imposibilidad de movimiento autónomo y cierre seguro de transportes. Shutdown/homing perdido no se resuelven en esta ventana y prohíben reanudación.

### P2. Respaldo y baseline privados

Solo tras aprobar exclusividad y datos estables, antes de señales:

```bash
umask 077
KCA_BACKUP=$(mktemp -d /home/impresora/kca-private-backups/deploy-cece955-XXXXXX)
printf '%s\n' "$KCA_BACKUP"
git status --short > "$KCA_BACKUP/git-status"
git rev-parse HEAD > "$KCA_BACKUP/old-head"
systemctl cat "$KCA_UNIT" > "$KCA_BACKUP/unit-before.txt"
cp -a data AGENTS.md .codex graphify-out frontend/dist "$KCA_BACKUP/"
python3 - "$KCA_REPO" "$KCA_BACKUP" <<'PY'
import hashlib,json,sys
from pathlib import Path
repo,backup=map(Path,sys.argv[1:])
rows=[]
for name in ['data','AGENTS.md','.codex','graphify-out','frontend/dist']:
 p=repo/name
 for f in ([p] if p.is_file() else sorted(p.rglob('*'))):
  if not f.is_file(): continue
  rel=f.relative_to(repo); digest=hashlib.sha256(f.read_bytes()).hexdigest()
  saved=backup/('dist' if name=='frontend/dist' else name)
  if not p.is_file(): saved=saved/f.relative_to(p)
  assert hashlib.sha256(saved.read_bytes()).hexdigest()==digest
  rows.append({'path':str(rel),'sha256':digest})
(backup/'baseline.json').write_text(json.dumps(rows,indent=2))
PY
```

Guardar ruta y baseline de forma privada. Revalidar también respaldo anterior 810/810; la excepción Graphify antigua no autoriza otras diferencias. No copiar secretos a Git/informes públicos.

### T1. Guardia contra escalada — requiere autorización B adicional

Mantener KillMode=control-group, KillSignal=SIGTERM y plazo 90s; no aumentar timeout. Propuesta de drop-in runtime exclusivo, sin sobrescribir archivos existentes:

```bash
KCA_GUARD=/run/systemd/system/klipper-cnc-assistant.service.d/99-kca-maintenance-no-force.conf
test ! -e "$KCA_GUARD"
sudo install -d -m 0755 /run/systemd/system/klipper-cnc-assistant.service.d
printf '[Service]\nSendSIGKILL=no\nRestart=no\n' | sudo tee "$KCA_GUARD" >/dev/null
sudo systemctl daemon-reload
systemctl show "$KCA_UNIT" -p SendSIGKILL -p Restart -p KillMode -p KillSignal -p TimeoutStopUSec
test "$(systemctl show "$KCA_UNIT" -p SendSIGKILL --value)" = no
test "$(systemctl show "$KCA_UNIT" -p Restart --value)" = no
```

No se modifica el servicio Klipper/Moonraker ni Tailscale. `daemon-reload` no es un restart, pero sí modificación del manager: requiere aprobación específica. Si no se verifica la guardia, NO enviar stop. Si el servicio desaparece inesperadamente antes de stop, NO relanzarlo automáticamente.

### T2. Parada única y observación

```bash
KCA_STOP_TIME=$(date --iso-8601=seconds)
sudo systemctl stop --no-block "$KCA_UNIT"
```

No repetir stop, no restart y no señales directas. Observar con las siguientes lecturas; una automatización futura debe limitarse a leer, nunca escalar:

```bash
systemctl show "$KCA_UNIT" -p MainPID -p ActiveState -p SubState -p Result -p ExecMainCode -p ExecMainStatus -p NRestarts
journalctl -u "$KCA_UNIT" --since "$KCA_STOP_TIME" --no-pager
ps -p "$KCA_OLD_PID" -o pid,lstart,stat,nlwp,comm
cat /sys/fs/cgroup/system.slice/klipper-cnc-assistant.service/cgroup.procs
ss -ltnp 'sport = :8000'
```

La ausencia del directorio cgroup puede ser normal si fue eliminado vacío; verificar además PID y listener. PID debe identificarse con su start time para no confundir reutilización. MainPID=0 aislado no basta.

Si no termina en 60s: ABORTAR la actualización, avisar y mantener observación hasta resolver el stop pendiente. **No retirar la guardia, no activar SendSIGKILL, no cancelar el proceso, no iniciar otro Assistant.** Si llega a 90s y permanece vivo, no forzar: el resultado es NO-GO y requiere diagnóstico/autorización nuevos. Nunca dejar una instrucción de “abortar” que reintroduzca escalada automática.

### T3. Gate humano después de la salida

Esperado en versión antigua con raíz retenida: error de callback conocido seguido de salida; NO llamarlo cleanup exitoso. Autorizar continuar solo si se cumplen todos:

- PID antiguo ausente, cgroup vacío/ausente, sin hijos, puerto 8000 sin listener ajeno.
- No hubo SIGKILL, timeout, otro error distinto al conocido ni nuevo proceso automático.
- Seguridad física independiente e aislamiento siguen vigentes; ningún trabajo remoto puede avanzar autónomamente.
- Hashes de datos y JobRun idénticos al baseline; operación 1 sigue COMPLETED, restantes PENDING.
- Se acepta explícitamente que el cierre de FDs fue por salida del proceso antiguo, no por cleanup. La pérdida de la sesión obliga a recuperar evidencias después, en otra fase.

Si falta una condición: no sustituir código. No borrar locks ni editar ownership. Un archivo de lock existente no significa lock retenido; no borrarlo ni usarlo como autoridad única. El proceso nuevo conserva su adquisición exclusiva habitual al conectar, pero en esta ventana no se conecta.

### T4. Sustitución autorizada del código y frontend

Verificar baseline antes de tocar código. Entonces, autorización C:

```bash
git fetch origin main
test "$(git rev-parse origin/main)" = "$KCA_TARGET"
git merge-base --is-ancestor HEAD origin/main
git diff --name-only HEAD..origin/main
# Gate: ningún conflicto con AGENTS.md, .codex/, graphify-out/ ni data/.
git merge --ff-only origin/main
test "$(git rev-parse HEAD)" = "$KCA_TARGET"
git status --short
```

Sin reset/stash/clean/force. No instalar dependencias. Si el remoto cambió, parar y revisar, no incorporar otros commits.

El frontend no cambió entre f897ab7 y cece955. El build aislado previo y su manifest se pueden reutilizar solo si siguen íntegros y coincide esa comparación:

```bash
git diff --exit-code f897ab70fe08dcf57811757540ca4eb80f45b94b "$KCA_TARGET" -- frontend
sha256sum -c /tmp/kca-predeploy-f897ab7-frontend.sha256
KCA_BUILD=/home/impresora/kca-worktrees/deploy-preparation-20261020/frontend/dist
cp -a "$KCA_BUILD/assets/." frontend/dist/assets/
install -m 0644 "$KCA_BUILD/index.html" frontend/dist/.index.html.cece955
mv -T frontend/dist/.index.html.cece955 frontend/dist/index.html
cmp "$KCA_BUILD/index.html" frontend/dist/index.html
```

Si faltan temporales o falla checksum, preparar de nuevo en staging bajo autorización, nunca reconstruir sobre dist servido a ciegas. Conservar assets viejos para rollback. No restaurar datos.

### T5. Arranque diagnóstico exclusivo

Antes: validar MACHINE_AUTO_CONNECT=false, ausencia de workers de mapa persistidos a reconciliar y baseline de data intacto. Mantener por ahora Restart=no y SendSIGKILL=no para evitar loops/escalada durante comprobaciones.

```bash
KCA_START_TIME=$(date --iso-8601=seconds)
sudo systemctl start "$KCA_UNIT"
```

Se inicia únicamente Assistant. No conecta CNC/Arduino por instrucciones manuales ni reconcilia recovery. No reutilizar tokens históricos como si pertenecieran a la nueva sesión.

### P3. Postflight read-only

```bash
test "$(git rev-parse HEAD)" = "$KCA_TARGET"
systemctl show "$KCA_UNIT" -p MainPID -p ActiveState -p SubState -p NRestarts -p Result
journalctl -u "$KCA_UNIT" --since "$KCA_START_TIME" --no-pager
curl --max-time 5 -fsS http://127.0.0.1:8000/api/machine/status
curl --max-time 5 -fsS http://127.0.0.1:8000/openapi.json -o /tmp/kca-cece955-openapi.json
curl --max-time 5 -fsS http://127.0.0.1:8000/ -o /tmp/kca-cece955-index.html
cmp frontend/dist/index.html /tmp/kca-cece955-index.html
tailscale serve status
```

Comprobar desde navegador del operador la URL tailnet indicada por serve status, solo página/estáticos; no pulsar acciones y no consultar execution/live. Una comparación exacta adicional puede hacerse con `curl --max-time 10 -fsS "$KCA_TAILNET_URL/" -o /tmp/kca-cece955-tailnet-index.html` y `cmp frontend/dist/index.html /tmp/kca-cece955-tailnet-index.html`, definiendo KCA_TAILNET_URL con la URL observada y autorizada, no una dirección inventada.

Confirmar no worker/start/movimiento/sondeo/reconexión inesperados en journal. La ausencia de mensajes no demuestra sola ausencia física; se combina con aislamiento y estado diagnóstico. OFFLINE/DISCONNECTED por equipos apagados es esperado. Cualquier auto-conexión inesperada: detener la secuencia y pedir decisión, no enviar comandos a la CNC.

Verificación de datos y paths protegidos:

```bash
python3 - "$KCA_REPO" "$KCA_BACKUP/baseline.json" <<'PY'
import hashlib,json,sys
from pathlib import Path
root=Path(sys.argv[1]); rows=json.loads(Path(sys.argv[2]).read_text())
for row in rows:
 if row['path'].startswith('frontend/dist/'): continue
 p=root/row['path']; assert p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest()==row['sha256'], row['path']
expected={r['path'] for r in rows if r['path'].startswith('data/')}
actual={str(p.relative_to(root)) for p in (root/'data').rglob('*') if p.is_file()}
assert actual==expected, 'Cambió el inventario de datos'
print('Datos y paths protegidos íntegros')
PY
git status --short
```

Tras aceptación explícita del postflight y **sin stop pendiente ni procesos antiguos**, se puede retirar únicamente el drop-in creado por esta ventana y hacer daemon-reload. Autorizar por separado restaurar la política original SendSIGKILL=yes/Restart=on-failure; no restaurarla a ciegas si se decide mantener una política no-forzada.

```bash
sudo rm -- "$KCA_GUARD"
sudo systemctl daemon-reload
systemctl show "$KCA_UNIT" -p SendSIGKILL -p Restart
```

Esto no es una orden para ejecutar hoy ni para restaurar otras configuraciones. Nunca eliminar drop-ins preexistentes.

## 8. Rollback condicionado, no restauración del trabajo

Requiere autorización D, aislamiento físico mantenido, ninguna nueva ejecución, exclusividad y guardia anti-escalada verificada. La nueva versión sí tiene protocolo de cierre, pero un timeout sigue bloqueando rollback. Si ya se retiró la guardia, volver a aprobar/aplicar T1 antes de cualquier stop. Parada como T2 y evidencia como T3; exigir ahora cleanup local exitoso, no aceptar otro fallo de shutdown como normal.

Después de confirmar cgroup vacío, conservar un respaldo adicional de datos actuales para evidencia **sin sobrescribirlos** y volver solo código/index:

```bash
git switch --detach "$KCA_OLD"
install -m 0644 "$KCA_BACKUP/dist/index.html" frontend/dist/.index.html.rollback
mv -T frontend/dist/.index.html.rollback frontend/dist/index.html
sudo systemctl start "$KCA_UNIT"
```

Sin force; conflicto local implica ABORTAR. Detached evita retroceder main. Assets viejos se conservaron; dependencias no cambiaron. No restaurar `data/`, current_run, tokens, mapas ni journals desde el respaldo. Si la versión nueva cambió datos, primero analizar compatibilidad; no arrancar código antiguo sobre un esquema desconocido. Mantener auto_connect=false y no relanzar operaciones. Repetir postflight contra el SHA anterior y el baseline vigente de datos, no borrar nuevas evidencias para hacerlo coincidir.

## 9. Resultado y autorización pendiente

```text
DEPLOY_RUNBOOK_READY_FOR_EXTERNAL_AUDIT
REMOTE_MAIN = cece9559e8d212ce489f7cb4ec07bf8b998137d5
PRODUCTIVE_CHECKOUT = b0dbcdc691a145b4b086dcd9cc2dcf1795e10880
SIGNAL_SCENARIOS = 5/5 PASS
RUNBOOK_BASH_SYNTAX = 15/15 PASS
BACKUP_HELPERS = PASS (positivo y negativo sintéticos)
DIFF_CHECK = PASS
SYSTEMD_ESCALATION_TEST = NOT_RUN (configuración/manual inspeccionados)
BACKUP = 810/810 PASS
PRODUCTIVE_DATA = 535/535 PASS
REAL_MACHINE_STATE = NOT_QUERIED
PRODUCTION_MODIFIED = NO
MAIN_MODIFIED = NO
PRODUCTION_SIGNALS = NO
SERVICES_STOPPED_OR_RESTARTED = NO
PHYSICAL_COMMANDS = NO
PHYSICAL_CONNECTIONS = NO
DEPENDENCIES_INSTALLED = NO
DEPLOY_EXECUTED = NO
DEPLOY_AUTHORIZED = NO
CURRENT_DEPLOY_DECISION = NO-GO hasta aprobar gates, aislamiento y manejo de escalada
```

La preparación del runbook está terminada; no equivale a GO de mantenimiento ni GO operativo. La aprobación debe decidir expresamente la guardia temporal de systemd, el tratamiento de la salida antigua con cleanup fallido y la evidencia física aceptable cuando la CNC está apagada. Ninguno de esos hechos se inventa a partir de tests simulados.
