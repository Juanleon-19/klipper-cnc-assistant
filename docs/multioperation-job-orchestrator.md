# Orquestador multioperación CNC

## Objetivo

Coordinar la ejecución completa de un proyecto CNC de PCB con varias operaciones, varias herramientas, un único mapa físico por montaje/cara/revisión de colocación y una referencia Z por instalación de herramienta.

## Componentes

- `JobService`: construye el plan, genera compensaciones, persiste el `JobRun`, arranca el worker y conserva historial.
- `MoonrakerJobAdapter`: encapsula subida de archivos, inicio/pausa/reanudación/cancelación y operaciones físicas seguras del runtime.
- `PhysicalMapService`: expone el mapa activo del montaje, invalida y registra referencias Z por herramienta.
- `CompensatedGCodeService`: genera archivos compensados por operación y conserva el original.
- `ProjectWorkspace`: muestra el plan en `Compensación` y la línea de tiempo en `Ejecución`.

## Modelo lógico

### Mapa

El mapa pertenece al montaje:

- `project_id`
- `setup_id`
- `face`
- `placement_revision`
- `probe_region`
- `grid recipe`

Se reutiliza entre operaciones mientras siga siendo compatible.

### Referencia Z

La referencia Z pertenece a la herramienta instalada:

- `tool_id`
- `installation_revision`
- `reference machine XY`
- `captured Z`

Cambiar herramienta invalida la referencia Z anterior para la siguiente ejecución, pero no invalida el mapa del montaje.

## Estados principales del trabajo

- `JOB_DRAFT`
- `JOB_VALIDATING`
- `JOB_READY`
- `JOB_STARTING`
- `OPERATION_PREFLIGHT`
- `OPERATION_UPLOADING`
- `OPERATION_READY`
- `OPERATION_RUNNING`
- `OPERATION_PAUSED`
- `OPERATION_COMPLETE`
- `TOOL_CHANGE_REQUIRED`
- `MOVING_TO_TOOL_CHANGE_SAFE_Z`
- `MOVING_TO_TOOL_CHANGE_XY`
- `WAITING_TOOL_CHANGE`
- `TOOL_CHANGE_CONFIRMED`
- `RETURNING_TO_REFERENCE_SAFE_Z`
- `RETURNING_TO_REFERENCE_XY`
- `PROBING_TOOL_REFERENCE`
- `TOOL_REFERENCE_READY`
- `COMPENSATING_NEXT_OPERATIONS`
- `NEXT_OPERATION_READY`
- `JOB_PAUSED`
- `JOB_CANCELLED`
- `JOB_ERROR`
- `JOB_COMPLETE`

## Flujo de ejecución

1. Crear o leer `JobPlan` para `project/setup/face`.
2. Agrupar operaciones consecutivas por `tool_id`.
3. Generar compensación por operación y escribir `job_manifest.json`.
4. Ejecutar preflight general.
5. Iniciar el worker backend.
6. Subir el archivo compensado de la operación actual a Moonraker.
7. Iniciar la operación y vigilar su terminación.
8. Si la siguiente operación usa la misma herramienta, continuar sin cambio.
9. Si cambia la herramienta:
   - mover a Z/X/Y de cambio;
   - esperar confirmación del operador;
   - volver al punto físico de referencia;
   - sondear un único punto Z;
   - guardar nueva referencia Z;
   - continuar con la siguiente operación.
10. Persistir historial y cerrar el `JobRun` en `JOB_COMPLETE`, `JOB_CANCELLED` o `JOB_ERROR`.

## Persistencia

Cada montaje/cara guarda:

- `job_plan.json`
- `job_manifest.json`
- `current_run.json`
- `history/<run_id>.json`

Los archivos compensados se siguen guardando por operación dentro de `generated/`.

## API añadida

- `GET /api/projects/{project_id}/job-plan`
- `POST /api/projects/{project_id}/job-plan`
- `POST /api/projects/{project_id}/job-plan/generate`
- `GET /api/projects/{project_id}/job-run`
- `POST /api/projects/{project_id}/job-run/prepare`
- `POST /api/projects/{project_id}/job-run/start`
- `POST /api/projects/{project_id}/job-run/action`
- `GET /api/projects/{project_id}/job-history`

## Restricciones de seguridad

- una sola operación física por vez;
- un solo `JobRun` activo por montaje/cara;
- no se repite la malla por cambio normal de herramienta;
- no se ejecuta la siguiente operación sin referencia Z válida de la herramienta actual;
- el backend continúa aunque el navegador se cierre;
- la reanudación automática tras reinicio de la app no mueve la máquina por sí sola.
