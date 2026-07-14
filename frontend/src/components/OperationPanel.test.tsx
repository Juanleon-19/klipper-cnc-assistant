import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { OperationPanel } from "./OperationPanel";

const baseProject = {
  id: "proj_1",
  nombre: "PCB demo",
  material: { ancho_mm: 50, alto_mm: 40, espesor_mm: 1.6 },
  doble_cara: false,
  eje_volteo: null,
  agujeros_alineacion: [],
  operaciones: [],
  creado_en: new Date().toISOString(),
  actualizado_en: new Date().toISOString(),
  version_esquema: "1.1",
  estado_general: "sin configurar",
};

describe("OperationPanel", () => {
  it("deshabilita la cara inferior en proyectos de una cara", () => {
    render(
      <OperationPanel
        project={baseProject}
        busyKey={null}
        onAddOperation={vi.fn().mockResolvedValue(undefined)}
        onDeleteOperation={vi.fn().mockResolvedValue(undefined)}
        onRemoveFile={vi.fn().mockResolvedValue(undefined)}
        onAnalyze={vi.fn().mockResolvedValue(undefined)}
        onUploadFile={vi.fn().mockResolvedValue(undefined)}
      />
    );

    expect(screen.getByText(/Disponible solo para PCB doble cara/i)).toBeInTheDocument();
  });

  it("permite seleccionar una operacion disponible", async () => {
    const user = userEvent.setup();
    const onAddOperation = vi.fn().mockResolvedValue(undefined);
    render(
      <OperationPanel
        project={{ ...baseProject, doble_cara: true }}
        busyKey={null}
        onAddOperation={onAddOperation}
        onDeleteOperation={vi.fn().mockResolvedValue(undefined)}
        onRemoveFile={vi.fn().mockResolvedValue(undefined)}
        onAnalyze={vi.fn().mockResolvedValue(undefined)}
        onUploadFile={vi.fn().mockResolvedValue(undefined)}
      />
    );

    const superiorCard = screen.getByText(/Fresado cara superior/i).closest("article");
    expect(superiorCard).not.toBeNull();
    const actionButton = within(superiorCard as HTMLElement).getByRole("button", {
      name: /Seleccionar operacion/i,
    });
    expect(actionButton).toBeEnabled();

    await user.click(actionButton);

    await waitFor(() => {
      expect(onAddOperation).toHaveBeenCalledWith("fresado-superior");
    });
  });
});
