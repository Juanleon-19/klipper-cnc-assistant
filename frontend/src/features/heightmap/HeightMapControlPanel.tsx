import { useState } from "react";

import { formatMillimeters } from "../../lib/format";
import { translateStatus } from "../../lib/ui";
import type { HeightMap } from "../../types";

type HeightMapControlPanelProps = {
  heightMap: HeightMap | null;
  busy: boolean;
  onConfigure: (rows: number, columns: number) => Promise<void>;
  onSimulate: (rows: number, columns: number, scenario: string, seed: number) => Promise<void>;
  onImportJson: (content: string) => Promise<void>;
  onImportCsv: (content: string) => Promise<void>;
  onRecalculate: () => Promise<void>;
  onDelete: () => Promise<void>;
};

const defaultScenario = "inclinacion_y_deformacion";

export function HeightMapControlPanel({
  heightMap,
  busy,
  onConfigure,
  onSimulate,
  onImportJson,
  onImportCsv,
  onRecalculate,
  onDelete,
}: HeightMapControlPanelProps) {
  const [rows, setRows] = useState(6);
  const [columns, setColumns] = useState(8);
  const [seed, setSeed] = useState(7);
  const [scenario, setScenario] = useState(defaultScenario);

  const readAndImport = async (file: File, format: "json" | "csv") => {
    const content = await file.text();
    if (format === "json") {
      await onImportJson(content);
      return;
    }
    await onImportCsv(content);
  };

  return (
    <section className="panel heightmap-control-panel">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Mapa de alturas</p>
          <h3>Estado del mapa</h3>
        </div>
        <span className="status-badge status-badge--info">{translateStatus(heightMap?.estado ?? "sin datos")}</span>
      </div>

      <div className="definition-grid definition-grid--compact">
        <div><dt>Fuente</dt><dd>{translateStatus(heightMap?.fuente_datos ?? "sin datos")}</dd></div>
        <div><dt>Versión</dt><dd>{heightMap?.version ?? "-"}</dd></div>
        <div><dt>Puntos</dt><dd>{heightMap?.estadisticas.cantidad_puntos ?? 0}</dd></div>
        <div><dt>RMS</dt><dd>{formatMillimeters(heightMap?.estadisticas.rms_residuos_mm, 4)}</dd></div>
        <div><dt>Rango</dt><dd>{formatMillimeters(heightMap?.estadisticas.rango_alturas_mm, 4)}</dd></div>
        <div><dt>Atípicos</dt><dd>{heightMap?.estadisticas.cantidad_puntos_atipicos ?? 0}</dd></div>
      </div>

      <div className="form-grid">
        <label>
          Filas
          <input type="number" min={2} value={rows} onChange={(event) => setRows(Number(event.target.value))} />
        </label>
        <label>
          Columnas
          <input type="number" min={2} value={columns} onChange={(event) => setColumns(Number(event.target.value))} />
        </label>
        <label>
          Escenario
          <select value={scenario} onChange={(event) => setScenario(event.target.value)}>
            <option value="plana">Placa plana</option>
            <option value="inclinada">Placa inclinada</option>
            <option value="deformacion_suave">Deformación suave</option>
            <option value="elevacion_localizada">Elevación localizada</option>
            <option value="ruido_pequeno">Ruido pequeño</option>
            <option value="punto_faltante">Punto faltante</option>
            <option value="punto_atipico">Punto atípico</option>
            <option value="inclinacion_y_deformacion">Inclinación y deformación</option>
          </select>
        </label>
        <label>
          Semilla
          <input type="number" value={seed} onChange={(event) => setSeed(Number(event.target.value))} />
        </label>
      </div>

      <div className="action-grid">
        <button className="button" type="button" disabled={busy} onClick={() => void onConfigure(rows, columns)}>
          Configurar malla
        </button>
        <button className="button button--ghost" type="button" disabled={busy} onClick={() => void onSimulate(rows, columns, scenario, seed)}>
          Crear mapa simulado
        </button>
        <label className="button button--ghost file-button">
          Importar JSON
          <input aria-label="Importar mapa JSON" type="file" accept=".json" disabled={busy} onChange={async (event) => {
            const file = event.target.files?.[0];
            if (!file) {
              return;
            }
            await readAndImport(file, "json");
            event.target.value = "";
          }} />
        </label>
        <label className="button button--ghost file-button">
          Importar CSV
          <input aria-label="Importar mapa CSV" type="file" accept=".csv,text/csv" disabled={busy} onChange={async (event) => {
            const file = event.target.files?.[0];
            if (!file) {
              return;
            }
            await readAndImport(file, "csv");
            event.target.value = "";
          }} />
        </label>
        <button className="button button--ghost" type="button" disabled={busy || !heightMap} onClick={() => void onRecalculate()}>
          Recalcular
        </button>
        <button
          className="button button--ghost button--danger"
          type="button"
          disabled={busy || !heightMap}
          onClick={async () => {
            if (window.confirm("Se eliminará el mapa de alturas actual. ¿Desea continuar?")) {
              await onDelete();
            }
          }}
        >
          Eliminar mapa
        </button>
      </div>

      {heightMap?.etiqueta_simulada ? (
        <p className="machine-banner heightmap-banner">
          <span className="machine-banner__dot" aria-hidden="true" />
          DATOS SIMULADOS
        </p>
      ) : null}
    </section>
  );
}

