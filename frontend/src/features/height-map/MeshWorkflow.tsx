import { useEffect, useState } from "react";
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
export function MeshArmInspection({ projectId, operationId, recipe, fingerprint, runtimeRevision }: {
  projectId: string; operationId: string; recipe: PhysicalMapPlanPayload; fingerprint?: string; runtimeRevision: string;
}) {
  const [assessment, setAssessment] = useState<MeshArmReadiness | null>(null);
  const [error, setError] = useState("");
  const body = JSON.stringify({ ...recipe, preview_fingerprint: fingerprint });
  useEffect(() => {
    let current = true;
    const timer = window.setTimeout(() => {
      void api.inspectMeshArm(projectId, operationId, JSON.parse(body)).then(result => {
        if (current) { setAssessment(result); setError(""); }
      }).catch((cause: unknown) => { if (current) { setAssessment(null); setError(cause instanceof Error ? cause.message : "No se pudo verificar el armado."); } });
    }, 250);
    return () => { current = false; window.clearTimeout(timer); };
  }, [body, projectId, operationId, runtimeRevision]);
  return <div aria-label="Requisitos para armar">
    <strong>{assessment?.can_arm && !error ? "SE PUEDE ARMAR" : "NO SE PUEDE ARMAR"}</strong>
    {error ? <MeshNotice severity="ERROR">{error} Actualice la inspección antes de continuar.</MeshNotice> : null}
    {assessment?.blocking_reasons.map(reason => <MeshNotice key={reason.code} severity="ERROR">{reason.message} {reason.action}</MeshNotice>)}
    {!assessment && !error ? <p>Verificando requisitos de armado…</p> : null}
  </div>;
}
