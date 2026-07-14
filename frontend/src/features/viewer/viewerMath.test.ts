import { describe, expect, it } from "vitest";

import {
  buildGridTicks,
  chooseGridStep,
  fitRectWithinViewport,
  getVisibleWorldRect,
  segmentToCanvasPoints,
  unionRects,
  worldToScreen,
  screenToWorld,
  zoomAtPoint,
} from "./viewerMath";

describe("viewerMath", () => {
  it("mantiene proporcion 1:1 al ajustar al material", () => {
    const transform = fitRectWithinViewport(
      { minX: 0, maxX: 100, minY: 0, maxY: 50 },
      { width: 1000, height: 600 }
    );

    expect(transform.scale).toBeGreaterThan(0);
    const origin = worldToScreen({ x: 0, y: 0 }, transform);
    const xPoint = worldToScreen({ x: 10, y: 0 }, transform);
    const yPoint = worldToScreen({ x: 0, y: 10 }, transform);
    expect(xPoint.x - origin.x).toBeCloseTo(origin.y - yPoint.y, 6);
  });

  it("invierte Y solo en pantalla", () => {
    const transform = { scale: 10, panX: 20, panY: 120 };
    const point = worldToScreen({ x: 5, y: 3 }, transform);
    expect(point).toEqual({ x: 70, y: 90 });
    expect(screenToWorld(point, transform)).toEqual({ x: 5, y: 3 });
  });

  it("ajusta al conjunto completo cuando une material y trayectoria", () => {
    const rect = unionRects(
      { minX: 0, maxX: 80, minY: 0, maxY: 50 },
      { minX: -2, maxX: 82, minY: -1, maxY: 55 }
    );
    expect(rect).toEqual({ minX: -2, maxX: 82, minY: -1, maxY: 55 });
  });

  it("preserva el punto bajo el cursor durante zoom", () => {
    const initial = { scale: 5, panX: 40, panY: 300 };
    const pointer = { x: 200, y: 150 };
    const before = screenToWorld(pointer, initial);
    const afterTransform = zoomAtPoint(initial, pointer, 1.2);
    const after = screenToWorld(pointer, afterTransform);
    expect(after.x).toBeCloseTo(before.x, 6);
    expect(after.y).toBeCloseTo(before.y, 6);
  });

  it("calcula una cuadrícula adaptativa", () => {
    expect(chooseGridStep(12)).toBe(10);
    expect(chooseGridStep(0.8)).toBe(100);
    const ticks = buildGridTicks({ minX: -5, maxX: 12, minY: 0, maxY: 18 }, 5);
    expect(ticks.x).toContain(10);
    expect(ticks.y).toContain(15);
  });

  it("convierte segmentos a puntos de canvas", () => {
    const points = segmentToCanvasPoints(
      {
        tipo: "G1",
        tipo_movimiento: "movimiento_lineal",
        numero_linea: 12,
        inicio_x_mm: 0,
        inicio_y_mm: 0,
        fin_x_mm: 4,
        fin_y_mm: 2,
        z_mm: -0.1,
        avance_mm_min: 120,
        distancia_mm: 4.5,
        advertencias: [],
        puntos: [
          { x_mm: 0, y_mm: 0 },
          { x_mm: 4, y_mm: 2 },
        ],
        desde: { x_mm: 0, y_mm: 0 },
        hasta: { x_mm: 4, y_mm: 2 },
      },
      { scale: 20, panX: 10, panY: 200 }
    );
    expect(points).toEqual([10, 200, 90, 160]);
  });

  it("obtiene el rectángulo visible en coordenadas de mundo", () => {
    const rect = getVisibleWorldRect({ scale: 10, panX: 20, panY: 120 }, { width: 200, height: 100 });
    expect(rect.minX).toBeCloseTo(-2);
    expect(rect.maxX).toBeCloseTo(18);
    expect(rect.minY).toBeCloseTo(2);
    expect(rect.maxY).toBeCloseTo(12);
  });
});
