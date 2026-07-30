import { useState } from "react";

import { formatCoordinate, formatMillimeters } from "../../lib/format";
import { translateStatus } from "../../lib/ui";
import type { HeightMap } from "../../types";

type HeightMapPointTableProps = {
  heightMap: HeightMap;
  busy: boolean;
  onToggleInclude: (sampleId: string, included: boolean) => Promise<void>;
  onEditSample: (sampleId: string, currentValue: number | null) => Promise<void>;
};

export function HeightMapPointTable({ heightMap, busy, onToggleInclude, onEditSample }: HeightMapPointTableProps) {
  const [filter, setFilter] = useState("todos");

  const samples = heightMap.muestras.filter((sample) => {
    if (filter === "todos") {
      return true;
    }
    if (filter === "atipicos") {
      return sample.estado_calidad === "atipica";
    }
    if (filter === "faltantes") {
      return sample.estado_calidad === "faltante";
    }
    return sample.incluida;
  });

  return (
    <section className="panel heightmap-table-panel">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Tabla de puntos</p>
          <h3>Muestras originales</h3>
        </div>
        <div className="toolbar-inline">
          <button className={`toolbar-pill${filter === "todos" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => setFilter("todos")}>Todos</button>
          <button className={`toolbar-pill${filter === "incluidos" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => setFilter("incluidos")}>Incluidos</button>
          <button className={`toolbar-pill${filter === "atipicos" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => setFilter("atipicos")}>Atípicos</button>
          <button className={`toolbar-pill${filter === "faltantes" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => setFilter("faltantes")}>Faltantes</button>
        </div>
      </div>

      <div className="heightmap-table-wrap">
        <table className="heightmap-table">
          <thead>
            <tr>
              <th>Punto</th>
              <th>X</th>
              <th>Y</th>
              <th>Z</th>
              <th>Residuo</th>
              <th>Calidad</th>
              <th>Incluida</th>
              <th>Acciones</th>
            </tr>
          </thead>
          <tbody>
            {samples.map((sample) => (
              <tr key={sample.id}>
                <td>P{sample.fila + 1}-{sample.columna + 1}</td>
                <td className="mono-text">{formatCoordinate(sample.x_mm)}</td>
                <td className="mono-text">{formatCoordinate(sample.y_mm)}</td>
                <td className="mono-text">{formatMillimeters(sample.z_mm, 4)}</td>
                <td className="mono-text">{formatMillimeters(sample.residuo_plano_mm, 4)}</td>
                <td>{translateStatus(sample.estado_calidad)}</td>
                <td>{sample.incluida ? "Sí" : "No"}</td>
                <td>
                  <div className="toolbar-inline">
                    <button className="button button--ghost" type="button" disabled={busy} onClick={() => void onToggleInclude(sample.id, !sample.incluida)}>
                      {sample.incluida ? "Excluir" : "Incluir"}
                    </button>
                    <button className="button button--ghost" type="button" disabled={busy} onClick={() => void onEditSample(sample.id, sample.z_mm)}>
                      Editar
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

