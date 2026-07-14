import type { HealthResponse, MachineSession, SystemInfoResponse } from "../types";
import { StatusBadge } from "./StatusBadge";

type SystemPageProps = {
  health: HealthResponse | null;
  systemInfo: SystemInfoResponse | null;
  machineSession: MachineSession | null;
  refreshing: boolean;
  onRefresh: () => Promise<void>;
};

export function SystemPage({ health, systemInfo, machineSession, refreshing, onRefresh }: SystemPageProps) {
  return (
    <div className="workspace-column">
      <article className="panel diagnostics-hero">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Sistema</p>
            <h2>Diagnostico seguro</h2>
          </div>
          <button className="button" type="button" onClick={() => void onRefresh()} disabled={refreshing}>
            {refreshing ? "Actualizando..." : "Actualizar diagnostico"}
          </button>
        </div>
        <p>
          La aplicacion opera en modo simulado y no inicia conexiones de movimiento, homing, sondeo ni ejecucion de G-code.
        </p>
      </article>

      <div className="diagnostics-grid">
        <article className="panel">
          <div className="section-heading">
            <h3>Estado general</h3>
            <StatusBadge tone="success">{health?.estado ?? "sin datos"}</StatusBadge>
          </div>
          <dl className="info-list">
            <div><dt>Version</dt><dd>{health?.version ?? "-"}</dd></div>
            <div><dt>Modo de maquina</dt><dd>{health?.modo_maquina ?? "-"}</dd></div>
            <div><dt>Almacenamiento</dt><dd>{health?.almacenamiento ?? "-"}</dd></div>
          </dl>
        </article>

        <article className="panel">
          <div className="section-heading">
            <h3>Informacion del sistema</h3>
            <StatusBadge tone={systemInfo?.almacenamiento_disponible ? "success" : "danger"}>
              {systemInfo?.estado_api ?? "sin datos"}
            </StatusBadge>
          </div>
          <dl className="info-list">
            <div><dt>Python</dt><dd>{systemInfo?.version_python ?? "-"}</dd></div>
            <div><dt>Hora del servidor</dt><dd>{systemInfo?.hora_servidor ?? "-"}</dd></div>
            <div><dt>Almacenamiento</dt><dd>{systemInfo?.almacenamiento_disponible ? "Disponible" : "No disponible"}</dd></div>
          </dl>
        </article>

        <article className="panel">
          <div className="section-heading">
            <h3>Sesion simulada</h3>
            <StatusBadge tone="info">{machineSession?.estado ?? "sin datos"}</StatusBadge>
          </div>
          <ul className="issue-list">
            {(machineSession?.operaciones_permitidas ?? []).map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </article>

        <article className="panel panel--disabled">
          <div className="section-heading">
            <h3>Movimiento y mecanizado</h3>
            <StatusBadge tone="neutral">Reservado</StatusBadge>
          </div>
          <p>Disponible en una fase posterior, despues de la validacion de seguridad.</p>
        </article>
      </div>
    </div>
  );
}
