import type { ApiError } from "../../lib/api";
import { formatDate, formatFileSize } from "../../lib/format";
import type { JobRunConflictDetail, JobRunEvent, JobRunOperation, LiveExecutionSnapshot } from "../../types";
import { StatusBadge } from "../../components/StatusBadge";

type ExecutionConsoleProps = {
  snapshot: LiveExecutionSnapshot | null;
  error: ApiError | null;
  busy: boolean;
  onPrepare: () => void | Promise<void>;
  onStart: () => void | Promise<void>;
  onAction: (action: string) => void | Promise<void>;
  onArchiveStale: () => void | Promise<void>;
};

const ACTION_LABELS: Record<string, string> = {
  start: "Iniciar trabajo",
  pause: "Pausar",
  resume: "Reanudar",
  cancel: "Cancelar",
  "confirm-spindle-stopped": "Spindle detenido",
  "confirm-tool-change": "Herramienta cambiada",
  "retry-tool-change-transition": "Reintentar transición de herramienta",
  "measure-reference": "Medir referencia",
  continue: "Continuar trabajo",
};

const CHECK_LABELS: Record<string, string> = {
  modo_fisico: "Modo físico",
  runtime_conectado: "Moonraker HTTP",
  websocket: "WebSocket",
  klipper_ready: "Klipper listo",
  homing: "Homing XYZ",
  mapa_activo: "Mapa físico",
  plan_generado: "Plan generado",
  operaciones_bloqueadas: "Operaciones compensables",
  archivos_compensados: "Archivos compensados",
  compensacion_jit: "Compensación Legacy JIT",
  referencia_inicial: "Referencia inicial",
};

const STARTABLE_STATES = new Set(["JOB_READY"]);
const ARCHIVE_CANDIDATE_STATES = new Set(["JOB_VALIDATING", "JOB_STARTING", "OPERATION_PREFLIGHT", "OPERATION_UPLOADING", "WAITING_FOR_KLIPPER", "PRINT_QUEUED", "OPERATION_RUNNING", "RECOVERY_REQUIRED", "SPINDLE_STOP_REQUIRED", "TOOL_CHANGE_REQUIRED", "READY_TO_RESUME", "OPERATION_PAUSED", "JOB_PAUSED", "NEXT_OPERATION_READY"]);
const TOOL_WAIT_STATES = new Set(["SPINDLE_STOP_REQUIRED", "TOOL_CHANGE_REQUIRED", "TOOL_CHANGE_CONFIRMED", "MOVING_TO_REFERENCE", "CALIBRATING_TOOL", "REGENERATING_COMPENSATION", "VALIDATING_REGENERATED_PLAN", "READY_TO_RESUME"]);
const ACTIVE_EXECUTION_STATES = new Set(["JOB_STARTING", "OPERATION_PREFLIGHT", "OPERATION_UPLOADING", "WAITING_FOR_KLIPPER", "PRINT_QUEUED", "OPERATION_RUNNING", "OPERATION_PAUSED", "JOB_PAUSED"]);
const AUTOMATIC_FLOW_STEPS = [
  { state: "READY_TO_RESUME", label: "Referencia Z medida" },
  { state: "OPERATION_PREFLIGHT", label: "Generar Legacy JIT" },
  { state: "OPERATION_UPLOADING", label: "Subir a Moonraker" },
  { state: "WAITING_FOR_KLIPPER", label: "Esperar confirmación de Klipper" },
  { state: "PRINT_QUEUED", label: "Archivo en cola de Moonraker" },
  { state: "OPERATION_RUNNING", label: "Ejecutar operación" },
] as const;
const AUTOMATIC_FLOW_DESCRIPTIONS: Record<string, string> = {
  READY_TO_RESUME: "Nueva referencia lista; esperando confirmación para continuar",
  OPERATION_PREFLIGHT: "Generando compensación Legacy JIT",
  OPERATION_UPLOADING: "Subiendo archivo compensado a Moonraker",
  WAITING_FOR_KLIPPER: "Esperando confirmación de Klipper",
  PRINT_QUEUED: "Archivo en cola de Moonraker",
  OPERATION_RUNNING: "Ejecutando operación",
};

function clamp(value: number | null | undefined): number {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return 0;
  }
  return Math.min(1, Math.max(0, value));
}

function percent(value: number | null | undefined): string {
  return `${(clamp(value) * 100).toFixed(1)} %`;
}

function formatDurationSeconds(value: number | null | undefined): string {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return "-";
  }
  const total = Math.max(0, Math.round(value));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  if (hours > 0) {
    return `${hours} h ${minutes.toString().padStart(2, "0")} min`;
  }
  if (minutes > 0) {
    return `${minutes} min ${seconds.toString().padStart(2, "0")} s`;
  }
  return `${seconds} s`;
}

function estimateMethodLabel(value: string | null | undefined): string {
  if (value === "moonraker_analysis") return "Moonraker analysis";
  if (value === "calibrated") return "calibrado";
  if (value === "internal") return "interno";
  return value || "-";
}

function preflightCheckLabel(name: string | null | undefined): string {
  const key = String(name ?? "");
  return CHECK_LABELS[key] ?? key;
}

function isConflictDetail(value: unknown): value is JobRunConflictDetail {
  return Boolean(value && typeof value === "object" && "code" in (value as Record<string, unknown>) && "existing_run" in (value as Record<string, unknown>));
}

function canArchiveStale(snapshot: LiveExecutionSnapshot | null, detail: JobRunConflictDetail | null): boolean {
  if (detail?.can_archive_stale) {
    return true;
  }
  if (!snapshot) {
    return false;
  }
  return snapshot.run.stale_candidate === true
    && !snapshot.moonraker.is_active
    && String(snapshot.moonraker.print_state ?? "").toLowerCase() !== "printing"
    && ARCHIVE_CANDIDATE_STATES.has(snapshot.run.status)
    && snapshot.run.worker_alive === false
    && snapshot.run.watcher_alive === false
    && snapshot.run.supervisor_registered === false;
}

function executionTone(value: string | null | undefined): "success" | "warning" | "danger" | "info" | "neutral" {
  const state = String(value ?? "").toUpperCase();
  if (["JOB_COMPLETE", "COMPLETED", "READY_TO_RESUME"].includes(state)) return "success";
  if (["SPINDLE_STOP_REQUIRED", "SPINDLE_STOP_CONFIRMED", "TOOL_CHANGE_REQUIRED", "TOOL_CHANGE_CONFIRMED", "RETRACTING", "MOVING_TO_REFERENCE", "CALIBRATING_TOOL", "REGENERATING_COMPENSATION", "VALIDATING_REGENERATED_PLAN", "JOB_VALIDATING"].includes(state)) return "warning";
  if (["JOB_ERROR", "FAILED", "ERROR", "CANCELLED", "JOB_CANCELLED"].includes(state)) return "danger";
  if (["OPERATION_RUNNING", "RUNNING", "WAITING_FOR_KLIPPER", "OPERATION_STARTING", "OPERATION_UPLOADING", "JOB_READY"].includes(state)) return "info";
  return "neutral";
}

function dedupeEvents(events: JobRunEvent[]): JobRunEvent[] {
  const seen = new Set<string>();
  const result: JobRunEvent[] = [];
  for (const event of events) {
    const key = event.event_id ?? `${event.timestamp}:${event.stage ?? event.level}:${event.message}`;
    if (seen.has(key)) continue;
    seen.add(key);
    result.push(event);
  }
  return result;
}

function lineState(operation: JobRunOperation, index: number, snapshot: LiveExecutionSnapshot): string {
  const runStatus = snapshot.run.status;
  if (operation.execution_status === "COMPLETED") {
    return `COMPLETED ${percent(1)}`;
  }
  if (snapshot.operation.operation_id === operation.operation_id) {
    return `${snapshot.operation.execution_status} ${percent(snapshot.operation.progress)}`.trim();
  }
  if (TOOL_WAIT_STATES.has(runStatus) && index > snapshot.run.current_operation_index) {
    return "WAITING TOOL";
  }
  return operation.execution_status || "PENDING";
}

function currentOperationLabel(snapshot: LiveExecutionSnapshot | null): string {
  if (!snapshot || snapshot.run.total_operations <= 0) {
    return "Operación 0 de 0";
  }
  const index = Math.min(snapshot.run.current_operation_index + 1, snapshot.run.total_operations);
  return `Operación ${index} de ${snapshot.run.total_operations}`;
}

export function ExecutionConsole({ snapshot, error, busy, onPrepare, onStart, onAction, onArchiveStale }: ExecutionConsoleProps) {
  const jobRun = snapshot?.job_run ?? null;
  const actions = (snapshot?.run.available_actions ?? jobRun?.available_actions ?? []).filter((action, index, list) => list.indexOf(action) === index);
  const events = dedupeEvents(snapshot?.events ?? []);
  const conflictDetail = isConflictDetail(error?.detail) ? error.detail : null;
  const showArchiveStale = canArchiveStale(snapshot, conflictDetail);
  const showStart = STARTABLE_STATES.has(snapshot?.run.status ?? "") || actions.includes("start");
  const extraActions = actions.filter((action) => action !== "start");
  const operationPercent = percent(snapshot?.operation.progress ?? 0);
  const overallPercent = percent(snapshot?.run.overall_progress ?? 0);
  const filename = snapshot?.moonraker.filename || snapshot?.operation.expected_remote_file || "pendiente";
  const desync = snapshot?.synchronization.ok === false;
  const runStatus = snapshot?.run.status ?? snapshot?.operation.execution_status ?? "JOB_DRAFT";
  const showSpindleStopRequired = runStatus === "SPINDLE_STOP_REQUIRED";
  const showToolChangeRequired = runStatus === "TOOL_CHANGE_REQUIRED";
  const showRecoveryRequired = runStatus === "RECOVERY_REQUIRED";
  const showReadyToResume = runStatus === "READY_TO_RESUME";
  const eta = snapshot?.eta;
  const etaIsCalculating = ACTIVE_EXECUTION_STATES.has(runStatus) && eta?.available === false;
  const transitionProfile = snapshot?.transition.tool_change_profile ?? snapshot?.transition.tool_reference_profile ?? "standard";
  const transitionProfileLabel = transitionProfile === "long_tool" ? "Herramienta larga" : "Estándar";
  const transitionTool = snapshot?.transition.tool ?? snapshot?.transition.required_tool ?? snapshot?.operation.tool ?? "pendiente";
  const automaticFlowDescription = AUTOMATIC_FLOW_DESCRIPTIONS[runStatus] ?? null;
  const automaticFlowStepIndex = AUTOMATIC_FLOW_STEPS.findIndex((step) => step.state === runStatus);
  const checks = jobRun?.checks ?? [];
  const failedChecks = checks.filter((check) => !check.ok);
  const showPreflightSummary = runStatus === "JOB_READY" || runStatus === "JOB_VALIDATING" || checks.length > 0;
  const readinessTitle = runStatus === "JOB_READY" ? "Trabajo listo para iniciar" : "Trabajo no listo";
  const readinessDetail = runStatus === "JOB_READY"
    ? "Todos los checks requeridos están aprobados."
    : failedChecks.length > 0
      ? "Revise y resuelva los checks fallidos antes de iniciar."
      : "El trabajo sigue en validación. Revise el estado actual antes de iniciar.";

  return (
    <article className="panel execution-console-v2" aria-label="Consola de ejecución en vivo v2">
      <div className="section-heading section-heading--stacked execution-console-v2__heading">
        <div>
          <p className="eyebrow">2. CONSOLA DE EJECUCIÓN EN VIVO — V2</p>
          <h3>{currentOperationLabel(snapshot)}</h3>
          <p className="execution-console-v2__subtitle">{snapshot?.operation.name ?? "Sin operación activa"} · Herramienta {snapshot?.operation.tool ?? "pendiente"}</p>
        </div>
        <StatusBadge tone={executionTone(runStatus)}>{snapshot?.run.status ?? "JOB_DRAFT"}</StatusBadge>
      </div>

      {desync ? <div className="alert alert--danger"><strong>DESINCRONIZACIÓN:</strong> Moonraker está ejecutando el archivo, pero JobRun no lo está siguiendo.</div> : null}
      {showSpindleStopRequired ? (
        <div className="alert alert--warning">
          <strong>Apague manualmente el spindle</strong>
          <p>Apague manualmente el spindle antes de continuar.</p>
        </div>
      ) : null}
      {showToolChangeRequired ? (
        <div className="alert alert--warning">
          <strong>Cambie la herramienta</strong>
          <p>Instale la herramienta requerida y pulse Herramienta cambiada.</p>
          <p>Después de confirmar el cambio, la referencia anterior dejará de ser válida y se medirá la nueva herramienta antes de generar su compensación.</p>
        </div>
      ) : null}
      {showRecoveryRequired ? (
        <div className="alert alert--danger">
          <strong>Transición de herramienta detenida</strong>
          <p>Causa: {snapshot?.transition.last_error?.message ?? "La transición segura no pudo completarse."}</p>
        </div>
      ) : null}

      {showReadyToResume ? (
        <section className="alert alert--success execution-console-v2__resume-card" aria-label="Nueva referencia Z lista">
          <h4>Nueva referencia Z lista</h4>
          <div className="info-grid info-grid--double compact-grid">
            <div className="metric-box"><span>Herramienta</span><strong>{transitionTool}</strong></div>
            <div className="metric-box"><span>Perfil de cambio</span><strong>{transitionProfileLabel}</strong></div>
            <div className="metric-box execution-console-v2__resume-next"><span>Siguiente paso</span><strong>Continuar trabajo</strong></div>
          </div>
          <p>Al continuar, el sistema generará automáticamente una nueva compensación Legacy para la siguiente operación usando la referencia Z recién medida, la subirá a Moonraker y comenzará la ejecución cuando Klipper la acepte.</p>
          <p><strong>No es necesario volver a la sección Compensación.</strong></p>
        </section>
      ) : null}

      {automaticFlowDescription ? (
        <section className="execution-panel execution-console-v2__automatic-flow" aria-label="Flujo automático de la siguiente operación">
          <div className="section-heading section-heading--stacked">
            <div>
              <h4>Flujo automático de la siguiente operación</h4>
              <p className="muted">El plan de operaciones se conserva. La compensación se genera por operación justo antes de ejecutarla.</p>
            </div>
          </div>
          <p className="execution-console-v2__automatic-status"><strong>{automaticFlowDescription}</strong></p>
          <ol className="execution-console-v2__automatic-steps">
            {AUTOMATIC_FLOW_STEPS.map((step, index) => {
              const isCurrent = index === automaticFlowStepIndex;
              const isComplete = index < automaticFlowStepIndex || (runStatus === "READY_TO_RESUME" && index === 0);
              return (
                <li
                  className={isCurrent ? "is-current" : isComplete ? "is-complete" : undefined}
                  aria-current={isCurrent ? "step" : undefined}
                  key={step.state}
                >
                  <span aria-hidden="true">{isComplete ? "✓" : isCurrent ? "→" : "·"}</span>
                  {step.label}
                </li>
              );
            })}
          </ol>
        </section>
      ) : null}

      {showPreflightSummary ? (
        <section className="execution-panel" aria-label="Preparación del trabajo">
          <div className="section-heading"><h4>Preparación del trabajo</h4></div>
          <div className={`alert ${runStatus === "JOB_READY" ? "alert--success" : "alert--warning"}`}>
            <strong>{readinessTitle}</strong>
            <p>{readinessDetail}</p>
          </div>
          {checks.length ? (
            <div className="stack gap-sm">
              {checks.map((check) => (
                <div className="subpanel subpanel--soft" key={`${check.name}:${check.ok ? "ok" : "fail"}`}>
                  <strong>{check.ok ? "OK" : "FALLA"} · {preflightCheckLabel(check.name)}</strong>
                  <p>{check.detail}</p>
                  <p className="muted mono-text">{check.name}</p>
                </div>
              ))}
            </div>
          ) : (
            <p className="muted">Todavía no hay checks persistidos para esta ejecución.</p>
          )}
          {failedChecks.length ? (
            <div className="alert alert--warning">
              <strong>Bloqueos que debes resolver</strong>
              <ul>
                {failedChecks.map((check) => (
                  <li key={`blocker:${check.name}`}>{check.detail}</li>
                ))}
              </ul>
            </div>
          ) : null}
        </section>
      ) : null}

      {error ? (
        <section className="execution-panel">
          <div className="section-heading"><h4>Errores de ejecución</h4></div>
          <div className="alert alert--danger">
            <strong>{conflictDetail?.code ?? `HTTP ${error.status}`}</strong>
            <p>{conflictDetail?.message ?? error.message}</p>
            {conflictDetail ? (
              <>
                <div className="info-grid info-grid--double compact-grid">
                  <div className="metric-box"><span>Run ID</span><strong className="mono-text execution-console-v2__file">{conflictDetail.existing_run.run_id ?? "-"}</strong></div>
                  <div className="metric-box"><span>Estado</span><strong>{conflictDetail.existing_run.status}</strong></div>
                  <div className="metric-box"><span>Operación</span><strong>{conflictDetail.existing_run.current_operation.name ?? "-"}</strong></div>
                  <div className="metric-box"><span>Archivo</span><strong className="mono-text execution-console-v2__file">{conflictDetail.existing_run.remote_file ?? conflictDetail.moonraker.filename ?? "-"}</strong></div>
                  <div className="metric-box"><span>Última actualización</span><strong>{conflictDetail.existing_run.updated_at ? formatDate(conflictDetail.existing_run.updated_at) : "-"}</strong></div>
                  <div className="metric-box"><span>Worker</span><strong>{conflictDetail.existing_run.worker_alive ? "activo" : "inactivo"}</strong></div>
                  <div className="metric-box"><span>Moonraker</span><strong>{conflictDetail.moonraker.print_state ?? conflictDetail.moonraker.webhooks_state ?? "standby"}</strong></div>
                  <div className="metric-box"><span>Acción disponible</span><strong>{conflictDetail.available_actions.join(", ") || "abrir"}</strong></div>
                </div>
                <p>{conflictDetail.conflict_condition}</p>
              </>
            ) : null}
          </div>
        </section>
      ) : null}

      <div className="execution-console-v2__metrics">
        <div className="metric-box"><span>Estado</span><strong>{snapshot?.operation.execution_status ?? snapshot?.run.status ?? "JOB_DRAFT"}</strong></div>
        <div className="metric-box"><span>Archivo real</span><strong className="mono-text execution-console-v2__file">{filename}</strong></div>
        <div className="metric-box"><span>Próxima acción</span><strong>{snapshot?.run.next_action ?? "Prepare el trabajo"}</strong></div>
        <div className="metric-box"><span>Operaciones</span><strong>{`${snapshot?.run.completed_operations ?? 0} de ${snapshot?.run.total_operations ?? 0} operaciones terminadas`}</strong></div>
        <div className="metric-box"><span>Tiempo transcurrido</span><strong>{formatDurationSeconds(eta?.available ? eta.elapsed_s : snapshot?.moonraker.print_duration ?? 0)}</strong></div>
        <div className="metric-box"><span>Método ETA</span><strong>{estimateMethodLabel(eta?.method)}</strong></div>
        <div className="metric-box"><span>Confianza</span><strong>{eta?.available ? (eta.confidence ?? "-") : "-"}</strong></div>
      </div>
      {eta?.available && eta.detail ? <p className="muted">{eta.detail}</p> : null}

      <div className="execution-console-v2__progress-grid">
        <section className="execution-progress-card">
          <div className="execution-progress-card__header">
            <strong>Progreso de la operación</strong>
            <span>{operationPercent}</span>
          </div>
          <progress max="1" value={clamp(snapshot?.operation.progress ?? 0)} aria-label="Progreso de operación" />
        </section>
        <section className="execution-progress-card">
          <div className="execution-progress-card__header">
            <strong>Progreso total del proyecto</strong>
            <span>{overallPercent}</span>
          </div>
          <progress max="1" value={clamp(snapshot?.run.overall_progress ?? 0)} aria-label="Progreso total" />
        </section>
      </div>
      <section className="execution-console-v2__eta" aria-label="Estimación de tiempo">
        <div><span>Tiempo restante:</span><strong>{eta?.available ? formatDurationSeconds(eta.remaining_s) : etaIsCalculating ? "calculando..." : "no disponible"}</strong></div>
        <div><span>Fin estimado:</span><strong>{eta?.available && eta.completion_at ? formatDate(eta.completion_at) : etaIsCalculating ? "calculando..." : "-"}</strong></div>
      </section>

      <div className="action-grid execution-console-v2__actions">
        <button className="button button--ghost" type="button" disabled={busy} onClick={() => void onPrepare()}>Preparar trabajo</button>
        {showStart ? <button className="button" type="button" disabled={busy || snapshot?.run.status === "OPERATION_RUNNING"} onClick={() => void onStart()}>Iniciar trabajo</button> : null}
        {extraActions.map((action) => (
          <button
            key={action}
            className={`button${action === "cancel" ? " button--ghost button--danger" : " button--ghost"}`}
            type="button"
            disabled={busy}
            onClick={() => void onAction(action)}
          >
            {ACTION_LABELS[action] ?? action}
          </button>
        ))}
        {showArchiveStale ? (
          <button
            className="button button--ghost button--danger"
            type="button"
            disabled={busy}
            onClick={() => {
              if (window.confirm("Se archivará la ejecución obsoleta actual sin borrar datos del proyecto. ¿Desea continuar?")) {
                void onArchiveStale();
              }
            }}
          >
            Cerrar ejecución obsoleta
          </button>
        ) : null}
      </div>

      <div className="execution-console-v2__layout">
        <section className="execution-panel execution-panel--timeline">
          <div className="section-heading"><h4>Línea de operaciones</h4></div>
          <div className="execution-timeline">
            {(snapshot?.operations ?? []).map((operation, index) => (
              <div key={operation.operation_id} className="execution-timeline__item">
                <strong>{operation.order_label} — {operation.name}</strong>
                <span>{lineState(operation, index, snapshot as LiveExecutionSnapshot)}</span>
              </div>
            ))}
          </div>
        </section>

        <section className="execution-panel">
          <div className="section-heading"><h4>Moonraker real</h4></div>
          <div className="info-grid info-grid--double compact-grid">
            <div className="metric-box"><span>State</span><strong>{snapshot?.moonraker.print_state ?? "standby"}</strong></div>
            <div className="metric-box"><span>Virtual SD activa</span><strong>{snapshot?.moonraker.is_active ? "si" : "no"}</strong></div>
            <div className="metric-box"><span>Archivo</span><strong className="mono-text execution-console-v2__file">{snapshot?.moonraker.filename ?? "-"}</strong></div>
            <div className="metric-box"><span>Progreso</span><strong>{percent(snapshot?.moonraker.progress ?? 0)}</strong></div>
            <div className="metric-box"><span>Bytes</span><strong>{`${formatFileSize(snapshot?.moonraker.file_position ?? 0)} / ${formatFileSize(snapshot?.moonraker.file_size ?? 0)}`}</strong></div>
            <div className="metric-box"><span>Última lectura</span><strong>{snapshot?.moonraker.updated_at ? formatDate(snapshot.moonraker.updated_at) : "-"}</strong></div>
          </div>
        </section>

        <section className="execution-panel">
          <div className="section-heading"><h4>Orquestador JobRun</h4></div>
          <div className="info-grid info-grid--double compact-grid">
            <div className="metric-box"><span>Run ID</span><strong className="mono-text execution-console-v2__file">{snapshot?.run.run_id ?? "-"}</strong></div>
            <div className="metric-box"><span>Status</span><strong>{snapshot?.run.status ?? "JOB_DRAFT"}</strong></div>
            <div className="metric-box"><span>Expected filename</span><strong className="mono-text execution-console-v2__file">{snapshot?.operation.expected_remote_file ?? "-"}</strong></div>
            <div className="metric-box"><span>Filename match</span><strong>{snapshot?.operation.filename_match ? "si" : "no"}</strong></div>
            <div className="metric-box"><span>Observed printing</span><strong>{snapshot?.operation.observed_printing ? "si" : "no"}</strong></div>
            <div className="metric-box"><span>Supervisor</span><strong>{snapshot?.run.supervisor_registered ? "activo" : "inactivo"}</strong></div>
            <div className="metric-box"><span>Watcher</span><strong>{snapshot?.run.watcher_alive ? "activo" : "inactivo"}</strong></div>
            <div className="metric-box"><span>Recuperación</span><strong>{snapshot?.run.recovery_state ?? "normal"}</strong></div>
            <div className="metric-box"><span>Última persistencia</span><strong>{snapshot?.run.updated_at ? formatDate(snapshot.run.updated_at) : "-"}</strong></div>
            <div className="metric-box execution-console-v2__error"><span>Error de transición activo</span><strong>{snapshot?.transition.last_error?.message ?? "ninguno"}</strong></div>
          </div>
        </section>
      </div>

      <section className="execution-panel">
        <div className="section-heading"><h4>Transición</h4></div>
        <div className="info-grid info-grid--double compact-grid">
          <div className="metric-box"><span>State</span><strong>{snapshot?.transition.state ?? "-"}</strong></div>
          <div className="metric-box"><span>Herramienta requerida</span><strong>{snapshot?.transition.required_tool ?? "-"}</strong></div>
          <div className="metric-box"><span>Herramienta</span><strong>{snapshot?.transition.tool ?? "-"}</strong></div>
          <div className="metric-box"><span>Perfil de cambio</span><strong>{transitionProfileLabel}</strong></div>
          <div className="metric-box"><span>Z segura durante cambio</span><strong>{typeof snapshot?.transition.tool_change_clearance_z_mm === "number" ? `${snapshot.transition.tool_change_clearance_z_mm.toFixed(3)} mm` : "-"}</strong></div>
          <div className="metric-box"><span>Z de aproximación a referencia</span><strong>{typeof snapshot?.transition.reference_prep_z_mm === "number" ? `${snapshot.transition.reference_prep_z_mm.toFixed(3)} mm` : "-"}</strong></div>
          <div className="metric-box"><span>Confirmación del operador</span><strong>{snapshot?.transition.operator_confirmation_required ? "requerida" : "no"}</strong></div>
        </div>
        <p className="muted">La Z segura se usa solo durante el cambio de herramienta. No modifica el mapa ni la compensación Z.</p>
      </section>

      <section className="execution-panel">
        <div className="section-heading"><h4>Eventos</h4></div>
        <div className="machine-event-list execution-console-v2__events">
          {events.length ? events.slice(-10).map((event) => (
            <div className="machine-event" key={event.event_id ?? `${event.timestamp}:${event.message}`}>
              <strong>{new Date(event.timestamp).toLocaleTimeString()} · {event.stage ?? event.level}</strong>
              <span>{event.message}</span>
            </div>
          )) : <p className="muted">Sin eventos todavía.</p>}
        </div>
      </section>
    </article>
  );
}
