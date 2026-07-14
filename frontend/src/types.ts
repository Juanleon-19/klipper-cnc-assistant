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
  origen_trabajo: Record<string, string | number | null> | null;
  referencia_z: Record<string, string | number | null> | null;
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
  puntos_fuera_dominio: number;
  puntos_virtuales_agregados: number;
  resumen_z_original: { min_mm: number | null; max_mm: number | null };
  resumen_z_compensada: { min_mm: number | null; max_mm: number | null };
  segmentos: CompensationPreviewSegment[];
};

export type Operation = {
  id: string;
  nombre: string;
  tipo: string;
  cara: string;
  orden: number;
  archivo_gcode: string | null;
  nombre_archivo_original: string | null;
  tamano_archivo_bytes: number | null;
  sha256: string | null;
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
  operaciones: Operation[];
  creado_en: string;
  actualizado_en: string;
  version_esquema: string;
  estado_general: string;
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
