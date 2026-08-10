from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one match, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")
    print(f"patched {path}")


replace_once(
    "frontend/src/App.tsx",
    '''import { api, type OperationInput, type OperationUpdateInput } from "./lib/api";\n''',
    '''import { api, type OperationInput, type OperationUpdateInput } from "./lib/api";\nimport { reorderOperations } from "./lib/operationOrder";\n''',
)

replace_once(
    "frontend/src/App.tsx",
    '''  const handleMoveOperation = async (operationId: string, direction: "up" | "down") => {\n    if (!selectedProjectId) {\n      return;\n    }\n    setBusyKey("operation:move:" + operationId);\n    setError("");\n    try {\n      await api.moveOperation(selectedProjectId, operationId, direction);\n      await syncProject(selectedProjectId);\n    } catch (requestError) {\n      setError(requestError instanceof Error ? requestError.message : "No fue posible reordenar la operación.");\n    } finally {\n      setBusyKey(null);\n    }\n  };\n''',
    '''  const handleMoveOperation = async (operationId: string, direction: "up" | "down") => {\n    if (!selectedProjectId || !selectedProject || busyKey?.startsWith("operation:move:")) {\n      return;\n    }\n    const optimisticOperations = reorderOperations(selectedProject.operaciones, operationId, direction);\n    if (optimisticOperations === selectedProject.operaciones) {\n      return;\n    }\n    const optimisticProject = { ...selectedProject, operaciones: optimisticOperations };\n    const optimisticMoved = optimisticOperations.find((item) => item.id === operationId);\n    setBusyKey("operation:move:" + operationId);\n    setError("");\n    setProjects((current) => current.map((item) => (item.id === selectedProjectId ? optimisticProject : item)));\n    try {\n      const moved = await api.moveOperation(selectedProjectId, operationId, direction);\n      if (!optimisticMoved || moved.orden !== optimisticMoved.orden) {\n        await syncProject(selectedProjectId);\n      }\n    } catch (requestError) {\n      try {\n        await syncProject(selectedProjectId);\n      } catch {\n        setProjects((current) => current.map((item) => (item.id === selectedProjectId ? selectedProject : item)));\n      }\n      setError(requestError instanceof Error ? requestError.message : "No fue posible reordenar la operación.");\n    } finally {\n      setBusyKey(null);\n    }\n  };\n''',
)

replace_once(
    "frontend/src/features/projects/ProjectWorkspace.tsx",
    '''                      <button type="button" className="icon-button" aria-label={"Mover arriba " + operation.nombre} disabled={index === 0} onClick={() => void onMoveOperation(operation.id, "up")}>↑</button>\n                      <button type="button" className="icon-button" aria-label={"Mover abajo " + operation.nombre} disabled={index === operations.length - 1} onClick={() => void onMoveOperation(operation.id, "down")}>↓</button>\n''',
    '''                      <button type="button" className="icon-button" aria-label={"Mover arriba " + operation.nombre} aria-busy={busyKey === "operation:move:" + operation.id} disabled={Boolean(busyKey?.startsWith("operation:move:")) || index === 0} onClick={() => void onMoveOperation(operation.id, "up")}>↑</button>\n                      <button type="button" className="icon-button" aria-label={"Mover abajo " + operation.nombre} aria-busy={busyKey === "operation:move:" + operation.id} disabled={Boolean(busyKey?.startsWith("operation:move:")) || index === operations.length - 1} onClick={() => void onMoveOperation(operation.id, "down")}>↓</button>\n''',
)

Path("frontend/src/lib/operationOrder.ts").write_text(
    '''type OrderedOperation = {\n  id: string;\n  setup_id: string;\n  orden: number;\n};\n\nexport function reorderOperations<T extends OrderedOperation>(operations: T[], operationId: string, direction: "up" | "down"): T[] {\n  const selected = operations.find((item) => item.id === operationId);\n  if (!selected) return operations;\n\n  const setupOperations = operations\n    .filter((item) => item.setup_id === selected.setup_id)\n    .sort((left, right) => left.orden - right.orden);\n  const index = setupOperations.findIndex((item) => item.id === operationId);\n  const target = direction === "up" ? index - 1 : index + 1;\n  if (index < 0 || target < 0 || target >= setupOperations.length) return operations;\n\n  [setupOperations[index], setupOperations[target]] = [setupOperations[target], setupOperations[index]];\n  const orderById = new Map(setupOperations.map((item, order) => [item.id, order]));\n  return operations.map((item) => {\n    const nextOrder = orderById.get(item.id);\n    return nextOrder === undefined || nextOrder === item.orden ? item : { ...item, orden: nextOrder };\n  });\n}\n''',
    encoding="utf-8",
)
print("created frontend/src/lib/operationOrder.ts")

Path("frontend/src/lib/operationOrder.test.ts").write_text(
    '''import { describe, expect, it } from "vitest";\n\nimport { reorderOperations } from "./operationOrder";\n\nconst operations = [\n  { id: "a", setup_id: "main", orden: 0, name: "A" },\n  { id: "b", setup_id: "main", orden: 1, name: "B" },\n  { id: "c", setup_id: "main", orden: 2, name: "C" },\n  { id: "x", setup_id: "other", orden: 0, name: "X" },\n];\n\ndescribe("reorderOperations", () => {\n  it("mueve de forma optimista solo dentro del mismo montaje", () => {\n    const result = reorderOperations(operations, "b", "up");\n    expect(result.filter((item) => item.setup_id === "main").sort((a, b) => a.orden - b.orden).map((item) => item.id)).toEqual(["b", "a", "c"]);\n    expect(result.find((item) => item.id === "x")?.orden).toBe(0);\n  });\n\n  it("devuelve la misma referencia cuando intenta salir del límite", () => {\n    expect(reorderOperations(operations, "a", "up")).toBe(operations);\n    expect(reorderOperations(operations, "c", "down")).toBe(operations);\n  });\n});\n''',
    encoding="utf-8",
)
print("created frontend/src/lib/operationOrder.test.ts")
print("Fast reorder patch applied. No machine or G-code commands were executed.")
