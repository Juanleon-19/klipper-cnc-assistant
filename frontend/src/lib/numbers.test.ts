import { describe, expect, it } from "vitest";

import { parseFiniteNumber } from "./numbers";

describe("parseFiniteNumber", () => {
  it("acepta cero, decimales y coma decimal", () => {
    expect(parseFiniteNumber("0")).toEqual({ value: 0, error: null });
    expect(parseFiniteNumber("0.25")).toEqual({ value: 0.25, error: null });
    expect(parseFiniteNumber("0,75")).toEqual({ value: 0.75, error: null });
  });

  it("rechaza campo vacío y NaN infinito", () => {
    expect(parseFiniteNumber("")).toEqual({ value: null, error: "empty" });
    expect(parseFiniteNumber("1e309")).toEqual({ value: null, error: "invalid" });
  });
});
