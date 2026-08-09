import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import type { MachineRuntime } from "../../types";
import { SystemPage } from "./SystemPage";

function runtime(state = "DIAGNOSTIC"): MachineRuntime {
  return {
    mode: "PHYSICAL",
    mode_label: "FÍSICO",
    state,
    health: "HEALTHY",
    safety: { movement_authorized: false },
    application: {},
    moonraker: {},
    klipper: {},
    arduino: {},
    controller: {},
    initialization_steps: [],
    events: [],
  } as unknown as MachineRuntime;
}

function renderSystemPage(options?: {
  state?: string;
  onRefresh?: ReturnType<typeof vi.fn>;
  onRuntimeRefresh?: ReturnType<typeof vi.fn>;
  onMachineAction?: ReturnType<typeof vi.fn>;
}) {
  const onRefresh = options?.onRefresh ?? vi.fn().mockResolvedValue(undefined);
  const onRuntimeRefresh = options?.onRuntimeRefresh ?? vi.fn().mockResolvedValue(undefined);
  const onMachineAction = options?.onMachineAction ?? vi.fn().mockResolvedValue(undefined);
  render(
    <SystemPage
      health={null}
      systemInfo={null}
      machineSession={null}
      machineRuntime={runtime(options?.state)}
      refreshing={false}
      onRefresh={onRefresh}
      onRuntimeRefresh={onRuntimeRefresh}
      onMachineAction={onMachineAction}
    />
  );
  return { onRefresh, onRuntimeRefresh, onMachineAction };
}

describe("SystemPage runtime reconnect", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
    vi.spyOn(window, "confirm").mockReturnValue(true);
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("no crea un segundo polling de runtime al montar la vista", async () => {
    const { onRuntimeRefresh } = renderSystemPage();

    await new Promise((resolve) => window.setTimeout(resolve, 20));

    expect(onRuntimeRefresh).not.toHaveBeenCalled();
    expect(fetch).not.toHaveBeenCalled();
  });

  it("reconecta el runtime con una sola petición y refresca el sistema", async () => {
    const responseRuntime = runtime("DIAGNOSTIC");
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => responseRuntime,
    } as Response);
    const { onRefresh } = renderSystemPage();

    fireEvent.click(screen.getByRole("button", { name: "Reconectar runtime" }));

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(1);
    });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/machine/reconnect-runtime",
      expect.objectContaining({ method: "POST" })
    );
    await waitFor(() => {
      expect(onRefresh).toHaveBeenCalledTimes(1);
    });
  });

  it("mantiene bloqueada la reconexión completa durante MESH_PROBING", () => {
    renderSystemPage({ state: "MESH_PROBING" });

    expect(screen.getByRole("button", { name: "Reconectar runtime" })).toBeDisabled();
  });

  it("mantiene disponible la reconexión separada del Arduino", () => {
    const onMachineAction = vi.fn().mockResolvedValue(undefined);
    renderSystemPage({ onMachineAction });

    fireEvent.click(screen.getByRole("button", { name: "Reconectar Arduino" }));

    expect(onMachineAction).toHaveBeenCalledTimes(1);
    expect(onMachineAction).toHaveBeenCalledWith("reconnect-arduino");
  });

  it("muestra el error del backend y libera el botón", async () => {
    const fetchMock = vi.mocked(fetch);
    fetchMock.mockResolvedValue({
      ok: false,
      status: 409,
      json: async () => ({ detalle: "Hay una operación física activa." }),
    } as Response);
    renderSystemPage();

    fireEvent.click(screen.getByRole("button", { name: "Reconectar runtime" }));

    expect(await screen.findByText("Hay una operación física activa.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reconectar runtime" })).toBeEnabled();
  });
});
