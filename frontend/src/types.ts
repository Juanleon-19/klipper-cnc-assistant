export type HealthResponse = {
  estado: string;
  version: string;
  modo_maquina: string;
  almacenamiento: string;
};

export type SystemInfoResponse = {
  estado: string;
  version_aplicacion: string;
  version_python: string;
  almacenamiento_disponible: boolean;
  estado_api: string;
  modo_maquina: string;
  hora_servidor: string;
  backend_version: string;
  frontend_build: string;
  git_commit: string | null;
  schema_version: string;
};

export type Material = {
  ancho_mm: number;
  alto_mm: number;
  espesor_mm: number | null;
};

export type AgujeroAlineacion = {
  x_mm: number;
  y_mm: number;
  diametro_mm: number | null;
};

export type PreviewPoint = {
  x_mm: number;
  y_mm: number;
};

export type PreviewSegment = {
  tipo: string;
  tipo_movimiento: string;
  numero_linea: number | null;
  inicio_x_mm: number;
  inicio_y_mm: number;
  fin_x_mm: number;
  fin_y_mm: number;
  z_mm: number | null;
  avance_mm_min: number | null;
  distancia_mm: number;
  advertencias: string[];
  puntos: PreviewPoint[];
  desde: PreviewPoint;
  hasta: PreviewPoint;
};

export type MaterialOverflow = {
  eje: string;
  direccion: string;
  limite_mm: number;
  valor_mm: number;
  exceso_mm: number;
};

export type Bounds = {
  min_x_mm: number;
  max_x_mm: number;
  min_y_mm: number;
  max_y_mm: number;
  min_z_mm: number;
  max_z_mm: number;
  ancho_mm: number;
  alto_mm: number;
};

export type AnalysisIssue = {
  severidad: string;
  codigo: string;
  mensaje: string;
  linea: number | null;
  comando: string | null;
};

export type OperationAnalysis = {
  analysis_version: string;
  current_analysis_version: string;
  analisis_desactualizado: boolean;
  limites: Bounds | null;
  avances_mm_min: number[];
  profundidad_min_mm: number | null;
  profundidad_max_mm: number | null;
  cantidad_movimientos: number;
  comandos_desconocidos: string[];
  comandos_no_compatibles: string[];
  acciones_husillo: string[];
  cambios_herramienta: string[];
  comandos_manuales: string[];
  unidades_detectadas: string[];
  modos_posicionamiento: string[];
  incidencias: AnalysisIssue[];
  analisis_incompleto: boolean;
  soporte_geometrico_incompleto: boolean;
  cabe_en_material: boolean | null;
  mensaje_material: string | null;
  tiene_errores_criticos: boolean;
  segmentos_lineales: PreviewSegment[];
  segmentos_vista_previa: PreviewSegment[];
  desbordes_material: MaterialOverflow[];
  tolerancia_arco_mm: number | null;
};

export type ProbeRegion = {
  min_x_mm: number;
  min_y_mm: number;
  max_x_mm: number;
  max_y_mm: number;
};

export type ExclusionZone = {
  id: string;
  nombre: string;
  min_x_mm: number;
  min_y_mm: number;
  max_x_mm: number;
  max_y_mm: number;
};

export type HeightMapGrid = {
  filas: number;
  columnas: number;
  ancho_mm: number;
  alto_mm: number;
  paso_x_mm: number;
  paso_y_mm: number;
};

export type HeightMapSample = {
  id: string;
  x_mm: number;
  y_mm: number;
  z_mm: number | null;
  fila: number;
  columna: number;
  origen_datos: string;
  estado_calidad: string;
  observacion: string | null;
  incluida: boolean;
  residuo_plano_mm: number | null;
};

export type HeightMapPlane = {
  a: number;
  b: number;
  c: number;
  inclinacion_x_mm_por_mm: number;
  inclinacion_y_mm_por_mm: number;
  rms_residuos_mm: number;
  residuo_maximo_mm: number;
  residuo_minimo_mm: number;
};

export type HeightMapStatistics = {
  cantidad_puntos: number;
  cantidad_puntos_incluidos: number;
  cantidad_puntos_faltantes: number;
  cantidad_puntos_atipicos: number;
  altura_min_mm: number | null;
  altura_max_mm: number | null;
  rango_alturas_mm: number | null;
  valor_referencia_mm: number | null;
  desviacion_rms_respecto_plano_mm: number | null;
  residuo_maximo_mm: number | null;
  ancho_cubierto_mm: number | null;
  alto_cubierto_mm: number | null;
};

export type HeightMapSurfacePoint = {
  fila: number;
  columna: number;
  x_mm: number;
  y_mm: number;
  z_mm: number | null;
  estado: string;
  observacion: string | null;
};

export type HeightMapSurface = {
  filas: number;
  columnas: number;
  modo: string;
  puntos: HeightMapSurfacePoint[];
};

export type HeightMap = {
  proyecto_id: string;
  operacion_id: string;
  version: number;
  version_algoritmo: string;
  estado: string;
  fuente_datos: string;
  superficie_simulada: string | null;
  repeticion_simulacion: number | null;
  etiqueta_simulada: boolean;
  grid: HeightMapGrid;
  probe_region: ProbeRegion;
  exclusion_zones: ExclusionZone[];
  muestras: HeightMapSample[];
  estadisticas: HeightMapStatistics;
  plano: HeightMapPlane | null;
  superficies: Record<string, HeightMapSurface>;
  creado_en: string;
  actualizado_en: string;
};

export type ReferencePoint = {
  x_mm: number | null;
  y_mm: number | null;
  z_mm: number | null;
};

export type CapturedPosition = {
  x_mm: number;
  y_mm: number;
  z_mm: number | null;
};

export type CoordinateReference = {
  x_mm: number;
  y_mm: number;
  z_mm: number | null;
  fecha: string | null;
  fuente: string;
  maquina: string | null;
  homed_axes: string | null;
  posicion_captura: CapturedPosition | null;
  sesion: string | null;
};

export type ReferenceConfirmation = {
  x_mm: number;
  y_mm: number;
  z_mm?: number;
};

export type ReferenceStep = {
  id: string;
  titulo: string;
  estado: string;
  confirmado: boolean;
  fecha: string | null;
  detalle: string | null;
};

export type ReferenceSession = {
  estado: string;
  machine_reference: {
    confirmada: boolean;
    fecha: string | null;
  };
  origen_maquina: ReferencePoint;
  origen_material: ReferencePoint;
  origen_gcode: ReferencePoint;
  origen_trabajo: CoordinateReference | null;
  referencia_z: CoordinateReference | null;
  pasos: ReferenceStep[];
  compensacion_previsualizada_en: string | null;
  analysis_stale: boolean;
  lista_para_compensacion: boolean;
  bloqueos_compensacion: string[];
  motivo_invalidacion: string | null;
};

export type CompensationPreviewPoint = {
  x_mm: number;
  y_mm: number;
  z_original_mm: number | null;
  z_superficie_mm: number | null;
  correccion_mm: number | null;
  z_compensada_mm: number | null;
  estado: string;
  observacion: string | null;
};

export type CompensationPreviewSegment = {
  tipo: string;
  tipo_movimiento: string;
  numero_linea: number | null;
  estado: string;
  distancia_mm: number;
  puntos: CompensationPreviewPoint[];
};

export type CompensationPreview = {
  convencion_matematica: string;
  z_referencia_mm: number;
  paso_muestreo_virtual_mm: number;
  tolerancia_dominio_mm: number;
  puntos_dentro_dominio: number;
  puntos_fuera_dominio: number;
  puntos_fuera_dominio_bloqueantes: number;
  distancia_maxima_fuera_dominio_mm: number;
  cobertura_suficiente: boolean;
  puntos_fuera_dominio_detalle: Array<{
    operation_id: string;
    operation_name: string;
    segment_index: number;
    point_index: number;
    x_mm: number;
    y_mm: number;
    distance_mm: number;
    reason: string;
    numerical_only: boolean;
  }>;
  puntos_virtuales_agregados: number;
  resumen_z_original: { min_mm: number | null; max_mm: number | null };
  resumen_z_compensada: { min_mm: number | null; max_mm: number | null };
  segmentos: CompensationPreviewSegment[];
};

export type Setup = {
  id: string;
  nombre: string;
  orden: number;
  placement_revision?: string;
  active_reference_id?: string | null;
  active_map_id?: string | null;
  preparation_status?: string;
  last_prepared_at?: string | null;
};

export type Operation = {
  id: string;
  nombre: string;
  tipo: string;
  cara: string;
  orden: number;
  setup_id: string;
  archivo_gcode: string | null;
  nombre_archivo_original: string | null;
  tamano_archivo_bytes: number | null;
  sha256: string | null;
  tool_id: string | null;
  herramienta: string | null;
  estado: string;
  analisis: OperationAnalysis | null;
};

export type Project = {
  id: string;
  nombre: string;
  material: Material;
  doble_cara: boolean;
  eje_volteo: string | null;
  agujeros_alineacion: AgujeroAlineacion[];
  montajes: Setup[];
  operaciones: Operation[];
  creado_en: string;
  actualizado_en: string;
  created_at?: string;
  updated_at?: string;
  last_opened_at?: string | null;
  archived_at?: string | null;
  trashed_at?: string | null;
  status?: string;
  current_setup_id?: string;
  version_esquema: string;
  estado_general: string;
};

export type ContinueProjectResult = {
  view: string;
  operation_id: string | null;
  reason: string | null;
};

export type MeshSuggestion = {
  grid_mode: "suggested";
  rows: number;
  columns: number;
  point_count: number;
  excluded_count: number;
  executable_point_count: number;
  dx_mm: number;
  dy_mm: number;
  estimated_distance_mm: number | null;
  estimated_time_s: number | null;
  reason: string;
  local_region: { min_x_mm: number; min_y_mm: number; max_x_mm: number; max_y_mm: number };
};


export type MachineRuntime = {
  mode: string;
  mode_label: string;
  state: string;
  health: string;
  started_at: string;
  application: Record<string, unknown>;
  moonraker: Record<string, unknown>;
  klipper: Record<string, unknown>;
  preparation?: {
    reference_prep_z_mm?: number;
    reference_prep_z_feed_mm_min?: number;
    reference_prep_z_speed_mm_s?: number;
    reference_prep_xy_feed_mm_min?: number;
    reference_prep_xy_speed_mm_s?: number;
    center_x_mm?: number | null;
    center_y_mm?: number | null;
    target?: { x_mm?: number | null; y_mm?: number | null; z_mm?: number | null } | null;
    sequence?: string[];
  };
  tool_change?: { x_mm?: number; y_mm?: number; z_mm?: number; z_feed_mm_min?: number; z_speed_mm_s?: number };
  arduino: Record<string, unknown>;
  controller: Record<string, unknown>;
  safety: Record<string, unknown>;
  last_command: string | null;
  last_movement: Record<string, unknown> | null;
  last_error: string | null;
  last_probe_result: Record<string, unknown> | null;
  initialization_steps: Array<Record<string, unknown>>;
  events: Array<Record<string, unknown>>;
};

export type MachineSession = {
  estado: string;
  home_realizado: boolean;
  referencia_maquina_confirmada_en: string | null;
  z_en_altura_segura: boolean;
  herramienta_en_centro_cama: boolean;
  material_montado: boolean;
  origen_xy_definido: boolean;
  cero_z_capturado: boolean;
  operaciones_permitidas: string[];
  z_puede_bajar_durante: string[];
};

export type ProjectPayload = {
  nombre: string;
  material: Material;
  doble_cara: boolean;
  eje_volteo: string | null;
  agujeros_alineacion: AgujeroAlineacion[];
};

export type PhysicalMeshPoint = {
  index: number;
  row: number | null;
  column: number | null;
  role?: "GRID" | "REFERENCE" | string;
  x_local: number;
  y_local: number;
  x_machine?: number | null;
  y_machine?: number | null;
  z_measured?: number | null;
  z_measured_abs?: number | null;
  delta_z?: number | null;
  status: string;
  attempts?: number;
  duration?: number | null;
  duration_s?: number | null;
  error?: string | null;
  last_error?: string | null;
};

export type PhysicalMapExclusion = {
  id: string;
  name: string;
  shape: "rectangle" | "circle";
  enabled: boolean;
  x_min_mm?: number | null;
  x_max_mm?: number | null;
  y_min_mm?: number | null;
  y_max_mm?: number | null;
  center_x_mm?: number | null;
  center_y_mm?: number | null;
  radius_mm?: number | null;
};

export type PhysicalMapPayload = Record<string, unknown> & {
  map_id?: string | null;
  preview_id?: string;
  preview_version?: string;
  status?: string;
  source?: string;
  point_count?: number;
  excluded_count?: number;
  executable_point_count?: number;
  estimated_distance_mm?: number | null;
  estimated_time_s?: number | null;
  edge_margins?: { left_mm: number; right_mm: number; bottom_mm: number; top_mm: number };
  exclusions?: PhysicalMapExclusion[];
  points?: PhysicalMeshPoint[];
  grid_mode?: "manual" | "suggested";
  rows?: number;
  columns?: number;
  dx?: number;
  dy?: number;
  configuration_change_warning?: string;
  grid?: { rows: number; columns: number; dx_mm: number; dy_mm: number };
  local_region?: { min_x_mm: number; min_y_mm: number; max_x_mm: number; max_y_mm: number };
  probe_region?: { min_x_mm: number; min_y_mm: number; max_x_mm: number; max_y_mm: number };
  material_bounds?: { min_x_mm: number; min_y_mm: number; max_x_mm: number; max_y_mm: number };
  machine_region?: { min_x_mm: number; min_y_mm: number; max_x_mm: number; max_y_mm: number } | null;
  local_points?: PhysicalMeshPoint[];
  machine_points?: PhysicalMeshPoint[] | null;
  serpentine_path?: PhysicalMeshPoint[];
  reference_point?: PhysicalMeshPoint;
  valid_for_execution?: boolean;
  warnings?: string[];
  probe_config?: { safe_z_mm?: number | null; probe_step_mm?: number | null; probe_feed_mm_min?: number | null; retract_mm?: number | null };
  tool_references?: Record<string, unknown>;
};

export type PhysicalMapResponse = {
  payload: PhysicalMapPayload;
};

export type CompensatedGCodeResult = {
  relative_path: string;
  metadata_path: string;
  metadata: Record<string, unknown>;
  preview: Record<string, unknown>;
};
