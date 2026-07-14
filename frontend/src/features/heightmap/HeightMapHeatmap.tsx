import { useEffect, useMemo, useRef, useState } from "react";
import { Circle, Layer, Rect, Stage, Text } from "react-konva";

import { formatCoordinate, formatMillimeters } from "../../lib/format";
import type { Bounds, HeightMap, HeightMapSample, HeightMapSurfacePoint, Material } from "../../types";
import { useMeasuredViewport } from "../viewer/useMeasuredViewport";
import {
  buildGridTicks,
  chooseGridStep,
  fitRectWithinViewport,
  getVisibleWorldRect,
  screenToWorld,
  VIEWER_MARGIN,
  worldToScreen,
  zoomAtPoint,
} from "../viewer/viewerMath";
import { rectFromMaterial } from "../viewer/viewerTypes";
import type { ViewTransform } from "../viewer/viewerTypes";

type HeightMapHeatmapProps = {
  material: Material;
  heightMap: HeightMap;
  mode: "bruto" | "plano" | "residuo";
  toolpathBounds?: Bounds | null;
};

type DragState = {
  x: number;
  y: number;
  panX: number;
  panY: number;
};

type LayerVisibility = {
  material: boolean;
  probeRegion: boolean;
  exclusions: boolean;
  samples: boolean;
  surface: boolean;
};

function buildTransform(material: Material, width: number, height: number): ViewTransform {
  return fitRectWithinViewport(rectFromMaterial(material), { width, height });
}

function colorScale(value: number | null, min: number, max: number): string {
  if (value == null || Number.isNaN(value)) {
    return "rgba(78, 92, 106, 0.28)";
  }
  const span = Math.max(0.0001, max - min);
  const ratio = Math.max(0, Math.min(1, (value - min) / span));
  const hue = 220 - ratio * 220;
  return `hsl(${hue} 74% 58%)`;
}

function findExtreme(samples: HeightMapSample[], direction: "min" | "max") {
  return samples
    .filter((sample) => sample.z_mm != null && sample.incluida)
    .sort((left, right) => (direction === "min" ? (left.z_mm ?? 0) - (right.z_mm ?? 0) : (right.z_mm ?? 0) - (left.z_mm ?? 0)))[0] ?? null;
}

export function HeightMapHeatmap({ material, heightMap, mode, toolpathBounds = null }: HeightMapHeatmapProps) {
  const fullscreenRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<DragState | null>(null);
  const [stageNode, setStageNode] = useState<HTMLDivElement | null>(null);
  const viewport = useMeasuredViewport(stageNode);
  const [transform, setTransform] = useState<ViewTransform>({ scale: 8, panX: VIEWER_MARGIN, panY: 480 });
  const [cursor, setCursor] = useState<{ x: number; y: number } | null>(null);
  const [hoverPoint, setHoverPoint] = useState<HeightMapSurfacePoint | HeightMapSample | null>(null);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [layers, setLayers] = useState<LayerVisibility>({
    material: true,
    probeRegion: true,
    exclusions: true,
    samples: true,
    surface: true,
  });

  useEffect(() => {
    setTransform(buildTransform(material, viewport.width, viewport.height));
  }, [material, viewport.height, viewport.width]);

  useEffect(() => {
    const handleChange = () => {
      setIsFullscreen(document.fullscreenElement === fullscreenRef.current);
    };
    document.addEventListener("fullscreenchange", handleChange);
    return () => document.removeEventListener("fullscreenchange", handleChange);
  }, []);

  const surface = heightMap.superficies[mode];
  const values = surface.puntos.map((point) => point.z_mm).filter((value): value is number => value != null);
  const minValue = values.length > 0 ? Math.min(...values) : 0;
  const maxValue = values.length > 0 ? Math.max(...values) : 1;
  const gridStep = chooseGridStep(transform.scale);
  const visibleRect = getVisibleWorldRect(transform, viewport);
  const gridTicks = buildGridTicks(visibleRect, gridStep);
  const cellWidth = surface.columnas > 1 ? heightMap.probe_region.max_x_mm - heightMap.probe_region.min_x_mm : material.ancho_mm;
  const cellHeight = surface.filas > 1 ? heightMap.probe_region.max_y_mm - heightMap.probe_region.min_y_mm : material.alto_mm;
  const denseCellWidth = surface.columnas > 1 ? cellWidth / (surface.columnas - 1) : cellWidth;
  const denseCellHeight = surface.filas > 1 ? cellHeight / (surface.filas - 1) : cellHeight;
  const minSample = useMemo(() => findExtreme(heightMap.muestras, "min"), [heightMap.muestras]);
  const maxSample = useMemo(() => findExtreme(heightMap.muestras, "max"), [heightMap.muestras]);
  const outliers = heightMap.muestras.filter((sample) => sample.estado_calidad === "atipica");

  const toggleFullscreen = async () => {
    const node = fullscreenRef.current;
    if (!node) {
      return;
    }
    if (document.fullscreenElement) {
      await document.exitFullscreen();
      return;
    }
    await node.requestFullscreen?.();
  };

  const focusMaterial = () => setTransform(buildTransform(material, viewport.width, viewport.height));
  const focusProbeRegion = () => setTransform(fitRectWithinViewport({
    minX: heightMap.probe_region.min_x_mm,
    maxX: heightMap.probe_region.max_x_mm,
    minY: heightMap.probe_region.min_y_mm,
    maxY: heightMap.probe_region.max_y_mm,
  }, { width: viewport.width, height: viewport.height }));
  const focusToolpath = () => {
    if (!toolpathBounds) {
      focusProbeRegion();
      return;
    }
    setTransform(fitRectWithinViewport({
      minX: toolpathBounds.min_x_mm,
      maxX: toolpathBounds.max_x_mm,
      minY: toolpathBounds.min_y_mm,
      maxY: toolpathBounds.max_y_mm,
    }, { width: viewport.width, height: viewport.height }));
  };

  return (
    <section className="heightmap-viewer" ref={fullscreenRef}>
      <div className="viewer-header viewer-header--heatmap">
        <div>
          <h3>Mapa de alturas 2D</h3>
          <p className="muted">Muestra simultáneamente material bruto, región sondeable, zonas excluidas, muestras y superficie interpolada.</p>
        </div>
        <div className="heightmap-toolbar">
          <div className="heightmap-toolbar__group">
            <button className="button button--ghost" type="button" onClick={focusMaterial}>Encuadrar material</button>
            <button className="button button--ghost" type="button" onClick={focusProbeRegion}>Encuadrar región</button>
            <button className="button button--ghost" type="button" onClick={focusToolpath}>Encuadrar trayectoria</button>
          </div>
          <div className="heightmap-toolbar__group">
            <button className="button button--ghost" type="button" onClick={() => void toggleFullscreen()}>
              {isFullscreen ? "Salir de pantalla completa" : "Pantalla completa"}
            </button>
          </div>
        </div>
      </div>

      <div className="heightmap-legend-row" aria-label="Capas del mapa de alturas">
        <label className="heightmap-legend-item">
          <input type="checkbox" checked={layers.material} onChange={() => setLayers((current) => ({ ...current, material: !current.material }))} />
          <span className="legend-swatch legend-swatch--material" />
          <span>Contorno material</span>
        </label>
        <label className="heightmap-legend-item">
          <input type="checkbox" checked={layers.probeRegion} onChange={() => setLayers((current) => ({ ...current, probeRegion: !current.probeRegion }))} />
          <span className="legend-swatch legend-swatch--probe" />
          <span>Región sondeable</span>
        </label>
        <label className="heightmap-legend-item">
          <input type="checkbox" checked={layers.exclusions} onChange={() => setLayers((current) => ({ ...current, exclusions: !current.exclusions }))} />
          <span className="legend-swatch legend-swatch--excluded" />
          <span>Zona excluida</span>
        </label>
        <label className="heightmap-legend-item">
          <input type="checkbox" checked={layers.samples} onChange={() => setLayers((current) => ({ ...current, samples: !current.samples }))} />
          <span className="legend-point" />
          <span>Muestra</span>
        </label>
        <label className="heightmap-legend-item">
          <input type="checkbox" checked={layers.surface} onChange={() => setLayers((current) => ({ ...current, surface: !current.surface }))} />
          <span className="legend-gradient" />
          <span>Superficie</span>
        </label>
      </div>

      <div className="viewer-stage-shell">
        <div className="viewer-stage viewer-stage--heatmap" ref={setStageNode}>
          <Stage
            width={viewport.width}
            height={viewport.height}
            pixelRatio={window.devicePixelRatio || 1}
            onWheel={(event) => {
              event.evt.preventDefault();
              const pointer = event.target.getStage()?.getPointerPosition();
              if (!pointer) {
                return;
              }
              const factor = event.evt.deltaY < 0 ? 1.12 : 1 / 1.12;
              setTransform((current) => zoomAtPoint(current, pointer, factor));
            }}
            onMouseDown={(event) => {
              if (event.target === event.target.getStage()) {
                dragRef.current = {
                  x: event.evt.clientX,
                  y: event.evt.clientY,
                  panX: transform.panX,
                  panY: transform.panY,
                };
              }
            }}
            onMouseMove={(event) => {
              const pointer = event.target.getStage()?.getPointerPosition() ?? null;
              if (pointer) {
                setCursor(screenToWorld(pointer, transform));
              }
              if (dragRef.current) {
                setTransform((current) => ({
                  ...current,
                  panX: dragRef.current!.panX + (event.evt.clientX - dragRef.current!.x),
                  panY: dragRef.current!.panY + (event.evt.clientY - dragRef.current!.y),
                }));
              }
            }}
            onMouseUp={() => {
              dragRef.current = null;
            }}
            onMouseLeave={() => {
              dragRef.current = null;
            }}
            onDblClick={focusMaterial}
          >
            <Layer>
              <Rect x={0} y={0} width={viewport.width} height={viewport.height} fill="#091015" />
              {gridTicks.x.map((value) => {
                const screen = worldToScreen({ x: value, y: 0 }, transform);
                return <Rect key={`hx-${value}`} x={screen.x} y={0} width={1} height={viewport.height} fill="rgba(111,144,168,0.12)" />;
              })}
              {gridTicks.y.map((value) => {
                const screen = worldToScreen({ x: 0, y: value }, transform);
                return <Rect key={`hy-${value}`} x={0} y={screen.y} width={viewport.width} height={1} fill="rgba(111,144,168,0.12)" />;
              })}

              {layers.surface
                ? surface.puntos.map((point) => {
                    const topLeft = worldToScreen({ x: point.x_mm, y: Math.min(heightMap.probe_region.max_y_mm, point.y_mm + denseCellHeight) }, transform);
                    const bottomRight = worldToScreen({ x: Math.min(heightMap.probe_region.max_x_mm, point.x_mm + denseCellWidth), y: point.y_mm }, transform);
                    return (
                      <Rect
                        key={`${point.fila}-${point.columna}`}
                        x={topLeft.x}
                        y={topLeft.y}
                        width={Math.max(1, bottomRight.x - topLeft.x)}
                        height={Math.max(1, bottomRight.y - topLeft.y)}
                        fill={colorScale(point.z_mm, minValue, maxValue)}
                        opacity={point.estado === "ok" ? 0.88 : 0.22}
                        onMouseEnter={() => setHoverPoint(point)}
                        onMouseLeave={() => setHoverPoint((current) => (current === point ? null : current))}
                      />
                    );
                  })
                : null}

              {layers.material
                ? (() => {
                    const bottomLeft = worldToScreen({ x: 0, y: 0 }, transform);
                    const topRight = worldToScreen({ x: material.ancho_mm, y: material.alto_mm }, transform);
                    return (
                      <Rect
                        x={bottomLeft.x}
                        y={topRight.y}
                        width={Math.max(1, topRight.x - bottomLeft.x)}
                        height={Math.max(1, bottomLeft.y - topRight.y)}
                        stroke="#8fb5c8"
                        strokeWidth={2}
                        dash={[8, 4]}
                      />
                    );
                  })()
                : null}

              {layers.probeRegion
                ? (() => {
                    const bottomLeft = worldToScreen({ x: heightMap.probe_region.min_x_mm, y: heightMap.probe_region.min_y_mm }, transform);
                    const topRight = worldToScreen({ x: heightMap.probe_region.max_x_mm, y: heightMap.probe_region.max_y_mm }, transform);
                    return (
                      <Rect
                        x={bottomLeft.x}
                        y={topRight.y}
                        width={Math.max(1, topRight.x - bottomLeft.x)}
                        height={Math.max(1, bottomLeft.y - topRight.y)}
                        stroke="#f6cf73"
                        strokeWidth={2}
                      />
                    );
                  })()
                : null}

              {layers.exclusions
                ? heightMap.exclusion_zones.map((zone) => {
                    const bottomLeft = worldToScreen({ x: zone.min_x_mm, y: zone.min_y_mm }, transform);
                    const topRight = worldToScreen({ x: zone.max_x_mm, y: zone.max_y_mm }, transform);
                    return (
                      <Rect
                        key={zone.id}
                        x={bottomLeft.x}
                        y={topRight.y}
                        width={Math.max(1, topRight.x - bottomLeft.x)}
                        height={Math.max(1, bottomLeft.y - topRight.y)}
                        fill="rgba(255, 122, 122, 0.12)"
                        stroke="#ff7a7a"
                        strokeWidth={1.5}
                      />
                    );
                  })
                : null}

              {layers.samples
                ? heightMap.muestras.map((sample) => {
                    const screen = worldToScreen({ x: sample.x_mm, y: sample.y_mm }, transform);
                    const isProblem = sample.estado_calidad === "atipica" || sample.estado_calidad === "faltante" || !sample.incluida;
                    return (
                      <Circle
                        key={sample.id}
                        x={screen.x}
                        y={screen.y}
                        radius={isProblem ? 4.8 : 3.5}
                        fill={
                          sample.estado_calidad === "faltante"
                            ? "#f6cf73"
                            : sample.estado_calidad === "atipica"
                              ? "#ff7a7a"
                              : !sample.incluida
                                ? "#6c8496"
                                : "#f3f8fd"
                        }
                        stroke="rgba(7,12,15,0.9)"
                        strokeWidth={1}
                        onMouseEnter={() => setHoverPoint(sample)}
                        onMouseLeave={() => setHoverPoint((current) => (current === sample ? null : current))}
                      />
                    );
                  })
                : null}

              {[minSample, maxSample, ...outliers].filter(Boolean).map((sample, index) => {
                const safeSample = sample as HeightMapSample;
                const screen = worldToScreen({ x: safeSample.x_mm, y: safeSample.y_mm }, transform);
                return <Circle key={`marker-${index}`} x={screen.x} y={screen.y} radius={7} stroke="#ffffff" strokeWidth={1.2} />;
              })}

              {cursor ? (
                <>
                  <Rect x={worldToScreen({ x: cursor.x, y: 0 }, transform).x} y={0} width={1} height={viewport.height} fill="rgba(255,255,255,0.28)" />
                  <Rect x={0} y={worldToScreen({ x: 0, y: cursor.y }, transform).y} width={viewport.width} height={1} fill="rgba(255,255,255,0.28)" />
                </>
              ) : null}

              {gridTicks.x.map((value) => {
                const screen = worldToScreen({ x: value, y: 0 }, transform);
                return <Text key={`tx-${value}`} x={screen.x + 4} y={viewport.height - 20} text={`${Math.round(value)} mm`} fill="#b9ceda" fontSize={11} />;
              })}
              {gridTicks.y.map((value) => {
                const screen = worldToScreen({ x: 0, y: value }, transform);
                return <Text key={`ty-${value}`} x={8} y={screen.y - 8} text={`${Math.round(value)} mm`} fill="#b9ceda" fontSize={11} />;
              })}
              <Text x={12} y={10} text="Y (mm)" fill="#c6d7e3" fontSize={12} />
              <Text x={viewport.width - 60} y={viewport.height - 24} text="X (mm)" fill="#c6d7e3" fontSize={12} />
            </Layer>
          </Stage>

          <div className="viewer-stage__overlay viewer-stage__overlay--top mono-text">
            Material · región sondeable · interpolación · muestras
          </div>
          <div className="viewer-stage__overlay viewer-stage__overlay--bottom mono-text">
            {cursor ? `${formatCoordinate(cursor.x)}, ${formatCoordinate(cursor.y)} mm` : "Cursor -"}
          </div>
        </div>
      </div>

      <div className="viewer-footer">
        <div className="viewer-footer__meta mono-text">
          Alturas de la superficie · mín {formatMillimeters(minValue, 3)} · máx {formatMillimeters(maxValue, 3)} · desviación RMS {formatMillimeters(heightMap.estadisticas.desviacion_rms_respecto_plano_mm, 3)}
        </div>
        <div className="viewer-footer__meta mono-text">
          {hoverPoint && "z_mm" in hoverPoint
            ? `X ${formatMillimeters(hoverPoint.x_mm, 3)} · Y ${formatMillimeters(hoverPoint.y_mm, 3)} · Z ${formatMillimeters(hoverPoint.z_mm, 4)}`
            : "Tooltip X/Y/Z disponible sobre muestras y superficie"}
        </div>
      </div>
    </section>
  );
}
