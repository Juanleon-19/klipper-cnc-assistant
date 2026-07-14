import type { AnalysisIssue, Operation, OperationAnalysis, Project } from "../types";

export type UiTone = "neutral" | "success" | "warning" | "danger" | "info";

const DIRECT_LABELS: Record<string, string> = {
  "simulada lista para preparacion": "Lista para preparación simulada",
  "sin configurar": "Sin configurar",
  "esperando archivo": "Esperando archivo",
  "lista para analizar": "Esperando análisis",
  "pendiente de analisis": "Pendiente de análisis",
  valida: "Válida",
  valido: "Válido",
  "con advertencias": "Con advertencias",
  "bloqueada por errores": "Bloqueada por errores",
  "bloqueado por errores": "Bloqueado por errores",
  aislamiento: "Fresado",
  taladrado: "Perforado",
  "corte exterior": "Corte del contorno",
  superior: "Cara superior",
  inferior: "Cara inferior",
  absolute: "Absoluto",
  relative: "Incremental",
  informacion: "Información",
  advertencia: "Advertencia",
  "error critico": "Error crítico",
  desplazamiento_rapido: "Desplazamiento rápido",
  movimiento_lineal: "Movimiento lineal",
  arco_horario: "Arco horario",
  arco_antihorario: "Arco antihorario",
  minimo: "Mínimo",
  maximo: "Máximo",
  material: "Material",
  toolpath: "Trayectoria",
  all: "Todo",
  bruto: "Altura bruta",
  plano: "Plano estimado",
  residuo: "Residuo local",
  simulado: "Simulado",
};

function normalizeToken(value: string): string {
  return value.trim().toLowerCase().replace(/[_-]+/g, " ").replace(/\s+/g, " ");
}

function capitalizeSentence(value: string): string {
  if (!value) {
    return value;
  }
  return value.charAt(0).toUpperCase() + value.slice(1);
}

export function translateStatus(value: string | null | undefined): string {
  if (!value) {
    return "Sin datos";
  }
  const normalized = normalizeToken(value);
  return DIRECT_LABELS[normalized] ?? capitalizeSentence(normalized);
}

export function translateIssueSeverity(value: string): string {
  return translateStatus(value);
}

export function toneForStatus(value: string | null | undefined): UiTone {
  const normalized = normalizeToken(value ?? "");
  if (["valido", "valida", "ok", "disponible"].includes(normalized)) {
    return "success";
  }
  if (["con advertencias", "pendiente de analisis", "lista para analizar", "esperando archivo"].includes(normalized)) {
    return "warning";
  }
  if (["bloqueado por errores", "bloqueada por errores", "error critico"].includes(normalized)) {
    return "danger";
  }
  if (["simulado", "operativa", "operativa api"].includes(normalized) || normalized.startsWith("simulada ")) {
    return "info";
  }
  return "neutral";
}

export function translateOperationType(value: string): string {
  return translateStatus(value);
}

export function translateFace(value: string): string {
  return translateStatus(value);
}

export function getRecentProject(projects: Project[]): Project | null {
  return [...projects].sort((left, right) => right.actualizado_en.localeCompare(left.actualizado_en))[0] ?? null;
}

export function countPendingOperations(projects: Project[]): number {
  return projects
    .flatMap((project) => project.operaciones)
    .filter((operation) => ["sin configurar", "esperando archivo", "lista para analizar", "pendiente de analisis"].includes(normalizeToken(operation.estado)))
    .length;
}

export function countWarningOperations(projects: Project[]): number {
  return projects
    .flatMap((project) => project.operaciones)
    .filter((operation) => normalizeToken(operation.estado) === "con advertencias")
    .length;
}

export function countBlockedOperations(projects: Project[]): number {
  return projects
    .flatMap((project) => project.operaciones)
    .filter((operation) => normalizeToken(operation.estado) === "bloqueada por errores")
    .length;
}

export function getOperationWorkflowState(operation: Operation | null | undefined): string {
  if (!operation) {
    return "Sin configurar";
  }
  if (!operation.archivo_gcode) {
    return "Configurada";
  }
  if (!operation.analisis) {
    return "Archivo cargado";
  }
  if (operation.analisis.tiene_errores_criticos) {
    return "Bloqueada por errores";
  }
  if (operation.analisis.incidencias.some((issue) => normalizeToken(issue.severidad) === "advertencia") || operation.analisis.analisis_incompleto) {
    return "Con advertencias";
  }
  return "Preparada para fase posterior";
}

export function buildOperationWorkflow(operation: Operation | null | undefined): Array<{ label: string; complete: boolean; active: boolean }> {
  const hasFile = Boolean(operation?.archivo_gcode);
  const hasAnalysis = Boolean(operation?.analisis);
  const blocked = Boolean(operation?.analisis?.tiene_errores_criticos);
  const warning = Boolean(operation?.analisis && (operation.analisis.analisis_incompleto || operation.analisis.incidencias.some((issue) => normalizeToken(issue.severidad) === "advertencia")));
  const ready = Boolean(hasAnalysis && !blocked);

  return [
    { label: "Configurada", complete: Boolean(operation), active: Boolean(operation) && !hasFile },
    { label: "Archivo cargado", complete: hasFile, active: hasFile && !hasAnalysis },
    { label: "Analizada", complete: hasAnalysis, active: hasAnalysis && !blocked && !warning },
    { label: blocked ? "Bloqueada por errores" : warning ? "Con advertencias" : ready ? "Válida" : "Pendiente", complete: ready || blocked || warning, active: blocked || warning },
    { label: ready ? "Preparada para fase posterior" : "Pendiente de preparación", complete: ready, active: false },
  ];
}

export function splitIssues(analysis: OperationAnalysis | null) {
  const empty = { info: [] as AnalysisIssue[], warnings: [] as AnalysisIssue[], critical: [] as AnalysisIssue[] };
  if (!analysis) {
    return empty;
  }
  return analysis.incidencias.reduce(
    (accumulator, issue) => {
      const severity = normalizeToken(issue.severidad);
      if (severity === "informacion") {
        accumulator.info.push(issue);
      } else if (severity === "error critico") {
        accumulator.critical.push(issue);
      } else {
        accumulator.warnings.push(issue);
      }
      return accumulator;
    },
    empty
  );
}

export function summarizeMachineMode(value: string | null | undefined): string {
  const normalized = normalizeToken(value ?? "simulado");
  if (normalized === "simulado") {
    return "Modo simulado";
  }
  return translateStatus(value);
}
