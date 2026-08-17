from __future__ import annotations

import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_BLOBS = {
    "src/klipper_cnc_assistant/execution/job_service.py": "2982e01246112ecbe78e00147d2e86271f534a29",
    "frontend/src/features/projects/ProjectWorkspace.tsx": "a06502ae3667d79ed7cd17b8aed754bd6e652caa",
    "frontend/src/features/references/ReferenceWorkspace.tsx": "e9f5884d255d8f5c35ec89356f97d729baa44247",
    "frontend/src/features/execution/ExecutionConsole.tsx": "8d055ccf80410c3506135416efd4ec05117f7a21",
}


def git_blob(path: str) -> str:
    result = subprocess.run(
        ["git", "hash-object", path],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: se esperaba 1 coincidencia exacta y se encontraron {count}.")
    return text.replace(old, new, 1)


def sub_once(text: str, pattern: str, replacement: str, label: str, *, flags: int = 0) -> str:
    updated, count = re.subn(pattern, replacement, text, count=1, flags=flags)
    if count != 1:
        raise RuntimeError(f"{label}: se esperaba 1 coincidencia regex y se encontraron {count}.")
    return updated


def verify_baseline() -> None:
    branch = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if branch != "hotfix/final-workflow-cleanup-2026-08-16":
        raise RuntimeError(
            "Este aplicador solo puede ejecutarse en hotfix/final-workflow-cleanup-2026-08-16; "
            f"rama actual: {branch or '(detached)'}"
        )
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if status:
        raise RuntimeError("Hay cambios rastreados antes de aplicar el hotfix. Revise git status y no continúe a ciegas.")
    for path, expected in EXPECTED_BLOBS.items():
        actual = git_blob(path)
        if actual != expected:
            raise RuntimeError(f"Baseline inesperado en {path}: {actual} != {expected}. No se aplicó ningún cambio.")


def patch_job_service() -> None:
    path = ROOT / "src/klipper_cnc_assistant/execution/job_service.py"
    text = path.read_text(encoding="utf-8")

    text = replace_once(
        text,
        "import json\nimport hashlib\n",
        "import json\nimport hashlib\nimport math\n",
        "job_service/import math",
    )

    text = replace_once(
        text,
        '''                generated = self.compensated_gcode_service.generate(\n                    project_id,\n                    item["operation_id"],\n                    require_tool_reference=False,\n                )''',
        '''                generated = self.compensated_gcode_service.generate(\n                    project_id,\n                    item["operation_id"],\n                    mode="legacy",\n                    require_tool_reference=False,\n                )''',
        "job_service/project compensation legacy-only",
    )

    text = replace_once(
        text,
        '''        progress = self._clamp_progress((operation or {}).get("progress"))\n        overall_progress = 1.0 if str(run.get("state")) == "JOB_COMPLETE" else (min(1.0, (completed + progress) / total) if total else 0.0)\n        next_index = index + 1''',
        '''        progress = self._clamp_progress((operation or {}).get("progress"))\n        if str(run.get("state")) == "JOB_COMPLETE":\n            overall_progress = 1.0\n        else:\n            weighted_total = 0.0\n            weighted_done = 0.0\n            weighted_available = bool(operations)\n            for item in operations:\n                raw_estimate = item.get("estimated_time_s") or item.get("original_estimated_time_s")\n                try:\n                    weight = float(raw_estimate)\n                except (TypeError, ValueError):\n                    weighted_available = False\n                    break\n                if not math.isfinite(weight) or weight <= 0:\n                    weighted_available = False\n                    break\n                execution_status = str(item.get("execution_status") or "")\n                fraction = 1.0 if execution_status == "COMPLETED" else self._clamp_progress(item.get("progress"))\n                weighted_total += weight\n                weighted_done += weight * fraction\n            if weighted_available and weighted_total > 0:\n                overall_progress = self._clamp_progress(weighted_done / weighted_total)\n            else:\n                current_is_completed = str((operation or {}).get("execution_status") or "") == "COMPLETED"\n                current_fraction = 0.0 if current_is_completed else progress\n                overall_progress = min(1.0, (completed + current_fraction) / total) if total else 0.0\n        next_index = index + 1''',
        "job_service/overall progress",
    )

    text = replace_once(
        text,
        '''        snapshot = self.runtime.snapshot()\n        if str(snapshot.get("moonraker", {}).get("telemetry_state") or "") != "LIVE":\n            raise ApplicationError("La telemetría Moonraker debe estar LIVE para confirmar el spindle detenido.")''',
        '''        refresh_observed_state = getattr(self.runtime, "refresh_observed_state", None)\n        try:\n            snapshot = refresh_observed_state() if callable(refresh_observed_state) else self.runtime.snapshot()\n        except Exception as error:\n            raise ApplicationError(\n                f"No fue posible actualizar el estado de Moonraker antes de confirmar el spindle detenido: {error}"\n            ) from error\n        if str(snapshot.get("moonraker", {}).get("telemetry_state") or "") != "LIVE":\n            raise ApplicationError(\n                "La telemetría Moonraker sigue sin estar fresca después de una observación HTTP activa; "\n                "no se autoriza el movimiento de cambio de herramienta."\n            )''',
        "job_service/spindle active observation",
    )

    text = replace_once(
        text,
        '''        missing_generated = [item for item in plan["operations"] if not item.get("generated_file") and not item["blocking"]]\n        add("archivos_compensados", not missing_generated, "Cada operación activa tiene archivo compensado generado.")''',
        '''        add(\n            "compensacion_jit",\n            True,\n            "La compensación Legacy se generará justo antes de cada operación usando la referencia Z vigente.",\n        )''',
        "job_service/preflight jit",
    )

    text = sub_once(
        text,
        r'''        run\["state"\] = "REGENERATING_COMPENSATION"\n        run\["next_action"\] = "Regenerando compensación pendiente con la nueva referencia Z"\n        self\._save_run\(context, run\)\n        self\.generate_project_compensation\(project_id=context\.project_id, setup_id=context\.setup_id, face=context\.face\)\n        run\["state"\] = "VALIDATING_REGENERATED_PLAN"\n        dry_run = self\.dry_run\(project_id=context\.project_id, setup_id=context\.setup_id, face=context\.face\)\n        if not dry_run\.get\("ok"\):\n            raise ApplicationError\("El dry-run del plan regenerado falló\."\)\n        run\["dry_run"\] = dry_run\n        operation_payload\["reference_status"\] = "LISTA"''',
        '''        operation_payload["reference_status"] = "LISTA"''',
        "job_service/no project-wide regeneration after tool change",
    )

    text = replace_once(
        text,
        '''        run["state"] = "READY_TO_RESUME"\n        run["next_action"] = "Revisar la nueva calibración y continuar trabajo"\n        run["available_actions"] = ["continue", "cancel"]\n        self._append_event(run, "info", f"Referencia Z medida para {operation_payload['tool_name']}; esperando confirmación explícita para continuar.")''',
        '''        run["state"] = "READY_TO_RESUME"\n        run["next_action"] = "Referencia Z lista; continuar para generar Legacy y ejecutar la siguiente operación"\n        run["available_actions"] = ["continue", "cancel"]\n        self._append_event(\n            run,\n            "info",\n            f"Referencia Z medida para {operation_payload['tool_name']}; la compensación Legacy se generará al continuar.",\n        )''',
        "job_service/tool reference next action",
    )

    text = replace_once(
        text,
        '''        adapter = self.adapter_factory(self.runtime)\n        plan = self._load_or_build_plan(context)\n        generated = self._generated_payload_for_operation(plan, operation["operation_id"])\n        if generated is None:\n            raise ApplicationError(f"No existe archivo compensado para la operación {operation['name']}.")\n        operation["execution_status"] = "PREFLIGHT"\n        run["current_operation_index"] = index\n        run["current_operation_id"] = operation["operation_id"]\n        run["current_tool_key"] = operation["tool_key"]\n        expected_remote_file = self._expected_remote_file(context, str(generated["relative_path"]))''',
        '''        adapter = self.adapter_factory(self.runtime)\n        operation["execution_status"] = "PREFLIGHT"\n        run["current_operation_index"] = index\n        run["current_operation_id"] = operation["operation_id"]\n        run["current_tool_key"] = operation["tool_key"]\n        run["state"] = "OPERATION_PREFLIGHT"\n        run["next_action"] = f"Generando compensación Legacy para {operation['name']}"\n        run["available_actions"] = ["pause", "cancel"]\n        self._append_event(run, "info", f"Generando compensación Legacy JIT para {operation['name']}.")\n        self._save_run(context, run)\n        generated = self.compensated_gcode_service.generate(\n            context.project_id,\n            operation["operation_id"],\n            mode="legacy",\n            require_tool_reference=True,\n        )\n        operation["generated_file"] = generated["relative_path"]\n        operation["generated_file_name"] = Path(str(generated["relative_path"])).name\n        operation["generated_metadata_path"] = generated.get("metadata_path")\n        operation["compensation_mode"] = "legacy"\n        expected_remote_file = self._expected_remote_file(context, str(generated["relative_path"]))''',
        "job_service/jit legacy generation",
    )

    text = replace_once(
        text,
        '''        metadata_mode = str(metadata.get("compensation_mode") or "legacy")\n        if metadata_mode != str(operation.compensation_mode):\n            return False''',
        '''        metadata_mode = str(metadata.get("compensation_mode") or "legacy")\n        if metadata_mode != "legacy":\n            return False''',
        "job_service/legacy artifact authority",
    )

    path.write_text(text, encoding="utf-8")


def patch_project_workspace() -> None:
    path = ROOT / "frontend/src/features/projects/ProjectWorkspace.tsx"
    text = path.read_text(encoding="utf-8")

    text = replace_once(
        text,
        'import { formatDate, formatFileSize, formatMillimeters, formatNumber } from "../../lib/format";',
        'import { formatDate, formatFileSize, formatMillimeters } from "../../lib/format";',
        "ProjectWorkspace/remove formatNumber",
    )
    text = replace_once(text, "  CompensationAudit,\n", "", "ProjectWorkspace/remove audit type import")
    text = sub_once(
        text,
        r'''type CompensationAuditRequester = \(\n.*?\n\) => Promise<CompensationAudit>;\n\n''',
        "",
        "ProjectWorkspace/remove audit requester type",
        flags=re.S,
    )
    text = sub_once(
        text,
        r'''const getCompensationAuditRequest =\n.*?function isAbortedRequest\(error: unknown\) \{\n.*?\n\}\n\n''',
        "",
        "ProjectWorkspace/remove audit helpers",
        flags=re.S,
    )

    for old in (
        '  const [compensationAudit, setCompensationAudit] = useState<CompensationAudit | null>(null);\n',
        '  const [compensationAuditBusy, setCompensationAuditBusy] = useState(false);\n',
        '  const [compensationAuditError, setCompensationAuditError] = useState<string | null>(null);\n',
        '  const [compensationToleranceInput, setCompensationToleranceInput] = useState("0.05");\n',
        '  const compensationAuditAbortRef = useRef<AbortController | null>(null);\n',
        '  const compensationAuditRequestIdRef = useRef(0);\n',
        '  const activeOperationId = selectedOperation?.id ?? null;\n',
        '  const projectId = project?.id ?? null;\n',
    ):
        text = replace_once(text, old, "", f"ProjectWorkspace/remove {old.strip()[:40]}")

    text = sub_once(
        text,
        r'''  useEffect\(\(\) => \{\n    setCompensationToleranceInput\(selectedOperation \? String\(selectedOperation\.max_z_error_mm \?\? 0\.05\) : "0\.05"\);\n  \}, \[selectedOperation\]\);\n\n''',
        "",
        "ProjectWorkspace/remove tolerance effect",
    )

    text = sub_once(
        text,
        r'''  const requestCompensationAudit = useCallback\(.*?\n  \}, \[activeOperationId, projectId, requestCompensationAudit\]\);\n\n''',
        "",
        "ProjectWorkspace/remove audit request/effect",
        flags=re.S,
    )

    text = sub_once(
        text,
        r'''  const updateCompensationSettings = async .*?\n  const prepareJobRun = async''',
        '''  const generateSelectedLegacyCompensation = async () => {\n    if (compensationInFlight.current || !project || !selectedOperation) {\n      return;\n    }\n    compensationInFlight.current = true;\n    setCompensationBusy(true);\n    setWorkspaceError("");\n    try {\n      const generated = await api.generateCompensatedGCode(project.id, selectedOperation.id, "legacy");\n      if (generated.warning) {\n        setWorkspaceError(generated.warning);\n      }\n      await refreshJobState();\n      if (onRefreshProject) {\n        await onRefreshProject();\n      }\n    } catch (error) {\n      setWorkspaceError(error instanceof Error ? error.message : "No fue posible generar la compensación Legacy de esta operación.");\n    } finally {\n      compensationInFlight.current = false;\n      setCompensationBusy(false);\n    }\n  };\n\n  const prepareJobRun = async''',
        "ProjectWorkspace/replace compensation actions",
        flags=re.S,
    )

    new_panel = '''  const renderJobCompensationPanel = () => {\n    if (!selectedSetup || !activeJobFace) {\n      return null;\n    }\n    const compensationControlsBusy = referenceBusy || compensationBusy;\n    const selectedPlanOperation = selectedOperation\n      ? jobPlan?.operations.find((item) => item.operation_id === selectedOperation.id) ?? null\n      : null;\n    return (\n      <article className="panel">\n        <div className="section-heading section-heading--stacked">\n          <div>\n            <p className="eyebrow">1. Compensación por operación</p>\n            <h3>Legacy · generación justo a tiempo — {translateFace(activeJobFace)}</h3>\n          </div>\n          <div className="toolbar-inline">\n            <button className="button button--ghost" type="button" disabled={compensationControlsBusy} onClick={() => void prepareJobRun()}>Revalidar plan</button>\n            <button className="button" type="button" disabled={compensationControlsBusy || !selectedOperation} onClick={() => void generateSelectedLegacyCompensation()}>\n              {compensationBusy ? "Generando compensación…" : "Generar compensación de esta operación"}\n            </button>\n          </div>\n        </div>\n        {selectedOperation ? (\n          <div className="stack gap-md">\n            <div className="info-grid info-grid--double compact-grid">\n              <div className="metric-box"><span>Operación seleccionada</span><strong>{selectedOperation.nombre}</strong></div>\n              <div className="metric-box"><span>Método de producción</span><strong>Legacy</strong></div>\n              <div className="metric-box"><span>Mapa</span><strong>{selectedPlanOperation?.map_status ?? "pendiente"}</strong></div>\n              <div className="metric-box"><span>Referencia Z</span><strong>{selectedPlanOperation?.reference_status ?? "pendiente"}</strong></div>\n              <div className="metric-box"><span>Archivo compensado</span><strong>{selectedPlanOperation?.generated_file_name ?? "Se generará al ejecutar"}</strong></div>\n            </div>\n            <p className="muted">La ejecución genera una compensación Legacy nueva únicamente para la operación que va a ejecutarse. Un cambio de herramienta conserva el mapa, mide la nueva Z y solo después genera la siguiente operación pendiente.</p>\n          </div>\n        ) : <p className="muted">Seleccione una operación para revisar o generar su compensación.</p>}\n        {jobPlan ? (\n          <>\n            <div className="info-grid info-grid--double compact-grid">\n              <div className="metric-box"><span>Operaciones</span><strong>{jobPlan.summary.operations_total}</strong></div>\n              <div className="metric-box"><span>Listas</span><strong>{jobPlan.summary.operations_ready}</strong></div>\n              <div className="metric-box"><span>Archivos ya generados</span><strong>{jobPlan.summary.generated_files}</strong></div>\n              <div className="metric-box"><span>Cambios de herramienta</span><strong>{jobPlan.summary.tool_changes}</strong></div>\n              <div className="metric-box"><span>Herramientas distintas</span><strong>{jobPlan.summary.distinct_tools}</strong></div>\n              <div className="metric-box"><span>Bloqueadas</span><strong>{jobPlan.summary.blocked_operations}</strong></div>\n            </div>\n            <div className="table-scroll">\n              <table className="data-table">\n                <thead><tr><th>Orden</th><th>Operación</th><th>Herramienta</th><th>Mapa</th><th>Referencia Z</th><th>G-code</th></tr></thead>\n                <tbody>\n                  {jobPlan.operations.map((item) => (\n                    <tr key={item.operation_id}>\n                      <td>{item.order_label}</td>\n                      <td><strong>{item.name}</strong>{item.blocking_reasons.length > 0 ? <div className="muted">{item.blocking_reasons[0]}</div> : null}</td>\n                      <td>{item.tool_name}</td>\n                      <td>{item.map_status}</td>\n                      <td>{item.reference_status}</td>\n                      <td>{item.generated_file_name ?? "se generará al ejecutar"}</td>\n                    </tr>\n                  ))}\n                </tbody>\n              </table>\n            </div>\n          </>\n        ) : <p className="muted">Revalide el plan para ver el estado de las operaciones.</p>}\n      </article>\n    );\n  };\n\n'''
    text = sub_once(
        text,
        r'''  const renderJobCompensationPanel = \(\) => \{.*?\n  const renderJobExecutionPanel = \(\) => \{''',
        new_panel + '  const renderJobExecutionPanel = () => {',
        "ProjectWorkspace/simplify compensation panel",
        flags=re.S,
    )

    path.write_text(text, encoding="utf-8")


def patch_reference_workspace() -> None:
    path = ROOT / "frontend/src/features/references/ReferenceWorkspace.tsx"
    text = path.read_text(encoding="utf-8")

    replacement = '''        <article className="panel">\n          <div className="section-heading"><h3>3. Posicionar X0/Y0 del G-code</h3></div>\n          <p className="muted">Habilite el joystick X/Y, coloque la herramienta exactamente sobre el X0/Y0 generado por FlatCAM y confirme esa posición como origen de trabajo.</p>\n          <div className="info-grid info-grid--double compact-grid">\n            <div className="metric-box"><span>X máquina</span><strong>{formatMillimeters(typeof position?.x === "number" ? Number(position.x) : null, 3)}</strong></div>\n            <div className="metric-box"><span>Y máquina</span><strong>{formatMillimeters(typeof position?.y === "number" ? Number(position.y) : null, 3)}</strong></div>\n          </div>\n          <div className="action-grid action-grid--inline">\n            <button className="button button--ghost" type="button" disabled={!canEnableJog || referenceBusy || machine.refreshing} onClick={onEnableManual}>Habilitar joystick X/Y</button>\n            <button className="button" type="button" disabled={!machine.isPhysical || referenceBusy || machine.refreshing || !selectedOperation} onClick={onCapturePhysicalWorkOrigin}>Confirmar X0/Y0</button>\n          </div>\n        </article>\n\n        <article className="panel">\n          <div className="section-heading"><h3>4. Medir referencia Z</h3></div>'''
    text = sub_once(
        text,
        r'''        <article className="panel">\n          <div className="section-heading"><h3>3\. Posicionar X0/Y0 del G-code</h3></div>.*?        <article className="panel">\n          <div className="section-heading"><h3>5\. Medir referencia Z</h3></div>''',
        replacement,
        "ReferenceWorkspace/simplify XY and remove numbered tool-change card",
        flags=re.S,
    )

    closing = '''        </article>\n      </div>\n    );\n  }\n\n  return ('''
    tool_check = '''        </article>\n\n        <article className="panel">\n          <div className="section-heading"><h3>Comprobación de posición de cambio de herramienta</h3></div>\n          <p className="muted">Esta comprobación no forma parte del proceso de referencia. Solo verifica la posición que usará la CNC cuando el trabajo requiera un cambio de herramienta.</p>\n          <div className="info-grid info-grid--double compact-grid">\n            <div className="metric-box"><span>X cambio</span><strong>{formatMillimeters(toolChangeX, 3)}</strong></div>\n            <div className="metric-box"><span>Y cambio</span><strong>{formatMillimeters(toolChangeY, 3)}</strong></div>\n            <div className="metric-box"><span>Z cambio</span><strong>{formatMillimeters(toolChangeZ, 3)}</strong></div>\n            <div className="metric-box"><span>Secuencia</span><strong>Z segura → X/Y</strong></div>\n          </div>\n          <button className="button" type="button" disabled={!machine.isPhysical || referenceBusy || machine.refreshing || !machine.homedAxes} onClick={onToolChangePosition}>Probar posición de cambio</button>\n        </article>\n      </div>\n    );\n  }\n\n  return ('''
    text = replace_once(text, closing, tool_check, "ReferenceWorkspace/append tool-change verification")

    path.write_text(text, encoding="utf-8")


def patch_execution_console() -> None:
    path = ROOT / "frontend/src/features/execution/ExecutionConsole.tsx"
    text = path.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '  archivos_compensados: "Archivos compensados",\n',
        '  archivos_compensados: "Archivos compensados",\n  compensacion_jit: "Compensación Legacy JIT",\n',
        "ExecutionConsole/JIT check label",
    )
    path.write_text(text, encoding="utf-8")


def main() -> None:
    verify_baseline()
    patch_job_service()
    patch_project_workspace()
    patch_reference_workspace()
    patch_execution_console()
    print("Hotfix aplicado en el working tree. No se hizo commit, push, merge, restart ni movimiento físico.")
    print("Siguiente paso: revisar git diff --check y ejecutar pruebas seguras en modo simulated.")


if __name__ == "__main__":
    main()
