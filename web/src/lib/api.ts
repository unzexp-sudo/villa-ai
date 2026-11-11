// Typed client for the Villa AI engine. No SDK, no retries, no magic:
// the SSE rule (close on terminal, dedupe by seq) lives in sse.ts.

import type {
  BuildingModel,
  CodeReport,
  MissingItem,
  RunResult,
  SystemKind,
} from "../contract";
import { API_BASE } from "../contract";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      /* body was not JSON; the status line is the best we have */
    }
    throw new Error(detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export interface SampleSummary {
  id: string;
  name: string;
  building_type: string;
  rooms: number;
  slug: string;
}

// --- Lead gate ---------------------------------------------------------------
// The server-side gate is in engine/app/api/leads.py. In dev the gate is OFF,
// so these calls succeed and return a dev_token. In production with
// REQUIRE_LEAD_GATE=true, the token must be present on POST /api/runs.

const TOKEN_KEY = "villa-ai-session-token";

export const lead = {
  submit: (body: {
    name: string;
    email: string;
    role: string;
    company?: string;
    country?: string;
    project_type?: string;
    phone?: string;
    consent: boolean;
  }) =>
    req<{ lead_id: string; status: string; dev_token: string | null }>("/api/leads", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  verify: (token: string) =>
    req<{ status: string; session_token: string; lead_id: string }>("/api/leads/verify", {
      method: "POST",
      body: JSON.stringify({ token }),
    }),

  saveToken: (token: string) => {
    try { localStorage.setItem(TOKEN_KEY, token); } catch { /* private mode */ }
  },

  getToken: (): string | null => {
    try { return localStorage.getItem(TOKEN_KEY); } catch { return null; }
  },

  clearToken: () => {
    try { localStorage.removeItem(TOKEN_KEY); } catch { /* private mode */ }
  },
};

export const api = {
  health: () => req<{ ok: boolean; version: string }>("/api/health"),

  listSamples: () => req<{ samples: SampleSummary[] }>("/api/samples"),

  sample: (slug: string) => req<BuildingModel>(`/api/samples/${slug}`),

  createModel: (payload: BuildingModel) =>
    req<{ model_id: string; model: BuildingModel }>("/api/models", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  getModel: (id: string) => req<BuildingModel>(`/api/models/${id}`),

  patchModel: (id: string, patch: Record<string, unknown>) =>
    req<BuildingModel>(`/api/models/${id}`, {
      method: "PATCH",
      body: JSON.stringify(patch),
    }),

  deleteModel: (id: string) =>
    req<void>(`/api/models/${id}`, { method: "DELETE" }),

  gaps: (id: string) => req<{ items: MissingItem[]; warnings?: string[] }>(`/api/models/${id}/gaps`),

  addItem: (
    id: string,
    body: { kind: string; at?: { x: number; y: number }; action: "place" | "suggest" | "skip" },
  ) =>
    req<BuildingModel>(`/api/models/${id}/items`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  calibrate: (id: string, p1: { x: number; y: number }, p2: { x: number; y: number }, distance_m: number) =>
    req<BuildingModel>(`/api/models/${id}/scale`, {
      method: "POST",
      body: JSON.stringify({ p1, p2, distance_m }),
    }),

  /** One-step upload: create a model directly from a file. */
  uploadCreate: async (file: File): Promise<BuildingModel> => {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`${API_BASE}/api/models/upload`, {
      method: "POST",
      body: form,
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(typeof body.detail === "string" ? body.detail : "upload failed");
    }
    return (await res.json()) as BuildingModel;
  },

  /** Two-step upload: replace geometry on an existing model id. */
  upload: async (id: string, file: File): Promise<BuildingModel> => {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(`${API_BASE}/api/models/${id}/upload`, {
      method: "POST",
      body: form,
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(typeof body.detail === "string" ? body.detail : "upload failed");
    }
    return (await res.json()) as BuildingModel;
  },

  startRun: (model_id: string, enabled_systems?: SystemKind[]) => {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    const token = lead.getToken();
    if (token) headers["x-session-token"] = token;
    return req<{ run_id: string; status: string; enabled_systems: SystemKind[] }>("/api/runs", {
      method: "POST",
      body: JSON.stringify({ model_id, enabled_systems }),
      headers,
    });
  },

  getRun: (runId: string) => req<RunResult>(`/api/runs/${runId}`),

  reportUrl: (runId: string) => `${API_BASE}/api/runs/${runId}/report.pdf`,
  exportUrl: (runId: string, ext: "dxf" | "gltf" | "ifc") =>
    `${API_BASE}/api/runs/${runId}/export.${ext}`,

  /** Local fallback when the engine is not running: keeps the page explorable. */
  runFromSampleLocally: (model: BuildingModel): LocalSampleRun => ({
    model,
    code: null as CodeReport | null,
  }),
};

export interface LocalSampleRun {
  model: BuildingModel;
  code: CodeReport | null;
}
