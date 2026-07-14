import { useEffect, useState } from "react";

import { summarizeMachineMode, toneForStatus, translateStatus } from "../lib/ui";
import type { HealthResponse, MachineRuntime, MachineSession, SystemInfoResponse } from "../types";
import { StatusBadge } from "./StatusBadge";

type SystemPageProps = {
  health: HealthResponse | null;
  systemInfo: SystemInfoResponse | null;
  machineSession: MachineSession | null;
  machineRuntime: MachineRuntime | null;
  refreshing: boolean;
  onRefresh: () => Promise<void>;
  onMachineAction: (action: string, targetZ?: number) => Promise<void>;
};

function value(record: Record<string, unknown> | undefined, key: string) {
  const current = record?.[key];
  if (current === null || current === undefined || current === "") return "-";
  if (typeof current === "boolean") return current ? "Sí" : "No";
  if (typeof current === "object") return JSON.stringify(current);
  return String(current);
}

function DiagnosticCard({ title, status, rows }: { title: string; status?: string; rows: Array<[string, string]> }) {
  return (
    <article className="panel info-card diagnostic-card">
      <div className="section-heading section-heading--compact">
        <h3>{title}</h3>
        {status ? <StatusBadge tone={toneForStatus(status)}>{translateStatus(status)}</StatusBadge> : null}
      </div>
      <dl className="definition-grid definition-grid--dense">
        {rows.map(([label, content]) => (
          <div key={label}><dt>{label}</dt><dd>{content}</dd></div>
        ))}
      </dl>
    </article>
  );
}

export function SystemPage({ health, systemInfo, machineSession, machineRuntime, refreshing, onRefresh, onMachineAction }: SystemPageProps) {
  const [targetZ, setTargetZ] = useState("10");
  const mode = machineRuntime?.mode_label ?? summarizeMachineMode(health?.modo_maquina).toUpperCase();
  const physical = machineRuntime?.mode === "PHYSICAL";
  const movementAuthorized = machineRuntime?.safety?.movement_authorized === true;
  const probeRequested = machineRuntime?.controller?.probe_requested === true;

  useEffect(() => {
    const id = window.setInterval(() => { void onRefresh(); }, 200);
    return () => window.clearInterval(id);
  }, [onRefresh]);

  const parsedZ = Number(targetZ.replace(",", "."));
  const invalidZ = !Number.isFinite(parsedZ);

  return (
    <div className="page-stack system-physical">
      <article className="panel hero-panel hero-panel--system">
        <div className="section-heading">
          <div>
            <p className="eyebrow">Sistema físico</p>
            <h2>Diagnóstico, inicialización y controles seguros</h2>
          </div>
          <div className="toolbar-inline">
            <StatusBadge tone={physical ? "danger" : "info"}>{mode}</StatusBadge>
            <StatusBadge tone={toneForStatus(machineRuntime?.health ?? health?.estado)}>{translateStatus(machineRuntime?.health ?? health?.estado)}</StatusBadge>
            <button className="button button--ghost" type="button" onClick={() => void onRefresh()} disabled={refreshing}>
              {refreshing ? "Actualizando..." : "Actualizar"}
            </button>
          </div>
        </div>
        <p className="muted">MODO {mode} — las acciones de movimiento solo se habilitan en modo físico con conexión, diagnóstico y autorización de seguridad.</p>
        {machineRuntime?.last_error ? <div className="alert alert--error">{machineRuntime.last_error}</div> : null}
      </article>

      <div className="machine-action-strip">
        <button className="button" type="button" onClick={() => void onMachineAction("connect")} disabled={!physical || refreshing}>Conectar</button>
        <button className="button button--ghost" type="button" onClick={() => void onMachineAction("diagnostic")} disabled={!physical || refreshing}>Modo diagnóstico</button>
        <label className="inline-field">
          Z objetivo absoluto (mm)
          <input value={targetZ} onChange={(event) => setTargetZ(event.target.value)} inputMode="decimal" disabled={!physical || refreshing} />
        </label>
        <button className="button" type="button" onClick={() => void onMachineAction("initialize", parsedZ)} disabled={!physical || refreshing || invalidZ}>Inicializar máquina</button>
        <button className="button button--ghost" type="button" onClick={() => void onMachineAction("manual-on")} disabled={!physical || refreshing || !machineRuntime || movementAuthorized}>Habilitar joystick</button>
        <button className="button button--ghost" type="button" onClick={() => void onMachineAction("probe-request")} disabled={!physical || refreshing}>Solicitar sonda</button>
        <button className="button" type="button" onClick={() => void onMachineAction("probe-confirm")} disabled={!physical || refreshing || !probeRequested}>Confirmar sonda</button>
        <button className="button button--ghost" type="button" onClick={() => void onMachineAction("cancel")} disabled={!physical || refreshing}>Cancelar</button>
        <button className="button button--danger" type="button" onClick={() => { if (window.confirm("Enviar M112 a Klipper y bloquear movimientos?")) void onMachineAction("emergency"); }} disabled={!physical || refreshing}>Emergencia M112</button>
      </div>
      {invalidZ ? <p className="form-error">Z objetivo debe ser un número finito en milímetros.</p> : null}

      <div className="info-grid info-grid--triple">
        <DiagnosticCard title="Aplicación" status={systemInfo?.estado_api} rows={[
          ["API", translateStatus(health?.estado)],
          ["Versión", systemInfo?.version_aplicacion ?? "-"],
          ["Modo", mode],
          ["Uptime", value(machineRuntime?.application, "uptime_s") + " s"],
          ["Esquema", systemInfo?.schema_version ?? "-"],
        ]} />
        <DiagnosticCard title="Moonraker" status={value(machineRuntime?.moonraker, "http_connected")} rows={[
          ["HTTP", value(machineRuntime?.moonraker, "http_connected")],
          ["WebSocket", value(machineRuntime?.moonraker, "websocket_connected")],
          ["URL", value(machineRuntime?.moonraker, "url")],
          ["Último error", value(machineRuntime?.moonraker, "last_error")],
        ]} />
        <DiagnosticCard title="Klipper" status={value(machineRuntime?.klipper, "ready")} rows={[
          ["Ready", value(machineRuntime?.klipper, "ready")],
          ["Posición", value(machineRuntime?.klipper, "position")],
          ["Homing", value(machineRuntime?.klipper, "homed_axes")],
          ["Límites", value(machineRuntime?.klipper, "limits")],
          ["Velocidad máx", value(machineRuntime?.klipper, "max_velocity")],
        ]} />
      </div>

      <div className="info-grid info-grid--triple">
        <DiagnosticCard title="Arduino" status={value(machineRuntime?.arduino, "open")} rows={[
          ["Puerto", value(machineRuntime?.arduino, "port")],
          ["Baudrate", value(machineRuntime?.arduino, "baudrate")],
          ["Abierto", value(machineRuntime?.arduino, "open")],
          ["Paquetes válidos", value(machineRuntime?.arduino, "valid_packets")],
          ["Checksum", value(machineRuntime?.arduino, "checksum_errors")],
        ]} />
        <DiagnosticCard title="Controlador" status={String(machineRuntime?.state ?? machineSession?.estado ?? "-")} rows={[
          ["Dirección", value(machineRuntime?.controller, "direction")],
          ["X/Y", `${value(machineRuntime?.controller, "x")} / ${value(machineRuntime?.controller, "y")}`],
          ["Joystick", value(machineRuntime?.controller, "joystick_button")],
          ["Botón externo", value(machineRuntime?.controller, "external_button")],
          ["Sonda", value(machineRuntime?.controller, "probe")],
          ["Modo", value(machineRuntime?.controller, "jog_mode")],
          ["Distancia", value(machineRuntime?.controller, "jog_distance_mm") + " mm"],
        ]} />
        <DiagnosticCard title="Seguridad" status={movementAuthorized ? "ok" : "bloqueado"} rows={[
          ["Telemetría reciente", value(machineRuntime?.safety, "telemetry_recent")],
          ["Serie reciente", value(machineRuntime?.safety, "serial_recent")],
          ["Klipper listo", value(machineRuntime?.safety, "klipper_ready")],
          ["Homing", value(machineRuntime?.safety, "homed_axes_required")],
          ["Movimiento", value(machineRuntime?.safety, "movement_authorized")],
          ["Bloqueo", value(machineRuntime?.safety, "blocked_reason")],
        ]} />
      </div>

      <article className="panel info-card">
        <div className="section-heading section-heading--compact"><h3>Inicialización y eventos</h3></div>
        <div className="machine-event-list">
          {(machineRuntime?.initialization_steps ?? []).map((step) => (
            <div key={`${step.name}-${step.timestamp}`} className="machine-event"><strong>{String(step.name)}</strong><span>{String(step.status)} · {String(step.detail)}</span></div>
          ))}
          {(machineRuntime?.events ?? []).slice(-8).map((event) => (
            <div key={`${event.timestamp}-${event.message}`} className="machine-event"><strong>{String(event.level)}</strong><span>{String(event.message)}</span></div>
          ))}
        </div>
      </article>
    </div>
  );
}
