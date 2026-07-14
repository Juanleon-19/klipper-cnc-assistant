import { useEffect, useMemo, useState } from "react";

import { HeightMapControlPanel } from "../features/heightmap/HeightMapControlPanel";
import { HeightMapHeatmap } from "../features/heightmap/HeightMapHeatmap";
import { HeightMapPointTable } from "../features/heightmap/HeightMapPointTable";
import { HeightMapSurface3D } from "../features/heightmap/HeightMapSurface3D";
import { ToolpathViewer } from "../features/viewer/ToolpathViewer";
import { formatDate, formatFileSize, formatMillimeters } from "../lib/format";
import { api } from "../lib/api";
import { operationPresets } from "../lib/presets";
import {
  buildOperationWorkflow,
  getOperationWorkflowState,
  splitIssues,
  toneForStatus,
  translateFace,
  translateOperationType,
  translateStatus,
} from "../lib/ui";
import type { HeightMap, Operation, Project, ProjectPayload } from "../types";
import { ProjectForm } from "./ProjectForm";
import { StatusBadge } from "./StatusBadge";

type ProjectWorkspaceProps = {
  project: Project | null;
  busyKey: string | null;
  savingProject: boolean;
  onSaveProject: (payload: ProjectPayload) => Promise<void>;
  onAddOperation: (presetKey: string) => Promise<void>;
  onDeleteOperation: (operation: Operation) => Promise<void>;
  onRemoveFile: (operation: Operation) => Promise<void>;
  onAnalyze: (operation: Operation) => Promise<void>;
  onUploadFile: (operation: Operation, file: File) => Promise<void>;
};

type WorkspaceTab = "trayectoria" | "mapa2d" | "superficie3d" | "tabla";
type HeightMode = "bruto" | "plano" | "residuo";

function findPresetOperation(project: Project, presetKey: string): Operation | undefined {
  const preset = operationPresets.find((item) => item.clave === presetKey);
  if (!preset) {
    return undefined;
  }
  return project.operaciones.find((operation) => operation.tipo === preset.tipo && operation.cara === preset.cara);
}

function pickDefaultOperation(project: Project | null): string | null {
  if (!project || project.operaciones.length === 0) {
    return null;
  }
  return project.operaciones.find((operation) => Boolean(operation.analisis))?.id ?? project.operaciones[0].id;
}

export function ProjectWorkspace({
  project,
  busyKey,
  savingProject,
  onSaveProject,
  onAddOperation,
  onDeleteOperation,
  onRemoveFile,
  onAnalyze,
  onUploadFile,
}: ProjectWorkspaceProps) {
  const [editingProject, setEditingProject] = useState(false);
  const [selectedOperationId, setSelectedOperationId] = useState<string | null>(pickDefaultOperation(project));
  const [activeTab, setActiveTab] = useState<WorkspaceTab>("trayectoria");
  const [heightMode, setHeightMode] = useState<HeightMode>("bruto");
  const [heightMap, setHeightMap] = useState<HeightMap | null>(null);
  const [heightMapBusy, setHeightMapBusy] = useState(false);
  const [heightMapError, setHeightMapError] = useState("");

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

  useEffect(() => {
    if (!project || !selectedOperation) {
      setHeightMap(null);
      setHeightMapError("");
      return;
    }

    const run = async () => {
      setHeightMapError("");
      try {
        const payload = await api.getHeightMap(project.id, selectedOperation.id);
        setHeightMap(payload);
      } catch (error) {
        const message = error instanceof Error ? error.message : "No fue posible cargar el mapa de alturas.";
        if (message.toLowerCase().includes("no existe")) {
          setHeightMap(null);
          return;
        }
        setHeightMapError(message);
      }
    };

    void run();
  }, [project, selectedOperation]);

  if (!project) {
    return (
      <div className="panel empty-state">
        <p className="eyebrow">Espacio de trabajo</p>
        <h2>Seleccione un proyecto</h2>
        <p>Abra un proyecto existente o cree uno nuevo para gestionar operaciones, revisar trayectorias y trabajar con el mapa de alturas simulado.</p>
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

  const issueGroups = splitIssues(selectedOperation?.analisis ?? null);
  const workflow = buildOperationWorkflow(selectedOperation);
  const analysisBusy = selectedOperation ? busyKey === `analyze:${selectedOperation.id}` : false;
  const fileBusy = selectedOperation ? busyKey === `file:${selectedOperation.id}` : false;
  const deleteBusy = selectedOperation ? busyKey === `delete:${selectedOperation.id}` : false;

  const withHeightMapAction = async (action: () => Promise<HeightMap | void>) => {
    setHeightMapBusy(true);
    setHeightMapError("");
    try {
      const result = await action();
      if (result) {
        setHeightMap(result);
      }
    } catch (error) {
      setHeightMapError(error instanceof Error ? error.message : "No fue posible actualizar el mapa de alturas.");
    } finally {
      setHeightMapBusy(false);
    }
  };

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
          <div>
            <span className="eyebrow">Material bruto</span>
            <strong>{project.material.ancho_mm} × {project.material.alto_mm} × {project.material.espesor_mm ?? "-"} mm</strong>
          </div>
          <div>
            <span className="eyebrow">Configuración</span>
            <strong>{project.doble_cara ? `Doble cara, volteo ${project.eje_volteo?.toUpperCase()}` : "Una cara"}</strong>
          </div>
          <div>
            <span className="eyebrow">Operaciones</span>
            <strong>{project.operaciones.length}</strong>
          </div>
          <div>
            <span className="eyebrow">Actualizado</span>
            <strong>{formatDate(project.actualizado_en)}</strong>
          </div>
        </div>
      </article>

      {editingProject ? <ProjectForm initialValue={payload} mode="edit" onSubmit={onSaveProject} submitting={savingProject} /> : null}

      <div className="workspace-layout">
        <section className="workspace-main">
          <article className="panel workflow-panel">
            <div className="section-heading section-heading--stacked">
              <div>
                <p className="eyebrow">Operaciones</p>
                <h3>Flujo del proyecto</h3>
              </div>
              <p className="muted">Seleccione solo las operaciones necesarias. No se ejecuta ningún movimiento físico.</p>
            </div>
            <div className="workflow-grid">
              {operationPresets.map((preset, index) => {
                const operation = findPresetOperation(project, preset.clave);
                const disabled = preset.cara === "inferior" && !project.doble_cara;
                const isSelected = operation?.id === selectedOperationId;
                return (
                  <article key={preset.clave} className={`workflow-card${isSelected ? " workflow-card--selected" : ""}${disabled ? " workflow-card--disabled" : ""}`}>
                    <div className="workflow-card__header">
                      <span className="workflow-step">{index + 1}</span>
                      <div>
                        <h4>{preset.etiqueta}</h4>
                        <p className="muted">{preset.descripcion}</p>
                      </div>
                    </div>
                    <StatusBadge tone={disabled ? "neutral" : toneForStatus(operation?.estado ?? "sin configurar")}>
                      {disabled ? "Solo para PCB doble cara" : operation ? translateStatus(operation.estado) : "Sin configurar"}
                    </StatusBadge>
                    <p className="workflow-card__state">{getOperationWorkflowState(operation)}</p>
                    {operation ? (
                      <button className="button button--ghost" type="button" onClick={() => setSelectedOperationId(operation.id)}>
                        Abrir operación
                      </button>
                    ) : (
                      <button className="button" type="button" disabled={disabled || busyKey === `add:${preset.clave}`} onClick={() => void onAddOperation(preset.clave)}>
                        {busyKey === `add:${preset.clave}` ? "Configurando..." : "Configurar operación"}
                      </button>
                    )}
                  </article>
                );
              })}
            </div>
          </article>

          <article className="panel viewer-panel">
            <div className="section-heading section-heading--stacked">
              <div>
                <p className="eyebrow">Espacio técnico</p>
                <h3>{selectedOperation?.nombre ?? "Seleccione una operación"}</h3>
              </div>
              <div className="toolbar-inline">
                <button className={`toolbar-pill${activeTab === "trayectoria" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => setActiveTab("trayectoria")}>Trayectoria</button>
                <button className={`toolbar-pill${activeTab === "mapa2d" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => setActiveTab("mapa2d")}>Mapa 2D</button>
                <button className={`toolbar-pill${activeTab === "superficie3d" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => setActiveTab("superficie3d")}>Superficie 3D</button>
                <button className={`toolbar-pill${activeTab === "tabla" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => setActiveTab("tabla")}>Tabla de puntos</button>
              </div>
            </div>

            {activeTab !== "trayectoria" ? (
              <div className="toolbar-inline">
                <button className={`toolbar-pill${heightMode === "bruto" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => setHeightMode("bruto")}>Altura bruta</button>
                <button className={`toolbar-pill${heightMode === "plano" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => setHeightMode("plano")}>Plano estimado</button>
                <button className={`toolbar-pill${heightMode === "residuo" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => setHeightMode("residuo")}>Residuo local</button>
              </div>
            ) : null}

            {selectedOperation?.analisis && activeTab === "trayectoria" ? (
              <ToolpathViewer material={project.material} analysis={selectedOperation.analisis} operationName={selectedOperation.nombre} />
            ) : null}

            {activeTab === "trayectoria" && !selectedOperation?.analisis ? (
              <div className="empty-state empty-state--viewer">
                <h3>Visor técnico preparado</h3>
                <p>Cargue un archivo y ejecute el análisis para habilitar la vista técnica 2D, el inspector y el recorrido visual.</p>
              </div>
            ) : null}

            {activeTab === "mapa2d" && heightMap ? (
              <HeightMapHeatmap material={project.material} heightMap={heightMap} mode={heightMode} />
            ) : null}

            {activeTab === "superficie3d" && heightMap ? (
              <HeightMapSurface3D heightMap={heightMap} mode={heightMode} />
            ) : null}

            {activeTab === "tabla" && heightMap ? (
              <HeightMapPointTable
                heightMap={heightMap}
                busy={heightMapBusy}
                onToggleInclude={async (sampleId, included) => {
                  if (!project || !selectedOperation) {
                    return;
                  }
                  await withHeightMapAction(() => api.updateHeightMapSample(project.id, selectedOperation.id, sampleId, { incluida: included }));
                }}
                onEditSample={async (sampleId, currentValue) => {
                  if (!project || !selectedOperation) {
                    return;
                  }
                  const prompted = window.prompt("Nuevo valor Z en milímetros. Deje vacío para marcarlo como faltante.", currentValue == null ? "" : String(currentValue));
                  if (prompted == null) {
                    return;
                  }
                  const nextValue = prompted.trim() === "" ? null : Number(prompted);
                  if (nextValue !== null && Number.isNaN(nextValue)) {
                    setHeightMapError("El valor Z debe ser numérico.");
                    return;
                  }
                  await withHeightMapAction(() => api.updateHeightMapSample(project.id, selectedOperation.id, sampleId, { z_mm: nextValue }));
                }}
              />
            ) : null}

            {activeTab !== "trayectoria" && !heightMap ? (
              <div className="empty-state empty-state--viewer">
                <h3>Mapa de alturas no disponible</h3>
                <p>Configure la malla, genere un escenario simulado o importe un archivo JSON/CSV para habilitar esta vista.</p>
              </div>
            ) : null}
          </article>
        </section>

        <aside className="workspace-side">
          <article className="panel operation-detail-panel">
            <div className="section-heading">
              <div>
                <p className="eyebrow">Detalle</p>
                <h3>{selectedOperation?.nombre ?? "Seleccione una operación"}</h3>
              </div>
              {selectedOperation ? <StatusBadge tone={toneForStatus(selectedOperation.estado)}>{translateStatus(selectedOperation.estado)}</StatusBadge> : null}
            </div>

            {selectedOperation ? (
              <>
                <dl className="definition-grid definition-grid--compact">
                  <div><dt>Tipo</dt><dd>{translateOperationType(selectedOperation.tipo)}</dd></div>
                  <div><dt>Cara</dt><dd>{translateFace(selectedOperation.cara)}</dd></div>
                  <div><dt>Herramienta</dt><dd>{selectedOperation.herramienta ?? "Sin definir"}</dd></div>
                  <div><dt>Archivo</dt><dd>{selectedOperation.nombre_archivo_original ?? "Sin archivo"}</dd></div>
                  <div><dt>Tamaño</dt><dd>{formatFileSize(selectedOperation.tamano_archivo_bytes)}</dd></div>
                  <div><dt>SHA-256</dt><dd className="mono-text mono-text--truncate">{selectedOperation.sha256 ?? "-"}</dd></div>
                </dl>

                <div className="workflow-steps-list">
                  {workflow.map((step) => (
                    <div className={`workflow-step-row${step.complete ? " workflow-step-row--complete" : ""}${step.active ? " workflow-step-row--active" : ""}`} key={step.label}>
                      <span className="workflow-step-row__dot" aria-hidden="true" />
                      <span>{step.label}</span>
                    </div>
                  ))}
                </div>

                <div className="action-grid">
                  <label className="button button--ghost file-button">
                    {selectedOperation.archivo_gcode ? "Reemplazar archivo" : "Cargar archivo"}
                    <input
                      aria-label={`Cargar archivo para ${selectedOperation.nombre}`}
                      type="file"
                      accept=".nc,.gcode,.tap"
                      disabled={fileBusy}
                      onChange={async (event) => {
                        const file = event.target.files?.[0];
                        if (!file) {
                          return;
                        }
                        await onUploadFile(selectedOperation, file);
                        event.target.value = "";
                      }}
                    />
                  </label>
                  <button className="button button--ghost" type="button" disabled={!selectedOperation.archivo_gcode || analysisBusy} onClick={() => void onAnalyze(selectedOperation)}>
                    {analysisBusy ? "Analizando archivo..." : "Analizar archivo"}
                  </button>
                  <button
                    className="button button--ghost"
                    type="button"
                    disabled={!selectedOperation.archivo_gcode || fileBusy}
                    onClick={async () => {
                      if (window.confirm("Se quitará la asociación del archivo actual. ¿Desea continuar?")) {
                        await onRemoveFile(selectedOperation);
                      }
                    }}
                  >
                    Eliminar asociación
                  </button>
                  <button
                    className="button button--ghost button--danger"
                    type="button"
                    disabled={deleteBusy}
                    onClick={async () => {
                      if (window.confirm("La operación seleccionada se eliminará del proyecto. ¿Desea continuar?")) {
                        await onDeleteOperation(selectedOperation);
                      }
                    }}
                  >
                    Eliminar operación
                  </button>
                </div>
              </>
            ) : (
              <p className="muted">Seleccione una operación configurada o añada una nueva desde el flujo.</p>
            )}
          </article>

          <article className="panel analysis-summary-panel">
            <div className="section-heading">
              <div>
                <p className="eyebrow">Análisis</p>
                <h3>Resumen técnico</h3>
              </div>
            </div>
            {selectedOperation?.analisis ? (
              <>
                <div className="info-grid info-grid--double compact-grid">
                  <div className="metric-box"><span>Movimientos</span><strong>{selectedOperation.analisis.cantidad_movimientos}</strong></div>
                  <div className="metric-box"><span>Avances</span><strong>{selectedOperation.analisis.avances_mm_min.join(", ") || "-"}</strong></div>
                  <div className="metric-box"><span>Z mín</span><strong>{formatMillimeters(selectedOperation.analisis.profundidad_min_mm, 3)}</strong></div>
                  <div className="metric-box"><span>Z máx</span><strong>{formatMillimeters(selectedOperation.analisis.profundidad_max_mm, 3)}</strong></div>
                </div>
                <div className="stack gap-sm">
                  <section className="subpanel subpanel--soft">
                    <h4>Información</h4>
                    {issueGroups.info.length > 0 ? (
                      <ul className="issue-list issue-list--info">
                        {issueGroups.info.map((issue, index) => <li key={`${issue.codigo}-${index}`}>{issue.mensaje}</li>)}
                      </ul>
                    ) : <p className="muted">Sin información adicional.</p>}
                  </section>
                  <section className="subpanel subpanel--soft">
                    <h4>Advertencias</h4>
                    {issueGroups.warnings.length > 0 ? (
                      <ul className="issue-list issue-list--warning">
                        {issueGroups.warnings.map((issue, index) => <li key={`${issue.codigo}-${index}`}>{issue.mensaje}</li>)}
                      </ul>
                    ) : <p className="muted">Sin advertencias.</p>}
                  </section>
                  <section className="subpanel subpanel--soft">
                    <h4>Errores críticos</h4>
                    {issueGroups.critical.length > 0 ? (
                      <ul className="issue-list issue-list--danger">
                        {issueGroups.critical.map((issue, index) => <li key={`${issue.codigo}-${index}`}>{issue.mensaje}</li>)}
                      </ul>
                    ) : <p className="muted">Sin errores críticos.</p>}
                  </section>
                  <section className="subpanel subpanel--soft">
                    <h4>Acciones manuales</h4>
                    {selectedOperation.analisis.comandos_manuales.length > 0 ? (
                      <ul className="chip-list">
                        {selectedOperation.analisis.comandos_manuales.map((command) => <li className="chip chip--warning" key={command}>{command}</li>)}
                      </ul>
                    ) : <p className="muted">Sin acciones manuales detectadas.</p>}
                  </section>
                </div>
              </>
            ) : (
              <p className="muted">Aún no hay resultados de análisis para esta operación.</p>
            )}
          </article>

          <HeightMapControlPanel
            heightMap={heightMap}
            busy={heightMapBusy}
            onConfigure={async (rows, columns) => {
              if (!selectedOperation) {
                return;
              }
              await withHeightMapAction(() => api.configureHeightMap(project.id, selectedOperation.id, { filas: rows, columnas: columns }));
            }}
            onSimulate={async (rows, columns, scenario, seed) => {
              if (!selectedOperation) {
                return;
              }
              await withHeightMapAction(() => api.simulateHeightMap(project.id, selectedOperation.id, { filas: rows, columnas: columns, escenario: scenario, semilla: seed }));
            }}
            onImportJson={async (content) => {
              if (!selectedOperation) {
                return;
              }
              await withHeightMapAction(() => api.importHeightMapJson(project.id, selectedOperation.id, content));
            }}
            onImportCsv={async (content) => {
              if (!selectedOperation) {
                return;
              }
              await withHeightMapAction(() => api.importHeightMapCsv(project.id, selectedOperation.id, content));
            }}
            onRecalculate={async () => {
              if (!selectedOperation) {
                return;
              }
              await withHeightMapAction(() => api.recalculateHeightMap(project.id, selectedOperation.id));
            }}
            onDelete={async () => {
              if (!selectedOperation) {
                return;
              }
              await withHeightMapAction(async () => {
                await api.deleteHeightMap(project.id, selectedOperation.id);
                setHeightMap(null);
              });
            }}
          />

          {heightMapError ? <div className="panel alert alert--error">{heightMapError}</div> : null}

          <article className="panel panel--disabled">
            <div className="section-heading">
              <h3>Movimiento y mecanizado</h3>
              <StatusBadge tone="neutral">Reservado</StatusBadge>
            </div>
            <p>Disponible en una fase posterior, después de la validación de seguridad.</p>
          </article>
        </aside>
      </div>
    </div>
  );
}
