from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one match, found {count}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")
    print(f"patched {path}")


replace_once(
    "frontend/src/features/projects/ProjectWorkspace.tsx",
    '                <input\n                  value={compensationToleranceInput}\n                  inputMode="decimal"',
    '                <input\n                  value={compensationToleranceInput}\n                  inputMode="decimal"\n                  disabled={compensationControlsBusy}',
)

replace_once(
    "frontend/src/features/projects/ProjectWorkspace.tsx",
    '        busy={referenceBusy}\n        onPrepare={prepareJobRun}',
    '        busy={referenceBusy || compensationBusy}\n        onPrepare={prepareJobRun}',
)

replace_once(
    "frontend/src/features/projects/ProjectWorkspace.test.tsx",
    '    expect(await screen.findByRole("button", { name: /Generando compensación…/i })).toBeDisabled();\n    expect(apiMock.generateProjectCompensation).toHaveBeenCalledTimes(1);\n    expect(screen.getByRole("button", { name: /Revalidar plan/i })).toBeDisabled();',
    '    expect(await screen.findByRole("button", { name: /Generando compensación…/i })).toBeDisabled();\n    expect(apiMock.generateProjectCompensation).toHaveBeenCalledTimes(1);\n    expect(screen.getByRole("button", { name: /Revalidar plan/i })).toBeDisabled();\n    expect(screen.getByRole("button", { name: "Iniciar trabajo" })).toBeDisabled();\n    expect(screen.getByLabelText(/Tolerancia Z \(mm\)/i)).toBeDisabled();',
)

print("PR14 final concurrency guard applied successfully.")
