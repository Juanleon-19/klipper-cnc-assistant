import type { ViewerFitMode } from "./viewerTypes";

type ViewerToolbarProps = {
  activeFitMode: ViewerFitMode;
  gridEnabled: boolean;
  depthMode: boolean;
  wideMode: boolean;
  inspectorOpen: boolean;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onFit: (mode: ViewerFitMode) => void;
  onReset: () => void;
  onToggleFullscreen: () => void;
  onToggleGrid: () => void;
  onToggleDepth: () => void;
  onToggleWide: () => void;
  onToggleInspector: () => void;
};

export function ViewerToolbar({
  activeFitMode,
  gridEnabled,
  depthMode,
  wideMode,
  inspectorOpen,
  onZoomIn,
  onZoomOut,
  onFit,
  onReset,
  onToggleFullscreen,
  onToggleGrid,
  onToggleDepth,
  onToggleWide,
  onToggleInspector,
}: ViewerToolbarProps) {
  return (
    <div className="viewer-toolbar" role="toolbar" aria-label="Herramientas del visor">
      <button className="icon-button" type="button" aria-label="Acercar" title="Acercar" onClick={onZoomIn}>+</button>
      <button className="icon-button" type="button" aria-label="Alejar" title="Alejar" onClick={onZoomOut}>-</button>
      <button className={`toolbar-pill${activeFitMode === "material" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => onFit("material")}>
        Material
      </button>
      <button className={`toolbar-pill${activeFitMode === "toolpath" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => onFit("toolpath")}>
        Trayectoria
      </button>
      <button className={`toolbar-pill${activeFitMode === "all" ? " toolbar-pill--active" : ""}`} type="button" onClick={() => onFit("all")}>
        Todo
      </button>
      <button className="toolbar-pill" type="button" onClick={onReset}>Restablecer</button>
      <button className="toolbar-pill" type="button" onClick={onToggleFullscreen}>Pantalla completa</button>
      <button className={`toolbar-pill${wideMode ? " toolbar-pill--active" : ""}`} type="button" onClick={onToggleWide}>Visor amplio</button>
      <button className={`toolbar-pill${inspectorOpen ? " toolbar-pill--active" : ""}`} type="button" onClick={onToggleInspector}>Inspector</button>
      <button className={`toolbar-pill${gridEnabled ? " toolbar-pill--active" : ""}`} type="button" onClick={onToggleGrid}>
        Cuadrícula
      </button>
      <button className={`toolbar-pill${depthMode ? " toolbar-pill--active" : ""}`} type="button" onClick={onToggleDepth}>
        Color por Z
      </button>
    </div>
  );
}
