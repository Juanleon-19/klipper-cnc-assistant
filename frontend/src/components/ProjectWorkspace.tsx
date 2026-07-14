import { formatDate } from "../lib/format";
import type { Operation, Project, ProjectPayload } from "../types";
import { OperationPanel } from "./OperationPanel";
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

function toneForProjectStatus(status: string): "neutral" | "success" | "warning" | "danger" {
  if (status === "valido") {
    return "success";
  }
  if (status === "con advertencias" || status === "pendiente de analisis") {
    return "warning";
  }
  if (status === "bloqueado por errores") {
    return "danger";
  }
  return "neutral";
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
  if (!project) {
    return (
      <div className="panel empty-state">
        <p className="eyebrow">Detalle del proyecto</p>
        <h2>Seleccione un proyecto</h2>
        <p>Abra un proyecto existente o cree uno nuevo para gestionar operaciones y archivos G-code.</p>
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

  return (
    <div className="workspace-column">
      <article className="panel project-summary">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Proyecto activo</p>
            <h2>{project.nombre}</h2>
          </div>
          <StatusBadge tone={toneForProjectStatus(project.estado_general)}>{project.estado_general}</StatusBadge>
        </div>
        <div className="summary-grid">
          <div>
            <span>Material bruto</span>
            <strong>{project.material.ancho_mm} × {project.material.alto_mm} × {project.material.espesor_mm ?? "-"} mm</strong>
          </div>
          <div>
            <span>Configuracion</span>
            <strong>{project.doble_cara ? `Doble cara, volteo ${project.eje_volteo?.toUpperCase()}` : "Una cara"}</strong>
          </div>
          <div>
            <span>Operaciones configuradas</span>
            <strong>{project.operaciones.length}</strong>
          </div>
          <div>
            <span>Ultima actualizacion</span>
            <strong>{formatDate(project.actualizado_en)}</strong>
          </div>
        </div>
      </article>

      <ProjectForm initialValue={payload} mode="edit" onSubmit={onSaveProject} submitting={savingProject} />

      <section>
        <div className="section-heading section-heading--stacked">
          <div>
            <p className="eyebrow">Operaciones del proyecto</p>
            <h2>Seleccion y analisis</h2>
          </div>
          <p className="muted">
            No se ejecuta ningun movimiento. Solo se almacenan archivos originales y se analiza su contenido.
          </p>
        </div>
        <OperationPanel
          project={project}
          busyKey={busyKey}
          onAddOperation={onAddOperation}
          onDeleteOperation={onDeleteOperation}
          onRemoveFile={onRemoveFile}
          onAnalyze={onAnalyze}
          onUploadFile={onUploadFile}
        />
      </section>
    </div>
  );
}
