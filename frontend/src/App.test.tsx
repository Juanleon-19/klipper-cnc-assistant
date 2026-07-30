import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";

const mockFetch = vi.fn();

function seedInitialFetch(projects: unknown[] = [], schemaVersion = "1.6") {
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
});
