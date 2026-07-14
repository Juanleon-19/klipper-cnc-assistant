import type {
  HealthResponse,
  HeightMap,
  MachineSession,
  Operation,
  OperationAnalysis,
  Project,
  ProjectPayload,
  SystemInfoResponse,
} from "../types";

export type OperationInput = {
  nombre: string;
  tipo: string;
  cara: string;
  orden: number;
  herramienta?: string;
};

async function request<T>(input: RequestInfo, init?: RequestInit): Promise<T> {
  const response = await fetch(input, {
    headers: {
      ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...init?.headers,
    },
    ...init,
  });

  if (!response.ok) {
    const payload = (await response.json().catch(() => ({ detalhe: "Error desconocido." }))) as {
      detalle?: string;
    };
    throw new Error(payload.detalle ?? "Error desconocido.");
  }
  return (await response.json()) as T;
}

export const api = {
  getHealth: () => request<HealthResponse>("/api/health"),
  getSystemInfo: () => request<SystemInfoResponse>("/api/system/info"),
  getMachineSession: () => request<MachineSession>("/api/machine/session"),
  listProjects: () => request<Project[]>("/api/projects"),
  getProject: (projectId: string) => request<Project>(`/api/projects/${projectId}`),
  createProject: (payload: ProjectPayload) =>
    request<Project>("/api/projects", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateProject: (projectId: string, payload: ProjectPayload) =>
    request<Project>(`/api/projects/${projectId}`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  addOperation: (projectId: string, payload: OperationInput) =>
    request<Operation>(`/api/projects/${projectId}/operations`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  deleteOperation: (projectId: string, operationId: string) =>
    request<{ detalle: string }>(`/api/projects/${projectId}/operations/${operationId}`, {
      method: "DELETE",
    }),
  uploadOperationFile: (projectId: string, operationId: string, file: File) => {
    const formData = new FormData();
    formData.append("archivo", file);
    return request<Operation>(`/api/projects/${projectId}/operations/${operationId}/gcode`, {
      method: "POST",
      body: formData,
    });
  },
  removeOperationFile: (projectId: string, operationId: string) =>
    request<Operation>(`/api/projects/${projectId}/operations/${operationId}/gcode`, {
      method: "DELETE",
    }),
  analyzeOperation: (projectId: string, operationId: string) =>
    request<OperationAnalysis>(`/api/projects/${projectId}/operations/${operationId}/analyze`, {
      method: "POST",
    }),
  getHeightMap: (projectId: string, operationId: string) =>
    request<HeightMap>(`/api/projects/${projectId}/operations/${operationId}/height-map`),
  configureHeightMap: (projectId: string, operationId: string, payload: { filas: number; columnas: number }) =>
    request<HeightMap>(`/api/projects/${projectId}/operations/${operationId}/height-map/config`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  simulateHeightMap: (projectId: string, operationId: string, payload: { filas: number; columnas: number; escenario: string; semilla: number }) =>
    request<HeightMap>(`/api/projects/${projectId}/operations/${operationId}/height-map/simulate`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  importHeightMapJson: (projectId: string, operationId: string, contenido: string) =>
    request<HeightMap>(`/api/projects/${projectId}/operations/${operationId}/height-map/import/json`, {
      method: "POST",
      body: JSON.stringify({ contenido }),
    }),
  importHeightMapCsv: (projectId: string, operationId: string, contenido: string) =>
    request<HeightMap>(`/api/projects/${projectId}/operations/${operationId}/height-map/import/csv`, {
      method: "POST",
      body: JSON.stringify({ contenido }),
    }),
  updateHeightMapSample: (
    projectId: string,
    operationId: string,
    sampleId: string,
    payload: { z_mm?: number | null; incluida?: boolean; observacion?: string | null }
  ) =>
    request<HeightMap>(`/api/projects/${projectId}/operations/${operationId}/height-map/samples/${sampleId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  recalculateHeightMap: (projectId: string, operationId: string) =>
    request<HeightMap>(`/api/projects/${projectId}/operations/${operationId}/height-map/recalculate`, {
      method: "POST",
    }),
  deleteHeightMap: (projectId: string, operationId: string) =>
    request<{ detalle: string }>(`/api/projects/${projectId}/operations/${operationId}/height-map`, {
      method: "DELETE",
    }),
};
