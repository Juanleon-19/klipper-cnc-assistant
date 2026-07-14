import { describe, expect, it } from "vitest";

import { buildOperationWorkflow, getOperationWorkflowState, translateStatus } from "./ui";

describe("ui helpers", () => {
  it("traduce estados internos del backend a español humano", () => {
    expect(translateStatus("simulada_lista_para_preparacion")).toBe("Lista para preparación simulada");
    expect(translateStatus("con advertencias")).toBe("Con advertencias");
  });

  it("resume el flujo de una operación", () => {
    const operation = {
      id: "op_1",
      nombre: "Perforado",
      tipo: "taladrado",
      cara: "superior",
      orden: 2,
      archivo_gcode: "originals/a.nc",
      nombre_archivo_original: "a.nc",
      tamano_archivo_bytes: 32,
      sha256: "abc",
      herramienta: "Broca",
      estado: "valida",
      analisis: {
        limites: null,
        avances_mm_min: [],
        profundidad_min_mm: null,
        profundidad_max_mm: null,
        cantidad_movimientos: 0,
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
    };

    expect(getOperationWorkflowState(operation)).toBe("Preparada para fase posterior");
    const workflow = buildOperationWorkflow(operation);
    expect(workflow[workflow.length - 1]?.complete).toBe(true);
  });
});
