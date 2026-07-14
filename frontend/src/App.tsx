import { useEffect, useMemo, useState } from "react";

import { api } from "./lib/api";
import { operationPresets } from "./lib/presets";
import type {
  HealthResponse,
  MachineSession,
  Operation,
  Project,
  ProjectPayload,
  SystemInfoResponse,
} from "./types";
import { ProjectForm } from "./components/ProjectForm";
import { ProjectList } from "./components/ProjectList";
import { ProjectWorkspace } from "./components/ProjectWorkspace";
import { StatusBadge } from "./components/StatusBadge";
import { SystemBanner } from "./components/SystemBanner";
import { SystemPage } from "./components/SystemPage";

type View = "panel" | "proyectos" | "sistema";

export default function App() {
  const [view, setView] = useState<View>("proyectos");
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [systemInfo, setSystemInfo] = useState<SystemInfoResponse | null>(null);
  const [machineSession, setMachineSession] = useState<MachineSession | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshingSystem, setRefreshingSystem] = useState(false);
  const [error, setError] = useState<string>("");
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [creatingProject, setCreatingProject] = useState(false);
  const [savingProject, setSavingProject] = useState(false);

  const selectedProject = useMemo(
    () => projects.find((project) => project.id === selectedProjectId) ?? null,
    [projects, selectedProjectId]
  );

  const summary = useMemo(() => {
    const operations = projects.flatMap((project) => project.operaciones);
    return {
      totalProjects: projects.length,
      totalOperations: operations.length,
      blockedOperations: operations.filter((operation) => operation.estado === "bloqueada por errores").length,
    };
  }, [projects]);

  const loadProjects = async () => {
    const payload = await api.listProjects();
    setProjects(payload);
    setSelectedProjectId((current) => current ?? payload[0]?.id ?? null);
  };

  const loadSystem = async () => {
    const [healthPayload, infoPayload, machinePayload] = await Promise.all([
      api.getHealth(),
      api.getSystemInfo(),
      api.getMachineSession(),
    ]);
    setHealth(healthPayload);
    setSystemInfo(infoPayload);
    setMachineSession(machinePayload);
  };

  const syncProject = async (projectId: string) => {
    const project = await api.getProject(projectId);
    setProjects((current) => current.map((item) => (item.id === projectId ? project : item)));
    return project;
  };

  useEffect(() => {
    const run = async () => {
      setLoading(true);
      setError("");
      try {
        await Promise.all([loadProjects(), loadSystem()]);
      } catch (requestError) {
        setError(requestError instanceof Error ? requestError.message : "No fue posible cargar la aplicacion.");
      } finally {
        setLoading(false);
      }
    };
    void run();
  }, []);

  const handleCreateProject = async (payload: ProjectPayload) => {
    setCreatingProject(true);
    setError("");
    try {
      const project = await api.createProject(payload);
      setProjects((current) => [project, ...current]);
      setSelectedProjectId(project.id);
      setView("proyectos");
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "No fue posible crear el proyecto.");
    } finally {
      setCreatingProject(false);
    }
  };

  const handleSaveProject = async (payload: ProjectPayload) => {
    if (!selectedProjectId) {
      return;
    }
    setSavingProject(true);
    setError("");
    try {
      await syncProject((await api.updateProject(selectedProjectId, payload)).id);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "No fue posible actualizar el proyecto.");
    } finally {
      setSavingProject(false);
    }
  };

  const handleAddOperation = async (presetKey: string) => {
    if (!selectedProjectId) {
      return;
    }
    const preset = operationPresets.find((item) => item.clave === presetKey);
    if (!preset) {
      return;
    }
    setBusyKey(`add:${presetKey}`);
    setError("");
    try {
      await api.addOperation(selectedProjectId, {
        nombre: preset.etiqueta,
        tipo: preset.tipo,
        cara: preset.cara,
        orden: preset.orden,
        herramienta: preset.herramienta,
      });
      await syncProject(selectedProjectId);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "No fue posible crear la operacion.");
    } finally {
      setBusyKey(null);
    }
  };

  const handleDeleteOperation = async (operation: Operation) => {
    if (!selectedProjectId) {
      return;
    }
    setBusyKey(`delete:${operation.id}`);
    setError("");
    try {
      await api.deleteOperation(selectedProjectId, operation.id);
      await syncProject(selectedProjectId);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "No fue posible eliminar la operacion.");
    } finally {
      setBusyKey(null);
    }
  };

  const handleRemoveFile = async (operation: Operation) => {
    if (!selectedProjectId) {
      return;
    }
    setBusyKey(`file:${operation.id}`);
    setError("");
    try {
      await api.removeOperationFile(selectedProjectId, operation.id);
      await syncProject(selectedProjectId);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "No fue posible quitar la asociacion del archivo.");
    } finally {
      setBusyKey(null);
    }
  };

  const handleAnalyze = async (operation: Operation) => {
    if (!selectedProjectId) {
      return;
    }
    setBusyKey(`analyze:${operation.id}`);
    setError("");
    try {
      await api.analyzeOperation(selectedProjectId, operation.id);
      await syncProject(selectedProjectId);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "No fue posible analizar el archivo G-code.");
    } finally {
      setBusyKey(null);
    }
  };

  const handleUploadFile = async (operation: Operation, file: File) => {
    if (!selectedProjectId) {
      return;
    }
    setBusyKey(`file:${operation.id}`);
    setError("");
    try {
      await api.uploadOperationFile(selectedProjectId, operation.id, file);
      await syncProject(selectedProjectId);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "No fue posible cargar el archivo G-code.");
    } finally {
      setBusyKey(null);
    }
  };

  const refreshSystem = async () => {
    setRefreshingSystem(true);
    setError("");
    try {
      await loadSystem();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "No fue posible actualizar el diagnostico.");
    } finally {
      setRefreshingSystem(false);
    }
  };

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div>
          <p className="eyebrow">Klipper CNC Assistant</p>
          <h1>Aplicacion web remota MVP</h1>
          <p className="muted">Gestion de proyectos PCB, analisis G-code y diagnostico seguro.</p>
        </div>
        <SystemBanner />
        <nav className="sidebar-nav">
          <button className={view === "proyectos" ? "nav-link nav-link--active" : "nav-link"} onClick={() => setView("proyectos")} type="button">
            Proyectos
          </button>
          <button className={view === "panel" ? "nav-link nav-link--active" : "nav-link"} onClick={() => setView("panel")} type="button">
            Crear proyecto
          </button>
          <button className={view === "sistema" ? "nav-link nav-link--active" : "nav-link"} onClick={() => setView("sistema")} type="button">
            Sistema
          </button>
        </nav>
        <div className="sidebar-stats">
          <div className="stat-card">
            <span>Proyectos</span>
            <strong>{summary.totalProjects}</strong>
          </div>
          <div className="stat-card">
            <span>Operaciones</span>
            <strong>{summary.totalOperations}</strong>
          </div>
          <div className="stat-card">
            <span>Bloqueadas</span>
            <strong>{summary.blockedOperations}</strong>
          </div>
        </div>
      </aside>

      <main className="main-content">
        <header className="page-header">
          <div>
            <p className="eyebrow">Estado de la aplicacion</p>
            <h2>{health?.estado === "ok" ? "API operativa y almacenamiento disponible" : "Cargando estado"}</h2>
          </div>
          <StatusBadge tone={health?.modo_maquina === "simulado" ? "info" : "danger"}>
            {health?.modo_maquina ?? "sin datos"}
          </StatusBadge>
        </header>

        {error ? <div className="alert alert--error">{error}</div> : null}
        {loading ? <div className="panel empty-state"><h2>Cargando aplicacion...</h2></div> : null}

        {!loading && view === "panel" ? (
          <div className="workspace-column">
            <ProjectForm mode="create" onSubmit={handleCreateProject} submitting={creatingProject} />
          </div>
        ) : null}

        {!loading && view === "proyectos" ? (
          <div className="page-grid">
            <section className="page-grid__sidebar">
              <ProjectList projects={projects} selectedProjectId={selectedProjectId} onSelect={setSelectedProjectId} />
            </section>
            <section className="page-grid__main">
              <ProjectWorkspace
                project={selectedProject}
                busyKey={busyKey}
                savingProject={savingProject}
                onSaveProject={handleSaveProject}
                onAddOperation={handleAddOperation}
                onDeleteOperation={handleDeleteOperation}
                onRemoveFile={handleRemoveFile}
                onAnalyze={handleAnalyze}
                onUploadFile={handleUploadFile}
              />
            </section>
          </div>
        ) : null}

        {!loading && view === "sistema" ? (
          <SystemPage
            health={health}
            systemInfo={systemInfo}
            machineSession={machineSession}
            refreshing={refreshingSystem}
            onRefresh={refreshSystem}
          />
        ) : null}
      </main>
    </div>
  );
}
