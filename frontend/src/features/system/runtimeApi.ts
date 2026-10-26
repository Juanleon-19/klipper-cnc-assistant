import type { MachineRuntime } from "../../types";

async function readError(response: Response): Promise<string> {
  const payload = await response.json().catch(() => ({})) as {
    detalle?: unknown;
    detail?: unknown;
  };
  const detail = payload.detalle ?? payload.detail;
  if (typeof detail === "string" && detail.trim()) {
    return detail;
  }
  if (detail && typeof detail === "object") {
    const record = detail as Record<string, unknown>;
    if (typeof record.message === "string" && record.message.trim()) {
      return record.message;
    }
    if (typeof record.detalle === "string" && record.detalle.trim()) {
      return record.detalle;
    }
  }
  return `No fue posible reconectar el runtime (HTTP ${response.status}).`;
}

export async function reconnectRuntime(): Promise<MachineRuntime> {
  const response = await fetch("/api/machine/reconnect-runtime", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  if (!response.ok) {
    throw new Error(await readError(response));
  }
  return await response.json() as MachineRuntime;
}

export async function recoverIdleControls(): Promise<MachineRuntime> {
  const response = await fetch("/api/machine/recover-idle-controls", { method: "POST" });
  if (!response.ok) throw new Error(await readError(response));
  return await response.json() as MachineRuntime;
}

export async function recoverAdministrativeTransaction(): Promise<void> {
  const inspection = await fetch("/api/machine/administrative-recovery");
  if (!inspection.ok) throw new Error(await readError(inspection));
  const pending = await inspection.json() as { pending: boolean; id?: string };
  if (!pending.pending || !pending.id) throw new Error("No hay transacción pendiente; actualice el estado.");
  const response = await fetch(`/api/machine/administrative-recovery/${encodeURIComponent(pending.id)}`, { method: "POST" });
  if (!response.ok) throw new Error(await readError(response));
}
