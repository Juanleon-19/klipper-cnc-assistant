import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "../../lib/api";
import type { JobRunConflictDetail, LiveExecutionSnapshot } from "../../types";
import { ExecutionConsole } from "./ExecutionConsole";

const baseSnapshot: LiveExecutionSnapshot = {
  moonraker: {
    connected: true,
    klipper_state: "ready",
    print_state: "printing",
    filename: "klipper-cnc-assistant/proj_1/setup-main/superior/op_1_compensated.gcode",
    progress: 0.553,
    is_active: true,
    file_position: 49386,
    file_size: 119710,
    print_duration: 12.4,
    message: "",
    updated_at: new Date().toISOString(),
  },
  run: {
    run_id: "job-run/setup-main/superior/1",
    status: "OPERATION_RUNNING",
    current_operation_index: 0,
    total_operations: 4,
    completed_operations: 0,
    overall_progress: 0.13825,
    next_action: "Ejecutando Fresado superior",
    available_actions: ["pause", "cancel"],
    worker_alive: true,
    watcher_alive: true,
    last_watcher_error: null,
    updated_at: new Date().toISOString(),
  },
  operation: {
    operation_id: "op_1",
    name: "Fresado superior",
    tool: "0.8 mm",
    execution_status: "RUNNING",
    expected_remote_file: "klipper-cnc-assistant/proj_1/setup-main/superior/op_1_compensated.gcode",
    observed_filename: "klipper-cnc-assistant/proj_1/setup-main/superior/op_1_compensated.gcode",
    filename_match: true,
    observed_printing: true,
    progress: 0.553,
  },
  operations: [
    { operation_id: "op_1", order: 0, order_label: "001", name: "Fresado superior", type: "aislamiento", tool_id: "tool-08", tool_name: "0.8 mm", tool_key: "tool-08", tool_changed: false, reference_status: "LISTA", generated_file: "generated/op_1.gcode", generated_file_name: "op_1.gcode", execution_status: "RUNNING", started_at: null, completed_at: null, error: null, progress: 0.553, remote_file: "klipper-cnc-assistant/proj_1/setup-main/superior/op_1_compensated.gcode", observed_printing: true },
    { operation_id: "op_2", order: 1, order_label: "002", name: "Taladrado_1", type: "taladrado", tool_id: "tool-10", tool_name: "1.0 mm", tool_key: "tool-10", tool_changed: true, reference_status: "REQUIERE_REFERENCIA", generated_file: "generated/op_2.gcode", generated_file_name: "op_2.gcode", execution_status: "PENDING", started_at: null, completed_at: null, error: null, progress: 0 },
    { operation_id: "op_3", order: 2, order_label: "003", name: "Taladrado_2", type: "taladrado", tool_id: "tool-10", tool_name: "1.0 mm", tool_key: "tool-10", tool_changed: false, reference_status: "REQUIERE_REFERENCIA", generated_file: "generated/op_3.gcode", generated_file_name: "op_3.gcode", execution_status: "PENDING", started_at: null, completed_at: null, error: null, progress: 0 },
    { operation_id: "op_4", order: 3, order_label: "004", name: "Contorno", type: "contorno", tool_id: "tool-10", tool_name: "1.0 mm", tool_key: "tool-10", tool_changed: false, reference_status: "REQUIERE_REFERENCIA", generated_file: "generated/op_4.gcode", generated_file_name: "op_4.gcode", execution_status: "PENDING", started_at: null, completed_at: null, error: null, progress: 0 },
  ],
  transition: {
    state: "OPERATION_RUNNING",
    required_tool: "1.0 mm",
    operator_confirmation_required: false,
  },
  synchronization: {
    ok: true,
    reason: null,
  },
  events: [
    { event_id: "evt-1", timestamp: new Date().toISOString(), level: "info", stage: "JOB_STARTED", message: "Trabajo iniciado" },
    { event_id: "evt-2", timestamp: new Date().toISOString(), level: "info", stage: "RUNNING", message: "Klipper confirmó la ejecución" },
  ],
  job_run: {
    schema_version: "job-run-v1",
    run_id: "job-run/setup-main/superior/1",
    plan_id: "job-plan/setup-main/superior",
    project_id: "proj_1",
    setup_id: "setup-main",
    face: "superior",
    placement_revision: "placement-1",
    active_map_id: "map-1",
    state: "OPERATION_RUNNING",
    ready: true,
    checks: [],
    started_at: null,
    completed_at: null,
    updated_at: new Date().toISOString(),
    current_operation_index: 0,
    current_operation_id: "op_1",
    current_tool_key: "tool-08",
    next_action: "Ejecutando Fresado superior",
    available_actions: ["pause", "cancel"],
    operations: [],
    summary: { operations_total: 4, operations_completed: 0, tool_changes_required: 1, tool_changes_completed: 0 },
    timeline: [],
    events: [],
    manifest_path: null,
    last_watcher_error: null,
  },
};

describe("ExecutionConsole", () => {
  it("renderiza la consola v2 con progreso real de operación y proyecto", () => {
    render(<ExecutionConsole snapshot={baseSnapshot} error={null} busy={false} onPrepare={vi.fn()} onStart={vi.fn()} onAction={vi.fn()} onArchiveStale={vi.fn()} />);

    expect(screen.getByText(/CONSOLA DE EJECUCIÓN EN VIVO — V2/i)).toBeInTheDocument();
    expect(screen.getAllByText(/RUNNING/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/55.3 %/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/13.8 %/i)).toBeInTheDocument();
    expect(screen.getByText(/0 de 4 operaciones terminadas/i)).toBeInTheDocument();
    expect(screen.getByText(/Moonraker real/i)).toBeInTheDocument();
    expect(screen.getByText(/Orquestador JobRun/i)).toBeInTheDocument();
    expect(screen.queryByText(/^info$/i)).toBeNull();
  });

  it("muestra banner de desincronización y no duplica el botón de inicio", () => {
    render(
      <ExecutionConsole
        snapshot={{
          ...baseSnapshot,
          run: { ...baseSnapshot.run, status: "JOB_READY", available_actions: ["start", "start"] },
          operation: { ...baseSnapshot.operation, execution_status: "PREFLIGHT", observed_printing: false },
          synchronization: { ok: false, reason: "jobrun_not_running" },
          moonraker: { ...baseSnapshot.moonraker, print_state: "printing" },
          job_run: { ...(baseSnapshot.job_run as NonNullable<typeof baseSnapshot.job_run>), state: "JOB_READY", available_actions: ["start", "start"] },
        }}
        error={null}
        busy={false}
        onPrepare={vi.fn()}
        onStart={vi.fn()}
        onAction={vi.fn()}
        onArchiveStale={vi.fn()}
      />,
    );

    expect(screen.getByText(/DESINCRONIZACIÓN/i)).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Iniciar trabajo" })).toHaveLength(1);
  });

  it("muestra las acciones correctas para TOOL_CHANGE_REQUIRED y READY_TO_RESUME", () => {
    const { rerender } = render(
      <ExecutionConsole
        snapshot={{
          ...baseSnapshot,
          run: { ...baseSnapshot.run, status: "TOOL_CHANGE_REQUIRED", available_actions: ["confirm-tool-change", "cancel"] },
          transition: { ...baseSnapshot.transition, state: "TOOL_CHANGE_REQUIRED", operator_confirmation_required: true },
          job_run: { ...(baseSnapshot.job_run as NonNullable<typeof baseSnapshot.job_run>), state: "TOOL_CHANGE_REQUIRED", available_actions: ["confirm-tool-change", "cancel"] },
        }}
        error={null}
        busy={false}
        onPrepare={vi.fn()}
        onStart={vi.fn()}
        onAction={vi.fn()}
        onArchiveStale={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: /Herramienta cambiada/i })).toBeInTheDocument();

    rerender(
      <ExecutionConsole
        snapshot={{
          ...baseSnapshot,
          run: { ...baseSnapshot.run, status: "READY_TO_RESUME", available_actions: ["continue", "cancel"] },
          transition: { ...baseSnapshot.transition, state: "READY_TO_RESUME", operator_confirmation_required: true },
          job_run: { ...(baseSnapshot.job_run as NonNullable<typeof baseSnapshot.job_run>), state: "READY_TO_RESUME", available_actions: ["continue", "cancel"] },
        }}
        error={null}
        busy={false}
        onPrepare={vi.fn()}
        onStart={vi.fn()}
        onAction={vi.fn()}
        onArchiveStale={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: /Continuar trabajo/i })).toBeInTheDocument();
  });
  it("muestra el conflicto estructurado y la acción para cerrar la ejecución obsoleta", () => {
    const detail: JobRunConflictDetail = {
      code: "JOB_ACTIVE_CONFLICT",
      message: "Ya existe un trabajo activo para este montaje y cara.",
      conflict_condition: "current_run.state=JOB_VALIDATING no es terminal ni JOB_READY.",
      existing_run: {
        run_id: "job-run/setup-main/superior/20260722-040230",
        project_id: "proj_1",
        setup: "setup-main",
        side: "superior",
        placement_revision: "placement-72",
        status: "JOB_VALIDATING",
        current_operation: { operation_id: "op_1", name: "Fresado superior", execution_status: "PENDING", remote_file: null },
        remote_file: null,
        worker_alive: false,
        watcher_alive: false,
        supervisor_registered: false,
        movement_lock: false,
        job_lock: true,
        updated_at: new Date().toISOString(),
        last_error: null,
        available_actions: [],
      },
      moonraker: {
        connected: true,
        webhooks_state: "ready",
        klipper_state: "ready",
        print_state: "standby",
        filename: "",
        progress: 0,
        is_active: false,
        file_position: 0,
        file_size: 0,
        print_duration: 0,
        message: "",
        updated_at: new Date().toISOString(),
      },
      available_actions: ["open", "archive-stale"],
      can_archive_stale: true,
    };
    render(
      <ExecutionConsole
        snapshot={{
          ...baseSnapshot,
          moonraker: { ...baseSnapshot.moonraker, print_state: "standby", is_active: false, filename: "", progress: 0 },
          run: { ...baseSnapshot.run, status: "JOB_VALIDATING", worker_alive: false, watcher_alive: false, available_actions: [] },
          operation: { ...baseSnapshot.operation, execution_status: "PENDING", progress: 0, observed_printing: false },
          synchronization: { ok: true, reason: null },
          job_run: { ...(baseSnapshot.job_run as NonNullable<typeof baseSnapshot.job_run>), state: "JOB_VALIDATING", available_actions: [] },
        }}
        error={new ApiError("JOB_ACTIVE_CONFLICT: Ya existe un trabajo activo para este montaje y cara.", 409, {}, detail)}
        busy={false}
        onPrepare={vi.fn()}
        onStart={vi.fn()}
        onAction={vi.fn()}
        onArchiveStale={vi.fn()}
      />,
    );

    expect(screen.getByText(/Errores de ejecución/i)).toBeInTheDocument();
    expect(screen.getByText(/JOB_ACTIVE_CONFLICT/i)).toBeInTheDocument();
    expect(screen.getByText(/job-run\/setup-main\/superior\/20260722-040230/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Cerrar ejecución obsoleta/i })).toBeInTheDocument();
  });

});
