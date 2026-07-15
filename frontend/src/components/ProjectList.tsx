import { useMemo, useState } from "react";

import { formatDate } from "../lib/format";
import { toneForStatus, translateStatus } from "../lib/ui";
import type { Project } from "../types";
import { StatusBadge } from "./StatusBadge";

type ProjectListProps = {
  projects: Project[];
  selectedProjectId: string | null;
  onSelect: (projectId: string) => void;
  onCreateProject?: () => void;
  onContinueProject?: (projectId: string) => void;
  onArchiveProject?: (projectId: string) => void;
  onTrashProject?: (project: Project) => void;
  onRestoreProject?: (projectId: string) => void;
  onPermanentlyDeleteProject?: (project: Project) => void;
};

type ProjectFilter = "todos" | "recientes" | "activos" | "preparacion" | "compensar" | "ejecutar" | "completados" | "errores" | "archivados" | "papelera";

const filters: Array<{ id: ProjectFilter; label: string }> = [
  { id: "todos", label: "Todos" },
  { id: "recientes", label: "Recientes" },
  { id: "activos", label: "Activos" },
  { id: "preparacion", label: "En preparación" },
  { id: "compensar", label: "Listo para compensar" },
  { id: "ejecutar", label: "Listo para ejecutar" },
  { id: "completados", label: "Completados" },
  { id: "errores", label: "Con errores" },
  { id: "archivados", label: "Archivados" },
  { id: "papelera", label: "Papelera" },
];

function projectSearchText(project: Project) {
  return [
    project.nombre,
    project.creado_en,
    project.actualizado_en,
    project.last_opened_at ?? "",
    ...project.operaciones.flatMap((operation) => [operation.nombre, operation.herramienta ?? "", operation.tipo]),
  ].join(" ").toLowerCase();
}

function preparationState(project: Project) {
  const setup = project.montajes.find((item) => item.id === project.current_setup_id) ?? project.montajes[0];
  const preparationStatus = setup?.preparation_status ?? "sin_iniciar";
  const reference = setup?.active_reference_id || preparationStatus !== "sin_iniciar" ? "medida" : "pendiente";
  const map = setup?.active_map_id ? "medido" : preparationStatus === "mapa_disponible" ? "parcial" : "sin mapa";
  return { setup, reference, map, preparationStatus };
}

function matchesFilter(project: Project, filter: ProjectFilter, index: number) {
  const status = project.status ?? "active";
  const state = preparationState(project);
  if (filter === "todos") return status !== "trashed";
  if (filter === "recientes") return status !== "trashed" && index < 5;
  if (filter === "activos") return status === "active";
  if (filter === "archivados") return status === "archived";
  if (filter === "papelera") return status === "trashed";
  if (filter === "errores") return project.estado_general.includes("error");
  if (filter === "preparacion") return status === "active" && (state.reference === "pendiente" || state.map === "sin mapa");
  if (filter === "compensar") return status === "active" && state.reference === "medida" && state.map !== "sin mapa";
  if (filter === "ejecutar") return status === "active" && state.map === "medido";
  if (filter === "completados") return project.estado_general === "valido" && state.map === "medido";
  return true;
}

export function ProjectList({ projects, selectedProjectId, onSelect, onCreateProject, onContinueProject, onArchiveProject, onTrashProject, onRestoreProject, onPermanentlyDeleteProject }: ProjectListProps) {
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<ProjectFilter>("todos");
  const normalizedQuery = query.trim().toLowerCase();
  const visibleProjects = useMemo(() => projects
    .filter((project, index) => matchesFilter(project, filter, index))
    .filter((project) => !normalizedQuery || projectSearchText(project).includes(normalizedQuery)), [filter, normalizedQuery, projects]);

  return (
    <section className="panel panel--section">
      <div className="section-heading section-heading--stacked">
        <div>
          <p className="eyebrow">Proyectos</p>
          <h2>Historial principal</h2>
        </div>
        <div className="hero-actions">
          <label className="inline-field">Buscar<input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Nombre, herramienta, operación o fecha" /></label>
          {onCreateProject ? <button className="button button--ghost" type="button" onClick={onCreateProject}>Nuevo proyecto</button> : null}
        </div>
      </div>

      <div className="map-segmented" aria-label="Filtros de proyectos">
        {filters.map((item) => <button key={item.id} className={`map-segment-button${filter === item.id ? " map-segment-button--active" : ""}`} type="button" onClick={() => setFilter(item.id)}>{item.label}</button>)}
      </div>

      {visibleProjects.length === 0 ? (
        <div className="empty-state empty-state--compact"><h3>Sin proyectos en esta vista</h3><p>Ajuste la búsqueda o el filtro para ver otros proyectos.</p></div>
      ) : (
        <div className="project-list stack gap-sm">
          {visibleProjects.map((project) => {
            const state = preparationState(project);
            const trashed = (project.status ?? "active") === "trashed";
            return (
              <article key={project.id} className={`project-list__item${project.id === selectedProjectId ? " project-list__item--selected" : ""}`} role="button" aria-label={`Abrir ${project.nombre}`} tabIndex={0} onClick={() => onSelect(project.id)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onSelect(project.id); } }}>
                <div className="project-list__header">
                  <div><h3>{project.nombre}</h3><p className="muted">{project.doble_cara ? "PCB doble cara" : "PCB de una cara"} · última sesión {project.last_opened_at ? formatDate(project.last_opened_at) : "sin apertura"}</p></div>
                  <StatusBadge tone={toneForStatus(project.estado_general)}>{translateStatus(project.estado_general)}</StatusBadge>
                </div>
                <dl className="project-list__meta">
                  <div><dt>Creado</dt><dd>{formatDate(project.created_at ?? project.creado_en)}</dd></div>
                  <div><dt>Actualizado</dt><dd>{formatDate(project.updated_at ?? project.actualizado_en)}</dd></div>
                  <div><dt>Material</dt><dd>{project.material.ancho_mm} × {project.material.alto_mm} × {project.material.espesor_mm ?? "-"} mm</dd></div>
                  <div><dt>Montajes</dt><dd>{project.montajes.length}</dd></div>
                  <div><dt>Operaciones</dt><dd>{project.operaciones.length}</dd></div>
                  <div><dt>Referencia</dt><dd>{state.reference}</dd></div>
                  <div><dt>Mapa</dt><dd>{state.map}</dd></div>
                  <div><dt>Compensación</dt><dd>{state.map === "medido" ? "pendiente/generable" : "pendiente"}</dd></div>
                  <div><dt>Ejecución</dt><dd>{project.estado_general === "valido" ? "no iniciada" : "pendiente"}</dd></div>
                  <div><dt>Último modo</dt><dd>{state.preparationStatus}</dd></div>
                </dl>
                <div className="action-grid action-grid--inline" onClick={(event) => event.stopPropagation()}>
                  <button className="button" type="button" onClick={() => onSelect(project.id)}>Abrir</button>
                  {onContinueProject ? <button className="button button--ghost" type="button" disabled={trashed} onClick={() => onContinueProject(project.id)}>Continuar</button> : null}
                  {onArchiveProject ? <button className="button button--ghost" type="button" disabled={trashed} onClick={() => onArchiveProject(project.id)}>Archivar</button> : null}
                  {trashed ? (
                    <>
                      {onRestoreProject ? <button className="button button--ghost" type="button" onClick={() => onRestoreProject(project.id)}>Restaurar</button> : null}
                      {onPermanentlyDeleteProject ? <button className="button button--ghost button--danger" type="button" onClick={() => onPermanentlyDeleteProject(project)}>Eliminar permanentemente</button> : null}
                    </>
                  ) : onTrashProject ? <button className="button button--ghost button--danger" type="button" onClick={() => onTrashProject(project)}>Mover a Papelera</button> : null}
                </div>
              </article>
            );
          })}
        </div>
      )}
    </section>
  );
}
