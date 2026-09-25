import type { ExecutionReadiness } from "../types";

export const readyExecution = (overrides: Partial<ExecutionReadiness> = {}): ExecutionReadiness => ({
  can_start: true, conditions_ready: true, can_prepare: true,
  blocking_reasons: [], checks: [], evaluated_at: "2026-09-23T00:00:00Z",
  physical_session_id: "fake-session", ownership_session_id: "fake-epoch",
  run_id: "fake-run", current_operation: { operation_id: "op_1", index: 0, total: 5, name: "Fresado superior", tool: "3 mm", installation_id: "A", reference_revision: "ref-A" },
  reference: { valid: true, code: "REFERENCE_VALID", status: "LISTA", message: "Referencia vigente." },
  map_ready: true, geometry: { covered: 5, blocked: 0 }, ...overrides,
});
