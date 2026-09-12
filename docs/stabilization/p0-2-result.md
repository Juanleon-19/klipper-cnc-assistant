# P0-2 — Cancelación dominante e identidad de impresión

Base: `7acbc1c94f3424f2971ca35c3346f335101a2a29`.
Rama: `safety/p0-2-jobrun-cancel-print-identity-2026-09-12`.

## Alcance

`JobRunStore` es la autoridad de lectura, escritura y retirada de
`current_run.json`. Cada escritura de dominio exige el mismo `run_id`,
`revision` y `cancellation_epoch`. Cancelar carga la versión vigente y persiste
`JOB_CANCELLED` antes de consultar Moonraker. Ninguna transición posterior puede
revivir ese run, ni siquiera usando su última revisión; preparar otro trabajo
crea otra identidad mediante reemplazo condicionado a la versión anterior.

El store usa un mutex por ruta y `flock` durante operaciones locales cortas,
con temporal único, fsync y reemplazo atómico. No ejecuta callbacks, JIT, red,
esperas, joins ni movimiento dentro de sus locks. Los JSON anteriores se leen
con revisión y epoch cero para conservar compatibilidad.

ETA y las observaciones del supervisor utilizan listas explícitas de campos
permitidos. No escriben un JobRun completo ni modifican estado, epoch o revisión
de dominio. Los supervisores quedan vinculados al run que los inició; los
conflictos CAS detienen al escritor antiguo. La API devuelve HTTP 409.

La ruta productiva realiza JIT, upload con `print_file=False`, comprueba el path
y root devueltos, y valida nuevamente run, operación, revisión, cancelación,
ownership P0-1 e inactividad remota antes de start. El adaptador revalida el
contexto físico después de la última validación del servicio.

`PrintIdentity` compara el nombre relativo completo de Moonraker. Rechaza
identidades ausentes, rutas absolutas, alias con `..`, separadores alternativos
y coincidencias por basename. Pause, resume, cancel, observación de ejecución,
finalización y recuperación requieren la identidad esperada. Un archivo distinto
o una consulta fallida no autoriza una acción remota; se conserva un estado de
recuperación seguro. La recuperación solo puede adoptar la operación actual.

## Start concurrente con cancelación

Antes de entrar al transporte se persiste un intento de start mediante CAS.
Si la cancelación ganó antes de ese punto, start queda bloqueado. Desde ese
punto el intento se considera potencialmente enviado: no se afirma que un
comando aceptado o de resultado incierto nunca salió. El resultado tardío solo
puede actualizar la evidencia del mismo run e intento, y mantiene
`JOB_CANCELLED` con `CANCEL_RECONCILIATION_REQUIRED` si llegó la cancelación.
Esta marca exige reconciliación; no declara que la máquina ya esté detenida.

## Validación automatizada

Tests focalizados, en modo simulado:

```bash
MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONPATH=src:tests python -m unittest tests.test_job_run_store tests.test_job_cancel_identity tests.test_job_service tests.test_job_plan_hotfix -v
```

Resultado: **69/69 OK**.

| Caso | Verificación |
| --- | --- |
| A | Cancelación en barrera anterior a start: cero starts; upload productivo sin inicio. |
| B | ETA termina después de cancelar: conserva `JOB_CANCELLED`. |
| C | Callback antiguo después de cancelar: escritura rechazada. |
| D | Run reemplazado: escritor antiguo no modifica el nuevo. |
| E | Paused B cuando se espera A: resume bloqueado. |
| F | Consulta de identidad fallida: cero acciones remotas. |
| G | Paused A con permiso válido: resume permitido. |
| H | Cancelación después de start enviado: evidencia conservada y reconciliación requerida. |

También se verifican CAS entre instancias del store, campos de observación,
callbacks tardíos de transporte, conflictos HTTP 409, compatibilidad de
`JOB_PAUSED`, cancelación durante preflight y recuperación de otra operación.

Suite backend completa, una sola ejecución tras pasar los focalizados:

```bash
MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONPATH=src:tests python -m unittest discover -s tests -v
git diff --check
```

Resultado: **405/405 OK**, 68,450 segundos. `git diff --check` sin errores.
No se ejecutaron pruebas de frontend porque no se modificó frontend.

## Entrega

Solo P0-2. Sin cambios en P0-3/P0-4 ni integración de PR #24. No se modificó
frontend, hardware, servicios ni datos operativos. La validación usa transportes
simulados y datos temporales. Entrega mediante commit local, sin push ni merge.
