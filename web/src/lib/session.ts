/**
 * Shared session store for the active BuildingModel.
 *
 * Persists the model id + a serialised snapshot to localStorage so that:
 * 1. Switching tabs (Studio -> Interior 3D -> Studio) does not lose the
 *    uploaded/selected model.
 * 2. The Interior 3D page can load the scene for the user's uploaded model
 *    instead of always falling back to the bundled sample fixture.
 *
 * The model object is stored as JSON. For large models this is fine because
 * the JsonFileStore on the server already keeps the authoritative copy; the
 * localStorage snapshot is a convenience for instant tab-restore without a
 * network round-trip. If localStorage is unavailable (private mode, quota),
 * every call degrades gracefully.
 */

import type { BuildingModel } from "../contract";

const KEY = "villa-ai-active-model";

export const session = {
  /** Save the active model so other tabs / re-mounts can restore it. */
  setActiveModel(model: BuildingModel): void {
    try {
      localStorage.setItem(KEY, JSON.stringify(model));
    } catch {
      /* private mode or quota — non-fatal */
    }
  },

  /** Get the active model id, or null if none was saved. */
  getActiveModelId(): string | null {
    try {
      const raw = localStorage.getItem(KEY);
      if (!raw) return null;
      const parsed = JSON.parse(raw) as BuildingModel;
      return parsed?.id ?? null;
    } catch {
      return null;
    }
  },

  /** Get the full active model snapshot, or null. */
  getActiveModel(): BuildingModel | null {
    try {
      const raw = localStorage.getItem(KEY);
      if (!raw) return null;
      return JSON.parse(raw) as BuildingModel;
    } catch {
      return null;
    }
  },

  /** Clear the active model (e.g. when the user starts fresh). */
  clearActiveModel(): void {
    try {
      localStorage.removeItem(KEY);
    } catch {
      /* private mode — non-fatal */
    }
  },
};
