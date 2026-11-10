// ORCHESTRATOR-owned. The only frontend gateway to the interior endpoints.
// Offline-first: these are read-only, deterministic GETs with zero credentials.

import { API_BASE } from "../contract";
import type { InteriorScene } from "./types";

/** Fetch the bundled sample scene, or a stored model's scene by id. */
export async function fetchInteriorScene(modelId?: string): Promise<InteriorScene> {
  const path = modelId
    ? `/interior/scene/${encodeURIComponent(modelId)}`
    : "/interior/scene";
  // API_BASE is the bare origin (e.g. http://localhost:8000); the router is
  // mounted under /api by engine/main.py, so the full URL needs /api here.
  const res = await fetch(`${API_BASE}/api${path}`);
  if (!res.ok) {
    throw new Error(`Interior scene request failed (${res.status} ${res.statusText})`);
  }
  return (await res.json()) as InteriorScene;
}
