import { useEffect, useMemo, useRef, useState } from "react";

import { formatMillimeters } from "../../lib/format";
import type { HeightMap } from "../../types";

type HeightMapSurface3DProps = {
  heightMap: HeightMap;
  mode: "bruto" | "plano" | "residuo";
};

type PlotlyModule = {
  newPlot: (target: HTMLDivElement, data: unknown[], layout: Record<string, unknown>, config: Record<string, unknown>) => Promise<unknown>;
  purge: (target: HTMLDivElement) => void;
};

export function HeightMapSurface3D({ heightMap, mode }: HeightMapSurface3DProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [zScaleMode, setZScaleMode] = useState<"real" | "exagerada">("real");
  const [loading, setLoading] = useState(true);

  const surface = heightMap.superficies[mode];
  const exaggeratedFactor = zScaleMode === "exagerada" ? 8 : 1;
  const matrix = useMemo(() => {
    const rows = Array.from({ length: surface.filas }, () => Array.from({ length: surface.columnas }, () => null as number | null));
    surface.puntos.forEach((point) => {
      rows[point.fila][point.columna] = point.z_mm == null ? null : point.z_mm * exaggeratedFactor;
    });
    return rows;
  }, [exaggeratedFactor, surface]);

  useEffect(() => {
    let disposed = false;
    let plotly: PlotlyModule | null = null;
    const target = containerRef.current;

    const render = async () => {
      if (!target) {
        return;
      }
      setLoading(true);
      const module = (await import("plotly.js-dist-min")) as unknown as PlotlyModule;
      if (disposed) {
        return;
      }
      plotly = module;
      const scatterPoints = heightMap.muestras.filter((sample) => sample.z_mm != null);
      await module.newPlot(
        target,
        [
          {
            type: "surface",
            z: matrix,
            colorscale: [
              [0, "#3dd5ff"],
              [0.5, "#f4ce73"],
              [1, "#ff7b72"],
            ],
            showscale: true,
            colorbar: { title: `${mode} (mm)` },
            hovertemplate: "X %{x:.2f} mm<br>Y %{y:.2f} mm<br>Z %{z:.4f} mm<extra></extra>",
          },
          {
            type: "scatter3d",
            mode: "markers",
            x: scatterPoints.map((sample) => sample.x_mm),
            y: scatterPoints.map((sample) => sample.y_mm),
            z: scatterPoints.map((sample) => (sample.z_mm ?? 0) * exaggeratedFactor),
            marker: {
              size: 4,
              color: "#eef6fb",
            },
            name: "Muestras",
            hovertemplate: "Punto medido<br>X %{x:.2f} mm<br>Y %{y:.2f} mm<br>Z %{z:.4f} mm<extra></extra>",
          },
        ],
        {
          paper_bgcolor: "rgba(0,0,0,0)",
          plot_bgcolor: "rgba(0,0,0,0)",
          font: { color: "#dce8ef" },
          margin: { l: 0, r: 0, t: 12, b: 0 },
          scene: {
            bgcolor: "rgba(7,11,15,0.96)",
            xaxis: { title: "X (mm)" },
            yaxis: { title: "Y (mm)" },
            zaxis: { title: `Z ${zScaleMode === "exagerada" ? `(x${exaggeratedFactor})` : ""}` },
            aspectmode: "manual",
            aspectratio: {
              x: Math.max(1, heightMap.grid.ancho_mm / Math.max(heightMap.grid.alto_mm, 1)),
              y: 1,
              z: zScaleMode === "exagerada" ? 0.45 : 0.12,
            },
            camera: {
              eye: { x: 1.4, y: -1.65, z: 0.9 },
            },
          },
        },
        {
          displaylogo: false,
          responsive: true,
        }
      );
      setLoading(false);
    };

    void render();

    return () => {
      disposed = true;
      if (plotly && target) {
        plotly.purge(target);
      }
    };
  }, [exaggeratedFactor, heightMap.grid.alto_mm, heightMap.grid.ancho_mm, heightMap.muestras, matrix, mode, zScaleMode]);

  return (
    <section className="panel heightmap-surface-panel">
      <div className="section-heading">
        <div>
          <p className="eyebrow">Superficie 3D</p>
          <h3>Visualización informativa</h3>
        </div>
        <div className="toolbar-inline">
          <button className={`toolbar-pill${zScaleMode === "real" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => setZScaleMode("real")}>
            Escala Z real
          </button>
          <button className={`toolbar-pill${zScaleMode === "exagerada" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => setZScaleMode("exagerada")}>
            Escala Z exagerada
          </button>
        </div>
      </div>
      <p className="muted">
        DATOS {heightMap.etiqueta_simulada ? "SIMULADOS" : "IMPORTADOS"} · modo {mode} · rango {formatMillimeters(heightMap.estadisticas.rango_alturas_mm, 4)}
        {zScaleMode === "exagerada" ? ` · factor visual x${exaggeratedFactor}` : ""}
      </p>
      {loading ? <div className="panel empty-state empty-state--compact"><p>Preparando superficie 3D...</p></div> : null}
      <div className="heightmap-plotly" ref={containerRef} />
    </section>
  );
}
