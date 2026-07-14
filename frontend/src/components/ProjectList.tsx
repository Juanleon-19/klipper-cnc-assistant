import { formatDate } from "../lib/format";
import { toneForStatus, translateStatus } from "../lib/ui";
import type { Project } from "../types";
import { StatusBadge } from "./StatusBadge";

type ProjectListProps = {
  projects: Project[];
  selectedProjectId: string | null;
  onSelect: (projectId: string) => void;
  onCreateProject?: () => void;
};

export function ProjectList({ projects, selectedProjectId, onSelect, onCreateProject }: ProjectListProps) {
  return (
    <section className="panel panel--section">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Proyectos</p>
          <h2>Gestión visual</h2>
        </div>
        {onCreateProject ? (
          <button className="button button--ghost" type="button" onClick={onCreateProject}>
            Nuevo proyecto
          </button>
        ) : null}
      </div>

      {projects.length === 0 ? (
        <div className="empty-state empty-state--compact">
          <h3>Sin proyectos todavía</h3>
          <p>Cree el primer proyecto para cargar operaciones, analizar G-code y revisar la vista técnica 2D.</p>
        </div>
      ) : (
        <div className="project-list stack gap-sm">
          {projects.map((project) => (
            <button
              key={project.id}
              className={`project-list__item${project.id === selectedProjectId ? " project-list__item--selected" : ""}`}
              onClick={() => onSelect(project.id)}
              type="button"
            >
              <div className="project-list__header">
                <div>
                  <h3>{project.nombre}</h3>
                  <p className="muted">{project.doble_cara ? "PCB doble cara" : "PCB de una cara"}</p>
                </div>
                <StatusBadge tone={toneForStatus(project.estado_general)}>{translateStatus(project.estado_general)}</StatusBadge>
              </div>
              <dl className="project-list__meta">
                <div>
                  <dt>Material</dt>
                  <dd>{project.material.ancho_mm} × {project.material.alto_mm} × {project.material.espesor_mm ?? "-"} mm</dd>
                </div>
                <div>
                  <dt>Operaciones</dt>
                  <dd>{project.operaciones.length}</dd>
                </div>
                <div>
                  <dt>Actualizado</dt>
                  <dd>{formatDate(project.actualizado_en)}</dd>
                </div>
              </dl>
            </button>
          ))}
        </div>
      )}
    </section>
  );
}
