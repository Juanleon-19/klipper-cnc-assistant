import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ToolpathPreview } from "./ToolpathPreview";

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

describe("ToolpathPreview", () => {
  it("renderiza el visor técnico y muestra advertencias", async () => {
    render(
      <ToolpathPreview
        material={{ ancho_mm: 20, alto_mm: 20, espesor_mm: 1.6 }}
        analysis={{
          limites: {
            min_x_mm: 0,
            max_x_mm: 24,
            min_y_mm: 0,
            max_y_mm: 10,
            min_z_mm: -0.2,
            max_z_mm: 0,
            ancho_mm: 24,
            alto_mm: 10,
          },
          avances_mm_min: [120],
          profundidad_min_mm: -0.2,
          profundidad_max_mm: 0,
          cantidad_movimientos: 2,
          comandos_desconocidos: [],
          comandos_no_compatibles: ["G2"],
          acciones_husillo: [],
          cambios_herramienta: [],
          comandos_manuales: [],
          unidades_detectadas: ["mm"],
          modos_posicionamiento: ["absolute"],
          incidencias: [],
          analisis_incompleto: true,
          soporte_geometrico_incompleto: true,
          cabe_en_material: false,
          mensaje_material: "Fuera de material",
          tiene_errores_criticos: false,
          segmentos_lineales: [],
          segmentos_vista_previa: [
            {
              tipo: "G1",
              tipo_movimiento: "movimiento_lineal",
              numero_linea: 5,
              inicio_x_mm: 0,
              inicio_y_mm: 0,
              fin_x_mm: 24,
              fin_y_mm: 10,
              z_mm: -0.2,
              avance_mm_min: 120,
              distancia_mm: 25,
              advertencias: ["fuera_material_x_max"],
              puntos: [
                { x_mm: 0, y_mm: 0 },
                { x_mm: 24, y_mm: 10 },
              ],
              desde: { x_mm: 0, y_mm: 0 },
              hasta: { x_mm: 24, y_mm: 10 },
            },
          ],
          desbordes_material: [
            { eje: "X", direccion: "maximo", limite_mm: 20, valor_mm: 24, exceso_mm: 4 },
          ],
          tolerancia_arco_mm: 0.05,
        }}
      />
    );

    expect(screen.getByText(/Visor técnico 2D V2/i)).toBeInTheDocument();
    expect(screen.getByText(/Ver problema/i)).toBeInTheDocument();
    expect(screen.getByText(/4\.000 mm fuera del material/i)).toBeInTheDocument();
  });
});
