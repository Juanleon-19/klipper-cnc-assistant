import { useEffect, useMemo, useState } from "react";

import { DashboardPage } from "./components/DashboardPage";
import { ProjectForm } from "./components/ProjectForm";
import { ProjectList } from "./components/ProjectList";
import { ProjectWorkspace } from "./components/ProjectWorkspace";
import { StatusBadge } from "./components/StatusBadge";
import { SystemBanner } from "./components/SystemBanner";
import { SystemPage } from "./components/SystemPage";
import { api, type OperationInput, type OperationUpdateInput } from "./lib/api";
import { getRecentProject, summarizeMachineMode, toneForStatus, translateStatus } from "./lib/ui";
import type {
  HealthResponse,
  MachineRuntime,
  MachineSession,
  Operation,
  Project,
  ProjectPayload,
  SystemInfoResponse,
} from "./types";

type View = "inicio" | "proyectos" | "nuevo" | "sistema";

type NavItem = {
  id: View;
  label: string;
  shortLabel: string;
  icon: string;
};

const FRONTEND_SCHEMA_VERSION = "1.5";
const FRONTEND_BUILD = "0.1.0";

const navItems: NavItem[] = [
  { id: "inicio", label: "Inicio", shortLabel: "Inicio", icon: "⌂" },
  { id: "proyectos", label: "Proyectos", shortLabel: "PCB", icon: "▣" },
  { id: "nuevo", label: "Nuevo proyecto", shortLabel: "Nuevo", icon: "+" },
  { id: "sistema", label: "Sistema", shortLabel: "Sistema", icon: "⚙" },
];

function useViewportWidth() {
  const [width, setWidth] = useState(() => window.innerWidth);
  useEffect(() => {
    const handleResize = () => setWidth(window.innerWidth);
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);
  return width;
}

export default function App() {
  const [view, setView] = useState<View>("inicio");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [systemInfo, setSystemInfo] = useState<SystemInfoResponse | null>(null);
  const [machineSession, setMachineSession] = useState<MachineSession | null>(null);
  const [machineRuntime, setMachineRuntime] = useState<MachineRuntime | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshingSystem, setRefreshingSystem] = useState(false);
  const [error, setError] = useState("");
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [creatingProject, setCreatingProject] = useState(false);
  const [savingProject, setSavingProject] = useState(false);
  const viewportWidth = useViewportWidth();
  const isDesktop = viewportWidth >= 1200;
  const isTabletOrSmaller = viewportWidth < 1200;

  const selectedProject = useMemo(
    () => projects.find((project) => project.id === selectedProjectId) ?? null,
    [projects, selectedProjectId]
  );
  const recentProject = useMemo(() => getRecentProject(projects), [projects]);
  const appIncompatible = Boolean(systemInfo && systemInfo.schema_version !== FRONTEND_SCHEMA_VERSION);

  useEffect(() => {
    if (isDesktop) {
      setSidebarOpen(false);
      return;
    }
    setSidebarCollapsed(false);
  }, [isDesktop]);

  useEffect(() => {
    if (!isTabletOrSmaller || !sidebarOpen) {
      document.body.style.overflow = "";
      return;
    }
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = "";
    };
  }, [isTabletOrSmaller, sidebarOpen]);

  const handleSelectView = (nextView: View) => {
    setView(nextView);
    setSidebarOpen(false);
  };

  const loadProjects = async () => {
    const payload = await api.listProjects();
    setProjects(payload);
    setSelectedProjectId((current) => current ?? payload[0]?.id ?? null);
  };

  const loadSystem = async () => {
    const [healthPayload, infoPayload, machinePayload, runtimePayload] = await Promise.all([
      api.getHealth(),
      api.getSystemInfo(),
      api.getMachineSession(),
      api.getMachineRuntime(),
    ]);
    setHealth(healthPayload);
    setSystemInfo(infoPayload);
    setMachineSession(machinePayload);
    setMachineRuntime(runtimePayload);
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
        setError(requestError instanceof Error ? requestError.message : "No fue posible cargar la aplicación.");
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
      handleSelectView("proyectos");
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

  const handleAddSetup = async (nombre: string) => {
    if (!selectedProjectId) {
      return;
    }
    setBusyKey("setup:add");
    setError("");
    try {
      await api.addSetup(selectedProjectId, nombre);
      await syncProject(selectedProjectId);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "No fue posible crear el montaje.");
    } finally {
      setBusyKey(null);
    }
  };

  const handleAddOperation = async (payload: OperationInput) => {
    if (!selectedProjectId) {
      return;
    }
    setBusyKey("operation:add");
    setError("");
    try {
      await api.addOperation(selectedProjectId, payload);
      await syncProject(selectedProjectId);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "No fue posible crear la operación.");
    } finally {
      setBusyKey(null);
    }
  };

  const handleUpdateOperation = async (operationId: string, payload: OperationUpdateInput) => {
    if (!selectedProjectId) {
      return;
    }
    setBusyKey("operation:update:" + operationId);
    setError("");
    try {
      await api.updateOperation(selectedProjectId, operationId, payload);
      await syncProject(selectedProjectId);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "No fue posible actualizar la operación.");
    } finally {
      setBusyKey(null);
    }
  };

  const handleDuplicateOperation = async (operationId: string) => {
    if (!selectedProjectId) {
      return;
    }
    setBusyKey("operation:duplicate:" + operationId);
    setError("");
    try {
      await api.duplicateOperation(selectedProjectId, operationId);
      await syncProject(selectedProjectId);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "No fue posible duplicar la operación.");
    } finally {
      setBusyKey(null);
    }
  };

  const handleMoveOperation = async (operationId: string, direction: "up" | "down") => {
    if (!selectedProjectId) {
      return;
    }
    setBusyKey("operation:move:" + operationId);
    setError("");
    try {
      await api.moveOperation(selectedProjectId, operationId, direction);
      await syncProject(selectedProjectId);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "No fue posible reordenar la operación.");
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
      setError(requestError instanceof Error ? requestError.message : "No fue posible eliminar la operación.");
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
      setError(requestError instanceof Error ? requestError.message : "No fue posible quitar la asociación del archivo.");
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
      setError(requestError instanceof Error ? requestError.message : "No fue posible actualizar el diagnóstico.");
    } finally {
      setRefreshingSystem(false);
    }
  };

  const handleMachineAction = async (action: string, targetZ?: number) => {
    setRefreshingSystem(true);
    setError("");
    try {
      let runtime: MachineRuntime;
      if (action === "connect") runtime = await api.connectMachine();
      else if (action === "disconnect") runtime = await api.disconnectMachine();
      else if (action === "diagnostic") runtime = await api.setMachineDiagnosticMode(true);
      else if (action === "initialize") runtime = await api.initializeMachine(targetZ ?? 0);
      else if (action === "manual-on") runtime = await api.setManualControl(true);
      else if (action === "manual-off") runtime = await api.setManualControl(false);
      else if (action === "probe-request") runtime = await api.requestProbe();
      else if (action === "probe-confirm") runtime = await api.confirmProbe();
      else if (action === "cancel") runtime = await api.cancelMachineOperation();
      else if (action === "safe-stop") runtime = await api.safeStopMachine();
      else if (action === "emergency") runtime = await api.emergencyStopMachine();
      else runtime = await api.getMachineRuntime();
      setMachineRuntime(runtime);
      await loadSystem();
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "No fue posible completar la acción física.");
    } finally {
      setRefreshingSystem(false);
    }
  };

  const titleByView: Record<View, { eyebrow: string; title: string; description: string }> = {
    inicio: {
      eyebrow: "Operación remota",
      title: "Panel principal",
      description: "Acceso privado, análisis G-code, visor técnico y mapa de alturas completamente simulado.",
    },
    proyectos: {
      eyebrow: "Espacio de trabajo",
      title: selectedProject?.nombre ?? "Proyectos",
      description: "Flujo visual de operaciones, visor 2D/3D y análisis del material sin movimientos físicos.",
    },
    nuevo: {
      eyebrow: "Nuevo proyecto",
      title: "Definición del material y la PCB",
      description: "Configure nombre, dimensiones, doble cara, eje de volteo y agujeros de alineación.",
    },
    sistema: {
      eyebrow: "Diagnóstico",
      title: "Sistema y servicio",
      description: "Estado seguro de la API, almacenamiento y sesión de máquina simulada.",
    },
  };

  const header = titleByView[view];

  const sidebarExpanded = isDesktop && !sidebarCollapsed;
  const sidebarVisible = isDesktop || sidebarOpen;

  return (
    <div className={`app-shell${isDesktop ? " app-shell--desktop" : " app-shell--drawer"}${sidebarCollapsed && isDesktop ? " app-shell--collapsed" : ""}${sidebarOpen && !isDesktop ? " app-shell--sidebar-open" : ""}`}>
      {!isDesktop ? (
        <button className={`shell-backdrop${sidebarOpen ? " shell-backdrop--visible" : ""}`} type="button" aria-label="Cerrar menú" onClick={() => setSidebarOpen(false)} />
      ) : null}

      <aside className={`sidebar${sidebarVisible ? " sidebar--visible" : ""}`} aria-label="Navegación lateral">
        <div className="sidebar__top">
          <div className="sidebar__brand">
            <p className="eyebrow">Klipper CNC Assistant</p>
            <h1>{sidebarExpanded ? "Visor técnico y mapa de alturas" : "KCA"}</h1>
            {sidebarExpanded ? <p className="muted">Aplicación privada para preparación remota de PCB en modo simulado.</p> : null}
          </div>
          {isDesktop ? (
            <button
              className="icon-button icon-button--sidebar"
              type="button"
              aria-label={sidebarCollapsed ? "Abrir menú" : "Cerrar menú"}
              aria-expanded={!sidebarCollapsed}
              title={sidebarCollapsed ? "Abrir menú" : "Cerrar menú"}
              onClick={() => setSidebarCollapsed((current) => !current)}
            >
              {sidebarCollapsed ? "☰" : "✕"}
            </button>
          ) : null}
        </div>

        <SystemBanner />

        <nav className="sidebar-nav" aria-label="Navegación principal">
          {navItems.map((item) => (
            <button
              key={item.id}
              className={`nav-link${view === item.id ? " nav-link--active" : ""}${sidebarCollapsed && isDesktop ? " nav-link--compact" : ""}`}
              type="button"
              aria-label={item.label}
              title={sidebarCollapsed && isDesktop ? item.label : undefined}
              onClick={() => handleSelectView(item.id)}
            >
              <span className="nav-link__icon" aria-hidden="true">{item.icon}</span>
              {(!isDesktop || !sidebarCollapsed) ? <span className="nav-link__label">{item.label}</span> : null}
            </button>
          ))}
        </nav>

        <div className={`sidebar-stats${sidebarCollapsed && isDesktop ? " sidebar-stats--compact" : ""}`}>
          <div className="stat-card"><span>API</span><strong>{translateStatus(health?.estado)}</strong></div>
          <div className="stat-card"><span>Modo</span><strong>{summarizeMachineMode(health?.modo_maquina)}</strong></div>
          <div className="stat-card"><span>Proyectos</span><strong>{projects.length}</strong></div>
        </div>
      </aside>

      <main className="main-area">
        <header className="topbar topbar--app">
          <div className="topbar__title">
            {!isDesktop ? (
              <button
                className="icon-button topbar__menu"
                type="button"
                aria-label={sidebarOpen ? "Cerrar menú" : "Abrir menú"}
                aria-expanded={sidebarOpen}
                title={sidebarOpen ? "Cerrar menú" : "Abrir menú"}
                onClick={() => setSidebarOpen((current) => !current)}
              >
                {sidebarOpen ? "✕" : "☰"}
              </button>
            ) : null}
            <div>
              <p className="eyebrow">{header.eyebrow}</p>
              <h2>{header.title}</h2>
              <p className="muted">{header.description}</p>
            </div>
          </div>
          <div className="topbar__actions">
            <StatusBadge tone={toneForStatus(machineSession?.estado ?? health?.modo_maquina)}>{summarizeMachineMode(machineSession?.estado ?? health?.modo_maquina)}</StatusBadge>
            <StatusBadge tone={toneForStatus(health?.estado)}>{translateStatus(health?.estado)}</StatusBadge>
          </div>
        </header>

        {error ? <div className="alert alert--error">{error}</div> : null}
        {appIncompatible ? (
          <div className="alert alert--error compatibility-alert" role="alert">
            <div><strong>La aplicación necesita actualizarse</strong><p>Frontend {FRONTEND_BUILD} · esquema {FRONTEND_SCHEMA_VERSION}; backend {systemInfo?.backend_version} · esquema {systemInfo?.schema_version}.</p></div>
            <button className="button" type="button" onClick={() => window.location.reload()}>Recargar aplicación</button>
          </div>
        ) : null}

        <div className="content-wrap">
          {loading ? <div className="panel empty-state"><h3>Cargando aplicación...</h3></div> : null}

          {!loading && !appIncompatible && view === "inicio" ? (
            <DashboardPage
              projects={projects}
              recentProject={recentProject}
              health={health}
              machineSession={machineSession}
              onCreateProject={() => handleSelectView("nuevo")}
              onOpenProject={(projectId) => {
                setSelectedProjectId(projectId);
                handleSelectView("proyectos");
              }}
              onGoToProjects={() => handleSelectView("proyectos")}
            />
          ) : null}

          {!loading && !appIncompatible && view === "nuevo" ? (
            <ProjectForm mode="create" onSubmit={handleCreateProject} submitting={creatingProject} />
          ) : null}

          {!loading && !appIncompatible && view === "proyectos" ? (
            <div className="projects-layout">
              <ProjectList
                projects={projects}
                selectedProjectId={selectedProjectId}
                onSelect={(projectId) => {
                  setSelectedProjectId(projectId);
                  setSidebarOpen(false);
                }}
                onCreateProject={() => handleSelectView("nuevo")}
              />
              <ProjectWorkspace
                project={selectedProject}
                busyKey={busyKey}
                savingProject={savingProject}
                onSaveProject={handleSaveProject}
                onAddSetup={handleAddSetup}
                onAddOperation={handleAddOperation}
                onUpdateOperation={handleUpdateOperation}
                onDuplicateOperation={handleDuplicateOperation}
                onMoveOperation={handleMoveOperation}
                onDeleteOperation={handleDeleteOperation}
                onRemoveFile={handleRemoveFile}
                onAnalyze={handleAnalyze}
                onUploadFile={handleUploadFile}
              />
            </div>
          ) : null}

          {!loading && !appIncompatible && view === "sistema" ? (
            <SystemPage
              health={health}
              systemInfo={systemInfo}
              machineSession={machineSession}
              machineRuntime={machineRuntime}
              refreshing={refreshingSystem}
              onRefresh={refreshSystem}
              onMachineAction={handleMachineAction}
            />
          ) : null}
        </div>
      </main>
    </div>
  );
}
