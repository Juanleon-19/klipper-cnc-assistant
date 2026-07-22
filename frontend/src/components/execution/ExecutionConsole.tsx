import { formatDate, formatFileSize } from "../../lib/format";
import type { JobRunEvent, JobRunOperation, LiveExecutionSnapshot } from "../../types";
import { StatusBadge } from "../StatusBadge";

type ExecutionConsoleProps = {
  snapshot: LiveExecutionSnapshot | null;
  busy: boolean;
  onPrepare: () => void | Promise<void>;
  onStart: () => void | Promise<void>;
  onAction: (action: string) => void | Promise<void>;
};

const ACTION_LABELS: Record<string, string> = {
  start: "Iniciar trabajo",
  pause: "Pausar",
  resume: "Reanudar",
  cancel: "Cancelar proyecto",
  "confirm-tool-change": "Herramienta cambiada",
  "retry-tool-change-transition": "Reintentar transición de herramienta",
  "measure-reference": "Medir referencia",
  continue: "Continuar trabajo",
};

const ACTIVE_STATES = new Set(["JOB_READY", "JOB_VALIDATING", "JOB_DRAFT"]);
const TOOL_WAIT_STATES = new Set(["TOOL_CHANGE_REQUIRED", "TOOL_CHANGE_CONFIRMED", "MOVING_TO_REFERENCE", "CALIBRATING_TOOL", "REGENERATING_COMPENSATION", "VALIDATING_REGENERATED_PLAN", "READY_TO_RESUME"]);

function clamp(value: number | null | undefined): number {
  if (typeof value !== "number" || Number.isNaN(value)) {
    return 0;
  }
  return Math.min(1, Math.max(0, value));
}

function percent(value: number | null | undefined): string {
  return `${(clamp(value) * 100).toFixed(1)} %`;
}

function executionTone(value: string | null | undefined): "success" | "warning" | "danger" | "info" | "neutral" {
  const state = String(value ?? "").toUpperCase();
  if (["JOB_COMPLETE", "COMPLETED", "READY_TO_RESUME"].includes(state)) return "success";
  if (["TOOL_CHANGE_REQUIRED", "TOOL_CHANGE_CONFIRMED", "RETRACTING", "MOVING_TO_REFERENCE", "CALIBRATING_TOOL", "REGENERATING_COMPENSATION", "VALIDATING_REGENERATED_PLAN"].includes(state)) return "warning";
  if (["JOB_ERROR", "FAILED", "ERROR", "CANCELLED", "JOB_CANCELLED"].includes(state)) return "danger";
  if (["OPERATION_RUNNING", "RUNNING", "WAITING_FOR_KLIPPER", "OPERATION_STARTING", "OPERATION_UPLOADING"].includes(state)) return "info";
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

export function ExecutionConsole({ snapshot, busy, onPrepare, onStart, onAction }: ExecutionConsoleProps) {
  const jobRun = snapshot?.job_run ?? null;
  const actions = (snapshot?.run.available_actions ?? jobRun?.available_actions ?? []).filter((action, index, list) => list.indexOf(action) === index);
  const events = dedupeEvents(snapshot?.events ?? []);
  const showStart = ACTIVE_STATES.has(snapshot?.run.status ?? "") || actions.includes("start");
  const extraActions = actions.filter((action) => action !== "start");
  const operationPercent = percent(snapshot?.operation.progress ?? 0);
  const overallPercent = percent(snapshot?.run.overall_progress ?? 0);
  const filename = snapshot?.moonraker.filename || snapshot?.operation.expected_remote_file || "pendiente";
  const desync = snapshot?.synchronization.ok === false;

  return (
    <article className="panel execution-console-v2" aria-label="Consola de ejecución en vivo v2">
      <div className="section-heading section-heading--stacked execution-console-v2__heading">
        <div>
          <p className="eyebrow">2. CONSOLA DE EJECUCIÓN EN VIVO — V2</p>
          <h3>{currentOperationLabel(snapshot)}</h3>
          <p className="execution-console-v2__subtitle">{snapshot?.operation.name ?? "Sin operación activa"} · Herramienta {snapshot?.operation.tool ?? "pendiente"}</p>
        </div>
        <StatusBadge tone={executionTone(snapshot?.run.status ?? snapshot?.operation.execution_status)}>{snapshot?.run.status ?? "JOB_DRAFT"}</StatusBadge>
      </div>

      {desync ? <div className="alert alert--danger"><strong>DESINCRONIZACIÓN:</strong> Moonraker está ejecutando el archivo, pero JobRun no lo está siguiendo.</div> : null}

      <div className="execution-console-v2__metrics">
        <div className="metric-box"><span>Estado</span><strong>{snapshot?.operation.execution_status ?? snapshot?.run.status ?? "JOB_DRAFT"}</strong></div>
        <div className="metric-box"><span>Archivo real</span><strong className="mono-text execution-console-v2__file">{filename}</strong></div>
        <div className="metric-box"><span>Próxima acción</span><strong>{snapshot?.run.next_action ?? "Prepare el trabajo"}</strong></div>
        <div className="metric-box"><span>Operaciones</span><strong>{`${snapshot?.run.completed_operations ?? 0} de ${snapshot?.run.total_operations ?? 0} operaciones terminadas`}</strong></div>
      </div>

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
            <div className="metric-box"><span>Supervisor</span><strong>{snapshot?.run.worker_alive ? "activo" : "inactivo"}</strong></div>
            <div className="metric-box"><span>Watcher</span><strong>{snapshot?.run.watcher_alive ? "activo" : "inactivo"}</strong></div>
            <div className="metric-box"><span>Recuperación</span><strong>{snapshot?.run.recovery_state ?? "normal"}</strong></div>
            <div className="metric-box"><span>Última persistencia</span><strong>{snapshot?.run.updated_at ? formatDate(snapshot.run.updated_at) : "-"}</strong></div>
            <div className="metric-box execution-console-v2__error"><span>Último error</span><strong>{snapshot?.run.last_watcher_error ?? "ninguno"}</strong></div>
          </div>
        </section>
      </div>

      <section className="execution-panel">
        <div className="section-heading"><h4>Transición</h4></div>
        <div className="info-grid info-grid--double compact-grid">
          <div className="metric-box"><span>State</span><strong>{snapshot?.transition.state ?? "-"}</strong></div>
          <div className="metric-box"><span>Herramienta requerida</span><strong>{snapshot?.transition.required_tool ?? "-"}</strong></div>
          <div className="metric-box"><span>Confirmación del operador</span><strong>{snapshot?.transition.operator_confirmation_required ? "requerida" : "no"}</strong></div>
        </div>
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
