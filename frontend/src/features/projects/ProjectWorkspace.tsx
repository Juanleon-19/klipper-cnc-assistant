import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useMachineStatus } from "../system/MachineContext";
import { HeightMapControlPanel } from "../height-map/HeightMapControlPanel";
import { HeightMapHeatmap } from "../height-map/HeightMapHeatmap";
import { HeightMapSurface3D } from "../height-map/HeightMapSurface3D";
import { ToolpathViewer } from "../viewer/ToolpathViewer";
import { formatDate, formatFileSize, formatMillimeters, formatNumber } from "../../lib/format";
import { ApiError, api, type OperationInput, type OperationUpdateInput } from "../../lib/api";
import { parseFiniteNumber } from "../../lib/numbers";
import { toneForStatus, translateFace, translateOperationType, translateStatus } from "../../lib/ui";
import type {
  HeightMap,
  CompensationAudit,
  Operation,
  Project,
  ProjectPayload,
  ReferenceSession,
  PhysicalMapPayload,
  MeshSuggestion,
  ReferenceStep,
  CapturedPosition,
  CoordinateReference,
  PhysicalMapExclusion,
  ProbeProfileSource,
  PhysicalMeshPoint,
  Bounds,
  OperationAnalysis,
  JobPlan,
  LiveExecutionSnapshot,
} from "../../types";
import { ProjectForm } from "./ProjectForm";
import { StatusBadge } from "../../components/StatusBadge";
import { ExecutionConsole } from "../execution/ExecutionConsole";
import { ReferenceWorkspace } from "../references/ReferenceWorkspace";

type ProjectWorkspaceProps = {
  project: Project | null;
  busyKey: string | null;
  savingProject: boolean;
  onSaveProject: (payload: ProjectPayload) => Promise<void>;
  onAddSetup: (nombre: string) => Promise<void>;
  onAddOperation: (payload: OperationInput) => Promise<void>;
  onUpdateOperation: (operationId: string, payload: OperationUpdateInput) => Promise<void>;
  onDuplicateOperation: (operationId: string) => Promise<void>;
  onMoveOperation: (operationId: string, direction: "up" | "down") => Promise<void>;
  onDeleteOperation: (operation: Operation) => Promise<void>;
  onRemoveFile: (operation: Operation) => Promise<void>;
  onAnalyze: (operation: Operation) => Promise<void>;
  onUploadFile: (operation: Operation, file: File) => Promise<void>;
  onRefreshProject?: () => Promise<void>;
  onProjectStateChange?: (project: Project) => void;
  initialView?: WorkspaceView;
};

export type WorkspaceView = "archivo" | "trayectoria" | "referencia" | "mapa" | "ejecucion";
type MapTab = "mapa2d" | "superficie3d" | "puntos" | "configuracion";
type HeightMode = "bruto" | "plano" | "residuo";
type HeightMapSource = "SIMULATED" | "MEASURED";

type ReferenceFieldErrors = Partial<Record<"x_mm" | "y_mm" | "z_mm", string>>;
type InputState = { x_mm: string; y_mm: string };
type ZInputState = { x_mm: string; y_mm: string; z_mm: string };

type CompensationAuditRequester = (
  projectId: string,
  operationId: string,
  options?: { signal?: AbortSignal },
) => Promise<CompensationAudit>;


const operationTypeOptions = [
  { value: "fresado_superior", label: "Fresado superior" },
  { value: "fresado_inferior", label: "Fresado inferior" },
  { value: "taladrado", label: "Taladrado" },
  { value: "contorno", label: "Contorno" },
  { value: "personalizado", label: "Personalizado" },
];

function pickDefaultOperation(project: Project | null): string | null {
  if (!project || project.operaciones.length === 0) {
    return null;
  }
  return project.operaciones.find((operation) => Boolean(operation.analisis))?.id ?? project.operaciones[0].id;
}

function workspaceViewStorageKey(projectId: string, operationId: string) {
  return `kca:workspace-view:${projectId}:${operationId}`;
}

function nextFieldError(message: string, fallback: "x_mm" | "y_mm" | "z_mm"): ReferenceFieldErrors {
  const lower = message.toLowerCase();
  const key = lower.includes("y_mm") || lower.includes(" y ") ? "y_mm" : lower.includes("z_mm") || lower.includes(" z ") ? "z_mm" : lower.includes("x_mm") || lower.includes(" x ") ? "x_mm" : fallback;
  return { [key]: message };
}

function referenceValue(record: CoordinateReference | null, key: "x_mm" | "y_mm" | "z_mm") {
  const value = record?.[key];
  return typeof value === "number" ? String(value) : "";
}

function formatCapturedPosition(position: CapturedPosition | null | undefined) {
  if (!position) {
    return "sin captura";
  }
  const z = typeof position.z_mm === "number" ? ` · Z ${formatMillimeters(position.z_mm, 3)}` : "";
  return `X ${formatMillimeters(position.x_mm, 3)} · Y ${formatMillimeters(position.y_mm, 3)}${z}`;
}

function combineOperationBounds(operations: Operation[]): Bounds | null {
  const bounds = operations.map((operation) => operation.analisis?.limites).filter((item): item is Bounds => Boolean(item));
  if (bounds.length === 0) return null;
  const min_x_mm = Math.min(...bounds.map((item) => item.min_x_mm));
  const max_x_mm = Math.max(...bounds.map((item) => item.max_x_mm));
  const min_y_mm = Math.min(...bounds.map((item) => item.min_y_mm));
  const max_y_mm = Math.max(...bounds.map((item) => item.max_y_mm));
  const min_z_mm = Math.min(...bounds.map((item) => item.min_z_mm));
  const max_z_mm = Math.max(...bounds.map((item) => item.max_z_mm));
  return { min_x_mm, max_x_mm, min_y_mm, max_y_mm, min_z_mm, max_z_mm, ancho_mm: max_x_mm - min_x_mm, alto_mm: max_y_mm - min_y_mm };
}

function combineOperationAnalyses(base: OperationAnalysis, operations: Operation[]): OperationAnalysis {
  const bounds = combineOperationBounds(operations);
  const analyses = operations.map((operation) => operation.analisis).filter((item): item is OperationAnalysis => Boolean(item));
  return {
    ...base,
    limites: bounds,
    segmentos_vista_previa: analyses.flatMap((analysis) => analysis.segmentos_vista_previa),
    segmentos_lineales: analyses.flatMap((analysis) => analysis.segmentos_lineales),
    cantidad_movimientos: analyses.reduce((total, analysis) => total + analysis.cantidad_movimientos, 0),
    incidencias: analyses.flatMap((analysis) => analysis.incidencias),
    desbordes_material: analyses.flatMap((analysis) => analysis.desbordes_material),
    cabe_en_material: analyses.every((analysis) => analysis.cabe_en_material !== false),
  };
}

const getCompensationAuditRequest =
  typeof (api as { getCompensationAudit?: unknown }).getCompensationAudit === "function"
    ? (api as { getCompensationAudit: CompensationAuditRequester }).getCompensationAudit
    : null;

function isAbortedRequest(error: unknown) {
  return error instanceof DOMException && error.name === "AbortError";
}


function toneForReferenceStep(step: ReferenceStep): "success" | "warning" | "danger" | "info" | "neutral" {
  if (step.estado === "confirmado") {
    return "success";
  }
  if (step.estado === "disponible") {
    return "info";
  }
  if (step.estado === "invalidado") {
    return "warning";
  }
  return "neutral";
}

function isPhysicalMapReady(payload: PhysicalMapPayload | null | undefined): payload is PhysicalMapPayload {
  if (!payload) {
    return false;
  }
  if (payload.source !== "MEASURED") {
    return false;
  }
  if (payload.status === "MESH_COMPLETE" || payload.status === "MAP_READY") {
    return true;
  }
  return payload.map_ready_state === "MAP_READY";
}

function resolveProbeProfileSource(payload: PhysicalMapPayload | null | undefined): ProbeProfileSource {
  const config = payload?.probe_config;
  if (config?.source === "map_override" || config?.probe_step_mm != null || config?.probe_feed_mm_min != null || config?.retract_mm != null) {
    return "map_override";
  }
  return "machine_reference_profile";
}

function describeMeshProbeState(payload: PhysicalMapPayload | null | undefined): string | null {
  const execution = payload?.execution;
  const pointState = typeof execution?.point_state === "string" ? execution.point_state : null;
  if (pointState === "POINT_DESCENT_STARTED" || pointState === "POINT_LOWER_STEP" || pointState === "POINT_CONFIRM_STEP") {
    return "Descendiendo: búsqueda de contacto";
  }
  return typeof execution?.last_event === "string" ? execution.last_event : null;
}

function normalizeComparableNumber(value: number | null | undefined) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return null;
  }
  return Number(value.toFixed(6));
}

function normalizeMeshExclusions(exclusions: PhysicalMapExclusion[]) {
  return [...exclusions]
    .map((exclusion) => ({
      id: exclusion.id,
      name: exclusion.name,
      shape: exclusion.shape,
      enabled: exclusion.enabled,
      x_min_mm: normalizeComparableNumber(exclusion.x_min_mm ?? null),
      x_max_mm: normalizeComparableNumber(exclusion.x_max_mm ?? null),
      y_min_mm: normalizeComparableNumber(exclusion.y_min_mm ?? null),
      y_max_mm: normalizeComparableNumber(exclusion.y_max_mm ?? null),
      center_x_mm: normalizeComparableNumber(exclusion.center_x_mm ?? null),
      center_y_mm: normalizeComparableNumber(exclusion.center_y_mm ?? null),
      radius_mm: normalizeComparableNumber(exclusion.radius_mm ?? null),
    }))
    .sort((left, right) => left.id.localeCompare(right.id));
}

function buildMeshPlanFingerprint(payload: {
  projectId: string;
  operationId: string;
  setupId: string;
  placementRevision: string | null;
  gridMode: "manual" | "suggested";
  rows: number;
  columns: number;
  edgeLeft: number;
  edgeRight: number;
  edgeBottom: number;
  edgeTop: number;
  exclusions: PhysicalMapExclusion[];
  safeZ: number | undefined;
  profileSource: ProbeProfileSource;
  effectiveProbeStep: number;
  effectiveProbeFeed: number;
  effectiveProbeRetract: number;
}) {
  return JSON.stringify({
    project_id: payload.projectId,
    operation_id: payload.operationId,
    setup_id: payload.setupId,
    placement_revision: payload.placementRevision,
    grid_mode: payload.gridMode,
    rows: payload.rows,
    columns: payload.columns,
    edge_margin_left_mm: normalizeComparableNumber(payload.edgeLeft),
    edge_margin_right_mm: normalizeComparableNumber(payload.edgeRight),
    edge_margin_bottom_mm: normalizeComparableNumber(payload.edgeBottom),
    edge_margin_top_mm: normalizeComparableNumber(payload.edgeTop),
    exclusions: normalizeMeshExclusions(payload.exclusions),
    safe_z_mm: normalizeComparableNumber(payload.safeZ ?? null),
    probe_profile_source: payload.profileSource,
    effective_probe_step_mm: normalizeComparableNumber(payload.effectiveProbeStep),
    effective_probe_feed_mm_min: normalizeComparableNumber(payload.effectiveProbeFeed),
    effective_retract_mm: normalizeComparableNumber(payload.effectiveProbeRetract),
  });
}

type MeshFingerprintKey = "mesh_configuration_fingerprint" | "mesh_geometry_fingerprint";

function normalizeComparableInteger(value: unknown) {
  if (typeof value === "number" && Number.isInteger(value)) {
    return value;
  }
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    if (Number.isInteger(parsed)) {
      return parsed;
    }
  }
  return null;
}

function compareComparableValues(left: unknown, right: unknown) {
  return JSON.stringify(left) === JSON.stringify(right);
}

function meshFingerprint(payload: PhysicalMapPayload | null, key: MeshFingerprintKey) {
  const fingerprint = payload?.[key];
  return typeof fingerprint === "string" && fingerprint.length > 0 ? fingerprint : null;
}

function buildCanonicalMeshSnapshot(payload: PhysicalMapPayload | null) {
  if (!payload) {
    return null;
  }
  const meshConfig = payload.mesh_config ?? {};
  const probeConfig = payload.probe_config ?? {};
  const region = payload.local_region ?? payload.probe_region ?? null;
  const operationIds = [...new Set((payload.operation_ids ?? [])
    .filter((item): item is string => typeof item === "string" && item.length > 0))]
    .sort();
  if (operationIds.length === 0 && typeof payload.operation_id === "string" && payload.operation_id.length > 0) {
    operationIds.push(payload.operation_id);
  }
  const nodes = (payload.points ?? [])
    .filter((point) => {
      const row = normalizeComparableInteger(point.row);
      const column = normalizeComparableInteger(point.column);
      return !(point.role === "REFERENCE" && (row === null || column === null || row < 0 || column < 0));
    })
    .map((point) => ({
      row: normalizeComparableInteger(point.row),
      column: normalizeComparableInteger(point.column),
      x_local: normalizeComparableNumber(point.x_local),
      y_local: normalizeComparableNumber(point.y_local),
      x_machine: normalizeComparableNumber(point.x_machine ?? null),
      y_machine: normalizeComparableNumber(point.y_machine ?? null),
      execution_state: point.status === "EXCLUDED" ? "EXCLUDED" : "EXECUTABLE",
    }))
    .sort((left, right) => {
      const leftRow = left.row ?? Number.MAX_SAFE_INTEGER;
      const rightRow = right.row ?? Number.MAX_SAFE_INTEGER;
      if (leftRow !== rightRow) return leftRow - rightRow;
      const leftColumn = left.column ?? Number.MAX_SAFE_INTEGER;
      const rightColumn = right.column ?? Number.MAX_SAFE_INTEGER;
      if (leftColumn !== rightColumn) return leftColumn - rightColumn;
      const leftX = left.x_local ?? Number.MAX_SAFE_INTEGER;
      const rightX = right.x_local ?? Number.MAX_SAFE_INTEGER;
      if (leftX !== rightX) return leftX - rightX;
      const leftY = left.y_local ?? Number.MAX_SAFE_INTEGER;
      const rightY = right.y_local ?? Number.MAX_SAFE_INTEGER;
      if (leftY !== rightY) return leftY - rightY;
      const leftMachineX = left.x_machine ?? Number.MAX_SAFE_INTEGER;
      const rightMachineX = right.x_machine ?? Number.MAX_SAFE_INTEGER;
      if (leftMachineX !== rightMachineX) return leftMachineX - rightMachineX;
      const leftMachineY = left.y_machine ?? Number.MAX_SAFE_INTEGER;
      const rightMachineY = right.y_machine ?? Number.MAX_SAFE_INTEGER;
      return leftMachineY - rightMachineY;
    });

  return {
    setupId: typeof payload.setup_id === "string" ? payload.setup_id : null,
    operationIds,
    placementRevision: payload.placement_revision ?? null,
    rows: normalizeComparableInteger(payload.rows ?? meshConfig.rows ?? null),
    columns: normalizeComparableInteger(payload.columns ?? meshConfig.columns ?? null),
    region: region ? {
      min_x_mm: normalizeComparableNumber(region.min_x_mm),
      min_y_mm: normalizeComparableNumber(region.min_y_mm),
      max_x_mm: normalizeComparableNumber(region.max_x_mm),
      max_y_mm: normalizeComparableNumber(region.max_y_mm),
    } : null,
    grid: {
      rows: normalizeComparableInteger(payload.grid?.rows ?? payload.rows ?? null),
      columns: normalizeComparableInteger(payload.grid?.columns ?? payload.columns ?? null),
      dx_mm: normalizeComparableNumber(payload.grid?.dx_mm ?? payload.dx ?? null),
      dy_mm: normalizeComparableNumber(payload.grid?.dy_mm ?? payload.dy ?? null),
    },
    margins: {
      left_mm: normalizeComparableNumber(Number(meshConfig.edge_margin_left_mm ?? payload.edge_margins?.left_mm ?? null)),
      right_mm: normalizeComparableNumber(Number(meshConfig.edge_margin_right_mm ?? payload.edge_margins?.right_mm ?? null)),
      bottom_mm: normalizeComparableNumber(Number(meshConfig.edge_margin_bottom_mm ?? payload.edge_margins?.bottom_mm ?? null)),
      top_mm: normalizeComparableNumber(Number(meshConfig.edge_margin_top_mm ?? payload.edge_margins?.top_mm ?? null)),
    },
    exclusions: normalizeMeshExclusions(payload.exclusions ?? []),
    profile: {
      source: resolveProbeProfileSource(payload),
      safe_z_mm: normalizeComparableNumber(probeConfig.safe_z_mm ?? null),
      probe_step_mm: normalizeComparableNumber(Number(probeConfig.effective_probe_step_mm ?? probeConfig.probe_step_mm ?? null)),
      probe_feed_mm_min: normalizeComparableNumber(Number(probeConfig.effective_probe_feed_mm_min ?? probeConfig.probe_feed_mm_min ?? null)),
      retract_mm: normalizeComparableNumber(Number(probeConfig.effective_retract_mm ?? probeConfig.retract_mm ?? null)),
      retract_feed_mm_min: normalizeComparableNumber(Number(probeConfig.effective_retract_feed_mm_min ?? null)),
      probe_open_stable_ms: normalizeComparableNumber(Number(probeConfig.effective_probe_open_stable_ms ?? null)),
      settle_tolerance_mm: normalizeComparableNumber(Number(probeConfig.effective_settle_tolerance_mm ?? null)),
    },
    pointCount: nodes.length,
    nodes,
  };
}

function describeMeshDifferences(meshPreview: PhysicalMapPayload | null, persisted: PhysicalMapPayload | null) {
  const preview = buildCanonicalMeshSnapshot(meshPreview);
  const saved = buildCanonicalMeshSnapshot(persisted);
  if (!preview || !saved) {
    return ["malla"];
  }
  const differences: string[] = [];
  const pushDifference = (label: string) => {
    if (!differences.includes(label)) {
      differences.push(label);
    }
  };
  if (!compareComparableValues(preview.setupId, saved.setupId)) pushDifference("montaje");
  if (!compareComparableValues(preview.operationIds, saved.operationIds)) pushDifference("operación");
  if (!compareComparableValues(preview.placementRevision, saved.placementRevision)) pushDifference("revisión de colocación");
  if (!compareComparableValues(preview.rows, saved.rows)) pushDifference("filas");
  if (!compareComparableValues(preview.columns, saved.columns)) pushDifference("columnas");
  if (!compareComparableValues(preview.region, saved.region)) pushDifference("región");
  if (!compareComparableValues(preview.grid, saved.grid)) pushDifference("grid");
  if (!compareComparableValues(preview.margins, saved.margins)) pushDifference("márgenes");
  if (!compareComparableValues(preview.exclusions, saved.exclusions)) pushDifference("exclusiones");
  if (!compareComparableValues(preview.profile, saved.profile)) pushDifference("perfil");
  if (!compareComparableValues(preview.pointCount, saved.pointCount)) pushDifference("número de nodos");
  if (!compareComparableValues(preview.nodes, saved.nodes)) pushDifference("nodos canónicos");
  return differences;
}

function previewMatchesPersisted(meshPreview: PhysicalMapPayload | null, persisted: PhysicalMapPayload | null) {
  if (!meshPreview || !persisted) {
    return { matches: false, differingFields: ["malla"] };
  }
  const previewConfigurationFingerprint = meshFingerprint(meshPreview, "mesh_configuration_fingerprint");
  const persistedConfigurationFingerprint = meshFingerprint(persisted, "mesh_configuration_fingerprint");
  const previewGeometryFingerprint = meshFingerprint(meshPreview, "mesh_geometry_fingerprint");
  const persistedGeometryFingerprint = meshFingerprint(persisted, "mesh_geometry_fingerprint");
  if (
    previewConfigurationFingerprint
    && persistedConfigurationFingerprint
    && previewGeometryFingerprint
    && persistedGeometryFingerprint
  ) {
    if (
      previewConfigurationFingerprint === persistedConfigurationFingerprint
      && previewGeometryFingerprint === persistedGeometryFingerprint
    ) {
      return { matches: true, differingFields: [] };
    }
  }
  const differingFields = describeMeshDifferences(meshPreview, persisted);
  return { matches: differingFields.length === 0, differingFields };
}

function buildPreviewMismatchMessage(differingFields: string[]) {
  if (differingFields.length === 0) {
    return "La malla persistida ya no coincide con la vista previa mostrada. Regénere la vista previa antes de continuar.";
  }
  return `La malla persistida difiere de la vista previa en: ${differingFields.join(", ")}. Regénere la vista previa antes de continuar.`;
}

function isAbortError(error: unknown) {
  return (
    (error instanceof DOMException && error.name === "AbortError")
    || (error instanceof Error && error.name === "AbortError")
  );
}

function formatDurationSeconds(value: number | null | undefined) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "-";
  }
  const total = Math.max(0, Math.round(value));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  if (hours > 0) {
    return `${hours} h ${minutes.toString().padStart(2, "0")} min`;
  }
  if (minutes > 0) {
    return `${minutes} min ${seconds.toString().padStart(2, "0")} s`;
  }
  return `${seconds} s`;
}

export function ProjectWorkspace({
  project,
  busyKey,
  savingProject,
  onSaveProject,
  onAddSetup,
  onAddOperation,
  onUpdateOperation,
  onDuplicateOperation,
  onMoveOperation,
  onDeleteOperation,
  onRemoveFile,
  onAnalyze,
  onUploadFile,
  onRefreshProject,
  onProjectStateChange,
  initialView,
}: ProjectWorkspaceProps) {
  const [editingProject, setEditingProject] = useState(false);
  const [selectedOperationId, setSelectedOperationId] = useState<string | null>(pickDefaultOperation(project));
  const [selectedSetupId, setSelectedSetupId] = useState<string | null>(project?.current_setup_id ?? project?.montajes[0]?.id ?? null);
  const [newSetupName, setNewSetupName] = useState("");
  const [newOperationName, setNewOperationName] = useState("Fresado superior");
  const [newOperationType, setNewOperationType] = useState("fresado_superior");
  const [newOperationTool, setNewOperationTool] = useState("");
  const [activeView, setActiveView] = useState<WorkspaceView>(initialView ?? "archivo");
  const [activeMapTab, setActiveMapTab] = useState<MapTab>("mapa2d");
  const [heightMode, setHeightMode] = useState<HeightMode>("bruto");
  const [coordinateMode, setCoordinateMode] = useState<"local" | "machine">("local");
  const [mapSource, setMapSource] = useState<HeightMapSource>("SIMULATED");
  const [heightMap, setHeightMap] = useState<HeightMap | null>(null);
  const [meshPreview, setMeshPreview] = useState<PhysicalMapPayload | null>(null);
  const [physicalMap, setPhysicalMap] = useState<PhysicalMapPayload | null>(null);
  const [physicalMapHistory, setPhysicalMapHistory] = useState<Array<Record<string, unknown>>>([]);
  const [referenceSession, setReferenceSession] = useState<ReferenceSession | null>(null);
  const [heightMapBusy, setHeightMapBusy] = useState(false);
  const [previewBusy, setPreviewBusy] = useState(false);
  const [mapActionBusy, setMapActionBusy] = useState(false);
  const [suggestionBusy, setSuggestionBusy] = useState(false);
  const [referenceBusy, setReferenceBusy] = useState(false);
  const [referenceMoveResult, setReferenceMoveResult] = useState<{ reference_x: number; reference_y: number; preparation_z: number; final_state: string; message: string } | null>(null);
  const referenceMoveInFlight = useRef(false);
  const startJobInFlight = useRef(false);
  const [workspaceError, setWorkspaceError] = useState("");
  const [executionError, setExecutionError] = useState<ApiError | null>(null);
  const [workOrigin, setWorkOrigin] = useState<InputState>({ x_mm: "0", y_mm: "0" });
  const [zReference, setZReference] = useState<ZInputState>({ x_mm: "0", y_mm: "0", z_mm: "0" });
  const [useWorkOriginXYForZ, setUseWorkOriginXYForZ] = useState(false);
  const [workOriginErrors, setWorkOriginErrors] = useState<ReferenceFieldErrors>({});
  const [zReferenceErrors, setZReferenceErrors] = useState<ReferenceFieldErrors>({});
  const workOriginRefs = useRef<Record<"x_mm" | "y_mm", HTMLInputElement | null>>({ x_mm: null, y_mm: null });
  const zReferenceRefs = useRef<Record<"x_mm" | "y_mm" | "z_mm", HTMLInputElement | null>>({ x_mm: null, y_mm: null, z_mm: null });
  const machine = useMachineStatus();
  const runtime = machine.runtime;
  const [safeZInput, setSafeZInput] = useState("10");
  const [gridDefinitionMode, setGridDefinitionMode] = useState<"suggested" | "manual">("manual");
  const [meshSuggestion, setMeshSuggestion] = useState<MeshSuggestion | null>(null);
  const [meshRowsInput, setMeshRowsInput] = useState("7");
  const [meshColumnsInput, setMeshColumnsInput] = useState("6");
  const [useUniformEdgeRetreat, setUseUniformEdgeRetreat] = useState(true);
  const [uniformEdgeRetreatInput, setUniformEdgeRetreatInput] = useState("2.0");
  const [edgeRetreatLeftInput, setEdgeRetreatLeftInput] = useState("2.0");
  const [edgeRetreatRightInput, setEdgeRetreatRightInput] = useState("2.0");
  const [edgeRetreatBottomInput, setEdgeRetreatBottomInput] = useState("2.0");
  const [edgeRetreatTopInput, setEdgeRetreatTopInput] = useState("2.0");
  const [meshSpacingInput, setMeshSpacingInput] = useState("10");
  const [probeProfileMode, setProbeProfileMode] = useState<ProbeProfileSource>("machine_reference_profile");
  const [probeStepInput, setProbeStepInput] = useState("0.05");
  const [probeSpeedInput, setProbeSpeedInput] = useState("60");
  const [probeRetractInput, setProbeRetractInput] = useState("1.0");
  const [meshExclusions, setMeshExclusions] = useState<PhysicalMapExclusion[]>([]);
  const [newExclusionShape, setNewExclusionShape] = useState<"rectangle" | "circle">("rectangle");
  const [pointFilter, setPointFilter] = useState<"ALL" | "PENDING" | "MEASURED" | "EXCLUDED" | "FAILED">("ALL");
  const [showAllTrajectoryOperations, setShowAllTrajectoryOperations] = useState(false);
  const [meshValidationMessage, setMeshValidationMessage] = useState("");
  const [meshPreviewFingerprint, setMeshPreviewFingerprint] = useState<string | null>(null);
  const [jobPlan, setJobPlan] = useState<JobPlan | null>(null);
  const [liveExecution, setLiveExecution] = useState<LiveExecutionSnapshot | null>(null);
  const [compensationAudit, setCompensationAudit] = useState<CompensationAudit | null>(null);
  const [compensationAuditBusy, setCompensationAuditBusy] = useState(false);
  const [compensationAuditError, setCompensationAuditError] = useState<string | null>(null);
  const [compensationToleranceInput, setCompensationToleranceInput] = useState("0.05");
  const [machineSettingsInput, setMachineSettingsInput] = useState({
    reference_prep_z_mm: "115",
    reference_prep_z_feed_mm_min: "180",
    move_total_timeout_s: "180",
    no_progress_timeout_s: "60",
    position_tolerance_mm: "0.05",
    velocity_tolerance_mm_s: "0.02",
    reference_probe_step_mm: "0.05",
    reference_probe_feed_mm_min: "60",
    reference_probe_retract_mm: "1.0",
    reference_probe_retract_feed_mm_min: "60",
  });
  const [machineSettingsMessage, setMachineSettingsMessage] = useState("");
  const physicalMapPollInFlight = useRef(false);
  const meshPreviewAbortRef = useRef<AbortController | null>(null);
  const meshPreviewRequestIdRef = useRef(0);
  const compensationAuditAbortRef = useRef<AbortController | null>(null);
  const compensationAuditRequestIdRef = useRef(0);
  const hydratedPhysicalMapIdRef = useRef<string | null>(null);
  const physicalMapWorkerActive = physicalMap?.execution?.worker_active === true;
  const physicalMapWorkerGeneration = physicalMap?.execution?.worker_generation ?? null;
  const physicalMapReadyId = machine.isPhysical && isPhysicalMapReady(physicalMap) ? physicalMap.map_id : null;
  const projectId = project?.id ?? null;

  useEffect(() => {
    if (!machine.isPhysical) {
      return;
    }
    void api.getMachineSettings().then((settings) => {
      const nextReferenceProbeStep = String(settings.reference_probe_step_mm ?? 0.05);
      const nextReferenceProbeFeed = String(settings.reference_probe_feed_mm_min ?? 60);
      const nextReferenceProbeRetract = String(settings.reference_probe_retract_mm ?? 1.0);
      setMachineSettingsInput({
        reference_prep_z_mm: String(settings.reference_prep_z_mm ?? 115),
        reference_prep_z_feed_mm_min: String(settings.reference_prep_z_feed_mm_min ?? 180),
        move_total_timeout_s: String(settings.move_total_timeout_s ?? 180),
        no_progress_timeout_s: String(settings.no_progress_timeout_s ?? 60),
        position_tolerance_mm: String(settings.position_tolerance_mm ?? 0.05),
        velocity_tolerance_mm_s: String(settings.velocity_tolerance_mm_s ?? 0.02),
        reference_probe_step_mm: nextReferenceProbeStep,
        reference_probe_feed_mm_min: nextReferenceProbeFeed,
        reference_probe_retract_mm: nextReferenceProbeRetract,
        reference_probe_retract_feed_mm_min: String(settings.reference_probe_retract_feed_mm_min ?? 60),
      });
    }).catch(() => {
      setMachineSettingsMessage("No se pudo leer la configuración avanzada de máquina.");
    });
  }, [machine.isPhysical]);

  useEffect(() => {
    setSelectedOperationId((current) => {
  if (!project) {
        return null;
      }
      if (current && project.operaciones.some((operation) => operation.id === current)) {
        return current;
      }
      return pickDefaultOperation(project);
    });
  }, [project]);

  const selectedOperation = useMemo(
    () => project?.operaciones.find((operation) => operation.id === selectedOperationId) ?? null,
    [project, selectedOperationId]
  );
  const activeOperationId = selectedOperation?.id ?? null;

  const selectedSetup = useMemo(
    () => project?.montajes.find((setup) => setup.id === selectedSetupId) ?? project?.montajes[0] ?? null,
    [project, selectedSetupId]
  );

  const processOperations = useMemo(
    () => {
      if (!project || !selectedSetup) {
        return [] as Operation[];
      }
      return [...project.operaciones]
        .filter((operation) => operation.setup_id === selectedSetup.id && (!selectedOperation || operation.cara === selectedOperation.cara))
        .sort((left, right) => left.orden - right.orden);
    },
    [project, selectedOperation, selectedSetup]
  );

  const activeJobFace = useMemo(() => selectedOperation?.cara ?? processOperations[0]?.cara ?? null, [processOperations, selectedOperation]);

  useEffect(() => {
    if (!project) {
      setSelectedSetupId(null);
      return;
    }
    if (selectedOperation) {
      setSelectedSetupId(selectedOperation.setup_id);
      return;
    }
    setSelectedSetupId((current) => project.montajes.some((setup) => setup.id === current) ? current : project.current_setup_id ?? project.montajes[0]?.id ?? null);
  }, [project, selectedOperation]);

  useEffect(() => {
    if (!project || !selectedSetupId || project.current_setup_id === selectedSetupId || !onProjectStateChange) {
      return;
    }
    onProjectStateChange({ ...project, current_setup_id: selectedSetupId });
  }, [onProjectStateChange, project, selectedSetupId]);

  useEffect(() => {
    if (!project || !selectedOperation) {
      setHeightMap(null);
      setMeshPreview(null);
      setPhysicalMap(null);
      setMeshPreviewFingerprint(null);
      hydratedPhysicalMapIdRef.current = null;
      setReferenceSession(null);
      return;
    }

    const run = async () => {
      setWorkspaceError("");
      try {
        const [referencePayload, maybePhysicalMap, history] = await Promise.all([
          api.getReferenceSession(project.id, selectedOperation.id),
          api.getPhysicalMap(project.id, selectedOperation.id).then((result) => result.payload).catch((error) => {
            if (error instanceof Error && error.message.toLowerCase().includes("no existe")) {
              return null;
            }
            return null;
          }),
          api.getPhysicalMapHistory(project.id, selectedOperation.id).catch(() => []),
        ]);
        let maybeMap: HeightMap | null = null;
        if (machine.isPhysical) {
          if (isPhysicalMapReady(maybePhysicalMap)) {
            maybeMap = await api.getPhysicalHeightMap(project.id, selectedOperation.id).catch(() => null);
          }
        } else {
          maybeMap = await api.getHeightMap(project.id, selectedOperation.id).catch((error) => {
            if (error instanceof Error && error.message.toLowerCase().includes("no existe")) {
              return null;
            }
            throw error;
          });
        }
        setReferenceSession(referencePayload);
        setPhysicalMap(maybePhysicalMap);
        setPhysicalMapHistory(history);
        setHeightMap(maybeMap);
        if (machine.isPhysical && isPhysicalMapReady(maybePhysicalMap)) {
          setMapSource("MEASURED");
        }
      } catch (error) {
        setWorkspaceError(error instanceof Error ? error.message : "No fue posible cargar el espacio de trabajo técnico.");
      }
    };

    void run();
  }, [machine.isPhysical, project, selectedOperation]);

  useEffect(() => {
    if (!project || !selectedOperation || !physicalMap?.map_id) {
      return;
    }
    const shouldPoll = physicalMap.status === "MESH_PROBING" || physicalMapWorkerActive;
    if (!shouldPoll) {
      return;
    }
    let cancelled = false;
    const poll = async () => {
      if (physicalMapPollInFlight.current) {
        return;
      }
      physicalMapPollInFlight.current = true;
      try {
        const nextMap = (await api.getPhysicalMap(project.id, selectedOperation.id)).payload;
        if (cancelled) {
          return;
        }
        setPhysicalMap(nextMap);
        setMapSource("MEASURED");
        if (isPhysicalMapReady(nextMap)) {
          const [measured, refreshedReference] = await Promise.all([
            api.getPhysicalHeightMap(project.id, selectedOperation.id),
            api.getReferenceSession(project.id, selectedOperation.id),
          ]);
          if (cancelled) {
            return;
          }
          setHeightMap(measured);
          setReferenceSession(refreshedReference);
          setActiveMapTab("mapa2d");
          setMeshValidationMessage("Malla completada. Cobertura validada automáticamente; la compensación ya puede generarse si no existen otros bloqueos.");
          void api.getPhysicalMapHistory(project.id, selectedOperation.id).then((history) => {
            if (!cancelled) {
              setPhysicalMapHistory(history);
            }
          }).catch(() => undefined);
        }
      } catch {
        // Keep the last visible state and try again on the next tick.
      } finally {
        physicalMapPollInFlight.current = false;
      }
    };
    const timer = window.setInterval(() => {
      void poll();
    }, 1500);
    void poll();
    return () => {
      cancelled = true;
      window.clearInterval(timer);
      physicalMapPollInFlight.current = false;
    };
  }, [project, selectedOperation, physicalMap?.map_id, physicalMap?.status, physicalMapWorkerActive, physicalMapWorkerGeneration]);

  useEffect(() => {
    if (!project || !selectedOperation || !machine.isPhysical || !physicalMapReadyId || heightMap) {
      return;
    }
    let cancelled = false;
    void api.getPhysicalHeightMap(project.id, selectedOperation.id).then((measured) => {
      if (!cancelled) {
        setHeightMap(measured);
      }
    }).catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [heightMap, machine.isPhysical, physicalMapReadyId, project, selectedOperation]);


  useEffect(() => {
    if (!project || !selectedSetup || !activeJobFace) {
      setJobPlan(null);
      setLiveExecution(null);
      return;
    }
    let cancelled = false;
    const loadJobState = async () => {
      try {
        const [plan, live] = await Promise.all([
          api.getJobPlan(project.id, selectedSetup.id, activeJobFace),
          api.getLiveExecution(project.id, selectedSetup.id, activeJobFace),
        ]);
        if (cancelled) {
          return;
        }
        setJobPlan(plan);
        setLiveExecution(live);
        setExecutionError(null);
      } catch {
        if (!cancelled) {
          setJobPlan(null);
          setLiveExecution(null);
        }
      }
    };
    void loadJobState();
    return () => {
      cancelled = true;
    };
  }, [activeJobFace, project, selectedSetup]);

  useEffect(() => {
    if (!project || !selectedSetup || !activeJobFace) {
      return;
    }
    let cancelled = false;
    const poll = async () => {
      try {
        const live = await api.getLiveExecution(project.id, selectedSetup.id, activeJobFace);
        if (cancelled) {
          return;
        }
        setLiveExecution(live);
      } catch {
        // conserve last visible state
      }
    };
    const timer = window.setInterval(() => {
      void poll();
    }, 1000);
    void poll();
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [activeJobFace, project, selectedSetup]);

  useEffect(() => {
    if (!referenceSession) {
      return;
    }
    const nextWorkOrigin = {
      x_mm: referenceValue(referenceSession.origen_trabajo, "x_mm") || "0",
      y_mm: referenceValue(referenceSession.origen_trabajo, "y_mm") || "0",
    };
    const nextZReference = {
      x_mm: referenceValue(referenceSession.referencia_z, "x_mm") || nextWorkOrigin.x_mm,
      y_mm: referenceValue(referenceSession.referencia_z, "y_mm") || nextWorkOrigin.y_mm,
      z_mm: referenceValue(referenceSession.referencia_z, "z_mm") || "0",
    };
    setWorkOrigin(nextWorkOrigin);
    setZReference(nextZReference);
    setUseWorkOriginXYForZ(
      nextZReference.x_mm === nextWorkOrigin.x_mm && nextZReference.y_mm === nextWorkOrigin.y_mm
    );
  }, [referenceSession]);

  useEffect(() => {
    setCompensationToleranceInput(selectedOperation ? String(selectedOperation.max_z_error_mm ?? 0.05) : "0.05");
  }, [selectedOperation]);

  const requestCompensationAudit = useCallback(async (nextProjectId: string, nextOperationId: string) => {
    if (getCompensationAuditRequest == null) {
      setCompensationAudit(null);
      setCompensationAuditError("La auditoría comparativa no está disponible en este build.");
      setCompensationAuditBusy(false);
      return;
    }
    compensationAuditAbortRef.current?.abort();
    const controller = new AbortController();
    compensationAuditAbortRef.current = controller;
    const requestId = compensationAuditRequestIdRef.current + 1;
    compensationAuditRequestIdRef.current = requestId;
    setCompensationAuditBusy(true);
    setCompensationAuditError(null);
    try {
      const audit = await getCompensationAuditRequest(nextProjectId, nextOperationId, { signal: controller.signal });
      if (compensationAuditRequestIdRef.current !== requestId || controller.signal.aborted) {
        return;
      }
      setCompensationAudit(audit);
      setCompensationAuditError(null);
    } catch (error) {
      if (compensationAuditRequestIdRef.current !== requestId || controller.signal.aborted || isAbortedRequest(error)) {
        return;
      }
      setCompensationAudit(null);
      setCompensationAuditError(error instanceof ApiError ? error.message : "La auditoría comparativa no está disponible para esta operación.");
    } finally {
      if (compensationAuditRequestIdRef.current === requestId && !controller.signal.aborted) {
        setCompensationAuditBusy(false);
      }
      if (compensationAuditAbortRef.current === controller) {
        compensationAuditAbortRef.current = null;
      }
    }
  }, []);

  const refreshCompensationAudit = async () => {
    if (!projectId || !activeOperationId) {
      setCompensationAudit(null);
      setCompensationAuditError(null);
      setCompensationAuditBusy(false);
      return;
    }
    await requestCompensationAudit(projectId, activeOperationId);
  };

  useEffect(() => {
    compensationAuditAbortRef.current?.abort();
    compensationAuditAbortRef.current = null;
    compensationAuditRequestIdRef.current += 1;
    if (!projectId || !activeOperationId || getCompensationAuditRequest == null) {
      setCompensationAudit(null);
      setCompensationAuditError(null);
      setCompensationAuditBusy(false);
      return;
    }
    setCompensationAudit(null);
    setCompensationAuditError(null);
    void requestCompensationAudit(projectId, activeOperationId);
    return () => {
      compensationAuditAbortRef.current?.abort();
      compensationAuditAbortRef.current = null;
      compensationAuditRequestIdRef.current += 1;
    };
  }, [activeOperationId, projectId, requestCompensationAudit]);

  useEffect(() => {
    const currentMapId = typeof physicalMap?.map_id === "string" ? physicalMap.map_id : null;
    if (!physicalMap || !currentMapId || hydratedPhysicalMapIdRef.current === currentMapId) {
      return;
    }
    hydratedPhysicalMapIdRef.current = currentMapId;
    const meshConfig = typeof physicalMap.mesh_config === "object" && physicalMap.mesh_config ? physicalMap.mesh_config : null;
    const probeConfig = typeof physicalMap.probe_config === "object" && physicalMap.probe_config ? physicalMap.probe_config : null;
    const nextProbeProfileMode = resolveProbeProfileSource(physicalMap);
    const nextRows = Number(meshConfig?.rows ?? physicalMap.rows);
    const nextColumns = Number(meshConfig?.columns ?? physicalMap.columns);
    const nextGridMode = meshConfig?.grid_mode ?? physicalMap.grid_mode;
    setProbeProfileMode(nextProbeProfileMode);
    if (nextGridMode === "manual" || nextGridMode === "suggested") {
      setGridDefinitionMode(nextGridMode);
    }
    if (Number.isFinite(nextRows) && nextRows >= 2) {
      setMeshRowsInput(String(nextRows));
    }
    if (Number.isFinite(nextColumns) && nextColumns >= 2) {
      setMeshColumnsInput(String(nextColumns));
    }
    const left = Number(meshConfig?.edge_margin_left_mm ?? physicalMap.edge_margins?.left_mm);
    const right = Number(meshConfig?.edge_margin_right_mm ?? physicalMap.edge_margins?.right_mm);
    const bottom = Number(meshConfig?.edge_margin_bottom_mm ?? physicalMap.edge_margins?.bottom_mm);
    const top = Number(meshConfig?.edge_margin_top_mm ?? physicalMap.edge_margins?.top_mm);
    if ([left, right, bottom, top].every((value) => Number.isFinite(value) && value >= 0)) {
      setUniformEdgeRetreatInput(String(left));
      setEdgeRetreatLeftInput(String(left));
      setEdgeRetreatRightInput(String(right));
      setEdgeRetreatBottomInput(String(bottom));
      setEdgeRetreatTopInput(String(top));
      setUseUniformEdgeRetreat(left === right && left === bottom && left === top);
    }
    const nextSpacing = Number(meshConfig?.max_spacing_mm);
    if (Number.isFinite(nextSpacing) && nextSpacing > 0) {
      setMeshSpacingInput(String(nextSpacing));
    }
    const nextSafeZ = Number(probeConfig?.safe_z_mm);
    const nextProbeStep = Number((nextProbeProfileMode === "map_override" ? probeConfig?.probe_step_mm : probeConfig?.effective_probe_step_mm) ?? probeConfig?.probe_step_mm);
    const nextProbeFeed = Number((nextProbeProfileMode === "map_override" ? probeConfig?.probe_feed_mm_min : probeConfig?.effective_probe_feed_mm_min) ?? probeConfig?.probe_feed_mm_min);
    const nextRetract = Number((nextProbeProfileMode === "map_override" ? probeConfig?.retract_mm : probeConfig?.effective_retract_mm) ?? probeConfig?.retract_mm);
    if (Number.isFinite(nextSafeZ) && nextSafeZ > 0) {
      setSafeZInput(String(nextSafeZ));
    }
    if (Number.isFinite(nextProbeStep) && nextProbeStep > 0) {
      setProbeStepInput(String(nextProbeStep));
    }
    if (Number.isFinite(nextProbeFeed) && nextProbeFeed > 0) {
      setProbeSpeedInput(String(nextProbeFeed));
    }
    if (Number.isFinite(nextRetract) && nextRetract > 0) {
      setProbeRetractInput(String(nextRetract));
    }
    if (Array.isArray(physicalMap.exclusions)) {
      setMeshExclusions(physicalMap.exclusions);
    }
  }, [physicalMap]);

  useEffect(() => {
    if (!project || !selectedOperation) {
      return;
    }
    if (initialView) {
      setActiveView(initialView);
      return;
    }
    const stored = window.localStorage.getItem(workspaceViewStorageKey(project.id, selectedOperation.id));
    if (stored === "validacion") {
      setActiveView("ejecucion");
      return;
    }
    if (stored === "archivo" || stored === "trayectoria" || stored === "referencia" || stored === "mapa" || stored === "ejecucion") {
      setActiveView(stored);
      return;
    }
  }, [initialView, project, selectedOperation]);

  useEffect(() => {
    if (!project || !selectedOperation) {
      return;
    }
    window.localStorage.setItem(workspaceViewStorageKey(project.id, selectedOperation.id), activeView);
  }, [activeView, project, selectedOperation]);

  useEffect(() => {
    if (useWorkOriginXYForZ) {
      setZReference((current) => ({ ...current, x_mm: workOrigin.x_mm, y_mm: workOrigin.y_mm }));
    }
  }, [useWorkOriginXYForZ, workOrigin]);

  useEffect(() => {
    if (machine.isPhysical) {
      setMapSource("MEASURED");
    }
  }, [machine.isPhysical]);

  const payload = useMemo<ProjectPayload | null>(() => project ? ({
    nombre: project.nombre,
    material: project.material,
    doble_cara: project.doble_cara,
    eje_volteo: project.eje_volteo,
    agujeros_alineacion: project.agujeros_alineacion,
  }) : null, [project]);

  if (!project) {
    return (
      <div className="panel empty-state">
        <p className="eyebrow">Espacio de trabajo</p>
        <h2>Seleccione un proyecto</h2>
        <p>Abra un proyecto existente o cree uno nuevo para gestionar operaciones, referencias simuladas, mapa de alturas y validación.</p>
      </div>
    );
  }

  const analysisBusy = selectedOperation ? busyKey === `analyze:${selectedOperation.id}` : false;
  const fileBusy = selectedOperation ? busyKey === `file:${selectedOperation.id}` : false;

  const focusWorkOriginField = (field: "x_mm" | "y_mm") => {
    workOriginRefs.current[field]?.focus();
  };

  const focusZReferenceField = (field: "x_mm" | "y_mm" | "z_mm") => {
    zReferenceRefs.current[field]?.focus();
  };

  const withHeightMapAction = async (action: () => Promise<HeightMap | void>) => {
    setHeightMapBusy(true);
    setWorkspaceError("");
    try {
      const result = await action();
      if (result) {
        setHeightMap(result);
      }
      if (project && selectedOperation) {
        setReferenceSession(await api.getReferenceSession(project.id, selectedOperation.id));
      }
    } catch (error) {
      setWorkspaceError(error instanceof Error ? error.message : "No fue posible actualizar el mapa de alturas.");
    } finally {
      setHeightMapBusy(false);
    }
  };

  const withReferenceAction = async (action: () => Promise<ReferenceSession>, options?: { onApiFieldError?: (error: ApiError) => void }) => {
    setReferenceBusy(true);
    setWorkspaceError("");
    try {
      const nextSession = await action();
      setReferenceSession(nextSession);
    } catch (error) {
      if (error instanceof ApiError && options?.onApiFieldError) {
        options.onApiFieldError(error);
      }
      setWorkspaceError(error instanceof Error ? error.message : "No fue posible actualizar la referencia simulada.");
    } finally {
      setReferenceBusy(false);
    }
  };


  const withPhysicalReferenceAction = async (action: () => Promise<ReferenceSession | void>) => {
    setReferenceBusy(true);
    setWorkspaceError("");
    try {
      const result = await action();
      if (result) {
        setReferenceSession(result);
      } else if (project && selectedOperation) {
        setReferenceSession(await api.getReferenceSession(project.id, selectedOperation.id));
      }
    } catch (error) {
      setWorkspaceError(error instanceof Error ? error.message : "No fue posible completar la acción física de referencia.");
    } finally {
      setReferenceBusy(false);
    }
  };

  const saveMachineSettings = async () => {
    const labels: Record<keyof typeof machineSettingsInput, string> = {
      reference_prep_z_mm: "Z de preparación",
      reference_prep_z_feed_mm_min: "Velocidad Z de preparación",
      move_total_timeout_s: "Timeout total",
      no_progress_timeout_s: "Timeout sin progreso",
      position_tolerance_mm: "Tolerancia de posición",
      velocity_tolerance_mm_s: "Tolerancia de velocidad",
      reference_probe_step_mm: "Paso de sonda de referencia",
      reference_probe_feed_mm_min: "Velocidad de sonda de referencia",
      reference_probe_retract_mm: "Retracto de sonda de referencia",
      reference_probe_retract_feed_mm_min: "Velocidad de retracto de sonda",
    };
    const payload: Record<string, number> = {};
    for (const [key, label] of Object.entries(labels)) {
      const parsed = parseFiniteNumber(machineSettingsInput[key as keyof typeof machineSettingsInput]);
      if (parsed.value === null || parsed.value <= 0) {
        setMachineSettingsMessage(`${label} debe ser un número mayor que cero.`);
        return;
      }
      payload[key] = parsed.value;
    }
    setReferenceBusy(true);
    setMachineSettingsMessage("");
    try {
      const settings = await api.updateMachineSettings(payload);
      setMachineSettingsInput({
        reference_prep_z_mm: String(settings.reference_prep_z_mm ?? payload.reference_prep_z_mm),
        reference_prep_z_feed_mm_min: String(settings.reference_prep_z_feed_mm_min ?? payload.reference_prep_z_feed_mm_min),
        move_total_timeout_s: String(settings.move_total_timeout_s ?? payload.move_total_timeout_s),
        no_progress_timeout_s: String(settings.no_progress_timeout_s ?? payload.no_progress_timeout_s),
        position_tolerance_mm: String(settings.position_tolerance_mm ?? payload.position_tolerance_mm),
        velocity_tolerance_mm_s: String(settings.velocity_tolerance_mm_s ?? payload.velocity_tolerance_mm_s),
        reference_probe_step_mm: String(settings.reference_probe_step_mm ?? payload.reference_probe_step_mm),
        reference_probe_feed_mm_min: String(settings.reference_probe_feed_mm_min ?? payload.reference_probe_feed_mm_min),
        reference_probe_retract_mm: String(settings.reference_probe_retract_mm ?? payload.reference_probe_retract_mm),
        reference_probe_retract_feed_mm_min: String(settings.reference_probe_retract_feed_mm_min ?? payload.reference_probe_retract_feed_mm_min),
      });
      setMachineSettingsMessage("Configuración avanzada de máquina guardada.");
      await machine.refreshRuntime();
    } catch (error) {
      setMachineSettingsMessage(error instanceof Error ? error.message : "No se pudo guardar la configuración avanzada de máquina.");
    } finally {
      setReferenceBusy(false);
    }
  };


  const submitWorkOrigin = async () => {
    const xParsed = parseFiniteNumber(workOrigin.x_mm);
    const yParsed = parseFiniteNumber(workOrigin.y_mm);
    const nextErrors: ReferenceFieldErrors = {};
    if (xParsed.error === "empty") {
      nextErrors.x_mm = "Indique X en milímetros.";
    } else if (xParsed.error === "invalid") {
      nextErrors.x_mm = "X debe ser un número válido.";
    }
    if (yParsed.error === "empty") {
      nextErrors.y_mm = "Indique Y en milímetros.";
    } else if (yParsed.error === "invalid") {
      nextErrors.y_mm = "Y debe ser un número válido.";
    }
    setWorkOriginErrors(nextErrors);
    if (nextErrors.x_mm) {
      focusWorkOriginField("x_mm");
      return;
    }
    if (nextErrors.y_mm) {
      focusWorkOriginField("y_mm");
      return;
    }
    await withReferenceAction(
      () => api.confirmWorkOrigin(project.id, selectedOperation!.id, { x_mm: xParsed.value as number, y_mm: yParsed.value as number }),
      {
        onApiFieldError: (error) => {
          const fieldErrors = error.fieldErrors.x_mm
            ? { x_mm: error.fieldErrors.x_mm }
            : error.fieldErrors.y_mm
              ? { y_mm: error.fieldErrors.y_mm }
              : nextFieldError(error.message, "x_mm");
          setWorkOriginErrors(fieldErrors);
          if (fieldErrors.x_mm) {
            focusWorkOriginField("x_mm");
          } else if (fieldErrors.y_mm) {
            focusWorkOriginField("y_mm");
          }
        },
      }
    );
  };

  const submitZReference = async () => {
    const referenceFallbackX = referenceValue(referenceSession?.referencia_z ?? null, "x_mm");
    const referenceFallbackY = referenceValue(referenceSession?.referencia_z ?? null, "y_mm");
    const xSource = useWorkOriginXYForZ
      ? workOrigin.x_mm
      : zReference.x_mm === "0" && referenceFallbackX && referenceFallbackX !== "0"
        ? referenceFallbackX
        : zReference.x_mm;
    const ySource = useWorkOriginXYForZ
      ? workOrigin.y_mm
      : zReference.y_mm === "0" && referenceFallbackY && referenceFallbackY !== "0"
        ? referenceFallbackY
        : zReference.y_mm;
    const xParsed = parseFiniteNumber(xSource);
    const yParsed = parseFiniteNumber(ySource);
    const zParsed = parseFiniteNumber(zReference.z_mm);
    const nextErrors: ReferenceFieldErrors = {};
    if (xParsed.error === "empty") {
      nextErrors.x_mm = "Indique X en milímetros.";
    } else if (xParsed.error === "invalid") {
      nextErrors.x_mm = "X debe ser un número válido.";
    }
    if (yParsed.error === "empty") {
      nextErrors.y_mm = "Indique Y en milímetros.";
    } else if (yParsed.error === "invalid") {
      nextErrors.y_mm = "Y debe ser un número válido.";
    }
    if (zParsed.error === "empty") {
      nextErrors.z_mm = "Indique Z en milímetros.";
    } else if (zParsed.error === "invalid") {
      nextErrors.z_mm = "Z debe ser un número válido.";
    }
    setZReferenceErrors(nextErrors);
    if (nextErrors.x_mm) {
      focusZReferenceField("x_mm");
      return;
    }
    if (nextErrors.y_mm) {
      focusZReferenceField("y_mm");
      return;
    }
    if (nextErrors.z_mm) {
      focusZReferenceField("z_mm");
      return;
    }
    await withReferenceAction(
      () => api.confirmZReference(project.id, selectedOperation!.id, {
        x_mm: xParsed.value as number,
        y_mm: yParsed.value as number,
        z_mm: zParsed.value as number,
      }),
      {
        onApiFieldError: (error) => {
          const fieldErrors = nextFieldError(error.message, "z_mm");
          setZReferenceErrors(fieldErrors);
          if (fieldErrors.x_mm) {
            focusZReferenceField("x_mm");
          } else if (fieldErrors.y_mm) {
            focusZReferenceField("y_mm");
          } else {
            focusZReferenceField("z_mm");
          }
        },
      }
    );
  };

  const continueWorkflow = () => {
    const withoutFile = project.operaciones.find((operation) => !operation.archivo_gcode);
    if (project.operaciones.length === 0 || withoutFile) {
      setSelectedOperationId(withoutFile?.id ?? null);
      setActiveView("archivo");
      return;
    }
    const withoutAnalysis = project.operaciones.find((operation) => !operation.analisis || operation.analisis.analisis_desactualizado);
    if (withoutAnalysis) {
      setSelectedOperationId(withoutAnalysis.id);
      setActiveView("trayectoria");
      return;
    }
    if (!referenceSession?.machine_reference.confirmada || !referenceSession.origen_trabajo || !referenceSession.referencia_z) {
      setActiveView("referencia");
      return;
    }
    if (!heightMap) {
      setActiveView("mapa");
      return;
    }
    setActiveView("ejecucion");
  };

  const workflowStatus = (complete: boolean, started = false) => complete ? "completado" : started ? "en progreso" : "pendiente";

  const renderArchivo = () => (
    <div className="stack gap-md">
      <article className="panel operation-workflow-panel">
        <div className="section-heading section-heading--stacked">
          <div>
            <p className="eyebrow">Proyecto / Montaje / Operaciones</p>
            <h3>Flujo ordenado de procesos</h3>
          </div>
          <p className="muted">Cada operación conserva su propio archivo, análisis, trayectoria, advertencias, herramienta y estado.</p>
        </div>

        <div className="setup-toolbar">
          <label>
            Montaje activo
            <select
              aria-label="Montaje activo"
              value={selectedSetup?.id ?? ""}
              onChange={(event) => {
                const setupId = event.target.value;
                setSelectedSetupId(setupId);
                const firstOperation = project.operaciones
                  .filter((operation) => operation.setup_id === setupId)
                  .sort((a, b) => a.orden - b.orden)[0];
                setSelectedOperationId(firstOperation?.id ?? null);
              }}
            >
              {project.montajes.map((setup) => <option key={setup.id} value={setup.id}>{setup.nombre}</option>)}
            </select>
          </label>
          <form
            className="inline-create-form"
            onSubmit={async (event) => {
              event.preventDefault();
              const name = newSetupName.trim();
              if (!name) {
                return;
              }
              await onAddSetup(name);
              setNewSetupName("");
            }}
          >
            <label>
              Nuevo montaje
              <input value={newSetupName} onChange={(event) => setNewSetupName(event.target.value)} placeholder="Ej. Cara inferior" />
            </label>
            <button className="button button--ghost" type="submit" disabled={!newSetupName.trim() || busyKey === "setup:add"}>Agregar montaje</button>
          </form>
        </div>

        <form
          className="operation-create-form"
          onSubmit={async (event) => {
            event.preventDefault();
            if (!selectedSetup || !newOperationName.trim()) {
              return;
            }
            await onAddOperation({
              setup_id: selectedSetup.id,
              nombre: newOperationName.trim(),
              tipo: newOperationType,
              herramienta: newOperationTool.trim() || null,
            });
          }}
        >
          <label>
            Tipo de operación
            <select
              value={newOperationType}
              onChange={(event) => {
                const type = event.target.value;
                setNewOperationType(type);
                setNewOperationName(operationTypeOptions.find((item) => item.value === type)?.label ?? "Operación");
              }}
            >
              {operationTypeOptions.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
            </select>
          </label>
          <label>
            Nombre
            <input value={newOperationName} onChange={(event) => setNewOperationName(event.target.value)} />
          </label>
          <label>
            Herramienta
            <input value={newOperationTool} onChange={(event) => setNewOperationTool(event.target.value)} placeholder="Ej. Broca 0,8 mm" />
          </label>
          <button className="button" type="submit" disabled={!selectedSetup || !newOperationName.trim() || busyKey === "operation:add"}>Agregar operación</button>
        </form>

        <div className="setup-operation-tree" aria-label="Operaciones por montaje">
          {project.montajes.map((setup) => {
            const operations = project.operaciones
              .filter((operation) => operation.setup_id === setup.id)
              .sort((a, b) => a.orden - b.orden);
            return (
              <section className="setup-group" key={setup.id}>
                <div className="setup-group__header">
                  <strong>{setup.nombre}</strong>
                  <StatusBadge tone={operations.length > 0 ? "info" : "neutral"}>{operations.length} operaciones</StatusBadge>
                </div>
                {operations.length === 0 ? <p className="muted">Este montaje todavía no tiene operaciones.</p> : null}
                {operations.map((operation, index) => (
                  <div className={"operation-row" + (operation.id === selectedOperationId ? " operation-row--active" : "")} key={operation.id}>
                    <button className="operation-row__select" type="button" onClick={() => setSelectedOperationId(operation.id)}>
                      <span className="operation-order">{index + 1}</span>
                      <span><strong>{operation.nombre}</strong><small>{translateOperationType(operation.tipo)} · {operation.nombre_archivo_original ?? "Sin G-code"}</small></span>
                    </button>
                    <StatusBadge tone={toneForStatus(operation.estado)}>{translateStatus(operation.estado)}</StatusBadge>
                    <label className="operation-tool-field">
                      <span className="sr-only">Herramienta de {operation.nombre}</span>
                      <input
                        aria-label={"Herramienta de " + operation.nombre}
                        defaultValue={operation.herramienta ?? ""}
                        placeholder="Herramienta"
                        onBlur={(event) => void onUpdateOperation(operation.id, { nombre: operation.nombre, tool_id: operation.tool_id, herramienta: event.target.value || null })}
                      />
                    </label>
                    <div className="operation-row__actions">
                      <button type="button" className="icon-button" aria-label={"Mover arriba " + operation.nombre} disabled={index === 0} onClick={() => void onMoveOperation(operation.id, "up")}>↑</button>
                      <button type="button" className="icon-button" aria-label={"Mover abajo " + operation.nombre} disabled={index === operations.length - 1} onClick={() => void onMoveOperation(operation.id, "down")}>↓</button>
                      <button type="button" className="button button--ghost" onClick={async () => {
                        const name = window.prompt("Nuevo nombre de la operación", operation.nombre)?.trim();
                        if (name) {
                          await onUpdateOperation(operation.id, { nombre: name, tool_id: operation.tool_id, herramienta: operation.herramienta });
                        }
                      }}>Renombrar</button>
                      <button type="button" className="button button--ghost" onClick={() => void onDuplicateOperation(operation.id)}>Duplicar</button>
                      <button type="button" className="button button--ghost button--danger" onClick={async () => {
                        if (window.confirm("La operación seleccionada se eliminará del proyecto. ¿Desea continuar?")) {
                          await onDeleteOperation(operation);
                        }
                      }}>Eliminar</button>
                    </div>
                  </div>
                ))}
              </section>
            );
          })}
        </div>
      </article>

      <article className="panel operation-detail-panel">
        <div className="section-heading">
          <div><p className="eyebrow">Archivo de la operación activa</p><h3>{selectedOperation?.nombre ?? "Seleccione una operación"}</h3></div>
          {selectedOperation ? <StatusBadge tone={toneForStatus(selectedOperation.estado)}>{translateStatus(selectedOperation.estado)}</StatusBadge> : null}
        </div>
        {selectedOperation ? (
          <>
            <dl className="definition-grid definition-grid--compact">
              <div><dt>Montaje</dt><dd>{project.montajes.find((setup) => setup.id === selectedOperation.setup_id)?.nombre ?? "-"}</dd></div>
              <div><dt>Tipo</dt><dd>{translateOperationType(selectedOperation.tipo)}</dd></div>
              <div><dt>Herramienta</dt><dd>{selectedOperation.herramienta ?? "Sin asignar"}</dd></div>
              <div><dt>Archivo</dt><dd>{selectedOperation.nombre_archivo_original ?? "Sin archivo"}</dd></div>
              <div><dt>Tamaño</dt><dd>{formatFileSize(selectedOperation.tamano_archivo_bytes)}</dd></div>
            </dl>
            {selectedOperation.analisis?.analisis_desactualizado ? <div className="alert alert--warning">Este análisis está desactualizado. Versión actual: {selectedOperation.analisis.current_analysis_version}.</div> : null}
            <div className="action-grid action-grid--inline">
              <label className="button file-button">
                {selectedOperation.archivo_gcode ? "Reemplazar archivo" : "Cargar archivo"}
                <input aria-label={"Cargar archivo para " + selectedOperation.nombre} type="file" accept=".nc,.gcode,.tap" disabled={fileBusy} onChange={async (event) => {
                  const file = event.target.files?.[0];
                  if (file) {
                    await onUploadFile(selectedOperation, file);
                    event.target.value = "";
                  }
                }} />
              </label>
              <button className="button button--ghost" type="button" disabled={!selectedOperation.archivo_gcode || analysisBusy} onClick={() => void onAnalyze(selectedOperation)}>
                {analysisBusy ? "Analizando archivo..." : selectedOperation.analisis?.analisis_desactualizado ? "Volver a analizar" : "Analizar archivo"}
              </button>
              <button className="button button--ghost" type="button" disabled={!selectedOperation.archivo_gcode || fileBusy} onClick={async () => {
                if (window.confirm("Se quitará la asociación del archivo actual. ¿Desea continuar?")) {
                  await onRemoveFile(selectedOperation);
                }
              }}>Eliminar asociación</button>
            </div>
          </>
        ) : <p className="muted">Seleccione una operación para gestionar su archivo y análisis.</p>}
      </article>
    </div>
  );

  const renderTrayectoria = () => {
    const selector = (
      <article className="panel operation-selector-panel">
        <label>
          Operación activa
          <select
            aria-label="Operación activa"
            value={selectedOperationId ?? ""}
            onChange={(event) => setSelectedOperationId(event.target.value || null)}
          >
            <option value="">Seleccione una operación</option>
            {project.montajes.map((setup) => (
              <optgroup key={setup.id} label={setup.nombre}>
                {project.operaciones
                  .filter((operation) => operation.setup_id === setup.id)
                  .sort((a, b) => a.orden - b.orden)
                  .map((operation) => <option key={operation.id} value={operation.id}>{operation.orden + 1}. {operation.nombre}</option>)}
              </optgroup>
            ))}
          </select>
        </label>
        {selectedOperation ? (
          <div className="operation-selector-meta">
            <span><strong>Archivo:</strong> {selectedOperation.nombre_archivo_original ?? "Sin G-code"}</span>
            <span><strong>Herramienta:</strong> {selectedOperation.herramienta ?? "Sin asignar"}</span>
            <span><strong>Estado:</strong> {translateStatus(selectedOperation.estado)}</span>
          </div>
        ) : null}
        <div className="map-segmented" aria-label="Operaciones visibles en visor técnico">
          <button className={`map-segment-button${!showAllTrajectoryOperations ? " map-segment-button--active" : ""}`} type="button" onClick={() => setShowAllTrajectoryOperations(false)}>Operación seleccionada</button>
          <button className={`map-segment-button${showAllTrajectoryOperations ? " map-segment-button--active" : ""}`} type="button" onClick={() => setShowAllTrajectoryOperations(true)}>Todas las operaciones</button>
        </div>
      </article>
    );
    const trajectoryOperations = project.operaciones.filter((operation) => operation.setup_id === selectedSetup?.id && operation.analisis);
    if (!selectedOperation) {
      return <div className="stack gap-md">{selector}<div className="panel empty-state"><p>Seleccione una operación para ver su trayectoria.</p></div></div>;
    }
    if (!selectedOperation.archivo_gcode) {
      return <div className="stack gap-md">{selector}<div className="panel empty-state"><p>Esta operación todavía no tiene G-code.</p></div></div>;
    }
    if (!selectedOperation.analisis) {
      return <div className="stack gap-md">{selector}<div className="panel empty-state"><p>Analice el archivo de esta operación para ver su trayectoria.</p></div></div>;
    }
    const viewerAnalysis = showAllTrajectoryOperations ? combineOperationAnalyses(selectedOperation.analisis, trajectoryOperations) : selectedOperation.analisis;
    return (
      <div className="stack gap-md">
        {selector}
        <article className="panel analysis-summary-panel">
          <div className="section-heading section-heading--stacked">
            <div><p className="eyebrow">Trayectoria exclusiva de {selectedOperation.nombre}</p><h3>Alturas de la trayectoria G-code</h3></div>
            <p className="muted">Estas métricas y advertencias pertenecen únicamente a la operación activa.</p>
          </div>
          <div className="info-grid info-grid--double compact-grid">
            <div className="metric-box"><span>Movimientos</span><strong>{selectedOperation.analisis.cantidad_movimientos}</strong></div>
            <div className="metric-box"><span>Advertencias</span><strong>{selectedOperation.analisis.incidencias.length}</strong></div>
            <div className="metric-box"><span>X</span><strong>{formatMillimeters(selectedOperation.analisis.limites?.ancho_mm, 3)}</strong></div>
            <div className="metric-box"><span>Y</span><strong>{formatMillimeters(selectedOperation.analisis.limites?.alto_mm, 3)}</strong></div>
            <div className="metric-box"><span>Z mínima</span><strong>{formatMillimeters(selectedOperation.analisis.profundidad_min_mm, 3)}</strong></div>
            <div className="metric-box"><span>Z máxima</span><strong>{formatMillimeters(selectedOperation.analisis.profundidad_max_mm, 3)}</strong></div>
          </div>
          {selectedOperation.analisis.incidencias.length > 0 ? (
            <ul className="compact-issue-list">
              {selectedOperation.analisis.incidencias.map((issue, index) => <li key={issue.codigo + index}>{issue.mensaje}</li>)}
            </ul>
          ) : null}
        </article>
        <article className="panel viewer-panel">
          <ToolpathViewer
            material={project.material}
            analysis={viewerAnalysis}
            operationName={showAllTrajectoryOperations ? "Todas las operaciones" : selectedOperation.nombre}
            storageKey={project.id + ":" + selectedOperation.id}
            machineOrigin={referenceSession?.origen_trabajo ? { x_mm: referenceSession.origen_trabajo.x_mm, y_mm: referenceSession.origen_trabajo.y_mm } : null}
          />
        </article>
      </div>
    );
  };

  const renderReferenceStep = (step: ReferenceStep, index: number) => (
    <div className="workflow-step-card" key={step.id}>
      <div className="workflow-step-card__header">
        <span className="workflow-step">{index + 1}</span>
        <div>
          <strong>{step.titulo}</strong>
          <p className="muted">{step.detalle ?? "Pendiente"}</p>
        </div>
        <StatusBadge tone={toneForReferenceStep(step)}>{step.estado}</StatusBadge>
      </div>
      <div className="workflow-step-card__meta mono-text">{step.fecha ? formatDate(step.fecha) : "Pendiente"}</div>
    </div>
  );

  const remeasurePhysicalReference = async () => {
    if (!project || !selectedOperation) return;
    await withPhysicalReferenceAction(async () => {
      await api.confirmProbe();
      await machine.refreshRuntime();
      await api.capturePhysicalWorkOrigin(project.id, selectedOperation.id);
      return await api.capturePhysicalZReferenceFromProbe(project.id, selectedOperation.id);
    });
  };

  const goToReferencePoint = async () => {
    if (!project || !selectedOperation || referenceMoveInFlight.current) return;
    referenceMoveInFlight.current = true;
    setReferenceBusy(true);
    setWorkspaceError("");
    setReferenceMoveResult(null);
    try {
      const result = await api.goToReferencePoint(project.id, selectedOperation.id);
      setReferenceMoveResult(result);
      await machine.refreshRuntime();
    } catch (error) {
      setWorkspaceError(error instanceof Error ? error.message : "No fue posible mover al punto de referencia.");
    } finally {
      referenceMoveInFlight.current = false;
      setReferenceBusy(false);
    }
  };

  const renderReferencia = () => (
    <ReferenceWorkspace
      machine={machine}
      runtime={runtime}
      referenceSession={referenceSession}
      referenceBusy={referenceBusy}
      selectedOperation={selectedOperation}
      heightMap={heightMap}
      machineSettingsInput={machineSettingsInput}
      machineSettingsMessage={machineSettingsMessage}
      referenceMoveResult={referenceMoveResult}
      workOrigin={workOrigin}
      zReference={zReference}
      useWorkOriginXYForZ={useWorkOriginXYForZ}
      workOriginErrors={workOriginErrors}
      zReferenceErrors={zReferenceErrors}
      workOriginRefs={workOriginRefs}
      zReferenceRefs={zReferenceRefs}
      formatCapturedPosition={formatCapturedPosition}
      renderReferenceStep={renderReferenceStep}
      onConnectRuntime={() => void machine.runMachineAction("connect")}
      onDiagnosticMode={() => void machine.runMachineAction("diagnostic")}
      onReconnectArduino={() => void machine.runMachineAction("reconnect-arduino")}
      onSaveMachineSettings={() => void saveMachineSettings()}
      onInitialize={() => void withPhysicalReferenceAction(async () => { await machine.runMachineAction("initialize", Number(machineSettingsInput.reference_prep_z_mm)); })}
      onEnableManual={() => void machine.runMachineAction("manual-on")}
      onCapturePhysicalWorkOrigin={() => void withPhysicalReferenceAction(() => api.capturePhysicalWorkOrigin(project.id, selectedOperation!.id))}
      onCancelOperation={() => void machine.runMachineAction("cancel")}
      onToolChangePosition={() => void withPhysicalReferenceAction(async () => { await machine.runMachineAction("tool-change-position"); })}
      onProbeRequest={() => void machine.runMachineAction("probe-request")}
      onRemeasurePhysicalReference={() => void remeasurePhysicalReference()}
      onGoToReferencePoint={() => void goToReferencePoint()}
      onConfirmMachineReference={() => void withReferenceAction(() => api.confirmMachineReference(project.id, selectedOperation!.id))}
      onSubmitWorkOrigin={() => void submitWorkOrigin()}
      onSubmitZReference={() => void submitZReference()}
      onValidateHeightMap={() => void withReferenceAction(() => api.validateHeightMap(project.id, selectedOperation!.id))}
      onMachineSettingChange={(field, value) => setMachineSettingsInput((current) => ({ ...current, [field]: value }))}
      onToggleUseWorkOriginXYForZ={(checked) => setUseWorkOriginXYForZ(checked)}
      onWorkOriginChange={(field, value) => {
        setWorkOrigin((current) => ({ ...current, [field]: value }));
        setWorkOriginErrors((current) => ({ ...current, [field]: undefined }));
      }}
      onZReferenceChange={(field, value) => {
        setZReference((current) => ({ ...current, [field]: value }));
        setZReferenceErrors((current) => ({ ...current, [field]: undefined }));
      }}
    />
  );

  const refreshPhysicalMapHistory = async () => {
    if (!project || !selectedOperation) {
      return;
    }
    const history = await api.getPhysicalMapHistory(project.id, selectedOperation.id).catch(() => []);
    setPhysicalMapHistory(history);
  };

  const clearMeshPreview = (message: string) => {
    meshPreviewAbortRef.current?.abort();
    meshPreviewAbortRef.current = null;
    meshPreviewRequestIdRef.current += 1;
    setPreviewBusy(false);
    setMeshPreview(null);
    setMeshPreviewFingerprint(null);
    setMeshValidationMessage(message);
  };

  const invalidateMeshPreview = (message?: string) => {
    meshPreviewAbortRef.current?.abort();
    meshPreviewAbortRef.current = null;
    meshPreviewRequestIdRef.current += 1;
    setPreviewBusy(false);
    setMeshPreview(null);
    setMeshPreviewFingerprint(null);
    setMeshSuggestion(null);
    setMeshValidationMessage(message ?? (physicalMap?.points?.some((point) => point.status === "MEASURED")
      ? "Existe una medición parcial. Cambiar la cuadrícula creará una nueva versión de malla. Los puntos medidos anteriores se conservarán en el historial, pero no pertenecerán a la nueva cuadrícula."
      : ""));
  };

  const withPhysicalMapAction = async (action: () => Promise<PhysicalMapPayload | null>, options?: { clearPreview?: boolean }) => {
    setMapActionBusy(true);
    setWorkspaceError("");
    try {
      const result = await action();
      const nextMap = result ?? physicalMap;
      if (result) {
        if (options?.clearPreview) {
          setMeshPreview(null);
          setMeshPreviewFingerprint(null);
        }
        setPhysicalMap(result);
        setMapSource("MEASURED");
      }
      if (project && selectedOperation && isPhysicalMapReady(nextMap)) {
        const [measured, refreshedReference] = await Promise.all([
          api.getPhysicalHeightMap(project.id, selectedOperation.id),
          api.getReferenceSession(project.id, selectedOperation.id),
        ]);
        setHeightMap(measured);
        setReferenceSession(refreshedReference);
        setMapSource("MEASURED");
        setActiveMapTab("mapa2d");
        setMeshValidationMessage("Malla completada. Cobertura validada automáticamente; la compensación ya puede generarse si no existen otros bloqueos.");
      } else if (project && selectedOperation) {
        void api.getReferenceSession(project.id, selectedOperation.id).then(setReferenceSession).catch(() => undefined);
        if (result) {
          setHeightMap(null);
        }
      }
      void refreshPhysicalMapHistory();
    } catch (error) {
      setWorkspaceError(error instanceof Error ? error.message : "No fue posible actualizar el mapa físico medido.");
    } finally {
      setMapActionBusy(false);
    }
  };

  const physicalFailedPoints = physicalMap?.points?.filter((point) => point.status === "FAILED" || point.status === "RETRY_REQUIRED").length ?? 0;
  const physicalMapId = typeof physicalMap?.map_id === "string" ? physicalMap.map_id : "";

  const renderMapa = () => {
    const effectiveMapSource: HeightMapSource = machine.isPhysical ? "MEASURED" : mapSource;
    const physicalReady = machine.isPhysical && Boolean(selectedOperation && referenceSession?.origen_trabajo && referenceSession?.referencia_z);
    const visibleMesh = meshPreview ?? physicalMap;
    const parsePositive = (value: string) => {
      const parsed = Number(value.replace(",", "."));
      return Number.isFinite(parsed) && parsed > 0 ? parsed : undefined;
    };
    const parseNonNegative = (value: string) => {
      const parsed = Number(value.replace(",", "."));
      return Number.isFinite(parsed) && parsed >= 0 ? parsed : undefined;
    };
    const parseInteger = (value: string) => {
      const parsed = Number.parseInt(value, 10);
      return Number.isFinite(parsed) ? parsed : undefined;
    };
    const uniformRetreat = parseNonNegative(uniformEdgeRetreatInput) ?? 2;
    const edgeLeft = useUniformEdgeRetreat ? uniformRetreat : (parseNonNegative(edgeRetreatLeftInput) ?? 2);
    const edgeRight = useUniformEdgeRetreat ? uniformRetreat : (parseNonNegative(edgeRetreatRightInput) ?? 2);
    const edgeBottom = useUniformEdgeRetreat ? uniformRetreat : (parseNonNegative(edgeRetreatBottomInput) ?? 2);
    const edgeTop = useUniformEdgeRetreat ? uniformRetreat : (parseNonNegative(edgeRetreatTopInput) ?? 2);
    const rows = Math.max(2, parseInteger(meshRowsInput) ?? 7);
    const columns = Math.max(2, parseInteger(meshColumnsInput) ?? 6);
    const probeWidth = Math.max(0, project.material.ancho_mm - edgeLeft - edgeRight);
    const probeHeight = Math.max(0, project.material.alto_mm - edgeBottom - edgeTop);
    const plannedPoints = rows * columns;
    const safeZ = parsePositive(safeZInput);
    const configuredProbeStep = parsePositive(probeStepInput);
    const configuredProbeFeed = parsePositive(probeSpeedInput);
    const configuredProbeRetract = parsePositive(probeRetractInput);
    const effectiveProfileSource = probeProfileMode;
    const inheritedProbeStep = parsePositive(machineSettingsInput.reference_probe_step_mm);
    const inheritedProbeFeed = parsePositive(machineSettingsInput.reference_probe_feed_mm_min);
    const inheritedProbeRetract = parsePositive(machineSettingsInput.reference_probe_retract_mm);
    const displayProbeStepInput = effectiveProfileSource === "machine_reference_profile" ? machineSettingsInput.reference_probe_step_mm : probeStepInput;
    const displayProbeFeedInput = effectiveProfileSource === "machine_reference_profile" ? machineSettingsInput.reference_probe_feed_mm_min : probeSpeedInput;
    const displayProbeRetractInput = effectiveProfileSource === "machine_reference_profile" ? machineSettingsInput.reference_probe_retract_mm : probeRetractInput;
    const effectiveProbeStep = Number(effectiveProfileSource === "map_override" ? configuredProbeStep : inheritedProbeStep);
    const effectiveProbeFeed = Number(effectiveProfileSource === "map_override" ? configuredProbeFeed : inheritedProbeFeed);
    const effectiveProbeRetract = Number(effectiveProfileSource === "map_override" ? configuredProbeRetract : inheritedProbeRetract);
    const meshProbeStateMessage = describeMeshProbeState(physicalMap);
    const persistenceCount = typeof physicalMap?.execution?.persistence_count === "number" ? physicalMap.execution.persistence_count : null;
    const stepCounter = typeof physicalMap?.execution?.step_counter === "number" ? physicalMap.execution.step_counter : null;
    const spacingTarget = parsePositive(meshSpacingInput);
    const visiblePoints = (visibleMesh?.points ?? visibleMesh?.local_points ?? []) as PhysicalMeshPoint[];
    const executablePoints = visibleMesh?.executable_point_count ?? visiblePoints.filter((point) => point.status !== "EXCLUDED").length ?? plannedPoints;
    const excludedPoints = visibleMesh?.excluded_count ?? visiblePoints.filter((point) => point.status === "EXCLUDED").length ?? 0;
    const hasReferencePoint = visiblePoints.some((point) => point.role === "REFERENCE");
    const executablePhysicalPoints = visiblePoints.filter((point) => point.role !== "REFERENCE" && point.status !== "EXCLUDED");
    const firstPhysicalPoint = executablePhysicalPoints[0] ?? null;
    const lastPhysicalPoint = executablePhysicalPoints[executablePhysicalPoints.length - 1] ?? null;
    const meshConfigValid = probeWidth > 0
      && probeHeight > 0
      && safeZ !== undefined
      && Number.isFinite(effectiveProbeStep) && effectiveProbeStep > 0
      && Number.isFinite(effectiveProbeFeed) && effectiveProbeFeed > 0
      && Number.isFinite(effectiveProbeRetract) && effectiveProbeRetract > 0
      && (gridDefinitionMode === "manual" || spacingTarget !== undefined);
    const filteredPhysicalPoints = visiblePoints.filter((point) => {
      if (pointFilter === "ALL") return true;
      if (pointFilter === "FAILED") return point.status === "FAILED" || point.status === "RETRY_REQUIRED";
      if (pointFilter === "PENDING") return ["PENDING", "MOVING", "PROBING"].includes(point.status);
      return point.status === pointFilter;
    });
    const physicalPlanPayload = {
      grid_mode: gridDefinitionMode,
      rows,
      columns,
      edge_margin_left_mm: edgeLeft,
      edge_margin_right_mm: edgeRight,
      edge_margin_bottom_mm: edgeBottom,
      edge_margin_top_mm: edgeTop,
      exclusions: meshExclusions,
      max_spacing_mm: spacingTarget,
      margin_mm: 0,
      safe_z_mm: safeZ,
      probe_profile_source: probeProfileMode,
      probe_step_mm: probeProfileMode === "map_override" ? configuredProbeStep : undefined,
      probe_feed_mm_min: probeProfileMode === "map_override" ? configuredProbeFeed : undefined,
      retract_mm: probeProfileMode === "map_override" ? configuredProbeRetract : undefined,
    };
    const currentMeshFingerprint = selectedOperation ? buildMeshPlanFingerprint({
      projectId: project.id,
      operationId: selectedOperation.id,
      setupId: selectedOperation.setup_id,
      placementRevision: selectedSetup?.placement_revision ?? null,
      gridMode: gridDefinitionMode,
      rows,
      columns,
      edgeLeft,
      edgeRight,
      edgeBottom,
      edgeTop,
      exclusions: meshExclusions,
      safeZ,
      profileSource: probeProfileMode,
      effectiveProbeStep,
      effectiveProbeFeed,
      effectiveProbeRetract,
    }) : null;
    const visibleStatus = String(visibleMesh?.status ?? visibleMesh?.map_ready_state ?? "sin mapa medido");
    const previewRequestDurationMs = typeof meshPreview?.preview_request_duration_ms === "number" ? meshPreview.preview_request_duration_ms : null;
    const previewBackendDurationMs = typeof meshPreview?.preview_backend_duration_ms === "number" ? meshPreview.preview_backend_duration_ms : null;
    const physicalMapCancelled = physicalMap?.status === "CANCELLED";
    const startMapDisabled = mapActionBusy || !physicalMapId || !physicalMap || Boolean(meshPreview) || isPhysicalMapReady(physicalMap) || physicalMapCancelled;
    const requestMeshPreview = async () => {
      if (!selectedOperation || !currentMeshFingerprint) {
        return;
      }
      meshPreviewAbortRef.current?.abort();
      const controller = new AbortController();
      meshPreviewAbortRef.current = controller;
      const requestId = meshPreviewRequestIdRef.current + 1;
      meshPreviewRequestIdRef.current = requestId;
      setPreviewBusy(true);
      setWorkspaceError("");
      const startedAt = performance.now();
      try {
        const result = await api.previewPhysicalMap(project.id, selectedOperation.id, physicalPlanPayload, { signal: controller.signal });
        const payload = {
          ...result.payload,
          preview_request_duration_ms: Number((performance.now() - startedAt).toFixed(3)),
          preview_cancelled: false,
          preview_stale_response_discarded: false,
        };
        if (meshPreviewRequestIdRef.current !== requestId || controller.signal.aborted) {
          return;
        }
        setMeshPreview(payload);
        setMeshPreviewFingerprint(currentMeshFingerprint);
        setActiveMapTab("mapa2d");
        const total = payload.point_count ?? rows * columns;
        setMeshValidationMessage(
          payload.configuration_change_warning
            ?? (physicalReady
              ? `Vista previa generada: ${rows} filas × ${columns} columnas, ${total} puntos. Revise retiro, exclusiones, puntos y recorrido antes de armar.`
              : `Vista previa generada: ${rows} filas × ${columns} columnas, ${total} puntos. Vista previa en coordenadas PCB. Complete la referencia para calcular las coordenadas CNC.`)
        );
      } catch (error) {
        if (isAbortError(error)) {
          setMeshValidationMessage("Generación de vista previa cancelada.");
          return;
        }
        setWorkspaceError(error instanceof Error ? error.message : "No fue posible generar la vista previa de malla.");
      } finally {
        if (meshPreviewRequestIdRef.current === requestId) {
          meshPreviewAbortRef.current = null;
          setPreviewBusy(false);
        }
      }
    };
    const armMeshPreview = async () => {
      if (!selectedOperation || !meshPreview || !currentMeshFingerprint || !physicalReady) {
        return;
      }
      if (meshPreviewFingerprint !== currentMeshFingerprint) {
        setMeshValidationMessage("La configuración cambió después de la preview. Regénere la vista previa antes de armar.");
        return;
      }
      await withPhysicalMapAction(async () => {
        const persisted = (await api.planPhysicalMapFromReference(project.id, selectedOperation.id, physicalPlanPayload)).payload;
        const comparison = previewMatchesPersisted(meshPreview, persisted);
        if (!comparison.matches) {
          throw new Error(buildPreviewMismatchMessage(comparison.differingFields));
        }
        setActiveMapTab("mapa2d");
        setMeshValidationMessage("Sondeo armado. El mapa persistido coincide con la vista previa actual y queda listo para iniciar.");
        return persisted;
      }, { clearPreview: true });
    };
    const mapTabItems: Array<{ id: MapTab; icon: string; label: string; title: string }> = [
      { id: "mapa2d", icon: "▦", label: "Mapa 2D", title: "Ver región, puntos y recorrido de sondeo" },
      { id: "superficie3d", icon: "◭", label: "Superficie 3D", title: "Ver superficie medida sin perder cámara" },
      { id: "puntos", icon: "•", label: "Puntos", title: "Ver puntos en tabla legible" },
      { id: "configuracion", icon: "⚙", label: "Configuración", title: "Configurar malla física" },
    ];
    const heightModeItems: Array<{ id: HeightMode; icon: string; label: string; title: string }> = [
      { id: "bruto", icon: "≈", label: "Altura medida", title: "Altura Z registrada directamente por la sonda." },
      { id: "plano", icon: "∠", label: "Inclinación general", title: "Plano que representa la inclinación promedio de la PCB." },
      { id: "residuo", icon: "Δ", label: "Deformación local", title: "Diferencia entre la superficie medida y su inclinación general." },
    ];
    const addExclusion = () => {
      const next: PhysicalMapExclusion = newExclusionShape === "rectangle"
        ? { id: `exclusion-${Date.now()}`, name: "Nueva zona", shape: "rectangle", enabled: true, x_min_mm: edgeLeft, x_max_mm: Math.min(project.material.ancho_mm - edgeRight, edgeLeft + 5), y_min_mm: edgeBottom, y_max_mm: Math.min(project.material.alto_mm - edgeTop, edgeBottom + 5) }
        : { id: `exclusion-${Date.now()}`, name: "Nueva zona", shape: "circle", enabled: true, center_x_mm: project.material.ancho_mm / 2, center_y_mm: project.material.alto_mm / 2, radius_mm: 3 };
      setMeshExclusions((current) => [...current, next]);
    };
    const updateExclusion = (id: string, patch: Partial<PhysicalMapExclusion>) => {
      setMeshExclusions((current) => current.map((item) => item.id === id ? { ...item, ...patch } : item));
    };
    const formatPointStatus = (status: string) => ({ PENDING: "Pendiente", MOVING: "Moviendo", PROBING: "Sondeando", MEASURED: "Medido", EXCLUDED: "Excluido", FAILED: "Fallido", RETRY_REQUIRED: "Reintento" }[status] ?? status);

    return (
      <div className="stack gap-md">
        <article className="panel map-panel-header">
          <div className="section-heading section-heading--stacked">
            <div>
              <p className="eyebrow">Mapa de alturas</p>
              <h3>{machine.isPhysical ? "Mapa medido físicamente" : "Alturas de la superficie"}</h3>
            </div>
            <StatusBadge tone={machine.isPhysical ? "success" : "neutral"}>{machine.modeLabel}</StatusBadge>
          </div>
          <div className="map-tabbar" role="tablist" aria-label="Vistas del mapa de alturas">
            {mapTabItems.map((item) => (
              <button key={item.id} className={`map-tab-button${activeMapTab === item.id ? " map-tab-button--active" : ""}`} type="button" role="tab" aria-selected={activeMapTab === item.id} title={item.title} onClick={() => setActiveMapTab(item.id)}>
                <span aria-hidden="true">{item.icon}</span><span>{item.label}</span>
              </button>
            ))}
          </div>
          <div className="map-subtoolbar">
            <div className="map-segmented" aria-label="Visualización secundaria">
              {heightModeItems.map((item) => (
                <button key={item.id} className={`map-segment-button${heightMode === item.id ? " map-segment-button--active" : ""}`} type="button" title={item.title} aria-pressed={heightMode === item.id} onClick={() => setHeightMode(item.id)}>
                  <span aria-hidden="true">{item.icon}</span><span>{item.label}</span>
                </button>
              ))}
            </div>
            {!machine.isPhysical ? (
              <div className="map-segmented" aria-label="Fuente del mapa">
                <button className={`map-segment-button${mapSource === "SIMULATED" ? " map-segment-button--active" : ""}`} type="button" onClick={async () => { setMapSource("SIMULATED"); if (selectedOperation) setHeightMap(await api.getHeightMap(project.id, selectedOperation.id).catch(() => null)); }}>SIMULADO</button>
                <button className={`map-segment-button${mapSource === "MEASURED" ? " map-segment-button--active" : ""}`} type="button" onClick={() => void withPhysicalMapAction(async () => selectedOperation ? (await api.getPhysicalMap(project.id, selectedOperation.id)).payload : null)}>MEDIDO</button>
              </div>
            ) : <div className="map-source-lock" role="status">Modo físico: mapa medido como flujo principal</div>}
            <div className="map-segmented" aria-label="Coordenadas del mapa">
              <button className={`map-segment-button${coordinateMode === "local" ? " map-segment-button--active" : ""}`} type="button" onClick={() => setCoordinateMode("local")}>PCB</button>
              <button className={`map-segment-button${coordinateMode === "machine" ? " map-segment-button--active" : ""}`} type="button" onClick={() => setCoordinateMode("machine")}>CNC</button>
            </div>
          </div>
        </article>

        {machine.isPhysical ? (
          <details className="subpanel subpanel--soft map-test-maps">
            <summary>Mapas de prueba / comparar</summary>
            <p className="muted">Los mapas simulados anteriores solo se consultan para comparación. En modo físico no son la acción operativa principal.</p>
            <button className="button button--ghost" type="button" disabled={!selectedOperation} onClick={async () => { if (!selectedOperation) return; setHeightMap(await api.getHeightMap(project.id, selectedOperation.id).catch(() => null)); setActiveMapTab("mapa2d"); }}>Consultar mapa de prueba existente</button>
          </details>
        ) : null}

        {effectiveMapSource === "MEASURED" ? (
          <article className="panel">
            <div className="section-heading section-heading--stacked">
              <div><p className="eyebrow">Malla física del material</p><h3>Configuración y sondeo automático</h3></div>
              <StatusBadge tone={meshPreview ? "info" : isPhysicalMapReady(physicalMap) ? "success" : physicalMap ? "info" : "neutral"}>{visibleStatus}</StatusBadge>
            </div>
            {!machine.isPhysical ? <div className="alert alert--warning">Modo físico requerido para medir un mapa real.</div> : null}
            {!referenceSession?.origen_trabajo ? <div className="alert alert--warning">No puede iniciar la malla: falta capturar el origen X/Y.</div> : null}
            {!referenceSession?.referencia_z ? <div className="alert alert--warning">No puede iniciar la malla: falta referencia Z medida.</div> : null}
            <div className="subpanel subpanel--soft">
              <div className="section-heading section-heading--stacked">
                <div><p className="eyebrow">Definición de cuadrícula</p><h4>Modo explícito</h4></div>
                <div className="map-segmented" aria-label="Definición de cuadrícula">
                  <button className={`map-segment-button${gridDefinitionMode === "suggested" ? " map-segment-button--active" : ""}`} type="button" onClick={() => { setGridDefinitionMode("suggested"); invalidateMeshPreview(); }}>Sugerida automáticamente</button>
                  <button className={`map-segment-button${gridDefinitionMode === "manual" ? " map-segment-button--active" : ""}`} type="button" onClick={() => { setGridDefinitionMode("manual"); invalidateMeshPreview(); }}>Filas y columnas</button>
                </div>
              </div>
              {gridDefinitionMode === "manual" ? (
                <div className="form-grid form-grid--dense">
                  <label>Filas<input value={meshRowsInput} inputMode="numeric" onChange={(event) => { setMeshRowsInput(event.target.value); invalidateMeshPreview(); }} /></label>
                  <label>Columnas<input value={meshColumnsInput} inputMode="numeric" onChange={(event) => { setMeshColumnsInput(event.target.value); invalidateMeshPreview(); }} /></label>
                  <div className="metric-box"><span>Separación X</span><strong>{formatMillimeters(columns > 1 ? probeWidth / (columns - 1) : null, 3)}</strong></div>
                  <div className="metric-box"><span>Separación Y</span><strong>{formatMillimeters(rows > 1 ? probeHeight / (rows - 1) : null, 3)}</strong></div>
                  <div className="metric-box"><span>Puntos totales</span><strong>{plannedPoints}</strong></div>
                </div>
              ) : (
                <div className="stack gap-sm">
                  <label>Separación objetivo recomendada (mm)<input value={meshSpacingInput} inputMode="decimal" onChange={(event) => { setMeshSpacingInput(event.target.value); invalidateMeshPreview(); }} /></label>
                  {meshSuggestion ? <div className="info-grid info-grid--double compact-grid">
                    <div className="metric-box"><span>Filas sugeridas</span><strong>{meshSuggestion.rows}</strong></div>
                    <div className="metric-box"><span>Columnas sugeridas</span><strong>{meshSuggestion.columns}</strong></div>
                    <div className="metric-box"><span>Puntos totales</span><strong>{meshSuggestion.point_count}</strong></div>
                    <div className="metric-box"><span>Separación X resultante</span><strong>{formatMillimeters(meshSuggestion.dx_mm, 3)}</strong></div>
                    <div className="metric-box"><span>Separación Y resultante</span><strong>{formatMillimeters(meshSuggestion.dy_mm, 3)}</strong></div>
                    <div className="metric-box"><span>Tiempo estimado</span><strong>{typeof meshSuggestion.estimated_time_s === "number" ? `${meshSuggestion.estimated_time_s.toFixed(1)} s` : "-"}</strong></div>
                  </div> : <p className="muted">Genere una propuesta antes de aceptarla o previsualizarla.</p>}
                  {meshSuggestion ? <p className="muted">{meshSuggestion.reason}</p> : null}
                  <div className="action-grid action-grid--inline">
                    <button className="button button--ghost" type="button" disabled={!selectedOperation || suggestionBusy} onClick={async () => {
                      if (!selectedOperation) return;
                      setSuggestionBusy(true);
                      setWorkspaceError("");
                      try {
                        const suggestion = await api.suggestPhysicalMap(project.id, selectedOperation.id, { ...physicalPlanPayload, grid_mode: "suggested" });
                        setMeshSuggestion(suggestion);
                        setMeshRowsInput(String(suggestion.rows));
                        setMeshColumnsInput(String(suggestion.columns));
                      } catch (error) {
                        setWorkspaceError(error instanceof Error ? error.message : "No fue posible generar la propuesta de malla.");
                      } finally {
                        setSuggestionBusy(false);
                      }
                    }}>Ver propuesta</button>
                    <button className="button" type="button" disabled={!meshSuggestion} onClick={() => { if (!meshSuggestion) return; setMeshRowsInput(String(meshSuggestion.rows)); setMeshColumnsInput(String(meshSuggestion.columns)); setMeshValidationMessage("Propuesta automática aceptada. Regenerar vista previa antes de confirmar sondeo."); }}>Aceptar sugerencia</button>
                  </div>
                </div>
              )}
            </div>
            <div className="subpanel subpanel--soft">
              <div className="section-heading section-heading--stacked">
                <div><p className="eyebrow">Perfil de sondeo</p><h4>Contrato efectivo de descenso</h4></div>
                <div className="map-segmented" aria-label="Perfil de sondeo">
                  <button className={`map-segment-button${probeProfileMode === "machine_reference_profile" ? " map-segment-button--active" : ""}`} type="button" onClick={() => { setProbeProfileMode("machine_reference_profile"); invalidateMeshPreview(); }}>Heredar referencia</button>
                  <button className={`map-segment-button${probeProfileMode === "map_override" ? " map-segment-button--active" : ""}`} type="button" onClick={() => { setProbeProfileMode("map_override"); invalidateMeshPreview(); }}>Override del mapa</button>
                </div>
              </div>
              <div className="form-grid form-grid--dense">
                <label>Z segura de traslado (mm)<input value={safeZInput} inputMode="decimal" onChange={(event) => { setSafeZInput(event.target.value); invalidateMeshPreview(); }} /></label>
                <label>Paso de sonda (mm)<input value={displayProbeStepInput} inputMode="decimal" disabled={effectiveProfileSource === "machine_reference_profile"} onChange={(event) => { setProbeStepInput(event.target.value); invalidateMeshPreview(); }} /></label>
                <label>Velocidad de sonda (mm/min)<input value={displayProbeFeedInput} inputMode="decimal" disabled={effectiveProfileSource === "machine_reference_profile"} onChange={(event) => { setProbeSpeedInput(event.target.value); invalidateMeshPreview(); }} /></label>
                <label>Retracto (mm)<input value={displayProbeRetractInput} inputMode="decimal" disabled={effectiveProfileSource === "machine_reference_profile"} onChange={(event) => { setProbeRetractInput(event.target.value); invalidateMeshPreview(); }} /></label>
              </div>
              <div className="info-grid info-grid--double compact-grid">
                <div className="metric-box"><span>Fuente efectiva</span><strong>{effectiveProfileSource === "map_override" ? "Override del mapa" : "Perfil de referencia"}</strong></div>
                <div className="metric-box"><span>Paso efectivo</span><strong>{formatMillimeters(Number.isFinite(effectiveProbeStep) ? effectiveProbeStep : null, 3)}</strong></div>
                <div className="metric-box"><span>Velocidad efectiva</span><strong>{formatMillimeters(Number.isFinite(effectiveProbeFeed) ? effectiveProbeFeed : null, 0)}/min</strong></div>
                <div className="metric-box"><span>Retracto efectivo</span><strong>{formatMillimeters(Number.isFinite(effectiveProbeRetract) ? effectiveProbeRetract : null, 3)}</strong></div>
              </div>
              <p className="muted">{probeProfileMode === "machine_reference_profile" ? "La malla hereda exactamente el paso, la velocidad y el retracto de Tomar referencia. Los campos numéricos quedan solo informativos." : "El mapa usa un override explícito. Este modo debe verse como una sustitución deliberada del perfil de referencia."}</p>
            </div>
            <div className="subpanel subpanel--soft">
              <div className="section-heading"><h4>Retiro del borde del material</h4><label className="inline-check"><input type="checkbox" checked={useUniformEdgeRetreat} onChange={(event) => { setUseUniformEdgeRetreat(event.target.checked); invalidateMeshPreview(); }} /> Usar el mismo retiro en todos los bordes</label></div>
              {useUniformEdgeRetreat ? (
                <label>Retiro uniforme (mm)<input value={uniformEdgeRetreatInput} inputMode="decimal" onChange={(event) => { setUniformEdgeRetreatInput(event.target.value); invalidateMeshPreview(); }} /></label>
              ) : (
                <div className="form-grid form-grid--dense">
                  <label>Retiro izquierdo (mm)<input value={edgeRetreatLeftInput} inputMode="decimal" onChange={(event) => { setEdgeRetreatLeftInput(event.target.value); invalidateMeshPreview(); }} /></label>
                  <label>Retiro derecho (mm)<input value={edgeRetreatRightInput} inputMode="decimal" onChange={(event) => { setEdgeRetreatRightInput(event.target.value); invalidateMeshPreview(); }} /></label>
                  <label>Retiro inferior (mm)<input value={edgeRetreatBottomInput} inputMode="decimal" onChange={(event) => { setEdgeRetreatBottomInput(event.target.value); invalidateMeshPreview(); }} /></label>
                  <label>Retiro superior (mm)<input value={edgeRetreatTopInput} inputMode="decimal" onChange={(event) => { setEdgeRetreatTopInput(event.target.value); invalidateMeshPreview(); }} /></label>
                </div>
              )}
              <p className="muted">La región sondeable comienza hacia el interior de la PCB. No modifica el tamaño real del material ni recentra el G-code.</p>
            </div>
            <div className="subpanel subpanel--soft">
              <div className="section-heading"><h4>Zonas no sondeables</h4><div className="segmented"><button className={newExclusionShape === "rectangle" ? "active" : ""} type="button" onClick={() => setNewExclusionShape("rectangle")}>Rectangular</button><button className={newExclusionShape === "circle" ? "active" : ""} type="button" onClick={() => setNewExclusionShape("circle")}>Circular</button></div></div>
              <button className="button button--ghost" type="button" onClick={addExclusion}>Añadir exclusión</button>
              {meshExclusions.length ? <div className="point-card-grid">{meshExclusions.map((exclusion) => (
                <div className="mesh-point-card" key={exclusion.id}>
                  <label>Nombre<input value={exclusion.name} onChange={(event) => updateExclusion(exclusion.id, { name: event.target.value })} /></label>
                  <label className="inline-check"><input type="checkbox" checked={exclusion.enabled} onChange={(event) => updateExclusion(exclusion.id, { enabled: event.target.checked })} /> Activa</label>
                  {exclusion.shape === "rectangle" ? <div className="form-grid form-grid--dense"><label>X min<input value={exclusion.x_min_mm ?? ""} onChange={(event) => updateExclusion(exclusion.id, { x_min_mm: parseNonNegative(event.target.value) ?? 0 })} /></label><label>X max<input value={exclusion.x_max_mm ?? ""} onChange={(event) => updateExclusion(exclusion.id, { x_max_mm: parseNonNegative(event.target.value) ?? 0 })} /></label><label>Y min<input value={exclusion.y_min_mm ?? ""} onChange={(event) => updateExclusion(exclusion.id, { y_min_mm: parseNonNegative(event.target.value) ?? 0 })} /></label><label>Y max<input value={exclusion.y_max_mm ?? ""} onChange={(event) => updateExclusion(exclusion.id, { y_max_mm: parseNonNegative(event.target.value) ?? 0 })} /></label></div> : <div className="form-grid form-grid--dense"><label>Centro X<input value={exclusion.center_x_mm ?? ""} onChange={(event) => updateExclusion(exclusion.id, { center_x_mm: parseNonNegative(event.target.value) ?? 0 })} /></label><label>Centro Y<input value={exclusion.center_y_mm ?? ""} onChange={(event) => updateExclusion(exclusion.id, { center_y_mm: parseNonNegative(event.target.value) ?? 0 })} /></label><label>Radio<input value={exclusion.radius_mm ?? ""} onChange={(event) => updateExclusion(exclusion.id, { radius_mm: parsePositive(event.target.value) ?? 1 })} /></label></div>}
                  <button className="button button--ghost button--danger" type="button" onClick={() => { setMeshExclusions((current) => current.filter((item) => item.id !== exclusion.id)); invalidateMeshPreview(); }}>Eliminar</button>
                </div>
              ))}</div> : <p className="muted">Sin exclusiones adicionales. Use esta sección para pinzas, tornillos u obstáculos.</p>}
            </div>
            <div className="info-grid info-grid--double compact-grid">
              <div className="metric-box"><span>Material</span><strong>{formatMillimeters(project.material.ancho_mm, 3)} × {formatMillimeters(project.material.alto_mm, 3)}</strong></div>
              <div className="metric-box"><span>Retiro del borde</span><strong>{useUniformEdgeRetreat ? `${formatMillimeters(uniformRetreat, 3)} por lado` : `I ${formatMillimeters(edgeLeft, 3)} · D ${formatMillimeters(edgeRight, 3)} · Inf ${formatMillimeters(edgeBottom, 3)} · Sup ${formatMillimeters(edgeTop, 3)}`}</strong></div>
              <div className="metric-box"><span>Región sondeable</span><strong>X {formatMillimeters(edgeLeft, 3)} a {formatMillimeters(project.material.ancho_mm - edgeRight, 3)} · Y {formatMillimeters(edgeBottom, 3)} a {formatMillimeters(project.material.alto_mm - edgeTop, 3)}</strong></div>
              <div className="metric-box"><span>Modo</span><strong>{gridDefinitionMode === "suggested" ? "Automático" : "Manual"}</strong></div>
              <div className="metric-box"><span>Filas / columnas</span><strong>{rows} × {columns}</strong></div>
              <div className="metric-box"><span>Puntos totales</span><strong>{visibleMesh?.point_count ?? plannedPoints}</strong></div>
              <div className="metric-box"><span>Excluidos</span><strong>{excludedPoints}</strong></div>
              <div className="metric-box"><span>Exclusiones</span><strong>{meshExclusions.length ? `${meshExclusions.length} configurada(s)` : "Sin exclusiones"}</strong></div>
              <div className="metric-box"><span>Ejecutables</span><strong>{executablePoints}</strong></div>
              <div className="metric-box"><span>Medidos</span><strong>{visiblePoints.filter((point) => point.status === "MEASURED").length}</strong></div>
              <div className="metric-box"><span>Pendientes</span><strong>{visiblePoints.filter((point) => ["PENDING", "MOVING", "PROBING"].includes(point.status)).length}</strong></div>
              <div className="metric-box"><span>Separación X</span><strong>{formatMillimeters(visibleMesh?.grid?.dx_mm ?? (columns > 1 ? probeWidth / (columns - 1) : null), 3)}</strong></div>
              <div className="metric-box"><span>Separación Y</span><strong>{formatMillimeters(visibleMesh?.grid?.dy_mm ?? (rows > 1 ? probeHeight / (rows - 1) : null), 3)}</strong></div>
              <div className="metric-box"><span>Z segura</span><strong>{formatMillimeters(safeZ, 3)}</strong></div>
              <div className="metric-box"><span>Perfil efectivo</span><strong>{effectiveProfileSource === "map_override" ? "Override del mapa" : "Perfil de referencia"}</strong></div>
              <div className="metric-box"><span>Paso / velocidad</span><strong>{formatMillimeters(Number.isFinite(effectiveProbeStep) ? effectiveProbeStep : null, 3)} · {formatMillimeters(Number.isFinite(effectiveProbeFeed) ? effectiveProbeFeed : null, 0)}/min</strong></div>
              <div className="metric-box"><span>Retracto</span><strong>{formatMillimeters(Number.isFinite(effectiveProbeRetract) ? effectiveProbeRetract : null, 3)}</strong></div>
              <div className="metric-box"><span>Primer punto</span><strong>{firstPhysicalPoint ? `X ${formatMillimeters(firstPhysicalPoint.x_local, 3)} · Y ${formatMillimeters(firstPhysicalPoint.y_local, 3)}` : "Pendiente de preview"}</strong></div>
              <div className="metric-box"><span>Último punto</span><strong>{lastPhysicalPoint ? `X ${formatMillimeters(lastPhysicalPoint.x_local, 3)} · Y ${formatMillimeters(lastPhysicalPoint.y_local, 3)}` : "Pendiente de preview"}</strong></div>
              <div className="metric-box"><span>Preview request</span><strong>{typeof previewRequestDurationMs === "number" ? `${previewRequestDurationMs.toFixed(1)} ms` : "-"}</strong></div>
              <div className="metric-box"><span>Preview backend</span><strong>{typeof previewBackendDurationMs === "number" ? `${previewBackendDurationMs.toFixed(1)} ms` : "-"}</strong></div>
            </div>
            {probeWidth <= 0 || probeHeight <= 0 ? <div className="alert alert--warning">El retiro de los bordes deja una región de sondeo inválida. Reduzca los valores o revise las dimensiones del material.</div> : null}
            {!meshConfigValid ? <div className="alert alert--warning">La configuración de malla es inválida. Revise filas, columnas, límites, separación objetivo y parámetros de sonda antes de continuar.</div> : null}
            {meshProbeStateMessage ? <div className="alert alert--info">{meshProbeStateMessage}{stepCounter !== null ? ` · pasos ${stepCounter}` : ""}{persistenceCount !== null ? ` · persistencias ${persistenceCount}` : ""}</div> : null}
            {meshValidationMessage ? <div className="alert alert--info">{meshValidationMessage}</div> : null}
            <div className="action-grid">
              <button className="button" type="button" disabled={previewBusy || !selectedOperation || !meshConfigValid} onClick={() => void requestMeshPreview()}>{previewBusy ? "Generando vista previa…" : "1. Generar vista previa de malla"}</button>
              {previewBusy ? <button className="button button--ghost" type="button" onClick={() => clearMeshPreview("Generación de vista previa cancelada.")}>Cancelar generación</button> : null}
              <button className="button" type="button" disabled={!meshPreview} onClick={() => setMeshValidationMessage(physicalFailedPoints > 0 ? `La malla tiene ${physicalFailedPoints} punto(s) fallidos o pendientes de reintento.` : "Cobertura geométrica revisada. No se extrapola fuera de la región interior ni sobre exclusiones.")}>2. Validar límites</button>
              <button className="button" type="button" disabled={!meshPreview || !physicalReady || meshPreviewFingerprint !== currentMeshFingerprint || physicalMap?.status === "MESH_COMPLETE"} onClick={() => void armMeshPreview()}>3. Armar sondeo</button>
              <button className="button" type="button" disabled={startMapDisabled} onClick={() => void withPhysicalMapAction(async () => (await api.executeAllPhysicalMapPoints(project.id, physicalMapId)).payload)}>{mapActionBusy ? "Iniciando sondeo…" : "4. Iniciar sondeo automático"}</button>
              <button className="button button--ghost" type="button" disabled={!physicalMapId} onClick={() => void withPhysicalMapAction(async () => {
                if (!physicalMapId) return null;
                const result = await api.repeatPhysicalMap(project.id, physicalMapId);
                setActiveMapTab("mapa2d"); setMeshValidationMessage("Mapa anterior archivado. Nueva versión vacía generada con punto #0 X0/Y0 y todos los nodos pendientes. Confirme antes de mover."); return result.payload;
              })} title="Conserva origen X/Y y receta; archiva el mapa actual y vuelve a medir referencia y nodos.">Repetir medición completa</button>
              <button className="button button--ghost" type="button" disabled={!meshPreview && !previewBusy} onClick={() => clearMeshPreview("Vista previa limpia. La configuración, la referencia y los mapas persistidos se conservaron.")}>Limpiar vista previa</button>
              <button className="button button--ghost" type="button" disabled={mapActionBusy || !physicalMapId} onClick={() => void withPhysicalMapAction(async () => (await api.pausePhysicalMap(project.id, physicalMapId)).payload)}>Pausar</button>
              <button className="button button--ghost" type="button" disabled={mapActionBusy || !physicalMapId} onClick={() => void withPhysicalMapAction(async () => (await api.resumePhysicalMap(project.id, physicalMapId)).payload)}>Reanudar</button>
              <button className="button button--ghost" type="button" disabled={mapActionBusy || !physicalMapId || physicalFailedPoints === 0} onClick={() => void withPhysicalMapAction(async () => (await api.executeAllPhysicalMapPoints(project.id, physicalMapId)).payload)}>Reintentar puntos fallidos</button>
              <button className="button button--ghost button--danger" type="button" disabled={mapActionBusy || !physicalMapId} onClick={() => void withPhysicalMapAction(async () => (await api.cancelPhysicalMap(project.id, physicalMapId)).payload)}>Cancelar</button>
            </div>
          </article>
        ) : null}

        {heightMap ? (
          <article className="panel"><div className="section-heading section-heading--stacked"><div><p className="eyebrow">Métricas</p><h3>Alturas de la superficie</h3></div><StatusBadge tone={heightMap.etiqueta_simulada ? "warning" : "success"}>{heightMap.etiqueta_simulada ? "SIMULADO" : "MEASURED"}</StatusBadge></div><div className="info-grid info-grid--double compact-grid"><div className="metric-box"><span>Z mínima</span><strong>{formatMillimeters(heightMap.estadisticas.altura_min_mm, 4)}</strong></div><div className="metric-box"><span>Z máxima</span><strong>{formatMillimeters(heightMap.estadisticas.altura_max_mm, 4)}</strong></div><div className="metric-box"><span>Rango</span><strong>{formatMillimeters(heightMap.estadisticas.rango_alturas_mm, 4)}</strong></div><div className="metric-box"><span>Valor de referencia</span><strong>{formatMillimeters(heightMap.estadisticas.valor_referencia_mm, 4)}</strong></div><div className="metric-box"><span>RMS</span><strong>{formatMillimeters(heightMap.estadisticas.desviacion_rms_respecto_plano_mm, 4)}</strong></div><div className="metric-box"><span>Residuo máximo</span><strong>{formatMillimeters(heightMap.estadisticas.residuo_maximo_mm, 4)}</strong></div></div></article>
        ) : null}

        {activeMapTab === "mapa2d" && (heightMap || visibleMesh) ? <HeightMapHeatmap material={project.material} heightMap={heightMap} mode={heightMode} meshPoints={visibleMesh?.points ?? visibleMesh?.local_points ?? []} exclusions={visibleMesh?.exclusions ?? meshExclusions} probeRegion={visibleMesh?.local_region ?? visibleMesh?.probe_region ?? heightMap?.probe_region ?? null} coordinateMode={coordinateMode} machineOrigin={typeof visibleMesh?.machine_origin_x === "number" && typeof visibleMesh?.machine_origin_y === "number" ? { x_mm: visibleMesh.machine_origin_x, y_mm: visibleMesh.machine_origin_y } : null} previewMessage={visibleMesh?.warnings?.[0] ?? null} /> : null}
        {activeMapTab === "superficie3d" && heightMap ? <HeightMapSurface3D heightMap={heightMap} mode={heightMode} /> : null}
        {activeMapTab === "superficie3d" && !heightMap && isPhysicalMapReady(physicalMap) ? <article className="panel empty-state"><p>Cargando superficie 3D del mapa medido...</p></article> : null}
        {activeMapTab === "puntos" ? (
          <article className="panel"><div className="section-heading section-heading--stacked"><div><p className="eyebrow">Puntos de malla</p><h3>Lecturas y estados</h3></div><div className="map-segmented" aria-label="Filtro de puntos">{(["ALL", "PENDING", "MEASURED", "EXCLUDED", "FAILED"] as const).map((filter) => <button key={filter} className={`map-segment-button${pointFilter === filter ? " map-segment-button--active" : ""}`} type="button" onClick={() => setPointFilter(filter)}>{filter === "ALL" ? "Todos" : filter === "PENDING" ? "Pendientes" : filter === "MEASURED" ? "Medidos" : filter === "EXCLUDED" ? "Excluidos" : "Fallidos"}</button>)}</div></div>{filteredPhysicalPoints.length ? <div className="point-card-grid">{filteredPhysicalPoints.map((point: PhysicalMeshPoint) => <div className="mesh-point-card" key={point.index}><strong>{point.role === "REFERENCE" ? "Punto #0 — Referencia X0/Y0" : `Punto #${hasReferencePoint ? point.index : point.index + 1}`}</strong><span>Fila {typeof point.row === "number" && point.row >= 0 ? point.row + 1 : "-"}</span><span>Columna {typeof point.column === "number" && point.column >= 0 ? point.column + 1 : "-"}</span><span>PCB X/Y: {formatMillimeters(point.x_local, 3)} / {formatMillimeters(point.y_local, 3)}</span><span>CNC X/Y: {formatMillimeters(point.x_machine ?? null, 3)} / {formatMillimeters(point.y_machine ?? null, 3)}</span><span>Z medida: {formatMillimeters(point.z_measured_abs ?? point.z_measured ?? null, 3)}</span><span>Delta Z: {formatMillimeters(point.delta_z ?? null, 3)}</span><span>Estado: {formatPointStatus(point.status)}</span><span>Intentos: {point.attempts ?? 0}</span><span>Duración: {typeof point.duration_s === "number" ? `${point.duration_s.toFixed(3)} s` : "-"}</span>{point.error || point.last_error ? <span>Error: {point.error ?? point.last_error}</span> : null}{point.status === "FAILED" && physicalMapId ? <div className="action-grid action-grid--inline"><button className="button button--ghost" type="button" disabled={mapActionBusy} onClick={() => void withPhysicalMapAction(async () => (await api.retryPhysicalMapPoint(project.id, physicalMapId, point.index)).payload)}>Reintentar punto</button><button className="button button--ghost" type="button" disabled={mapActionBusy} onClick={() => void withPhysicalMapAction(async () => (await api.skipPhysicalMapPoint(project.id, physicalMapId, point.index)).payload)}>Omitir punto</button></div> : null}</div>)}</div> : <p className="muted">Genere la vista previa de malla para ver los puntos.</p>}</article>
        ) : null}
        {activeMapTab === "puntos" && physicalMapHistory.length > 0 ? (
          <article className="panel"><div className="section-heading"><h3>Historial de mediciones</h3></div><div className="point-card-grid">{physicalMapHistory.slice(0, 8).map((entry) => <div className="mesh-point-card" key={String(entry.map_id)}><strong>Versión {String(entry.version ?? "-")}</strong><span>Estado: {String(entry.status ?? "-")}</span><span>Placement: {String(entry.placement_revision ?? "-")}</span><span>Filas/columnas: {String(entry.rows ?? "-")} × {String(entry.columns ?? "-")}</span><span>Medidos: {String(entry.points_measured ?? 0)}</span><span>Fallidos: {String(entry.points_failed ?? 0)}</span><span>{entry.active ? "Activo" : "Histórico"}</span></div>)}</div></article>
        ) : null}
        {activeMapTab === "configuracion" && !machine.isPhysical ? (
          <HeightMapControlPanel material={project.material} heightMap={heightMap} busy={heightMapBusy} onConfigure={(nextPayload) => withHeightMapAction(() => api.configureHeightMap(project.id, selectedOperation!.id, nextPayload))} onSimulate={(nextPayload) => withHeightMapAction(() => api.simulateHeightMap(project.id, selectedOperation!.id, nextPayload))} onImportJson={(content) => withHeightMapAction(() => api.importHeightMapJson(project.id, selectedOperation!.id, content))} onImportCsv={(content) => withHeightMapAction(() => api.importHeightMapCsv(project.id, selectedOperation!.id, content))} onRecalculate={() => withHeightMapAction(() => api.recalculateHeightMap(project.id, selectedOperation!.id))} onDelete={() => withHeightMapAction(async () => { await api.deleteHeightMap(project.id, selectedOperation!.id); setHeightMap(null); })} />
        ) : null}
        {activeMapTab === "configuracion" && machine.isPhysical ? <article className="panel"><div className="section-heading"><h3>Configuración física activa</h3></div><p className="muted">La configuración física está arriba: filas, columnas, retiro de borde, exclusiones, Z segura, paso, velocidad y retracto.</p></article> : null}
        {!heightMap && !visibleMesh ? <div className="panel empty-state"><p>{machine.isPhysical ? "Genere la vista previa de malla para ver región, puntos y recorrido." : "Configure la región sondeable, genere un mapa simulado o importe mediciones."}</p></div> : null}
      </div>
    );
  };


  const resetPreparation = async (scope: "reference" | "map" | "preparation") => {
    if (!selectedSetup) return;
    const completeMessage = "Esta acción eliminará las referencias y el mapa activos del montaje. Los G-codes originales, operaciones y mediciones históricas se conservarán. Después deberá repetir homing, origen, referencia y malla.";
    const mapMessage = "Reiniciar solo mapa archivará el mapa activo, conservará origen X/Y y referencias válidas, y exigirá una nueva malla.";
    const referenceMessage = "Reiniciar solo referencia invalidará origen X/Y, referencias Z, mapa y compensaciones dependientes.";
    const confirmed = window.confirm(scope === "preparation" ? completeMessage : scope === "map" ? mapMessage : referenceMessage);
    if (!confirmed) return;
    setReferenceBusy(true);
    setWorkspaceError("");
    try {
      if (scope === "map") {
        await api.resetSetupMap(project.id, selectedSetup.id);
      } else if (scope === "reference") {
        await api.resetSetupReference(project.id, selectedSetup.id);
      } else {
        await api.resetSetupPreparation(project.id, selectedSetup.id);
      }
      if (onRefreshProject) {
        await onRefreshProject();
      }
      setMeshPreview(null);
      setMeshPreviewFingerprint(null);
      setPhysicalMap(null);
      setHeightMap(null);
      setMeshSuggestion(null);
      setWorkspaceError("");
      setMeshValidationMessage("");
      await machine.refreshRuntime();
      if (selectedOperation) {
        setReferenceSession(await api.getReferenceSession(project.id, selectedOperation.id));
      }
      setMeshValidationMessage(scope === "map" ? "Mapa activo reiniciado. G-codes y operaciones siguen presentes. Genere la vista previa para volver a medir." : "Preparación reiniciada. Arduino desconectado; G-codes, operaciones y receta de malla siguen presentes.");
    } catch (error) {
      setWorkspaceError(error instanceof Error ? error.message : "No fue posible reiniciar la preparación.");
    } finally {
      setReferenceBusy(false);
    }
  };



  const refreshJobState = async () => {
    if (!project || !selectedSetup || !activeJobFace) {
      return;
    }
    const [plan, live] = await Promise.all([
      api.getJobPlan(project.id, selectedSetup.id, activeJobFace),
      api.getLiveExecution(project.id, selectedSetup.id, activeJobFace),
    ]);
    setJobPlan(plan);
    setLiveExecution(live);
    setExecutionError(null);
  };

  const updateCompensationSettings = async (next: { compensation_mode?: "legacy" | "adaptive_fast"; max_z_error_mm?: number }) => {
    if (!selectedOperation) {
      return;
    }
    const currentCompensationMode = selectedOperation.compensation_mode ?? "legacy";
    const currentMaxZError = selectedOperation.max_z_error_mm ?? 0.05;
    await onUpdateOperation(selectedOperation.id, {
      nombre: selectedOperation.nombre,
      tool_id: selectedOperation.tool_id,
      herramienta: selectedOperation.herramienta,
      compensation_mode: next.compensation_mode ?? currentCompensationMode,
      max_z_error_mm: next.max_z_error_mm ?? currentMaxZError,
    });
    if (onRefreshProject) {
      await onRefreshProject();
    }
    await refreshCompensationAudit();
  };

  const downloadCompensatedArtifact = async (mode: "legacy" | "adaptive_fast") => {
    if (!project || !selectedOperation) {
      return;
    }
    setReferenceBusy(true);
    setWorkspaceError("");
    try {
      const generated = await api.generateCompensatedGCode(project.id, selectedOperation.id, mode);
      window.open(api.generatedFileUrl(project.id, generated.relative_path), "_blank", "noopener,noreferrer");
      if (generated.warning) {
        setWorkspaceError(generated.warning);
      }
      await refreshCompensationAudit();
    } catch (error) {
      setWorkspaceError(error instanceof Error ? error.message : "No fue posible generar el archivo compensado solicitado.");
    } finally {
      setReferenceBusy(false);
    }
  };

  const generateProjectCompensation = async () => {
    if (!project || !selectedSetup || !activeJobFace) {
      return;
    }
    setReferenceBusy(true);
    setWorkspaceError("");
    try {
      const plan = await api.generateProjectCompensation(project.id, selectedSetup.id, activeJobFace);
      setJobPlan(plan);
      setLiveExecution(await api.getLiveExecution(project.id, selectedSetup.id, activeJobFace));
    } catch (error) {
      setWorkspaceError(error instanceof Error ? error.message : "No fue posible generar la compensación del proyecto.");
    } finally {
      setReferenceBusy(false);
    }
  };

  const prepareJobRun = async () => {
    if (!project || !selectedSetup || !activeJobFace) {
      return;
    }
    setReferenceBusy(true);
    setWorkspaceError("");
    try {
      await api.prepareJobRun(project.id, selectedSetup.id, activeJobFace);
      await refreshJobState();
    } catch (error) {
      setExecutionError(error instanceof ApiError ? error : null);
      setWorkspaceError(error instanceof Error ? error.message : "No fue posible preparar el trabajo.");
    } finally {
      setReferenceBusy(false);
    }
  };

  const startJobRun = async () => {
    if (startJobInFlight.current || !project || !selectedSetup || !activeJobFace) {
      return;
    }
    startJobInFlight.current = true;
    setReferenceBusy(true);
    setWorkspaceError("");
    try {
      await api.startJobRun(project.id, selectedSetup.id, activeJobFace);
      await refreshJobState();
    } catch (error) {
      setExecutionError(error instanceof ApiError ? error : null);
      setWorkspaceError(error instanceof Error ? error.message : "No fue posible iniciar el trabajo multioperación.");
    } finally {
      startJobInFlight.current = false;
      setReferenceBusy(false);
    }
  };

  const runJobAction = async (action: string) => {
    if (!project || !selectedSetup || !activeJobFace) {
      return;
    }
    setReferenceBusy(true);
    setWorkspaceError("");
    try {
      await api.runJobAction(project.id, selectedSetup.id, activeJobFace, action);
      await refreshJobState();
      if (selectedOperation) {
        void api.getReferenceSession(project.id, selectedOperation.id).then(setReferenceSession).catch(() => undefined);
        void api.getPhysicalMap(project.id, selectedOperation.id).then((result) => setPhysicalMap(result.payload)).catch(() => undefined);
      }
    } catch (error) {
      setExecutionError(error instanceof ApiError ? error : null);
      setWorkspaceError(error instanceof Error ? error.message : "No fue posible actualizar el trabajo multioperación.");
    } finally {
      setReferenceBusy(false);
    }
  };

  const archiveStaleJobRun = async () => {
    if (!project || !selectedSetup || !activeJobFace) {
      return;
    }
    setReferenceBusy(true);
    setWorkspaceError("");
    try {
      await api.archiveStaleJobRun(project.id, selectedSetup.id, activeJobFace);
      await refreshJobState();
      if (onRefreshProject) {
        await onRefreshProject();
      }
    } catch (error) {
      setExecutionError(error instanceof ApiError ? error : null);
      setWorkspaceError(error instanceof Error ? error.message : "No fue posible cerrar la ejecución obsoleta.");
    } finally {
      setReferenceBusy(false);
    }
  };

  const renderJobCompensationPanel = () => {
    if (!selectedSetup || !activeJobFace) {
      return null;
    }
    const currentCompensationMode = selectedOperation?.compensation_mode ?? "legacy";
    const currentMaxZError = selectedOperation?.max_z_error_mm ?? 0.05;
    const adaptiveExecutable = compensationAudit?.adaptive_fast?.executable !== false;
    const adaptiveDownloadLabel = adaptiveExecutable ? "Descargar adaptive_fast" : "Descargar adaptive experimental";
    return (
      <article className="panel">
        <div className="section-heading section-heading--stacked">
          <div>
            <p className="eyebrow">1. Compensación del proyecto</p>
            <h3>Plan multioperación listo para ejecución — {translateFace(activeJobFace)}</h3>
          </div>
          <div className="toolbar-inline">
            <button className="button button--ghost" type="button" disabled={referenceBusy} onClick={() => void prepareJobRun()}>Revalidar plan</button>
            <button className="button" type="button" disabled={referenceBusy} onClick={() => void generateProjectCompensation()}>Generar compensación del proyecto</button>
          </div>
        </div>
        {selectedOperation ? (
          <div className="stack gap-md">
            <div className="info-grid info-grid--double compact-grid">
              <div className="metric-box">
                <span>Operación seleccionada</span>
                <strong>{selectedOperation.nombre}</strong>
              </div>
              <div className="metric-box">
                <span>Motor activo</span>
                <strong>{currentCompensationMode === "adaptive_fast" ? "Adaptativa rápida" : "Legacy"}</strong>
              </div>
              <div className="metric-box">
                <span>Tolerancia Z</span>
                <strong>{formatMillimeters(currentMaxZError, 3)}</strong>
              </div>
              <div className="metric-box">
                <span>Auditoría</span>
                <strong>{compensationAuditBusy ? "Calculando..." : compensationAudit ? "Disponible" : compensationAuditError ? "No disponible" : "Pendiente"}</strong>
              </div>
            </div>
            <div className="action-grid action-grid--inline">
              <button className={`button${currentCompensationMode === "legacy" ? "" : " button--ghost"}`} type="button" disabled={referenceBusy} onClick={() => void updateCompensationSettings({ compensation_mode: "legacy" })}>Legacy</button>
              <button className={`button${currentCompensationMode === "adaptive_fast" ? "" : " button--ghost"}`} type="button" disabled={referenceBusy || !adaptiveExecutable} onClick={() => void updateCompensationSettings({ compensation_mode: "adaptive_fast" })}>Adaptativa rápida</button>
              <label className="field-inline">
                <span>Tolerancia Z (mm)</span>
                <input
                  value={compensationToleranceInput}
                  inputMode="decimal"
                  onChange={(event) => setCompensationToleranceInput(event.target.value)}
                  onBlur={() => {
                    const parsed = Number(compensationToleranceInput);
                    if (Number.isFinite(parsed) && parsed > 0 && selectedOperation) {
                      void updateCompensationSettings({ max_z_error_mm: parsed });
                    }
                  }}
                />
              </label>
              <button className="button button--ghost" type="button" disabled={compensationAuditBusy || referenceBusy} onClick={() => void refreshCompensationAudit()}>Recalcular auditoría</button>
              <button className="button button--ghost" type="button" disabled={referenceBusy} onClick={() => void downloadCompensatedArtifact("legacy")}>Descargar legacy</button>
              <button className="button button--ghost" type="button" disabled={referenceBusy} onClick={() => void downloadCompensatedArtifact("adaptive_fast")}>{adaptiveDownloadLabel}</button>
            </div>
            {compensationAudit ? (
              <div className="table-scroll">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Métrica</th>
                      <th>Original</th>
                      <th>Legacy</th>
                      <th>Adaptive</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <td>Tiempo estimado</td>
                      <td>{formatDurationSeconds(compensationAudit.original.estimated_time_s)}</td>
                      <td>{formatDurationSeconds(compensationAudit.legacy.estimated_time_s)}</td>
                      <td>{formatDurationSeconds(compensationAudit.adaptive_fast.estimated_time_s)}</td>
                    </tr>
                    <tr>
                      <td>Método / confianza</td>
                      <td>{[compensationAudit.original.estimation_method, compensationAudit.original.estimation_confidence].filter(Boolean).join(" / ") || "-"}</td>
                      <td>{[compensationAudit.legacy.estimation_method, compensationAudit.legacy.estimation_confidence].filter(Boolean).join(" / ") || "-"}</td>
                      <td>{[compensationAudit.adaptive_fast.estimation_method, compensationAudit.adaptive_fast.estimation_confidence].filter(Boolean).join(" / ") || "-"}</td>
                    </tr>
                    <tr>
                      <td>Detalle de estimación</td>
                      <td>{compensationAudit.original.estimation_detail ?? "-"}</td>
                      <td>{compensationAudit.legacy.estimation_detail ?? "-"}</td>
                      <td>{compensationAudit.adaptive_fast.estimation_detail ?? "-"}</td>
                    </tr>
                    <tr>
                      <td>Movimientos</td>
                      <td>{compensationAudit.original.movements_total ?? "-"}</td>
                      <td>{compensationAudit.legacy.movements_total ?? "-"}</td>
                      <td>{compensationAudit.adaptive_fast.movements_total ?? "-"}</td>
                    </tr>
                    <tr>
                      <td>Diferencia vs legacy</td>
                      <td>-</td>
                      <td>-</td>
                      <td>{compensationAudit.adaptive_fast.time_difference_pct == null ? "-" : `${formatNumber(compensationAudit.adaptive_fast.time_difference_pct, 2)} %`}</td>
                    </tr>
                    <tr>
                      <td>Error Z máximo</td>
                      <td>-</td>
                      <td>{formatMillimeters(compensationAudit.legacy.error_z_max_approximation_mm ?? null, 4)}</td>
                      <td>{formatMillimeters(compensationAudit.adaptive_fast.error_z_max_approximation_mm ?? null, 4)}</td>
                    </tr>
                    <tr>
                      <td>Segmentos subdivididos</td>
                      <td>-</td>
                      <td>{compensationAudit.legacy.segments_subdivided ?? 0}</td>
                      <td>{compensationAudit.adaptive_fast.segments_subdivided ?? 0}</td>
                    </tr>
                    <tr>
                      <td>Comandos no soportados</td>
                      <td>{(compensationAudit.original.unsupported_commands ?? []).join(", ") || "-"}</td>
                      <td>{(compensationAudit.legacy.unsupported_commands ?? []).join(", ") || "-"}</td>
                      <td>{compensationAudit.adaptive_fast.error ?? ((compensationAudit.adaptive_fast.unsupported_commands ?? []).join(", ") || "-")}</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            ) : compensationAuditError ? (
              <p className="muted">{compensationAuditError}</p>
            ) : <p className="muted">La auditoría comparativa se generará cuando exista mapa válido y archivo analizado para esta operación.</p>}
            {compensationAudit?.adaptive_fast && compensationAudit.adaptive_fast.eligible === false ? (
              <div className="alert alert--warning">
                <strong>Adaptive_fast no es elegible</strong>
                <p>{compensationAudit.adaptive_fast.error ?? "La auditoría detectó que supera tolerancia, introduce comandos no soportados o excede el umbral de tiempo frente a legacy."}</p>
                <p>{adaptiveExecutable ? "Puede revisar la auditoría y volver a generar." : "Solo se permite descargar un artefacto experimental no ejecutable; legacy sigue disponible para plan y ejecución."}</p>
              </div>
            ) : null}
          </div>
        ) : null}
        {jobPlan ? (
          <>
            <div className="info-grid info-grid--double compact-grid">
              <div className="metric-box"><span>Operaciones</span><strong>{jobPlan.summary.operations_total}</strong></div>
              <div className="metric-box"><span>Listas</span><strong>{jobPlan.summary.operations_ready}</strong></div>
              <div className="metric-box"><span>Archivos generados</span><strong>{jobPlan.summary.generated_files}</strong></div>
              <div className="metric-box"><span>Cambios de herramienta</span><strong>{jobPlan.summary.tool_changes}</strong></div>
              <div className="metric-box"><span>Herramientas distintas</span><strong>{jobPlan.summary.distinct_tools}</strong></div>
              <div className="metric-box"><span>Bloqueadas</span><strong>{jobPlan.summary.blocked_operations}</strong></div>
              <div className="metric-box"><span>Tiempo compensado</span><strong>{formatDurationSeconds(jobPlan.summary.estimated_time_s)}</strong></div>
            </div>
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Orden</th>
                    <th>Operación</th>
                    <th>Herramienta</th>
                    <th>Mapa</th>
                    <th>Cobertura</th>
                    <th>Referencia Z</th>
                    <th>ETA</th>
                    <th>G-code</th>
                  </tr>
                </thead>
                <tbody>
                  {jobPlan.operations.map((item) => (
                    <tr key={item.operation_id}>
                      <td>{item.order_label}</td>
                      <td>
                        <strong>{item.name}</strong>
                        {item.blocking_reasons.length > 0 ? <div className="muted">{item.blocking_reasons[0]}</div> : null}
                      </td>
                      <td>{item.tool_name}</td>
                      <td>{item.map_status}</td>
                      <td>{item.coverage_status}{item.coverage_detail ? <div className="muted">{item.coverage_detail}</div> : null}</td>
                      <td>{item.reference_status}</td>
                      <td>{formatDurationSeconds(item.estimated_time_s)}</td>
                      <td>{item.generated_file_name ?? "pendiente"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="muted">Orden real: compensar todas las operaciones, ejecutar la primera, ir a cambio de herramienta cuando corresponda, confirmar, medir la nueva Z en X0/Y0, recompensar lo pendiente y continuar automáticamente.</p>
            {jobPlan.manifest_path ? <p className="muted">Manifiesto actual: {jobPlan.manifest_path}</p> : null}
          </>
        ) : <p className="muted">Cree el plan del montaje/cara actual para ver todas las operaciones compensables.</p>}
      </article>
    );
  };

  const renderJobExecutionPanel = () => {
    if (!selectedSetup || !activeJobFace) {
      return null;
    }
    return (
      <ExecutionConsole
        snapshot={liveExecution}
        error={executionError}
        busy={referenceBusy}
        onPrepare={prepareJobRun}
        onStart={startJobRun}
        onAction={runJobAction}
        onArchiveStale={archiveStaleJobRun}
      />
    );
  };

  const renderEjecucion = () => (
    <div className="stack gap-md">
      {renderJobCompensationPanel()}
      {renderJobExecutionPanel()}
    </div>
  );

  return (
    <div className="workspace-stack">
      <article className="panel project-hero">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Proyecto activo</p>
            <h2>{project.nombre}</h2>
          </div>
          <div className="hero-actions">
            <StatusBadge tone={toneForStatus(project.estado_general)}>{translateStatus(project.estado_general)}</StatusBadge>
            <button className="button button--ghost" type="button" onClick={() => setEditingProject((current) => !current)}>
              {editingProject ? "Cerrar edición" : "Editar proyecto"}
            </button>
            <details className="inline-actions-menu">
              <summary className="button button--ghost">Más acciones</summary>
              <div className="inline-actions-menu__content">
                <strong>Reiniciar...</strong>
                <button className="button button--ghost" type="button" disabled={referenceBusy || !selectedSetup} onClick={() => void resetPreparation("reference")} title="Reiniciar solo referencia">Solo origen/Z</button>
                <button className="button button--ghost" type="button" disabled={referenceBusy || !selectedSetup} onClick={() => void resetPreparation("map")}>Reiniciar solo mapa</button>
                <button className="button button--ghost button--danger" type="button" disabled={referenceBusy || !selectedSetup} onClick={() => void resetPreparation("preparation")} title="Desconecta Arduino, invalida origen X/Y, referencias Z y mapa activo; conserva G-code, operaciones y receta de malla.">Reiniciar proceso</button>
              </div>
            </details>
          </div>
        </div>
        <div className="hero-grid hero-grid--project">
          <div><span className="eyebrow">Material bruto</span><strong>{project.material.ancho_mm} × {project.material.alto_mm} × {project.material.espesor_mm ?? "-"} mm</strong></div>
          <div><span className="eyebrow">Operaciones</span><strong>{project.operaciones.length}</strong></div>
          <div><span className="eyebrow">Actualizado</span><strong>{formatDate(project.actualizado_en)}</strong></div>
          <div><span className="eyebrow">Sesión</span><strong>{referenceSession?.estado ?? "sin iniciar"}</strong></div>
        </div>
      </article>

      {editingProject ? <ProjectForm initialValue={payload!} projectId={project.id} mode="edit" onSubmit={onSaveProject} submitting={savingProject} /> : null}

      <article className="panel workspace-summary-panel">
        <div className="section-heading section-heading--stacked">
          <div>
            <p className="eyebrow">Resumen operativo</p>
            <h3>{selectedOperation?.nombre ?? "Sin operación"}</h3>
          </div>
          {selectedOperation ? <StatusBadge tone={toneForStatus(selectedOperation.estado)}>{translateStatus(selectedOperation.estado)}</StatusBadge> : null}
        </div>
        {selectedOperation ? (
          <div className="info-grid info-grid--double compact-grid">
            <div className="metric-box"><span>Tipo</span><strong>{translateOperationType(selectedOperation.tipo)}</strong></div>
            <div className="metric-box"><span>Cara</span><strong>{translateFace(selectedOperation.cara)}</strong></div>
            <div className="metric-box"><span>Archivo</span><strong>{selectedOperation.nombre_archivo_original ?? "Sin archivo"}</strong></div>
            <div className="metric-box"><span>Mapa</span><strong>{heightMap ? `${heightMap.fuente_datos} v${heightMap.version}` : "No disponible"}</strong></div>
            <div className="metric-box"><span>Referencia de máquina</span><strong>{referenceSession?.machine_reference.confirmada ? "Confirmada" : "Pendiente"}</strong></div>
            <div className="metric-box"><span>Compensación</span><strong>{referenceSession?.lista_para_compensacion ? "Lista" : "Bloqueada"}</strong></div>
          </div>
        ) : <p className="muted">Seleccione una operación para abrir el workspace.</p>}
      </article>

      <details className="panel workflow-guide">
        <summary>Flujo de trabajo</summary>
        <div className="workflow-guide__content">
          <ol className="workflow-guide__steps">
            <li data-status="completado"><span>Proyecto</span><strong>completado</strong></li>
            <li data-status={workflowStatus(project.montajes.length > 0)}><span>Montajes</span><strong>{workflowStatus(project.montajes.length > 0)}</strong></li>
            <li data-status={workflowStatus(project.operaciones.length > 0)}><span>Operaciones</span><strong>{workflowStatus(project.operaciones.length > 0)}</strong></li>
            <li data-status={workflowStatus(project.operaciones.length > 0 && project.operaciones.every((operation) => Boolean(operation.archivo_gcode)), project.operaciones.some((operation) => Boolean(operation.archivo_gcode)))}><span>Archivos G-code</span><strong>{workflowStatus(project.operaciones.length > 0 && project.operaciones.every((operation) => Boolean(operation.archivo_gcode)), project.operaciones.some((operation) => Boolean(operation.archivo_gcode)))}</strong></li>
            <li data-status={workflowStatus(project.operaciones.length > 0 && project.operaciones.every((operation) => Boolean(operation.analisis)), project.operaciones.some((operation) => Boolean(operation.analisis)))}><span>Análisis</span><strong>{workflowStatus(project.operaciones.length > 0 && project.operaciones.every((operation) => Boolean(operation.analisis)), project.operaciones.some((operation) => Boolean(operation.analisis)))}</strong></li>
            <li data-status={workflowStatus(Boolean(referenceSession?.referencia_z), Boolean(referenceSession?.origen_trabajo))}><span>Referencias</span><strong>{workflowStatus(Boolean(referenceSession?.referencia_z), Boolean(referenceSession?.origen_trabajo))}</strong></li>
            <li data-status={workflowStatus(Boolean(heightMap))}><span>Mapa</span><strong>{workflowStatus(Boolean(heightMap))}</strong></li>
            <li data-status={workflowStatus(Boolean(referenceSession?.lista_para_compensacion), Boolean(referenceSession?.motivo_invalidacion))}><span>Validación</span><strong>{workflowStatus(Boolean(referenceSession?.lista_para_compensacion), Boolean(referenceSession?.motivo_invalidacion))}</strong></li>
          </ol>
          <div className="workflow-progress-tree">
            {project.montajes.map((setup) => (
              <div key={setup.id}>
                <strong>{setup.nombre}</strong>
                {project.operaciones.filter((operation) => operation.setup_id === setup.id).map((operation) => (
                  <span key={operation.id}>{operation.analisis ? "✓" : operation.archivo_gcode ? "!" : "○"} {operation.nombre}</span>
                ))}
              </div>
            ))}
          </div>
          <button className="button" type="button" onClick={continueWorkflow}>Continuar con el siguiente paso</button>
        </div>
      </details>

      <article className="panel workspace-navigation-panel">
        <div className="toolbar-inline toolbar-inline--scrollable workspace-tabs" role="tablist" aria-label="Navegación del workspace">
          <button className={`toolbar-pill${activeView === "archivo" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => setActiveView("archivo")}>Archivo</button>
          <button className={`toolbar-pill${activeView === "trayectoria" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => setActiveView("trayectoria")}>Trayectoria</button>
          <button className={`toolbar-pill${activeView === "referencia" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => setActiveView("referencia")}>Referencia</button>
          <button className={`toolbar-pill${activeView === "mapa" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => setActiveView("mapa")}>Mapa de alturas</button>
                    <button className={`toolbar-pill${activeView === "ejecucion" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => setActiveView("ejecucion")}>Ejecución</button>
        </div>
      </article>

      {workspaceError ? <div className="panel alert alert--error">{workspaceError}</div> : null}

      <section className="workspace-view-panel">
        {activeView === "archivo" ? renderArchivo() : null}
        {activeView === "trayectoria" ? renderTrayectoria() : null}
        {activeView === "referencia" ? renderReferencia() : null}
        {activeView === "mapa" ? renderMapa() : null}
              {activeView === "ejecucion" ? renderEjecucion() : null}
      </section>
    </div>
  );
}
