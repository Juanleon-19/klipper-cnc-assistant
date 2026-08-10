from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"ERROR: patrón no encontrado en {path}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "frontend/src/App.tsx",
    '  const runtimePollInFlight = useRef(false);\n  const [busyKey, setBusyKey] = useState<string | null>(null);',
    '  const runtimePollInFlight = useRef(false);\n  const operationMoveQueue = useRef<Promise<void>>(Promise.resolve());\n  const optimisticOperationOrders = useRef<Record<string, string[]>>({});\n  const [busyKey, setBusyKey] = useState<string | null>(null);',
)

old_handler = '''  const handleMoveOperation = async (operationId: string, direction: "up" | "down") => {
    if (!selectedProjectId || !selectedProject || busyKey?.startsWith("operation:move:")) {
      return;
    }
    const optimisticOperations = reorderOperations(selectedProject.operaciones, operationId, direction);
    if (optimisticOperations === selectedProject.operaciones) {
      return;
    }
    const optimisticProject = { ...selectedProject, operaciones: optimisticOperations };
    const optimisticMoved = optimisticOperations.find((item) => item.id === operationId);
    setBusyKey("operation:move:" + operationId);
    setError("");
    setProjects((current) => current.map((item) => (item.id === selectedProjectId ? optimisticProject : item)));
    try {
      const moved = await api.moveOperation(selectedProjectId, operationId, direction);
      if (!optimisticMoved || moved.orden !== optimisticMoved.orden) {
        await syncProject(selectedProjectId);
      }
    } catch (requestError) {
      try {
        await syncProject(selectedProjectId);
      } catch {
        setProjects((current) => current.map((item) => (item.id === selectedProjectId ? selectedProject : item)));
      }
      setError(requestError instanceof Error ? requestError.message : "No fue posible reordenar la operación.");
    } finally {
      setBusyKey(null);
    }
  };
'''

new_handler = '''  const handleMoveOperation = async (operationId: string, direction: "up" | "down") => {
    if (!selectedProjectId || !selectedProject) {
      return;
    }
    const selectedOperation = selectedProject.operaciones.find((item) => item.id === operationId);
    if (!selectedOperation) {
      return;
    }
    const setupId = selectedOperation.setup_id;
    const visibleSetupOperations = selectedProject.operaciones
      .filter((item) => item.setup_id === setupId)
      .sort((left, right) => left.orden - right.orden);
    const visibleIds = visibleSetupOperations.map((item) => item.id);
    const storedIds = optimisticOperationOrders.current[setupId];
    const storedStillValid = Boolean(
      storedIds
      && storedIds.length === visibleIds.length
      && storedIds.every((id) => visibleIds.includes(id))
    );
    const baseIds = storedStillValid ? [...storedIds!] : visibleIds;
    const index = baseIds.indexOf(operationId);
    const target = direction === "up" ? index - 1 : index + 1;
    if (index < 0 || target < 0 || target >= baseIds.length) {
      return;
    }
    [baseIds[index], baseIds[target]] = [baseIds[target], baseIds[index]];
    optimisticOperationOrders.current[setupId] = baseIds;
    const orderById = new Map(baseIds.map((id, order) => [id, order]));
    setError("");
    setProjects((current) => current.map((project) => {
      if (project.id !== selectedProjectId) {
        return project;
      }
      return {
        ...project,
        operaciones: project.operaciones.map((item) => {
          const nextOrder = orderById.get(item.id);
          return nextOrder === undefined || nextOrder === item.orden ? item : { ...item, orden: nextOrder };
        }),
      };
    }));

    operationMoveQueue.current = operationMoveQueue.current
      .then(async () => {
        await api.moveOperation(selectedProjectId, operationId, direction);
      })
      .catch(async (requestError) => {
        delete optimisticOperationOrders.current[setupId];
        try {
          await syncProject(selectedProjectId);
        } catch {
          // Preserve the latest optimistic UI if the resync also fails.
        }
        setError(requestError instanceof Error ? requestError.message : "No fue posible reordenar la operación.");
      });

    await Promise.resolve();
  };
'''

replace_once("frontend/src/App.tsx", old_handler, new_handler)

replace_once(
    "frontend/src/features/projects/ProjectWorkspace.tsx",
    '''                      <button type="button" className="icon-button" aria-label={"Mover arriba " + operation.nombre} aria-busy={busyKey === "operation:move:" + operation.id} disabled={Boolean(busyKey?.startsWith("operation:move:")) || index === 0} onClick={() => void onMoveOperation(operation.id, "up")}>↑</button>\n                      <button type="button" className="icon-button" aria-label={"Mover abajo " + operation.nombre} aria-busy={busyKey === "operation:move:" + operation.id} disabled={Boolean(busyKey?.startsWith("operation:move:")) || index === operations.length - 1} onClick={() => void onMoveOperation(operation.id, "down")}>↓</button>''',
    '''                      <button type="button" className="icon-button" aria-label={"Mover arriba " + operation.nombre} disabled={index === 0} onClick={() => void onMoveOperation(operation.id, "up")}>↑</button>\n                      <button type="button" className="icon-button" aria-label={"Mover abajo " + operation.nombre} disabled={index === operations.length - 1} onClick={() => void onMoveOperation(operation.id, "down")}>↓</button>''',
)

replace_once(
    "frontend/src/lib/operationOrder.test.ts",
    '''  it("devuelve la misma referencia cuando intenta salir del límite", () => {\n    expect(reorderOperations(operations, "a", "up")).toBe(operations);\n    expect(reorderOperations(operations, "c", "down")).toBe(operations);\n  });''',
    '''  it("permite encadenar varios movimientos optimistas sin esperar al servidor", () => {\n    const first = reorderOperations(operations, "c", "up");\n    const second = reorderOperations(first, "c", "up");\n    expect(second.filter((item) => item.setup_id === "main").sort((a, b) => a.orden - b.orden).map((item) => item.id)).toEqual(["c", "a", "b"]);\n  });\n\n  it("devuelve la misma referencia cuando intenta salir del límite", () => {\n    expect(reorderOperations(operations, "a", "up")).toBe(operations);\n    expect(reorderOperations(operations, "c", "down")).toBe(operations);\n  });''',
)

print("Hotfix de reordenamiento rápido aplicado.")
