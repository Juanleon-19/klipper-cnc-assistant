# P0-4 — Referencia de herramienta ligada a la sesión física

Base: `c96cb57865a3a140f8d684c37c776c957d0de949`.
Rama: `safety/p0-4-session-bound-tool-reference-2026-09-12`.

## Alcance

`PhysicalReferenceToken` registra evidencia de una medición de herramienta.
Usa `MachineRuntime.current_physical_session_id()` (inicio del runtime y
generación serial del hotfix) y la sesión del coordinator P0-1. No crea una
autoridad de sesión nueva. Una reconciliación que cambia la sesión del
coordinator también exige una nueva medición.

El token se persiste junto a la calibración canónica en `tool_references`.
Incluye proyecto, montaje, cara, herramienta, perfil físico, instalación,
placement revision, punto de referencia, revisión única de medición, fecha y
posición medida. Las referencias guardadas de montaje y la configuración física
(incluyendo perfil de probe, alineación y límites) participan en sus fingerprints.
Los endpoints y la identidad del transporte solo se incluyen en el digest.
La identidad de medición es independiente de la identidad y versión del mapa.

El flujo de medición conserva su contexto antes del movimiento y lo compara
después del probe y antes de persistir. Un cambio de sesión no puede adoptar
la sesión nueva como si hubiera sido la sesión de medición. Se comprueba el
JobRun vigente antes de publicar. Solo después de validar el token nuevo se
publica `READY_TO_RESUME`; la instalación nueva usa una identidad única.

Continue y resume desde estados de preparación, inicio de JobRun, Legacy JIT,
start remoto y resume remoto revalidan la referencia. JIT comprueba el mismo
token antes y después de generar, antes de publicar archivos. El JobRun y el
artefacto conservan la identidad de medición para rechazar reemplazos tardíos.
La recuperación de una impresión existente no adopta una referencia vieja.
Pause y cancel conservan su disponibilidad y no emiten tokens ni renuevan
referencias.

Una referencia legacy `valid=True` sin token sigue siendo legible. No se crea
evidencia desde fechas antiguas ni se reasigna un token a otra herramienta.
Las previews explícitas sin validación de herramienta se publican con
`executable=False`; no entran al plan productivo. Las operaciones de otras
herramientas reciben sus artefactos ejecutables tras la medición y JIT respectivos.

No se rediseñaron P0-1/P0-2/P0-3. No se integró PR #24 ni se modificaron frontend,
hardware, servicios o datos operativos.

## Validación

Los tests usan runtimes/transportes simulados y proyectos temporales. Las fixtures
que requieren autorización de ejecución modelan explícitamente una medición
nueva; las pruebas geométricas sin runtime generan previews no ejecutables.

| Caso | Verificación |
| --- | --- |
| A | READY_TO_RESUME con otra generación serial o sesión runtime rechaza continue. |
| B | Misma herramienta con otra instalación rechaza la medición anterior. |
| C | Cambios de montaje, cara o placement rechazan el token. |
| D | Cambios de configuración física o perfil de probe rechazan el token. |
| E | Legacy sin token legible, generación productiva bloqueada, preview no ejecutable. |
| F | Sesión cambia durante probe o publicación: no se emite evidencia para la sesión nueva. |
| G | Sesión cambia durante JIT: cero uploads/starts, sin artefacto publicado. |
| H | Medición, JIT, start y resume permitidos con sesión/contexto vigentes. |
| I | Cancelación y reconciliación no cambian el token viejo; recovery no lo adopta. |

También se comprueban cambios de perfil de herramienta/referencia guardada,
revalidación después del upload y de la consulta de identidad previa a resume,
y rechazo de un inicio con JobRun listo pero referencia caducada.

Focalizados:

```bash
MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONPATH=src:tests python -m unittest tests.test_physical_reference_token tests.test_job_service tests.test_job_plan_hotfix tests.test_job_cancel_identity tests.test_physical_integration tests.test_api tests.test_job_run_store -v
```

Resultado: **147/147 pruebas de regresión OK**. El conjunto de 166 pruebas
detectó únicamente una fixture de cambio de cara sin eje de volteo. Tras corregir
esa fixture, se ejecutó de nuevo el módulo afectado:

```bash
MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONPATH=src:tests python -m unittest tests.test_physical_reference_token -v
```

Resultado: **19/19 OK**, 2,877 segundos. Total focalizado validado: **166 pruebas**.
Las iteraciones focalizadas corrigieron la comparación de remedición con el
token anterior, fixtures con autorización legacy implícita y la clasificación
de errores de identidad frente a errores de referencia.

Suite backend completa, una sola ejecución después de pasar los focalizados:

```bash
MACHINE_MODE=simulated MACHINE_AUTO_CONNECT=false PYTHONPATH=src:tests python -m unittest discover -s tests -v
git diff --check
```

Resultado: **434/434 OK**, 75,306 segundos. `git diff --check` sin errores.
Frontend no requerido, sin cambios.
Entrega mediante commit local, sin push/merge, comandos físicos ni reinicios.
