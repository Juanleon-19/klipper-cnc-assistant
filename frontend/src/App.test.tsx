import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";

const mockFetch = vi.fn();

function seedInitialFetch(projects: unknown[] = []) {
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
    });
}

describe("App", () => {
  beforeEach(() => {
    mockFetch.mockReset();
    vi.stubGlobal("fetch", mockFetch);
  });

  it("muestra el modo simulado y el dashboard inicial", async () => {
    seedInitialFetch();

    render(<App />);

    expect(screen.getByText(/MÁQUINA EN MODO SIMULADO/i)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/Panel de trabajo/i)).toBeInTheDocument());
    expect(screen.getAllByText(/Modo simulado/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/Sin proyectos/i)).toBeInTheDocument();
  });

  it("abre la navegación compacta desde el topbar", async () => {
    seedInitialFetch();

    const { container } = render(<App />);
    await waitFor(() => expect(screen.getByText(/Panel de trabajo/i)).toBeInTheDocument());

    fireEvent.click(screen.getByRole("button", { name: /Abrir navegación/i }));
    expect(container.querySelector(".app-shell--sidebar-open")).not.toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /Cerrar navegación/i }));
    expect(container.querySelector(".app-shell--sidebar-open")).toBeNull();
  });
});
