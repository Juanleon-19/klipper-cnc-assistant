import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import type { Project } from "./types";

const mockFetch = vi.fn();

type FetchResult = {
  ok: boolean;
  status: number;
  json: () => Promise<unknown>;
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((nextResolve, nextReject) => {
    resolve = nextResolve;
    reject = nextReject;
  });
  return { promise, resolve, reject };
}

function jsonResponse(payload: unknown, ok = true, status = ok ? 200 : 500): FetchResult {
  return {
    ok,
    status,
    json: async () => payload,
  };
}

const operationProfileProject: Project = {
  id: "proj_1",
  nombre: "Proyecto de perfiles",
  material: { ancho_mm: 80, alto_mm: 60, espesor_mm: 1.6 },
  doble_cara: false,
  eje_volteo: null,
  agujeros_alineacion: [],
  montajes: [{ id: "setup-main", nombre: "Montaje principal", orden: 0 }],
  operaciones: [{
    id: "op_1",
    nombre: "Taladrado 0.8 mm",
    tipo: "taladrado",
    cara: "superior",
    orden: 0,
    setup_id: "setup-main",
    archivo_gcode: null,
    nombre_archivo_original: null,
    tamano_archivo_bytes: null,
    sha256: null,
    tool_id: "drill-08",
    herramienta: "Broca 0.8 mm",
    tool_reference_profile: "standard",
    compensation_mode: "legacy",
    max_z_error_mm: 0.05,
    estado: "esperando_archivo",
    analisis: null,
  }],
  creado_en: "2026-08-17T12:00:00Z",
  actualizado_en: "2026-08-17T12:00:00Z",
  current_setup_id: "setup-main",
  version_esquema: "1.7",
  estado_general: "incompleto",
};

function seedOperationProfileApi(initialProject: Project, options: { persistPatch?: boolean } = {}) {
  let persistedProject = initialProject;
  let patchPayload: Record<string, unknown> | null = null;
  let projectGets = 0;
  const patchResponse = deferred<FetchResult>();

  mockFetch.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";

    if (url === "/api/projects/proj_1/operations/op_1" && method === "PATCH") {
      patchPayload = JSON.parse(String(init?.body)) as Record<string, unknown>;
      if (options.persistPatch !== false) {
        persistedProject = {
          ...persistedProject,
          operaciones: persistedProject.operaciones.map((operation) => operation.id === "op_1"
            ? { ...operation, ...patchPayload }
            : operation),
        };
      }
      return patchResponse.promise;
    }
    if (url === "/api/projects/proj_1" && method === "GET") {
      projectGets += 1;
      return Promise.resolve(jsonResponse(persistedProject));
    }
    if (url === "/api/projects" && method === "GET") {
      return Promise.resolve(jsonResponse([persistedProject]));
    }
    if (url === "/api/health") {
      return Promise.resolve(jsonResponse({ estado: "ok", version: "0.1.0", modo_maquina: "simulado", almacenamiento: "disponible" }));
    }
    if (url === "/api/system/info") {
      return Promise.resolve(jsonResponse({
        estado: "ok",
        version_aplicacion: "0.1.0",
        version_python: "3.12.0",
        almacenamiento_disponible: true,
        estado_api: "operativa",
        modo_maquina: "simulado",
        hora_servidor: "2026-08-17T12:00:00Z",
        backend_version: "0.1.0",
        frontend_build: "0.1.0",
        git_commit: null,
        schema_version: "1.7",
      }));
    }
    if (url === "/api/machine/session") {
      return Promise.resolve(jsonResponse({
        estado: "simulada_lista_para_preparacion",
        home_realizado: false,
        referencia_maquina_confirmada_en: null,
        z_en_altura_segura: true,
        herramienta_en_centro_cama: true,
        material_montado: false,
        origen_xy_definido: false,
        cero_z_capturado: false,
        operaciones_permitidas: ["crear proyecto"],
        z_puede_bajar_durante: [],
      }));
    }
    if (url === "/api/machine/status") {
      return Promise.resolve(jsonResponse({
        mode: "SIMULATED",
        mode_label: "SIMULADO",
        state: "READY",
        health: "HEALTHY",
        moonraker: { http_connected: false, websocket_connected: false },
        klipper: { ready: false, position: null, homed_axes: null, limits: null },
        arduino: { open: false, valid_packets: 0, checksum_errors: 0 },
        controller: { direction: "CENTER", probe_requested: false },
        safety: { movement_authorized: false, blocked_reason: "Modo simulado" },
        last_error: null,
      }));
    }
    return Promise.resolve(jsonResponse({ detalle: "No existe recurso de prueba." }, false, 404));
  });

  return {
    patchResponse,
    get patchPayload() {
      return patchPayload;
    },
    get projectGets() {
      return projectGets;
    },
    get persistedProject() {
      return persistedProject;
    },
  };
}

function seedInitialFetch(projects: unknown[] = [], schemaVersion = "1.7") {
  mockFetch
    .mockResolvedValueOnce({
      ok: true,
      json: async () => projects,
    })
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({ estado: "ok", version: "0.1.0", modo_maquina: "simulado", almacenamiento: "disponible" }),
    })
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        estado: "ok",
        version_aplicacion: "0.1.0",
        version_python: "3.12.0",
        almacenamiento_disponible: true,
        estado_api: "operativa",
        modo_maquina: "simulado",
        hora_servidor: new Date().toISOString(),
        backend_version: "0.1.0",
        frontend_build: "0.1.0",
        git_commit: null,
        schema_version: schemaVersion,
      }),
    })
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        estado: "simulada_lista_para_preparacion",
        home_realizado: false,
        referencia_maquina_confirmada_en: null,
        z_en_altura_segura: true,
        herramienta_en_centro_cama: true,
        material_montado: false,
        origen_xy_definido: false,
        cero_z_capturado: false,
        operaciones_permitidas: ["crear proyecto"],
        z_puede_bajar_durante: [],
      }),
    })
    .mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        mode: "SIMULATED",
        mode_label: "SIMULADO",
        state: "READY",
        health: "HEALTHY",
        started_at: new Date().toISOString(),
        application: { api_active: true, mode: "simulated", uptime_s: 1 },
        moonraker: { http_connected: false, websocket_connected: false },
        klipper: { ready: false, position: null, homed_axes: null, limits: null },
        arduino: { open: false, valid_packets: 0, checksum_errors: 0 },
        controller: { direction: "CENTER", probe_requested: false },
        safety: { movement_authorized: false, blocked_reason: "Modo simulado" },
        last_command: null,
        last_movement: null,
        last_error: null,
        last_probe_result: null,
        initialization_steps: [],
        events: [],
      }),
    });
}

describe("App", () => {
  beforeEach(() => {
    mockFetch.mockReset();
    vi.stubGlobal("fetch", mockFetch);
    document.body.style.overflow = "";
    window.history.replaceState(null, "", "/");
  });

  it("muestra el modo simulado y el dashboard inicial", async () => {
    window.innerWidth = 1440;
    seedInitialFetch();

    render(<App />);

    expect(screen.getByText(/MÁQUINA EN MODO SIMULADO/i)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/Panel de trabajo/i)).toBeInTheDocument());
    expect(screen.getAllByText(/Modo simulado/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/Sin proyectos/i)).toBeInTheDocument();
  });

  it("permite colapsar la sidebar en escritorio sin dejar el drawer abierto", async () => {
    window.innerWidth = 1440;
    seedInitialFetch();

    const { container } = render(<App />);
    await waitFor(() => expect(screen.getByText(/Panel de trabajo/i)).toBeInTheDocument());

    fireEvent.click(screen.getAllByRole("button", { name: /Cerrar menú/i })[0]);
    expect(container.querySelector(".app-shell--collapsed")).not.toBeNull();
    expect(container.querySelector(".app-shell--sidebar-open")).toBeNull();
  });

  it("abre y cierra el drawer móvil y bloquea el scroll de fondo", async () => {
    window.innerWidth = 390;
    seedInitialFetch();

    const { container } = render(<App />);
    await waitFor(() => expect(screen.getByText(/Panel de trabajo/i)).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /Abrir menú/i }));
    expect(container.querySelector(".app-shell--sidebar-open")).not.toBeNull();
    expect(document.body.style.overflow).toBe("hidden");

    fireEvent.click(screen.getAllByRole("button", { name: /Cerrar menú/i })[0]);
    expect(container.querySelector(".app-shell--sidebar-open")).toBeNull();
  });

  it("bloquea la interfaz cuando frontend y backend son incompatibles", async () => {
    window.innerWidth = 1440;
    seedInitialFetch([], "1.3");

    render(<App />);

    expect(await screen.findByText(/La aplicación necesita actualizarse/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Recargar aplicación/i })).toBeInTheDocument();
    expect(screen.queryByText(/Panel de trabajo/i)).toBeNull();
  });

  it.each([
    [1920, 1080, "desktop"],
    [1366, 768, "desktop"],
    [1024, 768, "drawer"],
    [768, 1024, "drawer"],
    [390, 844, "drawer"],
  ])("mantiene el AppShell responsive en %i × %i", async (width, height, mode) => {
    window.innerWidth = width;
    window.innerHeight = height;
    seedInitialFetch();

    const { container } = render(<App />);
    await waitFor(() => expect(screen.getByText(/Panel de trabajo/i)).toBeInTheDocument());

    expect(container.querySelector(mode === "desktop" ? ".app-shell--desktop" : ".app-shell--drawer")).not.toBeNull();
    if (mode === "drawer") {
      expect(screen.getByRole("button", { name: /Abrir menú/i })).toHaveAttribute("aria-expanded", "false");
      expect(container.querySelector(".app-shell--sidebar-open")).toBeNull();
    } else {
      expect(screen.getAllByRole("button", { name: /Cerrar menú/i })[0]).toHaveAttribute("aria-expanded", "true");
    }
  });

  it("mantiene el perfil largo durante el PATCH, el sync y una recarga", async () => {
    window.innerWidth = 1440;
    window.history.replaceState(null, "", "/?view=proyectos&project=proj_1");
    const scenario = seedOperationProfileApi(operationProfileProject);

    const firstRender = render(<App />);
    const selector = await screen.findByLabelText("Perfil de referencia de Taladrado 0.8 mm");
    expect(selector).toHaveValue("standard");

    fireEvent.change(selector, { target: { value: "long_tool" } });

    expect(scenario.patchPayload).toEqual(expect.objectContaining({ tool_reference_profile: "long_tool" }));
    expect(selector).toHaveValue("long_tool");

    await act(async () => {
      scenario.patchResponse.resolve(jsonResponse(scenario.persistedProject.operaciones[0]));
      await scenario.patchResponse.promise;
    });

    await waitFor(() => expect(scenario.projectGets).toBe(1));
    expect(selector).toHaveValue("long_tool");
    const operationDetail = firstRender.container.querySelector(".operation-detail-panel");
    expect(operationDetail).not.toBeNull();
    expect(within(operationDetail as HTMLElement).getByText("Herramienta larga")).toBeInTheDocument();

    firstRender.unmount();
    render(<App />);

    expect(await screen.findByLabelText("Perfil de referencia de Taladrado 0.8 mm")).toHaveValue("long_tool");
  });

  it("revierte el perfil optimista y muestra el error cuando falla el PATCH", async () => {
    window.innerWidth = 1440;
    window.history.replaceState(null, "", "/?view=proyectos&project=proj_1");
    const scenario = seedOperationProfileApi(operationProfileProject, { persistPatch: false });

    render(<App />);
    const selector = await screen.findByLabelText("Perfil de referencia de Taladrado 0.8 mm");

    fireEvent.change(selector, { target: { value: "long_tool" } });

    expect(selector).toHaveValue("long_tool");
    await act(async () => {
      scenario.patchResponse.resolve(jsonResponse({ detalle: "El perfil no pudo guardarse." }, false, 500));
      await scenario.patchResponse.promise;
    });

    expect(await screen.findByText("El perfil no pudo guardarse.")).toBeInTheDocument();
    expect(selector).toHaveValue("standard");
    expect(scenario.projectGets).toBe(0);
  });

  it("persiste el cambio de herramienta larga a estándar", async () => {
    window.innerWidth = 1440;
    window.history.replaceState(null, "", "/?view=proyectos&project=proj_1");
    const longToolProject: Project = {
      ...operationProfileProject,
      operaciones: operationProfileProject.operaciones.map((operation) => ({
        ...operation,
        tool_reference_profile: "long_tool",
      })),
    };
    const scenario = seedOperationProfileApi(longToolProject);

    const { container } = render(<App />);
    const selector = await screen.findByLabelText("Perfil de referencia de Taladrado 0.8 mm");

    fireEvent.change(selector, { target: { value: "standard" } });

    expect(selector).toHaveValue("standard");
    expect(scenario.patchPayload).toEqual(expect.objectContaining({ tool_reference_profile: "standard" }));
    await act(async () => {
      scenario.patchResponse.resolve(jsonResponse(scenario.persistedProject.operaciones[0]));
      await scenario.patchResponse.promise;
    });

    await waitFor(() => expect(scenario.projectGets).toBe(1));
    expect(selector).toHaveValue("standard");
    const operationDetail = container.querySelector(".operation-detail-panel");
    expect(operationDetail).not.toBeNull();
    expect(within(operationDetail as HTMLElement).getByText("Estándar")).toBeInTheDocument();
  });
});
