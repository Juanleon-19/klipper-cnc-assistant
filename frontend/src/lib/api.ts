import type {
  CompensationPreview,
  HealthResponse,
  HeightMap,
  MachineSession,
  Operation,
  OperationAnalysis,
  Project,
  ProjectPayload,
  ReferenceConfirmation,
  ReferenceSession,
  SystemInfoResponse,
} from "../types";

export type OperationInput = {
  nombre: string;
  tipo: string;
  cara: string;
  orden: number;
  herramienta?: string;
};

export class ApiError extends Error {
  status: number;
  fieldErrors: Record<string, string>;

  constructor(message: string, status: number, fieldErrors: Record<string, string> = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.fieldErrors = fieldErrors;
  }
}

function translateFastApiDetail(detail: unknown): { message: string; fieldErrors: Record<string, string> } {
  if (typeof detail === "string" && detail.trim()) {
    const fieldErrors: Record<string, string> = {};
    for (const field of ["x_mm", "y_mm", "z_mm"]) {
      if (detail.includes(`${field}:`)) {
        fieldErrors[field] = detail;
      }
    }
    return { message: detail, fieldErrors };
  }

  if (Array.isArray(detail)) {
    const fieldErrors: Record<string, string> = {};
    const messages = detail
      .map((item) => {
        if (!item || typeof item !== "object") {
          return null;
        }
        const record = item as Record<string, unknown>;
        const location = Array.isArray(record.loc)
          ? record.loc.filter((part) => part !== "body").map(String).join(".")
          : "solicitud";
        const message = typeof record.msg === "string" ? record.msg : "valor inválido";
        const translated = `${location || "solicitud"}: ${message}.`;
        if (location) {
          fieldErrors[location] = translated;
        }
        return translated;
      })
      .filter((value): value is string => Boolean(value));
    return {
      message: messages.length > 0 ? `Solicitud inválida. ${messages.join(" ")}` : "Solicitud inválida.",
      fieldErrors,
    };
  }

  return { message: "La solicitud no pudo validarse.", fieldErrors: {} };
}

async function request<T>(input: RequestInfo, init?: RequestInit): Promise<T> {
  const response = await fetch(input, {
    headers: {
      ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...init?.headers,
    },
    ...init,
  });

  if (!response.ok) {
    const payload = (await response.json().catch(() => ({}))) as {
      detalle?: unknown;
      detail?: unknown;
    };
    const rawDetail = payload.detalle ?? payload.detail;
    const { message, fieldErrors } = translateFastApiDetail(rawDetail);
    throw new ApiError(message, response.status, fieldErrors);
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
  getReferenceSession: (projectId: string, operationId: string) =>
    request<ReferenceSession>(`/api/projects/${projectId}/operations/${operationId}/reference-session`),
  confirmMachineReference: (projectId: string, operationId: string) =>
    request<ReferenceSession>(`/api/projects/${projectId}/operations/${operationId}/reference-session/machine-reference`, {
      method: "POST",
    }),
  confirmWorkOrigin: (projectId: string, operationId: string, payload: ReferenceConfirmation) =>
    request<ReferenceSession>(`/api/projects/${projectId}/operations/${operationId}/reference-session/work-origin`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  confirmZReference: (projectId: string, operationId: string, payload: Required<ReferenceConfirmation>) =>
    request<ReferenceSession>(`/api/projects/${projectId}/operations/${operationId}/reference-session/z-reference`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getHeightMap: (projectId: string, operationId: string) =>
    request<HeightMap>(`/api/projects/${projectId}/operations/${operationId}/height-map`),
  configureHeightMap: (projectId: string, operationId: string, payload: Record<string, unknown>) =>
    request<HeightMap>(`/api/projects/${projectId}/operations/${operationId}/height-map/config`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  simulateHeightMap: (projectId: string, operationId: string, payload: Record<string, unknown>) =>
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
  validateHeightMap: (projectId: string, operationId: string) =>
    request<ReferenceSession>(`/api/projects/${projectId}/operations/${operationId}/height-map/validate`, {
      method: "POST",
    }),
  getCompensationPreview: (projectId: string, operationId: string) =>
    request<{ session: ReferenceSession; preview: CompensationPreview }>(`/api/projects/${projectId}/operations/${operationId}/compensation-preview`, {
      method: "POST",
    }),
  deleteHeightMap: (projectId: string, operationId: string) =>
    request<{ detalle: string }>(`/api/projects/${projectId}/operations/${operationId}/height-map`, {
      method: "DELETE",
    }),
};
