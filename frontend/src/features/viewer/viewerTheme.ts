export const viewerTheme = {
  background: "#0a1117",
  frame: "#1a2630",
  gridMinor: "rgba(118, 147, 166, 0.12)",
  gridMajor: "rgba(118, 147, 166, 0.24)",
  axis: "#7dc3ff",
  axisText: "#c8d9e6",
  materialFill: "rgba(18, 60, 88, 0.18)",
  materialStroke: "#4f87b3",
  boundsStroke: "#f0a65b",
  originMaterial: "#49b2ff",
  originGcode: "#f6b15e",
  start: "#5de2a5",
  end: "#ff8f70",
  rapid: "#88a9c0",
  cut: "#3fd7ff",
  arc: "#ffd166",
  warning: "#ff6b6b",
  selection: "#f8f9fb",
  depthLow: "#41d6ff",
  depthHigh: "#ff7f50",
} as const;

function mixChannel(start: number, end: number, ratio: number): number {
  return Math.round(start + (end - start) * ratio);
}

export function colorForDepth(z: number | null, minZ: number | null, maxZ: number | null): string {
  if (z == null || minZ == null || maxZ == null || minZ === maxZ) {
    return viewerTheme.cut;
  }
  const ratio = Math.max(0, Math.min(1, (z - minZ) / (maxZ - minZ)));
  return `rgb(${mixChannel(65, 255, ratio)}, ${mixChannel(214, 127, ratio)}, ${mixChannel(255, 80, ratio)})`;
}
