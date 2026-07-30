import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ToolpathViewer } from "./ToolpathViewer";

vi.mock("react-konva", () => ({
  Stage: ({ children }: { children: React.ReactNode }) => <div data-testid="stage">{children}</div>,
  Layer: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  Rect: () => <div />,
  Circle: () => <div />,
  Text: ({ text }: { text: string }) => <span>{text}</span>,
  Line: ({ onClick, onMouseEnter }: { onClick?: () => void; onMouseEnter?: () => void }) =>
    onClick || onMouseEnter ? (
      <button data-testid="viewer-line" onClick={onClick} onMouseEnter={onMouseEnter} type="button">
        line
      </button>
    ) : (
      <div />
    ),
}));

const analysis = {
  analysis_version: "gcode-analysis-v2",
  current_analysis_version: "gcode-analysis-v2",
  analisis_desactualizado: false,
  limites: {
    min_x_mm: 0,
    max_x_mm: 12,
    min_y_mm: 0,
    max_y_mm: 8,
    min_z_mm: -0.4,
    max_z_mm: 0,
    ancho_mm: 12,
    alto_mm: 8,
  },
  avances_mm_min: [120],
  profundidad_min_mm: -0.4,
  profundidad_max_mm: 0,
  cantidad_movimientos: 2,
  comandos_desconocidos: [],
  comandos_no_compatibles: [],
  acciones_husillo: [],
  cambios_herramienta: [],
  comandos_manuales: [],
  unidades_detectadas: ["mm"],
  modos_posicionamiento: ["absolute"],
  incidencias: [],
  analisis_incompleto: false,
  soporte_geometrico_incompleto: false,
  cabe_en_material: true,
  mensaje_material: "Cabe",
  tiene_errores_criticos: false,
  segmentos_lineales: [],
  segmentos_vista_previa: [
    {
      tipo: "G0",
      tipo_movimiento: "desplazamiento_rapido",
      numero_linea: 2,
      inicio_x_mm: 0,
      inicio_y_mm: 0,
      fin_x_mm: 2,
      fin_y_mm: 2,
      z_mm: 0,
      avance_mm_min: null,
      distancia_mm: 2.8,
      advertencias: [],
      puntos: [
        { x_mm: 0, y_mm: 0 },
        { x_mm: 2, y_mm: 2 },
      ],
      desde: { x_mm: 0, y_mm: 0 },
      hasta: { x_mm: 2, y_mm: 2 },
    },
    {
      tipo: "G1",
      tipo_movimiento: "movimiento_lineal",
      numero_linea: 3,
      inicio_x_mm: 2,
      inicio_y_mm: 2,
      fin_x_mm: 8,
      fin_y_mm: 4,
      z_mm: -0.4,
      avance_mm_min: 120,
      distancia_mm: 6.3,
      advertencias: [],
      puntos: [
        { x_mm: 2, y_mm: 2 },
        { x_mm: 8, y_mm: 4 },
      ],
      desde: { x_mm: 2, y_mm: 2 },
      hasta: { x_mm: 8, y_mm: 4 },
    },
  ],
  desbordes_material: [],
  tolerancia_arco_mm: 0.05,
};

describe("ToolpathViewer", () => {
  it("traduce capas y permite inspeccionar un segmento", () => {
    render(
      <ToolpathViewer
        material={{ ancho_mm: 20, alto_mm: 20, espesor_mm: 1.6 }}
        analysis={analysis}
        operationName="Fresado cara superior"
      />
    );

    expect(screen.getAllByText(/Material bruto/i).length).toBeGreaterThan(0);
    fireEvent.click(screen.getAllByTestId("viewer-line")[1]);
    expect(screen.getByText(/Movimiento lineal/i)).toBeInTheDocument();
    expect(screen.getByText(/Fresado cara superior/i)).toBeInTheDocument();
  });

  it("incluye controles de recorrido y zoom", () => {
    render(
      <ToolpathViewer
        material={{ ancho_mm: 20, alto_mm: 20, espesor_mm: 1.6 }}
        analysis={analysis}
        operationName="Fresado cara superior"
      />
    );

    expect(screen.getByRole("button", { name: /Acercar/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Alejar/i })).toBeInTheDocument();
    expect(screen.getByLabelText(/Recorrido visual/i)).toBeInTheDocument();
  });
});
