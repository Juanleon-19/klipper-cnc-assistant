import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MeshArmInspection, type MeshCoverage } from "./MeshWorkflow";
import type { MachineRuntime } from "../../types";

const inspect = vi.hoisted(() => vi.fn());
vi.mock("../../lib/api", () => ({ api: { inspectMeshArm: inspect } }));
const runtime = { state: "IDLE", recovery_pending: false, active_operation: null,
  physical_owner: { kind: "IDLE", owner_id: null, revision: 1 },
  moonraker: { http_connected: true, websocket_connected: true, telemetry_state: "LIVE" },
  klipper: { ready: true } } as unknown as MachineRuntime;
const validation: MeshCoverage = { coverage_valid: true, validated_at: "first", mesh_fingerprint: "fp", operations_checked: 1, segments_checked: 1, blocking_issues: [], warnings: [] };
const props = { projectId: "p", operationId: "o", recipe: { rows: 3 }, fingerprint: "fp", runtime, validation };
const allowed = { can_arm: true, blocking_reasons: [] };
const blocked = { can_arm: false, blocking_reasons: [{ code: "BUSY", message: "Otro worker conserva control.", action: "Espere al cierre." }] };
beforeEach(() => { vi.useFakeTimers(); inspect.mockReset(); });
afterEach(() => vi.useRealTimers());
async function settle() { await act(async () => { await vi.advanceTimersByTimeAsync(260); }); }

it.each([
  ["recovery", { runtime: { ...runtime, recovery_pending: true } }],
  ["operation", { runtime: { ...runtime, active_operation: { operation_id: "move", generation: 2 } } }],
  ["owner revision", { runtime: { ...runtime, physical_owner: { ...runtime.physical_owner, revision: 2 } } }],
  ["owner kind", { runtime: { ...runtime, physical_owner: { ...runtime.physical_owner, kind: "MESH", owner_id: "another" } } }],
  ["runtime state", { runtime: { ...runtime, state: "MESH_PROBING" } }],
  ["Klipper", { runtime: { ...runtime, klipper: { ready: false } } }],
  ["HTTP", { runtime: { ...runtime, moonraker: { ...runtime.moonraker, http_connected: false } } }],
  ["WS", { runtime: { ...runtime, moonraker: { ...runtime.moonraker, websocket_connected: false } } }],
  ["validation", { validation: { ...validation, validated_at: "next", coverage_valid: false } }],
  ["fingerprint", { fingerprint: "changed" }],
])("invalidates and refreshes assessment on %s change", async (_name, change) => {
  inspect.mockResolvedValueOnce(allowed).mockResolvedValueOnce(blocked);
  const view = render(<MeshArmInspection {...props} />);
  await settle();
  expect(screen.getByText("SE PUEDE ARMAR")).toBeInTheDocument();
  view.rerender(<MeshArmInspection {...props} {...change} />);
  expect(screen.queryByText("SE PUEDE ARMAR")).not.toBeInTheDocument();
  expect(screen.getByText("Verificando requisitos de armado…")).toBeInTheDocument();
  await settle();
  expect(inspect).toHaveBeenCalledTimes(2);
  expect(screen.getByText(/Otro worker conserva control/)).toBeInTheDocument();
});

it("discards a late allowed response from an earlier owner", async () => {
  let resolve!: (value: typeof allowed) => void;
  inspect.mockReturnValueOnce(new Promise(done => { resolve = done; })).mockResolvedValueOnce(blocked);
  const view = render(<MeshArmInspection {...props} />);
  await settle();
  view.rerender(<MeshArmInspection {...props} runtime={{ ...runtime, recovery_pending: true }} />);
  await settle();
  await act(async () => { resolve(allowed); });
  expect(screen.queryByText("SE PUEDE ARMAR")).not.toBeInTheDocument();
  expect(screen.getByText(/Otro worker conserva control/)).toBeInTheDocument();
});
