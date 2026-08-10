type OrderedOperation = {
  id: string;
  setup_id: string;
  orden: number;
};

export function reorderOperations<T extends OrderedOperation>(operations: T[], operationId: string, direction: "up" | "down"): T[] {
  const selected = operations.find((item) => item.id === operationId);
  if (!selected) return operations;

  const setupOperations = operations
    .filter((item) => item.setup_id === selected.setup_id)
    .sort((left, right) => left.orden - right.orden);
  const index = setupOperations.findIndex((item) => item.id === operationId);
  const target = direction === "up" ? index - 1 : index + 1;
  if (index < 0 || target < 0 || target >= setupOperations.length) return operations;

  [setupOperations[index], setupOperations[target]] = [setupOperations[target], setupOperations[index]];
  const orderById = new Map(setupOperations.map((item, order) => [item.id, order]));
  return operations.map((item) => {
    const nextOrder = orderById.get(item.id);
    return nextOrder === undefined || nextOrder === item.orden ? item : { ...item, orden: nextOrder };
  });
}
