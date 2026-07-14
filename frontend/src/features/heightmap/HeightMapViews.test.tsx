import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { HeightMap } from "../../types";
import { HeightMapHeatmap } from "./HeightMapHeatmap";
import { HeightMapPointTable } from "./HeightMapPointTable";
import { HeightMapSurface3D } from "./HeightMapSurface3D";

const newPlot = vi.fn().mockResolvedValue(undefined);
const purge = vi.fn();

vi.mock("plotly.js-dist-min", () => ({
  newPlot,
  purge,
}));

vi.mock("react-konva", () => ({
  Stage: ({ children }: { children: React.ReactNode }) => <div data-testid="stage">{children}</div>,
  Layer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  Rect: ({ children, onMouseEnter }: { children?: React.ReactNode; onMouseEnter?: () => void }) =>
    onMouseEnter ? (
      <button onMouseEnter={onMouseEnter} type="button">{children}</button>
    ) : (
      <div>{children}</div>
    ),
  Circle: () => <div />,
  Text: ({ text }: { text: string }) => <span>{text}</span>,
}));

const heightMap: HeightMap = {
  proyecto_id: "proj_1",
  operacion_id: "op_1",
  version: 1,
  version_algoritmo: "heightmap-v1",
  estado: "datos simulados",
  fuente_datos: "simulado",
  escenario: "inclinacion_y_deformacion",
  semilla: 7,
  etiqueta_simulada: true,
  grid: { filas: 2, columnas: 2, ancho_mm: 80, alto_mm: 60, paso_x_mm: 80, paso_y_mm: 60 },
  muestras: [
    { id: "hm_0_0", x_mm: 0, y_mm: 0, z_mm: 0.0, fila: 0, columna: 0, origen_datos: "simulado", estado_calidad: "valida", observacion: "DATOS SIMULADOS", incluida: true, residuo_plano_mm: 0 },
    { id: "hm_0_1", x_mm: 80, y_mm: 0, z_mm: 0.02, fila: 0, columna: 1, origen_datos: "simulado", estado_calidad: "atipica", observacion: "DATOS SIMULADOS", incluida: true, residuo_plano_mm: 0.01 },
    { id: "hm_1_0", x_mm: 0, y_mm: 60, z_mm: -0.01, fila: 1, columna: 0, origen_datos: "simulado", estado_calidad: "valida", observacion: "DATOS SIMULADOS", incluida: true, residuo_plano_mm: -0.01 },
    { id: "hm_1_1", x_mm: 80, y_mm: 60, z_mm: null, fila: 1, columna: 1, origen_datos: "simulado", estado_calidad: "faltante", observacion: "Punto faltante", incluida: true, residuo_plano_mm: null },
  ],
  estadisticas: {
    cantidad_puntos: 4,
    cantidad_puntos_incluidos: 3,
    cantidad_puntos_faltantes: 1,
    cantidad_puntos_atipicos: 1,
    altura_min_mm: -0.01,
    altura_max_mm: 0.02,
    rango_alturas_mm: 0.03,
    rms_residuos_mm: 0.01,
    residuo_maximo_mm: 0.01,
    ancho_cubierto_mm: 80,
    alto_cubierto_mm: 60,
  },
  plano: {
    a: 0.0002,
    b: -0.0001,
    c: 0,
    inclinacion_x_mm_por_mm: 0.0002,
    inclinacion_y_mm_por_mm: -0.0001,
    rms_residuos_mm: 0.01,
    residuo_maximo_mm: 0.01,
    residuo_minimo_mm: -0.01,
  },
  superficies: {
    bruto: {
      filas: 2,
      columnas: 2,
      modo: "bruto",
      puntos: [
        { fila: 0, columna: 0, x_mm: 0, y_mm: 0, z_mm: 0.0, estado: "ok", observacion: null },
        { fila: 0, columna: 1, x_mm: 80, y_mm: 0, z_mm: 0.02, estado: "ok", observacion: null },
        { fila: 1, columna: 0, x_mm: 0, y_mm: 60, z_mm: -0.01, estado: "ok", observacion: null },
        { fila: 1, columna: 1, x_mm: 80, y_mm: 60, z_mm: null, estado: "insuficiente", observacion: "faltante" },
      ],
    },
    plano: {
      filas: 2,
      columnas: 2,
      modo: "plano",
      puntos: [
        { fila: 0, columna: 0, x_mm: 0, y_mm: 0, z_mm: 0.0, estado: "ok", observacion: null },
        { fila: 0, columna: 1, x_mm: 80, y_mm: 0, z_mm: 0.015, estado: "ok", observacion: null },
        { fila: 1, columna: 0, x_mm: 0, y_mm: 60, z_mm: -0.005, estado: "ok", observacion: null },
        { fila: 1, columna: 1, x_mm: 80, y_mm: 60, z_mm: 0.01, estado: "ok", observacion: null },
      ],
    },
    residuo: {
      filas: 2,
      columnas: 2,
      modo: "residuo",
      puntos: [
        { fila: 0, columna: 0, x_mm: 0, y_mm: 0, z_mm: 0.0, estado: "ok", observacion: null },
        { fila: 0, columna: 1, x_mm: 80, y_mm: 0, z_mm: 0.005, estado: "ok", observacion: null },
        { fila: 1, columna: 0, x_mm: 0, y_mm: 60, z_mm: -0.005, estado: "ok", observacion: null },
        { fila: 1, columna: 1, x_mm: 80, y_mm: 60, z_mm: null, estado: "insuficiente", observacion: "faltante" },
      ],
    },
  },
  creado_en: new Date().toISOString(),
  actualizado_en: new Date().toISOString(),
};

describe("HeightMap views", () => {
  it("renderiza el heatmap 2D con modo simulado y leyenda", () => {
    render(
      <HeightMapHeatmap
        material={{ ancho_mm: 80, alto_mm: 60, espesor_mm: 1.6 }}
        heightMap={heightMap}
        mode="bruto"
      />
    );

    expect(screen.getByText(/Mapa de alturas 2D/i)).toBeInTheDocument();
    expect(screen.getByText(/DATOS SIMULADOS/i)).toBeInTheDocument();
    expect(screen.getByText(/P1-1/i)).toBeInTheDocument();
  });

  it("permite excluir y editar puntos desde la tabla", async () => {
    const toggleInclude = vi.fn().mockResolvedValue(undefined);
    const editSample = vi.fn().mockResolvedValue(undefined);

    render(
      <HeightMapPointTable
        heightMap={heightMap}
        busy={false}
        onToggleInclude={toggleInclude}
        onEditSample={editSample}
      />
    );

    fireEvent.click(screen.getAllByRole("button", { name: /Excluir/i })[0]);
    fireEvent.click(screen.getAllByRole("button", { name: /Editar/i })[0]);

    await waitFor(() => {
      expect(toggleInclude).toHaveBeenCalled();
      expect(editSample).toHaveBeenCalled();
    });
  });

  it("carga la superficie 3D de forma diferida y permite exagerar Z", async () => {
    render(<HeightMapSurface3D heightMap={heightMap} mode="plano" />);

    await waitFor(() => expect(newPlot).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: /Escala Z exagerada/i }));
    expect(await screen.findByText(/factor visual x8/i)).toBeInTheDocument();
  });
});

