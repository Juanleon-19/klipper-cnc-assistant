import type { MutableRefObject, ReactNode } from "react";

import { StatusBadge } from "../../components/StatusBadge";
import { formatMillimeters } from "../../lib/format";
import type { HeightMap, MachineRuntime, Operation, ReferenceSession, ReferenceStep, CapturedPosition } from "../../types";
import type { MachineContextValue } from "../system/MachineContext";

type InputState = { x_mm: string; y_mm: string };
type ZInputState = { x_mm: string; y_mm: string; z_mm: string };
type ReferenceFieldErrors = Partial<Record<"x_mm" | "y_mm" | "z_mm", string>>;
type ReferenceMoveResult = { reference_x: number; reference_y: number; preparation_z: number; final_state: string; message: string } | null;
type MachineSettingsInput = {
  reference_prep_z_mm: string;
  reference_prep_z_feed_mm_min: string;
  move_total_timeout_s: string;
  no_progress_timeout_s: string;
  position_tolerance_mm: string;
  velocity_tolerance_mm_s: string;
  reference_probe_step_mm: string;
  reference_probe_feed_mm_min: string;
  reference_probe_retract_mm: string;
  reference_probe_retract_feed_mm_min: string;
};

type ReferenceWorkspaceProps = {
  machine: MachineContextValue;
  runtime: MachineRuntime | null;
  referenceSession: ReferenceSession | null;
  referenceBusy: boolean;
  selectedOperation: Operation | null;
  heightMap: HeightMap | null;
  machineSettingsInput: MachineSettingsInput;
  machineSettingsMessage: string;
  referenceMoveResult: ReferenceMoveResult;
  workOrigin: InputState;
  zReference: ZInputState;
  useWorkOriginXYForZ: boolean;
  workOriginErrors: ReferenceFieldErrors;
  zReferenceErrors: ReferenceFieldErrors;
  workOriginRefs: MutableRefObject<Record<"x_mm" | "y_mm", HTMLInputElement | null>>;
  zReferenceRefs: MutableRefObject<Record<"x_mm" | "y_mm" | "z_mm", HTMLInputElement | null>>;
  formatCapturedPosition: (position: CapturedPosition | null | undefined) => string;
  renderReferenceStep: (step: ReferenceStep, index: number) => ReactNode;
  onConnectRuntime: () => void;
  onDiagnosticMode: () => void;
  onReconnectArduino: () => void;
  onSaveMachineSettings: () => void;
  onInitialize: () => void;
  onEnableManual: () => void;
  onCapturePhysicalWorkOrigin: () => void;
  onCancelOperation: () => void;
  onToolChangePosition: () => void;
  onProbeRequest: () => void;
  onRemeasurePhysicalReference: () => void;
  onGoToReferencePoint: () => void;
  onConfirmMachineReference: () => void;
  onSubmitWorkOrigin: () => void;
  onSubmitZReference: () => void;
  onValidateHeightMap: () => void;
  onMachineSettingChange: (field: keyof MachineSettingsInput, value: string) => void;
  onToggleUseWorkOriginXYForZ: (checked: boolean) => void;
  onWorkOriginChange: (field: "x_mm" | "y_mm", value: string) => void;
  onZReferenceChange: (field: "x_mm" | "y_mm" | "z_mm", value: string) => void;
};

export function ReferenceWorkspace({
  machine,
  runtime,
  referenceSession,
  referenceBusy,
  selectedOperation,
  heightMap,
  machineSettingsInput,
  machineSettingsMessage,
  referenceMoveResult,
  workOrigin,
  zReference,
  useWorkOriginXYForZ,
  workOriginErrors,
  zReferenceErrors,
  workOriginRefs,
  zReferenceRefs,
  formatCapturedPosition,
  renderReferenceStep,
  onConnectRuntime,
  onDiagnosticMode,
  onReconnectArduino,
  onSaveMachineSettings,
  onInitialize,
  onEnableManual,
  onCapturePhysicalWorkOrigin,
  onCancelOperation,
  onToolChangePosition,
  onProbeRequest,
  onRemeasurePhysicalReference,
  onGoToReferencePoint,
  onConfirmMachineReference,
  onSubmitWorkOrigin,
  onSubmitZReference,
  onValidateHeightMap,
  onMachineSettingChange,
  onToggleUseWorkOriginXYForZ,
  onWorkOriginChange,
  onZReferenceChange,
}: ReferenceWorkspaceProps) {
  const moonraker = ((runtime?.moonraker ?? {}) as Record<string, unknown>);
  const klipper = ((runtime?.klipper ?? {}) as Record<string, unknown>);
  const preparation = ((runtime?.preparation ?? {}) as Record<string, unknown>);
  const toolChange = ((runtime?.tool_change ?? {}) as Record<string, unknown>);
  const arduino = ((runtime?.arduino ?? {}) as Record<string, unknown>);
  const controller = ((runtime?.controller ?? {}) as Record<string, unknown>);
  const probeLive = ((runtime?.probe_live ?? {}) as Record<string, unknown>);
  const position = (klipper.position ?? null) as Record<string, unknown> | null;
  const livePosition = (position?.live_position ?? null) as Record<string, unknown> | null;
  const commandedPosition = (position?.commanded_position ?? null) as Record<string, unknown> | null;
  const activeOperation = (runtime?.active_operation ?? null) as Record<string, unknown> | null;
  const lastProbeFailure = (runtime?.last_probe_failure ?? null) as Record<string, unknown> | null;
  const lastMovement = (runtime?.last_movement ?? null) as Record<string, unknown> | null;
  const initializationSteps = (runtime?.initialization_steps ?? []) as Array<Record<string, unknown>>;
  const preparationStage = initializationSteps[initializationSteps.length - 1] ?? null;
  const movementTarget = (lastMovement?.target ?? null) as Record<string, unknown> | null;
  const referencePrepZ = typeof preparation.reference_prep_z_mm === "number" ? preparation.reference_prep_z_mm : 115;
  const referencePrepZFeed = typeof preparation.reference_prep_z_feed_mm_min === "number" ? preparation.reference_prep_z_feed_mm_min : 180;
  const referencePrepXyFeed = typeof preparation.reference_prep_xy_feed_mm_min === "number" ? preparation.reference_prep_xy_feed_mm_min : 1800;
  const centerX = typeof preparation.center_x_mm === "number" ? preparation.center_x_mm : null;
  const centerY = typeof preparation.center_y_mm === "number" ? preparation.center_y_mm : null;
  const toolChangeX = typeof toolChange.x_mm === "number" ? toolChange.x_mm : 0;
  const toolChangeY = typeof toolChange.y_mm === "number" ? toolChange.y_mm : 0;
  const toolChangeZ = typeof toolChange.z_mm === "number" ? toolChange.z_mm : 115;
  const toolChangeZFeed = typeof toolChange.z_feed_mm_min === "number" ? toolChange.z_feed_mm_min : 180;
  const referenceProbeFeed = Number(machineSettingsInput.reference_probe_feed_mm_min);
  const probeStableMs = typeof probeLive.filtered_stable_ms === "number" ? probeLive.filtered_stable_ms : null;
  const probeRequiredStableMs = typeof probeLive.required_stable_ms === "number" ? probeLive.required_stable_ms : null;
  const probeOpenOk = probeLive.open_ok === true;
  const probeFreshOk = probeLive.fresh_ok === true;
  const canConnect = machine.isPhysical && machine.runtimeState === "DISCONNECTED";
  const canInitialize = machine.isPhysical && ["DIAGNOSTIC", "READY_FOR_HOME", "HOMED", "ERROR", "CANCELLED"].includes(machine.runtimeState);
  const canEnableJog = machine.isPhysical && machine.runtimeState === "WAITING_FOR_XY_REFERENCE";
  const canArm = machine.isPhysical && ["WAITING_FOR_XY_REFERENCE", "REFERENCE_CAPTURED"].includes(machine.runtimeState);
  const canProbe = machine.isPhysical && ["WAITING_FOR_XY_REFERENCE", "REFERENCE_ARMED", "REFERENCE_CAPTURED"].includes(machine.runtimeState);
  const canGoToReference = machine.isPhysical && Boolean(referenceSession?.referencia_z) && Boolean(selectedOperation) && !runtime?.active_operation;
  const arduinoState = String(arduino.connection_state ?? arduino.state ?? "DISCONNECTED");
  const reconnectBlocked = !machine.isPhysical || referenceBusy || machine.refreshing || machine.runtimeState === "STOPPING" || Boolean(activeOperation) || runtime?.mode === "SIMULATED" || arduinoState === "CONNECTING" || arduinoState === "RETRY_WAIT" || arduinoState === "DISCOVERING";

  if (machine.isPhysical) {
    return (
      <div className="stack gap-md">
        <article className="panel">
          <div className="section-heading section-heading--stacked">
            <div>
              <p className="eyebrow">Preparación física del montaje</p>
              <h3>Referencia X/Y/Z medida</h3>
            </div>
            <StatusBadge tone="success">{machine.modeLabel}</StatusBadge>
          </div>
          <p className="muted">Coloque el origen X/Y real del G-code con joystick y mida Z con la sonda. La Z segura de traslado no es referencia Z ni profundidad de fresado.</p>
          {machine.lastError ? <div className="alert alert--warning">{machine.lastError}</div> : null}
          <div className="info-grid info-grid--double compact-grid">
            <div className="metric-box"><span>Estado</span><strong>{machine.runtimeState}</strong></div>
            <div className="metric-box"><span>Moonraker HTTP</span><strong>{String(moonraker.http_state ?? "DISCONNECTED")}</strong></div>
            <div className="metric-box"><span>WebSocket</span><strong>{String(moonraker.websocket_state ?? moonraker.telemetry_state ?? "DISCONNECTED")}</strong></div>
            <div className="metric-box"><span>Klipper</span><strong>{String(klipper.state ?? (machine.klipperReady ? "ready" : "no ready"))}</strong></div>
            <div className="metric-box"><span>Homing</span><strong>{machine.homedAxes || "sin ejes"}</strong></div>
            <div className="metric-box"><span>Arduino</span><strong>{arduinoState}</strong></div>
          </div>
        </article>

        <article className="panel">
          <div className="section-heading"><h3>1. Conexión y diagnóstico</h3></div>
          <p className="muted">Conecta Moonraker HTTP, WebSocket, Klipper y Arduino. En diagnóstico puede observar joystick, botón externo y sonda sin movimiento.</p>
          <div className="action-grid action-grid--inline">
            <button className="button" type="button" disabled={!canConnect || machine.refreshing} onClick={onConnectRuntime}>Conectar runtime</button>
            <button className="button button--ghost" type="button" disabled={!machine.isPhysical || machine.refreshing || machine.runtimeState === "DISCONNECTED"} onClick={onDiagnosticMode}>Modo diagnóstico</button>
            <button className="button button--ghost" type="button" disabled={reconnectBlocked} onClick={onReconnectArduino}>Reconectar Arduino</button>
          </div>
          <p className="muted">Reconectar Arduino no habilita movimiento. La nueva sesión queda en diagnóstico, con control manual desactivado y `ready_for_jog = false`.</p>
          <dl className="definition-grid definition-grid--compact">
            <div><dt>Puerto configurado</dt><dd>{String(arduino.configured_port ?? arduino.port ?? "-")}</dd></div>
            <div><dt>Puerto conectado</dt><dd>{String(arduino.connected_port ?? arduino.port ?? "-")}</dd></div>
            <div><dt>USB identidad</dt><dd>{String(((arduino.usb_identity as Record<string, unknown> | null)?.serial_number) ?? ((arduino.usb_identity as Record<string, unknown> | null)?.port) ?? "sin identidad")}</dd></div>
            <div><dt>Generación</dt><dd>{String(arduino.generation ?? 0)}</dd></div>
            <div><dt>Reconexiones</dt><dd>{String(arduino.reconnects ?? 0)}</dd></div>
            <div><dt>Paquetes válidos</dt><dd>{String(arduino.valid_packets ?? 0)}</dd></div>
            <div><dt>Edad último paquete</dt><dd>{typeof arduino.last_packet_age_s === "number" ? `${Number(arduino.last_packet_age_s).toFixed(2)} s` : "-"}</dd></div>
            <div><dt>HTTP activa</dt><dd>{typeof moonraker.last_http_observation_age_s === "number" ? `${Number(moonraker.last_http_observation_age_s).toFixed(2)} s` : "sin consulta"}</dd></div>
            <div><dt>Edad último WS</dt><dd>{typeof moonraker.last_websocket_message_age_s === "number" ? `${Number(moonraker.last_websocket_message_age_s).toFixed(2)} s` : "sin mensajes"}</dd></div>
            <div><dt>Edad de posición</dt><dd>{typeof moonraker.last_position_age_s === "number" ? `${Number(moonraker.last_position_age_s).toFixed(2)} s` : "sin posición"}</dd></div>
            <div><dt>Dirección joystick</dt><dd>{String(controller.direction ?? "CENTER")}</dd></div>
            <div><dt>Botón externo</dt><dd>{controller.external_button ? "pulsado" : "reposo"}</dd></div>
            <div><dt>Sonda actual</dt><dd>{String(probeLive.display_state ?? (controller.probe ? "TRIGGERED" : "OPEN"))}</dd></div>
            <div><dt>Probe raw / filtrada</dt><dd>{String(probeLive.raw_value ?? controller.probe ?? false)} / {String(probeLive.filtered_triggered ?? controller.probe ?? false)}</dd></div>
            <div><dt>Edad del paquete</dt><dd>{typeof probeLive.packet_age_s === "number" ? `${(Number(probeLive.packet_age_s) * 1000).toFixed(0)} ms` : "-"}</dd></div>
            <div><dt>Estable / mínimo</dt><dd>{probeStableMs === null ? "-" : `${probeStableMs.toFixed(0)} ms`} / {probeRequiredStableMs === null ? "-" : `${probeRequiredStableMs.toFixed(0)} ms`}</dd></div>
            <div><dt>Precheck</dt><dd>OPEN {probeOpenOk ? "✓" : "✗"} · FRESH {probeFreshOk ? "✓" : "✗"} · STABLE {probeStableMs !== null && probeRequiredStableMs !== null && probeStableMs >= probeRequiredStableMs ? "✓" : "✗"}</dd></div>
            <div><dt>Telemetría</dt><dd>{String(moonraker.telemetry_state ?? "DISCONNECTED")}</dd></div>
            <div><dt>Operación activa</dt><dd>{activeOperation ? `${String(activeOperation.operation_type)} #${String(activeOperation.generation)}` : "ninguna"}</dd></div>
            <div><dt>Última excepción</dt><dd>{String(moonraker.last_websocket_error ?? moonraker.last_http_error ?? runtime?.last_error ?? "-")}</dd></div>
          </dl>
          {lastProbeFailure ? <div className="alert alert--warning">Último fallo de sonda (histórico): {String(lastProbeFailure.error ?? "-")}</div> : null}
        </article>

        <article className="panel">
          <div className="section-heading"><h3>2. Home, Z de preparación y centro</h3></div>
          <p className="muted">El backend envía G28, confirma `toolhead.homed_axes`, mueve primero Z a la altura de preparación configurada y después mueve X/Y al centro real calculado desde límites Klipper.</p>
          <div className="info-grid info-grid--double compact-grid">
            <div className="metric-box"><span>Z de preparación</span><strong>{formatMillimeters(referencePrepZ, 3)}</strong></div>
            <div className="metric-box"><span>Velocidad Z</span><strong>{referencePrepZFeed.toFixed(0)} mm/min · {(referencePrepZFeed / 60).toFixed(3)} mm/s</strong></div>
            <div className="metric-box"><span>Centro calculado</span><strong>X {formatMillimeters(centerX, 3)} · Y {formatMillimeters(centerY, 3)}</strong></div>
            <div className="metric-box"><span>Velocidad centro X/Y</span><strong>{referencePrepXyFeed.toFixed(0)} mm/min · {(referencePrepXyFeed / 60).toFixed(3)} mm/s</strong></div>
            <div className="metric-box"><span>Posición actual</span><strong>X {formatMillimeters(typeof position?.x === "number" ? Number(position.x) : null, 3)} · Y {formatMillimeters(typeof position?.y === "number" ? Number(position.y) : null, 3)} · Z {formatMillimeters(typeof position?.z === "number" ? Number(position.z) : null, 3)}</strong></div>
            <div className="metric-box"><span>Z en vivo</span><strong>{formatMillimeters(typeof livePosition?.z === "number" ? Number(livePosition.z) : null, 3)}</strong></div>
            <div className="metric-box"><span>Z comandada</span><strong>{formatMillimeters(typeof commandedPosition?.z === "number" ? Number(commandedPosition.z) : null, 3)}</strong></div>
            <div className="metric-box"><span>Velocidad observada</span><strong>{typeof position?.velocity === "number" ? `${Number(position.velocity).toFixed(3)} mm/s` : "-"}</strong></div>
            <div className="metric-box"><span>Fuente de posición</span><strong>{String(position?.source ?? "-")}</strong></div>
            <div className="metric-box"><span>Objetivo configurado</span><strong>X {formatMillimeters(centerX, 3)} · Y {formatMillimeters(centerY, 3)} · Z {formatMillimeters(referencePrepZ, 3)}</strong></div>
            <div className="metric-box"><span>Etapa actual</span><strong>{String(preparationStage?.name ?? "pendiente")}</strong></div>
            <div className="metric-box"><span>Objetivo enviado</span><strong>X {formatMillimeters(typeof movementTarget?.x === "number" ? Number(movementTarget.x) : null, 3)} · Y {formatMillimeters(typeof movementTarget?.y === "number" ? Number(movementTarget.y) : null, 3)} · Z {formatMillimeters(typeof movementTarget?.z === "number" ? Number(movementTarget.z) : null, 3)}</strong></div>
            <div className="metric-box"><span>Timeout calculado</span><strong>{typeof lastMovement?.timeout_s === "number" ? `${Number(lastMovement.timeout_s).toFixed(1)} s` : "-"}</strong></div>
            <div className="metric-box"><span>Z viva anterior</span><strong>{formatMillimeters(typeof lastMovement?.previous_live_z === "number" ? Number(lastMovement.previous_live_z) : null, 3)}</strong></div>
            <div className="metric-box"><span>Z viva actual</span><strong>{formatMillimeters(typeof lastMovement?.current_live_z === "number" ? Number(lastMovement.current_live_z) : null, 3)}</strong></div>
            <div className="metric-box"><span>Distancia anterior</span><strong>{formatMillimeters(typeof lastMovement?.previous_distance_mm === "number" ? Number(lastMovement.previous_distance_mm) : null, 3)}</strong></div>
            <div className="metric-box"><span>Distancia actual</span><strong>{formatMillimeters(typeof lastMovement?.current_distance_mm === "number" ? Number(lastMovement.current_distance_mm) : null, 3)}</strong></div>
            <div className="metric-box"><span>Fuente viva</span><strong>{String(lastMovement?.live_position_source ?? "-")}</strong></div>
            <div className="metric-box"><span>Muestras alejándose</span><strong>{typeof lastMovement?.consecutive_away_samples === "number" ? String(lastMovement.consecutive_away_samples) : "-"}</strong></div>
          </div>
          <details className="advanced-settings">
            <summary>Configuración avanzada de movimiento</summary>
            <div className="form-grid form-grid--dense">
              <label>Z de preparación (mm)<input value={machineSettingsInput.reference_prep_z_mm} inputMode="decimal" onChange={(event) => onMachineSettingChange("reference_prep_z_mm", event.target.value)} /></label>
              <label>Velocidad Z de preparación (mm/min)<input value={machineSettingsInput.reference_prep_z_feed_mm_min} inputMode="decimal" onChange={(event) => onMachineSettingChange("reference_prep_z_feed_mm_min", event.target.value)} /></label>
              <label>Timeout total de movimiento (s)<input value={machineSettingsInput.move_total_timeout_s} inputMode="decimal" onChange={(event) => onMachineSettingChange("move_total_timeout_s", event.target.value)} /></label>
              <label>Timeout sin progreso (s)<input value={machineSettingsInput.no_progress_timeout_s} inputMode="decimal" onChange={(event) => onMachineSettingChange("no_progress_timeout_s", event.target.value)} /></label>
              <label>Tolerancia de posición (mm)<input value={machineSettingsInput.position_tolerance_mm} inputMode="decimal" onChange={(event) => onMachineSettingChange("position_tolerance_mm", event.target.value)} /></label>
              <label>Tolerancia de velocidad (mm/s)<input value={machineSettingsInput.velocity_tolerance_mm_s} inputMode="decimal" onChange={(event) => onMachineSettingChange("velocity_tolerance_mm_s", event.target.value)} /></label>
            </div>
            <div className="action-grid action-grid--inline">
              <button className="button button--ghost" type="button" disabled={!machine.isPhysical || referenceBusy || machine.refreshing} onClick={onSaveMachineSettings}>Guardar configuración</button>
              <button className="button button--ghost" type="button" disabled={!canInitialize || referenceBusy || machine.refreshing} onClick={onInitialize}>Repetir preparación</button>
            </div>
            {machineSettingsMessage ? <p className="muted">{machineSettingsMessage}</p> : null}
          </details>
          <button className="button" type="button" disabled={!canInitialize || referenceBusy || machine.refreshing} onClick={onInitialize}>Realizar homing, subir Z e ir al centro</button>
          <div className="workflow-steps-grid">
            {initializationSteps.map((step, index) => (
              <div className="workflow-step-card" key={`${String(step.name)}-${index}`}>
                <div className="workflow-step-card__header"><span className="workflow-step">{index + 1}</span><div><strong>{String(step.name)}</strong><p className="muted">{String(step.detail ?? "")}</p></div><StatusBadge tone={String(step.status) === "ok" ? "success" : "warning"}>{String(step.status)}</StatusBadge></div>
              </div>
            ))}
          </div>
        </article>

        <article className="panel">
          <div className="section-heading"><h3>3. Posicionar X0/Y0 del G-code</h3></div>
          <p className="muted">Habilite joystick X/Y y coloque la herramienta exactamente sobre el X0/Y0 generado por FlatCAM. El jog es cardinal discreto y no mueve Z.</p>
          <div className="info-grid info-grid--double compact-grid">
            <div className="metric-box"><span>X máquina</span><strong>{formatMillimeters(typeof position?.x === "number" ? Number(position.x) : null, 3)}</strong></div>
            <div className="metric-box"><span>Y máquina</span><strong>{formatMillimeters(typeof position?.y === "number" ? Number(position.y) : null, 3)}</strong></div>
            <div className="metric-box"><span>Z máquina</span><strong>{formatMillimeters(typeof position?.z === "number" ? Number(position.z) : null, 3)}</strong></div>
            <div className="metric-box"><span>Modo jog</span><strong>{String(controller.jog_mode ?? "FINE")}</strong></div>
          </div>
          <div className="action-grid action-grid--inline"><button className="button button--ghost" type="button" disabled={!canEnableJog || referenceBusy || machine.refreshing} onClick={onEnableManual}>Reposicionar origen X/Y</button><button className="button" type="button" disabled={!machine.isPhysical || referenceBusy || machine.refreshing || !selectedOperation} onClick={onCapturePhysicalWorkOrigin}>Confirmar nuevo origen</button><button className="button button--ghost" type="button" disabled={!machine.isPhysical || machine.refreshing} onClick={onCancelOperation}>Cancelar reposicionamiento</button></div>
          <button className="button" type="button" disabled={!canEnableJog || referenceBusy || machine.refreshing} onClick={onEnableManual}>Habilitar joystick X/Y</button>
        </article>

        <article className="panel">
          <div className="section-heading"><h3>Posición segura de cambio de herramienta</h3></div>
          <p className="muted">Estas son coordenadas de máquina, no el origen X0/Y0 de FlatCAM ni la referencia Z de la PCB.</p>
          <div className="info-grid info-grid--double compact-grid">
            <div className="metric-box"><span>X cambio</span><strong>{formatMillimeters(toolChangeX, 3)}</strong></div>
            <div className="metric-box"><span>Y cambio</span><strong>{formatMillimeters(toolChangeY, 3)}</strong></div>
            <div className="metric-box"><span>Z cambio</span><strong>{formatMillimeters(toolChangeZ, 3)}</strong></div>
            <div className="metric-box"><span>Velocidad Z cambio</span><strong>{toolChangeZFeed.toFixed(0)} mm/min · {(toolChangeZFeed / 60).toFixed(3)} mm/s</strong></div>
            <div className="metric-box"><span>Orden</span><strong>Z primero, luego X/Y</strong></div>
          </div>
          <div className="action-grid action-grid--inline">
            <button className="button button--ghost" type="button" disabled={!machine.isPhysical || referenceBusy || machine.refreshing || !machine.homedAxes} onClick={onToolChangePosition}>Reintentar movimiento</button>
            <button className="button button--ghost" type="button" disabled={!machine.isPhysical || machine.refreshing} onClick={onCancelOperation}>Cancelar intento</button>
            <button className="button button--ghost" type="button" disabled={!machine.isPhysical || referenceBusy || machine.refreshing} onClick={() => window.alert("Modifique TOOL_CHANGE_X_MM, TOOL_CHANGE_Y_MM y TOOL_CHANGE_Z_MM en la configuración del servicio y reinicie la aplicación para persistir los cambios.")}>Modificar posición de cambio</button>
            <button className="button" type="button" disabled={!machine.isPhysical || referenceBusy || machine.refreshing || !machine.homedAxes} onClick={onToolChangePosition}>Ir a posición de cambio</button>
          </div>
        </article>

        <article className="panel">
          <div className="section-heading"><h3>4. Medir referencia</h3></div>
          <div className="form-grid form-grid--dense">
            <label>Paso de sonda (mm)<input value={machineSettingsInput.reference_probe_step_mm} inputMode="decimal" disabled={referenceBusy || machine.runtimeState === "PROBING_REFERENCE"} onChange={(event) => onMachineSettingChange("reference_probe_step_mm", event.target.value)} /></label>
            <label>Velocidad de sonda (mm/min)<input value={machineSettingsInput.reference_probe_feed_mm_min} inputMode="decimal" disabled={referenceBusy || machine.runtimeState === "PROBING_REFERENCE"} onChange={(event) => onMachineSettingChange("reference_probe_feed_mm_min", event.target.value)} /></label>
            <label>Retracto tras contacto (mm)<input value={machineSettingsInput.reference_probe_retract_mm} inputMode="decimal" disabled={referenceBusy || machine.runtimeState === "PROBING_REFERENCE"} onChange={(event) => onMachineSettingChange("reference_probe_retract_mm", event.target.value)} /></label>
            <label>Velocidad de retracto (mm/min)<input value={machineSettingsInput.reference_probe_retract_feed_mm_min} inputMode="decimal" disabled={referenceBusy || machine.runtimeState === "PROBING_REFERENCE"} onChange={(event) => onMachineSettingChange("reference_probe_retract_feed_mm_min", event.target.value)} /></label>
          </div>
          <p className="muted">Velocidad efectiva: {Number.isFinite(referenceProbeFeed) ? `${referenceProbeFeed.toFixed(2)} mm/min · ${(referenceProbeFeed / 60).toFixed(3)} mm/s` : "valor inválido"}</p>
          <button className="button button--ghost" type="button" disabled={!machine.isPhysical || referenceBusy || machine.refreshing || machine.runtimeState === "PROBING_REFERENCE"} onClick={onSaveMachineSettings}>Guardar parámetros de sonda</button>
          <p className="muted">Puede armar la referencia para usar el botón externo o lanzar el sondeo directamente desde pantalla. Si la primera toma quedó mal, use "Volver a medir referencia" para repetir el mismo flujo seguro y sobrescribir la referencia Z activa con una nueva captura física.</p>
          <div className="action-grid action-grid--inline">
            <button className="button button--ghost" type="button" disabled={!canArm || referenceBusy || machine.refreshing} onClick={onProbeRequest}>Armar referencia</button>
            <button className="button" type="button" disabled={!canProbe || referenceBusy || machine.refreshing || !selectedOperation} onClick={onRemeasurePhysicalReference}>Sondear referencia ahora</button>
            <button className="button button--ghost" type="button" disabled={!canProbe || referenceBusy || machine.refreshing || !selectedOperation} onClick={onRemeasurePhysicalReference}>Volver a medir referencia</button>
            <button className="button button--ghost" type="button" disabled={!canGoToReference || referenceBusy || machine.refreshing} onClick={onGoToReferencePoint}>{referenceBusy ? "Yendo al punto de referencia…" : "Ir al punto de referencia"}</button>
            <button className="button button--ghost" type="button" disabled={!machine.isPhysical || machine.refreshing} onClick={onCancelOperation}>Cancelar</button>
          </div>
          {referenceMoveResult ? <div className="alert alert--success"><strong>{referenceMoveResult.message}</strong><br />CNC X: {formatMillimeters(referenceMoveResult.reference_x, 3)} · CNC Y: {formatMillimeters(referenceMoveResult.reference_y, 3)} · Z segura: {formatMillimeters(referenceMoveResult.preparation_z, 3)} · {referenceMoveResult.final_state}</div> : null}
          <div className="info-grid info-grid--double compact-grid">
            <div className="metric-box"><span>Origen X/Y</span><strong>{referenceSession?.origen_trabajo ? `${referenceSession.origen_trabajo.x_mm}, ${referenceSession.origen_trabajo.y_mm}` : "pendiente"}</strong></div>
            <div className="metric-box"><span>Captura origen</span><strong>{formatCapturedPosition(referenceSession?.origen_trabajo?.posicion_captura)}</strong></div>
            <div className="metric-box"><span>Referencia Z</span><strong>{referenceSession?.referencia_z?.z_mm ?? "pendiente"}</strong></div>
            <div className="metric-box"><span>Captura referencia</span><strong>{formatCapturedPosition(referenceSession?.referencia_z?.posicion_captura)}</strong></div>
            <div className="metric-box"><span>Herramienta</span><strong>{selectedOperation?.herramienta ?? selectedOperation?.tool_id ?? "sin herramienta"}</strong></div>
            <div className="metric-box"><span>Fuente</span><strong>{String(referenceSession?.referencia_z?.fuente ?? "-")}</strong></div>
          </div>
        </article>
      </div>
    );
  }

  return (
    <div className="stack gap-md">
      <article className="panel">
        <div className="section-heading section-heading--stacked">
          <div>
            <p className="eyebrow">Referencia simulada</p>
            <h3>Flujo simulado de preparación</h3>
          </div>
          <p className="muted">Modo SIMULADO: confirma referencias manuales sin abrir hardware ni enviar movimientos.</p>
        </div>
        <div className="machine-banner machine-banner--large" role="status">
          <span className="machine-banner__dot" aria-hidden="true" />
          <span>MODO SIMULADO - no se enviará movimiento a la máquina</span>
        </div>
        {referenceSession?.motivo_invalidacion ? <div className="alert alert--warning">{referenceSession.motivo_invalidacion}</div> : null}
        <div className="workflow-steps-grid">
          {(referenceSession?.pasos ?? []).map(renderReferenceStep)}
        </div>
      </article>

      <article className="panel">
        <div className="section-heading"><h3>1. Referencia de máquina</h3></div>
        <p className="muted">Ubica el sistema de coordenadas de la máquina. En simulación se confirma una vez por sesión.</p>
        <button className="button" type="button" disabled={referenceBusy || referenceSession?.machine_reference.confirmada || !selectedOperation} onClick={onConfirmMachineReference}>
          {referenceSession?.machine_reference.confirmada ? "Ya confirmada en simulación" : "Confirmar en simulación"}
        </button>
      </article>

      <article className="panel">
        <div className="section-heading"><h3>2. Origen de trabajo X/Y</h3></div>
        <p className="muted">Define dónde queda X0 Y0 del G-code respecto al montaje de la placa.</p>
        <div className="form-grid">
          <label>X (mm)<input ref={(node) => { workOriginRefs.current.x_mm = node; }} type="number" inputMode="decimal" value={workOrigin.x_mm} onChange={(event) => onWorkOriginChange("x_mm", event.target.value)} />{workOriginErrors.x_mm ? <span className="form-error">{workOriginErrors.x_mm}</span> : null}</label>
          <label>Y (mm)<input ref={(node) => { workOriginRefs.current.y_mm = node; }} type="number" inputMode="decimal" value={workOrigin.y_mm} onChange={(event) => onWorkOriginChange("y_mm", event.target.value)} />{workOriginErrors.y_mm ? <span className="form-error">{workOriginErrors.y_mm}</span> : null}</label>
        </div>
        <button className="button" type="button" disabled={referenceBusy || !selectedOperation} onClick={onSubmitWorkOrigin}>Confirmar en simulación</button>
      </article>

      <article className="panel">
        <div className="section-heading"><h3>3. Referencia Z</h3></div>
        <p className="muted">Define la altura que se considera Z0 para esta herramienta en simulación.</p>
        <label className="toggle-field"><input type="checkbox" checked={useWorkOriginXYForZ} onChange={(event) => onToggleUseWorkOriginXYForZ(event.target.checked)} /><span>Usar la misma posición X/Y del origen de trabajo</span></label>
        <div className="form-grid">
          <label>X (mm)<input ref={(node) => { zReferenceRefs.current.x_mm = node; }} type="number" inputMode="decimal" value={useWorkOriginXYForZ ? workOrigin.x_mm : zReference.x_mm} disabled={useWorkOriginXYForZ} onChange={(event) => onZReferenceChange("x_mm", event.target.value)} />{zReferenceErrors.x_mm ? <span className="form-error">{zReferenceErrors.x_mm}</span> : null}</label>
          <label>Y (mm)<input ref={(node) => { zReferenceRefs.current.y_mm = node; }} type="number" inputMode="decimal" value={useWorkOriginXYForZ ? workOrigin.y_mm : zReference.y_mm} disabled={useWorkOriginXYForZ} onChange={(event) => onZReferenceChange("y_mm", event.target.value)} />{zReferenceErrors.y_mm ? <span className="form-error">{zReferenceErrors.y_mm}</span> : null}</label>
          <label>Z de referencia (mm)<input ref={(node) => { zReferenceRefs.current.z_mm = node; }} type="number" inputMode="decimal" value={zReference.z_mm} onChange={(event) => onZReferenceChange("z_mm", event.target.value)} />{zReferenceErrors.z_mm ? <span className="form-error">{zReferenceErrors.z_mm}</span> : null}</label>
        </div>
        <button className="button" type="button" disabled={referenceBusy || !selectedOperation} onClick={onSubmitZReference}>Confirmar en simulación</button>
      </article>

      <article className="panel"><div className="section-heading"><h3>4. Región sondeable</h3></div><p className="muted">La región sondeable se configura desde la pestaña Mapa de alturas.</p>{heightMap ? <p className="mono-text">{JSON.stringify(heightMap.probe_region)}</p> : <p className="muted">Aún no hay región configurada.</p>}</article>
      <article className="panel"><div className="section-heading"><h3>5. Mapa</h3></div><p className="muted">{referenceSession?.pasos.find((step) => step.id === "mapa")?.detalle ?? "Aún no hay mapa disponible."}</p><p className="mono-text">Mapa actual: {heightMap ? `${heightMap.fuente_datos} · v${heightMap.version}` : "no disponible"}</p></article>
      <article className="panel"><div className="section-heading"><h3>6. Validación</h3></div><p className="muted">{referenceSession?.pasos.find((step) => step.id === "validacion")?.detalle ?? "La validación del mapa sigue pendiente."}</p><button className="button" type="button" disabled={referenceBusy || !selectedOperation || !heightMap} onClick={onValidateHeightMap}>Confirmar en simulación</button></article>
    </div>
  );
}
