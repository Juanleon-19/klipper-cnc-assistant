import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { HeightMap, JobPlan, JobRun, LiveExecutionSnapshot, Project, ReferenceSession } from "../../types";
import { ProjectWorkspace } from "./ProjectWorkspace";

const apiMock = vi.hoisted(() => ({
  getHeightMap: vi.fn(),
  getReferenceSession: vi.fn(),
  getPhysicalMap: vi.fn(),
  getPhysicalHeightMap: vi.fn(),
  getPhysicalMapHistory: vi.fn(),
  getMachineSettings: vi.fn(),
  updateMachineSettings: vi.fn(),
  getJobPlan: vi.fn(),
  getLiveExecution: vi.fn(),
  prepareJobRun: vi.fn(),
  startJobRun: vi.fn(),
  archiveStaleJobRun: vi.fn(),
  runJobAction: vi.fn(),
  getJobHistory: vi.fn(),
  getCompensationAudit: vi.fn(),
  generatedFileUrl: vi.fn(),
}));

vi.mock("../../lib/api", async () => {
  const actual = await vi.importActual<typeof import("../../lib/api")>("../../lib/api");
  return {
    ...actual,
    api: apiMock,
  };
});

vi.mock("../viewer/ToolpathViewer", () => ({
  ToolpathViewer: () => <div>ToolpathViewer mock</div>,
}));

vi.mock("../height-map/HeightMapHeatmap", () => ({
  HeightMapHeatmap: () => <div>Heatmap mock</div>,
}));

vi.mock("../height-map/HeightMapSurface3D", () => ({
  HeightMapSurface3D: () => <div>Surface 3D mock</div>,
}));

const jobPlan: JobPlan = {
  schema_version: "job-plan-v1",
  plan_id: "job-plan/setup-main/superior",
  project_id: "proj_1",
  setup_id: "setup-main",
  face: "superior",
  placement_revision: "placement-1",
  active_map_id: "map-1",
  operations: [
    {
      operation_id: "op_1",
      order: 0,
      order_label: "001",
      name: "Fresado superior",
      type: "aislamiento",
      tool_id: "vbit-30",
      tool_name: "V-bit 30°",
      tool_key: "vbit-30",
      tool_changed: false,
      map_status: "LISTO",
      coverage_status: "VALIDA",
      coverage_detail: null,
      reference_status: "LISTA",
      generated_file: "generated/compensated/op_1_compensated.gcode",
      generated_file_name: "001_fresado_superior_compensado.nc",
      generated_metadata_path: "generated/compensated/op_1_compensated.json",
      compensation_status: "COMPENSADO",
      preflight_status: "PENDIENTE",
      execution_status: "PENDING",
      blocking: false,
      blocking_reasons: [],
    },
  ],
  summary: { operations_total: 1, operations_ready: 1, generated_files: 1, tool_changes: 0, distinct_tools: 1, blocked_operations: 0 },
  manifest_path: "reports/jobs/setup-main/superior/job_manifest.json",
  created_at: new Date().toISOString(),
  updated_at: new Date().toISOString(),
};

const jobRun: JobRun = {
  schema_version: "job-run-v1",
  run_id: "job-run/setup-main/superior/1",
  plan_id: jobPlan.plan_id,
  project_id: "proj_1",
  setup_id: "setup-main",
  face: "superior",
  placement_revision: "placement-1",
  active_map_id: "map-1",
  state: "JOB_VALIDATING",
  ready: false,
  checks: [{ name: "homing", ok: false, detail: "Homing actual: pendiente." }],
  started_at: null,
  completed_at: null,
  updated_at: new Date().toISOString(),
  current_operation_index: 0,
  current_operation_id: "op_1",
  current_tool_key: "vbit-30",
  next_action: "Resolver bloqueos",
  available_actions: [],
  operations: [
    { operation_id: "op_1", order: 0, order_label: "001", name: "Fresado superior", type: "aislamiento", tool_id: "vbit-30", tool_name: "V-bit 30°", tool_key: "vbit-30", tool_changed: false, reference_status: "LISTA", generated_file: "generated/compensated/op_1_compensated.gcode", generated_file_name: "001_fresado_superior_compensado.nc", execution_status: "PENDING", started_at: null, completed_at: null, error: null, progress: 0 },
  ],
  summary: { operations_total: 1, operations_completed: 0, tool_changes_required: 0, tool_changes_completed: 0 },
  timeline: [],
  events: [{ timestamp: new Date().toISOString(), level: "info", message: "Trabajo no listo." }],
  manifest_path: jobPlan.manifest_path,
};

const liveExecution: LiveExecutionSnapshot = {
  moonraker: {
    connected: true,
    klipper_state: "ready",
    print_state: "standby",
    filename: null,
    progress: 0,
    is_active: false,
    file_position: null,
    file_size: null,
    print_duration: 0,
    message: null,
    updated_at: new Date().toISOString(),
  },
  run: {
    run_id: jobRun.run_id,
    status: "JOB_VALIDATING",
    current_operation_index: 0,
    total_operations: 1,
    completed_operations: 0,
    overall_progress: 0,
    next_action: "Resolver bloqueos",
    available_actions: [],
    worker_alive: false,
    watcher_alive: false,
    stale_candidate: false,
    last_watcher_error: null,
    updated_at: new Date().toISOString(),
  },
  operation: {
    operation_id: "op_1",
    name: "Fresado superior",
    tool: "V-bit 30°",
    execution_status: "JOB_VALIDATING",
    expected_remote_file: null,
    observed_filename: null,
    filename_match: false,
    observed_printing: false,
    progress: 0,
  },
  operations: jobRun.operations,
  transition: {
    state: "JOB_VALIDATING",
    required_tool: null,
    operator_confirmation_required: false,
  },
  synchronization: {
    ok: true,
    reason: null,
  },
  events: [{ event_id: "evt-1", timestamp: new Date().toISOString(), level: "info", stage: "JOB_VALIDATING", message: "Trabajo no listo." }],
  job_run: jobRun,
};

const referenceSession: ReferenceSession = {
  estado: "mapa_validado",
  machine_reference: { confirmada: true, fecha: new Date().toISOString() },
  origen_maquina: { x_mm: 0, y_mm: 0, z_mm: 0 },
  origen_material: { x_mm: 0, y_mm: 0, z_mm: 0 },
  origen_gcode: { x_mm: 0, y_mm: 0, z_mm: 0 },
  origen_trabajo: { x_mm: 0, y_mm: 0, z_mm: null, fecha: new Date().toISOString(), fuente: "MEASURED", maquina: "klipper", homed_axes: "xyz", posicion_captura: { x_mm: 0, y_mm: 0, z_mm: null }, sesion: "test" },
  referencia_z: { x_mm: 10, y_mm: 8, z_mm: 0, fecha: new Date().toISOString(), fuente: "MEASURED", maquina: "klipper", homed_axes: "xyz", posicion_captura: { x_mm: 10, y_mm: 8, z_mm: 0 }, sesion: "test" },
  pasos: [],
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
  repeticion_simulacion: 1,
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

const compensationAudit = {
  selected_mode: "legacy" as const,
  recommended_mode: "legacy" as const,
  max_z_error_mm: 0.05,
  original: { mode: "original", estimated_time_s: 10, estimation_method: "internal", estimation_confidence: "medium", estimation_detail: "Interno", movements_total: 2, unsupported_commands: [] },
  legacy: { mode: "legacy", estimated_time_s: 10, estimation_method: "internal", estimation_confidence: "medium", estimation_detail: "Interno", movements_total: 2, unsupported_commands: [], executable: true },
  adaptive_fast: { mode: "adaptive_fast", estimated_time_s: 10.2, estimation_method: "internal", estimation_confidence: "medium", estimation_detail: "Interno", movements_total: 4, unsupported_commands: [], eligible: false, executable: false, experimental_available: true, error: "Adaptive_fast solo puede descargarse como experimental." },
  warnings: [],
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

function renderWorkspace() {
  return render(
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
      initialView="ejecucion"
    />,
  );
}

describe("ProjectWorkspace job run flow", () => {
  beforeEach(() => {
    Object.values(apiMock).forEach((fn) => fn.mockReset());
    apiMock.getHeightMap.mockResolvedValue(heightMap);
    apiMock.getReferenceSession.mockResolvedValue(referenceSession);
    apiMock.getPhysicalMap.mockRejectedValue(new Error("No existe mapa físico medido para este montaje y cara."));
    apiMock.getPhysicalHeightMap.mockResolvedValue(heightMap);
    apiMock.getPhysicalMapHistory.mockResolvedValue([]);
    apiMock.getMachineSettings.mockResolvedValue({
      reference_prep_z_mm: 115,
      reference_prep_z_feed_mm_min: 180,
      move_total_timeout_s: 180,
      no_progress_timeout_s: 60,
      position_tolerance_mm: 0.05,
      velocity_tolerance_mm_s: 0.02,
    });
    apiMock.updateMachineSettings.mockResolvedValue({
      reference_prep_z_mm: 115,
      reference_prep_z_feed_mm_min: 180,
      move_total_timeout_s: 180,
      no_progress_timeout_s: 60,
      position_tolerance_mm: 0.05,
      velocity_tolerance_mm_s: 0.02,
    });
    apiMock.getJobPlan.mockResolvedValue(jobPlan);
    apiMock.getLiveExecution.mockResolvedValue(liveExecution);
    apiMock.prepareJobRun.mockResolvedValue(jobRun);
    apiMock.startJobRun.mockResolvedValue({ ...jobRun, state: "JOB_STARTING" });
    apiMock.archiveStaleJobRun.mockResolvedValue({ archived_run_id: jobRun.run_id });
    apiMock.runJobAction.mockResolvedValue(jobRun);
    apiMock.getJobHistory.mockResolvedValue([]);
    apiMock.getCompensationAudit.mockResolvedValue(compensationAudit);
    apiMock.generatedFileUrl.mockImplementation((_projectId: string, relativePath: string) => `/api/projects/proj_1/generated/${relativePath}`);
    window.localStorage.clear();
  });

  it("no dispara prepareJobRun durante el render ni el rerender", async () => {
    const view = renderWorkspace();

    await screen.findByRole("button", { name: /Preparar trabajo/i });
    expect(apiMock.prepareJobRun).not.toHaveBeenCalled();

    view.rerender(
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
        initialView="ejecucion"
      />,
    );

    expect(apiMock.prepareJobRun).not.toHaveBeenCalled();
  });

  it("lanza exactamente una petición al pulsar Preparar trabajo", async () => {
    renderWorkspace();

    fireEvent.click(await screen.findByRole("button", { name: /Preparar trabajo/i }));

    await waitFor(() => expect(apiMock.prepareJobRun).toHaveBeenCalledTimes(1));
    expect(apiMock.prepareJobRun).toHaveBeenCalledWith("proj_1", "setup-main", "superior");
  });
});
