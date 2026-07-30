import { useEffect, useMemo, useRef, useState } from "react";
import { Circle, Layer, Line, Rect, Stage, Text } from "react-konva";

import { formatCoordinate, formatMillimeters } from "../../lib/format";
import type { HeightMap, HeightMapSample, HeightMapSurfacePoint, Material, PhysicalMapExclusion, PhysicalMeshPoint, ProbeRegion } from "../../types";
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
  heightMap: HeightMap | null;
  mode: "bruto" | "plano" | "residuo";
  meshPoints?: PhysicalMeshPoint[];
  exclusions?: PhysicalMapExclusion[];
  probeRegion?: ProbeRegion | null;
  coordinateMode?: "local" | "machine";
  machineOrigin?: { x_mm: number; y_mm: number } | null;
  previewMessage?: string | null;
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
  mesh: boolean;
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

export function HeightMapHeatmap({ material, heightMap, mode, meshPoints = [], exclusions = [], probeRegion = null, coordinateMode = "local", machineOrigin = null, previewMessage = null }: HeightMapHeatmapProps) {
  const fullscreenRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<DragState | null>(null);
  const [stageNode, setStageNode] = useState<HTMLDivElement | null>(null);
  const viewport = useMeasuredViewport(stageNode);
  const [transform, setTransform] = useState<ViewTransform>({ scale: 8, panX: VIEWER_MARGIN, panY: 480 });
  const [cursor, setCursor] = useState<{ x: number; y: number } | null>(null);
  const [hoverPoint, setHoverPoint] = useState<HeightMapSurfacePoint | HeightMapSample | null>(null);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [layerPanelOpen, setLayerPanelOpen] = useState(false);
  const [layers, setLayers] = useState<LayerVisibility>({
    material: true,
    probeRegion: true,
    exclusions: true,
    samples: true,
    surface: true,
    mesh: true,
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

  const activeProbeRegion = probeRegion ?? heightMap?.probe_region ?? { min_x_mm: 0, min_y_mm: 0, max_x_mm: material.ancho_mm, max_y_mm: material.alto_mm };
  const surface = heightMap?.superficies[mode] ?? { filas: 0, columnas: 0, modo: mode, puntos: [] };
  const samples = useMemo(() => heightMap?.muestras ?? [], [heightMap]);
  const values = surface.puntos.map((point) => point.z_mm).filter((value): value is number => value != null);
  const minValue = values.length > 0 ? Math.min(...values) : 0;
  const maxValue = values.length > 0 ? Math.max(...values) : 1;
  const gridStep = chooseGridStep(transform.scale);
  const visibleRect = getVisibleWorldRect(transform, viewport);
  const gridTicks = buildGridTicks(visibleRect, gridStep);
  const minorGridTicks = buildGridTicks(visibleRect, Math.max(gridStep / 5, 0.1));
  const cellWidth = surface.columnas > 1 ? activeProbeRegion.max_x_mm - activeProbeRegion.min_x_mm : material.ancho_mm;
  const cellHeight = surface.filas > 1 ? activeProbeRegion.max_y_mm - activeProbeRegion.min_y_mm : material.alto_mm;
  const denseCellWidth = surface.columnas > 1 ? cellWidth / (surface.columnas - 1) : cellWidth;
  const denseCellHeight = surface.filas > 1 ? cellHeight / (surface.filas - 1) : cellHeight;
  const minSample = useMemo(() => findExtreme(samples, "min"), [samples]);
  const maxSample = useMemo(() => findExtreme(samples, "max"), [samples]);
  const outliers = samples.filter((sample) => sample.estado_calidad === "atipica");
  const hasMachineCoordinates = meshPoints.some((point) => typeof point.x_machine === "number" && typeof point.y_machine === "number");
  const visibleMeshPoints = meshPoints.map((point) => {
    const useMachine = coordinateMode === "machine" && typeof point.x_machine === "number" && typeof point.y_machine === "number";
    return {
      x: useMachine ? Number(point.x_machine) : Number(point.x_local),
      y: useMachine ? Number(point.y_machine) : Number(point.y_local),
      status: String(point.status ?? "PENDING"),
      role: String(point.role ?? "GRID"),
      index: Number(point.index ?? 0),
    };
  }).filter((point) => Number.isFinite(point.x) && Number.isFinite(point.y));
  const meshLinePoints = visibleMeshPoints.flatMap((point) => { const screen = worldToScreen({ x: point.x, y: point.y }, transform); return [screen.x, screen.y]; });
  const activeMeshPoint = visibleMeshPoints.find((point) => point.status === "MOVING" || point.status === "PROBING")
    ?? visibleMeshPoints.find((point) => point.status !== "MEASURED" && point.status !== "SKIPPED" && point.status !== "EXCLUDED")
    ?? null;
  const offsetX = coordinateMode === "machine" ? machineOrigin?.x_mm ?? 0 : 0;
  const offsetY = coordinateMode === "machine" ? machineOrigin?.y_mm ?? 0 : 0;
  const world = (x: number, y: number) => ({ x: x + offsetX, y: y + offsetY });
  const visibleExclusions = exclusions.length > 0 ? exclusions : (heightMap?.exclusion_zones ?? []).map((zone) => ({ id: zone.id, name: zone.nombre, shape: "rectangle" as const, enabled: true, x_min_mm: zone.min_x_mm, x_max_mm: zone.max_x_mm, y_min_mm: zone.min_y_mm, y_max_mm: zone.max_y_mm }));

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
    minX: activeProbeRegion.min_x_mm + offsetX,
    maxX: activeProbeRegion.max_x_mm + offsetX,
    minY: activeProbeRegion.min_y_mm + offsetY,
    maxY: activeProbeRegion.max_y_mm + offsetY,
  }, { width: viewport.width, height: viewport.height }));
  const focusAll = () => {
    const rects = [
      rectFromMaterial(material),
      { minX: activeProbeRegion.min_x_mm + offsetX, maxX: activeProbeRegion.max_x_mm + offsetX, minY: activeProbeRegion.min_y_mm + offsetY, maxY: activeProbeRegion.max_y_mm + offsetY },
    ].filter((item): item is { minX: number; maxX: number; minY: number; maxY: number } => Boolean(item));
    setTransform(fitRectWithinViewport({
      minX: Math.min(...rects.map((item) => item.minX)),
      maxX: Math.max(...rects.map((item) => item.maxX)),
      minY: Math.min(...rects.map((item) => item.minY)),
      maxY: Math.max(...rects.map((item) => item.maxY)),
    }, { width: viewport.width, height: viewport.height }));
  };
  const focusOneToOne = () => setTransform({ scale: 1, panX: VIEWER_MARGIN, panY: viewport.height - VIEWER_MARGIN });

  return (
    <section className="heightmap-viewer" ref={fullscreenRef}>
      <div className="viewer-header viewer-header--heatmap">
        <div>
          <h3>Mapa de alturas 2D</h3>
          <p className="muted">Ejes, escala en milímetros, región, exclusiones y recorrido de sondeo.</p>
        </div>
        <div className="heightmap-toolbar" role="toolbar" aria-label="Controles del visor 2D">
          <div className="heightmap-toolbar__group">
            <button className="button button--ghost" type="button" onClick={focusMaterial}>Material</button>
            <button className="button button--ghost" type="button" onClick={focusProbeRegion}>Malla</button>
            <button className="button button--ghost" type="button" onClick={focusAll}>Todo</button>
            <button className="button button--ghost" type="button" onClick={focusOneToOne}>1:1</button>
          </div>
          <div className="heightmap-toolbar__group">
            <button className={`button button--ghost${layerPanelOpen ? " toolbar-pill--active" : ""}`} type="button" onClick={() => setLayerPanelOpen((current) => !current)}>Capas</button>
            <button className={`button button--ghost${inspectorOpen ? " toolbar-pill--active" : ""}`} type="button" onClick={() => setInspectorOpen((current) => !current)}>Inspector</button>
            <button className="button button--ghost" type="button" onClick={() => void toggleFullscreen()}>
              {isFullscreen ? "Cerrar" : "Pantalla completa"}
            </button>
          </div>
        </div>
      </div>

      {layerPanelOpen ? (
        <div className="heightmap-legend-row" aria-label="Capas del mapa de alturas">
          <label className="heightmap-legend-item">
            <input type="checkbox" checked={layers.material} onChange={() => setLayers((current) => ({ ...current, material: !current.material }))} />
            <span className="legend-swatch legend-swatch--material" />
            <span>Material</span>
          </label>
          <label className="heightmap-legend-item">
            <input type="checkbox" checked={layers.probeRegion} onChange={() => setLayers((current) => ({ ...current, probeRegion: !current.probeRegion }))} />
            <span className="legend-swatch legend-swatch--probe" />
            <span>Región</span>
          </label>
          <label className="heightmap-legend-item">
            <input type="checkbox" checked={layers.exclusions} onChange={() => setLayers((current) => ({ ...current, exclusions: !current.exclusions }))} />
            <span className="legend-swatch legend-swatch--excluded" />
            <span>Exclusiones</span>
          </label>
          <label className="heightmap-legend-item">
            <input type="checkbox" checked={layers.samples} onChange={() => setLayers((current) => ({ ...current, samples: !current.samples }))} />
            <span className="legend-point" />
            <span>Muestras</span>
          </label>
          <label className="heightmap-legend-item">
            <input type="checkbox" checked={layers.surface} onChange={() => setLayers((current) => ({ ...current, surface: !current.surface }))} />
            <span className="legend-gradient" />
            <span>Superficie</span>
          </label>
          <label className="heightmap-legend-item">
            <input type="checkbox" checked={layers.mesh} onChange={() => setLayers((current) => ({ ...current, mesh: !current.mesh }))} />
            <span className="legend-point legend-point--mesh" />
            <span>Malla</span>
          </label>
        </div>
      ) : null}

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
              {minorGridTicks.x.map((value) => {
                const screen = worldToScreen({ x: value, y: 0 }, transform);
                return <Rect key={`mhx-${value}`} x={screen.x} y={0} width={1} height={viewport.height} fill="rgba(111,144,168,0.045)" />;
              })}
              {minorGridTicks.y.map((value) => {
                const screen = worldToScreen({ x: 0, y: value }, transform);
                return <Rect key={`mhy-${value}`} x={0} y={screen.y} width={viewport.width} height={1} fill="rgba(111,144,168,0.045)" />;
              })}
              {gridTicks.x.map((value) => {
                const screen = worldToScreen({ x: value, y: 0 }, transform);
                return <Rect key={`hx-${value}`} x={screen.x} y={0} width={1} height={viewport.height} fill="rgba(111,144,168,0.16)" />;
              })}
              {gridTicks.y.map((value) => {
                const screen = worldToScreen({ x: 0, y: value }, transform);
                return <Rect key={`hy-${value}`} x={0} y={screen.y} width={viewport.width} height={1} fill="rgba(111,144,168,0.16)" />;
              })}
              <Rect x={worldToScreen(world(0, 0), transform).x} y={0} width={2} height={viewport.height} fill="rgba(255,255,255,0.42)" />
              <Rect x={0} y={worldToScreen(world(0, 0), transform).y} width={viewport.width} height={2} fill="rgba(255,255,255,0.42)" />

              {layers.surface
                ? surface.puntos.map((point) => {
                    const topLeft = worldToScreen(world(point.x_mm, Math.min(activeProbeRegion.max_y_mm, point.y_mm + denseCellHeight)), transform);
                    const bottomRight = worldToScreen(world(Math.min(activeProbeRegion.max_x_mm, point.x_mm + denseCellWidth), point.y_mm), transform);
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
                    const bottomLeft = worldToScreen(world(0, 0), transform);
                    const topRight = worldToScreen(world(material.ancho_mm, material.alto_mm), transform);
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
                    const bottomLeft = worldToScreen(world(activeProbeRegion.min_x_mm, activeProbeRegion.min_y_mm), transform);
                    const topRight = worldToScreen(world(activeProbeRegion.max_x_mm, activeProbeRegion.max_y_mm), transform);
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
                ? visibleExclusions.filter((zone) => zone.enabled).map((zone) => {
                    if (zone.shape === "circle") {
                      const center = worldToScreen(world(Number(zone.center_x_mm ?? 0), Number(zone.center_y_mm ?? 0)), transform);
                      return <Circle key={zone.id} x={center.x} y={center.y} radius={Math.max(2, Number(zone.radius_mm ?? 0) * transform.scale)} fill="rgba(255, 122, 122, 0.12)" stroke="#ff7a7a" strokeWidth={1.5} />;
                    }
                    const bottomLeft = worldToScreen(world(Number(zone.x_min_mm ?? 0), Number(zone.y_min_mm ?? 0)), transform);
                    const topRight = worldToScreen(world(Number(zone.x_max_mm ?? 0), Number(zone.y_max_mm ?? 0)), transform);
                    return (
                      <Rect key={zone.id} x={bottomLeft.x} y={topRight.y} width={Math.max(1, topRight.x - bottomLeft.x)} height={Math.max(1, bottomLeft.y - topRight.y)} fill="rgba(255, 122, 122, 0.12)" stroke="#ff7a7a" strokeWidth={1.5} />
                    );
                  })
                : null}

              {layers.samples
                ? samples.map((sample) => {
                    const screen = worldToScreen(world(sample.x_mm, sample.y_mm), transform);
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
                const screen = worldToScreen(world(safeSample.x_mm, safeSample.y_mm), transform);
                return <Circle key={`marker-${index}`} x={screen.x} y={screen.y} radius={7} stroke="#ffffff" strokeWidth={1.2} />;
              })}



              {layers.mesh && visibleMeshPoints.length > 1 ? <Line points={meshLinePoints} stroke="#ffffff" strokeWidth={1.2} dash={[4, 5]} opacity={0.75} /> : null}
              {layers.mesh ? visibleMeshPoints.map((point) => {
                const screen = worldToScreen({ x: point.x, y: point.y }, transform);
                const measured = point.status === "MEASURED";
                const failed = point.status === "FAILED" || point.status === "RETRY_REQUIRED";
                const excluded = point.status === "EXCLUDED";
                const active = activeMeshPoint?.index === point.index;
                const reference = point.role === "REFERENCE";
                return (
                  <>
                    <Circle
                      key={`mesh-${point.index}`}
                      x={screen.x}
                      y={screen.y}
                      radius={reference ? 7 : active ? 7 : measured ? 5 : 4}
                      fill={reference ? "#64d8ff" : excluded ? "#6c8496" : measured ? "#63d471" : failed ? "#ff7a7a" : "#f6cf73"}
                      stroke={active || reference ? "#ffffff" : "#071015"}
                      strokeWidth={active || reference ? 2.2 : 1.2}
                    />
                    {reference ? <Text key={`mesh-label-${point.index}`} x={screen.x + 8} y={screen.y - 18} text="X0/Y0" fill="#d9f7ff" fontSize={11} /> : null}
                  </>
                );
              }) : null}

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
            Material · región/malla · exclusiones · recorrido serpentino · muestras
          </div>
          <div className="viewer-stage__overlay viewer-stage__overlay--bottom mono-text">
            {cursor ? `${coordinateMode === "machine" && hasMachineCoordinates ? "Máquina" : "PCB"} ${formatCoordinate(cursor.x)}, ${formatCoordinate(cursor.y)} mm` : "Cursor -"}
          </div>
        </div>
      </div>

      {inspectorOpen ? (
        <aside className="heightmap-inspector" aria-label="Inspector del mapa 2D">
          <div className="viewer-footer__meta mono-text">
            Coordenadas: {coordinateMode === "machine" && hasMachineCoordinates ? "Máquina" : "PCB/local"}
          </div>
          <div className="viewer-footer__meta mono-text">
            {heightMap ? <>Alturas · mín {formatMillimeters(minValue, 3)} · máx {formatMillimeters(maxValue, 3)} · RMS {formatMillimeters(heightMap.estadisticas.desviacion_rms_respecto_plano_mm, 3)}</> : (previewMessage ?? "Vista previa en coordenadas PCB. Complete la referencia para calcular las coordenadas CNC.")}
          </div>
          <div className="viewer-footer__meta mono-text">
            Malla · {visibleMeshPoints.length} puntos · activo {activeMeshPoint ? `#${activeMeshPoint.index}` : "-"}
          </div>
          <div className="viewer-footer__meta mono-text">
            {hoverPoint && "z_mm" in hoverPoint
              ? `X ${formatMillimeters(hoverPoint.x_mm, 3)} · Y ${formatMillimeters(hoverPoint.y_mm, 3)} · Z ${formatMillimeters(hoverPoint.z_mm, 4)}`
              : "Mueva el cursor sobre muestras o superficie para ver X/Y/Z."}
          </div>
        </aside>
      ) : null}
    </section>
  );
}
