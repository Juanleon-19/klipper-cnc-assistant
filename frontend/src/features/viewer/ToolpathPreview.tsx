import type { Material, OperationAnalysis } from "../types";
import { ToolpathViewer } from "../features/viewer/ToolpathViewer";

type ToolpathPreviewProps = {
  material: Material;
  analysis: OperationAnalysis;
};

export function ToolpathPreview({ material, analysis }: ToolpathPreviewProps) {
  return <ToolpathViewer material={material} analysis={analysis} operationName="Vista previa" />;
}
