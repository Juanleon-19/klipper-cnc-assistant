import { useMemo, useRef, useState } from "react";

import { formatMillimeters } from "../lib/format";
import type { Material, OperationAnalysis } from "../types";

type ToolpathPreviewProps = {
  material: Material;
  analysis: OperationAnalysis;
};

const WIDTH = 520;
const HEIGHT = 340;
const PADDING = 28;

export function ToolpathPreview({ material, analysis }: ToolpathPreviewProps) {
  const [zoom, setZoom] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const dragRef = useRef<{ x: number; y: number } | null>(null);

  const layout = useMemo(() => {
    const maxX = Math.max(material.ancho_mm, analysis.limites?.max_x_mm ?? material.ancho_mm, 1);
    const maxY = Math.max(material.alto_mm, analysis.limites?.max_y_mm ?? material.alto_mm, 1);
    const usableWidth = WIDTH - PADDING * 2;
    const usableHeight = HEIGHT - PADDING * 2;
    const scale = Math.min(usableWidth / maxX, usableHeight / maxY);
    return {
      scale,
      contentWidth: maxX,
      contentHeight: maxY,
    };
  }, [analysis.limites, material.alto_mm, material.ancho_mm]);

  const mapPoint = (x: number, y: number) => ({
    x: PADDING + offset.x + x * layout.scale * zoom,
    y: HEIGHT - PADDING + offset.y - y * layout.scale * zoom,
  });

  return (
    <section className="preview-panel">
      <div className="section-heading">
        <div>
          <h3>Vista previa 2D inicial</h3>
          <p className="muted">Informativa. No representa una simulacion exacta de mecanizado.</p>
        </div>
        <div className="preview-controls">
          <button className="button button--ghost" type="button" onClick={() => setZoom((value) => value * 1.15)}>
            Zoom +
          </button>
          <button className="button button--ghost" type="button" onClick={() => setZoom((value) => Math.max(0.6, value / 1.15))}>
            Zoom -
          </button>
          <button className="button button--ghost" type="button" onClick={() => { setZoom(1); setOffset({ x: 0, y: 0 }); }}>
            Restablecer vista
          </button>
        </div>
      </div>

      <svg
        aria-label="Vista previa 2D de trayectorias"
        className="preview-canvas"
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        onPointerDown={(event) => {
          dragRef.current = { x: event.clientX - offset.x, y: event.clientY - offset.y };
        }}
        onPointerMove={(event) => {
          if (!dragRef.current) {
            return;
          }
          setOffset({
            x: event.clientX - dragRef.current.x,
            y: event.clientY - dragRef.current.y,
          });
        }}
        onPointerUp={() => {
          dragRef.current = null;
        }}
        onPointerLeave={() => {
          dragRef.current = null;
        }}
      >
        <rect x="0" y="0" width={WIDTH} height={HEIGHT} rx="18" className="preview-canvas__bg" />
        <line x1={PADDING} y1={HEIGHT - PADDING} x2={WIDTH - PADDING / 2} y2={HEIGHT - PADDING} className="preview-axis" />
        <line x1={PADDING} y1={HEIGHT - PADDING} x2={PADDING} y2={PADDING / 2} className="preview-axis" />
        <text x={WIDTH - PADDING} y={HEIGHT - PADDING - 6} className="preview-label">X</text>
        <text x={PADDING + 6} y={PADDING} className="preview-label">Y</text>

        {(() => {
          const topLeft = mapPoint(0, material.alto_mm);
          const bottomRight = mapPoint(material.ancho_mm, 0);
          return (
            <rect
              x={topLeft.x}
              y={topLeft.y}
              width={bottomRight.x - topLeft.x}
              height={bottomRight.y - topLeft.y}
              className="preview-material"
            />
          );
        })()}

        {analysis.segmentos_lineales.map((segment, index) => {
          const start = mapPoint(segment.inicio_x_mm, segment.inicio_y_mm);
          const end = mapPoint(segment.fin_x_mm, segment.fin_y_mm);
          return (
            <line
              key={`${segment.tipo}-${index}`}
              x1={start.x}
              y1={start.y}
              x2={end.x}
              y2={end.y}
              className={segment.tipo === "G0" ? "preview-segment preview-segment--rapid" : "preview-segment preview-segment--cut"}
            />
          );
        })}
      </svg>

      <div className="preview-legend">
        <span><i className="legend-line legend-line--rapid" /> G0 desplazamiento rapido</span>
        <span><i className="legend-line legend-line--cut" /> G1 movimiento lineal</span>
      </div>

      <div className="preview-metadata">
        <p>Material: {material.ancho_mm} × {material.alto_mm} mm</p>
        <p>Limites ocupados: {analysis.limites ? `${formatMillimeters(analysis.limites.ancho_mm)} × ${formatMillimeters(analysis.limites.alto_mm)}` : "Sin limites detectados"}</p>
      </div>

      {analysis.cabe_en_material === false ? (
        <p className="warning-banner">Advertencia: la trayectoria excede el material bruto definido.</p>
      ) : null}
      {analysis.analisis_incompleto ? (
        <p className="warning-banner">Advertencia: existen G2 o G3 no representados en esta vista previa.</p>
      ) : null}
    </section>
  );
}
