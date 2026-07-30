import type { ChangeEvent } from "react";

import { formatFileSize, formatMillimeters } from "../lib/format";
import { operationPresets } from "../lib/presets";
import type { Operation, Project } from "../types";
import { StatusBadge } from "./StatusBadge";
import { ToolpathPreview } from "./ToolpathPreview";

type OperationPanelProps = {
  project: Project;
  busyKey: string | null;
  onAddOperation: (presetKey: string) => Promise<void>;
  onDeleteOperation: (operation: Operation) => Promise<void>;
  onRemoveFile: (operation: Operation) => Promise<void>;
  onAnalyze: (operation: Operation) => Promise<void>;
  onUploadFile: (operation: Operation, file: File) => Promise<void>;
};

function findOperation(project: Project, presetKey: string): Operation | undefined {
  const preset = operationPresets.find((item) => item.clave === presetKey);
  if (!preset) {
    return undefined;
  }
  return project.operaciones.find(
    (operation) => operation.tipo === preset.tipo && operation.cara === preset.cara
  );
}

function badgeToneForOperation(status: string): "neutral" | "success" | "warning" | "danger" {
  if (status === "valida") {
    return "success";
  }
  if (status === "con advertencias" || status === "lista para analizar") {
    return "warning";
  }
  if (status === "bloqueada por errores") {
    return "danger";
  }
  return "neutral";
}

export function OperationPanel({
  project,
  busyKey,
  onAddOperation,
  onDeleteOperation,
  onRemoveFile,
  onAnalyze,
  onUploadFile,
}: OperationPanelProps) {
  const disabledLowerFace = !project.doble_cara;

  return (
    <div className="operations-grid">
      {operationPresets.map((preset) => {
        const operation = findOperation(project, preset.clave);
        const slotDisabled = preset.cara === "inferior" && disabledLowerFace;

        if (!operation) {
          return (
            <article className="panel operation-slot" key={preset.clave}>
              <div className="section-heading">
                <div>
                  <p className="eyebrow">Operacion</p>
                  <h3>{preset.etiqueta}</h3>
                </div>
                <StatusBadge tone={slotDisabled ? "neutral" : "info"}>
                  {slotDisabled ? "Disponible solo para PCB doble cara" : "Sin configurar"}
                </StatusBadge>
              </div>
              <p className="muted">{preset.descripcion}</p>
              <button
                className="button"
                disabled={slotDisabled || busyKey === `add:${preset.clave}`}
                onClick={() => onAddOperation(preset.clave)}
                type="button"
              >
                {busyKey === `add:${preset.clave}` ? "Agregando..." : "Seleccionar operacion"}
              </button>
            </article>
          );
        }

        const fileBusy = busyKey === `file:${operation.id}`;
        const analyzeBusy = busyKey === `analyze:${operation.id}`;
        const deleteBusy = busyKey === `delete:${operation.id}`;

        const handleFileChange = async (event: ChangeEvent<HTMLInputElement>) => {
          const file = event.target.files?.[0];
          if (!file) {
            return;
          }
          await onUploadFile(operation, file);
          event.target.value = "";
        };

        return (
          <article className="panel operation-card" key={operation.id}>
            <div className="section-heading">
              <div>
                <p className="eyebrow">Operacion</p>
                <h3>{preset.etiqueta}</h3>
              </div>
              <StatusBadge tone={badgeToneForOperation(operation.estado)}>{operation.estado}</StatusBadge>
            </div>

            <dl className="operation-meta">
              <div>
                <dt>Tipo</dt>
                <dd>{operation.tipo}</dd>
              </div>
              <div>
                <dt>Archivo asociado</dt>
                <dd>{operation.nombre_archivo_original ?? "Sin archivo"}</dd>
              </div>
              <div>
                <dt>Tamano</dt>
                <dd>{formatFileSize(operation.tamano_archivo_bytes)}</dd>
              </div>
              <div>
                <dt>Herramienta</dt>
                <dd>{operation.herramienta ?? "Sin definir"}</dd>
              </div>
            </dl>

            <div className="operation-actions">
              <label className="button button--ghost file-button">
                {operation.archivo_gcode ? "Reemplazar archivo" : "Cargar archivo"}
                <input type="file" accept=".nc,.gcode,.tap" onChange={handleFileChange} disabled={fileBusy} />
              </label>
              <button
                className="button button--ghost"
                type="button"
                disabled={!operation.archivo_gcode || analyzeBusy}
                onClick={() => onAnalyze(operation)}
              >
                {analyzeBusy ? "Analizando..." : "Analizar G-code"}
              </button>
              <button
                className="button button--ghost"
                type="button"
                disabled={!operation.archivo_gcode}
                onClick={async () => {
                  if (window.confirm("Se quitara la asociacion del archivo actual. Desea continuar?")) {
                    await onRemoveFile(operation);
                  }
                }}
              >
                Eliminar asociacion
              </button>
              <button
                className="button button--ghost button--danger"
                type="button"
                disabled={deleteBusy}
                onClick={async () => {
                  if (window.confirm("La operacion seleccionada se eliminara del proyecto. Desea continuar?")) {
                    await onDeleteOperation(operation);
                  }
                }}
              >
                Eliminar operacion
              </button>
            </div>

            {operation.analisis ? (
              <div className="analysis-block">
                <div className="analysis-highlights">
                  <div>
                    <span>Movimientos</span>
                    <strong>{operation.analisis.cantidad_movimientos}</strong>
                  </div>
                  <div>
                    <span>Avances</span>
                    <strong>{operation.analisis.avances_mm_min.join(", ") || "-"}</strong>
                  </div>
                  <div>
                    <span>Profundidad minima</span>
                    <strong>{formatMillimeters(operation.analisis.profundidad_min_mm)}</strong>
                  </div>
                </div>

                {operation.analisis.comandos_manuales.length > 0 ? (
                  <div className="tag-strip">
                    {operation.analisis.comandos_manuales.map((command) => (
                      <span className="tag" key={command}>{command}</span>
                    ))}
                  </div>
                ) : null}

                {operation.analisis.incidencias.length > 0 ? (
                  <ul className="issue-list">
                    {operation.analisis.incidencias.map((issue, index) => (
                      <li key={`${issue.codigo}-${index}`}>{issue.mensaje}</li>
                    ))}
                  </ul>
                ) : null}

                <ToolpathPreview material={project.material} analysis={operation.analisis} />
              </div>
            ) : (
              <p className="muted">Suba un archivo y ejecute el analisis para ver limites, advertencias y vista previa 2D.</p>
            )}
          </article>
        );
      })}
    </div>
  );
}
