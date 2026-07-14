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
  react: (target: HTMLDivElement, data: unknown[], layout: Record<string, unknown>, config: Record<string, unknown>) => Promise<unknown>;
};

type CameraPreset = "isometrica" | "superior";

export function HeightMapSurface3D({ heightMap, mode }: HeightMapSurface3DProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [zScaleMode, setZScaleMode] = useState<"real" | "exagerada">("exagerada");
  const [cameraPreset, setCameraPreset] = useState<CameraPreset>("isometrica");
  const [loading, setLoading] = useState(true);

  const surface = heightMap.superficies[mode];
  const exaggeratedFactor = zScaleMode === "exagerada" ? 8 : 1;
  const zValues = heightMap.muestras.filter((sample) => sample.z_mm != null).map((sample) => sample.z_mm as number);
  const xAxis = Array.from({ length: surface.columnas }, (_, index) => heightMap.probe_region.min_x_mm + ((heightMap.probe_region.max_x_mm - heightMap.probe_region.min_x_mm) * index) / Math.max(surface.columnas - 1, 1));
  const yAxis = Array.from({ length: surface.filas }, (_, index) => heightMap.probe_region.min_y_mm + ((heightMap.probe_region.max_y_mm - heightMap.probe_region.min_y_mm) * index) / Math.max(surface.filas - 1, 1));
  const surfaceRange = zValues.length >= 2 ? Math.max(...zValues) - Math.min(...zValues) : 0;
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
      const camera = cameraPreset === "superior" ? { eye: { x: 0.01, y: 0.01, z: 2.4 } } : { eye: { x: 1.4, y: -1.65, z: 0.9 } };
      await module.newPlot(
        target,
        [
          {
            type: "surface",
            x: xAxis,
            y: yAxis,
            z: matrix,
            customdata: surface.puntos.map((point) => point.z_mm),
            colorscale: [
              [0, "#3dd5ff"],
              [0.5, "#f4ce73"],
              [1, "#ff7b72"],
            ],
            showscale: true,
            colorbar: { title: `${mode} (mm reales)` },
            hovertemplate: "X %{x:.2f} mm<br>Y %{y:.2f} mm<br>Z real %{customdata:.4f} mm<extra></extra>",
          },
          {
            type: "scatter3d",
            mode: "markers",
            x: scatterPoints.map((sample) => sample.x_mm),
            y: scatterPoints.map((sample) => sample.y_mm),
            z: scatterPoints.map((sample) => (sample.z_mm ?? 0) * exaggeratedFactor),
            customdata: scatterPoints.map((sample) => sample.z_mm),
            marker: {
              size: 4,
              color: "#eef6fb",
            },
            name: "Muestras",
            hovertemplate: "Muestra<br>X %{x:.2f} mm<br>Y %{y:.2f} mm<br>Z real %{customdata:.4f} mm<extra></extra>",
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
            zaxis: { title: "Z (mm)" },
            aspectmode: "manual",
            aspectratio: {
              x: Math.max(1, heightMap.grid.ancho_mm / Math.max(heightMap.grid.alto_mm, 1)),
              y: 1,
              z: zScaleMode === "exagerada" ? 0.55 : 0.12,
            },
            camera,
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
  }, [cameraPreset, exaggeratedFactor, heightMap.grid.alto_mm, heightMap.grid.ancho_mm, heightMap.muestras, matrix, mode, surface, xAxis, yAxis, zScaleMode]);

  return (
    <section className="panel heightmap-surface-panel">
      <div className="section-heading section-heading--stacked">
        <div>
          <p className="eyebrow">Superficie 3D</p>
          <h3>Vista espacial de la superficie</h3>
        </div>
        <div className="toolbar-inline">
          <button className={`toolbar-pill${cameraPreset === "superior" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => setCameraPreset("superior")}>
            Vista superior
          </button>
          <button className={`toolbar-pill${cameraPreset === "isometrica" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => setCameraPreset("isometrica")}>
            Vista isométrica
          </button>
          <button className="toolbar-pill" type="button" onClick={() => {
            setCameraPreset("isometrica");
            setZScaleMode("exagerada");
          }}>
            Restablecer
          </button>
          <button className={`toolbar-pill${zScaleMode === "real" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => setZScaleMode("real")}>
            Escala Z real
          </button>
          <button className={`toolbar-pill${zScaleMode === "exagerada" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => setZScaleMode("exagerada")}>
            Escala Z exagerada
          </button>
        </div>
      </div>
      <p className="muted">
        Alturas reales en tooltips y etiquetas. Factor de exageración visible: x{exaggeratedFactor}. Rango de superficie {formatMillimeters(surfaceRange, 4)}.
      </p>
      {loading ? <div className="panel empty-state empty-state--compact"><p>Preparando superficie 3D...</p></div> : null}
      <div className="heightmap-plotly" ref={containerRef} />
    </section>
  );
}
