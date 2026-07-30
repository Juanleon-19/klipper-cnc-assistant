import { useEffect, useState } from "react";

import { formatMillimeters } from "../../lib/format";
import type { ExclusionZone, HeightMap, Material, ProbeRegion } from "../../types";

type HeightMapControlPanelProps = {
  material: Material;
  heightMap: HeightMap | null;
  busy: boolean;
  onConfigure: (payload: {
    filas: number;
    columnas: number;
    probe_region: ProbeRegion;
    exclusion_zones: ExclusionZone[];
  }) => Promise<void>;
  onSimulate: (payload: {
    filas: number;
    columnas: number;
    probe_region: ProbeRegion;
    exclusion_zones: ExclusionZone[];
    superficie_simulada: string;
    repeticion_simulacion: number;
  }) => Promise<void>;
  onImportJson: (content: string) => Promise<void>;
  onImportCsv: (content: string) => Promise<void>;
  onRecalculate: () => Promise<void>;
  onDelete: () => Promise<void>;
};

const defaultSurface = "inclinacion_y_deformacion";

function defaultProbeRegion(material: Material): ProbeRegion {
  return {
    min_x_mm: 0,
    min_y_mm: 0,
    max_x_mm: material.ancho_mm,
    max_y_mm: material.alto_mm,
  };
}

export function HeightMapControlPanel({
  material,
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
  const [surface, setSurface] = useState(defaultSurface);
  const [repeat, setRepeat] = useState(7);
  const [probeRegion, setProbeRegion] = useState<ProbeRegion>(defaultProbeRegion(material));
  const [zones, setZones] = useState<ExclusionZone[]>([]);

  useEffect(() => {
    if (heightMap) {
      setRows(heightMap.grid.filas);
      setColumns(heightMap.grid.columnas);
      setProbeRegion(heightMap.probe_region);
      setZones(heightMap.exclusion_zones);
      if (heightMap.superficie_simulada) {
        setSurface(heightMap.superficie_simulada);
      }
      if (heightMap.repeticion_simulacion != null) {
        setRepeat(heightMap.repeticion_simulacion);
      }
      return;
    }
    setProbeRegion(defaultProbeRegion(material));
    setZones([]);
  }, [heightMap, material]);

  const readAndImport = async (file: File, format: "json" | "csv") => {
    const content = await file.text();
    if (format === "json") {
      await onImportJson(content);
      return;
    }
    await onImportCsv(content);
  };

  const simulationFieldsVisible = !heightMap || heightMap.fuente_datos === "simulado" || heightMap.fuente_datos === "manual";

  return (
    <section className="panel heightmap-control-panel">
      <div className="section-heading section-heading--stacked">
        <div>
          <p className="eyebrow">Configuración</p>
          <h3>Región sondeable y simulación</h3>
        </div>
        <p className="muted">Todos los márgenes y exclusiones aquí definidos son parámetros de simulación configurables. No representan recomendaciones físicas.</p>
      </div>

      <div className="definition-grid definition-grid--compact">
        <div><dt>Fuente</dt><dd>{heightMap?.fuente_datos ?? "sin datos"}</dd></div>
        <div><dt>Versión</dt><dd>{heightMap?.version ?? "-"}</dd></div>
        <div><dt>Región</dt><dd>{formatMillimeters(probeRegion.max_x_mm - probeRegion.min_x_mm, 2)} × {formatMillimeters(probeRegion.max_y_mm - probeRegion.min_y_mm, 2)}</dd></div>
        <div><dt>Zonas excluidas</dt><dd>{zones.length}</dd></div>
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
          Región sondeable X mínima (mm)
          <input type="number" value={probeRegion.min_x_mm} onChange={(event) => setProbeRegion((current) => ({ ...current, min_x_mm: Number(event.target.value) }))} />
        </label>
        <label>
          Región sondeable X máxima (mm)
          <input type="number" value={probeRegion.max_x_mm} onChange={(event) => setProbeRegion((current) => ({ ...current, max_x_mm: Number(event.target.value) }))} />
        </label>
        <label>
          Región sondeable Y mínima (mm)
          <input type="number" value={probeRegion.min_y_mm} onChange={(event) => setProbeRegion((current) => ({ ...current, min_y_mm: Number(event.target.value) }))} />
        </label>
        <label>
          Región sondeable Y máxima (mm)
          <input type="number" value={probeRegion.max_y_mm} onChange={(event) => setProbeRegion((current) => ({ ...current, max_y_mm: Number(event.target.value) }))} />
        </label>
      </div>

      <details className="subpanel subpanel--soft" open>
        <summary>Zonas excluidas</summary>
        <div className="stack gap-sm">
          {zones.map((zone, index) => (
            <div className="form-grid" key={zone.id}>
              <label>
                Nombre
                <input value={zone.nombre} onChange={(event) => setZones((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, nombre: event.target.value } : item))} />
              </label>
              <label>
                X mínima
                <input type="number" value={zone.min_x_mm} onChange={(event) => setZones((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, min_x_mm: Number(event.target.value) } : item))} />
              </label>
              <label>
                X máxima
                <input type="number" value={zone.max_x_mm} onChange={(event) => setZones((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, max_x_mm: Number(event.target.value) } : item))} />
              </label>
              <label>
                Y mínima
                <input type="number" value={zone.min_y_mm} onChange={(event) => setZones((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, min_y_mm: Number(event.target.value) } : item))} />
              </label>
              <label>
                Y máxima
                <input type="number" value={zone.max_y_mm} onChange={(event) => setZones((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, max_y_mm: Number(event.target.value) } : item))} />
              </label>
              <button className="button button--ghost button--danger" type="button" onClick={() => setZones((current) => current.filter((_, itemIndex) => itemIndex !== index))}>
                Eliminar zona
              </button>
            </div>
          ))}
          <button
            className="button button--ghost"
            type="button"
            onClick={() => setZones((current) => [
              ...current,
              {
                id: `zone_${current.length + 1}`,
                nombre: `Zona ${current.length + 1}`,
                min_x_mm: probeRegion.min_x_mm,
                min_y_mm: probeRegion.min_y_mm,
                max_x_mm: Math.min(probeRegion.max_x_mm, probeRegion.min_x_mm + 5),
                max_y_mm: Math.min(probeRegion.max_y_mm, probeRegion.min_y_mm + 5),
              },
            ])}
          >
            Añadir zona excluida
          </button>
        </div>
      </details>

      {simulationFieldsVisible ? (
        <details className="subpanel subpanel--soft" open>
          <summary>Opciones avanzadas de simulación</summary>
          <div className="form-grid">
            <label>
              Superficie simulada
              <select value={surface} onChange={(event) => setSurface(event.target.value)}>
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
              Repetición de simulación
              <input type="number" value={repeat} onChange={(event) => setRepeat(Number(event.target.value))} />
            </label>
          </div>
          <p className="muted">Superficie simulada = patrón artificial usado para probar el sistema.</p>
          <p className="muted">Repetición = identificador que permite reproducir los mismos datos.</p>
          <p className="muted">Estos campos no existen en mapas de mediciones reales.</p>
        </details>
      ) : null}

      <div className="action-grid">
        <button
          className="button"
          type="button"
          disabled={busy}
          onClick={() => void onSimulate({ filas: rows, columnas: columns, probe_region: probeRegion, exclusion_zones: zones, superficie_simulada: surface, repeticion_simulacion: repeat })}
        >
          Crear mapa simulado
        </button>
        <button className="button button--ghost" type="button" disabled={busy} onClick={() => void onConfigure({ filas: rows, columnas: columns, probe_region: probeRegion, exclusion_zones: zones })}>
          Configurar región sin datos
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
    </section>
  );
}
