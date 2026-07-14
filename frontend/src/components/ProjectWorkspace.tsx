import { useEffect, useMemo, useRef, useState } from "react";

import { HeightMapControlPanel } from "../features/heightmap/HeightMapControlPanel";
import { HeightMapHeatmap } from "../features/heightmap/HeightMapHeatmap";
import { HeightMapPointTable } from "../features/heightmap/HeightMapPointTable";
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
  ReferenceStep,
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
};

type WorkspaceView = "archivo" | "trayectoria" | "referencia" | "mapa" | "validacion" | "ejecucion";
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

function referenceValue(record: Record<string, string | number | null> | null, key: "x_mm" | "y_mm" | "z_mm") {
  const value = record?.[key];
  return typeof value === "number" ? String(value) : "";
}

function workspaceViewStorageKey(projectId: string, operationId: string) {
  return `kca:workspace-view:${projectId}:${operationId}`;
}

function nextFieldError(message: string, fallbackField: "x_mm" | "y_mm" | "z_mm"): ReferenceFieldErrors {
  for (const field of ["x_mm", "y_mm", "z_mm"] as const) {
    if (message.includes(`${field}:`)) {
      return { [field]: message };
    }
  }
  return { [fallbackField]: message };
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
}: ProjectWorkspaceProps) {
  const [editingProject, setEditingProject] = useState(false);
  const [selectedOperationId, setSelectedOperationId] = useState<string | null>(pickDefaultOperation(project));
  const [selectedSetupId, setSelectedSetupId] = useState<string | null>(project?.montajes[0]?.id ?? null);
  const [newSetupName, setNewSetupName] = useState("");
  const [newOperationName, setNewOperationName] = useState("Fresado superior");
  const [newOperationType, setNewOperationType] = useState("fresado_superior");
  const [newOperationTool, setNewOperationTool] = useState("");
  const [activeView, setActiveView] = useState<WorkspaceView>("archivo");
  const [activeMapTab, setActiveMapTab] = useState<MapTab>("mapa2d");
  const [heightMode, setHeightMode] = useState<HeightMode>("bruto");
  const [mapSource, setMapSource] = useState<HeightMapSource>("SIMULATED");
  const [heightMap, setHeightMap] = useState<HeightMap | null>(null);
  const [physicalMap, setPhysicalMap] = useState<PhysicalMapPayload | null>(null);
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
    const stored = window.localStorage.getItem(workspaceViewStorageKey(project.id, selectedOperation.id));
    if (stored === "archivo" || stored === "trayectoria" || stored === "referencia" || stored === "mapa" || stored === "validacion" || stored === "ejecucion") {
      setActiveView(stored);
      return;
    }
  }, [project, selectedOperation]);

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
      </article>
    );
    if (!selectedOperation) {
      return <div className="stack gap-md">{selector}<div className="panel empty-state"><p>Seleccione una operación para ver su trayectoria.</p></div></div>;
    }
    if (!selectedOperation.archivo_gcode) {
      return <div className="stack gap-md">{selector}<div className="panel empty-state"><p>Esta operación todavía no tiene G-code.</p></div></div>;
    }
    if (!selectedOperation.analisis) {
      return <div className="stack gap-md">{selector}<div className="panel empty-state"><p>Analice el archivo de esta operación para ver su trayectoria.</p></div></div>;
    }
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
            analysis={selectedOperation.analisis}
            operationName={selectedOperation.nombre}
            storageKey={project.id + ":" + selectedOperation.id}
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

  const renderReferencia = () => (
    <div className="stack gap-md">
      <article className="panel">
        <div className="section-heading section-heading--stacked">
          <div>
            <p className="eyebrow">Referencia</p>
            <h3>Flujo simulado de preparación</h3>
          </div>
          <p className="muted">Todos los botones confirman estados en simulación. No existe home real, sondeo físico ni comandos hacia Moonraker.</p>
        </div>
        <div className="machine-banner machine-banner--large" role="status">
          <span className="machine-banner__dot" aria-hidden="true" />
          <span>MODO SIMULADO — no se enviará movimiento a la máquina</span>
        </div>
        {referenceSession?.motivo_invalidacion ? <div className="alert alert--warning">{referenceSession.motivo_invalidacion}</div> : null}
        <div className="workflow-steps-grid">
          {(referenceSession?.pasos ?? []).map(renderReferenceStep)}
        </div>
      </article>

      <article className="panel">
        <div className="section-heading"><h3>1. Referencia de máquina</h3></div>
        <p className="muted">Ubica el sistema de coordenadas de la máquina. En esta versión solo se confirma en simulación y una vez por sesión.</p>
        <button className="button" type="button" disabled={referenceBusy || referenceSession?.machine_reference.confirmada || !selectedOperation} onClick={() => void withReferenceAction(() => api.confirmMachineReference(project.id, selectedOperation!.id))}>
          {referenceSession?.machine_reference.confirmada ? "Ya confirmada en simulación" : "Confirmar en simulación"}
        </button>
      </article>

      <article className="panel">
        <div className="section-heading"><h3>2. Origen de trabajo X/Y</h3></div>
        <p className="muted">Define dónde queda X0 Y0 del G-code respecto al montaje de la placa.</p>
        <div className="form-grid">
          <label>
            X (mm)
            <input
              ref={(node) => { workOriginRefs.current.x_mm = node; }}
              type="number"
              inputMode="decimal"
              value={workOrigin.x_mm}
              onChange={(event) => {
                setWorkOrigin((current) => ({ ...current, x_mm: event.target.value }));
                setWorkOriginErrors((current) => ({ ...current, x_mm: undefined }));
              }}
            />
            {workOriginErrors.x_mm ? <span className="form-error">{workOriginErrors.x_mm}</span> : null}
          </label>
          <label>
            Y (mm)
            <input
              ref={(node) => { workOriginRefs.current.y_mm = node; }}
              type="number"
              inputMode="decimal"
              value={workOrigin.y_mm}
              onChange={(event) => {
                setWorkOrigin((current) => ({ ...current, y_mm: event.target.value }));
                setWorkOriginErrors((current) => ({ ...current, y_mm: undefined }));
              }}
            />
            {workOriginErrors.y_mm ? <span className="form-error">{workOriginErrors.y_mm}</span> : null}
          </label>
        </div>
        <button className="button" type="button" disabled={referenceBusy || !selectedOperation} onClick={() => void submitWorkOrigin()}>
          Confirmar en simulación
        </button>
      </article>

      <article className="panel">
        <div className="section-heading"><h3>3. Referencia Z</h3></div>
        <p className="muted">Define la altura que se considera Z0 para este montaje. Puede usar la misma posición X/Y del origen de trabajo, pero es una referencia vertical diferente.</p>
        <label className="toggle-field">
          <input type="checkbox" checked={useWorkOriginXYForZ} onChange={(event) => setUseWorkOriginXYForZ(event.target.checked)} />
          <span>Usar la misma posición X/Y del origen de trabajo</span>
        </label>
        <div className="form-grid">
          <label>
            X (mm)
            <input
              ref={(node) => { zReferenceRefs.current.x_mm = node; }}
              type="number"
              inputMode="decimal"
              value={useWorkOriginXYForZ ? workOrigin.x_mm : zReference.x_mm}
              disabled={useWorkOriginXYForZ}
              onChange={(event) => {
                setZReference((current) => ({ ...current, x_mm: event.target.value }));
                setZReferenceErrors((current) => ({ ...current, x_mm: undefined }));
              }}
            />
            {zReferenceErrors.x_mm ? <span className="form-error">{zReferenceErrors.x_mm}</span> : null}
          </label>
          <label>
            Y (mm)
            <input
              ref={(node) => { zReferenceRefs.current.y_mm = node; }}
              type="number"
              inputMode="decimal"
              value={useWorkOriginXYForZ ? workOrigin.y_mm : zReference.y_mm}
              disabled={useWorkOriginXYForZ}
              onChange={(event) => {
                setZReference((current) => ({ ...current, y_mm: event.target.value }));
                setZReferenceErrors((current) => ({ ...current, y_mm: undefined }));
              }}
            />
            {zReferenceErrors.y_mm ? <span className="form-error">{zReferenceErrors.y_mm}</span> : null}
          </label>
          <label>
            Z de referencia (mm)
            <input
              ref={(node) => { zReferenceRefs.current.z_mm = node; }}
              type="number"
              inputMode="decimal"
              value={zReference.z_mm}
              onChange={(event) => {
                setZReference((current) => ({ ...current, z_mm: event.target.value }));
                setZReferenceErrors((current) => ({ ...current, z_mm: undefined }));
              }}
            />
            {zReferenceErrors.z_mm ? <span className="form-error">{zReferenceErrors.z_mm}</span> : null}
          </label>
        </div>
        <button className="button" type="button" disabled={referenceBusy || !selectedOperation} onClick={() => void submitZReference()}>
          Confirmar en simulación
        </button>
      </article>

      <article className="panel">
        <div className="section-heading"><h3>4. Región sondeable</h3></div>
        <p className="muted">La región sondeable se configura desde la pestaña “Mapa de alturas / Configuración”.</p>
        {heightMap ? <p className="mono-text">{JSON.stringify(heightMap.probe_region)}</p> : <p className="muted">Aún no hay región configurada.</p>}
      </article>

      <article className="panel">
        <div className="section-heading"><h3>5. Mapa</h3></div>
        <p className="muted">{referenceSession?.pasos.find((step) => step.id === "mapa")?.detalle ?? "Aún no hay mapa disponible."}</p>
        <p className="mono-text">Mapa actual: {heightMap ? `${heightMap.fuente_datos} · v${heightMap.version}` : "no disponible"}</p>
      </article>

      <article className="panel">
        <div className="section-heading"><h3>6. Validación</h3></div>
        <p className="muted">{referenceSession?.pasos.find((step) => step.id === "validacion")?.detalle ?? "La validación del mapa sigue pendiente."}</p>
        <button className="button" type="button" disabled={referenceBusy || !selectedOperation || !heightMap} onClick={() => void withReferenceAction(() => api.validateHeightMap(project.id, selectedOperation!.id))}>
          Confirmar en simulación
        </button>
      </article>
    </div>
  );


  const withPhysicalMapAction = async (action: () => Promise<PhysicalMapPayload | null>) => {
    setHeightMapBusy(true);
    setWorkspaceError("");
    try {
      const result = await action();
      if (result) {
        setPhysicalMap(result);
      }
      if (project && selectedOperation) {
        const measured = await api.getPhysicalHeightMap(project.id, selectedOperation.id);
        setHeightMap(measured);
        setMapSource("MEASURED");
      }
      setCompensationPreview(null);
      setGeneratedGCode(null);
    } catch (error) {
      setWorkspaceError(error instanceof Error ? error.message : "No fue posible actualizar el mapa físico medido.");
    } finally {
      setHeightMapBusy(false);
    }
  };

  const selectedToolKey = selectedOperation?.tool_id || selectedOperation?.herramienta || "sin herramienta";
  const physicalMeasuredPoints = physicalMap?.points?.filter((point) => point.status === "MEASURED").length ?? 0;
  const physicalPointCount = typeof physicalMap?.point_count === "number" ? physicalMap.point_count : physicalMap?.points?.length ?? 0;
  const physicalMapId = typeof physicalMap?.map_id === "string" ? physicalMap.map_id : "";

  const renderMapa = () => (
    <div className="stack gap-md">
      <article className="panel">
        <div className="section-heading section-heading--stacked">
          <div>
            <p className="eyebrow">Mapa de alturas</p>
            <h3>Alturas de la superficie</h3>
          </div>
          <div className="toolbar-inline toolbar-inline--scrollable" role="tablist" aria-label="Vistas del mapa de alturas">
            <button className={`toolbar-pill${activeMapTab === "mapa2d" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => setActiveMapTab("mapa2d")}>Mapa 2D</button>
            <button className={`toolbar-pill${activeMapTab === "superficie3d" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => setActiveMapTab("superficie3d")}>Superficie 3D</button>
            <button className={`toolbar-pill${activeMapTab === "puntos" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => setActiveMapTab("puntos")}>Puntos</button>
            <button className={`toolbar-pill${activeMapTab === "configuracion" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => setActiveMapTab("configuracion")}>Configuración</button>
          </div>
        </div>
        <div className="toolbar-inline toolbar-inline--scrollable">
          <button className={`toolbar-pill${heightMode === "bruto" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => setHeightMode("bruto")}>Superficie bruta</button>
          <button className={`toolbar-pill${heightMode === "plano" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => setHeightMode("plano")}>Plano</button>
          <button className={`toolbar-pill${heightMode === "residuo" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => setHeightMode("residuo")}>Residuo</button>
        </div>
        <div className="toolbar-inline toolbar-inline--scrollable" aria-label="Fuente del mapa">
          <span className="eyebrow">Fuente del mapa</span>
          <button className={`toolbar-pill${mapSource === "SIMULATED" ? " toolbar-pill--active" : ""}`} type="button" onClick={async () => {
            setMapSource("SIMULATED");
            if (selectedOperation) {
              const maybeMap = await api.getHeightMap(project.id, selectedOperation.id).catch(() => null);
              setHeightMap(maybeMap);
            }
          }}>SIMULADO</button>
          <button className={`toolbar-pill${mapSource === "MEASURED" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => void withPhysicalMapAction(async () => {
            if (!selectedOperation) return null;
            const payload = await api.getPhysicalMap(project.id, selectedOperation.id);
            return payload.payload;
          })}>MEDIDO FÍSICAMENTE</button>
        </div>
      </article>

      {mapSource === "MEASURED" ? (
        <article className="panel">
          <div className="section-heading section-heading--stacked">
            <div>
              <p className="eyebrow">Preparación física del montaje</p>
              <h3>Mapa medido desde operaciones activas</h3>
            </div>
            <StatusBadge tone={physicalMap?.status === "MESH_COMPLETE" ? "success" : physicalMap ? "info" : "neutral"}>{physicalMap?.status ?? "sin mapa medido"}</StatusBadge>
          </div>
          <div className="info-grid info-grid--double compact-grid">
            <div className="metric-box"><span>Herramienta seleccionada</span><strong>{selectedToolKey}</strong></div>
            <div className="metric-box"><span>Puntos medidos</span><strong>{physicalMeasuredPoints}/{physicalPointCount}</strong></div>
            <div className="metric-box"><span>Filas</span><strong>{physicalMap?.grid?.rows ?? "-"}</strong></div>
            <div className="metric-box"><span>Columnas</span><strong>{physicalMap?.grid?.columns ?? "-"}</strong></div>
            <div className="metric-box"><span>dx</span><strong>{formatMillimeters(physicalMap?.grid?.dx_mm, 3)}</strong></div>
            <div className="metric-box"><span>dy</span><strong>{formatMillimeters(physicalMap?.grid?.dy_mm, 3)}</strong></div>
          </div>
          <p className="muted">El origen X/Y pertenece al montaje. El mapa de superficie se comparte por montaje/cara; cada herramienta conserva su propia referencia Z.</p>
          <div className="action-grid">
            <button className="button" type="button" disabled={heightMapBusy || !selectedOperation} onClick={() => void withPhysicalMapAction(async () => {
              if (!selectedOperation) return null;
              const result = await api.planPhysicalMapFromReference(project.id, selectedOperation.id, { max_spacing_mm: 10, margin_mm: 1 });
              return result.payload;
            })}>Preparar mapa físico</button>
            <button className="button" type="button" disabled={heightMapBusy || !physicalMapId || physicalMap?.status === "MESH_COMPLETE"} onClick={() => void withPhysicalMapAction(async () => {
              const result = await api.executeNextPhysicalMapPoint(project.id, physicalMapId);
              return result.payload;
            })}>Iniciar sondeo de malla</button>
            <button className="button button--ghost" type="button" disabled={heightMapBusy || !physicalMapId} onClick={() => void withPhysicalMapAction(async () => (await api.pausePhysicalMap(project.id, physicalMapId)).payload)}>Pausar</button>
            <button className="button button--ghost" type="button" disabled={heightMapBusy || !physicalMapId} onClick={() => void withPhysicalMapAction(async () => (await api.resumePhysicalMap(project.id, physicalMapId)).payload)}>Reanudar</button>
            <button className="button button--ghost button--danger" type="button" disabled={heightMapBusy || !physicalMapId} onClick={() => void withPhysicalMapAction(async () => (await api.cancelPhysicalMap(project.id, physicalMapId)).payload)}>Cancelar</button>
          </div>
        </article>
      ) : null}

      {heightMap ? (
        <article className="panel">
          <div className="section-heading section-heading--stacked">
            <div>
              <p className="eyebrow">Métricas</p>
              <h3>Alturas de la superficie</h3>
            </div>
          </div>
          <div className="info-grid info-grid--double compact-grid">
            <div className="metric-box"><span>Z mínima</span><strong>{formatMillimeters(heightMap.estadisticas.altura_min_mm, 4)}</strong></div>
            <div className="metric-box"><span>Z máxima</span><strong>{formatMillimeters(heightMap.estadisticas.altura_max_mm, 4)}</strong></div>
            <div className="metric-box"><span>Rango</span><strong>{formatMillimeters(heightMap.estadisticas.rango_alturas_mm, 4)}</strong></div>
            <div className="metric-box"><span>Valor de referencia</span><strong>{formatMillimeters(heightMap.estadisticas.valor_referencia_mm, 4)}</strong></div>
            <div className="metric-box"><span>Desviación RMS respecto al plano</span><strong title="Mide cuánto se apartan las muestras del plano ajustado en promedio cuadrático medio.">{formatMillimeters(heightMap.estadisticas.desviacion_rms_respecto_plano_mm, 4)}</strong></div>
            <div className="metric-box"><span>Residuo máximo</span><strong>{formatMillimeters(heightMap.estadisticas.residuo_maximo_mm, 4)}</strong></div>
            <div className="metric-box"><span>Valores atípicos</span><strong>{heightMap.estadisticas.cantidad_puntos_atipicos}</strong></div>
            <div className="metric-box"><span>Inclinación X</span><strong>{formatMillimeters(heightMap.plano?.inclinacion_x_mm_por_mm, 6)}</strong></div>
            <div className="metric-box"><span>Inclinación Y</span><strong>{formatMillimeters(heightMap.plano?.inclinacion_y_mm_por_mm, 6)}</strong></div>
            <div className="metric-box"><span>Coeficiente a</span><strong>{formatMillimeters(heightMap.plano?.a, 6)}</strong></div>
            <div className="metric-box"><span>Coeficiente b</span><strong>{formatMillimeters(heightMap.plano?.b, 6)}</strong></div>
            <div className="metric-box"><span>Coeficiente c</span><strong>{formatMillimeters(heightMap.plano?.c, 6)}</strong></div>
          </div>
        </article>
      ) : null}

      {activeMapTab === "mapa2d" && heightMap ? <HeightMapHeatmap material={project.material} heightMap={heightMap} mode={heightMode} toolpathBounds={selectedOperation?.analisis?.limites ?? null} /> : null}
      {activeMapTab === "superficie3d" && heightMap ? <HeightMapSurface3D heightMap={heightMap} mode={heightMode} /> : null}
      {activeMapTab === "puntos" && heightMap ? (
        <HeightMapPointTable
          heightMap={heightMap}
          busy={heightMapBusy}
          onToggleInclude={async (sampleId, included) => {
            await withHeightMapAction(() => api.updateHeightMapSample(project.id, selectedOperation!.id, sampleId, { incluida: included }));
          }}
          onEditSample={async (sampleId, currentValue) => {
            const prompted = window.prompt("Nuevo valor Z en milímetros. Deje vacío para marcarlo como faltante.", currentValue == null ? "" : String(currentValue));
            if (prompted == null) {
              return;
            }
            const parsed = parseFiniteNumber(prompted);
            if (prompted.trim() !== "" && parsed.error) {
              setWorkspaceError("El valor Z debe ser numérico.");
              return;
            }
            await withHeightMapAction(() => api.updateHeightMapSample(project.id, selectedOperation!.id, sampleId, { z_mm: prompted.trim() === "" ? null : parsed.value }));
          }}
        />
      ) : null}
      {activeMapTab === "configuracion" ? (
        <HeightMapControlPanel
          material={project.material}
          heightMap={heightMap}
          busy={heightMapBusy}
          onConfigure={(nextPayload) => withHeightMapAction(() => api.configureHeightMap(project.id, selectedOperation!.id, nextPayload))}
          onSimulate={(nextPayload) => withHeightMapAction(() => api.simulateHeightMap(project.id, selectedOperation!.id, nextPayload))}
          onImportJson={(content) => withHeightMapAction(() => api.importHeightMapJson(project.id, selectedOperation!.id, content))}
          onImportCsv={(content) => withHeightMapAction(() => api.importHeightMapCsv(project.id, selectedOperation!.id, content))}
          onRecalculate={() => withHeightMapAction(() => api.recalculateHeightMap(project.id, selectedOperation!.id))}
          onDelete={() => withHeightMapAction(async () => {
            await api.deleteHeightMap(project.id, selectedOperation!.id);
            setHeightMap(null);
          })}
        />
      ) : null}

      {!heightMap ? <div className="panel empty-state"><p>Configure la región sondeable, genere un mapa simulado o importe mediciones para habilitar estas vistas.</p></div> : null}
    </div>
  );


  const renderEjecucion = () => (
    <div className="stack gap-md">
      <article className="panel">
        <div className="section-heading section-heading--stacked">
          <div>
            <p className="eyebrow">Ejecución controlada</p>
            <h3>Preflight Moonraker/Klipper</h3>
          </div>
          <StatusBadge tone="info">preparado en software</StatusBadge>
        </div>
        <div className="checklist-list">
          <li data-status={physicalMap?.status === "MESH_COMPLETE" ? "confirmado" : "pendiente"}><span>Mapa medido del montaje</span><strong>{physicalMap?.status ?? "pendiente"}</strong></li>
          <li data-status={generatedGCode ? "confirmado" : "pendiente"}><span>Archivo compensado</span><strong>{generatedGCode?.relative_path ?? "pendiente"}</strong></li>
          <li data-status={referenceSession?.referencia_z ? "confirmado" : "pendiente"}><span>Referencia Z de herramienta</span><strong>{referenceSession?.referencia_z ? "vigente" : "pendiente"}</strong></li>
        </div>
        <p className="muted">La subida a Moonraker y el inicio real quedan bloqueados para validación supervisada. No se ejecuta ningún archivo desde esta pantalla durante desarrollo.</p>
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
