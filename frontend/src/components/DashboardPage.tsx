import { formatDate } from "../lib/format";
import {
  countBlockedOperations,
  countPendingOperations,
  countWarningOperations,
  summarizeMachineMode,
  toneForStatus,
  translateStatus,
} from "../lib/ui";
import type { HealthResponse, MachineSession, Project } from "../types";
import { StatusBadge } from "./StatusBadge";

type DashboardPageProps = {
  projects: Project[];
  recentProject: Project | null;
  health: HealthResponse | null;
  machineSession: MachineSession | null;
  onCreateProject: () => void;
  onOpenProject: (projectId: string) => void;
  onGoToProjects: () => void;
};

export function DashboardPage({
  projects,
  recentProject,
  health,
  machineSession,
  onCreateProject,
  onOpenProject,
  onGoToProjects,
}: DashboardPageProps) {
  const pendingOperations = countPendingOperations(projects);
  const warningOperations = countWarningOperations(projects);
  const blockedOperations = countBlockedOperations(projects);

  return (
    <div className="page-stack">
      <article className="panel hero-panel">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Inicio</p>
            <h2>Panel de trabajo</h2>
          </div>
          <div className="hero-actions">
            <button className="button" type="button" onClick={onCreateProject}>Nuevo proyecto</button>
            <button className="button button--ghost" type="button" onClick={onGoToProjects}>Abrir proyectos</button>
          </div>
        </div>
        <div className="hero-grid">
          <div>
            <span className="eyebrow">Aplicación</span>
            <strong>{health?.estado === "ok" ? "Operativa" : "Cargando"}</strong>
            <p className="muted">{summarizeMachineMode(health?.modo_maquina)}</p>
          </div>
          <div>
            <span className="eyebrow">Proyectos</span>
            <strong>{projects.length}</strong>
            <p className="muted">Persistentes en almacenamiento local</p>
          </div>
          <div>
            <span className="eyebrow">Pendientes</span>
            <strong>{pendingOperations}</strong>
            <p className="muted">Operaciones aún sin completar</p>
          </div>
          <div>
            <span className="eyebrow">Advertencias</span>
            <strong>{warningOperations}</strong>
            <p className="muted">Archivos que requieren revisión</p>
          </div>
        </div>
      </article>

      <div className="dashboard-grid">
        <article className="panel dashboard-card dashboard-card--large">
          <div className="section-heading">
            <div>
              <p className="eyebrow">Proyecto reciente</p>
              <h3>{recentProject?.nombre ?? "Sin proyectos"}</h3>
            </div>
            {recentProject ? <StatusBadge tone={toneForStatus(recentProject.estado_general)}>{translateStatus(recentProject.estado_general)}</StatusBadge> : null}
          </div>
          {recentProject ? (
            <>
              <dl className="definition-grid definition-grid--compact">
                <div><dt>Material</dt><dd>{recentProject.material.ancho_mm} × {recentProject.material.alto_mm} × {recentProject.material.espesor_mm ?? "-"} mm</dd></div>
                <div><dt>Configuración</dt><dd>{recentProject.doble_cara ? `Doble cara, volteo ${recentProject.eje_volteo?.toUpperCase()}` : "Una cara"}</dd></div>
                <div><dt>Operaciones</dt><dd>{recentProject.operaciones.length}</dd></div>
                <div><dt>Actualizado</dt><dd>{formatDate(recentProject.actualizado_en)}</dd></div>
              </dl>
              <div className="hero-actions">
                <button className="button" type="button" onClick={() => onOpenProject(recentProject.id)}>Abrir espacio de trabajo</button>
              </div>
            </>
          ) : (
            <div className="empty-state empty-state--compact">
              <p>Empiece creando un proyecto PCB para configurar operaciones y analizar trayectorias.</p>
            </div>
          )}
        </article>

        <article className="panel dashboard-card">
          <div className="section-heading">
            <h3>Operaciones pendientes</h3>
            <StatusBadge tone="warning">{pendingOperations}</StatusBadge>
          </div>
          <p className="muted">Incluye operaciones sin archivo y archivos todavía no analizados.</p>
        </article>

        <article className="panel dashboard-card">
          <div className="section-heading">
            <h3>Archivos con advertencias</h3>
            <StatusBadge tone={warningOperations > 0 ? "warning" : "success"}>{warningOperations}</StatusBadge>
          </div>
          <p className="muted">No se ejecuta nada en máquina. La revisión sigue siendo únicamente visual y analítica.</p>
        </article>

        <article className="panel dashboard-card">
          <div className="section-heading">
            <h3>Bloqueadas por errores</h3>
            <StatusBadge tone={blockedOperations > 0 ? "danger" : "success"}>{blockedOperations}</StatusBadge>
          </div>
          <p className="muted">Los errores críticos del analizador se muestran antes de cualquier fase posterior.</p>
        </article>

        <article className="panel dashboard-card dashboard-card--wide">
          <div className="section-heading">
            <h3>Estado resumido</h3>
            <StatusBadge tone="info">{summarizeMachineMode(machineSession?.estado)}</StatusBadge>
          </div>
          <ul className="chip-list">
            {(machineSession?.operaciones_permitidas ?? []).map((item) => (
              <li key={item} className="chip chip--info">{translateStatus(item)}</li>
            ))}
          </ul>
        </article>
      </div>
    </div>
  );
}
