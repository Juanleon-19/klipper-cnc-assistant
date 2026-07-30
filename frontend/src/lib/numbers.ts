export type ParsedFiniteNumber = {
  value: number | null;
  error: "empty" | "invalid" | null;
};

export function parseFiniteNumber(rawValue: string): ParsedFiniteNumber {
  const trimmed = rawValue.trim();
  if (!trimmed) {
    return { value: null, error: "empty" };
  }
  const normalized = trimmed.replace(/,/g, ".");
  const value = Number(normalized);
  if (!Number.isFinite(value)) {
    return { value: null, error: "invalid" };
  }
  return { value, error: null };
}
