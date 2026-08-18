import { fireEvent, render, screen } from "@testing-library/react";
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
    stale_candidate: false,
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
  it("destaca tiempo restante y fin estimado junto al progreso", () => {
    render(
      <ExecutionConsole
        snapshot={{
          ...baseSnapshot,
          eta: {
            available: true,
            elapsed_s: 75,
            remaining_s: 245,
            completion_at: "2026-08-17T20:30:00-05:00",
            method: "calibrated",
            confidence: "high",
          },
        }}
        error={null}
        busy={false}
        onPrepare={vi.fn()}
        onStart={vi.fn()}
        onAction={vi.fn()}
        onArchiveStale={vi.fn()}
      />,
    );

    const etaRegion = screen.getByRole("region", { name: "Estimación de tiempo" });
    expect(etaRegion).toHaveTextContent("Tiempo restante:");
    expect(etaRegion).toHaveTextContent("4 min 05 s");
    expect(etaRegion).toHaveTextContent("Fin estimado:");
    expect(screen.getAllByText("Tiempo restante:")).toHaveLength(1);
  });

  it("indica que calcula el ETA durante una ejecución activa sin estimación disponible", () => {
    render(
      <ExecutionConsole
        snapshot={{ ...baseSnapshot, eta: { available: false, reason: "warming_up" } }}
        error={null}
        busy={false}
        onPrepare={vi.fn()}
        onStart={vi.fn()}
        onAction={vi.fn()}
        onArchiveStale={vi.fn()}
      />,
    );

    expect(screen.getByRole("region", { name: "Estimación de tiempo" })).toHaveTextContent(/Tiempo restante:\s*calculando\.\.\./i);
  });

  it("muestra una causa segura en RECOVERY_REQUIRED y oculta el traceback interno", () => {
    const onAction = vi.fn();
    render(
      <ExecutionConsole
        snapshot={{
          ...baseSnapshot,
          run: {
            ...baseSnapshot.run,
            status: "RECOVERY_REQUIRED",
            available_actions: ["retry-tool-change-transition", "cancel"],
            last_watcher_error: "Traceback (most recent call last): /private/runtime.py:99",
          },
          transition: {
            state: "RECOVERY_REQUIRED",
            required_tool: "Broca 0.8 mm",
            tool: "Broca 0.8 mm",
            tool_reference_profile: "long_tool",
            reference_prep_z_mm: 130,
            last_error: {
              code: "TOOL_CHANGE_TRANSITION_FAILED",
              message: "No se confirmó la posición segura.",
              occurred_at: "2026-08-17T20:00:00-05:00",
            },
            operator_confirmation_required: false,
          },
        }}
        error={null}
        busy={false}
        onPrepare={vi.fn()}
        onStart={vi.fn()}
        onAction={onAction}
        onArchiveStale={vi.fn()}
      />,
    );

    expect(screen.getByText("Transición de herramienta detenida")).toBeInTheDocument();
    expect(screen.getAllByText(/Causa: No se confirmó la posición segura\./i).length).toBeGreaterThan(0);
    expect(screen.queryByText(/Traceback \(most recent call last\)/i)).toBeNull();
    expect(screen.getByText("Herramienta larga")).toBeInTheDocument();
    expect(screen.getByText("130.000 mm")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Reintentar transición de herramienta" }));
    expect(onAction).toHaveBeenCalledWith("retry-tool-change-transition");
  });

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

  it("muestra checks aprobados, fallidos y sus detalles cuando el trabajo no está listo", () => {
    render(
      <ExecutionConsole
        snapshot={{
          ...baseSnapshot,
          run: { ...baseSnapshot.run, status: "JOB_VALIDATING", available_actions: [] },
          job_run: {
            ...(baseSnapshot.job_run as NonNullable<typeof baseSnapshot.job_run>),
            state: "JOB_VALIDATING",
            ready: false,
            checks: [
              { name: "modo_fisico", ok: true, detail: "MACHINE_MODE=physical requerido para ejecutar." },
              { name: "homing", ok: false, detail: "Homing actual: pendiente." },
              { name: "mapa_activo", ok: true, detail: "Mapa físico activo del montaje." },
              { name: "referencia_inicial", ok: false, detail: "Falta referencia Z de la herramienta inicial." },
            ],
            available_actions: [],
          },
        }}
        error={null}
        busy={false}
        onPrepare={vi.fn()}
        onStart={vi.fn()}
        onAction={vi.fn()}
        onArchiveStale={vi.fn()}
      />,
    );

    expect(screen.getByText(/Preparación del trabajo/i)).toBeInTheDocument();
    expect(screen.getByText(/Trabajo no listo/i)).toBeInTheDocument();
    expect(screen.getByText(/OK · Modo físico/i)).toBeInTheDocument();
    expect(screen.getByText(/OK · Mapa físico/i)).toBeInTheDocument();
    expect(screen.getByText(/FALLA · Homing XYZ/i)).toBeInTheDocument();
    expect(screen.getByText(/FALLA · Referencia inicial/i)).toBeInTheDocument();
    expect(screen.getAllByText(/Homing actual: pendiente\./i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Falta referencia Z de la herramienta inicial\./i).length).toBeGreaterThan(0);
    expect(screen.getByText(/Bloqueos que debes resolver/i)).toBeInTheDocument();
  });

  it("muestra el estado listo cuando todos los checks están OK", () => {
    render(
      <ExecutionConsole
        snapshot={{
          ...baseSnapshot,
          run: { ...baseSnapshot.run, status: "JOB_READY", available_actions: ["start"] },
          job_run: {
            ...(baseSnapshot.job_run as NonNullable<typeof baseSnapshot.job_run>),
            state: "JOB_READY",
            ready: true,
            checks: [
              { name: "modo_fisico", ok: true, detail: "MACHINE_MODE=physical requerido para ejecutar." },
              { name: "archivos_compensados", ok: true, detail: "Cada operación activa tiene archivo compensado generado." },
            ],
            available_actions: ["start"],
          },
        }}
        error={null}
        busy={false}
        onPrepare={vi.fn()}
        onStart={vi.fn()}
        onAction={vi.fn()}
        onArchiveStale={vi.fn()}
      />,
    );

    expect(screen.getByText(/Trabajo listo para iniciar/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Iniciar trabajo" })).toBeInTheDocument();
    expect(screen.queryByText(/Bloqueos que debes resolver/i)).toBeNull();
  });

  it("bloquea preparar e iniciar cuando la configuración física no está confirmada", () => {
    const onPrepare = vi.fn();
    const onStart = vi.fn();
    render(
      <ExecutionConsole
        snapshot={{
          ...baseSnapshot,
          run: { ...baseSnapshot.run, status: "JOB_READY", available_actions: ["start"] },
          job_run: {
            ...(baseSnapshot.job_run as NonNullable<typeof baseSnapshot.job_run>),
            state: "JOB_READY",
            available_actions: ["start"],
          },
        }}
        error={null}
        busy={false}
        settingsBlocked
        settingsBlockReason="Hay cambios de configuración de máquina sin guardar. Guárdelos y confirme el runtime antes de preparar la ejecución."
        onPrepare={onPrepare}
        onStart={onStart}
        onAction={vi.fn()}
        onArchiveStale={vi.fn()}
      />,
    );

    expect(screen.getByText(/Hay cambios de configuración de máquina sin guardar/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Preparar trabajo" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Iniciar trabajo" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Preparar trabajo" }));
    fireEvent.click(screen.getByRole("button", { name: "Iniciar trabajo" }));
    expect(onPrepare).not.toHaveBeenCalled();
    expect(onStart).not.toHaveBeenCalled();
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

  it("no dispara prepare automáticamente en render ni rerender, y el click manual hace una sola petición", () => {
    const onPrepare = vi.fn();
    const { rerender } = render(
      <ExecutionConsole
        snapshot={{
          ...baseSnapshot,
          run: { ...baseSnapshot.run, status: "JOB_VALIDATING", available_actions: [] },
          job_run: { ...(baseSnapshot.job_run as NonNullable<typeof baseSnapshot.job_run>), state: "JOB_VALIDATING", available_actions: [] },
        }}
        error={null}
        busy={false}
        onPrepare={onPrepare}
        onStart={vi.fn()}
        onAction={vi.fn()}
        onArchiveStale={vi.fn()}
      />,
    );

    expect(onPrepare).not.toHaveBeenCalled();

    rerender(
      <ExecutionConsole
        snapshot={{
          ...baseSnapshot,
          run: { ...baseSnapshot.run, status: "JOB_VALIDATING", available_actions: [] },
          job_run: { ...(baseSnapshot.job_run as NonNullable<typeof baseSnapshot.job_run>), state: "JOB_VALIDATING", available_actions: [] },
        }}
        error={null}
        busy={false}
        onPrepare={onPrepare}
        onStart={vi.fn()}
        onAction={vi.fn()}
        onArchiveStale={vi.fn()}
      />,
    );

    expect(onPrepare).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: /Preparar trabajo/i }));

    expect(onPrepare).toHaveBeenCalledTimes(1);
  });

  it("separa la confirmación de spindle detenido y herramienta cambiada", () => {
    const { rerender } = render(
      <ExecutionConsole
        snapshot={{
          ...baseSnapshot,
          run: { ...baseSnapshot.run, status: "SPINDLE_STOP_REQUIRED", available_actions: ["confirm-spindle-stopped", "cancel"] },
          transition: { ...baseSnapshot.transition, state: "SPINDLE_STOP_REQUIRED", operator_confirmation_required: true },
          job_run: { ...(baseSnapshot.job_run as NonNullable<typeof baseSnapshot.job_run>), state: "SPINDLE_STOP_REQUIRED", available_actions: ["confirm-spindle-stopped", "cancel"] },
        }}
        error={null}
        busy={false}
        onPrepare={vi.fn()}
        onStart={vi.fn()}
        onAction={vi.fn()}
        onArchiveStale={vi.fn()}
      />,
    );

    expect(screen.getAllByText(/Apague manualmente el spindle/i).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: /Spindle detenido/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Herramienta cambiada/i })).toBeNull();

    rerender(
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

    expect(screen.getAllByText(/Cambie físicamente la herramienta/i).length).toBeGreaterThan(0);
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

    expect(screen.getByRole("button", { name: /Spindle preparado — continuar/i })).toBeInTheDocument();
  });

  it("explica en READY_TO_RESUME que Continuar genera Legacy JIT con la nueva referencia", () => {
    render(
      <ExecutionConsole
        snapshot={{
          ...baseSnapshot,
          run: { ...baseSnapshot.run, status: "READY_TO_RESUME", available_actions: ["continue", "cancel"] },
          transition: {
            ...baseSnapshot.transition,
            state: "READY_TO_RESUME",
            tool: "Broca 0.8 mm",
            tool_reference_profile: "long_tool",
            tool_change_profile: "long_tool",
            tool_change_clearance_z_mm: 130,
            reference_prep_z_mm: 105,
            operator_confirmation_required: true,
          },
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

    expect(screen.getByRole("region", { name: "Nueva referencia Z lista" })).toHaveTextContent("Broca 0.8 mm");
    expect(screen.getByRole("region", { name: "Nueva referencia Z lista" })).toHaveTextContent("Herramienta larga");
    expect(screen.getAllByText("Perfil de cambio").length).toBeGreaterThan(0);
    expect(screen.getByText("Z segura durante cambio").parentElement).toHaveTextContent("130.000 mm");
    expect(screen.getByText("Z de aproximación a referencia").parentElement).toHaveTextContent("105.000 mm");
    expect(screen.getByText(/La Z segura se usa solo durante el cambio de herramienta/i)).toBeInTheDocument();
    expect(screen.getByText("Nueva referencia Z lista")).toBeInTheDocument();
    expect(screen.getByText(/generará automáticamente una nueva compensación Legacy/i)).toBeInTheDocument();
    expect(screen.getByText(/No es necesario volver a la sección Compensación/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Spindle preparado — continuar" })).toBeInTheDocument();
  });

  it("describe OPERATION_PREFLIGHT como generación de compensación Legacy JIT", () => {
    render(
      <ExecutionConsole
        snapshot={{
          ...baseSnapshot,
          run: { ...baseSnapshot.run, status: "OPERATION_PREFLIGHT", available_actions: ["cancel"] },
          transition: { ...baseSnapshot.transition, state: "OPERATION_PREFLIGHT" },
        }}
        error={null}
        busy={false}
        onPrepare={vi.fn()}
        onStart={vi.fn()}
        onAction={vi.fn()}
        onArchiveStale={vi.fn()}
      />,
    );

    expect(screen.getByRole("region", { name: "Flujo automático de la siguiente operación" })).toHaveTextContent("Generando compensación Legacy JIT");
  });

  it("describe OPERATION_UPLOADING como subida del archivo compensado a Moonraker", () => {
    render(
      <ExecutionConsole
        snapshot={{
          ...baseSnapshot,
          run: { ...baseSnapshot.run, status: "OPERATION_UPLOADING", available_actions: ["cancel"] },
          transition: { ...baseSnapshot.transition, state: "OPERATION_UPLOADING" },
        }}
        error={null}
        busy={false}
        onPrepare={vi.fn()}
        onStart={vi.fn()}
        onAction={vi.fn()}
        onArchiveStale={vi.fn()}
      />,
    );

    expect(screen.getByRole("region", { name: "Flujo automático de la siguiente operación" })).toHaveTextContent("Subiendo archivo compensado a Moonraker");
  });

  it("describe WAITING_FOR_KLIPPER como espera de confirmación de Klipper", () => {
    render(
      <ExecutionConsole
        snapshot={{
          ...baseSnapshot,
          run: { ...baseSnapshot.run, status: "WAITING_FOR_KLIPPER", available_actions: ["cancel"] },
          transition: { ...baseSnapshot.transition, state: "WAITING_FOR_KLIPPER" },
        }}
        error={null}
        busy={false}
        onPrepare={vi.fn()}
        onStart={vi.fn()}
        onAction={vi.fn()}
        onArchiveStale={vi.fn()}
      />,
    );

    expect(screen.getByRole("region", { name: "Flujo automático de la siguiente operación" })).toHaveTextContent("Esperando confirmación de Klipper");
  });

  it("muestra la etapa y las velocidades independientes de la transición automática", () => {
    render(
      <ExecutionConsole
        snapshot={{
          ...baseSnapshot,
          run: { ...baseSnapshot.run, status: "MOVING_TO_REFERENCE", available_actions: ["cancel"] },
          transition: {
            ...baseSnapshot.transition,
            state: "MOVING_TO_REFERENCE",
            stage: "MOVING_TO_REFERENCE",
            tool_change_clearance_z_mm: 130,
            reference_prep_z_mm: 105,
            z_clearance_feed_mm_min: 240,
            reference_approach_z_feed_mm_min: 45,
            reference_probe_feed_mm_min: 30,
          },
        }}
        error={null}
        busy={false}
        onPrepare={vi.fn()}
        onStart={vi.fn()}
        onAction={vi.fn()}
        onArchiveStale={vi.fn()}
      />,
    );

    const transition = screen.getByRole("region", { name: "Medición automática de la nueva referencia" });
    expect(transition).toHaveTextContent("Subiendo a clearance 130.000 mm · 240 mm/min");
    expect(transition).toHaveTextContent("Moviendo a referencia");
    expect(transition).toHaveTextContent("Aproximando Z a 105.000 mm · 45 mm/min");
    expect(transition).toHaveTextContent("Midiendo referencia Z · 30 mm/min");
  });

  it("mantiene el aviso de TOOL_CHANGE_REQUIRED y anticipa la nueva referencia", () => {
    render(
      <ExecutionConsole
        snapshot={{
          ...baseSnapshot,
          run: { ...baseSnapshot.run, status: "TOOL_CHANGE_REQUIRED", available_actions: ["confirm-tool-change", "cancel"] },
          transition: {
            ...baseSnapshot.transition,
            state: "TOOL_CHANGE_REQUIRED",
            required_tool: "Broca 0.8 mm",
            operator_confirmation_required: true,
          },
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

    expect(screen.getAllByText(/Cambie físicamente la herramienta/i).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Herramienta cambiada" })).toBeInTheDocument();
    expect(screen.getByText(/la referencia anterior dejará de ser válida/i)).toBeInTheDocument();
    expect(screen.getByText(/se medirá la nueva herramienta antes de generar su compensación/i)).toBeInTheDocument();
  });

  it("no muestra cerrar ejecución obsoleta para un JOB_VALIDATING nuevo", () => {
    render(
      <ExecutionConsole
        snapshot={{
          ...baseSnapshot,
          moonraker: { ...baseSnapshot.moonraker, print_state: "standby", is_active: false, filename: "", progress: 0 },
          run: { ...baseSnapshot.run, status: "JOB_VALIDATING", worker_alive: false, watcher_alive: false, supervisor_registered: false, stale_candidate: false, available_actions: [] },
          operation: { ...baseSnapshot.operation, execution_status: "PENDING", progress: 0, observed_printing: false },
          synchronization: { ok: true, reason: null },
          job_run: { ...(baseSnapshot.job_run as NonNullable<typeof baseSnapshot.job_run>), state: "JOB_VALIDATING", available_actions: [] },
        }}
        error={null}
        busy={false}
        onPrepare={vi.fn()}
        onStart={vi.fn()}
        onAction={vi.fn()}
        onArchiveStale={vi.fn()}
      />,
    );

    expect(screen.queryByRole("button", { name: /Cerrar ejecución obsoleta/i })).toBeNull();
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
          run: { ...baseSnapshot.run, status: "JOB_VALIDATING", worker_alive: false, watcher_alive: false, supervisor_registered: false, stale_candidate: false, available_actions: [] },
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
