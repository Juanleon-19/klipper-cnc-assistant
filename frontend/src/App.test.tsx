import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";

const mockFetch = vi.fn();

describe("App", () => {
  beforeEach(() => {
    mockFetch.mockReset();
    vi.stubGlobal("fetch", mockFetch);
  });

  it("muestra el modo simulado y el estado vacio de proyectos", async () => {
    mockFetch
      .mockResolvedValueOnce({ ok: true, json: async () => [] })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ estado: "ok", version: "0.1.0", modo_maquina: "simulado", almacenamiento: "disponible" }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ estado: "ok", version_aplicacion: "0.1.0", version_python: "3.12.0", almacenamiento_disponible: true, estado_api: "operativa", modo_maquina: "simulado", hora_servidor: new Date().toISOString() }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ estado: "simulada_lista_para_preparacion", home_realizado: true, z_en_altura_segura: true, herramienta_en_centro_cama: true, material_montado: false, origen_xy_definido: false, cero_z_capturado: false, operaciones_permitidas: ["crear proyecto"], z_puede_bajar_durante: [] }) });

    render(<App />);

    expect(screen.getByText(/MÁQUINA EN MODO SIMULADO/i)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/Sin proyectos todavia/i)).toBeInTheDocument());
  });
});
