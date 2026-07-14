import { formatDate } from "../lib/format";
import type { Project } from "../types";
import { StatusBadge } from "./StatusBadge";

type ProjectListProps = {
  projects: Project[];
  selectedProjectId: string | null;
  onSelect: (projectId: string) => void;
};

function toneForStatus(status: string): "neutral" | "success" | "warning" | "danger" {
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

export function ProjectList({ projects, selectedProjectId, onSelect }: ProjectListProps) {
  if (projects.length === 0) {
    return (
      <div className="panel empty-state">
        <p className="eyebrow">Proyectos</p>
        <h2>Sin proyectos todavia</h2>
        <p>
          Cree el primer proyecto PCB para cargar operaciones, analizar G-code y revisar la vista
          previa 2D en modo simulado.
        </p>
      </div>
    );
  }

  return (
    <div className="project-list">
      {projects.map((project) => (
        <button
          key={project.id}
          className={`panel project-card${project.id === selectedProjectId ? " project-card--selected" : ""}`}
          onClick={() => onSelect(project.id)}
          type="button"
        >
          <div className="project-card__header">
            <div>
              <p className="eyebrow">Proyecto</p>
              <h3>{project.nombre}</h3>
            </div>
            <StatusBadge tone={toneForStatus(project.estado_general)}>{project.estado_general}</StatusBadge>
          </div>
          <div className="project-card__meta">
            <span>{project.doble_cara ? "PCB doble cara" : "PCB de una cara"}</span>
            <span>{project.operaciones.length} operaciones</span>
          </div>
          <dl className="project-card__stats">
            <div>
              <dt>Material</dt>
              <dd>
                {project.material.ancho_mm} × {project.material.alto_mm} mm
              </dd>
            </div>
            <div>
              <dt>Ultima actualizacion</dt>
              <dd>{formatDate(project.actualizado_en)}</dd>
            </div>
          </dl>
        </button>
      ))}
    </div>
  );
}
