import { summarizeMachineMode, toneForStatus, translateStatus } from "../lib/ui";
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
    <div className="page-stack">
      <article className="panel hero-panel hero-panel--system">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Sistema</p>
            <h2>Diagnóstico y estado del servicio</h2>
          </div>
          <button className="button button--ghost" type="button" onClick={() => void onRefresh()} disabled={refreshing}>
            {refreshing ? "Actualizando..." : "Actualizar"}
          </button>
        </div>
        <p className="muted">Esta vista es secundaria. La aplicación sigue en modo simulado y no envía movimientos, homing, sondeo ni ejecución de G-code.</p>
      </article>

      <div className="info-grid info-grid--triple">
        <article className="panel info-card">
          <div className="section-heading">
            <h3>API</h3>
            <StatusBadge tone={toneForStatus(health?.estado)}>{translateStatus(health?.estado)}</StatusBadge>
          </div>
          <dl className="definition-grid">
            <div><dt>Versión</dt><dd>{health?.version ?? "-"}</dd></div>
            <div><dt>Almacenamiento</dt><dd>{translateStatus(health?.almacenamiento)}</dd></div>
            <div><dt>Modo</dt><dd>{summarizeMachineMode(health?.modo_maquina)}</dd></div>
          </dl>
        </article>

        <article className="panel info-card">
          <div className="section-heading">
            <h3>Servidor</h3>
            <StatusBadge tone={systemInfo?.almacenamiento_disponible ? "success" : "danger"}>{translateStatus(systemInfo?.estado_api)}</StatusBadge>
          </div>
          <dl className="definition-grid">
            <div><dt>Python</dt><dd>{systemInfo?.version_python ?? "-"}</dd></div>
            <div><dt>Hora</dt><dd>{systemInfo?.hora_servidor ?? "-"}</dd></div>
            <div><dt>Aplicación</dt><dd>{systemInfo?.version_aplicacion ?? "-"}</dd></div>
            <div><dt>Backend</dt><dd>{systemInfo?.backend_version ?? "-"}</dd></div>
            <div><dt>Frontend build</dt><dd>{systemInfo?.frontend_build ?? "-"}</dd></div>
            <div><dt>Esquema</dt><dd>{systemInfo?.schema_version ?? "-"}</dd></div>
            <div><dt>Commit</dt><dd>{systemInfo?.git_commit ?? "No disponible"}</dd></div>
          </dl>
        </article>

        <article className="panel info-card">
          <div className="section-heading">
            <h3>Sesión simulada</h3>
            <StatusBadge tone="info">{translateStatus(machineSession?.estado)}</StatusBadge>
          </div>
          <dl className="definition-grid">
            <div><dt>Home realizado</dt><dd>{machineSession?.home_realizado ? "Sí" : "No"}</dd></div>
            <div><dt>Material montado</dt><dd>{machineSession?.material_montado ? "Sí" : "No"}</dd></div>
            <div><dt>Origen XY</dt><dd>{machineSession?.origen_xy_definido ? "Sí" : "No"}</dd></div>
          </dl>
        </article>
      </div>

      <div className="info-grid info-grid--double">
        <article className="panel info-card">
          <div className="section-heading">
            <h3>Operaciones permitidas</h3>
            <StatusBadge tone="info">Simulado</StatusBadge>
          </div>
          <ul className="chip-list">
            {(machineSession?.operaciones_permitidas ?? []).map((item) => (
              <li key={item} className="chip chip--info">{translateStatus(item)}</li>
            ))}
          </ul>
        </article>

        <article className="panel info-card panel--disabled">
          <div className="section-heading">
            <h3>Movimiento y mecanizado</h3>
            <StatusBadge tone="neutral">Reservado</StatusBadge>
          </div>
          <p>Disponible en una fase posterior, después de la validación de seguridad.</p>
        </article>
      </div>
    </div>
  );
}
