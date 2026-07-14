import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ToolpathPreview } from "./ToolpathPreview";

describe("ToolpathPreview", () => {
  it("renderiza advertencias y segmentos basicos", () => {
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
          segmentos_lineales: [
            { tipo: "G0", inicio_x_mm: 0, inicio_y_mm: 0, fin_x_mm: 2, fin_y_mm: 2 },
            { tipo: "G1", inicio_x_mm: 2, inicio_y_mm: 2, fin_x_mm: 8, fin_y_mm: 4 },
          ],
        }}
      />
    );

    expect(screen.getByLabelText(/Vista previa 2D de trayectorias/i)).toBeInTheDocument();
    expect(screen.getByText(/la trayectoria excede el material bruto definido/i)).toBeInTheDocument();
    expect(screen.getByText(/existen G2 o G3 no representados/i)).toBeInTheDocument();
  });
});
