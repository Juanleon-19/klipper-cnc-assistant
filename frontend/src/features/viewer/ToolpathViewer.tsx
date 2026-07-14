import { useEffect, useMemo, useRef, useState } from "react";
import { Circle, Layer, Line, Rect, Stage, Text } from "react-konva";

import { formatMillimeters, formatNumber } from "../../lib/format";
import { translateStatus } from "../../lib/ui";
import type { Material, OperationAnalysis } from "../../types";
import { ViewerInspector } from "./ViewerInspector";
import { ViewerLayers } from "./ViewerLayers";
import { ViewerToolbar } from "./ViewerToolbar";
import {
  buildGridTicks,
  chooseGridStep,
  fitRectWithinViewport,
  getVisibleWorldRect,
  isSegmentOutsideMaterial,
  resolveAutoFitMode,
  screenToWorld,
  segmentToCanvasPoints,
  unionRects,
  VIEWER_MARGIN,
  worldToScreen,
  zoomAtPoint,
} from "./viewerMath";
import { colorForDepth, viewerTheme } from "./viewerTheme";
import { defaultViewerLayers, rectFromBounds, rectFromMaterial } from "./viewerTypes";
import type { ScreenPoint, ViewerFitMode, ViewerLayersState, ViewTransform, ViewportSize, WorldPoint } from "./viewerTypes";

type ToolpathViewerProps = {
  material: Material;
  analysis: OperationAnalysis;
  operationName: string;
};

type DragState = {
  x: number;
  y: number;
  panX: number;
  panY: number;
};

type PinchState = {
  distance: number;
  center: ScreenPoint;
  panX: number;
  panY: number;
  scale: number;
};

function buildFitTransform(mode: ViewerFitMode, material: Material, analysis: OperationAnalysis, viewport: ViewportSize): ViewTransform {
  const materialRect = rectFromMaterial(material);
  const toolpathRect = rectFromBounds(analysis.limites);
  const rect =
    mode === "material"
      ? materialRect
      : mode === "toolpath"
        ? toolpathRect ?? materialRect
        : unionRects(materialRect, toolpathRect) ?? materialRect;
  return fitRectWithinViewport(rect, viewport);
}

export function ToolpathViewer({ material, analysis, operationName }: ToolpathViewerProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<DragState | null>(null);
  const pinchRef = useRef<PinchState | null>(null);
  const [viewport, setViewport] = useState<ViewportSize>({ width: 960, height: 560 });
  const [transform, setTransform] = useState<ViewTransform>({ scale: 6, panX: VIEWER_MARGIN, panY: 520 });
  const [fitMode, setFitMode] = useState<ViewerFitMode>("all");
  const [layers, setLayers] = useState<ViewerLayersState>(defaultViewerLayers);
  const [depthMode, setDepthMode] = useState(false);
  const [traceIndex, setTraceIndex] = useState<number | null>(null);
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);
  const [cursor, setCursor] = useState<WorldPoint | null>(null);
  const hasToolpath = analysis.segmentos_vista_previa.length > 0;

  const autoFitMode = useMemo(
    () => resolveAutoFitMode(hasToolpath, analysis.cabe_en_material),
    [analysis.cabe_en_material, hasToolpath]
  );

  useEffect(() => {
    const node = containerRef.current;
    if (!node) {
      return;
    }
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) {
        return;
      }
      setViewport({
        width: Math.max(320, Math.floor(entry.contentRect.width)),
        height: Math.max(320, Math.floor(entry.contentRect.height)),
      });
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const next = buildFitTransform(autoFitMode, material, analysis, viewport);
    setTransform(next);
    setFitMode(autoFitMode);
    setSelectedIndex(null);
    setHoverIndex(null);
  }, [analysis, autoFitMode, material, viewport]);

  const applyFit = (mode: ViewerFitMode) => {
    setTransform(buildFitTransform(mode, material, analysis, viewport));
    setFitMode(mode);
  };

  const segments = useMemo(() => {
    const visibleSegments = traceIndex == null ? analysis.segmentos_vista_previa : analysis.segmentos_vista_previa.slice(0, traceIndex + 1);
    return visibleSegments.map((segment, index) => ({
      index,
      segment,
      points: segmentToCanvasPoints(segment, transform),
      outsideMaterial: isSegmentOutsideMaterial(segment, material),
    }));
  }, [analysis.segmentos_vista_previa, material, traceIndex, transform]);

  const selectedSegment = selectedIndex != null ? analysis.segmentos_vista_previa[selectedIndex] : hoverIndex != null ? analysis.segmentos_vista_previa[hoverIndex] : null;
  const materialRect = rectFromMaterial(material);
  const toolpathRect = rectFromBounds(analysis.limites);
  const allRect = unionRects(materialRect, toolpathRect);
  const visibleRect = getVisibleWorldRect(transform, viewport);
  const gridStep = chooseGridStep(transform.scale);
  const gridTicks = buildGridTicks(visibleRect, gridStep);
  const startSegment = analysis.segmentos_vista_previa[0] ?? null;
  const endSegment = analysis.segmentos_vista_previa.length > 0
    ? analysis.segmentos_vista_previa[analysis.segmentos_vista_previa.length - 1]
    : null;
  const warningSegmentIndex = analysis.segmentos_vista_previa.findIndex((segment) => segment.advertencias.length > 0);

  const handlePointerMove = (pointer: ScreenPoint | null) => {
    if (!pointer) {
      return;
    }
    setCursor(screenToWorld(pointer, transform));
  };

  const handlePanStart = (event: { clientX: number; clientY: number }) => {
    dragRef.current = {
      x: event.clientX,
      y: event.clientY,
      panX: transform.panX,
      panY: transform.panY,
    };
  };

  const handlePanMove = (event: { clientX: number; clientY: number }) => {
    if (!dragRef.current) {
      return;
    }
    setTransform((current) => ({
      ...current,
      panX: dragRef.current!.panX + (event.clientX - dragRef.current!.x),
      panY: dragRef.current!.panY + (event.clientY - dragRef.current!.y),
    }));
  };

  const renderMaterialRect = () => {
    const topLeft = worldToScreen({ x: materialRect.minX, y: materialRect.maxY }, transform);
    const bottomRight = worldToScreen({ x: materialRect.maxX, y: materialRect.minY }, transform);
    return (
      <Rect
        x={topLeft.x}
        y={topLeft.y}
        width={bottomRight.x - topLeft.x}
        height={bottomRight.y - topLeft.y}
        fill={viewerTheme.materialFill}
        stroke={viewerTheme.materialStroke}
        strokeWidth={1.2}
        cornerRadius={14}
      />
    );
  };

  const renderBoundsRect = () => {
    if (!toolpathRect) {
      return null;
    }
    const topLeft = worldToScreen({ x: toolpathRect.minX, y: toolpathRect.maxY }, transform);
    const bottomRight = worldToScreen({ x: toolpathRect.maxX, y: toolpathRect.minY }, transform);
    return (
      <Rect
        x={topLeft.x}
        y={topLeft.y}
        width={bottomRight.x - topLeft.x}
        height={bottomRight.y - topLeft.y}
        stroke={viewerTheme.boundsStroke}
        dash={[8, 6]}
        strokeWidth={1.2}
      />
    );
  };

  const toggleLayer = (key: keyof ViewerLayersState) => {
    setLayers((current) => ({ ...current, [key]: !current[key] }));
  };

  const toggleFullscreen = async () => {
    const node = containerRef.current;
    if (!node) {
      return;
    }
    if (document.fullscreenElement) {
      await document.exitFullscreen();
      return;
    }
    await node.requestFullscreen?.();
  };

  return (
    <section className="toolpath-viewer-shell">
      <div className="toolpath-viewer-main">
        <div className="viewer-header">
          <div>
            <h3>Visor técnico 2D V2</h3>
            <p className="muted">Visualización informativa. No representa mecanizado real ni envía comandos a la máquina.</p>
          </div>
          <div className="viewer-scale-meta mono-text">Escala {formatNumber(transform.scale, 2)} px/mm</div>
        </div>

        <ViewerToolbar
          activeFitMode={fitMode}
          gridEnabled={layers.grid}
          depthMode={depthMode}
          onZoomIn={() => setTransform((current) => zoomAtPoint(current, { x: viewport.width / 2, y: viewport.height / 2 }, 1.15))}
          onZoomOut={() => setTransform((current) => zoomAtPoint(current, { x: viewport.width / 2, y: viewport.height / 2 }, 1 / 1.15))}
          onFit={applyFit}
          onReset={() => applyFit(autoFitMode)}
          onToggleFullscreen={() => void toggleFullscreen()}
          onToggleGrid={() => toggleLayer("grid")}
          onToggleDepth={() => setDepthMode((current) => !current)}
        />

        <div className="viewer-stage" ref={containerRef}>
          <Stage
            width={viewport.width}
            height={viewport.height}
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
              const pointer = event.target.getStage()?.getPointerPosition() ?? null;
              handlePointerMove(pointer);
              if (event.target === event.target.getStage()) {
                handlePanStart(event.evt);
              }
            }}
            onMouseMove={(event) => {
              const pointer = event.target.getStage()?.getPointerPosition() ?? null;
              handlePointerMove(pointer);
              if (dragRef.current) {
                handlePanMove(event.evt);
              }
            }}
            onMouseUp={() => {
              dragRef.current = null;
            }}
            onMouseLeave={() => {
              dragRef.current = null;
            }}
            onDblClick={() => applyFit(autoFitMode)}
            onTouchStart={(event) => {
              const touches = event.evt.touches;
              if (touches.length === 1) {
                handlePanStart(touches[0]);
              }
              if (touches.length === 2) {
                const center = {
                  x: (touches[0].clientX + touches[1].clientX) / 2,
                  y: (touches[0].clientY + touches[1].clientY) / 2,
                };
                const distance = Math.hypot(touches[0].clientX - touches[1].clientX, touches[0].clientY - touches[1].clientY);
                pinchRef.current = { distance, center, panX: transform.panX, panY: transform.panY, scale: transform.scale };
              }
            }}
            onTouchMove={(event) => {
              const stage = event.target.getStage();
              const pointer = stage?.getPointerPosition() ?? null;
              handlePointerMove(pointer);
              const touches = event.evt.touches;
              if (touches.length === 1 && dragRef.current) {
                handlePanMove(touches[0]);
              }
              if (touches.length === 2 && pinchRef.current) {
                event.evt.preventDefault();
                const center = {
                  x: (touches[0].clientX + touches[1].clientX) / 2,
                  y: (touches[0].clientY + touches[1].clientY) / 2,
                };
                const distance = Math.hypot(touches[0].clientX - touches[1].clientX, touches[0].clientY - touches[1].clientY);
                const factor = distance / Math.max(1, pinchRef.current.distance);
                const interim = { scale: pinchRef.current.scale, panX: pinchRef.current.panX, panY: pinchRef.current.panY };
                const zoomed = zoomAtPoint(interim, center, factor);
                setTransform({
                  scale: zoomed.scale,
                  panX: zoomed.panX + (center.x - pinchRef.current.center.x),
                  panY: zoomed.panY + (center.y - pinchRef.current.center.y),
                });
              }
            }}
            onTouchEnd={() => {
              dragRef.current = null;
              pinchRef.current = null;
            }}
            pixelRatio={window.devicePixelRatio || 1}
          >
            <Layer>
              <Rect x={0} y={0} width={viewport.width} height={viewport.height} fill={viewerTheme.background} />

              {layers.grid
                ? gridTicks.x.map((value) => {
                    const screen = worldToScreen({ x: value, y: 0 }, transform);
                    const isMajor = Math.round(value / gridStep) % 5 === 0;
                    return (
                      <Line
                        key={`grid-x-${value}`}
                        points={[screen.x, 0, screen.x, viewport.height]}
                        stroke={isMajor ? viewerTheme.gridMajor : viewerTheme.gridMinor}
                        strokeWidth={1}
                      />
                    );
                  })
                : null}
              {layers.grid
                ? gridTicks.y.map((value) => {
                    const screen = worldToScreen({ x: 0, y: value }, transform);
                    const isMajor = Math.round(value / gridStep) % 5 === 0;
                    return (
                      <Line
                        key={`grid-y-${value}`}
                        points={[0, screen.y, viewport.width, screen.y]}
                        stroke={isMajor ? viewerTheme.gridMajor : viewerTheme.gridMinor}
                        strokeWidth={1}
                      />
                    );
                  })
                : null}

              {layers.material ? renderMaterialRect() : null}
              {layers.bounds ? renderBoundsRect() : null}

              {layers.origin ? (
                <>
                  {(() => {
                    const materialOrigin = worldToScreen({ x: 0, y: 0 }, transform);
                    return (
                      <>
                        <Line points={[materialOrigin.x - 8, materialOrigin.y, materialOrigin.x + 8, materialOrigin.y]} stroke={viewerTheme.originMaterial} strokeWidth={1.5} />
                        <Line points={[materialOrigin.x, materialOrigin.y - 8, materialOrigin.x, materialOrigin.y + 8]} stroke={viewerTheme.originMaterial} strokeWidth={1.5} />
                        <Text x={materialOrigin.x + 8} y={materialOrigin.y - 24} text="Origen material" fill={viewerTheme.axisText} fontSize={11} />
                        <Text x={materialOrigin.x + 8} y={materialOrigin.y - 10} text="G-code 0,0" fill={viewerTheme.originGcode} fontSize={11} />
                      </>
                    );
                  })()}
                </>
              ) : null}

              {segments.map(({ index, segment, points, outsideMaterial }) => {
                if (segment.tipo === "G0" && !layers.g0) {
                  return null;
                }
                if (segment.tipo === "G1" && !layers.g1) {
                  return null;
                }
                if ((segment.tipo === "G2" || segment.tipo === "G3") && !layers.arcs) {
                  return null;
                }
                const selected = selectedIndex === index || hoverIndex === index;
                const stroke = selected
                  ? viewerTheme.selection
                  : outsideMaterial && layers.warnings
                    ? viewerTheme.warning
                    : depthMode
                      ? colorForDepth(segment.z_mm, analysis.profundidad_min_mm, analysis.profundidad_max_mm)
                      : segment.tipo === "G0"
                        ? viewerTheme.rapid
                        : segment.tipo === "G1"
                          ? viewerTheme.cut
                          : viewerTheme.arc;
                return (
                  <Line
                    key={`${segment.tipo}-${index}`}
                    points={points}
                    stroke={stroke}
                    strokeWidth={selected ? 2.8 : 1.8}
                    dash={segment.tipo === "G0" ? [8, 6] : undefined}
                    lineCap="round"
                    lineJoin="round"
                    listening
                    hitStrokeWidth={10}
                    onMouseEnter={() => setHoverIndex(index)}
                    onMouseLeave={() => setHoverIndex((current) => (current === index ? null : current))}
                    onClick={() => setSelectedIndex(index)}
                    onTap={() => setSelectedIndex(index)}
                  />
                );
              })}

              {layers.startPoint && startSegment ? (
                <Circle radius={5} fill={viewerTheme.start} x={worldToScreen({ x: startSegment.desde.x_mm, y: startSegment.desde.y_mm }, transform).x} y={worldToScreen({ x: startSegment.desde.x_mm, y: startSegment.desde.y_mm }, transform).y} />
              ) : null}
              {layers.endPoint && endSegment ? (
                <Circle radius={5} fill={viewerTheme.end} x={worldToScreen({ x: endSegment.hasta.x_mm, y: endSegment.hasta.y_mm }, transform).x} y={worldToScreen({ x: endSegment.hasta.x_mm, y: endSegment.hasta.y_mm }, transform).y} />
              ) : null}

              <Text x={12} y={10} text="Y" fill={viewerTheme.axisText} fontSize={12} />
              <Text x={viewport.width - 20} y={viewport.height - 24} text="X" fill={viewerTheme.axisText} fontSize={12} />
            </Layer>
          </Stage>
        </div>

        <div className="viewer-footer">
          <div className="viewer-footer__meta mono-text">
            Material {material.ancho_mm} × {material.alto_mm} mm
            {analysis.limites ? ` · Trayectoria ${formatNumber(analysis.limites.ancho_mm)} × ${formatNumber(analysis.limites.alto_mm)} mm` : ""}
          </div>
          <div className="viewer-tracebar">
            <label>
              Recorrido visual
              <input
                aria-label="Recorrido visual"
                type="range"
                min={0}
                max={Math.max(0, analysis.segmentos_vista_previa.length - 1)}
                value={traceIndex ?? Math.max(0, analysis.segmentos_vista_previa.length - 1)}
                disabled={analysis.segmentos_vista_previa.length === 0}
                onChange={(event) => setTraceIndex(Number(event.target.value))}
              />
            </label>
            <button className="button button--ghost" type="button" onClick={() => setTraceIndex(null)}>
              Recorrido completo
            </button>
          </div>
        </div>
      </div>

      <aside className="toolpath-viewer-side">
        <ViewerInspector
          cursor={cursor}
          selectedSegment={selectedSegment}
          operationName={operationName}
          totalSegments={analysis.segmentos_vista_previa.length}
          traceIndex={traceIndex}
        />
        <ViewerLayers layers={layers} onToggle={toggleLayer} />
        <section className="viewer-sidecard">
          <div className="section-heading section-heading--compact">
            <h4>Advertencias</h4>
          </div>
          {analysis.desbordes_material.length > 0 ? (
            <div className="stack gap-sm">
              {analysis.desbordes_material.map((overflow) => (
                <div className="warning-card" key={`${overflow.eje}-${overflow.direccion}`}>
                  <strong>{overflow.eje} {translateStatus(overflow.direccion)}</strong>
                  <span>{formatMillimeters(overflow.exceso_mm, 3)} fuera del material</span>
                </div>
              ))}
              <div className="toolbar-inline">
                <button
                  className="button button--ghost"
                  type="button"
                  onClick={() => {
                    if (warningSegmentIndex >= 0) {
                      setSelectedIndex(warningSegmentIndex);
                      setTraceIndex(null);
                    }
                    applyFit("all");
                  }}
                >
                  Ver problema
                </button>
                <button className="button button--ghost" type="button" onClick={() => applyFit("all")}>Ajustar a todo</button>
              </div>
            </div>
          ) : (
            <p className="muted">Sin desbordes respecto al material bruto.</p>
          )}
          {analysis.analisis_incompleto ? (
            <p className="muted">La geometría contiene soporte incompleto o comandos no representables de forma segura.</p>
          ) : null}
          {depthMode ? (
            <div className="depth-legend">
              <span>Color por Z</span>
              <div className="depth-legend__bar" />
              <small>{formatMillimeters(analysis.profundidad_min_mm)} a {formatMillimeters(analysis.profundidad_max_mm)}</small>
            </div>
          ) : null}
          {allRect ? <p className="muted">Encuadre actual: {translateStatus(fitMode)}</p> : null}
        </section>
      </aside>
    </section>
  );
}
