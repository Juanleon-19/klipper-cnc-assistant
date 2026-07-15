import { useEffect, useMemo, useRef, useState } from "react";

import { useMachineStatus } from "../context/MachineContext";
import { HeightMapControlPanel } from "../features/heightmap/HeightMapControlPanel";
import { HeightMapHeatmap } from "../features/heightmap/HeightMapHeatmap";
import { HeightMapSurface3D } from "../features/heightmap/HeightMapSurface3D";
import { ToolpathViewer } from "../features/viewer/ToolpathViewer";
import { formatDate, formatFileSize, formatMillimeters } from "../lib/format";
import { ApiError, api, type OperationInput, type OperationUpdateInput } from "../lib/api";
import { parseFiniteNumber } from "../lib/numbers";
import { toneForStatus, translateFace, translateOperationType, translateStatus } from "../lib/ui";
import type {
  CompensationPreview,
  CompensatedGCodeResult,
  HeightMap,
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
  PhysicalMeshPoint,
  Bounds,
  OperationAnalysis,
} from "../types";
import { ProjectForm } from "./ProjectForm";
import { StatusBadge } from "./StatusBadge";

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
  initialView?: WorkspaceView;
};

export type WorkspaceView = "archivo" | "trayectoria" | "referencia" | "mapa" | "validacion" | "ejecucion";
type MapTab = "mapa2d" | "superficie3d" | "puntos" | "configuracion";
type HeightMode = "bruto" | "plano" | "residuo";
type HeightMapSource = "SIMULATED" | "MEASURED";

type ReferenceFieldErrors = Partial<Record<"x_mm" | "y_mm" | "z_mm", string>>;
type InputState = { x_mm: string; y_mm: string };
type ZInputState = { x_mm: string; y_mm: string; z_mm: string };

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
  initialView,
}: ProjectWorkspaceProps) {
  const [editingProject, setEditingProject] = useState(false);
  const [selectedOperationId, setSelectedOperationId] = useState<string | null>(pickDefaultOperation(project));
  const [selectedSetupId, setSelectedSetupId] = useState<string | null>(project?.montajes[0]?.id ?? null);
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
  const [physicalMap, setPhysicalMap] = useState<PhysicalMapPayload | null>(null);
  const [physicalMapHistory, setPhysicalMapHistory] = useState<Array<Record<string, unknown>>>([]);
  const [referenceSession, setReferenceSession] = useState<ReferenceSession | null>(null);
  const [compensationPreview, setCompensationPreview] = useState<CompensationPreview | null>(null);
  const [generatedGCode, setGeneratedGCode] = useState<CompensatedGCodeResult | null>(null);
  const [heightMapBusy, setHeightMapBusy] = useState(false);
  const [referenceBusy, setReferenceBusy] = useState(false);
  const [workspaceError, setWorkspaceError] = useState("");
  const [workOrigin, setWorkOrigin] = useState<InputState>({ x_mm: "0", y_mm: "0" });
  const [zReference, setZReference] = useState<ZInputState>({ x_mm: "0", y_mm: "0", z_mm: "0" });
  const [useWorkOriginXYForZ, setUseWorkOriginXYForZ] = useState(false);
  const [workOriginErrors, setWorkOriginErrors] = useState<ReferenceFieldErrors>({});
  const [zReferenceErrors, setZReferenceErrors] = useState<ReferenceFieldErrors>({});
  const workOriginRefs = useRef<Record<"x_mm" | "y_mm", HTMLInputElement | null>>({ x_mm: null, y_mm: null });
  const zReferenceRefs = useRef<Record<"x_mm" | "y_mm" | "z_mm", HTMLInputElement | null>>({ x_mm: null, y_mm: null, z_mm: null });
  const machine = useMachineStatus();
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
  const [probeStepInput, setProbeStepInput] = useState("0.05");
  const [probeSpeedInput, setProbeSpeedInput] = useState("60");
  const [probeRetractInput, setProbeRetractInput] = useState("1.0");
  const [meshExclusions, setMeshExclusions] = useState<PhysicalMapExclusion[]>([]);
  const [newExclusionShape, setNewExclusionShape] = useState<"rectangle" | "circle">("rectangle");
  const [pointFilter, setPointFilter] = useState<"ALL" | "PENDING" | "MEASURED" | "EXCLUDED" | "FAILED">("ALL");
  const [showAllTrajectoryOperations, setShowAllTrajectoryOperations] = useState(false);
  const [meshArmed, setMeshArmed] = useState(false);
  const [meshValidationMessage, setMeshValidationMessage] = useState("");
  const [executionState, setExecutionState] = useState<"PREFLIGHT" | "READY_TO_EXECUTE" | "UPLOADING" | "RUNNING" | "PAUSED" | "CANCELLED" | "COMPLETED">("PREFLIGHT");
  const [executionEvent, setExecutionEvent] = useState("Sin acciones de ejecución todavía.");
  const [machineSettingsInput, setMachineSettingsInput] = useState({
    reference_prep_z_mm: "115",
    reference_prep_z_feed_mm_min: "180",
    move_total_timeout_s: "180",
    no_progress_timeout_s: "60",
    position_tolerance_mm: "0.05",
    velocity_tolerance_mm_s: "0.02",
  });
  const [machineSettingsMessage, setMachineSettingsMessage] = useState("");

  useEffect(() => {
    if (!machine.isPhysical) {
      return;
    }
    void api.getMachineSettings().then((settings) => {
      setMachineSettingsInput({
        reference_prep_z_mm: String(settings.reference_prep_z_mm ?? 115),
        reference_prep_z_feed_mm_min: String(settings.reference_prep_z_feed_mm_min ?? 180),
        move_total_timeout_s: String(settings.move_total_timeout_s ?? 180),
        no_progress_timeout_s: String(settings.no_progress_timeout_s ?? 60),
        position_tolerance_mm: String(settings.position_tolerance_mm ?? 0.05),
        velocity_tolerance_mm_s: String(settings.velocity_tolerance_mm_s ?? 0.02),
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

  const selectedSetup = useMemo(
    () => project?.montajes.find((setup) => setup.id === selectedSetupId) ?? project?.montajes[0] ?? null,
    [project, selectedSetupId]
  );

  useEffect(() => {
    if (!project) {
      setSelectedSetupId(null);
      return;
    }
    if (selectedOperation) {
      setSelectedSetupId(selectedOperation.setup_id);
      return;
    }
    setSelectedSetupId((current) => project.montajes.some((setup) => setup.id === current) ? current : project.montajes[0]?.id ?? null);
  }, [project, selectedOperation]);

  useEffect(() => {
    if (!project || !selectedOperation) {
      setHeightMap(null);
      setPhysicalMap(null);
      setReferenceSession(null);
      setCompensationPreview(null);
      setGeneratedGCode(null);
      setGeneratedGCode(null);
      return;
    }

    const run = async () => {
      setWorkspaceError("");
      try {
        const [referencePayload, maybeMap, maybePhysicalMap] = await Promise.all([
          api.getReferenceSession(project.id, selectedOperation.id),
          api.getHeightMap(project.id, selectedOperation.id).catch((error) => {
            if (error instanceof Error && error.message.toLowerCase().includes("no existe")) {
              return null;
            }
            throw error;
          }),
          api.getPhysicalMap(project.id, selectedOperation.id).then((result) => result.payload).catch((error) => {
            if (error instanceof Error && error.message.toLowerCase().includes("no existe")) {
              return null;
            }
            return null;
          }),
        ]);
        setReferenceSession(referencePayload);
        setPhysicalMap(maybePhysicalMap);
        setHeightMap(maybeMap);
      } catch (error) {
        setWorkspaceError(error instanceof Error ? error.message : "No fue posible cargar el espacio de trabajo técnico.");
      }
    };

    void run();
  }, [project, selectedOperation]);

  useEffect(() => {
    if (!project || !selectedOperation || !physicalMap?.map_id) {
      return;
    }
    const execution = (physicalMap.execution ?? null) as { worker_active?: boolean } | null;
    const workerActive = execution?.worker_active === true;
    const shouldPoll = physicalMap.status === "MESH_PROBING" || workerActive;
    if (!shouldPoll) {
      return;
    }
    let cancelled = false;
    const poll = async () => {
      try {
        const nextMap = (await api.getPhysicalMap(project.id, selectedOperation.id)).payload;
        if (cancelled) {
          return;
        }
        setPhysicalMap(nextMap);
        setMapSource("MEASURED");
        if (nextMap.status === "MESH_COMPLETE") {
          const measured = await api.getPhysicalHeightMap(project.id, selectedOperation.id);
          if (cancelled) {
            return;
          }
          setHeightMap(measured);
          void api.getPhysicalMapHistory(project.id, selectedOperation.id).then((history) => {
            if (!cancelled) {
              setPhysicalMapHistory(history);
            }
          }).catch(() => undefined);
        }
      } catch {
        // Keep the last visible state and try again on the next tick.
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
  }, [project, selectedOperation, physicalMap?.map_id, physicalMap?.status, physicalMap?.execution]);

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
    if (!project || !selectedOperation) {
      return;
    }
    if (initialView) {
      setActiveView(initialView);
      return;
    }
    const stored = window.localStorage.getItem(workspaceViewStorageKey(project.id, selectedOperation.id));
    if (stored === "archivo" || stored === "trayectoria" || stored === "referencia" || stored === "mapa" || stored === "validacion" || stored === "ejecucion") {
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

  if (!project) {
    return (
      <div className="panel empty-state">
        <p className="eyebrow">Espacio de trabajo</p>
        <h2>Seleccione un proyecto</h2>
        <p>Abra un proyecto existente o cree uno nuevo para gestionar operaciones, referencias simuladas, mapa de alturas y validación.</p>
      </div>
    );
  }

  const payload: ProjectPayload = {
    nombre: project.nombre,
    material: project.material,
    doble_cara: project.doble_cara,
    eje_volteo: project.eje_volteo,
    agujeros_alineacion: project.agujeros_alineacion,
  };

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
      setCompensationPreview(null);
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
      setCompensationPreview(null);
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
      setCompensationPreview(null);
      setGeneratedGCode(null);
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
    setActiveView("validacion");
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

  const renderPhysicalReference = () => {
    const runtime = machine.runtime;
    const position = runtime?.klipper?.position as Record<string, unknown> | null | undefined;
    const livePosition = position?.live_position as Record<string, unknown> | null | undefined;
    const commandedPosition = position?.commanded_position as Record<string, unknown> | null | undefined;
    const lastMovement = runtime?.last_movement as Record<string, unknown> | null | undefined;
    const controller = runtime?.controller ?? {};
    const arduino = runtime?.arduino ?? {};
    const preparation = runtime?.preparation ?? {};
    const toolChange = runtime?.tool_change ?? {};
    const referencePrepZ = typeof preparation.reference_prep_z_mm === "number" ? preparation.reference_prep_z_mm : 115;
    const referencePrepZFeed = typeof preparation.reference_prep_z_feed_mm_min === "number" ? preparation.reference_prep_z_feed_mm_min : 180;
    const referencePrepXyFeed = typeof preparation.reference_prep_xy_feed_mm_min === "number" ? preparation.reference_prep_xy_feed_mm_min : 1800;
    const centerX = typeof preparation.center_x_mm === "number" ? preparation.center_x_mm : null;
    const centerY = typeof preparation.center_y_mm === "number" ? preparation.center_y_mm : null;
    const toolChangeX = typeof toolChange.x_mm === "number" ? toolChange.x_mm : 0;
    const toolChangeY = typeof toolChange.y_mm === "number" ? toolChange.y_mm : 0;
    const toolChangeZ = typeof toolChange.z_mm === "number" ? toolChange.z_mm : 115;
    const toolChangeZFeed = typeof toolChange.z_feed_mm_min === "number" ? toolChange.z_feed_mm_min : 180;
    const canConnect = machine.isPhysical && machine.runtimeState === "DISCONNECTED";
    const canInitialize = machine.isPhysical && ["DIAGNOSTIC", "READY_FOR_HOME", "HOMED", "ERROR", "CANCELLED"].includes(machine.runtimeState);
    const canEnableJog = machine.isPhysical && machine.runtimeState === "WAITING_FOR_XY_REFERENCE";
    const canArm = machine.isPhysical && machine.runtimeState === "WAITING_FOR_XY_REFERENCE";
    const canProbe = machine.isPhysical && ["WAITING_FOR_XY_REFERENCE", "REFERENCE_ARMED"].includes(machine.runtimeState);
    return (
      <div className="stack gap-md">
        <article className="panel">
          <div className="section-heading section-heading--stacked">
            <div>
              <p className="eyebrow">Preparación física del montaje</p>
              <h3>Referencia X/Y/Z medida</h3>
            </div>
            <StatusBadge tone={machine.isPhysical ? "success" : "warning"}>{machine.modeLabel}</StatusBadge>
          </div>
          <p className="muted">Coloque el origen X/Y real del G-code con joystick y mida Z con la sonda. La Z segura de traslado no es referencia Z ni profundidad de fresado.</p>
          {machine.lastError ? <div className="alert alert--warning">{machine.lastError}</div> : null}
          <div className="info-grid info-grid--double compact-grid">
            <div className="metric-box"><span>Estado</span><strong>{machine.runtimeState}</strong></div>
            <div className="metric-box"><span>Moonraker</span><strong>{machine.connected ? "conectado" : "desconectado"}</strong></div>
            <div className="metric-box"><span>Klipper</span><strong>{machine.klipperReady ? "ready" : "no ready"}</strong></div>
            <div className="metric-box"><span>Homing</span><strong>{machine.homedAxes || "sin ejes"}</strong></div>
            <div className="metric-box"><span>Arduino</span><strong>{machine.serialRecent ? "paquetes recientes" : "sin paquetes recientes"}</strong></div>
            <div className="metric-box"><span>Telemetría</span><strong>{machine.telemetryRecent ? "reciente" : "obsoleta"}</strong></div>
          </div>
        </article>

        <article className="panel">
          <div className="section-heading"><h3>1. Conexión y diagnóstico</h3></div>
          <p className="muted">Conecta Moonraker HTTP, WebSocket, Klipper y Arduino. En diagnóstico puede observar joystick, botón externo y sonda sin movimiento.</p>
          <div className="action-grid action-grid--inline">
            <button className="button" type="button" disabled={!canConnect || machine.refreshing} onClick={() => void machine.runMachineAction("connect")}>Conectar runtime</button>
            <button className="button button--ghost" type="button" disabled={!machine.isPhysical || machine.refreshing || machine.runtimeState === "DISCONNECTED"} onClick={() => void machine.runMachineAction("diagnostic")}>Modo diagnóstico</button>
          </div>
          <dl className="definition-grid definition-grid--compact">
            <div><dt>Puerto Arduino</dt><dd>{String(arduino.port ?? "-")}</dd></div>
            <div><dt>Paquetes válidos</dt><dd>{String(arduino.valid_packets ?? 0)}</dd></div>
            <div><dt>Dirección joystick</dt><dd>{String(controller.direction ?? "CENTER")}</dd></div>
            <div><dt>Botón externo</dt><dd>{controller.external_button ? "pulsado" : "reposo"}</dd></div>
            <div><dt>Sonda</dt><dd>{controller.probe ? "contacto" : "inactiva"}</dd></div>
          </dl>
        </article>

        <article className="panel">
          <div className="section-heading"><h3>2. Home, Z de preparación y centro</h3></div>
          <p className="muted">El backend envía G28, confirma `toolhead.homed_axes`, mueve primero Z a la altura de preparación configurada y después mueve X/Y al centro real calculado desde límites Klipper.</p>
          <div className="info-grid info-grid--double compact-grid">
            <div className="metric-box"><span>Z de preparación</span><strong>{formatMillimeters(referencePrepZ, 3)}</strong></div>
            <div className="metric-box"><span>Velocidad Z</span><strong>{referencePrepZFeed.toFixed(0)} mm/min · {(referencePrepZFeed / 60).toFixed(3)} mm/s</strong></div>
            <div className="metric-box"><span>Centro calculado</span><strong>X {formatMillimeters(centerX, 3)} · Y {formatMillimeters(centerY, 3)}</strong></div>
            <div className="metric-box"><span>Velocidad centro X/Y</span><strong>{referencePrepXyFeed.toFixed(0)} mm/min · {(referencePrepXyFeed / 60).toFixed(3)} mm/s</strong></div>
            <div className="metric-box"><span>Posición actual</span><strong>X {formatMillimeters(typeof position?.x === "number" ? position.x : null, 3)} · Y {formatMillimeters(typeof position?.y === "number" ? position.y : null, 3)} · Z {formatMillimeters(typeof position?.z === "number" ? position.z : null, 3)}</strong></div>
            <div className="metric-box"><span>Z en vivo</span><strong>{formatMillimeters(typeof livePosition?.z === "number" ? livePosition.z : null, 3)}</strong></div>
            <div className="metric-box"><span>Z comandada</span><strong>{formatMillimeters(typeof commandedPosition?.z === "number" ? commandedPosition.z : null, 3)}</strong></div>
            <div className="metric-box"><span>Velocidad observada</span><strong>{typeof position?.velocity === "number" ? `${position.velocity.toFixed(3)} mm/s` : "-"}</strong></div>
            <div className="metric-box"><span>Fuente de posición</span><strong>{String(position?.source ?? "-")}</strong></div>
            <div className="metric-box"><span>Objetivo</span><strong>X {formatMillimeters(centerX, 3)} · Y {formatMillimeters(centerY, 3)} · Z {formatMillimeters(referencePrepZ, 3)}</strong></div>
            <div className="metric-box"><span>Timeout calculado</span><strong>{typeof lastMovement?.timeout_s === "number" ? `${lastMovement.timeout_s.toFixed(1)} s` : "-"}</strong></div>
            <div className="metric-box"><span>Z viva anterior</span><strong>{formatMillimeters(typeof lastMovement?.previous_live_z === "number" ? lastMovement.previous_live_z : null, 3)}</strong></div>
            <div className="metric-box"><span>Z viva actual</span><strong>{formatMillimeters(typeof lastMovement?.current_live_z === "number" ? lastMovement.current_live_z : null, 3)}</strong></div>
            <div className="metric-box"><span>Distancia anterior</span><strong>{formatMillimeters(typeof lastMovement?.previous_distance_mm === "number" ? lastMovement.previous_distance_mm : null, 3)}</strong></div>
            <div className="metric-box"><span>Distancia actual</span><strong>{formatMillimeters(typeof lastMovement?.current_distance_mm === "number" ? lastMovement.current_distance_mm : null, 3)}</strong></div>
            <div className="metric-box"><span>Fuente viva</span><strong>{String(lastMovement?.live_position_source ?? "-")}</strong></div>
            <div className="metric-box"><span>Muestras alejándose</span><strong>{typeof lastMovement?.consecutive_away_samples === "number" ? String(lastMovement.consecutive_away_samples) : "-"}</strong></div>
          </div>
          <details className="advanced-settings">
            <summary>Configuración avanzada de movimiento</summary>
            <div className="form-grid form-grid--dense">
              <label>Z de preparación (mm)<input value={machineSettingsInput.reference_prep_z_mm} inputMode="decimal" onChange={(event) => setMachineSettingsInput((current) => ({ ...current, reference_prep_z_mm: event.target.value }))} /></label>
              <label>Velocidad Z de preparación (mm/min)<input value={machineSettingsInput.reference_prep_z_feed_mm_min} inputMode="decimal" onChange={(event) => setMachineSettingsInput((current) => ({ ...current, reference_prep_z_feed_mm_min: event.target.value }))} /></label>
              <label>Timeout total de movimiento (s)<input value={machineSettingsInput.move_total_timeout_s} inputMode="decimal" onChange={(event) => setMachineSettingsInput((current) => ({ ...current, move_total_timeout_s: event.target.value }))} /></label>
              <label>Timeout sin progreso (s)<input value={machineSettingsInput.no_progress_timeout_s} inputMode="decimal" onChange={(event) => setMachineSettingsInput((current) => ({ ...current, no_progress_timeout_s: event.target.value }))} /></label>
              <label>Tolerancia de posición (mm)<input value={machineSettingsInput.position_tolerance_mm} inputMode="decimal" onChange={(event) => setMachineSettingsInput((current) => ({ ...current, position_tolerance_mm: event.target.value }))} /></label>
              <label>Tolerancia de velocidad (mm/s)<input value={machineSettingsInput.velocity_tolerance_mm_s} inputMode="decimal" onChange={(event) => setMachineSettingsInput((current) => ({ ...current, velocity_tolerance_mm_s: event.target.value }))} /></label>
            </div>
            <div className="action-grid action-grid--inline">
              <button className="button button--ghost" type="button" disabled={!machine.isPhysical || referenceBusy || machine.refreshing} onClick={() => void saveMachineSettings()}>Guardar configuración</button>
            </div>
            {machineSettingsMessage ? <p className="muted">{machineSettingsMessage}</p> : null}
          </details>
          <button className="button" type="button" disabled={!canInitialize || referenceBusy || machine.refreshing} onClick={() => void withPhysicalReferenceAction(async () => { await machine.runMachineAction("initialize", referencePrepZ); })}>Realizar homing, subir Z e ir al centro</button>
          <div className="workflow-steps-grid">
            {(runtime?.initialization_steps ?? []).map((step, index) => (
              <div className="workflow-step-card" key={`${String(step.name)}-${index}`}>
                <div className="workflow-step-card__header"><span className="workflow-step">{index + 1}</span><div><strong>{String(step.name)}</strong><p className="muted">{String(step.detail ?? "")}</p></div><StatusBadge tone={String(step.status) === "ok" ? "success" : "warning"}>{String(step.status)}</StatusBadge></div>
              </div>
            ))}
          </div>
        </article>

        <article className="panel">
          <div className="section-heading"><h3>3. Posicionar X0/Y0 del G-code</h3></div>
          <p className="muted">Habilite joystick X/Y y coloque la herramienta exactamente sobre el X0/Y0 generado por FlatCAM. El jog es cardinal discreto y no mueve Z.</p>
          <div className="info-grid info-grid--double compact-grid">
            <div className="metric-box"><span>X máquina</span><strong>{formatMillimeters(typeof position?.x === "number" ? position.x : null, 3)}</strong></div>
            <div className="metric-box"><span>Y máquina</span><strong>{formatMillimeters(typeof position?.y === "number" ? position.y : null, 3)}</strong></div>
            <div className="metric-box"><span>Z máquina</span><strong>{formatMillimeters(typeof position?.z === "number" ? position.z : null, 3)}</strong></div>
            <div className="metric-box"><span>Modo jog</span><strong>{String(controller.jog_mode ?? "FINE")}</strong></div>
          </div>
          <button className="button" type="button" disabled={!canEnableJog || referenceBusy || machine.refreshing} onClick={() => void machine.runMachineAction("manual-on")}>Habilitar joystick X/Y</button>
        </article>

        <article className="panel">
          <div className="section-heading"><h3>Posición segura de cambio de herramienta</h3></div>
          <p className="muted">Estas son coordenadas de máquina, no el origen X0/Y0 de FlatCAM ni la referencia Z de la PCB.</p>
          <div className="info-grid info-grid--double compact-grid">
            <div className="metric-box"><span>X cambio</span><strong>{formatMillimeters(toolChangeX, 3)}</strong></div>
            <div className="metric-box"><span>Y cambio</span><strong>{formatMillimeters(toolChangeY, 3)}</strong></div>
            <div className="metric-box"><span>Z cambio</span><strong>{formatMillimeters(toolChangeZ, 3)}</strong></div>
            <div className="metric-box"><span>Velocidad Z cambio</span><strong>{toolChangeZFeed.toFixed(0)} mm/min · {(toolChangeZFeed / 60).toFixed(3)} mm/s</strong></div>
            <div className="metric-box"><span>Orden</span><strong>Z primero, luego X/Y</strong></div>
          </div>
          <div className="action-grid action-grid--inline">
            <button className="button button--ghost" type="button" disabled={!machine.isPhysical || referenceBusy || machine.refreshing} onClick={() => window.alert("Modifique TOOL_CHANGE_X_MM, TOOL_CHANGE_Y_MM y TOOL_CHANGE_Z_MM en la configuración del servicio y reinicie la aplicación para persistir los cambios.")}>Modificar posición de cambio</button>
            <button className="button" type="button" disabled={!machine.isPhysical || referenceBusy || machine.refreshing || !machine.homedAxes} onClick={() => void withPhysicalReferenceAction(async () => { await machine.runMachineAction("tool-change-position"); })}>Ir a posición de cambio</button>
          </div>
        </article>

        <article className="panel">
          <div className="section-heading"><h3>4. Medir referencia</h3></div>
          <p className="muted">Puede armar la referencia para usar el botón externo o lanzar el sondeo directamente desde pantalla. La aplicación captura X/Y actuales, baja Z por pasos discretos, detecta contacto, retrae y guarda origen X/Y más referencia Z `MEASURED`.</p>
          <div className="action-grid action-grid--inline">
            <button className="button button--ghost" type="button" disabled={!canArm || referenceBusy || machine.refreshing} onClick={() => void machine.runMachineAction("probe-request")}>Armar referencia</button>
            <button className="button" type="button" disabled={!canProbe || referenceBusy || machine.refreshing || !selectedOperation} onClick={() => void withPhysicalReferenceAction(async () => {
              await api.confirmProbe();
              await machine.refreshRuntime();
              if (!selectedOperation) return;
              await api.capturePhysicalWorkOrigin(project.id, selectedOperation.id);
              return await api.capturePhysicalZReferenceFromProbe(project.id, selectedOperation.id);
            })}>Sondear referencia ahora</button>
            <button className="button button--ghost" type="button" disabled={!machine.isPhysical || machine.refreshing} onClick={() => void machine.runMachineAction("cancel")}>Cancelar</button>
          </div>
          <div className="info-grid info-grid--double compact-grid">
            <div className="metric-box"><span>Origen X/Y</span><strong>{referenceSession?.origen_trabajo ? `${referenceSession.origen_trabajo.x_mm}, ${referenceSession.origen_trabajo.y_mm}` : "pendiente"}</strong></div>
            <div className="metric-box"><span>Captura origen</span><strong>{formatCapturedPosition(referenceSession?.origen_trabajo?.posicion_captura)}</strong></div>
            <div className="metric-box"><span>Referencia Z</span><strong>{referenceSession?.referencia_z?.z_mm ?? "pendiente"}</strong></div>
            <div className="metric-box"><span>Captura referencia</span><strong>{formatCapturedPosition(referenceSession?.referencia_z?.posicion_captura)}</strong></div>
            <div className="metric-box"><span>Herramienta</span><strong>{selectedOperation?.herramienta ?? selectedOperation?.tool_id ?? "sin herramienta"}</strong></div>
            <div className="metric-box"><span>Fuente</span><strong>{String(referenceSession?.referencia_z?.fuente ?? "-")}</strong></div>
          </div>
        </article>
      </div>
    );
  };

  const renderSimulatedReferencia = () => (
    <div className="stack gap-md">
      <article className="panel">
        <div className="section-heading section-heading--stacked">
          <div>
            <p className="eyebrow">Referencia simulada</p>
            <h3>Flujo simulado de preparación</h3>
          </div>
          <p className="muted">Modo SIMULADO: confirma referencias manuales sin abrir hardware ni enviar movimientos.</p>
        </div>
        <div className="machine-banner machine-banner--large" role="status">
          <span className="machine-banner__dot" aria-hidden="true" />
          <span>MODO SIMULADO - no se enviará movimiento a la máquina</span>
        </div>
        {referenceSession?.motivo_invalidacion ? <div className="alert alert--warning">{referenceSession.motivo_invalidacion}</div> : null}
        <div className="workflow-steps-grid">
          {(referenceSession?.pasos ?? []).map(renderReferenceStep)}
        </div>
      </article>

      <article className="panel">
        <div className="section-heading"><h3>1. Referencia de máquina</h3></div>
        <p className="muted">Ubica el sistema de coordenadas de la máquina. En simulación se confirma una vez por sesión.</p>
        <button className="button" type="button" disabled={referenceBusy || referenceSession?.machine_reference.confirmada || !selectedOperation} onClick={() => void withReferenceAction(() => api.confirmMachineReference(project.id, selectedOperation!.id))}>
          {referenceSession?.machine_reference.confirmada ? "Ya confirmada en simulación" : "Confirmar en simulación"}
        </button>
      </article>

      <article className="panel">
        <div className="section-heading"><h3>2. Origen de trabajo X/Y</h3></div>
        <p className="muted">Define dónde queda X0 Y0 del G-code respecto al montaje de la placa.</p>
        <div className="form-grid">
          <label>X (mm)<input ref={(node) => { workOriginRefs.current.x_mm = node; }} type="number" inputMode="decimal" value={workOrigin.x_mm} onChange={(event) => { setWorkOrigin((current) => ({ ...current, x_mm: event.target.value })); setWorkOriginErrors((current) => ({ ...current, x_mm: undefined })); }} />{workOriginErrors.x_mm ? <span className="form-error">{workOriginErrors.x_mm}</span> : null}</label>
          <label>Y (mm)<input ref={(node) => { workOriginRefs.current.y_mm = node; }} type="number" inputMode="decimal" value={workOrigin.y_mm} onChange={(event) => { setWorkOrigin((current) => ({ ...current, y_mm: event.target.value })); setWorkOriginErrors((current) => ({ ...current, y_mm: undefined })); }} />{workOriginErrors.y_mm ? <span className="form-error">{workOriginErrors.y_mm}</span> : null}</label>
        </div>
        <button className="button" type="button" disabled={referenceBusy || !selectedOperation} onClick={() => void submitWorkOrigin()}>Confirmar en simulación</button>
      </article>

      <article className="panel">
        <div className="section-heading"><h3>3. Referencia Z</h3></div>
        <p className="muted">Define la altura que se considera Z0 para esta herramienta en simulación.</p>
        <label className="toggle-field"><input type="checkbox" checked={useWorkOriginXYForZ} onChange={(event) => setUseWorkOriginXYForZ(event.target.checked)} /><span>Usar la misma posición X/Y del origen de trabajo</span></label>
        <div className="form-grid">
          <label>X (mm)<input ref={(node) => { zReferenceRefs.current.x_mm = node; }} type="number" inputMode="decimal" value={useWorkOriginXYForZ ? workOrigin.x_mm : zReference.x_mm} disabled={useWorkOriginXYForZ} onChange={(event) => { setZReference((current) => ({ ...current, x_mm: event.target.value })); setZReferenceErrors((current) => ({ ...current, x_mm: undefined })); }} />{zReferenceErrors.x_mm ? <span className="form-error">{zReferenceErrors.x_mm}</span> : null}</label>
          <label>Y (mm)<input ref={(node) => { zReferenceRefs.current.y_mm = node; }} type="number" inputMode="decimal" value={useWorkOriginXYForZ ? workOrigin.y_mm : zReference.y_mm} disabled={useWorkOriginXYForZ} onChange={(event) => { setZReference((current) => ({ ...current, y_mm: event.target.value })); setZReferenceErrors((current) => ({ ...current, y_mm: undefined })); }} />{zReferenceErrors.y_mm ? <span className="form-error">{zReferenceErrors.y_mm}</span> : null}</label>
          <label>Z de referencia (mm)<input ref={(node) => { zReferenceRefs.current.z_mm = node; }} type="number" inputMode="decimal" value={zReference.z_mm} onChange={(event) => { setZReference((current) => ({ ...current, z_mm: event.target.value })); setZReferenceErrors((current) => ({ ...current, z_mm: undefined })); }} />{zReferenceErrors.z_mm ? <span className="form-error">{zReferenceErrors.z_mm}</span> : null}</label>
        </div>
        <button className="button" type="button" disabled={referenceBusy || !selectedOperation} onClick={() => void submitZReference()}>Confirmar en simulación</button>
      </article>

      <article className="panel"><div className="section-heading"><h3>4. Región sondeable</h3></div><p className="muted">La región sondeable se configura desde la pestaña Mapa de alturas.</p>{heightMap ? <p className="mono-text">{JSON.stringify(heightMap.probe_region)}</p> : <p className="muted">Aún no hay región configurada.</p>}</article>
      <article className="panel"><div className="section-heading"><h3>5. Mapa</h3></div><p className="muted">{referenceSession?.pasos.find((step) => step.id === "mapa")?.detalle ?? "Aún no hay mapa disponible."}</p><p className="mono-text">Mapa actual: {heightMap ? `${heightMap.fuente_datos} · v${heightMap.version}` : "no disponible"}</p></article>
      <article className="panel"><div className="section-heading"><h3>6. Validación</h3></div><p className="muted">{referenceSession?.pasos.find((step) => step.id === "validacion")?.detalle ?? "La validación del mapa sigue pendiente."}</p><button className="button" type="button" disabled={referenceBusy || !selectedOperation || !heightMap} onClick={() => void withReferenceAction(() => api.validateHeightMap(project.id, selectedOperation!.id))}>Confirmar en simulación</button></article>
    </div>
  );

  const renderReferencia = () => machine.isPhysical ? renderPhysicalReference() : renderSimulatedReferencia();

  const withPhysicalMapAction = async (action: () => Promise<PhysicalMapPayload | null>) => {
    setHeightMapBusy(true);
    setWorkspaceError("");
    try {
      const result = await action();
      const nextMap = result ?? physicalMap;
      if (result) {
        setPhysicalMap(result);
        setMapSource("MEASURED");
      }
      if (project && selectedOperation && nextMap?.status === "MESH_COMPLETE") {
        const measured = await api.getPhysicalHeightMap(project.id, selectedOperation.id);
        setHeightMap(measured);
        setMapSource("MEASURED");
      } else if (result) {
        setHeightMap(null);
      }
      if (project && selectedOperation) {
        void api.getPhysicalMapHistory(project.id, selectedOperation.id).then(setPhysicalMapHistory).catch(() => undefined);
      }
      setCompensationPreview(null);
      setGeneratedGCode(null);
    } catch (error) {
      setWorkspaceError(error instanceof Error ? error.message : "No fue posible actualizar el mapa físico medido.");
    } finally {
      setHeightMapBusy(false);
    }
  };

  const invalidateMeshPreview = () => {
    setMeshArmed(false);
    setMeshSuggestion(null);
    setMeshValidationMessage(physicalMap?.points?.some((point) => point.status === "MEASURED")
      ? "Existe una medición parcial. Cambiar la cuadrícula creará una nueva versión de malla. Los puntos medidos anteriores se conservarán en el historial, pero no pertenecerán a la nueva cuadrícula."
      : "");
  };

  const physicalFailedPoints = physicalMap?.points?.filter((point) => point.status === "FAILED" || point.status === "RETRY_REQUIRED").length ?? 0;
  const physicalMapId = typeof physicalMap?.map_id === "string" ? physicalMap.map_id : "";

  const renderMapa = () => {
    const effectiveMapSource: HeightMapSource = machine.isPhysical ? "MEASURED" : mapSource;
    const physicalReady = machine.isPhysical && Boolean(selectedOperation && referenceSession?.origen_trabajo && referenceSession?.referencia_z);
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
    const executablePoints = physicalMap?.executable_point_count ?? physicalMap?.points?.filter((point) => point.status !== "EXCLUDED").length ?? plannedPoints;
    const excludedPoints = physicalMap?.excluded_count ?? physicalMap?.points?.filter((point) => point.status === "EXCLUDED").length ?? 0;
    const hasReferencePoint = (physicalMap?.points ?? []).some((point) => point.role === "REFERENCE");
    const filteredPhysicalPoints = (physicalMap?.points ?? []).filter((point) => {
      if (pointFilter === "ALL") return true;
      if (pointFilter === "FAILED") return point.status === "FAILED" || point.status === "RETRY_REQUIRED";
      if (pointFilter === "PENDING") return ["PENDING", "MOVING", "PROBING"].includes(point.status);
      return point.status === pointFilter;
    });
    const physicalPlanPayload = { grid_mode: gridDefinitionMode, rows, columns, edge_margin_left_mm: edgeLeft, edge_margin_right_mm: edgeRight, edge_margin_bottom_mm: edgeBottom, edge_margin_top_mm: edgeTop, exclusions: meshExclusions, max_spacing_mm: parsePositive(meshSpacingInput), margin_mm: 0, safe_z_mm: parsePositive(safeZInput), probe_step_mm: parsePositive(probeStepInput), probe_feed_mm_min: parsePositive(probeSpeedInput), retract_mm: parsePositive(probeRetractInput) };
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
      setMeshArmed(false);
    };
    const updateExclusion = (id: string, patch: Partial<PhysicalMapExclusion>) => {
      setMeshExclusions((current) => current.map((item) => item.id === id ? { ...item, ...patch } : item));
      setMeshArmed(false);
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
              <StatusBadge tone={physicalMap?.status === "MESH_COMPLETE" ? "success" : physicalMap ? "info" : "neutral"}>{physicalMap?.status ?? "sin mapa medido"}</StatusBadge>
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
                    <button className="button button--ghost" type="button" disabled={!selectedOperation || heightMapBusy} onClick={async () => {
                      if (!selectedOperation) return;
                      setHeightMapBusy(true);
                      setWorkspaceError("");
                      try {
                        const suggestion = await api.suggestPhysicalMap(project.id, selectedOperation.id, { grid_mode: "suggested", rows, columns, edge_margin_left_mm: edgeLeft, edge_margin_right_mm: edgeRight, edge_margin_bottom_mm: edgeBottom, edge_margin_top_mm: edgeTop, exclusions: meshExclusions, max_spacing_mm: parsePositive(meshSpacingInput), margin_mm: 0, safe_z_mm: parsePositive(safeZInput), probe_step_mm: parsePositive(probeStepInput), probe_feed_mm_min: parsePositive(probeSpeedInput), retract_mm: parsePositive(probeRetractInput) });
                        setMeshSuggestion(suggestion);
                        setMeshRowsInput(String(suggestion.rows));
                        setMeshColumnsInput(String(suggestion.columns));
                      } catch (error) {
                        setWorkspaceError(error instanceof Error ? error.message : "No fue posible generar la propuesta de malla.");
                      } finally {
                        setHeightMapBusy(false);
                      }
                    }}>Ver propuesta</button>
                    <button className="button" type="button" disabled={!meshSuggestion} onClick={() => { if (!meshSuggestion) return; setMeshRowsInput(String(meshSuggestion.rows)); setMeshColumnsInput(String(meshSuggestion.columns)); setMeshValidationMessage("Propuesta automática aceptada. Regenerar vista previa antes de confirmar sondeo."); }}>Aceptar sugerencia</button>
                  </div>
                </div>
              )}
            </div>
            <div className="form-grid form-grid--dense">
              <label>Z segura de traslado (mm)<input value={safeZInput} inputMode="decimal" onChange={(event) => { setSafeZInput(event.target.value); invalidateMeshPreview(); }} /></label>
              <label>Paso de sonda (mm)<input value={probeStepInput} inputMode="decimal" onChange={(event) => { setProbeStepInput(event.target.value); invalidateMeshPreview(); }} /></label>
              <label>Velocidad de sonda (mm/min)<input value={probeSpeedInput} inputMode="decimal" onChange={(event) => { setProbeSpeedInput(event.target.value); invalidateMeshPreview(); }} /></label>
              <label>Retracto (mm)<input value={probeRetractInput} inputMode="decimal" onChange={(event) => { setProbeRetractInput(event.target.value); invalidateMeshPreview(); }} /></label>
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
              <div className="metric-box"><span>Puntos totales</span><strong>{physicalMap?.point_count ?? plannedPoints}</strong></div>
              <div className="metric-box"><span>Excluidos</span><strong>{excludedPoints}</strong></div>
              <div className="metric-box"><span>Ejecutables</span><strong>{executablePoints}</strong></div>
              <div className="metric-box"><span>Medidos</span><strong>{physicalMap?.points?.filter((point) => point.status === "MEASURED").length ?? 0}</strong></div>
              <div className="metric-box"><span>Pendientes</span><strong>{physicalMap?.points?.filter((point) => ["PENDING", "MOVING", "PROBING"].includes(point.status)).length ?? 0}</strong></div>
              <div className="metric-box"><span>Separación X</span><strong>{formatMillimeters(physicalMap?.grid?.dx_mm ?? (columns > 1 ? probeWidth / (columns - 1) : null), 3)}</strong></div>
              <div className="metric-box"><span>Separación Y</span><strong>{formatMillimeters(physicalMap?.grid?.dy_mm ?? (rows > 1 ? probeHeight / (rows - 1) : null), 3)}</strong></div>
              <div className="metric-box"><span>Z segura</span><strong>{formatMillimeters(parsePositive(safeZInput), 3)}</strong></div>
              <div className="metric-box"><span>Paso / velocidad</span><strong>{formatMillimeters(parsePositive(probeStepInput), 3)} · {formatMillimeters(parsePositive(probeSpeedInput), 0)}/min</strong></div>
              <div className="metric-box"><span>Retracto</span><strong>{formatMillimeters(parsePositive(probeRetractInput), 3)}</strong></div>
            </div>
            {probeWidth <= 0 || probeHeight <= 0 ? <div className="alert alert--warning">El retiro de los bordes deja una región de sondeo inválida. Reduzca los valores o revise las dimensiones del material.</div> : null}
            {meshValidationMessage ? <div className="alert alert--info">{meshValidationMessage}</div> : null}
            <div className="action-grid">
              <button className="button" type="button" disabled={heightMapBusy || !selectedOperation || probeWidth <= 0 || probeHeight <= 0} onClick={() => void withPhysicalMapAction(async () => {
                if (!selectedOperation) return null;
                const result = physicalReady
                  ? await api.planPhysicalMapFromReference(project.id, selectedOperation.id, physicalPlanPayload)
                  : await api.previewPhysicalMap(project.id, selectedOperation.id, physicalPlanPayload);
                const payload = result.payload;
                const total = payload.point_count ?? rows * columns;
                setActiveMapTab("mapa2d"); setMeshArmed(false); setMeshValidationMessage(payload.configuration_change_warning ?? (physicalReady ? `Vista previa generada: ${rows} filas × ${columns} columnas, ${total} puntos. Revise retiro, exclusiones, puntos y recorrido antes de armar.` : `Vista previa generada: ${rows} filas × ${columns} columnas, ${total} puntos. Vista previa en coordenadas PCB. Complete la referencia para calcular las coordenadas CNC.`)); return payload;
              })}>1. Generar vista previa de malla</button>
              <button className="button" type="button" disabled={!physicalMapId} onClick={() => setMeshValidationMessage(physicalFailedPoints > 0 ? `La malla tiene ${physicalFailedPoints} punto(s) fallidos o pendientes de reintento.` : "Cobertura geométrica revisada. No se extrapola fuera de la región interior ni sobre exclusiones.")}>2. Validar límites</button>
              <button className="button" type="button" disabled={!physicalMapId || physicalMap?.status === "MESH_COMPLETE"} onClick={() => { setMeshArmed(true); setMeshValidationMessage("Sondeo armado. Una sola confirmación ejecutará todos los puntos ejecutables de la malla."); }}>3. Armar sondeo</button>
              <button className="button" type="button" disabled={heightMapBusy || !physicalMapId || !meshArmed || physicalMap?.status === "MESH_COMPLETE"} onClick={() => void withPhysicalMapAction(async () => (await api.executeAllPhysicalMapPoints(project.id, physicalMapId)).payload)}>4. Iniciar sondeo automático</button>
              <button className="button button--ghost" type="button" disabled={!physicalMapId} onClick={() => void withPhysicalMapAction(async () => {
                if (!physicalMapId) return null;
                const result = await api.repeatPhysicalMap(project.id, physicalMapId);
                setActiveMapTab("mapa2d"); setMeshArmed(false); setMeshValidationMessage("Mapa anterior archivado. Nueva versión vacía generada con punto #0 X0/Y0 y todos los nodos pendientes. Confirme antes de mover."); return result.payload;
              })} title="Conserva origen X/Y y receta; archiva el mapa actual y vuelve a medir referencia y nodos.">Repetir medición completa</button>
              <button className="button button--ghost" type="button" disabled={!physicalMap} onClick={() => { setPhysicalMap(null); setHeightMap(null); setMeshArmed(false); setMeshValidationMessage("Vista previa limpia. La configuración y los mapas medidos no se borraron."); }}>Limpiar vista previa</button>
              <button className="button button--ghost" type="button" disabled={heightMapBusy || !physicalMapId} onClick={() => void withPhysicalMapAction(async () => (await api.pausePhysicalMap(project.id, physicalMapId)).payload)}>Pausar</button>
              <button className="button button--ghost" type="button" disabled={heightMapBusy || !physicalMapId} onClick={() => void withPhysicalMapAction(async () => (await api.resumePhysicalMap(project.id, physicalMapId)).payload)}>Reanudar</button>
              <button className="button button--ghost" type="button" disabled={heightMapBusy || !physicalMapId || physicalFailedPoints === 0} onClick={() => void withPhysicalMapAction(async () => (await api.executeAllPhysicalMapPoints(project.id, physicalMapId)).payload)}>Reintentar puntos fallidos</button>
              <button className="button button--ghost button--danger" type="button" disabled={heightMapBusy || !physicalMapId} onClick={() => void withPhysicalMapAction(async () => (await api.cancelPhysicalMap(project.id, physicalMapId)).payload)}>Cancelar</button>
            </div>
          </article>
        ) : null}

        {heightMap ? (
          <article className="panel"><div className="section-heading section-heading--stacked"><div><p className="eyebrow">Métricas</p><h3>Alturas de la superficie</h3></div><StatusBadge tone={heightMap.etiqueta_simulada ? "warning" : "success"}>{heightMap.etiqueta_simulada ? "SIMULADO" : "MEASURED"}</StatusBadge></div><div className="info-grid info-grid--double compact-grid"><div className="metric-box"><span>Z mínima</span><strong>{formatMillimeters(heightMap.estadisticas.altura_min_mm, 4)}</strong></div><div className="metric-box"><span>Z máxima</span><strong>{formatMillimeters(heightMap.estadisticas.altura_max_mm, 4)}</strong></div><div className="metric-box"><span>Rango</span><strong>{formatMillimeters(heightMap.estadisticas.rango_alturas_mm, 4)}</strong></div><div className="metric-box"><span>Valor de referencia</span><strong>{formatMillimeters(heightMap.estadisticas.valor_referencia_mm, 4)}</strong></div><div className="metric-box"><span>RMS</span><strong>{formatMillimeters(heightMap.estadisticas.desviacion_rms_respecto_plano_mm, 4)}</strong></div><div className="metric-box"><span>Residuo máximo</span><strong>{formatMillimeters(heightMap.estadisticas.residuo_maximo_mm, 4)}</strong></div></div></article>
        ) : null}

        {activeMapTab === "mapa2d" && (heightMap || physicalMap) ? <HeightMapHeatmap material={project.material} heightMap={heightMap} mode={heightMode} meshPoints={physicalMap?.points ?? physicalMap?.local_points ?? []} exclusions={physicalMap?.exclusions ?? meshExclusions} probeRegion={physicalMap?.local_region ?? physicalMap?.probe_region ?? heightMap?.probe_region ?? null} coordinateMode={coordinateMode} machineOrigin={typeof physicalMap?.machine_origin_x === "number" && typeof physicalMap?.machine_origin_y === "number" ? { x_mm: physicalMap.machine_origin_x, y_mm: physicalMap.machine_origin_y } : null} previewMessage={physicalMap?.warnings?.[0] ?? null} /> : null}
        {activeMapTab === "superficie3d" && heightMap ? <HeightMapSurface3D heightMap={heightMap} mode={heightMode} /> : null}
        {activeMapTab === "puntos" ? (
          <article className="panel"><div className="section-heading section-heading--stacked"><div><p className="eyebrow">Puntos de malla</p><h3>Lecturas y estados</h3></div><div className="map-segmented" aria-label="Filtro de puntos">{(["ALL", "PENDING", "MEASURED", "EXCLUDED", "FAILED"] as const).map((filter) => <button key={filter} className={`map-segment-button${pointFilter === filter ? " map-segment-button--active" : ""}`} type="button" onClick={() => setPointFilter(filter)}>{filter === "ALL" ? "Todos" : filter === "PENDING" ? "Pendientes" : filter === "MEASURED" ? "Medidos" : filter === "EXCLUDED" ? "Excluidos" : "Fallidos"}</button>)}</div></div>{filteredPhysicalPoints.length ? <div className="point-card-grid">{filteredPhysicalPoints.map((point: PhysicalMeshPoint) => <div className="mesh-point-card" key={point.index}><strong>{point.role === "REFERENCE" ? "Punto #0 — Referencia X0/Y0" : `Punto #${hasReferencePoint ? point.index : point.index + 1}`}</strong><span>Fila {typeof point.row === "number" && point.row >= 0 ? point.row + 1 : "-"}</span><span>Columna {typeof point.column === "number" && point.column >= 0 ? point.column + 1 : "-"}</span><span>PCB X/Y: {formatMillimeters(point.x_local, 3)} / {formatMillimeters(point.y_local, 3)}</span><span>CNC X/Y: {formatMillimeters(point.x_machine ?? null, 3)} / {formatMillimeters(point.y_machine ?? null, 3)}</span><span>Z medida: {formatMillimeters(point.z_measured_abs ?? point.z_measured ?? null, 3)}</span><span>Delta Z: {formatMillimeters(point.delta_z ?? null, 3)}</span><span>Estado: {formatPointStatus(point.status)}</span><span>Intentos: {point.attempts ?? 0}</span><span>Duración: {typeof point.duration_s === "number" ? `${point.duration_s.toFixed(3)} s` : "-"}</span>{point.error || point.last_error ? <span>Error: {point.error ?? point.last_error}</span> : null}</div>)}</div> : <p className="muted">Genere la vista previa de malla para ver los puntos.</p>}</article>
        ) : null}
        {activeMapTab === "puntos" && physicalMapHistory.length > 0 ? (
          <article className="panel"><div className="section-heading"><h3>Historial de mediciones</h3></div><div className="point-card-grid">{physicalMapHistory.slice(0, 8).map((entry) => <div className="mesh-point-card" key={String(entry.map_id)}><strong>Versión {String(entry.version ?? "-")}</strong><span>Estado: {String(entry.status ?? "-")}</span><span>Placement: {String(entry.placement_revision ?? "-")}</span><span>Filas/columnas: {String(entry.rows ?? "-")} × {String(entry.columns ?? "-")}</span><span>Medidos: {String(entry.points_measured ?? 0)}</span><span>Fallidos: {String(entry.points_failed ?? 0)}</span><span>{entry.active ? "Activo" : "Histórico"}</span></div>)}</div></article>
        ) : null}
        {activeMapTab === "configuracion" && !machine.isPhysical ? (
          <HeightMapControlPanel material={project.material} heightMap={heightMap} busy={heightMapBusy} onConfigure={(nextPayload) => withHeightMapAction(() => api.configureHeightMap(project.id, selectedOperation!.id, nextPayload))} onSimulate={(nextPayload) => withHeightMapAction(() => api.simulateHeightMap(project.id, selectedOperation!.id, nextPayload))} onImportJson={(content) => withHeightMapAction(() => api.importHeightMapJson(project.id, selectedOperation!.id, content))} onImportCsv={(content) => withHeightMapAction(() => api.importHeightMapCsv(project.id, selectedOperation!.id, content))} onRecalculate={() => withHeightMapAction(() => api.recalculateHeightMap(project.id, selectedOperation!.id))} onDelete={() => withHeightMapAction(async () => { await api.deleteHeightMap(project.id, selectedOperation!.id); setHeightMap(null); })} />
        ) : null}
        {activeMapTab === "configuracion" && machine.isPhysical ? <article className="panel"><div className="section-heading"><h3>Configuración física activa</h3></div><p className="muted">La configuración física está arriba: filas, columnas, retiro de borde, exclusiones, Z segura, paso, velocidad y retracto.</p></article> : null}
        {!heightMap && !physicalMap ? <div className="panel empty-state"><p>{machine.isPhysical ? "Genere la vista previa de malla para ver región, puntos y recorrido." : "Configure la región sondeable, genere un mapa simulado o importe mediciones."}</p></div> : null}
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
      setPhysicalMap(null);
      setHeightMap(null);
      setCompensationPreview(null);
      setGeneratedGCode(null);
      setMeshArmed(false);
      setMeshSuggestion(null);
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

  const runExecutionAction = async (action: "preflight" | "upload" | "confirm-file" | "confirm-tool" | "confirm-spindle" | "start" | "pause" | "resume" | "cancel") => {
    if (action === "preflight") {
      setExecutionState(physicalMap?.status === "MESH_COMPLETE" && generatedGCode ? "READY_TO_EXECUTE" : "PREFLIGHT");
      setExecutionEvent("Preflight revisado desde la aplicación. No se enviaron movimientos.");
      return;
    }
    const nextState = action === "upload" ? "UPLOADING" : action === "start" ? "RUNNING" : action === "pause" ? "PAUSED" : action === "cancel" ? "CANCELLED" : executionState;
    setExecutionState(nextState);
    setExecutionEvent(`Acción ${action} registrada para ejecución supervisada. No se inició mecanizado durante desarrollo.`);
  };

  const renderEjecucion = () => (
    <div className="stack gap-md">
      <article className="panel">
        <div className="section-heading section-heading--stacked">
          <div>
            <p className="eyebrow">Ejecución controlada</p>
            <h3>Preflight Moonraker/Klipper</h3>
          </div>
          <StatusBadge tone={executionState === "READY_TO_EXECUTE" ? "success" : executionState === "CANCELLED" ? "warning" : "info"}>{executionState}</StatusBadge>
        </div>
        <div className="info-grid info-grid--double compact-grid">
          <div className="metric-box"><span>Modo</span><strong>{machine.modeLabel}</strong></div>
          <div className="metric-box"><span>Runtime</span><strong>{machine.connected ? "conectado" : "desconectado"}</strong></div>
          <div className="metric-box"><span>Klipper</span><strong>{machine.klipperReady ? "ready" : "no ready"}</strong></div>
          <div className="metric-box"><span>Homing</span><strong>{machine.homedAxes || "pendiente"}</strong></div>
          <div className="metric-box"><span>Mapa</span><strong>{physicalMap?.status ?? "pendiente"}</strong></div>
          <div className="metric-box"><span>Archivo compensado</span><strong>{generatedGCode?.relative_path ?? "pendiente"}</strong></div>
        </div>
        <p className="muted">Último evento: {executionEvent}</p>
        <div className="action-grid">
          <button className="button" type="button" disabled={referenceBusy || !selectedOperation} onClick={() => void runExecutionAction("preflight")}>Ejecutar preflight</button>
          <button className="button button--ghost" type="button" disabled={referenceBusy || !selectedOperation} onClick={() => void runExecutionAction("upload")}>Subir archivo a Moonraker</button>
          <button className="button button--ghost" type="button" disabled={referenceBusy || !selectedOperation} onClick={() => void runExecutionAction("confirm-file")}>Confirmar archivo</button>
          <button className="button button--ghost" type="button" disabled={referenceBusy || !selectedOperation} onClick={() => void runExecutionAction("confirm-tool")}>Confirmar herramienta</button>
          <button className="button button--ghost" type="button" disabled={referenceBusy || !selectedOperation} onClick={() => void runExecutionAction("confirm-spindle")}>Confirmar spindle</button>
          <button className="button" type="button" disabled={referenceBusy || !selectedOperation} onClick={() => void runExecutionAction("start")}>Iniciar ejecución supervisada</button>
          <button className="button button--ghost" type="button" disabled={referenceBusy || !selectedOperation} onClick={() => void runExecutionAction("pause")}>Pausar</button>
          <button className="button button--ghost" type="button" disabled={referenceBusy || !selectedOperation} onClick={() => void runExecutionAction("resume")}>Reanudar</button>
          <button className="button button--ghost button--danger" type="button" disabled={referenceBusy || !selectedOperation} onClick={() => void runExecutionAction("cancel")}>Cancelar</button>
          <button className="button button--danger" type="button" disabled={!machine.isPhysical || machine.refreshing} onClick={() => { if (window.confirm("Enviar M112 a Klipper?")) void machine.runMachineAction("emergency"); }}>Emergencia M112</button>
        </div>
      </article>
    </div>
  );

  const renderValidacion = () => (
    <div className="stack gap-md">
      <article className="panel">
        <div className="section-heading section-heading--stacked">
          <div>
            <p className="eyebrow">Validación</p>
            <h3>Previsualización matemática de compensación</h3>
          </div>
          <p className="muted">Bloqueada si faltan referencias o el mapa no está validado. La generación real conserva X/Y y aplica Z += delta_superficie.</p>
        </div>
        {!referenceSession?.lista_para_compensacion ? (
          <div className="alert alert--warning">
            {(referenceSession?.bloqueos_compensacion ?? []).join(" ") || "La preparación aún no está lista para compensación."}
          </div>
        ) : null}
        <button
          className="button"
          type="button"
          disabled={referenceBusy || !selectedOperation || !referenceSession?.lista_para_compensacion}
          onClick={async () => {
            if (!selectedOperation) {
              return;
            }
            setReferenceBusy(true);
            setWorkspaceError("");
            try {
              const result = await api.getCompensationPreview(project.id, selectedOperation.id);
              setReferenceSession(result.session);
              setCompensationPreview(result.preview);
            } catch (error) {
              setWorkspaceError(error instanceof Error ? error.message : "No fue posible calcular la previsualización de compensación.");
            } finally {
              setReferenceBusy(false);
            }
          }}
        >
          Previsualizar compensación
        </button>
        <button
          className="button button--ghost"
          type="button"
          disabled={referenceBusy || !selectedOperation}
          onClick={async () => {
            if (!selectedOperation) return;
            setReferenceBusy(true);
            setWorkspaceError("");
            try {
              const result = await api.generateCompensatedGCode(project.id, selectedOperation.id);
              setGeneratedGCode(result);
            } catch (error) {
              setWorkspaceError(error instanceof Error ? error.message : "No fue posible generar el G-code compensado.");
            } finally {
              setReferenceBusy(false);
            }
          }}
        >
          Generar G-code compensado
        </button>
        {generatedGCode ? (
          <p className="muted">Archivo generado: <a href={api.generatedFileUrl(project.id, generatedGCode.relative_path)}>{generatedGCode.relative_path}</a></p>
        ) : null}
      </article>

      {compensationPreview ? (
        <>
          <article className="panel">
            <div className="info-grid info-grid--double compact-grid">
              <div className="metric-box"><span>Z original mín</span><strong>{formatMillimeters(compensationPreview.resumen_z_original.min_mm, 4)}</strong></div>
              <div className="metric-box"><span>Z original máx</span><strong>{formatMillimeters(compensationPreview.resumen_z_original.max_mm, 4)}</strong></div>
              <div className="metric-box"><span>Z simulada mín</span><strong>{formatMillimeters(compensationPreview.resumen_z_compensada.min_mm, 4)}</strong></div>
              <div className="metric-box"><span>Z simulada máx</span><strong>{formatMillimeters(compensationPreview.resumen_z_compensada.max_mm, 4)}</strong></div>
              <div className="metric-box"><span>Puntos fuera de dominio</span><strong>{compensationPreview.puntos_fuera_dominio}</strong></div>
              <div className="metric-box"><span>Subdivisiones virtuales</span><strong>{compensationPreview.puntos_virtuales_agregados}</strong></div>
            </div>
            <p className="muted">{compensationPreview.convencion_matematica}</p>
          </article>
          <article className="panel">
            <div className="section-heading"><h3>Comparación de trayectoria</h3></div>
            <div className="stack gap-sm">
              {compensationPreview.segmentos.slice(0, 12).map((segment, index) => (
                <div className="subpanel subpanel--soft" key={`${segment.numero_linea}-${index}`}>
                  <strong>{segment.tipo_movimiento}</strong>
                  <p className="mono-text">Línea {segment.numero_linea ?? "-"} · estado {segment.estado} · distancia {formatMillimeters(segment.distancia_mm, 3)}</p>
                  <p className="mono-text">
                    {segment.puntos[0] ? `Inicio Z original ${formatMillimeters(segment.puntos[0].z_original_mm, 4)} · Z simulada ${formatMillimeters(segment.puntos[0].z_compensada_mm, 4)}` : "Sin puntos"}
                  </p>
                </div>
              ))}
            </div>
          </article>
        </>
      ) : null}
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

      {editingProject ? <ProjectForm initialValue={payload} mode="edit" onSubmit={onSaveProject} submitting={savingProject} /> : null}

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
          <button className={`toolbar-pill${activeView === "validacion" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => setActiveView("validacion")}>Compensación</button>
          <button className={`toolbar-pill${activeView === "ejecucion" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => setActiveView("ejecucion")}>Ejecución</button>
        </div>
      </article>

      {workspaceError ? <div className="panel alert alert--error">{workspaceError}</div> : null}

      <section className="workspace-view-panel">
        {activeView === "archivo" ? renderArchivo() : null}
        {activeView === "trayectoria" ? renderTrayectoria() : null}
        {activeView === "referencia" ? renderReferencia() : null}
        {activeView === "mapa" ? renderMapa() : null}
        {activeView === "validacion" ? renderValidacion() : null}
      {activeView === "ejecucion" ? renderEjecucion() : null}
      </section>
    </div>
  );
}
