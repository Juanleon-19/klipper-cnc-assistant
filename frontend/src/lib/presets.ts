export type OperationPreset = {
  clave: string;
  etiqueta: string;
  descripcion: string;
  tipo: string;
  cara: string;
  orden: number;
  herramienta?: string;
};

export const operationPresets: OperationPreset[] = [
  {
    clave: "fresado-superior",
    etiqueta: "Fresado cara superior",
    descripcion: "Aislamiento o limpieza en la cara superior.",
    tipo: "aislamiento",
    cara: "superior",
    orden: 0,
    herramienta: "V-bit 30",
  },
  {
    clave: "fresado-inferior",
    etiqueta: "Fresado cara inferior",
    descripcion: "Aislamiento o limpieza en la cara inferior.",
    tipo: "aislamiento",
    cara: "inferior",
    orden: 1,
    herramienta: "V-bit 30",
  },
  {
    clave: "perforado",
    etiqueta: "Perforado",
    descripcion: "Taladrado de vias, pads y agujeros mecanicos.",
    tipo: "taladrado",
    cara: "superior",
    orden: 2,
    herramienta: "Broca 0.8",
  },
  {
    clave: "corte-contorno",
    etiqueta: "Corte del contorno",
    descripcion: "Perfilado exterior para separar la PCB del material.",
    tipo: "corte exterior",
    cara: "superior",
    orden: 3,
    herramienta: "Fresa 1.0",
  },
];
