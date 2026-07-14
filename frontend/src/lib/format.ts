export function formatDate(value: string): string {
  return new Intl.DateTimeFormat("es-CO", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export function formatMillimeters(value: number | null | undefined, digits = 2): string {
  if (value == null || Number.isNaN(value)) {
    return "-";
  }
  return `${value.toFixed(digits)} mm`;
}

export function formatCoordinate(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) {
    return "-";
  }
  return value.toFixed(3);
}

export function formatNumber(value: number | null | undefined, digits = 2): string {
  if (value == null || Number.isNaN(value)) {
    return "-";
  }
  return value.toFixed(digits);
}

export function formatFileSize(value: number | null | undefined): string {
  if (value == null) {
    return "-";
  }
  if (value < 1024) {
    return `${value} B`;
  }
  if (value < 1024 * 1024) {
    return `${(value / 1024).toFixed(1)} KB`;
  }
  return `${(value / (1024 * 1024)).toFixed(2)} MB`;
}
