import { useMemo, useState } from "react";

import type { AgujeroAlineacion, ProjectPayload } from "../types";

const emptyHole: AgujeroAlineacion = {
  x_mm: 0,
  y_mm: 0,
  diametro_mm: 3,
};

type ProjectFormProps = {
  initialValue?: ProjectPayload;
  mode: "create" | "edit";
  onSubmit: (payload: ProjectPayload) => Promise<void>;
  submitting: boolean;
};

type FormState = {
  nombre: string;
  ancho: string;
  alto: string;
  espesor: string;
  dobleCara: boolean;
  ejeVolteo: "x" | "y" | "";
  agujeros: AgujeroAlineacion[];
};

function createInitialState(initialValue?: ProjectPayload): FormState {
  return {
    nombre: initialValue?.nombre ?? "",
    ancho: initialValue ? String(initialValue.material.ancho_mm) : "80",
    alto: initialValue ? String(initialValue.material.alto_mm) : "50",
    espesor:
      initialValue?.material.espesor_mm != null
        ? String(initialValue.material.espesor_mm)
        : "1.6",
    dobleCara: initialValue?.doble_cara ?? false,
    ejeVolteo: (initialValue?.eje_volteo as "x" | "y" | null) ?? "",
    agujeros: initialValue?.agujeros_alineacion ?? [],
  };
}

export function ProjectForm({ initialValue, mode, onSubmit, submitting }: ProjectFormProps) {
  const [state, setState] = useState<FormState>(() => createInitialState(initialValue));
  const [error, setError] = useState<string>("");

  const title = useMemo(
    () => (mode === "create" ? "Crear proyecto" : "Editar proyecto"),
    [mode]
  );

  const updateHole = (index: number, field: keyof AgujeroAlineacion, value: string) => {
    const numericValue = value === "" ? null : Number(value);
    setState((current) => ({
      ...current,
      agujeros: current.agujeros.map((hole, holeIndex) =>
        holeIndex === index
          ? {
              ...hole,
              [field]: numericValue,
            }
          : hole
      ),
    }));
  };

  const validate = (): string | null => {
    if (!state.nombre.trim()) {
      return "El nombre del proyecto es obligatorio.";
    }
    if (Number(state.ancho) <= 0 || Number(state.alto) <= 0) {
      return "Las dimensiones del material deben ser positivas.";
    }
    if (state.espesor && Number(state.espesor) <= 0) {
      return "El espesor del material debe ser positivo.";
    }
    if (state.dobleCara && !state.ejeVolteo) {
      return "Seleccione el eje de volteo para una PCB de doble cara.";
    }
    for (const hole of state.agujeros) {
      if (hole.x_mm < 0 || hole.y_mm < 0) {
        return "Los agujeros de alineacion no pueden tener coordenadas negativas.";
      }
      if (hole.diametro_mm != null && hole.diametro_mm <= 0) {
        return "El diametro de cada agujero debe ser positivo.";
      }
    }
    return null;
  };

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const validationError = validate();
    if (validationError) {
      setError(validationError);
      return;
    }
    setError("");
    await onSubmit({
      nombre: state.nombre.trim(),
      material: {
        ancho_mm: Number(state.ancho),
        alto_mm: Number(state.alto),
        espesor_mm: state.espesor ? Number(state.espesor) : null,
      },
      doble_cara: state.dobleCara,
      eje_volteo: state.dobleCara ? state.ejeVolteo : null,
      agujeros_alineacion: state.dobleCara ? state.agujeros : [],
    });
    if (mode === "create") {
      setState(createInitialState());
    }
  };

  return (
    <form className="panel project-form" onSubmit={handleSubmit}>
      <div className="panel__header">
        <div>
          <p className="eyebrow">Proyecto PCB</p>
          <h2>{title}</h2>
        </div>
      </div>

      <label>
        Nombre del proyecto
        <input
          value={state.nombre}
          onChange={(event) => setState((current) => ({ ...current, nombre: event.target.value }))}
          placeholder="PCB controlador"
        />
      </label>

      <div className="grid grid--triple">
        <label>
          Ancho del material (mm)
          <input
            type="number"
            min="0.01"
            step="0.01"
            value={state.ancho}
            onChange={(event) => setState((current) => ({ ...current, ancho: event.target.value }))}
          />
        </label>
        <label>
          Alto del material (mm)
          <input
            type="number"
            min="0.01"
            step="0.01"
            value={state.alto}
            onChange={(event) => setState((current) => ({ ...current, alto: event.target.value }))}
          />
        </label>
        <label>
          Espesor (mm)
          <input
            type="number"
            min="0.01"
            step="0.01"
            value={state.espesor}
            onChange={(event) => setState((current) => ({ ...current, espesor: event.target.value }))}
          />
        </label>
      </div>

      <label className="toggle-row">
        <input
          type="checkbox"
          checked={state.dobleCara}
          onChange={(event) =>
            setState((current) => ({
              ...current,
              dobleCara: event.target.checked,
              ejeVolteo: event.target.checked ? current.ejeVolteo : "",
              agujeros: event.target.checked ? current.agujeros : [],
            }))
          }
        />
        PCB de doble cara
      </label>

      {state.dobleCara ? (
        <>
          <label>
            Eje de volteo
            <select
              value={state.ejeVolteo}
              onChange={(event) =>
                setState((current) => ({
                  ...current,
                  ejeVolteo: event.target.value as "x" | "y" | "",
                }))
              }
            >
              <option value="">Seleccione un eje</option>
              <option value="x">Eje X</option>
              <option value="y">Eje Y</option>
            </select>
          </label>

          <div className="holes-editor">
            <div className="section-heading">
              <h3>Agujeros de alineacion</h3>
              <button
                type="button"
                className="button button--ghost"
                onClick={() =>
                  setState((current) => ({
                    ...current,
                    agujeros: [...current.agujeros, { ...emptyHole }],
                  }))
                }
              >
                Añadir agujero
              </button>
            </div>
            {state.agujeros.length === 0 ? (
              <p className="muted">Opcional. Puede agregarlos ahora o mas adelante.</p>
            ) : null}
            {state.agujeros.map((hole, index) => (
              <div className="grid grid--triple hole-row" key={`${index}-${hole.x_mm}-${hole.y_mm}`}>
                <label>
                  X (mm)
                  <input
                    type="number"
                    min="0"
                    step="0.01"
                    value={hole.x_mm}
                    onChange={(event) => updateHole(index, "x_mm", event.target.value)}
                  />
                </label>
                <label>
                  Y (mm)
                  <input
                    type="number"
                    min="0"
                    step="0.01"
                    value={hole.y_mm}
                    onChange={(event) => updateHole(index, "y_mm", event.target.value)}
                  />
                </label>
                <label>
                  Diametro (mm)
                  <input
                    type="number"
                    min="0.01"
                    step="0.01"
                    value={hole.diametro_mm ?? ""}
                    onChange={(event) => updateHole(index, "diametro_mm", event.target.value)}
                  />
                </label>
                <button
                  type="button"
                  className="button button--ghost button--danger"
                  onClick={() =>
                    setState((current) => ({
                      ...current,
                      agujeros: current.agujeros.filter((_, holeIndex) => holeIndex !== index),
                    }))
                  }
                >
                  Eliminar agujero
                </button>
              </div>
            ))}
          </div>
        </>
      ) : null}

      {error ? <p className="form-error">{error}</p> : null}
      <button className="button" type="submit" disabled={submitting}>
        {submitting ? "Guardando..." : mode === "create" ? "Crear proyecto" : "Guardar cambios"}
      </button>
    </form>
  );
}
