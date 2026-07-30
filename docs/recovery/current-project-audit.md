# Auditoría actual del proyecto

Fecha de auditoría: jueves 30 de julio de 2026

## Alcance y restricciones aplicadas

- Repositorio auditado: `/home/impresora/klipper-cnc-assistant`
- Rama de inspección inicial: `deploy/viernes-recuperado`
- Rama de trabajo de Fase 1: `fase-1/auditoria-arquitectura`
- Commit base real de la fase: `05c68590eabfd9ab09875682d071b8fa10fd840b`
- No se ejecutó G-code, homing, jog, probe, spindle, mapa físico ni ejecución de trabajos.
- No se reinició, detuvo ni recargó `systemd`.
- No se modificó configuración real de Klipper, Moonraker, Arduino ni de la máquina.

## Estado Git inicial comprobado

Captura realizada antes de crear la rama de Fase 1:

- `pwd`: `/home/impresora`
- repositorio real: `/home/impresora/klipper-cnc-assistant`
- rama actual al iniciar: `deploy/viernes-recuperado`
- `HEAD`: `05c68590eabfd9ab09875682d071b8fa10fd840b`
- remoto: `origin git@github.com:Juanleon-19/klipper-cnc-assistant.git`
- relación con `origin/main`:
  - merge-base: `e9ac6e90dae64d9bf3b9e20e4a1b336f14b33626`
  - divergencia: `0` commits exclusivos de `origin/main`, `75` commits exclusivos de `HEAD`
- cambios preparados: ninguno
- cambios no preparados: ninguno
- archivos sin seguimiento: ninguno
- stashes detectados:
  - `stash@{0}: On stabilization/phase-0-audit: respaldo antes de restaurar mapa 20260725-183119`
- worktrees detectados:
  - `/home/impresora/klipper-cnc-assistant` -> `deploy/viernes-recuperado`
  - `/home/impresora/klipper-cnc-assistant-viernes` -> `integration/viernes-mas-mejoras`
- ramas locales relevantes:
  - `main` -> `e9ac6e9`
  - `deploy/viernes-recuperado` -> `05c6859`
  - `recovery/viernes-mapa-ejecucion` -> `05c6859`
  - `integration/viernes-mas-mejoras` -> `05c6859`
  - `fix/integracion-runtime-mapa-ejecucion` -> `2bcfcf2`
  - `fix/phase-43-stability-workflow` -> `c627f2d`
  - ramas `rescue/*`, `restore/*`, `stabilization/*`

## Aplicación realmente servida por `systemd`

Inspección en modo lectura de `systemd`, proceso y puertos:

- unidad real: `/etc/systemd/system/klipper-cnc-assistant.service`
- override real: `/etc/systemd/system/klipper-cnc-assistant.service.d/override.conf`
- estado: activo desde el lunes 27 de julio de 2026 a las 21:49:12 `-05`
- usuario del servicio: `impresora`
- `WorkingDirectory`: `/home/impresora/klipper-cnc-assistant`
- `ExecStart`: `.venv/bin/python -m klipper_cnc_assistant serve --host 127.0.0.1 --port 8000 --data-dir /home/impresora/klipper-cnc-assistant/data`
- `cwd` real del proceso: `/home/impresora/klipper-cnc-assistant`
- Python real del proceso: `/usr/bin/python3.12` invocado a través de `/home/impresora/klipper-cnc-assistant/.venv/bin/python`
- PID observado: `1127831`
- puerto HTTP servido: `127.0.0.1:8000`
- frontend servido: `frontend/dist` dentro del repositorio actual
- carpeta de datos: `/home/impresora/klipper-cnc-assistant/data`
- entorno virtual: `/home/impresora/klipper-cnc-assistant/.venv`
- Moonraker visibles en host:
  - instancia A escuchando en `7125`
  - instancia B escuchando en `7126`
- la aplicación visible no corre desde una copia de rescate ni desde un build separado:
  - corre directamente desde el repositorio actual `/home/impresora/klipper-cnc-assistant`

Hallazgo importante:

- la unidad base sigue describiéndose como “simulated mode”, pero el override visible fuerza modo físico con configuración local explícita;
- por seguridad, la auditoría trató el host como entorno físico activo, aunque no se autorizó ningún movimiento.

## Copias y versiones candidatas dentro de `/home/impresora`

| Ruta | Tipo | Fecha de modificación | `.git` | Rama/commit | Estado del árbol | Componentes observados | Evaluación provisional |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `/home/impresora/klipper-cnc-assistant` | repositorio principal | 2026-07-27 01:30 -05 | sí | `deploy/viernes-recuperado` -> `05c6859` al inspeccionar; luego `fase-1/auditoria-arquitectura` sobre el mismo commit | limpio | backend, frontend, `frontend/dist`, firmware, pruebas, deploy, docs, experimentos, datos locales ignorados | `KEEP` |
| `/home/impresora/klipper-cnc-assistant-viernes` | worktree Git | 2026-07-27 01:06 -05 | worktree | `integration/viernes-mas-mejoras` -> `05c6859` | limpio | backend, frontend, firmware, pruebas, deploy, docs, experimentos; sin `frontend/dist` en la inspección | `ARCHIVE` como evidencia de trabajo paralelo |
| `/home/impresora/respaldo-cnc-assistant` | respaldo de metadatos | 2026-07-18 20:04 -05 | no | rama registrada `fix/phase-43-stability-workflow`, commit `c627f2d952b2faeb494a12903fede804283d8d8d` | snapshot textual | `git-status`, patches, captura de `systemd`, journal | `ARCHIVE` como evidencia forense |
| `/home/impresora/klipper-cnc-assistant-respaldo-20260719-115820.tar.gz` | tarball recuperable | 2026-07-19 11:58 -05 | no visible sin extraer | snapshot empaquetado | no aplica | contiene `src/`, `frontend/`, `firmware/`, `tests/`, `deploy/` y `data/` | `RECOVER` solo si falta evidencia en el repo activo |
| `/home/impresora/backups/klipper-cnc-assistant/despliegue-viernes-20260727-012837` | backup de despliegue | 2026-07-27 | no | `2bcfcf285dac840277914a903f453d5a3c29045a` | no aplica | `commit-anterior.txt`, `estado-git-anterior.txt` | `ARCHIVE` |
| `/home/impresora/backups/klipper-cnc-assistant/despliegue-viernes-20260727-012949` | backup de despliegue + datos | 2026-07-27 | no | `2bcfcf285dac840277914a903f453d5a3c29045a` | no aplica | metadatos Git + `data.tar.gz` | `ARCHIVE` |
| `/home/impresora/backups/klipper-cnc-assistant/rollback-mapa-20260725-183119` | backup de datos | 2026-07-25 | no | no aplica | no aplica | proyecto, mapas, originales, reports, generados | `ARCHIVE` |
| `/home/impresora/printer_kp3s2_data/gcodes/klipper-cnc-assistant` | artefactos de ejecución | 2026-07-19..2026-07-27 | no | no aplica | no aplica | G-code compensado remoto organizado por proyecto/montaje/cara | `ARCHIVE` como salida de runtime, no como código fuente |

Notas sobre procedencia:

- el repositorio servido y el worktree viernes comparten el commit `05c6859`, pero el worktree usa otro nombre de rama;
- el tarball del 19 de julio de 2026 preserva una copia completa recuperable, mientras que `respaldo-cnc-assistant` solo preserva evidencia de estado;
- los backups del 27 de julio de 2026 preservan datos y metadatos de despliegue asociados a `2bcfcf2`, no una tercera copia viva del producto.

## Riesgos de publicación y material sensible

No se detectaron archivos clásicos de secreto dentro del árbol versionado durante esta auditoría:

- sin `.env`
- sin `.env.*`
- sin `.pem`
- sin `.key`
- sin `.p12`

Riesgos sí detectados:

- `deploy/klipper-cnc-assistant.env` está versionado y contiene configuración local operativa del host;
- la unidad versionada `deploy/systemd/klipper-cnc-assistant.service` consume ese archivo mediante `EnvironmentFile`;
- la documentación de despliegue y validación física repite valores locales equivalentes;
- `data/` contiene mapas medidos, G-code generado, historial de `JobRun` y datos de proyecto reales; por fortuna está ignorado por Git;
- `.venv/`, `frontend/node_modules/` y `frontend/dist/` son artefactos locales o regenerables y no deben entrar a Git.

Tamaños observados en el host:

- `.venv/`: `123M`
- `frontend/node_modules/`: `140M`
- `data/`: `114M`

Archivos grandes detectados fuera de `.venv/` y `node_modules/`:

- varios `height_map.json` y `generated/compensated/*.json` dentro de `data/`
- `frontend/dist/assets/plotly.min-*.js`

Conclusión de seguridad:

- no hay credenciales evidentes;
- sí hay configuración local operativa versionada que debe tratarse como riesgo de publicación y reorganizarse hacia formato de ejemplo antes del cierre de fase.

## Estructura actual comprobada

Raíces funcionales actuales:

- `src/klipper_cnc_assistant/`
- `frontend/src/`
- `firmware/arduino_pro_mini/`
- `tests/`
- `deploy/`
- `docs/`
- `experiments/`

Módulos concentrados que justifican reorganización estructural:

- `src/klipper_cnc_assistant/machine/runtime.py`: `1961` líneas
- `frontend/src/components/ProjectWorkspace.tsx`: `2019` líneas
- `src/klipper_cnc_assistant/application/job_service.py`: `1668` líneas
- `src/klipper_cnc_assistant/api/routes.py`: `835` líneas
- `frontend/src/App.tsx`: `687` líneas
- `src/klipper_cnc_assistant/application/services.py`: `639` líneas

Hallazgos estructurales confirmados:

- backend ya usa paquetes de dominio claros (`application`, `domain`, `machine`, `moonraker`, `input`, `jog`, `gcode`, `heightmap`, `storage`);
- `api/routes.py` mezcla proyectos, referencias, mapa, compensación y ejecución en un solo archivo;
- `api/machine_routes.py` separa la frontera física, pero expone duplicidades semánticas:
  - `/runtime` y `/status`
  - `/cancel` y `/safe-stop`
- `machine/runtime.py` concentra conexión Moonraker HTTP, WebSocket, serie Arduino, estado, inicialización, referencia, sonda, malla, tool-change y cancelación;
- el frontend ya tiene `features/viewer` y `features/heightmap`, pero `ProjectWorkspace.tsx` y parte de `App.tsx` mantienen demasiada orquestación de producto;
- `frontend/src/components/execution/ExecutionConsole.tsx` ya identifica un núcleo funcional claro para `execution`;
- `deploy/`, `docs/` y `firmware/` ya existen, pero la documentación operativa está repartida entre README, `docs/` y `docs/stabilization/`.

## Estructura objetivo propuesta para Fase 1

La propuesta no implica reescritura funcional; solo movimiento y separación de archivos según responsabilidad actual:

```text
src/klipper_cnc_assistant/
├── api/
│   ├── __init__.py
│   ├── app.py
│   ├── machine_schemas.py
│   ├── schemas.py
│   ├── heightmap_schemas.py
│   └── routes/
│       ├── __init__.py
│       ├── system.py
│       ├── projects.py
│       ├── references.py
│       ├── heightmaps.py
│       ├── execution.py
│       └── machine.py
├── application/
├── domain/
├── execution/
├── gcode/
├── heightmap/
├── input/
├── jog/
├── machine/
├── moonraker/
└── storage/

frontend/src/
├── components/
│   └── StatusBadge.tsx
├── features/
│   ├── execution/
│   ├── height-map/
│   ├── projects/
│   ├── system/
│   └── viewer/
├── lib/
├── main.tsx
├── App.tsx
└── types.ts
```

## Movimientos previstos antes de ejecutarlos

Archivos candidatos a mover en backend:

- `api/routes.py` -> `api/routes/*`
- `api/machine_routes.py` -> `api/routes/machine.py`
- `application/job_service.py` -> `execution/job_service.py`
- `application/mesh_execution_service.py` -> `execution/mesh_execution_service.py`

Archivos candidatos a mover en frontend:

- `components/ProjectWorkspace.tsx` -> `features/projects/ProjectWorkspace.tsx`
- `components/ProjectList.tsx` -> `features/projects/ProjectList.tsx`
- `components/ProjectForm.tsx` -> `features/projects/ProjectForm.tsx`
- `components/OperationPanel.tsx` -> `features/projects/OperationPanel.tsx`
- `components/ToolpathPreview.tsx` -> `features/viewer/ToolpathPreview.tsx`
- `components/SystemPage.tsx` y `components/SystemBanner.tsx` -> `features/system/`
- `components/execution/ExecutionConsole.tsx` -> `features/execution/ExecutionConsole.tsx`
- pruebas correspondientes junto a sus módulos

Archivos que probablemente permanezcan donde están:

- `machine/runtime.py` en Fase 1, salvo partición puramente estructural posterior si no cambia comportamiento;
- `machine/state.py`, `moonraker/*`, `input/*`, `jog/*`, `gcode/*`, `heightmap/*`, `storage/*`;
- `firmware/arduino_pro_mini/*`;
- `experiments/*` como evidencia aislada;
- `deploy/*`, pero con documentación y separación más clara entre ejemplo y configuración local.

Riesgos de reorganización:

- romper imports internos del backend al extraer `api/routes.py`;
- romper imports del frontend al mover `ProjectWorkspace.tsx` y tests asociados;
- mezclar accidentalmente despliegue real con despliegue de ejemplo;
- tocar código físico sin querer al mover archivos con lógica de runtime;
- dejar documentación afirmando capacidades no verificadas automáticamente.

Procedimiento de rollback previsto:

1. movimientos con `git mv` cuando el archivo ya esté rastreado;
2. commits pequeños por área;
3. ejecutar pruebas entre bloques de movimiento;
4. si un bloque falla, revertir únicamente el commit de estructura correspondiente;
5. no tocar `data/`, `frontend/dist/`, `.venv/`, `node_modules/` ni backups durante rollback.

## Línea base de pruebas antes de reorganizar

Fecha: jueves 30 de julio de 2026

Backend solicitado por la fase:

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

Resultado:

- ejecución completa bloqueada por seguridad del host;
- motivo: el servicio activo está configurado en modo físico y la política del entorno rechazó correr la suite completa porque incluye módulos de runtime físico;
- módulos excluidos por seguridad:
  - `tests/test_machine_runtime.py`
  - `tests/test_physical_integration.py`

Línea base backend segura ejecutada:

```bash
MACHINE_MODE=simulated PYTHONPATH=src .venv/bin/python -m unittest -v \
  tests.test_api \
  tests.test_gcode_analysis \
  tests.test_heightmap \
  tests.test_job_service \
  tests.test_moonraker_client \
  tests.test_project_service \
  tests.test_web_mvp
```

Resultado backend seguro:

- `94` pruebas aprobadas
- `0` fallos
- duración: `40.593s`
- advertencias repetidas:
  - `FastAPI/Starlette on_event is deprecated`
  - `starlette.testclient` con aviso de compatibilidad de `httpx`

Detección de `pytest`:

- no existe `pytest.ini`
- no existe `tool.pytest` en `pyproject.toml`
- sí existen cachés `.pytest_cache/`, pero no se detectó configuración activa que reemplace la línea base `unittest`

Frontend:

```bash
cd frontend
npm run lint
npm run test
npm run build
```

Resultado frontend:

- `npm run lint`: correcto
- `npm run test`: `12` archivos, `63` pruebas aprobadas, `31.10s`
- `npm run build`: correcto, `50.29s`
- advertencia no bloqueante:
  - chunk grande de Plotly (`plotly.min-*.js`) superior a `500 kB`

Firmware:

- no se compiló en Fase 1;
- no se encontró un procedimiento documentado de compilación sin revisar primero la cadena local;
- no se flasheó hardware.

## Matriz de procedencia funcional

| Componente | Ruta de origen | Rama/commit | Cambios locales | Estado observado | Evidencia | Última validación física | Decisión provisional | Destino propuesto | Riesgo |
| ---------- | -------------- | ----------- | --------------- | ---------------- | --------- | ------------------------ | -------------------- | ----------------- | ------ |
| Aplicación realmente servida | `/home/impresora/klipper-cnc-assistant` | `deploy/viernes-recuperado` / `05c6859` | ninguno al iniciar | servicio activo real en repo actual | `systemctl show`, `systemctl cat`, `/proc/1127831/cwd`, `ss -ltnp` | `UNKNOWN` | `KEEP` | repositorio principal | descripción del servicio desactualizada; host en modo físico |
| Backend FastAPI | `src/klipper_cnc_assistant/api` + `main.py` | `05c6859` | ninguno | backend funcional, SPA montada desde FastAPI | `api/app.py`, `main.py`, pruebas backend seguras | `UNKNOWN` | `KEEP` | mantener en `src/klipper_cnc_assistant/` | rutas monolíticas |
| Frontend React | `frontend/src` | `05c6859` | ninguno | frontend funcional, build correcto | `App.tsx`, `ProjectWorkspace.tsx`, `npm run test`, `npm run build` | `UNKNOWN` | `KEEP` | `frontend/src/features/*` | componentes monolíticos |
| Visor técnico | `frontend/src/features/viewer` | `05c6859` | ninguno | funcional, con pruebas | `ToolpathViewer.test.tsx`, `viewerMath.test.ts`, build | `UNKNOWN` | `KEEP` | `frontend/src/features/viewer/` | acoplamiento parcial con `ProjectWorkspace` |
| Gestión de proyectos | `application/services.py`, `domain/models.py`, `frontend/src/components/ProjectList.tsx` | `05c6859` | ninguno | funcional | `test_project_service.py`, `ProjectList.test.tsx`, rutas `/api/projects` | `UNKNOWN` | `KEEP` | `application/` + `frontend/src/features/projects/` | mezcla de UI y orquestación |
| Analizador G-code | `gcode/*` | `05c6859` | ninguno | funcional | `test_gcode_analysis.py` | `UNKNOWN` | `KEEP` | `gcode/` | bajo |
| Pestaña Referencias | `frontend/src/components/ProjectWorkspace.tsx` + rutas de referencia | `05c6859` | ninguno | existe y responde | `ProjectWorkspace.test.tsx`, `api/routes.py` | `UNKNOWN` | `REFACTOR` | `frontend/src/features/references/` + rutas separadas | mucha lógica en un solo componente |
| Persistencia de referencias | `application/reference_service.py`, `storage/json_repository.py` | `05c6859` | ninguno | persistencia presente | `ReferenceSessionService`, `test_api.py`, `test_heightmap.py` | `UNKNOWN` | `KEEP` | `application/` o `storage/` sin duplicar | depende de formato de proyecto |
| Firmware Arduino | `firmware/arduino_pro_mini` | `05c6859`; evidencia extra en respaldo `c627f2d` | respaldo histórico con cambios locales en julio | firmware presente | árbol `firmware/arduino_pro_mini`, `docs/probe-wiring.md` | `UNKNOWN` | `KEEP` | `firmware/` | no compilar ni modificar en Fase 1 |
| `SerialDriver` | `input/serial_driver.py` | `05c6859` | ninguno | presente y usado por runtime | `machine/runtime.py`, `test_physical_integration.py` | `UNKNOWN` | `KEEP` | `input/` | validación física no repetida |
| Reconexión Arduino | `machine/runtime.py`, `input/serial_driver.py` | `05c6859` | ninguno | implementada dentro del runtime | `machine/runtime.py`, snapshot de runtime | `UNKNOWN` | `REFACTOR` | `machine/` manteniendo una sola conexión | lógica concentrada en runtime |
| Moonraker HTTP | `moonraker/client.py` | `05c6859` | ninguno | presente y usado | `machine/runtime.py`, `job_service.py`, `test_moonraker_client.py` | `UNKNOWN` | `KEEP` | `moonraker/` | usado desde más de una capa |
| Moonraker WebSocket | `moonraker/telemetry.py` | `05c6859` | ninguno | presente y usado | `machine/runtime.py`, `MachineState` | `UNKNOWN` | `KEEP` | `moonraker/` | depende de runtime monolítico |
| Telemetría | `moonraker/telemetry.py` + `machine/state.py` | `05c6859` | ninguno | presente | `machine/runtime.py`, tests de integración física | `UNKNOWN` | `KEEP` | `moonraker/` + `machine/` | vigencia temporal crítica |
| `MachineState` | `machine/state.py` | `05c6859` | ninguno | estado central observado | `machine/runtime.py`, `discovery.py` | `UNKNOWN` | `KEEP` | `machine/` | acoplado al runtime grande |
| Control de jog | `jog/*`, `input/jog_input.py` | `05c6859` | ninguno | presente | `ManualJogController`, `JogController`, AGENTS, pruebas históricas | `UNKNOWN` | `KEEP` | `jog/` | no validar físicamente en Fase 1 |
| Mapa de alturas backend | `heightmap/*`, `application/heightmap_service.py`, `application/physical_map_service.py` | `05c6859` | ninguno | presente y probado lógicamente | `test_heightmap.py`, `test_api.py` | `UNKNOWN` | `KEEP` | `heightmap/` + servicios asociados | flujo medido y simulado mezclado en rutas |
| Mapa de alturas frontend | `frontend/src/features/heightmap` + `ProjectWorkspace.tsx` | `05c6859` | ninguno | presente y probado | `HeightMapViews.test.tsx`, `ProjectWorkspace.test.tsx` | `UNKNOWN` | `KEEP` | `frontend/src/features/height-map/` | dependencias directas desde `ProjectWorkspace` |
| Persistencia del mapa | `storage/json_repository.py` + `data/` | `05c6859` | ninguno | persistencia presente | rutas de mapa, `test_heightmap.py`, backups de `data` | `UNKNOWN` | `KEEP` | `storage/` | contiene datos reales en host |
| Ejecución consola 2 | `frontend/src/components/execution/ExecutionConsole.tsx` | `05c6859` | ninguno | presente y probado | `ExecutionConsole.test.tsx` | `UNKNOWN` | `KEEP` | `frontend/src/features/execution/` | ubicación actual todavía híbrida |
| Máquina de estados de ejecución | `application/job_service.py`, `machine/runtime.py` | `05c6859` | ninguno | presente | `test_job_service.py`, `api/routes.py` | `UNKNOWN` | `REFACTOR` | `execution/` | dispersión entre backend de dominio y runtime físico |
| `JobRun` | `application/job_service.py` + `data/projects/.../reports/jobs` | `05c6859` | datos reales locales ignorados | persistencia y recuperación presentes | `test_job_service.py`, `data/` ignorado | `UNKNOWN` | `KEEP` | `execution/` + `storage/` | mezcla de estado vivo y persistido |
| Cambio de herramienta | `application/job_service.py`, `machine/runtime.py` | `05c6859` | ninguno | implementado | tests de `job_service`, README, docs | `UNKNOWN` | `REFACTOR` | `execution/` con frontera física clara | sensible a seguridad y secuencias reales |
| Persistencia de proyectos | `storage/json_repository.py` | `05c6859` | ninguno | presente | `test_project_service.py`, `project.json` ignorados en `data/` | `UNKNOWN` | `KEEP` | `storage/` | bajo |
| Configuración `systemd` | `deploy/systemd/klipper-cnc-assistant.service` + unidad instalada | `05c6859` | unidad instalada diverge por override real | operativa, pero con descripción desalineada | `systemctl cat`, archivo versionado | `UNKNOWN` | `REFACTOR` | `deploy/` con ejemplo + docs claras | archivo env versionado contiene config local |
| Pruebas backend | `tests/*.py` | `05c6859` | ninguno | presentes; subset seguro aprobado | `94` pruebas seguras aprobadas; suite completa bloqueada | `UNKNOWN` | `KEEP` | `tests/` | cobertura física no ejecutada |
| Pruebas frontend | `frontend/src/**/*.test.tsx` | `05c6859` | ninguno | presentes y aprobadas | `63` pruebas aprobadas | `UNKNOWN` | `KEEP` | junto a features organizadas | bajo |
| Scripts de despliegue | `deploy/install_service.sh`, `deploy/uninstall_service.sh` | `05c6859` | ninguno | presentes | inspección directa | `UNKNOWN` | `REFACTOR` | `deploy/` | acoplados a configuración local versionada |
| Documentación | `README.md`, `AGENTS.md`, `docs/*` | `05c6859` | ninguno | abundante pero dispersa y optimista | inspección directa | `UNKNOWN` | `REFACTOR` | `README.md`, `PLAN.md`, `docs/architecture.md`, `docs/recovery/` | riesgo de afirmar más de lo verificado |
| Copias antiguas | worktree viernes, tarball, respaldos y backups | varios | sí, según copia | existen y preservan evidencia útil | tablas anteriores | `UNKNOWN` | `ARCHIVE` | fuera del producto activo, conservadas | riesgo de copiar ciegamente trabajo viejo |

## Conclusiones de auditoría

- la aplicación visible sí corresponde al repositorio actual y no a una copia oculta;
- el estado de código útil está concentrado en `05c6859`, con una historia de rescates importante por encima de `origin/main`;
- la arquitectura ya tiene capas reconocibles, pero la organización real todavía no refleja bien los límites funcionales;
- la línea base lógica es estable;
- la validación física documentada existe como procedimiento, pero no puede considerarse repetida ni revalidada durante esta fase;
- el principal riesgo de publicación no es un secreto clásico, sino configuración local operativa versionada en `deploy/`.
