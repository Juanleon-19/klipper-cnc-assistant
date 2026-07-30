import { useMachineStatus } from "../context/MachineContext";

export function SystemBanner() {
  const machine = useMachineStatus();
  return (
    <div className={`machine-banner${machine.isPhysical ? " machine-banner--physical" : ""}`} role="status" aria-live="polite">
      <span className="machine-banner__dot" aria-hidden="true" />
      <span>MÁQUINA EN MODO {machine.modeLabel}</span>
    </div>
  );
}
