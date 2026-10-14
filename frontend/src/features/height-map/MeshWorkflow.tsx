import { useEffect, useState } from "react";
import type { MachineRuntime } from "../../types";
import { api, type PhysicalMapPlanPayload } from "../../lib/api";

export type MeshSeverity = "SUCCESS" | "INFO" | "WARNING" | "ERROR";
export type MeshCoverage = {
  coverage_valid: boolean; validated_at: string; mesh_fingerprint: string;
  operations_checked: number; segments_checked: number;
  blocking_issues: Array<{ code: string; reason: string; recommended_action: string; operation_name?: string; operation_id?: string; segment?: number; x_mm?: number; y_mm?: number }>;
  warnings: string[];
};
export type MeshArmReadiness = { can_arm: boolean; blocking_reasons: Array<{ code: string; message: string; action: string }> };
export function MeshNotice({ severity, children }: { severity: MeshSeverity; children: React.ReactNode }) {
  const style = { SUCCESS: "success", INFO: "info", WARNING: "warning", ERROR: "danger" }[severity];
  return <div role={severity === "ERROR" ? "alert" : "status"} className={`alert alert--${style}`} data-severity={severity}>{children}</div>;
}

/** Advisory only. Arm and start independently recheck backend safety guards. */
export function MeshArmInspection({ projectId, operationId, recipe, fingerprint, runtime, validation }: {
  projectId: string; operationId: string; recipe: PhysicalMapPlanPayload; fingerprint?: string; runtime?: MachineRuntime | null; validation?: MeshCoverage | null;
}) {
  const body = JSON.stringify({ ...recipe, preview_fingerprint: fingerprint });
  // Exclude ages/progress: only changes to prerequisites need a new inspection.
  const requestKey = JSON.stringify([body, projectId, operationId,
    runtime?.recovery_pending, runtime?.active_operation, runtime?.physical_owner,
    runtime?.state, runtime?.klipper?.ready, runtime?.moonraker?.http_connected,
    runtime?.moonraker?.websocket_connected, runtime?.moonraker?.telemetry_state,
    validation?.validated_at, validation?.mesh_fingerprint, validation?.coverage_valid]);
  const [response, setResponse] = useState<{ key: string; assessment: MeshArmReadiness | null; error: string } | null>(null);
  const assessment = response?.key === requestKey ? response.assessment : null;
  const error = response?.key === requestKey ? response.error : "";
  useEffect(() => {
    let current = true;
    const timer = window.setTimeout(() => {
      void api.inspectMeshArm(projectId, operationId, JSON.parse(body)).then(result => {
        if (current) setResponse({ key: requestKey, assessment: result, error: "" });
      }).catch((cause: unknown) => { if (current) setResponse({ key: requestKey, assessment: null, error: cause instanceof Error ? cause.message : "No se pudo verificar el armado." }); });
    }, 250);
    return () => { current = false; window.clearTimeout(timer); };
  }, [body, projectId, operationId, requestKey]);
  return <div aria-label="Requisitos para armar">
    <strong>{assessment?.can_arm && !error ? "SE PUEDE ARMAR" : "NO SE PUEDE ARMAR"}</strong>
    {error ? <MeshNotice severity="ERROR">{error} Actualice la inspección antes de continuar.</MeshNotice> : null}
    {assessment?.blocking_reasons.map(reason => <MeshNotice key={reason.code} severity="ERROR">{reason.message} {reason.action}</MeshNotice>)}
    {!assessment && !error ? <p>Verificando requisitos de armado…</p> : null}
  </div>;
}
