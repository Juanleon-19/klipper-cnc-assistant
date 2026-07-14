import { useEffect, useMemo, useState } from "react";

import { HeightMapControlPanel } from "../features/heightmap/HeightMapControlPanel";
import { HeightMapHeatmap } from "../features/heightmap/HeightMapHeatmap";
import { HeightMapPointTable } from "../features/heightmap/HeightMapPointTable";
import { HeightMapSurface3D } from "../features/heightmap/HeightMapSurface3D";
import { ToolpathViewer } from "../features/viewer/ToolpathViewer";
import { formatDate, formatFileSize, formatMillimeters } from "../lib/format";
import { api } from "../lib/api";
import { operationPresets } from "../lib/presets";
import { toneForStatus, translateFace, translateOperationType, translateStatus } from "../lib/ui";
import type {
  CompensationPreview,
  HeightMap,
  Operation,
  Project,
  ProjectPayload,
  ReferenceSession,
} from "../types";
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

type WorkspaceView = "archivo" | "trayectoria" | "referencia" | "mapa" | "validacion";
type MapTab = "mapa2d" | "superficie3d" | "puntos" | "configuracion";
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

function referenceValue(record: Record<string, string | number | null> | null, key: "x_mm" | "y_mm" | "z_mm") {
  const value = record?.[key];
  return typeof value === "number" ? value : 0;
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
  const [activeView, setActiveView] = useState<WorkspaceView>("archivo");
  const [activeMapTab, setActiveMapTab] = useState<MapTab>("mapa2d");
  const [heightMode, setHeightMode] = useState<HeightMode>("bruto");
  const [heightMap, setHeightMap] = useState<HeightMap | null>(null);
  const [referenceSession, setReferenceSession] = useState<ReferenceSession | null>(null);
  const [compensationPreview, setCompensationPreview] = useState<CompensationPreview | null>(null);
  const [heightMapBusy, setHeightMapBusy] = useState(false);
  const [referenceBusy, setReferenceBusy] = useState(false);
  const [workspaceError, setWorkspaceError] = useState("");
  const [workOrigin, setWorkOrigin] = useState({ x_mm: 0, y_mm: 0 });
  const [zReference, setZReference] = useState({ x_mm: 0, y_mm: 0, z_mm: 0 });

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
      setReferenceSession(null);
      setCompensationPreview(null);
      return;
    }

    const run = async () => {
      setWorkspaceError("");
      try {
        const [referencePayload, maybeMap] = await Promise.all([
          api.getReferenceSession(project.id, selectedOperation.id),
          api.getHeightMap(project.id, selectedOperation.id).catch((error) => {
            if (error instanceof Error && error.message.toLowerCase().includes("no existe")) {
              return null;
            }
            throw error;
          }),
        ]);
        setReferenceSession(referencePayload);
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
    setWorkOrigin({
      x_mm: referenceValue(referenceSession.origen_trabajo, "x_mm"),
      y_mm: referenceValue(referenceSession.origen_trabajo, "y_mm"),
    });
    setZReference({
      x_mm: referenceValue(referenceSession.referencia_z, "x_mm"),
      y_mm: referenceValue(referenceSession.referencia_z, "y_mm"),
      z_mm: referenceValue(referenceSession.referencia_z, "z_mm"),
    });
  }, [referenceSession]);

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
  const deleteBusy = selectedOperation ? busyKey === `delete:${selectedOperation.id}` : false;

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

  const withReferenceAction = async (action: () => Promise<ReferenceSession>) => {
    setReferenceBusy(true);
    setWorkspaceError("");
    try {
      const nextSession = await action();
      setReferenceSession(nextSession);
      setCompensationPreview(null);
    } catch (error) {
      setWorkspaceError(error instanceof Error ? error.message : "No fue posible actualizar la referencia simulada.");
    } finally {
      setReferenceBusy(false);
    }
  };

  const renderArchivo = () => (
    <div className="stack gap-md">
      <article className="panel workflow-panel">
        <div className="section-heading section-heading--stacked">
          <div>
            <p className="eyebrow">Archivo</p>
            <h3>Operaciones del proyecto</h3>
          </div>
          <p className="muted">Seleccione solo las operaciones necesarias. Ninguna acción aquí realiza movimientos físicos.</p>
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

      <article className="panel operation-detail-panel">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Archivo</p>
            <h3>{selectedOperation?.nombre ?? "Seleccione una operación"}</h3>
          </div>
          {selectedOperation ? <StatusBadge tone={toneForStatus(selectedOperation.estado)}>{translateStatus(selectedOperation.estado)}</StatusBadge> : null}
        </div>
        {selectedOperation ? (
          <>
            <dl className="definition-grid definition-grid--compact">
              <div><dt>Tipo</dt><dd>{translateOperationType(selectedOperation.tipo)}</dd></div>
              <div><dt>Cara</dt><dd>{translateFace(selectedOperation.cara)}</dd></div>
              <div><dt>Archivo</dt><dd>{selectedOperation.nombre_archivo_original ?? "Sin archivo"}</dd></div>
              <div><dt>Tamaño</dt><dd>{formatFileSize(selectedOperation.tamano_archivo_bytes)}</dd></div>
            </dl>

            {selectedOperation.analisis?.analisis_desactualizado ? (
              <div className="alert alert--warning">
                Este análisis está desactualizado. Versión actual: {selectedOperation.analisis.current_analysis_version}.
              </div>
            ) : null}

            <div className="action-grid">
              <label className="button file-button">
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
                {analysisBusy ? "Analizando archivo..." : selectedOperation.analisis?.analisis_desactualizado ? "Volver a analizar" : "Analizar archivo"}
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
          <p className="muted">Seleccione una operación configurada para gestionar archivo y análisis.</p>
        )}
      </article>
    </div>
  );

  const renderTrayectoria = () => {
    if (!selectedOperation?.analisis) {
      return <div className="panel empty-state"><p>Suba un archivo y ejecute el análisis para ver la trayectoria.</p></div>;
    }
    return (
      <div className="stack gap-md">
        <article className="panel analysis-summary-panel">
          <div className="section-heading section-heading--stacked">
            <div>
              <p className="eyebrow">Trayectoria</p>
              <h3>Alturas de la trayectoria G-code</h3>
            </div>
            <p className="muted">Estas métricas pertenecen al G-code y no se mezclan con las alturas del mapa de superficie.</p>
          </div>
          <div className="info-grid info-grid--double compact-grid">
            <div className="metric-box"><span>Movimientos</span><strong>{selectedOperation.analisis.cantidad_movimientos}</strong></div>
            <div className="metric-box"><span>Avances</span><strong>{selectedOperation.analisis.avances_mm_min.join(", ") || "-"}</strong></div>
            <div className="metric-box"><span>Z mínima</span><strong>{formatMillimeters(selectedOperation.analisis.profundidad_min_mm, 3)}</strong></div>
            <div className="metric-box"><span>Z máxima</span><strong>{formatMillimeters(selectedOperation.analisis.profundidad_max_mm, 3)}</strong></div>
          </div>
        </article>
        <article className="panel viewer-panel">
          <ToolpathViewer material={project.material} analysis={selectedOperation.analisis} operationName={selectedOperation.nombre} />
        </article>
      </div>
    );
  };

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
        <div className="workflow-steps-list">
          {(referenceSession?.pasos ?? []).map((step, index) => (
            <div className={`workflow-step-row${step.confirmado ? " workflow-step-row--complete" : ""}`} key={step.id}>
              <span className="workflow-step">{index + 1}</span>
              <span>{step.titulo}</span>
              <span className="muted">{step.fecha ? formatDate(step.fecha) : "Pendiente"}</span>
            </div>
          ))}
        </div>
      </article>

      <article className="panel">
        <div className="section-heading"><h3>1. Referencia de máquina</h3></div>
        <p className="muted">Pertenece a la sesión de máquina y solo se confirma una vez por sesión.</p>
        <button className="button" type="button" disabled={referenceBusy || referenceSession?.machine_reference.confirmada || !selectedOperation} onClick={() => void withReferenceAction(() => api.confirmMachineReference(project.id, selectedOperation!.id))}>
          {referenceSession?.machine_reference.confirmada ? "Ya confirmada en simulación" : "Confirmar en simulación"}
        </button>
      </article>

      <article className="panel">
        <div className="section-heading"><h3>2. Origen de trabajo X/Y</h3></div>
        <div className="form-grid">
          <label>
            X (mm)
            <input type="number" value={workOrigin.x_mm} onChange={(event) => setWorkOrigin((current) => ({ ...current, x_mm: Number(event.target.value) }))} />
          </label>
          <label>
            Y (mm)
            <input type="number" value={workOrigin.y_mm} onChange={(event) => setWorkOrigin((current) => ({ ...current, y_mm: Number(event.target.value) }))} />
          </label>
        </div>
        <button className="button" type="button" disabled={referenceBusy || !selectedOperation} onClick={() => void withReferenceAction(() => api.confirmWorkOrigin(project.id, selectedOperation!.id, workOrigin))}>
          Confirmar en simulación
        </button>
      </article>

      <article className="panel">
        <div className="section-heading"><h3>3. Referencia Z</h3></div>
        <div className="form-grid">
          <label>
            X (mm)
            <input type="number" value={zReference.x_mm} onChange={(event) => setZReference((current) => ({ ...current, x_mm: Number(event.target.value) }))} />
          </label>
          <label>
            Y (mm)
            <input type="number" value={zReference.y_mm} onChange={(event) => setZReference((current) => ({ ...current, y_mm: Number(event.target.value) }))} />
          </label>
          <label>
            Z de referencia (mm)
            <input type="number" value={zReference.z_mm} onChange={(event) => setZReference((current) => ({ ...current, z_mm: Number(event.target.value) }))} />
          </label>
        </div>
        <button className="button" type="button" disabled={referenceBusy || !selectedOperation} onClick={() => void withReferenceAction(() => api.confirmZReference(project.id, selectedOperation!.id, zReference))}>
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
        <p className="muted">Mapa actual: {heightMap ? `${heightMap.fuente_datos} · v${heightMap.version}` : "no disponible"}</p>
      </article>

      <article className="panel">
        <div className="section-heading"><h3>6. Validación</h3></div>
        <button className="button" type="button" disabled={referenceBusy || !selectedOperation || !heightMap} onClick={() => void withReferenceAction(() => api.validateHeightMap(project.id, selectedOperation!.id))}>
          Confirmar en simulación
        </button>
      </article>
    </div>
  );

  const renderMapa = () => (
    <div className="stack gap-md">
      <article className="panel">
        <div className="section-heading section-heading--stacked">
          <div>
            <p className="eyebrow">Mapa de alturas</p>
            <h3>Alturas de la superficie</h3>
          </div>
          <div className="toolbar-inline">
            <button className={`toolbar-pill${activeMapTab === "mapa2d" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => setActiveMapTab("mapa2d")}>Mapa 2D</button>
            <button className={`toolbar-pill${activeMapTab === "superficie3d" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => setActiveMapTab("superficie3d")}>Superficie 3D</button>
            <button className={`toolbar-pill${activeMapTab === "puntos" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => setActiveMapTab("puntos")}>Puntos</button>
            <button className={`toolbar-pill${activeMapTab === "configuracion" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => setActiveMapTab("configuracion")}>Configuración</button>
          </div>
        </div>
        <div className="toolbar-inline">
          <button className={`toolbar-pill${heightMode === "bruto" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => setHeightMode("bruto")}>Superficie bruta</button>
          <button className={`toolbar-pill${heightMode === "plano" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => setHeightMode("plano")}>Plano</button>
          <button className={`toolbar-pill${heightMode === "residuo" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => setHeightMode("residuo")}>Residuo</button>
        </div>
      </article>

      {heightMap ? (
        <article className="panel">
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

      {activeMapTab === "mapa2d" && heightMap ? <HeightMapHeatmap material={project.material} heightMap={heightMap} mode={heightMode} /> : null}
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
            const nextValue = prompted.trim() === "" ? null : Number(prompted);
            if (nextValue !== null && Number.isNaN(nextValue)) {
              setWorkspaceError("El valor Z debe ser numérico.");
              return;
            }
            await withHeightMapAction(() => api.updateHeightMapSample(project.id, selectedOperation!.id, sampleId, { z_mm: nextValue }));
          }}
        />
      ) : null}
      {activeMapTab === "configuracion" ? (
        <HeightMapControlPanel
          material={project.material}
          heightMap={heightMap}
          busy={heightMapBusy}
          onConfigure={(payload) => withHeightMapAction(() => api.configureHeightMap(project.id, selectedOperation!.id, payload))}
          onSimulate={(payload) => withHeightMapAction(() => api.simulateHeightMap(project.id, selectedOperation!.id, payload))}
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

  const renderValidacion = () => (
    <div className="stack gap-md">
      <article className="panel">
        <div className="section-heading section-heading--stacked">
          <div>
            <p className="eyebrow">Validación</p>
            <h3>Previsualización matemática de compensación</h3>
          </div>
          <p className="muted">Bloqueada si faltan referencias o el mapa no está validado. No genera ni descarga G-code ejecutable.</p>
        </div>
        <button
          className="button"
          type="button"
          disabled={referenceBusy || !selectedOperation}
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

      <div className="workspace-layout">
        <aside className="workspace-side workspace-side--sticky">
          <article className="panel">
            <div className="section-heading">
              <div>
                <p className="eyebrow">Resumen</p>
                <h3>{selectedOperation?.nombre ?? "Sin operación"}</h3>
              </div>
              {selectedOperation ? <StatusBadge tone={toneForStatus(selectedOperation.estado)}>{translateStatus(selectedOperation.estado)}</StatusBadge> : null}
            </div>
            {selectedOperation ? (
              <dl className="definition-grid definition-grid--compact">
                <div><dt>Tipo</dt><dd>{translateOperationType(selectedOperation.tipo)}</dd></div>
                <div><dt>Cara</dt><dd>{translateFace(selectedOperation.cara)}</dd></div>
                <div><dt>Archivo</dt><dd>{selectedOperation.nombre_archivo_original ?? "Sin archivo"}</dd></div>
                <div><dt>Mapa</dt><dd>{heightMap ? `${heightMap.fuente_datos} v${heightMap.version}` : "No disponible"}</dd></div>
                <div><dt>Home simulado</dt><dd>{referenceSession?.machine_reference.confirmada ? "Confirmado" : "Pendiente"}</dd></div>
                <div><dt>Validación mapa</dt><dd>{referenceSession?.pasos.find((step) => step.id === "validacion")?.confirmado ? "Confirmada" : "Pendiente"}</dd></div>
              </dl>
            ) : <p className="muted">Seleccione una operación para abrir el workspace.</p>}
          </article>
        </aside>

        <section className="workspace-main">
          <article className="panel">
            <div className="toolbar-inline toolbar-inline--wrap">
              <button className={`toolbar-pill${activeView === "archivo" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => setActiveView("archivo")}>Archivo</button>
              <button className={`toolbar-pill${activeView === "trayectoria" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => setActiveView("trayectoria")}>Trayectoria</button>
              <button className={`toolbar-pill${activeView === "referencia" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => setActiveView("referencia")}>Referencia</button>
              <button className={`toolbar-pill${activeView === "mapa" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => setActiveView("mapa")}>Mapa de alturas</button>
              <button className={`toolbar-pill${activeView === "validacion" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => setActiveView("validacion")}>Validación</button>
            </div>
          </article>

          {workspaceError ? <div className="panel alert alert--error">{workspaceError}</div> : null}

          {activeView === "archivo" ? renderArchivo() : null}
          {activeView === "trayectoria" ? renderTrayectoria() : null}
          {activeView === "referencia" ? renderReferencia() : null}
          {activeView === "mapa" ? renderMapa() : null}
          {activeView === "validacion" ? renderValidacion() : null}
        </section>
      </div>
    </div>
  );
}
