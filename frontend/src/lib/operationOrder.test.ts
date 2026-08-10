import { describe, expect, it } from "vitest";

import { reorderOperations } from "./operationOrder";

const operations = [
  { id: "a", setup_id: "main", orden: 0, name: "A" },
  { id: "b", setup_id: "main", orden: 1, name: "B" },
  { id: "c", setup_id: "main", orden: 2, name: "C" },
  { id: "x", setup_id: "other", orden: 0, name: "X" },
];

describe("reorderOperations", () => {
  it("mueve de forma optimista solo dentro del mismo montaje", () => {
    const result = reorderOperations(operations, "b", "up");
    expect(result.filter((item) => item.setup_id === "main").sort((a, b) => a.orden - b.orden).map((item) => item.id)).toEqual(["b", "a", "c"]);
    expect(result.find((item) => item.id === "x")?.orden).toBe(0);
  });

  it("permite encadenar varios movimientos optimistas sin esperar al servidor", () => {
    const first = reorderOperations(operations, "c", "up");
    const second = reorderOperations(first, "c", "up");
    expect(second.filter((item) => item.setup_id === "main").sort((a, b) => a.orden - b.orden).map((item) => item.id)).toEqual(["c", "a", "b"]);
  });

  it("devuelve la misma referencia cuando intenta salir del límite", () => {
    expect(reorderOperations(operations, "a", "up")).toBe(operations);
    expect(reorderOperations(operations, "c", "down")).toBe(operations);
  });
});
