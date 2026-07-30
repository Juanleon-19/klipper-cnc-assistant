import { createContext, useContext } from "react";

import type { MachineRuntime } from "../types";

export type MachineAction = "connect" | "disconnect" | "diagnostic" | "initialize" | "manual-on" | "manual-off" | "probe-request" | "probe-confirm" | "tool-change-position" | "cancel" | "safe-stop" | "emergency" | "refresh";

export type MachineContextValue = {
  runtime: MachineRuntime | null;
  refreshing: boolean;
  isPhysical: boolean;
  modeLabel: string;
  runtimeState: string;
  connected: boolean;
  homedAxes: string;
  klipperReady: boolean;
  serialRecent: boolean;
  telemetryRecent: boolean;
  movementAuthorized: boolean;
  lastError: string | null;
  runMachineAction: (action: MachineAction, targetZ?: number) => Promise<void>;
  refreshRuntime: () => Promise<void>;
};

const defaultValue: MachineContextValue = {
  runtime: null,
  refreshing: false,
  isPhysical: false,
  modeLabel: "SIMULADO",
  runtimeState: "DISCONNECTED",
  connected: false,
  homedAxes: "",
  klipperReady: false,
  serialRecent: false,
  telemetryRecent: false,
  movementAuthorized: false,
  lastError: null,
  runMachineAction: async () => {},
  refreshRuntime: async () => {},
};

export const MachineContext = createContext<MachineContextValue>(defaultValue);

export function useMachineStatus() {
  return useContext(MachineContext);
}

export function buildMachineContextValue(
  runtime: MachineRuntime | null,
  refreshing: boolean,
  runMachineAction: MachineContextValue["runMachineAction"],
  refreshRuntime: MachineContextValue["refreshRuntime"]
): MachineContextValue {
  const safety = runtime?.safety ?? {};
  const klipper = runtime?.klipper ?? {};
  return {
    runtime,
    refreshing,
    isPhysical: runtime?.mode === "PHYSICAL",
    modeLabel: runtime?.mode === "PHYSICAL" ? "FÍSICO" : runtime?.mode_label ?? "SIMULADO",
    runtimeState: runtime?.state ?? "DISCONNECTED",
    connected: runtime?.moonraker?.http_connected === true,
    homedAxes: typeof klipper.homed_axes === "string" ? klipper.homed_axes : "",
    klipperReady: klipper.ready === true,
    serialRecent: safety.serial_recent === true,
    telemetryRecent: safety.telemetry_recent === true,
    movementAuthorized: safety.movement_authorized === true,
    lastError: runtime?.last_error ?? null,
    runMachineAction,
    refreshRuntime,
  };
}
