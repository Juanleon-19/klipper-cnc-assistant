import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { MachineContext, type MachineContextValue } from "../context/MachineContext";
import { ApiError } from "../lib/api";
import type { HeightMap, Project, ReferenceSession } from "../types";
import { ProjectWorkspace } from "./ProjectWorkspace";

const apiMock = vi.hoisted(() => ({
  getHeightMap: vi.fn(),
  getReferenceSession: vi.fn(),
  updateHeightMapSample: vi.fn(),
  configureHeightMap: vi.fn(),
  simulateHeightMap: vi.fn(),
  importHeightMapJson: vi.fn(),
  importHeightMapCsv: vi.fn(),
  recalculateHeightMap: vi.fn(),
  deleteHeightMap: vi.fn(),
  confirmMachineReference: vi.fn(),
  confirmWorkOrigin: vi.fn(),
  confirmZReference: vi.fn(),
  capturePhysicalWorkOrigin: vi.fn(),
  capturePhysicalZReferenceFromProbe: vi.fn(),
  validateHeightMap: vi.fn(),
  getCompensationPreview: vi.fn(),
  getPhysicalMap: vi.fn(),
  getPhysicalHeightMap: vi.fn(),
  getPhysicalMapHistory: vi.fn(),
  repeatPhysicalMap: vi.fn(),
  previewPhysicalMap: vi.fn(),
  planPhysicalMapFromReference: vi.fn(),
  executeNextPhysicalMapPoint: vi.fn(),
  executeAllPhysicalMapPoints: vi.fn(),
  suggestPhysicalMap: vi.fn(),
  resetSetupReference: vi.fn(),
  resetSetupMap: vi.fn(),
  resetSetupPreparation: vi.fn(),
  pausePhysicalMap: vi.fn(),
  resumePhysicalMap: vi.fn(),
  cancelPhysicalMap: vi.fn(),
  generateCompensatedGCode: vi.fn(),
  executionPreflight: vi.fn(),
  executionAction: vi.fn(),
  generatedFileUrl: vi.fn(),
  getMachineSettings: vi.fn(),
  updateMachineSettings: vi.fn(),
  confirmProbe: vi.fn(),
}));

vi.mock("../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../lib/api")>("../lib/api");
  return {
    ...actual,
    api: apiMock,
  };
});

vi.mock("../features/viewer/ToolpathViewer", () => ({
  ToolpathViewer: () => <div>ToolpathViewer mock</div>,
}));

vi.mock("../features/heightmap/HeightMapHeatmap", () => ({
  HeightMapHeatmap: (props: { meshPoints?: unknown[]; heightMap?: unknown }) => <div>Heatmap mock · {props.meshPoints?.length ?? 0} puntos · {props.heightMap ? "medido" : "preview"}</div>,
}));

vi.mock("../features/heightmap/HeightMapSurface3D", () => ({
  HeightMapSurface3D: () => <div>Surface 3D mock</div>,
}));

const referenceSession: ReferenceSession = {
  estado: "mapa_validado",
  machine_reference: { confirmada: true, fecha: new Date().toISOString() },
  origen_maquina: { x_mm: 0, y_mm: 0, z_mm: 0 },
  origen_material: { x_mm: 0, y_mm: 0, z_mm: 0 },
  origen_gcode: { x_mm: 0, y_mm: 0, z_mm: 0 },
  origen_trabajo: { x_mm: 0, y_mm: 0, z_mm: null, fecha: new Date().toISOString(), fuente: "MEASURED", maquina: "klipper", homed_axes: "xyz", posicion_captura: { x_mm: 0, y_mm: 0, z_mm: null }, sesion: "test" },
  referencia_z: { x_mm: 10, y_mm: 8, z_mm: 0, fecha: new Date().toISOString(), fuente: "MEASURED", maquina: "klipper", homed_axes: "xyz", posicion_captura: { x_mm: 10, y_mm: 8, z_mm: 0 }, sesion: "test" },
  pasos: [
    { id: "referencia_maquina", titulo: "Referencia de maquina", estado: "confirmado", confirmado: true, fecha: new Date().toISOString(), detalle: "Pertenece a la sesión general de máquina." },
    { id: "origen_xy", titulo: "Origen de trabajo X/Y", estado: "confirmado", confirmado: true, fecha: new Date().toISOString(), detalle: "Define dónde queda X0 Y0 del G-code respecto al montaje." },
    { id: "referencia_z", titulo: "Referencia Z", estado: "confirmado", confirmado: true, fecha: new Date().toISOString(), detalle: "Referencia vertical del montaje." },
    { id: "region_sondeable", titulo: "Region sondeable", estado: "confirmado", confirmado: true, fecha: new Date().toISOString(), detalle: "Dominio medido del mapa." },
    { id: "mapa", titulo: "Mapa", estado: "confirmado", confirmado: true, fecha: new Date().toISOString(), detalle: "Mapa disponible y validado." },
    { id: "validacion", titulo: "Validacion", estado: "confirmado", confirmado: true, fecha: new Date().toISOString(), detalle: "Preparación lista para compensación matemática." },
  ],
  compensacion_previsualizada_en: null,
  analysis_stale: false,
  lista_para_compensacion: true,
  bloqueos_compensacion: [],
  motivo_invalidacion: null,
};

const heightMap: HeightMap = {
  proyecto_id: "proj_1",
  operacion_id: "op_1",
  version: 1,
  version_algoritmo: "heightmap-v2",
  estado: "datos simulados",
  fuente_datos: "simulado",
  superficie_simulada: "inclinada",
  repeticion_simulacion: 3,
  etiqueta_simulada: true,
  grid: { filas: 2, columnas: 2, ancho_mm: 60, alto_mm: 44, paso_x_mm: 60, paso_y_mm: 44 },
  probe_region: { min_x_mm: 10, min_y_mm: 8, max_x_mm: 70, max_y_mm: 52 },
  exclusion_zones: [],
  muestras: [],
  estadisticas: {
    cantidad_puntos: 4,
    cantidad_puntos_incluidos: 4,
    cantidad_puntos_faltantes: 0,
    cantidad_puntos_atipicos: 0,
    altura_min_mm: -0.01,
    altura_max_mm: 0.02,
    rango_alturas_mm: 0.03,
    valor_referencia_mm: 0,
    desviacion_rms_respecto_plano_mm: 0.001,
    residuo_maximo_mm: 0.001,
    ancho_cubierto_mm: 60,
    alto_cubierto_mm: 44,
  },
  plano: {
    a: 0.0002,
    b: -0.0001,
    c: 0,
    inclinacion_x_mm_por_mm: 0.0002,
    inclinacion_y_mm_por_mm: -0.0001,
    rms_residuos_mm: 0.001,
    residuo_maximo_mm: 0.001,
    residuo_minimo_mm: -0.001,
  },
  superficies: {
    bruto: { filas: 2, columnas: 2, modo: "bruto", puntos: [] },
    plano: { filas: 2, columnas: 2, modo: "plano", puntos: [] },
    residuo: { filas: 2, columnas: 2, modo: "residuo", puntos: [] },
  },
  creado_en: new Date().toISOString(),
  actualizado_en: new Date().toISOString(),
};

const project: Project = {
  id: "proj_1",
  nombre: "Proyecto de prueba",
  material: { ancho_mm: 80, alto_mm: 60, espesor_mm: 1.6 },
  doble_cara: false,
  eje_volteo: null,
  agujeros_alineacion: [],
  montajes: [{ id: "setup-main", nombre: "Montaje principal", orden: 0 }],
  operaciones: [
    {
      id: "op_1",
      nombre: "Fresado superior",
      tipo: "aislamiento",
      cara: "superior",
      orden: 0,
      setup_id: "setup-main",
      archivo_gcode: "originals/job.nc",
      nombre_archivo_original: "job.nc",
      tamano_archivo_bytes: 120,
      sha256: "abc",
      tool_id: null,
      herramienta: "V-bit 30",
      estado: "valida",
      analisis: {
        analysis_version: "gcode-analysis-v2",
        current_analysis_version: "gcode-analysis-v2",
        analisis_desactualizado: false,
        limites: { min_x_mm: 0, max_x_mm: 40, min_y_mm: 0, max_y_mm: 25, min_z_mm: -0.1, max_z_mm: 0, ancho_mm: 40, alto_mm: 25 },
        avances_mm_min: [120],
        profundidad_min_mm: -0.1,
        profundidad_max_mm: 0,
        cantidad_movimientos: 2,
        comandos_desconocidos: [],
        comandos_no_compatibles: [],
        acciones_husillo: [],
        cambios_herramienta: [],
        comandos_manuales: [],
        unidades_detectadas: ["mm"],
        modos_posicionamiento: ["absolute"],
        incidencias: [],
        analisis_incompleto: false,
        soporte_geometrico_incompleto: false,
        cabe_en_material: true,
        mensaje_material: "ok",
        tiene_errores_criticos: false,
        segmentos_lineales: [],
        segmentos_vista_previa: [],
        desbordes_material: [],
        tolerancia_arco_mm: 0.05,
      },
    },
  ],
  creado_en: new Date().toISOString(),
  actualizado_en: new Date().toISOString(),
  version_esquema: "1.3",
  estado_general: "valido",
};

const physicalMachine: MachineContextValue = {
  runtime: {
    state: "WAITING_FOR_XY_REFERENCE",
    mode: "PHYSICAL",
    mode_label: "FÍSICO",
    moonraker: { http_connected: true, websocket_connected: true },
    klipper: { ready: true, homed_axes: "xyz", position: { x: 60, y: 88.75, z: 10.05 } },
    preparation: {
      reference_prep_z_mm: 115,
      reference_prep_z_feed_mm_min: 120,
      reference_prep_z_speed_mm_s: 2,
      center_x_mm: 110,
      center_y_mm: 110,
      target: { x_mm: 110, y_mm: 110, z_mm: 115 },
      sequence: ["HOME", "MOVE_Z_PREP", "MOVE_XY_CENTER", "WAITING_FOR_REFERENCE"],
    },
    tool_change: { x_mm: 0, y_mm: 0, z_mm: 115, z_feed_mm_min: 180, z_speed_mm_s: 3 },
    arduino: { port: "/dev/ttyUSB0", valid_packets: 12 },
    controller: { direction: "CENTER", jog_mode: "FINE", external_button: false, probe: false },
    safety: { serial_recent: true, telemetry_recent: true, movement_authorized: false },
    health: "ok",
    started_at: new Date().toISOString(),
    application: {},
    last_command: null,
    last_movement: null,
    last_error: null,
    last_probe_result: null,
    initialization_steps: [],
    events: [],
  },
  refreshing: false,
  isPhysical: true,
  modeLabel: "FÍSICO",
  runtimeState: "WAITING_FOR_XY_REFERENCE",
  connected: true,
  homedAxes: "xyz",
  klipperReady: true,
  serialRecent: true,
  telemetryRecent: true,
  movementAuthorized: false,
  lastError: null,
  runMachineAction: vi.fn(),
  refreshRuntime: vi.fn(),
};

function renderWorkspace(machine?: MachineContextValue, options?: { onRefreshProject?: () => Promise<void> }) {
  const workspace = (
    <ProjectWorkspace
      project={project}
      busyKey={null}
      savingProject={false}
      onSaveProject={vi.fn()}
      onAddSetup={vi.fn()}
      onAddOperation={vi.fn()}
      onUpdateOperation={vi.fn()}
      onDuplicateOperation={vi.fn()}
      onMoveOperation={vi.fn()}
      onDeleteOperation={vi.fn()}
      onRemoveFile={vi.fn()}
      onAnalyze={vi.fn()}
      onUploadFile={vi.fn()}
      onRefreshProject={options?.onRefreshProject}
    />
  );
  return render(machine ? <MachineContext.Provider value={machine}>{workspace}</MachineContext.Provider> : workspace);
}

describe("ProjectWorkspace", () => {
  beforeEach(() => {
    Object.values(apiMock).forEach((fn) => fn.mockReset());
    apiMock.getMachineSettings.mockResolvedValue({ reference_prep_z_mm: 115, reference_prep_z_feed_mm_min: 180, move_total_timeout_s: 180, no_progress_timeout_s: 60, position_tolerance_mm: 0.05, velocity_tolerance_mm_s: 0.02 });
    apiMock.updateMachineSettings.mockResolvedValue({ reference_prep_z_mm: 115, reference_prep_z_feed_mm_min: 180, move_total_timeout_s: 180, no_progress_timeout_s: 60, position_tolerance_mm: 0.05, velocity_tolerance_mm_s: 0.02 });
    apiMock.confirmProbe.mockResolvedValue(physicalMachine.runtime);
    apiMock.getHeightMap.mockResolvedValue(heightMap);
    apiMock.getReferenceSession.mockResolvedValue(referenceSession);
    apiMock.getPhysicalMap.mockRejectedValue(new Error("No existe mapa físico medido para este montaje y cara."));
    apiMock.getPhysicalHeightMap.mockResolvedValue(heightMap);
    apiMock.getPhysicalMapHistory.mockResolvedValue([]);
    apiMock.planPhysicalMapFromReference.mockResolvedValue({
      payload: {
        map_id: "measured/manual-2x2",
        status: "MESH_PLANNED",
        source: "MEASURED",
        point_count: 4,
        grid_mode: "manual",
        rows: 2,
        columns: 2,
        dx: 76,
        dy: 56,
        grid: { rows: 2, columns: 2, dx_mm: 76, dy_mm: 56 },
        local_region: { min_x_mm: 2, min_y_mm: 2, max_x_mm: 78, max_y_mm: 58 },
        machine_region: { min_x_mm: 62, min_y_mm: 90.75, max_x_mm: 138, max_y_mm: 146.75 },
        points: [
          { index: 0, row: 0, column: 0, x_local: 2, y_local: 2, x_machine: 62, y_machine: 90.75, status: "PENDING" },
          { index: 1, row: 0, column: 1, x_local: 78, y_local: 2, x_machine: 138, y_machine: 90.75, status: "PENDING" },
          { index: 2, row: 1, column: 1, x_local: 78, y_local: 58, x_machine: 138, y_machine: 146.75, status: "PENDING" },
          { index: 3, row: 1, column: 0, x_local: 2, y_local: 58, x_machine: 62, y_machine: 146.75, status: "PENDING" },
        ],
      },
    });
    apiMock.previewPhysicalMap.mockResolvedValue({
      payload: {
        preview_id: "preview/manual-2x2",
        status: "MESH_PREVIEW",
        source: "PREVIEW",
        point_count: 4,
        grid_mode: "manual",
        rows: 2,
        columns: 2,
        dx: 76,
        dy: 56,
        grid: { rows: 2, columns: 2, dx_mm: 76, dy_mm: 56 },
        local_region: { min_x_mm: 2, min_y_mm: 2, max_x_mm: 78, max_y_mm: 58 },
        points: [
          { index: 0, row: 0, column: 0, x_local: 2, y_local: 2, x_machine: null, y_machine: null, status: "PENDING" },
          { index: 1, row: 0, column: 1, x_local: 78, y_local: 2, x_machine: null, y_machine: null, status: "PENDING" },
          { index: 2, row: 1, column: 1, x_local: 78, y_local: 58, x_machine: null, y_machine: null, status: "PENDING" },
          { index: 3, row: 1, column: 0, x_local: 2, y_local: 58, x_machine: null, y_machine: null, status: "PENDING" },
        ],
        warnings: ["Vista previa en coordenadas PCB. Complete la referencia para calcular las coordenadas CNC."],
      },
    });
    apiMock.suggestPhysicalMap.mockResolvedValue({
      grid_mode: "suggested",
      rows: 3,
      columns: 4,
      point_count: 12,
      excluded_count: 0,
      executable_count: 12,
      dx_mm: 25.3333333333,
      dy_mm: 28,
      estimated_distance_mm: 240,
      estimated_time_s: 48,
      reason: "Se ajusta a la separación objetivo dentro de la región sondeable.",
      local_region: { min_x_mm: 2, min_y_mm: 2, max_x_mm: 78, max_y_mm: 58 },
    });
    apiMock.resetSetupReference.mockResolvedValue({ ...project.montajes[0], preparation_status: "sin_iniciar" });
    apiMock.resetSetupMap.mockResolvedValue({ ...project.montajes[0], active_map_id: null, preparation_status: "referencia_lista" });
    apiMock.resetSetupPreparation.mockResolvedValue({ ...project.montajes[0], placement_revision: "placement-2", preparation_status: "sin_iniciar" });
    apiMock.generatedFileUrl.mockImplementation((_projectId: string, relativePath: string) => `/api/projects/proj_1/generated/${relativePath}`);
    apiMock.confirmWorkOrigin.mockResolvedValue(referenceSession);
    apiMock.confirmZReference.mockResolvedValue(referenceSession);
    apiMock.capturePhysicalWorkOrigin.mockResolvedValue(referenceSession);
    apiMock.capturePhysicalZReferenceFromProbe.mockResolvedValue(referenceSession);
    apiMock.executionPreflight.mockResolvedValue({ state: "PREFLIGHT", ready: false, checks: [], generated_file: null, detail: "Preflight incompleto." });
    apiMock.executionAction.mockResolvedValue({ state: "PREFLIGHT", ready: false, checks: [], generated_file: null, detail: "Acción registrada." });
    apiMock.getCompensationPreview.mockResolvedValue({
      session: referenceSession,
      preview: {
        convencion_matematica: "z_compensada = z_original + (superficie_xy - z_referencia).",
        z_referencia_mm: 0,
        paso_muestreo_virtual_mm: 1,
        puntos_fuera_dominio: 0,
        puntos_virtuales_agregados: 0,
        resumen_z_original: { min_mm: -0.1, max_mm: 0 },
        resumen_z_compensada: { min_mm: -0.09, max_mm: 0.01 },
        segmentos: [],
      },
    });
    window.localStorage.clear();
  });

  it("navega entre vistas principales y subpestañas del mapa", async () => {
    renderWorkspace();

    await waitFor(() => expect(apiMock.getReferenceSession).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: /^Referencia$/i }));
    expect(await screen.findByText(/Flujo simulado de preparación/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Mapa de alturas/i }));
    fireEvent.click(screen.getByRole("tab", { name: /Configuración/i }));
    expect(screen.getAllByText(/Alturas de la superficie/i).length).toBeGreaterThan(0);
  });

  it("lee y muestra la posición capturada estructurada de la referencia física", async () => {
    renderWorkspace(physicalMachine);

    fireEvent.click(screen.getByRole("button", { name: /^Referencia$/i }));

    expect(await screen.findByText(/Referencia X\/Y\/Z medida/i)).toBeInTheDocument();
    expect(screen.queryByText(/Flujo simulado de preparación/i)).toBeNull();
    expect(screen.getByText(/Captura referencia/i)).toBeInTheDocument();
    expect(screen.getByText(/X 10.000 mm · Y 8.000 mm · Z 0.000 mm/i)).toBeInTheDocument();
  });

  it("muestra Z de preparación, centro y posición de cambio en referencia física", async () => {
    vi.mocked(physicalMachine.runMachineAction).mockClear();
    renderWorkspace(physicalMachine);

    fireEvent.click(screen.getByRole("button", { name: /^Referencia$/i }));

    expect(await screen.findByText(/Home, Z de preparación y centro/i)).toBeInTheDocument();
    expect(screen.getAllByText(/Z de preparación/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/X 110.000 mm · Y 110.000 mm/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/180 mm\/min · 3.000 mm\/s/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/1800 mm\/min · 30.000 mm\/s/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/180 mm\/min · 3.000 mm\/s/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/Posición segura de cambio de herramienta/i)).toBeInTheDocument();
    expect(screen.getByText(/Z primero, luego X\/Y/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Ir a posición de cambio/i }));

    await waitFor(() => expect(physicalMachine.runMachineAction).toHaveBeenCalledWith("tool-change-position"));
  });

  it("permite sondear referencia ahora directamente desde pantalla", async () => {
    vi.mocked(physicalMachine.refreshRuntime).mockClear();
    renderWorkspace(physicalMachine);

    fireEvent.click(screen.getByRole("button", { name: /^Referencia$/i }));
    const probeButton = await screen.findByRole("button", { name: /Sondear referencia ahora/i });
    expect(probeButton).toBeEnabled();

    fireEvent.click(probeButton);

    await waitFor(() => expect(apiMock.confirmProbe).toHaveBeenCalled());
    await waitFor(() => expect(physicalMachine.refreshRuntime).toHaveBeenCalled());
    await waitFor(() => expect(apiMock.capturePhysicalWorkOrigin).toHaveBeenCalledWith(project.id, project.operaciones[0].id));
    await waitFor(() => expect(apiMock.capturePhysicalZReferenceFromProbe).toHaveBeenCalledWith(project.id, project.operaciones[0].id));
  });

  it("no persiste origen ni referencia Z si probe-confirm falla", async () => {
    apiMock.confirmProbe.mockRejectedValueOnce(new Error("Timeout esperando confirmación de paso de sonda."));
    vi.mocked(physicalMachine.refreshRuntime).mockClear();
    renderWorkspace(physicalMachine);

    fireEvent.click(screen.getByRole("button", { name: /^Referencia$/i }));
    fireEvent.click(await screen.findByRole("button", { name: /Sondear referencia ahora/i }));

    await waitFor(() => expect(apiMock.confirmProbe).toHaveBeenCalled());
    expect(apiMock.capturePhysicalWorkOrigin).not.toHaveBeenCalled();
    expect(apiMock.capturePhysicalZReferenceFromProbe).not.toHaveBeenCalled();
    expect(physicalMachine.refreshRuntime).not.toHaveBeenCalled();
    expect(await screen.findByText(/Timeout esperando confirmación de paso de sonda/i)).toBeInTheDocument();
  });

  it("reiniciar proceso refresca el proyecto activo y el runtime", async () => {
    const onRefreshProject = vi.fn().mockResolvedValue(undefined);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    vi.mocked(physicalMachine.refreshRuntime).mockClear();
    renderWorkspace(physicalMachine, { onRefreshProject });

    fireEvent.click(screen.getByText(/Más acciones/i));
    fireEvent.click(await screen.findByRole("button", { name: /Reiniciar proceso/i }));

    await waitFor(() => expect(apiMock.resetSetupPreparation).toHaveBeenCalledWith(project.id, project.montajes[0].id));
    await waitFor(() => expect(onRefreshProject).toHaveBeenCalled());
    await waitFor(() => expect(physicalMachine.refreshRuntime).toHaveBeenCalled());
  });

  it("permite guardar parámetros avanzados de preparación física", async () => {
    vi.mocked(physicalMachine.refreshRuntime).mockClear();
    renderWorkspace(physicalMachine);

    fireEvent.click(screen.getByRole("button", { name: /^Referencia$/i }));
    expect(await screen.findByText(/Configuración avanzada de movimiento/i)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/Velocidad Z de preparación/i), { target: { value: "90" } });
    fireEvent.click(screen.getByRole("button", { name: /Guardar configuración/i }));

    await waitFor(() => expect(apiMock.updateMachineSettings).toHaveBeenCalledWith(expect.objectContaining({
      reference_prep_z_mm: 115,
      reference_prep_z_feed_mm_min: 90,
      move_total_timeout_s: 180,
      no_progress_timeout_s: 60,
      position_tolerance_mm: 0.05,
      velocity_tolerance_mm_s: 0.02,
    })));
    expect(physicalMachine.refreshRuntime).toHaveBeenCalled();
  });

  it("muestra mapa físico como flujo principal sin botón operativo SIMULADO", async () => {
    apiMock.getPhysicalMap.mockResolvedValueOnce({
      payload: {
        map_id: "measured/test",
        status: "MESH_PLANNED",
        source: "MEASURED",
        point_count: 2,
        grid: { rows: 1, columns: 2, dx_mm: 5, dy_mm: 0 },
        local_region: { min_x_mm: 0, min_y_mm: 0, max_x_mm: 5, max_y_mm: 0 },
        machine_region: { min_x_mm: 60, min_y_mm: 88.75, max_x_mm: 65, max_y_mm: 88.75 },
        points: [
          { index: 0, row: 0, column: 0, x_local: 0, y_local: 0, x_machine: 60, y_machine: 88.75, status: "PENDING" },
          { index: 1, row: 0, column: 1, x_local: 5, y_local: 0, x_machine: 65, y_machine: 88.75, status: "PENDING" },
        ],
      },
    });
    renderWorkspace(physicalMachine);

    fireEvent.click(screen.getByRole("button", { name: /Mapa de alturas/i }));

    expect(await screen.findByText(/Mapa medido físicamente/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^SIMULADO$/i })).toBeNull();
    const previewButton = screen.getByRole("button", { name: /^1\. Generar vista previa de malla$/i });
    const validateButton = screen.getByRole("button", { name: /^2\. Validar límites$/i });
    const armButton = screen.getByRole("button", { name: /^3\. Armar sondeo$/i });
    const startButton = screen.getByRole("button", { name: /^4\. Iniciar sondeo automático$/i });
    expect(previewButton).toBeInTheDocument();
    expect(validateButton).toBeInTheDocument();
    expect(armButton).toBeInTheDocument();
    expect(startButton).toBeInTheDocument();
    expect(previewButton).not.toHaveClass("button--ghost");
    expect(validateButton).not.toHaveClass("button--ghost");
    expect(armButton).not.toHaveClass("button--ghost");
    expect(startButton).not.toHaveClass("button--ghost");
    expect(screen.getByRole("button", { name: /^Pausar$/i })).toHaveClass("button--ghost");
    expect(screen.getByRole("button", { name: /^Cancelar$/i })).toHaveClass("button--ghost");
  });



  it("permite configurar malla manual 2x2 y previsualiza exactamente cuatro puntos interiores", async () => {
    renderWorkspace(physicalMachine);
    fireEvent.click(screen.getByRole("button", { name: /Mapa de alturas/i }));
    expect(await screen.findByText(/Mapa medido físicamente/i)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/^Filas$/i), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText(/^Columnas$/i), { target: { value: "2" } });
    expect(screen.getAllByText(/Separación X/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText("4").length).toBeGreaterThan(0);

    fireEvent.click(screen.getByRole("button", { name: /Generar vista previa de malla/i }));

    await waitFor(() => expect(apiMock.planPhysicalMapFromReference).toHaveBeenCalledWith(
      "proj_1",
      "op_1",
      expect.objectContaining({ grid_mode: "manual", rows: 2, columns: 2, edge_margin_left_mm: 2, edge_margin_right_mm: 2 })
    ));
    expect(await screen.findByText(/Vista previa generada/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: /Puntos/i }));
    expect(await screen.findByText(/Punto #1/i)).toBeInTheDocument();
    expect(screen.getByText(/Punto #4/i)).toBeInTheDocument();
    expect(screen.queryByText(/Punto #5/i)).toBeNull();
    expect(screen.getByText(/PCB X\/Y: 2.000 mm \/ 2.000 mm/i)).toBeInTheDocument();
    expect(screen.getByText(/PCB X\/Y: 78.000 mm \/ 58.000 mm/i)).toBeInTheDocument();
  });

  it("genera preview local sin referencia fisica y actualiza el visor 2D", async () => {
    const noReferenceSession = { ...referenceSession, origen_trabajo: null, referencia_z: null, lista_para_compensacion: false };
    apiMock.getReferenceSession.mockResolvedValue(noReferenceSession);
    renderWorkspace(physicalMachine);
    fireEvent.click(screen.getByRole("button", { name: /Mapa de alturas/i }));
    expect(await screen.findByText(/Mapa medido físicamente/i)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/^Filas$/i), { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText(/^Columnas$/i), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: /Generar vista previa de malla/i }));

    await waitFor(() => expect(apiMock.previewPhysicalMap).toHaveBeenCalledWith(
      "proj_1",
      "op_1",
      expect.objectContaining({ grid_mode: "manual", rows: 2, columns: 2 })
    ));
    expect(await screen.findByText(/Heatmap mock · 4 puntos · preview/i)).toBeInTheDocument();
    expect(screen.getByText(/Vista previa en coordenadas PCB/i)).toBeInTheDocument();
  });

  it("refresca automaticamente los puntos medidos mientras el backend completa la malla", async () => {
    const plannedMap = {
      map_id: "measured/manual-2x2",
      status: "MESH_PLANNED",
      source: "MEASURED",
      point_count: 4,
      grid_mode: "manual",
      rows: 2,
      columns: 2,
      grid: { rows: 2, columns: 2, dx_mm: 76, dy_mm: 56 },
      local_region: { min_x_mm: 2, min_y_mm: 2, max_x_mm: 78, max_y_mm: 58 },
      points: [
        { index: 0, role: "REFERENCE", row: 0, column: 0, x_local: 2, y_local: 2, x_machine: 62, y_machine: 90.75, status: "MEASURED", z_measured: 0, delta_z: 0 },
        { index: 1, row: 0, column: 1, x_local: 78, y_local: 2, x_machine: 138, y_machine: 90.75, status: "PENDING" },
        { index: 2, row: 1, column: 1, x_local: 78, y_local: 58, x_machine: 138, y_machine: 146.75, status: "PENDING" },
        { index: 3, row: 1, column: 0, x_local: 2, y_local: 58, x_machine: 62, y_machine: 146.75, status: "PENDING" },
      ],
    };
    const probingMap = {
      ...plannedMap,
      status: "MESH_PROBING",
      execution: { worker_active: true, point_state: "POINT_MOVE_XY" },
    };
    const completedMap = {
      ...plannedMap,
      status: "MESH_COMPLETE",
      execution: { worker_active: false, point_state: "MESH_COMPLETE" },
      points: [
        { index: 0, role: "REFERENCE", row: 0, column: 0, x_local: 2, y_local: 2, x_machine: 62, y_machine: 90.75, status: "MEASURED", z_measured: 0, delta_z: 0 },
        { index: 1, row: 0, column: 1, x_local: 78, y_local: 2, x_machine: 138, y_machine: 90.75, status: "MEASURED", z_measured: 0.01, delta_z: 0.01 },
        { index: 2, row: 1, column: 1, x_local: 78, y_local: 58, x_machine: 138, y_machine: 146.75, status: "MEASURED", z_measured: 0.02, delta_z: 0.02 },
        { index: 3, row: 1, column: 0, x_local: 2, y_local: 58, x_machine: 62, y_machine: 146.75, status: "MEASURED", z_measured: -0.01, delta_z: -0.01 },
      ],
    };
    apiMock.getPhysicalMap.mockResolvedValueOnce({ payload: plannedMap });
    apiMock.getPhysicalMap.mockResolvedValue({ payload: completedMap });
    apiMock.executeAllPhysicalMapPoints.mockResolvedValue({ payload: probingMap });

    renderWorkspace(physicalMachine);
    fireEvent.click(screen.getByRole("button", { name: /Mapa de alturas/i }));
    expect(await screen.findByText(/Mapa medido físicamente/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /^3\. Armar sondeo$/i }));
    fireEvent.click(screen.getByRole("button", { name: /^4\. Iniciar sondeo automático$/i }));

    await waitFor(() => expect(apiMock.executeAllPhysicalMapPoints).toHaveBeenCalledWith("proj_1", "measured/manual-2x2"));
    await waitFor(() => expect(apiMock.getPhysicalMap).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(apiMock.getPhysicalHeightMap).toHaveBeenCalledWith("proj_1", "op_1"));
    await waitFor(() => expect(apiMock.getReferenceSession).toHaveBeenCalledTimes(3));
    expect(await screen.findByText(/^MESH_COMPLETE$/i)).toBeInTheDocument();
  });

  it("hidrata el mapa físico completo al abrir el workspace y habilita 2D/3D sin re-sondear", async () => {
    apiMock.getHeightMap.mockRejectedValue(new Error("No existe mapa de alturas para esta operación."));
    apiMock.getReferenceSession.mockResolvedValue(referenceSession);
    apiMock.getPhysicalMap.mockResolvedValue({
      payload: {
        map_id: "measured/manual-2x2",
        status: "MESH_COMPLETE",
        source: "MEASURED",
        map_ready_state: "MAP_READY",
        point_count: 4,
        grid_mode: "manual",
        rows: 2,
        columns: 2,
        dx: 76,
        dy: 56,
        grid: { rows: 2, columns: 2, dx_mm: 76, dy_mm: 56 },
        local_region: { min_x_mm: 2, min_y_mm: 2, max_x_mm: 78, max_y_mm: 58 },
        points: [
          { index: 0, role: "REFERENCE", row: 0, column: 0, x_local: 2, y_local: 2, x_machine: 62, y_machine: 90.75, status: "MEASURED", z_measured: 0, delta_z: 0 },
          { index: 1, row: 0, column: 1, x_local: 78, y_local: 2, x_machine: 138, y_machine: 90.75, status: "MEASURED", z_measured: 0.01, delta_z: 0.01 },
          { index: 2, row: 1, column: 1, x_local: 78, y_local: 58, x_machine: 138, y_machine: 146.75, status: "MEASURED", z_measured: 0.02, delta_z: 0.02 },
          { index: 3, row: 1, column: 0, x_local: 2, y_local: 58, x_machine: 62, y_machine: 146.75, status: "MEASURED", z_measured: -0.01, delta_z: -0.01 },
        ],
        validation: { status: "VALID", sufficient: true, validated_at: new Date().toISOString() },
      },
    });

    renderWorkspace(physicalMachine);
    fireEvent.click(screen.getByRole("button", { name: /Mapa de alturas/i }));

    await waitFor(() => expect(apiMock.getPhysicalHeightMap).toHaveBeenCalledWith("proj_1", "op_1"));
    expect(await screen.findByText(/Heatmap mock · 4 puntos · medido/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: /Superficie 3D/i }));
    expect(await screen.findByText(/Surface 3D mock/i)).toBeInTheDocument();
  });

  it("hidrata un mapa físico MAP_READY sin requerir recargar la página", async () => {
    apiMock.getHeightMap.mockRejectedValue(new Error("No existe mapa de alturas para esta operación."));
    apiMock.getReferenceSession.mockResolvedValue(referenceSession);
    apiMock.getPhysicalMap.mockResolvedValue({
      payload: {
        map_id: "measured/manual-2x2",
        status: "MAP_READY",
        source: "MEASURED",
        map_ready_state: "MAP_READY",
        point_count: 4,
        grid_mode: "manual",
        rows: 2,
        columns: 2,
        dx: 76,
        dy: 56,
        grid: { rows: 2, columns: 2, dx_mm: 76, dy_mm: 56 },
        local_region: { min_x_mm: 2, min_y_mm: 2, max_x_mm: 78, max_y_mm: 58 },
        points: [
          { index: 0, role: "REFERENCE", row: 0, column: 0, x_local: 2, y_local: 2, x_machine: 62, y_machine: 90.75, status: "MEASURED", z_measured: 0, delta_z: 0 },
          { index: 1, row: 0, column: 1, x_local: 78, y_local: 2, x_machine: 138, y_machine: 90.75, status: "MEASURED", z_measured: 0.01, delta_z: 0.01 },
          { index: 2, row: 1, column: 1, x_local: 78, y_local: 58, x_machine: 138, y_machine: 146.75, status: "MEASURED", z_measured: 0.02, delta_z: 0.02 },
          { index: 3, row: 1, column: 0, x_local: 2, y_local: 58, x_machine: 62, y_machine: 146.75, status: "MEASURED", z_measured: -0.01, delta_z: -0.01 },
        ],
        validation: { status: "VALID", sufficient: true, validated_at: new Date().toISOString() },
      },
    });

    renderWorkspace(physicalMachine);
    fireEvent.click(screen.getByRole("button", { name: /Mapa de alturas/i }));

    await waitFor(() => expect(apiMock.getPhysicalHeightMap).toHaveBeenCalledWith("proj_1", "op_1"));
    expect(await screen.findByText(/Heatmap mock · 4 puntos · medido/i)).toBeInTheDocument();
  });

  it("muestra propuesta automática, permite aceptarla y reiniciar solo el mapa", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderWorkspace(physicalMachine);
    fireEvent.click(screen.getByRole("button", { name: /Mapa de alturas/i }));
    expect(await screen.findByText(/Mapa medido físicamente/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Sugerida automáticamente/i }));
    fireEvent.click(screen.getByRole("button", { name: /Ver propuesta/i }));
    expect(await screen.findByText(/Filas sugeridas/i)).toBeInTheDocument();
    expect(screen.getByText(/Se ajusta a la separación objetivo/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Aceptar sugerencia/i }));
    expect(screen.getByText(/Propuesta automática aceptada/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Generar vista previa de malla/i }));
    await waitFor(() => expect(apiMock.planPhysicalMapFromReference).toHaveBeenLastCalledWith(
      "proj_1",
      "op_1",
      expect.objectContaining({ grid_mode: "suggested", rows: 3, columns: 4 })
    ));

    fireEvent.click(screen.getByText(/Más acciones/i));
    fireEvent.click(screen.getByRole("button", { name: /Reiniciar solo mapa/i }));
    await waitFor(() => expect(apiMock.resetSetupMap).toHaveBeenCalledWith("proj_1", "setup-main"));
    expect(confirmSpy).toHaveBeenCalledWith(expect.stringContaining("conservará origen X/Y"));
    confirmSpy.mockRestore();
  });


  it("acepta 0 válido en X e Y del origen de trabajo", async () => {
    renderWorkspace();
    fireEvent.click(screen.getByRole("button", { name: /^Referencia$/i }));
    const originHeading = await screen.findByText(/2. Origen de trabajo X\/Y/i);
    const originPanel = originHeading.closest("article");
    expect(originPanel).not.toBeNull();
    const scope = within(originPanel as HTMLElement);

    fireEvent.change(scope.getByLabelText(/X \(mm\)/i), { target: { value: "0" } });
    fireEvent.change(scope.getByLabelText(/Y \(mm\)/i), { target: { value: "0" } });
    fireEvent.click(scope.getByRole("button", { name: /Confirmar en simulación/i }));

    await waitFor(() => expect(apiMock.confirmWorkOrigin).toHaveBeenCalledWith("proj_1", "op_1", { x_mm: 0, y_mm: 0 }));
  });

  it("acepta 0 válido en Z y números decimales", async () => {
    renderWorkspace();
    fireEvent.click(screen.getByRole("button", { name: /^Referencia$/i }));
    const zHeading = await screen.findByText(/3. Referencia Z/i);
    const zPanel = zHeading.closest("article");
    expect(zPanel).not.toBeNull();
    const scope = within(zPanel as HTMLElement);

    const zInput = scope.getByLabelText(/Z de referencia/i);
    fireEvent.change(zInput, { target: { value: "0" } });
    fireEvent.click(scope.getByRole("button", { name: /Confirmar en simulación/i }));
    await waitFor(() => expect(apiMock.confirmZReference).toHaveBeenCalledWith("proj_1", "op_1", { x_mm: 10, y_mm: 8, z_mm: 0 }));

    fireEvent.change(zInput, { target: { value: "0.25" } });
    fireEvent.click(scope.getByRole("button", { name: /Confirmar en simulación/i }));
    await waitFor(() => expect(apiMock.confirmZReference).toHaveBeenLastCalledWith("proj_1", "op_1", { x_mm: 10, y_mm: 8, z_mm: 0.25 }));
  });

  it("muestra error en campo vacío y conserva lo escrito si la API responde con 422", async () => {
    apiMock.confirmZReference.mockRejectedValueOnce(
      new ApiError("Solicitud invalida. z_mm: debe ser un numero valido.", 422, { z_mm: "Solicitud invalida. z_mm: debe ser un numero valido." })
    );
    renderWorkspace();
    fireEvent.click(screen.getByRole("button", { name: /^Referencia$/i }));
    const zHeading = await screen.findByText(/3. Referencia Z/i);
    const zPanel = zHeading.closest("article");
    expect(zPanel).not.toBeNull();
    const scope = within(zPanel as HTMLElement);

    const zInput = scope.getByLabelText(/Z de referencia/i) as HTMLInputElement;
    fireEvent.change(zInput, { target: { value: "" } });
    fireEvent.click(scope.getByRole("button", { name: /Confirmar en simulación/i }));
    expect(scope.getByText(/Indique Z en milímetros/i)).toBeInTheDocument();
    expect(zInput).toHaveFocus();

    fireEvent.change(zInput, { target: { value: "0.75" } });
    fireEvent.click(scope.getByRole("button", { name: /Confirmar en simulación/i }));
    await waitFor(() => expect(apiMock.confirmZReference).toHaveBeenCalled());
    expect(screen.getAllByText(/Solicitud invalida. z_mm: debe ser un numero valido./i).length).toBeGreaterThan(0);
    expect(zInput.value).toBe("0.75");
    expect(document.activeElement).toBe(zInput);
  });

  it("copia X/Y del origen de trabajo hacia la referencia Z cuando se activa la opción", async () => {
    renderWorkspace();
    fireEvent.click(screen.getByRole("button", { name: /^Referencia$/i }));
    const originPanel = (await screen.findByText(/2. Origen de trabajo X\/Y/i)).closest("article") as HTMLElement;
    const zPanel = (await screen.findByText(/3. Referencia Z/i)).closest("article") as HTMLElement;
    const originScope = within(originPanel);
    const zScope = within(zPanel);

    fireEvent.change(originScope.getByLabelText(/X \(mm\)/i), { target: { value: "12.5" } });
    fireEvent.change(originScope.getByLabelText(/Y \(mm\)/i), { target: { value: "8.5" } });
    fireEvent.click(zScope.getByLabelText(/Usar la misma posición X\/Y/i));

    const xInputs = zScope.getAllByLabelText(/X \(mm\)/i) as HTMLInputElement[];
    const yInputs = zScope.getAllByLabelText(/Y \(mm\)/i) as HTMLInputElement[];
    expect(xInputs[0]).toBeDisabled();
    expect(yInputs[0]).toBeDisabled();

    fireEvent.change(zScope.getByLabelText(/Z de referencia/i), { target: { value: "0" } });
    fireEvent.click(zScope.getByRole("button", { name: /Confirmar en simulación/i }));

    await waitFor(() => expect(apiMock.confirmZReference).toHaveBeenCalledWith("proj_1", "op_1", { x_mm: 12.5, y_mm: 8.5, z_mm: 0 }));
  });

  it("mantiene la navegación accesible y no muestra exportación ejecutable", async () => {
    window.innerWidth = 360;
    renderWorkspace();

    await waitFor(() => expect(screen.getAllByRole("button", { name: /^Archivo$/i }).length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole("button", { name: /Compensación/i }));
    expect(screen.getByRole("button", { name: /Previsualizar compensación/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Exportar/i })).toBeNull();
    expect(screen.queryByText(/Descargar G-code/i)).toBeNull();
  });

  it("crea una operación repetible dentro del montaje activo", async () => {
    const onAddOperation = vi.fn().mockResolvedValue(undefined);
    render(
      <ProjectWorkspace
        project={project}
        busyKey={null}
        savingProject={false}
        onSaveProject={vi.fn()}
        onAddSetup={vi.fn()}
        onAddOperation={onAddOperation}
        onUpdateOperation={vi.fn()}
        onDuplicateOperation={vi.fn()}
        onMoveOperation={vi.fn()}
        onDeleteOperation={vi.fn()}
        onRemoveFile={vi.fn()}
        onAnalyze={vi.fn()}
        onUploadFile={vi.fn()}
      />
    );

    fireEvent.change(screen.getByLabelText(/Tipo de operación/i), { target: { value: "taladrado" } });
    fireEvent.change(screen.getByLabelText("Nombre"), { target: { value: "Taladrado 0,8 mm" } });
    fireEvent.change(screen.getByLabelText("Herramienta"), { target: { value: "Broca 0,8 mm" } });
    fireEvent.click(screen.getByRole("button", { name: /Agregar operación/i }));

    await waitFor(() => expect(onAddOperation).toHaveBeenCalledWith({
      setup_id: "setup-main",
      nombre: "Taladrado 0,8 mm",
      tipo: "taladrado",
      herramienta: "Broca 0,8 mm",
    }));
  });

  it("selecciona una trayectoria independiente por operation_id", async () => {
    const secondProject: Project = {
      ...project,
      operaciones: [
        ...project.operaciones,
        {
          ...project.operaciones[0],
          id: "op_2",
          nombre: "Taladrado 1,0 mm",
          tipo: "taladrado",
          orden: 1,
          archivo_gcode: null,
          nombre_archivo_original: null,
          analisis: null,
        },
      ],
    };
    render(
      <ProjectWorkspace
        project={secondProject}
        busyKey={null}
        savingProject={false}
        onSaveProject={vi.fn()}
        onAddSetup={vi.fn()}
        onAddOperation={vi.fn()}
        onUpdateOperation={vi.fn()}
        onDuplicateOperation={vi.fn()}
        onMoveOperation={vi.fn()}
        onDeleteOperation={vi.fn()}
        onRemoveFile={vi.fn()}
        onAnalyze={vi.fn()}
        onUploadFile={vi.fn()}
      />
    );

    fireEvent.click(screen.getByRole("button", { name: "Trayectoria" }));
    fireEvent.change(screen.getByLabelText(/Operación activa/i), { target: { value: "op_2" } });
    expect(await screen.findByText("Esta operación todavía no tiene G-code.")).toBeInTheDocument();
    expect(screen.getAllByText(/Taladrado 1,0 mm/).length).toBeGreaterThan(0);
  });
});
