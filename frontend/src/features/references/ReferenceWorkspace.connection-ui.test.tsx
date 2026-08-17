import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { MachineRuntime, Operation } from "../../types";
import type { MachineContextValue } from "../system/MachineContext";
import { ReferenceWorkspace } from "./ReferenceWorkspace";

function runtime(connected: boolean): MachineRuntime {
  return {
    mode: "PHYSICAL",
    mode_label: "FÍSICO",
    state: connected ? "DIAGNOSTIC" : "DISCONNECTED",
    health: connected ? "HEALTHY" : "DISCONNECTED",
    safety: {
      movement_authorized: false,
      serial_recent: connected,
      telemetry_recent: connected,
    },
    moonraker: connected
      ? { http_connected: true, websocket_connected: true, http_state: "CONNECTED", websocket_state: "CONNECTED" }
      : { http_connected: false, websocket_connected: false, http_state: "DISCONNECTED", websocket_state: "DISCONNECTED" },
    klipper: connected
      ? { ready: true, state: "ready", homed_axes: "", position: { x: 0, y: 0, z: 0 } }
      : { ready: false, state: "disconnected", homed_axes: "", position: null },
    arduino: connected
      ? { open: true, connection_state: "CONNECTED" }
      : { open: false, connection_state: "DISCONNECTED" },
    controller: {},
    initialization_steps: [],
    events: [],
  } as unknown as MachineRuntime;
}

function machine(connected: boolean, refreshRuntime = vi.fn().mockResolvedValue(undefined)): MachineContextValue {
  const currentRuntime = runtime(connected);
  return {
    runtime: currentRuntime,
    refreshing: false,
    isPhysical: true,
    modeLabel: "FÍSICO",
    runtimeState: connected ? "DIAGNOSTIC" : "DISCONNECTED",
    connected,
    homedAxes: "",
    klipperReady: connected,
    serialRecent: connected,
    telemetryRecent: connected,
    movementAuthorized: false,
    lastError: null,
    runMachineAction: vi.fn().mockResolvedValue(undefined),
    refreshRuntime,
  };
}

function renderReference(options?: {
  connected?: boolean;
  onConnectRuntime?: ReturnType<typeof vi.fn>;
  refreshRuntime?: ReturnType<typeof vi.fn>;
}) {
  const connected = options?.connected ?? false;
  const onConnectRuntime = options?.onConnectRuntime ?? vi.fn();
  const refreshRuntime = options?.refreshRuntime ?? vi.fn().mockResolvedValue(undefined);
  const currentRuntime = runtime(connected);

  render(
    <ReferenceWorkspace
      machine={machine(connected, refreshRuntime)}
      runtime={currentRuntime}
      referenceSession={null}
      referenceBusy={false}
      selectedOperation={{ id: "op-1", herramienta: "V-bit" } as unknown as Operation}
      heightMap={null}
      machineSettingsInput={{
        reference_prep_z_mm: "115",
        reference_prep_z_feed_mm_min: "180",
        move_total_timeout_s: "180",
        no_progress_timeout_s: "60",
        position_tolerance_mm: "0.25",
        velocity_tolerance_mm_s: "0.12",
        reference_probe_step_mm: "0.05",
        reference_probe_feed_mm_min: "80",
        reference_probe_retract_mm: "1",
        reference_probe_retract_feed_mm_min: "60",
      }}
      machineSettingsMessage=""
      referenceMoveResult={null}
      workOrigin={{ x_mm: "", y_mm: "" }}
      zReference={{ x_mm: "", y_mm: "", z_mm: "" }}
      useWorkOriginXYForZ={false}
      workOriginErrors={{}}
      zReferenceErrors={{}}
      workOriginRefs={{ current: { x_mm: null, y_mm: null } }}
      zReferenceRefs={{ current: { x_mm: null, y_mm: null, z_mm: null } }}
      formatCapturedPosition={() => "sin captura"}
      renderReferenceStep={() => null}
      onConnectRuntime={onConnectRuntime}
      onDiagnosticMode={vi.fn()}
      onReconnectArduino={vi.fn()}
      onSaveMachineSettings={vi.fn()}
      onInitialize={vi.fn()}
      onEnableManual={vi.fn()}
      onCapturePhysicalWorkOrigin={vi.fn()}
      onCancelOperation={vi.fn()}
      onToolChangePosition={vi.fn()}
      onProbeRequest={vi.fn()}
      onRemeasurePhysicalReference={vi.fn()}
      onGoToReferencePoint={vi.fn()}
      onConfirmMachineReference={vi.fn()}
      onSubmitWorkOrigin={vi.fn()}
      onSubmitZReference={vi.fn()}
      onValidateHeightMap={vi.fn()}
      onMachineSettingChange={vi.fn()}
      onToggleUseWorkOriginXYForZ={vi.fn()}
      onWorkOriginChange={vi.fn()}
      onZReferenceChange={vi.fn()}
    />
  );

  return { onConnectRuntime, refreshRuntime };
}

describe("ReferenceWorkspace connection and workflow UI", () => {
  beforeEach(() => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("muestra el estado general de conexión en rojo o verde", () => {
    renderReference({ connected: false });
    expect(screen.getByText("SIN CONEXIÓN")).toHaveClass("status-badge--danger");
  });

  it("muestra Reconectar runtime si Conectar no queda listo después de 10 segundos", () => {
    vi.useFakeTimers();
    const { onConnectRuntime } = renderReference({ connected: false });

    fireEvent.click(screen.getByRole("button", { name: "Conectar runtime" }));
    expect(onConnectRuntime).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: "Reconectar runtime" })).not.toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(10_000);
    });

    expect(screen.getByRole("button", { name: "Reconectar runtime" })).toBeEnabled();
    expect(screen.getByText(/La conexión no quedó lista después de 10 s/)).toBeInTheDocument();
  });

  it("usa la reconexión completa existente y refresca el runtime", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => runtime(true),
    } as Response);
    vi.stubGlobal("fetch", fetchMock);
    const refreshRuntime = vi.fn().mockResolvedValue(undefined);
    renderReference({ connected: false, refreshRuntime });

    fireEvent.click(screen.getByRole("button", { name: "Conectar runtime" }));
    act(() => {
      vi.advanceTimersByTime(10_000);
    });

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Reconectar runtime" }));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/machine/reconnect-runtime",
      expect.objectContaining({ method: "POST" })
    );
    expect(refreshRuntime).toHaveBeenCalledTimes(1);
  });

  it("mantiene diagnóstico Arduino y configuración avanzada de Home fuera del flujo principal", () => {
    renderReference({ connected: true });

    expect(screen.getByText("Diagnóstico avanzado de conexión")).toBeInTheDocument();
    expect(screen.getByText(/Configuración avanzada de movimiento/i)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "4. Medir referencia Z" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Comprobación de posición de cambio de herramienta" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Probar posición de cambio" })).toBeInTheDocument();
  });

  it("muestra verde cuando todos los enlaces del runtime están listos", () => {
    renderReference({ connected: true });
    expect(screen.getByText("CONECTADO")).toHaveClass("status-badge--success");
  });
});
