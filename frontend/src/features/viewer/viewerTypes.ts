import type { Bounds, Material, PreviewSegment } from "../../types";

export type ViewerRect = {
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
};

export type ViewportSize = {
  width: number;
  height: number;
};

export type ViewTransform = {
  scale: number;
  panX: number;
  panY: number;
};

export type ScreenPoint = {
  x: number;
  y: number;
};

export type WorldPoint = {
  x: number;
  y: number;
};

export type ViewerFitMode = "material" | "toolpath" | "all";

export type ViewerLayersState = {
  material: boolean;
  grid: boolean;
  g0: boolean;
  g1: boolean;
  arcs: boolean;
  origin: boolean;
  startPoint: boolean;
  endPoint: boolean;
  warnings: boolean;
  bounds: boolean;
};

export type ViewerSegmentRecord = {
  index: number;
  segment: PreviewSegment;
  points: number[];
  outsideMaterial: boolean;
};

export const defaultViewerLayers: ViewerLayersState = {
  material: true,
  grid: true,
  g0: true,
  g1: true,
  arcs: true,
  origin: true,
  startPoint: true,
  endPoint: true,
  warnings: true,
  bounds: true,
};

export function rectFromMaterial(material: Material): ViewerRect {
  return {
    minX: 0,
    maxX: material.ancho_mm,
    minY: 0,
    maxY: material.alto_mm,
  };
}

export function rectFromBounds(bounds: Bounds | null): ViewerRect | null {
  if (!bounds) {
    return null;
  }
  return {
    minX: bounds.min_x_mm,
    maxX: bounds.max_x_mm,
    minY: bounds.min_y_mm,
    maxY: bounds.max_y_mm,
  };
}
