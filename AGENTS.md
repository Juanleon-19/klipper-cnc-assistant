# AGENTS.md

## Regla de entrada obligatoria

Antes de modificar el repositorio, leer en este orden:

1. `AGENTS.md`
2. `docs/CURRENT_STATE.md`, si existe
3. `README.md`
4. `PLAN.md`, si existe
5. `docs/architecture.md`

Si `docs/CURRENT_STATE.md` contradice GitHub `main`, el proceso debe detenerse y reconciliar primero esa diferencia. Ninguna rama histórica, worktree, backup o copia local debe asumirse como más nueva sin comparar su SHA con `origin/main`.

## Politica de ramas

- Nunca trabajar directamente en `main`.
- Cada fase va en una rama exclusiva y con nombre explicito.
- No cambiar de commit para crear una fase si eso pone en riesgo cambios locales del usuario.
- No hacer merge a `main` sin aprobacion explicita del usuario.
- No usar `git reset --hard`, `git clean`, `git stash`, `git rebase`, `git push --force` ni otras acciones destructivas no solicitadas.

## Seguridad fisica permanente

- No enviar G-code, homing, jog, probe, spindle, mapas fisicos ni ejecucion de trabajos sin autorizacion explicita del usuario.
- No reiniciar, detener ni recargar servicios durante fases de auditoria o reorganizacion.
- No modificar configuracion real de Klipper, Moonraker, Arduino, firmware operativo, puertos ni systemd salvo que la fase y el usuario lo autoricen de forma explicita.
- Un entorno con `MACHINE_MODE=physical` debe tratarse como host fisico activo aunque no se haya movido la maquina en esta sesion.

## Fronteras de arquitectura

- `api/` expone HTTP y validacion de payloads; no debe ocultar logica de seguridad fisica.
- `application/` coordina casos de uso de producto y no debe duplicar conexiones fisicas.
- `execution/` concentra servicios de ejecucion y no debe dispersarse entre UI, rutas y runtime.
- `machine/`, `moonraker/`, `input/` y `jog/` forman la frontera fisica.
- `jog/controller.py` es la unica capa autorizada para generar movimiento manual deliberado.
- `storage/` persiste; no debe imponer logica de UI ni de hardware.
- `frontend/src/features/` agrupa comportamiento funcional; evitar componentes monoliticos en `App.tsx` o workspaces gigantes.

## Prohibiciones de duplicacion

- No duplicar conexiones Moonraker HTTP o WebSocket.
- No duplicar estado de maquina fuera de `MachineState` y los adaptadores aprobados.
- No duplicar logica de seguridad entre frontend, rutas API y runtime.
- No introducir una segunda fuente de verdad para referencias, mapas, `JobRun` o persistencia de proyectos.

## Manejo de informacion sensible

No publicar ni mover a Git:

- credenciales, tokens, claves o `.env` reales;
- IP privadas, configuraciones locales, rutas de despliegue privadas o identificadores fisicos;
- referencias reales, calibraciones, mapas fisicos, G-code de clientes o datos de produccion;
- logs privados o snapshots operativos que contengan datos sensibles.

Si un archivo sensible ya esta versionado:

1. no reescribir historia en esta fase;
2. no hacer cambios destructivos sin aprobacion del usuario;
3. documentar el riesgo con precision;
4. continuar solo con acciones locales seguras.

## Pruebas y validacion

- Ejecutar pruebas antes de commit y despues de una reorganizacion relevante.
- En hosts fisicos activos, preferir validacion segura en modo simulado o subconjuntos que no inicialicen hardware.
- No presentar una prueba bloqueada por seguridad como si hubiera sido ejecutada.
- Documentar comando, resultado, fallos preexistentes y alcance exacto de la validacion.

## Coordinacion entre agentes

- GitHub `main` es la referencia de codigo integrado; `docs/CURRENT_STATE.md` es la referencia narrativa del estado operativo.
- Codex debe comprobar estado local del host antes de diagnosticar y no debe tratar ramas o copias locales como autoridad sin compararlas con `origin/main`.
- ChatGPT coordina alcance, arquitectura, revision de PR y CI; Codex puede inspeccionar y modificar el workspace local en una rama asignada.
- El usuario mantiene la autoridad exclusiva sobre merge, deploy, reinicios y acciones fisicas.
- No trabajar dos agentes sobre la misma rama o los mismos archivos al mismo tiempo sin un handoff explícito.

## Commits

- Hacer commits pequenos, coherentes, revisables y reversibles.
- No mezclar auditoria, reorganizacion, firmware y reparaciones funcionales en un mismo commit.
- No sobrescribir cambios locales del usuario.
- Usar `git mv` cuando un archivo rastreado cambia de ruta.

## Criterio de fase

Una fase se considera lista solo si deja:

- diff entendible;
- documentacion alineada con el codigo real;
- pruebas ejecutadas o bloqueo explicado;
- riesgos abiertos enumerados;
- rama publicada sin merge a `main`.
