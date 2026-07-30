import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Project } from "../types";
import { ProjectList } from "./ProjectList";

function makeProject(overrides: Partial<Project> = {}): Project {
  return {
    id: "project-active",
    nombre: "Control spindle",
    material: { ancho_mm: 80, alto_mm: 60, espesor_mm: 1.6 },
    doble_cara: false,
    eje_volteo: null,
    agujeros_alineacion: [],
    montajes: [{ id: "setup-main", nombre: "Montaje principal", orden: 0, preparation_status: "sin_iniciar" }],
    operaciones: [{
      id: "op-1",
      nombre: "Aislamiento",
      tipo: "fresado",
      cara: "superior",
      orden: 0,
      setup_id: "setup-main",
      archivo_gcode: "originals/job.nc",
      nombre_archivo_original: "job.nc",
      tamano_archivo_bytes: 120,
      sha256: "abc",
      tool_id: "tool-1",
      herramienta: "V-bit 30",
      estado: "valida",
      analisis: null,
    }],
    creado_en: "2026-01-01T00:00:00Z",
    actualizado_en: "2026-01-02T00:00:00Z",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-02T00:00:00Z",
    last_opened_at: "2026-01-03T00:00:00Z",
    archived_at: null,
    trashed_at: null,
    status: "active",
    current_setup_id: "setup-main",
    version_esquema: "1.6",
    estado_general: "valido",
    ...overrides,
  };
}

describe("ProjectList", () => {
  it("muestra historial, búsqueda, filtros y acciones de papelera", () => {
    const active = makeProject();
    const trashed = makeProject({
      id: "project-trash",
      nombre: "Mapa descartado",
      status: "trashed",
      trashed_at: "2026-01-04T00:00:00Z",
      operaciones: [{ ...makeProject().operaciones[0], nombre: "Taladrado", herramienta: "Broca 0.8" }],
    });
    const onSelect = vi.fn();
    const onContinueProject = vi.fn();
    const onResetProjectProcess = vi.fn();
    const onTrashProject = vi.fn();
    const onRestoreProject = vi.fn();
    const onPermanentlyDeleteProject = vi.fn();

    render(
      <ProjectList
        projects={[active, trashed]}
        selectedProjectId={null}
        onSelect={onSelect}
        onContinueProject={onContinueProject}
        onResetProjectProcess={onResetProjectProcess}
        onTrashProject={onTrashProject}
        onRestoreProject={onRestoreProject}
        onPermanentlyDeleteProject={onPermanentlyDeleteProject}
      />
    );

    expect(screen.getByText(/Historial principal/i)).toBeInTheDocument();
    expect(screen.getByText(/Control spindle/i)).toBeInTheDocument();
    expect(screen.queryByText(/Mapa descartado/i)).toBeNull();

    fireEvent.change(screen.getByLabelText(/Buscar/i), { target: { value: "v-bit" } });
    expect(screen.getByText(/Control spindle/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Continuar/i }));
    expect(onContinueProject).toHaveBeenCalledWith("project-active");
    fireEvent.click(screen.getByText(/Más acciones/i));
    fireEvent.click(screen.getByRole("button", { name: /Reiniciar proceso/i }));
    expect(onResetProjectProcess).toHaveBeenCalledWith(active);
    fireEvent.click(screen.getByRole("button", { name: /Mover a Papelera/i }));
    expect(onTrashProject).toHaveBeenCalledWith(active);

    fireEvent.change(screen.getByLabelText(/Buscar/i), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: /^Papelera$/i }));
    const trashCard = screen.getByText(/Mapa descartado/i).closest("article");
    expect(trashCard).not.toBeNull();
    const scope = within(trashCard as HTMLElement);
    fireEvent.click(scope.getByRole("button", { name: /Restaurar/i }));
    fireEvent.click(scope.getByRole("button", { name: /Eliminar permanentemente/i }));

    expect(onRestoreProject).toHaveBeenCalledWith("project-trash");
    expect(onPermanentlyDeleteProject).toHaveBeenCalledWith(trashed);
  });
});
