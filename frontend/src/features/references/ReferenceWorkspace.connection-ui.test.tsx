import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { MachineRuntime, Operation, ReferenceSession } from "../../types";
import type { MachineContextValue } from "../system/MachineContext";
import { ReferenceWorkspace } from "./ReferenceWorkspace";

function runtime(connected: boolean, options?: {
  state?: string;
  referencePrepZ?: number;
  longToolReferencePrepZ?: number;
}): MachineRuntime {
  const state = options?.state ?? (connected ? "DIAGNOSTIC" : "DISCONNECTED");
  return {
    mode: "PHYSICAL",
    mode_label: "FÍSICO",
    state,
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
    preparation: {
      reference_prep_z_mm: options?.referencePrepZ ?? 115,
    },
    tool_change: {
      clearance_z_mm: 115,
      long_tool_clearance_z_mm: options?.longToolReferencePrepZ ?? 130,
      work_z_mm: 115,
    },
    initialization_steps: [],
    events: [],
  } as unknown as MachineRuntime;
}

function machine(currentRuntime: MachineRuntime, refreshRuntime = vi.fn().mockResolvedValue(currentRuntime)): MachineContextValue {
  const connected = currentRuntime.state !== "DISCONNECTED";
  return {
    runtime: currentRuntime,
    refreshing: false,
    isPhysical: true,
    modeLabel: "FÍSICO",
    runtimeState: currentRuntime.state,
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
  toolReferenceProfile?: "standard" | "long_tool";
  runtimeState?: string;
  referencePrepZ?: number;
  longToolReferencePrepZ?: number;
  referencePrepZInput?: string;
  longToolReferencePrepZInput?: string;
  machineSettingsDirty?: boolean;
  machineSettingsHasUnsavedChanges?: boolean;
  machineSettingsRuntimeStatus?: "coherent" | "unconfirmed" | "refresh_failed" | "inconsistent";
  hasReference?: boolean;
}) {
  const connected = options?.connected ?? false;
  const onConnectRuntime = options?.onConnectRuntime ?? vi.fn();
  const currentRuntime = runtime(connected, {
    state: options?.runtimeState,
    referencePrepZ: options?.referencePrepZ,
    longToolReferencePrepZ: options?.longToolReferencePrepZ,
  });
  const refreshRuntime = options?.refreshRuntime ?? vi.fn().mockResolvedValue(currentRuntime);
  const consistencyProps = {
    machineSettingsDirty: options?.machineSettingsDirty ?? false,
    machineSettingsHasUnsavedChanges: options?.machineSettingsHasUnsavedChanges ?? false,
    machineSettingsRuntimeStatus: options?.machineSettingsRuntimeStatus ?? "coherent",
  };

  const renderResult = render(
    <ReferenceWorkspace
      {...consistencyProps}
      machine={machine(currentRuntime, refreshRuntime)}
      runtime={currentRuntime}
      referenceSession={(options?.hasReference ? { referencia_z: { z_mm: 0 } } : null) as ReferenceSession | null}
      referenceBusy={false}
      selectedOperation={{ id: "op-1", herramienta: "Broca 0.8 mm", tool_reference_profile: options?.toolReferenceProfile ?? "standard" } as unknown as Operation}
      heightMap={null}
      machineSettingsInput={{
        reference_prep_z_mm: options?.referencePrepZInput ?? "115",
        long_tool_change_clearance_z_mm: options?.longToolReferencePrepZInput ?? "130",
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

  return { ...renderResult, onConnectRuntime, refreshRuntime };
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

  it("muestra la herramienta, el perfil largo y su Z antes de referenciar", () => {
    renderReference({ connected: true, toolReferenceProfile: "long_tool" });

    expect(screen.getByText("Herramienta: Broca 0.8 mm")).toBeInTheDocument();
    expect(screen.getAllByText("Perfil de cambio: Herramienta larga").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Z de aproximación a referencia: 115.000 mm").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Z segura durante cambio: 130.000 mm").length).toBeGreaterThan(0);
    expect(screen.getByText("Esta altura solo se usa durante el cambio de herramienta. No modifica el mapa ni la compensación Z.")).toBeInTheDocument();
    expect(screen.getByLabelText("Z segura de cambio para herramienta larga (mm)")).toHaveValue("130");
  });

  it("distingue la Z larga editada de la activa y bloquea movimientos de referencia", () => {
    renderReference({
      connected: true,
      runtimeState: "REFERENCE_CAPTURED",
      referencePrepZ: 105,
      longToolReferencePrepZ: 105,
      referencePrepZInput: "105.0",
      longToolReferencePrepZInput: "130",
      toolReferenceProfile: "long_tool",
      machineSettingsDirty: true,
      machineSettingsHasUnsavedChanges: true,
      hasReference: true,
    });

    expect(screen.getByText("Cambios sin guardar")).toBeInTheDocument();
    expect(screen.getByText("Hay cambios de configuración sin guardar.")).toBeInTheDocument();
    expect(screen.getByText("Z de aproximación a referencia: 105.000 mm")).toBeInTheDocument();
    expect(screen.getByText("Pendiente de guardar para cambio: 130.000 mm")).toBeInTheDocument();
    expect(screen.getByText("Guarde la configuración antes de realizar movimientos de referencia.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sondear referencia ahora" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Volver a medir referencia" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Ir al punto de referencia" })).toBeDisabled();
  });

  it("bloquea preparación inicial y repetición mientras la configuración está dirty", () => {
    renderReference({
      connected: true,
      runtimeState: "DIAGNOSTIC",
      referencePrepZ: 105,
      longToolReferencePrepZ: 105,
      referencePrepZInput: "115",
      longToolReferencePrepZInput: "130",
      machineSettingsDirty: true,
      machineSettingsHasUnsavedChanges: true,
    });

    expect(screen.getByRole("button", { name: "Realizar homing, subir Z e ir al centro" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Repetir preparación" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Guardar configuración" })).toBeEnabled();
  });

  it("usa reference_prep_z para ambos perfiles y separa el clearance de cambio", () => {
    const standardRender = renderReference({
      connected: true,
      referencePrepZ: 105,
      longToolReferencePrepZ: 130,
      referencePrepZInput: "105",
      longToolReferencePrepZInput: "130",
      toolReferenceProfile: "standard",
    });
    expect(screen.getByText("Perfil de cambio: Estándar")).toBeInTheDocument();
    expect(screen.getByText("Z de aproximación a referencia: 105.000 mm")).toBeInTheDocument();
    expect(screen.getByText("Z segura durante cambio: 115.000 mm")).toBeInTheDocument();

    standardRender.unmount();
    renderReference({
      connected: true,
      referencePrepZ: 105,
      longToolReferencePrepZ: 130,
      referencePrepZInput: "105",
      longToolReferencePrepZInput: "130",
      toolReferenceProfile: "long_tool",
    });
    expect(screen.getByText("Perfil de cambio: Herramienta larga")).toBeInTheDocument();
    expect(screen.getByText("Z de aproximación a referencia: 105.000 mm")).toBeInTheDocument();
    expect(screen.getByText("Z segura durante cambio: 130.000 mm")).toBeInTheDocument();
  });

  it("muestra verde cuando todos los enlaces del runtime están listos", () => {
    renderReference({ connected: true });
    expect(screen.getByText("CONECTADO")).toHaveClass("status-badge--success");
  });
});
