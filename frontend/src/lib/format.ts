export function formatDate(value: string): string {
  return new Intl.DateTimeFormat("es-CO", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export function formatMillimeters(value: number | null | undefined): string {
  if (value == null) {
    return "-";
  }
  return `${value.toFixed(2)} mm`;
}

export function formatFileSize(value: number | null | undefined): string {
  if (value == null) {
    return "-";
  }
  if (value < 1024) {
    return `${value} B`;
  }
  return `${(value / 1024).toFixed(1)} KB`;
}
