import type { ViewerLayersState } from "./viewerTypes";

type ViewerLayersProps = {
  layers: ViewerLayersState;
  onToggle: (key: keyof ViewerLayersState) => void;
};

const layerLabels: Array<{ key: keyof ViewerLayersState; label: string }> = [
  { key: "material", label: "Material bruto" },
  { key: "grid", label: "Cuadrícula" },
  { key: "g0", label: "G0" },
  { key: "g1", label: "G1" },
  { key: "arcs", label: "G2/G3" },
  { key: "origin", label: "Origen" },
  { key: "startPoint", label: "Punto inicial" },
  { key: "endPoint", label: "Punto final" },
  { key: "warnings", label: "Advertencias" },
  { key: "bounds", label: "Límites ocupados" },
];

export function ViewerLayers({ layers, onToggle }: ViewerLayersProps) {
  return (
    <section className="viewer-sidecard">
      <div className="section-heading section-heading--compact">
        <h4>Capas</h4>
      </div>
      <div className="viewer-layers-grid">
        {layerLabels.map((layer) => (
          <label className="viewer-layer-toggle" key={layer.key}>
            <input type="checkbox" checked={layers[layer.key]} onChange={() => onToggle(layer.key)} />
            <span>{layer.label}</span>
          </label>
        ))}
      </div>
    </section>
  );
}
