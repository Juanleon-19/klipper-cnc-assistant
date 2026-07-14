import type { Material, PreviewSegment } from "../../types";
import type { ScreenPoint, ViewTransform, ViewerRect, ViewportSize, WorldPoint } from "./viewerTypes";

export const VIEWER_MARGIN = 36;
export const MIN_SCALE = 0.4;
export const MAX_SCALE = 120;

export function clampScale(scale: number): number {
  return Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale));
}

export function unionRects(...rects: Array<ViewerRect | null>): ViewerRect | null {
  const valid = rects.filter((rect): rect is ViewerRect => Boolean(rect));
  if (valid.length === 0) {
    return null;
  }
  return valid.reduce(
    (accumulator, rect) => ({
      minX: Math.min(accumulator.minX, rect.minX),
      maxX: Math.max(accumulator.maxX, rect.maxX),
      minY: Math.min(accumulator.minY, rect.minY),
      maxY: Math.max(accumulator.maxY, rect.maxY),
    }),
    valid[0]
  );
}

export function expandRect(rect: ViewerRect, padding: number): ViewerRect {
  return {
    minX: rect.minX - padding,
    maxX: rect.maxX + padding,
    minY: rect.minY - padding,
    maxY: rect.maxY + padding,
  };
}

export function rectWidth(rect: ViewerRect): number {
  return Math.max(0.0001, rect.maxX - rect.minX);
}

export function rectHeight(rect: ViewerRect): number {
  return Math.max(0.0001, rect.maxY - rect.minY);
}

export function fitRectWithinViewport(rect: ViewerRect, viewport: ViewportSize, margin = VIEWER_MARGIN): ViewTransform {
  const usableWidth = Math.max(1, viewport.width - margin * 2);
  const usableHeight = Math.max(1, viewport.height - margin * 2);
  const scale = clampScale(Math.min(usableWidth / rectWidth(rect), usableHeight / rectHeight(rect)));
  const extraX = (viewport.width - margin * 2 - rectWidth(rect) * scale) / 2;
  const extraY = (viewport.height - margin * 2 - rectHeight(rect) * scale) / 2;
  return {
    scale,
    panX: margin + extraX - rect.minX * scale,
    panY: margin + extraY + rect.maxY * scale,
  };
}

export function worldToScreen(point: WorldPoint, transform: ViewTransform): ScreenPoint {
  return {
    x: transform.panX + point.x * transform.scale,
    y: transform.panY - point.y * transform.scale,
  };
}

export function screenToWorld(point: ScreenPoint, transform: ViewTransform): WorldPoint {
  return {
    x: (point.x - transform.panX) / transform.scale,
    y: (transform.panY - point.y) / transform.scale,
  };
}

export function getVisibleWorldRect(transform: ViewTransform, viewport: ViewportSize): ViewerRect {
  const topLeft = screenToWorld({ x: 0, y: 0 }, transform);
  const bottomRight = screenToWorld({ x: viewport.width, y: viewport.height }, transform);
  return {
    minX: Math.min(topLeft.x, bottomRight.x),
    maxX: Math.max(topLeft.x, bottomRight.x),
    minY: Math.min(bottomRight.y, topLeft.y),
    maxY: Math.max(bottomRight.y, topLeft.y),
  };
}

export function zoomAtPoint(transform: ViewTransform, point: ScreenPoint, factor: number): ViewTransform {
  const worldBefore = screenToWorld(point, transform);
  const scale = clampScale(transform.scale * factor);
  return {
    scale,
    panX: point.x - worldBefore.x * scale,
    panY: point.y + worldBefore.y * scale,
  };
}

export function chooseGridStep(scale: number): number {
  const desiredMm = 80 / Math.max(scale, 0.0001);
  const steps = [0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200];
  return steps.find((step) => step >= desiredMm) ?? 500;
}

export function buildGridTicks(rect: ViewerRect, step: number): { x: number[]; y: number[] } {
  const xs: number[] = [];
  const ys: number[] = [];
  const startX = Math.floor(rect.minX / step) * step;
  const endX = Math.ceil(rect.maxX / step) * step;
  const startY = Math.floor(rect.minY / step) * step;
  const endY = Math.ceil(rect.maxY / step) * step;

  for (let value = startX; value <= endX + step / 2; value += step) {
    xs.push(Number(value.toFixed(6)));
  }
  for (let value = startY; value <= endY + step / 2; value += step) {
    ys.push(Number(value.toFixed(6)));
  }

  return { x: xs, y: ys };
}

export function isSegmentOutsideMaterial(segment: PreviewSegment, material: Material): boolean {
  return segment.puntos.some(
    (point) => point.x_mm < 0 || point.y_mm < 0 || point.x_mm > material.ancho_mm || point.y_mm > material.alto_mm
  );
}

export function segmentToCanvasPoints(segment: PreviewSegment, transform: ViewTransform): number[] {
  const points = segment.puntos.length > 0 ? segment.puntos : [segment.desde, segment.hasta];
  return points.flatMap((point) => {
    const screen = worldToScreen({ x: point.x_mm, y: point.y_mm }, transform);
    return [screen.x, screen.y];
  });
}

export function resolveAutoFitMode(hasToolpath: boolean, pathFitsMaterial: boolean | null): "material" | "toolpath" | "all" {
  if (!hasToolpath) {
    return "material";
  }
  if (pathFitsMaterial === false) {
    return "all";
  }
  return "toolpath";
}
