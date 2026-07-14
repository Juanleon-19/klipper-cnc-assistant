import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

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
  validateHeightMap: vi.fn(),
  getCompensationPreview: vi.fn(),
}));

vi.mock("../lib/api", () => ({
  api: apiMock,
}));

vi.mock("../features/viewer/ToolpathViewer", () => ({
  ToolpathViewer: () => <div>ToolpathViewer mock</div>,
}));

vi.mock("../features/heightmap/HeightMapHeatmap", () => ({
  HeightMapHeatmap: () => <div>Heatmap mock</div>,
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
  origen_trabajo: { x_mm: 0, y_mm: 0, z_mm: null, fecha: new Date().toISOString() },
  referencia_z: { x_mm: 10, y_mm: 8, z_mm: 0, fecha: new Date().toISOString() },
  pasos: [
    { id: "referencia_maquina", titulo: "Referencia de maquina", confirmado: true, fecha: new Date().toISOString() },
    { id: "origen_xy", titulo: "Origen de trabajo X/Y", confirmado: true, fecha: new Date().toISOString() },
    { id: "referencia_z", titulo: "Referencia Z", confirmado: true, fecha: new Date().toISOString() },
    { id: "region_sondeable", titulo: "Region sondeable", confirmado: true, fecha: new Date().toISOString() },
    { id: "mapa", titulo: "Mapa", confirmado: true, fecha: new Date().toISOString() },
    { id: "validacion", titulo: "Validacion", confirmado: true, fecha: new Date().toISOString() },
  ],
  compensacion_previsualizada_en: null,
  analysis_stale: false,
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
  operaciones: [
    {
      id: "op_1",
      nombre: "Fresado superior",
      tipo: "aislamiento",
      cara: "superior",
      orden: 0,
      archivo_gcode: "originals/job.nc",
      nombre_archivo_original: "job.nc",
      tamano_archivo_bytes: 120,
      sha256: "abc",
      herramienta: "V-bit 30",
      estado: "valida",
      analisis: {
        analysis_version: "gcode-analysis-v2",
        current_analysis_version: "gcode-analysis-v2",
        analisis_desactualizado: false,
        limites: null,
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

describe("ProjectWorkspace", () => {
  beforeEach(() => {
    Object.values(apiMock).forEach((fn) => fn.mockReset());
    apiMock.getHeightMap.mockResolvedValue(heightMap);
    apiMock.getReferenceSession.mockResolvedValue(referenceSession);
    apiMock.getCompensationPreview.mockResolvedValue({ session: referenceSession, preview: { convencion_matematica: "z_compensada = z_original + (superficie_xy - z_referencia).", z_referencia_mm: 0, paso_muestreo_virtual_mm: 1, puntos_fuera_dominio: 0, puntos_virtuales_agregados: 0, resumen_z_original: { min_mm: -0.1, max_mm: 0 }, resumen_z_compensada: { min_mm: -0.09, max_mm: 0.01 }, segmentos: [] } });
  });

  it("navega entre vistas principales y subpestañas del mapa", async () => {
    render(
      <ProjectWorkspace
        project={project}
        busyKey={null}
        savingProject={false}
        onSaveProject={vi.fn()}
        onAddOperation={vi.fn()}
        onDeleteOperation={vi.fn()}
        onRemoveFile={vi.fn()}
        onAnalyze={vi.fn()}
        onUploadFile={vi.fn()}
      />
    );

    await waitFor(() => expect(apiMock.getReferenceSession).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: /Referencia/i }));
    expect(screen.getByText(/Flujo simulado de preparación/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Mapa de alturas/i }));
    fireEvent.click(screen.getByRole("button", { name: /Configuración/i }));
    expect(screen.getByText(/Región sondeable y simulación/i)).toBeInTheDocument();
  });

  it("mantiene la navegación accesible en un viewport estrecho y no muestra exportación ejecutable", async () => {
    window.innerWidth = 360;
    render(
      <ProjectWorkspace
        project={project}
        busyKey={null}
        savingProject={false}
        onSaveProject={vi.fn()}
        onAddOperation={vi.fn()}
        onDeleteOperation={vi.fn()}
        onRemoveFile={vi.fn()}
        onAnalyze={vi.fn()}
        onUploadFile={vi.fn()}
      />
    );

    await waitFor(() => expect(screen.getAllByRole("button", { name: /^Archivo$/i }).length).toBeGreaterThan(0));
    fireEvent.click(screen.getByRole("button", { name: /Validación/i }));
    expect(screen.getByRole("button", { name: /Previsualizar compensación/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Exportar/i })).toBeNull();
    expect(screen.queryByText(/Descargar G-code/i)).toBeNull();
  });
});
