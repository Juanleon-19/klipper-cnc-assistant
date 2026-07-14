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

export type PreviewSegment = {
  tipo: string;
  inicio_x_mm: number;
  inicio_y_mm: number;
  fin_x_mm: number;
  fin_y_mm: number;
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
