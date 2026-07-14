import { useEffect, useMemo, useRef, useState } from "react";
import { Circle, Layer, Rect, Stage, Text } from "react-konva";

import { formatCoordinate, formatMillimeters } from "../../lib/format";
import { translateStatus } from "../../lib/ui";
import type { HeightMap, HeightMapSurfacePoint, Material } from "../../types";
import { useMeasuredViewport } from "../viewer/useMeasuredViewport";
import { buildGridTicks, chooseGridStep, fitRectWithinViewport, getVisibleWorldRect, screenToWorld, VIEWER_MARGIN, worldToScreen, zoomAtPoint } from "../viewer/viewerMath";
import { rectFromMaterial } from "../viewer/viewerTypes";
import type { ViewTransform } from "../viewer/viewerTypes";

type HeightMapHeatmapProps = {
  material: Material;
  heightMap: HeightMap;
  mode: "bruto" | "plano" | "residuo";
};

type DragState = {
  x: number;
  y: number;
  panX: number;
  panY: number;
};

function buildTransform(material: Material, width: number, height: number): ViewTransform {
  return fitRectWithinViewport(rectFromMaterial(material), { width, height });
}

function colorScale(value: number | null, min: number, max: number): string {
  if (value == null || Number.isNaN(value)) {
    return "rgba(78, 92, 106, 0.42)";
  }
  const span = Math.max(0.0001, max - min);
  const ratio = Math.max(0, Math.min(1, (value - min) / span));
  const hue = 220 - ratio * 220;
  return `hsl(${hue} 74% 58%)`;
}

export function HeightMapHeatmap({ material, heightMap, mode }: HeightMapHeatmapProps) {
  const fullscreenRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<DragState | null>(null);
  const [stageNode, setStageNode] = useState<HTMLDivElement | null>(null);
  const viewport = useMeasuredViewport(stageNode);
  const [transform, setTransform] = useState<ViewTransform>({ scale: 8, panX: VIEWER_MARGIN, panY: 480 });
  const [cursor, setCursor] = useState<{ x: number; y: number } | null>(null);
  const [hoverPoint, setHoverPoint] = useState<HeightMapSurfacePoint | null>(null);

  useEffect(() => {
    setTransform(buildTransform(material, viewport.width, viewport.height));
  }, [material, viewport.height, viewport.width]);

  const surface = heightMap.superficies[mode];
  const values = surface.puntos.map((point) => point.z_mm).filter((value): value is number => value != null);
  const minValue = values.length > 0 ? Math.min(...values) : 0;
  const maxValue = values.length > 0 ? Math.max(...values) : 1;
  const gridStep = chooseGridStep(transform.scale);
  const visibleRect = getVisibleWorldRect(transform, viewport);
  const gridTicks = buildGridTicks(visibleRect, gridStep);
  const cellWidth = surface.columnas > 1 ? material.ancho_mm / (surface.columnas - 1) : material.ancho_mm;
  const cellHeight = surface.filas > 1 ? material.alto_mm / (surface.filas - 1) : material.alto_mm;

  const samplesById = useMemo(
    () => Object.fromEntries(heightMap.muestras.map((sample) => [sample.id, sample])),
    [heightMap.muestras]
  );

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

  return (
    <section className="heightmap-viewer" ref={fullscreenRef}>
      <div className="viewer-header">
        <div>
          <h3>Mapa de alturas 2D</h3>
          <p className="muted">DATOS {heightMap.etiqueta_simulada ? "SIMULADOS" : translateStatus(heightMap.fuente_datos)}. La escala de color muestra {translateStatus(mode)}.</p>
        </div>
        <div className="toolbar-inline">
          <button className="button button--ghost" type="button" onClick={() => setTransform(buildTransform(material, viewport.width, viewport.height))}>Ajustar al material</button>
          <button className="button button--ghost" type="button" onClick={() => void toggleFullscreen()}>Pantalla completa</button>
        </div>
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
            onDblClick={() => setTransform(buildTransform(material, viewport.width, viewport.height))}
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

              {surface.puntos.map((point) => {
                const topLeft = worldToScreen({ x: point.x_mm, y: Math.min(material.alto_mm, point.y_mm + cellHeight) }, transform);
                const bottomRight = worldToScreen({ x: Math.min(material.ancho_mm, point.x_mm + cellWidth), y: point.y_mm }, transform);
                return (
                  <Rect
                    key={`${point.fila}-${point.columna}`}
                    x={topLeft.x}
                    y={topLeft.y}
                    width={Math.max(1, bottomRight.x - topLeft.x)}
                    height={Math.max(1, bottomRight.y - topLeft.y)}
                    fill={colorScale(point.z_mm, minValue, maxValue)}
                    opacity={point.estado === "ok" ? 0.86 : 0.3}
                    onMouseEnter={() => setHoverPoint(point)}
                    onMouseLeave={() => setHoverPoint((current) => (current === point ? null : current))}
                  />
                );
              })}

              {heightMap.muestras.map((sample) => {
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
                  />
                );
              })}

              <Text x={12} y={10} text="Y" fill="#c6d7e3" fontSize={12} />
              <Text x={viewport.width - 22} y={viewport.height - 24} text="X" fill="#c6d7e3" fontSize={12} />
            </Layer>
          </Stage>

          <div className="viewer-stage__overlay viewer-stage__overlay--top mono-text">
            {translateStatus(mode)} · rango {formatMillimeters(heightMap.estadisticas.rango_alturas_mm, 3)}
          </div>
          <div className="viewer-stage__overlay viewer-stage__overlay--bottom mono-text">
            {cursor ? `${formatCoordinate(cursor.x)}, ${formatCoordinate(cursor.y)} mm` : "Cursor -"}
          </div>
        </div>
      </div>

      <div className="viewer-footer">
        <div className="viewer-footer__meta mono-text">
          Mín {formatMillimeters(minValue, 3)} · Máx {formatMillimeters(maxValue, 3)} · RMS {formatMillimeters(heightMap.estadisticas.rms_residuos_mm, 3)}
        </div>
        <div className="viewer-footer__meta mono-text">
          {hoverPoint ? `Celda ${hoverPoint.fila + 1}/${hoverPoint.columna + 1}: ${formatMillimeters(hoverPoint.z_mm, 4)} · ${translateStatus(hoverPoint.estado)}` : "Pase el cursor para inspeccionar valores"}
        </div>
      </div>

      <div className="heightmap-legend">
        <div className="depth-legend__bar" />
        <span className="mono-text">{formatMillimeters(minValue, 3)}</span>
        <span className="mono-text">{formatMillimeters(maxValue, 3)}</span>
      </div>

      <div className="chip-list">
        {heightMap.muestras.slice(0, 6).map((sample) => {
          const key = `${sample.fila}-${sample.columna}`;
          const localSample = samplesById[sample.id];
          return (
            <li className="chip" key={key}>
              P{sample.fila + 1}-{sample.columna + 1}: {formatMillimeters(localSample?.z_mm, 3)}
            </li>
          );
        })}
      </div>
    </section>
  );
}

