import { formatCoordinate, formatMillimeters, formatNumber } from "../../lib/format";
import { translateStatus } from "../../lib/ui";
import type { PreviewSegment } from "../../types";

type ViewerInspectorProps = {
  cursor: { x: number; y: number } | null;
  selectedSegment: PreviewSegment | null;
  operationName: string;
  totalSegments: number;
  traceIndex: number | null;
};

export function ViewerInspector({ cursor, selectedSegment, operationName, totalSegments, traceIndex }: ViewerInspectorProps) {
  return (
    <section className="viewer-sidecard">
      <div className="section-heading section-heading--compact">
        <h4>Inspector</h4>
        <span className="mono-text">mm</span>
      </div>
      <dl className="definition-grid definition-grid--compact">
        <div>
          <dt>Cursor X/Y</dt>
          <dd className="mono-text">{cursor ? `${formatCoordinate(cursor.x)}, ${formatCoordinate(cursor.y)}` : "-"}</dd>
        </div>
        <div>
          <dt>Recorrido</dt>
          <dd>{traceIndex == null ? `Completo (${totalSegments})` : `${traceIndex + 1} / ${totalSegments}`}</dd>
        </div>
      </dl>
      {selectedSegment ? (
        <dl className="definition-grid definition-grid--compact">
          <div><dt>Movimiento</dt><dd>{translateStatus(selectedSegment.tipo_movimiento)}</dd></div>
          <div><dt>Línea G-code</dt><dd>{selectedSegment.numero_linea ?? "-"}</dd></div>
          <div><dt>Desde</dt><dd className="mono-text">{formatCoordinate(selectedSegment.desde.x_mm)}, {formatCoordinate(selectedSegment.desde.y_mm)}</dd></div>
          <div><dt>Hasta</dt><dd className="mono-text">{formatCoordinate(selectedSegment.hasta.x_mm)}, {formatCoordinate(selectedSegment.hasta.y_mm)}</dd></div>
          <div><dt>Z</dt><dd className="mono-text">{formatMillimeters(selectedSegment.z_mm, 3)}</dd></div>
          <div><dt>Avance</dt><dd>{selectedSegment.avance_mm_min != null ? `${formatNumber(selectedSegment.avance_mm_min, 0)} mm/min` : "-"}</dd></div>
          <div><dt>Distancia</dt><dd>{formatMillimeters(selectedSegment.distancia_mm, 3)}</dd></div>
          <div><dt>Operación</dt><dd>{operationName}</dd></div>
        </dl>
      ) : (
        <p className="muted">Pase el cursor o seleccione un segmento para ver su información técnica.</p>
      )}
    </section>
  );
}
