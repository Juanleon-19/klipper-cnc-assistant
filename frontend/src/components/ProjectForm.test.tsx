import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ProjectForm } from "./ProjectForm";

describe("ProjectForm", () => {
  it("muestra un error cuando las dimensiones no son positivas", async () => {
    const onSubmit = vi.fn().mockResolvedValue(undefined);
    render(<ProjectForm mode="create" onSubmit={onSubmit} submitting={false} />);

    fireEvent.change(screen.getByLabelText(/Nombre del proyecto/i), {
      target: { value: "PCB demo" },
    });
    fireEvent.change(screen.getByLabelText(/Ancho del material/i), {
      target: { value: "0" },
    });
    fireEvent.submit(screen.getByRole("button", { name: /Crear proyecto/i }).closest("form")!);

    expect(await screen.findByText(/Las dimensiones del material deben ser positivas/i)).toBeInTheDocument();
    expect(onSubmit).not.toHaveBeenCalled();
  });
});
