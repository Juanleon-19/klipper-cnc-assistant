export function SystemBanner() {
  return (
    <div className="machine-banner" role="status" aria-live="polite">
      <span className="machine-banner__dot" aria-hidden="true" />
      <span>MÁQUINA EN MODO SIMULADO</span>
    </div>
  );
}
