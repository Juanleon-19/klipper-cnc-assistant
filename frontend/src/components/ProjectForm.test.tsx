import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ProjectPayload } from "../types";
import { ProjectForm } from "./ProjectForm";

const initialValue: ProjectPayload = {
  nombre: "PCB original",
  material: { ancho_mm: 4, alto_mm: 58, espesor_mm: 1.66 },
  doble_cara: false,
  eje_volteo: null,
  agujeros_alineacion: [],
};

describe("ProjectForm", () => {
  it("muestra un error cuando las dimensiones no son positivas", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<ProjectForm mode="create" onSubmit={onSubmit} submitting={false} />);
    fireEvent.change(screen.getByLabelText(/Nombre del proyecto/i), { target: { value: "PCB demo" } });
    fireEvent.change(screen.getByLabelText(/Ancho del material/i), { target: { value: "0" } });
    fireEvent.submit(screen.getByRole("button", { name: /Crear proyecto/i }).closest("form")!);
    expect(await screen.findByText(/Las dimensiones del material deben ser positivas/i)).toBeInTheDocument();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("conserva cambios locales ante valores iniciales equivalentes de otra identidad", () => {
    const { rerender } = render(<ProjectForm projectId="proj_1" initialValue={initialValue} mode="edit" onSubmit={vi.fn()} submitting={false} />);
    const name = screen.getByLabelText(/Nombre del proyecto/i) as HTMLInputElement;
    fireEvent.change(name, { target: { value: "PCB sin guardar" } });
    rerender(<ProjectForm projectId="proj_1" initialValue={{ ...initialValue, material: { ...initialValue.material } }} mode="edit" onSubmit={vi.fn()} submitting={false} />);
    expect(name.value).toBe("PCB sin guardar");
    expect(document.querySelector("form")!).toHaveAttribute("data-dirty", "true");
  });

  it("solo reinicializa al cambiar realmente de proyecto", () => {
    const { rerender } = render(<ProjectForm projectId="proj_1" initialValue={initialValue} mode="edit" onSubmit={vi.fn()} submitting={false} />);
    fireEvent.change(screen.getByLabelText(/Nombre del proyecto/i), { target: { value: "Borrador" } });
    rerender(<ProjectForm projectId="proj_2" initialValue={{ ...initialValue, nombre: "PCB nueva" }} mode="edit" onSubmit={vi.fn()} submitting={false} />);
    expect((screen.getByLabelText(/Nombre del proyecto/i) as HTMLInputElement).value).toBe("PCB nueva");
  });

  it("restablece el estado dirty tras guardar sin alterar los datos enviados", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<ProjectForm projectId="proj_1" initialValue={initialValue} mode="edit" onSubmit={onSubmit} submitting={false} />);
    fireEvent.change(screen.getByLabelText(/Nombre del proyecto/i), { target: { value: "PCB guardada" } });
    fireEvent.submit(screen.getByRole("button", { name: /Guardar cambios/i }).closest("form")!);
    await vi.waitFor(() => expect(onSubmit).toHaveBeenCalledWith(expect.objectContaining({ nombre: "PCB guardada" })));
    await vi.waitFor(() => expect(document.querySelector("form")!).toHaveAttribute("data-dirty", "false"));
  });
});
