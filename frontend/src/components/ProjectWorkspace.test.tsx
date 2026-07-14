import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

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
  validateHeightMap: vi.fn(),
  getCompensationPreview: vi.fn(),
  getPhysicalMap: vi.fn(),
  getPhysicalHeightMap: vi.fn(),
  planPhysicalMapFromReference: vi.fn(),
  executeNextPhysicalMapPoint: vi.fn(),
  pausePhysicalMap: vi.fn(),
  resumePhysicalMap: vi.fn(),
  cancelPhysicalMap: vi.fn(),
  generateCompensatedGCode: vi.fn(),
  generatedFileUrl: vi.fn(),
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
    />
  );
}

describe("ProjectWorkspace", () => {
  beforeEach(() => {
    Object.values(apiMock).forEach((fn) => fn.mockReset());
    apiMock.getHeightMap.mockResolvedValue(heightMap);
    apiMock.getReferenceSession.mockResolvedValue(referenceSession);
    apiMock.getPhysicalMap.mockRejectedValue(new Error("No existe mapa físico medido para este montaje y cara."));
    apiMock.getPhysicalHeightMap.mockResolvedValue(heightMap);
    apiMock.generatedFileUrl.mockImplementation((_projectId: string, relativePath: string) => `/api/projects/proj_1/generated/${relativePath}`);
    apiMock.confirmWorkOrigin.mockResolvedValue(referenceSession);
    apiMock.confirmZReference.mockResolvedValue(referenceSession);
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
    fireEvent.click(screen.getByRole("button", { name: /Referencia/i }));
    expect(await screen.findByText(/Flujo simulado de preparación/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Mapa de alturas/i }));
    fireEvent.click(screen.getByRole("button", { name: /Configuración/i }));
    expect(screen.getAllByText(/Alturas de la superficie/i).length).toBeGreaterThan(0);
  });

  it("acepta 0 válido en X e Y del origen de trabajo", async () => {
    renderWorkspace();
    fireEvent.click(screen.getByRole("button", { name: /Referencia/i }));
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
    fireEvent.click(screen.getByRole("button", { name: /Referencia/i }));
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
    fireEvent.click(screen.getByRole("button", { name: /Referencia/i }));
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
    fireEvent.click(screen.getByRole("button", { name: /Referencia/i }));
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
