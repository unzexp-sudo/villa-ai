// FROZEN CONTRACT — mirror of engine/app/contract/*.py and docs/MODULE_CONTRACT.md.
// Orchestrator-owned. Do NOT edit. Derive everything from these constants.

export const PRODUCT_NAME = "Villa AI";

// API origin.
//
// Local dev: the Vite dev server runs on :5173 and the FastAPI engine runs
// on :8000 as a SEPARATE process, so the frontend needs the absolute origin.
//
// Production: the Docker image serves the built SPA and the /api routes from
// the SAME FastAPI process, so the correct origin is the page's own origin.
// `import.meta.env.DEV` is false in a production `vite build`, which makes
// this work with no build-time configuration and no CORS in production.
//
// `import.meta.env` is replaced statically by Vite at build time, so this
// constant stays a plain string and the tree-shaking behaviour is unchanged.
export const API_BASE = import.meta.env.DEV
  ? "http://localhost:8000"
  : window.location.origin;

export type SystemKind = "drainage" | "water" | "air" | "power" | "fire";
export const SYSTEM_ORDER: readonly SystemKind[] = [
  "drainage",
  "water",
  "air",
  "power",
  "fire",
] as const;

export const SYSTEM_COLORS: Readonly<Record<SystemKind, string>> = {
  drainage: "#f97316",
  water: "#3b82f6",
  air: "#22d3ee",
  power: "#eab308",
  fire: "#e6379c",
} as const;

// Red is reserved for clashes / failures; fire is magenta (docs/UX_SPEC.md).
export const CLASH_COLOR = "#ff3b30";
export const RESOLVER_COLOR = "#a78bfa";

export const SYSTEM_LABELS: Readonly<Record<SystemKind, string>> = {
  drainage: "Drainage",
  water: "Water supply",
  air: "Air & ventilation",
  power: "Power",
  fire: "Fire protection",
} as const;

export type BuildingType = "apartment" | "villa";
export type Country = "ID" | "VN" | "TH";
export type AgentPhase =
  | "queued"
  | "exploring"
  | "testing"
  | "committing"
  | "done"
  | "failed";
export type RuleStatus = "pass" | "fail" | "not_checked";
export type Severity = "error" | "warning" | "info";
export type ClashKind = "hard" | "clearance";
export type RunStatus = "pending" | "running" | "complete" | "failed";

// The ONE event vocabulary. Do not hand-copy a second list anywhere.
export const EVENT_NAMES = [
  "run.started",
  "agent.status",
  "agent.progress",
  "candidate.flicker",
  "layer.committed",
  "merge.done",
  "clash.found",
  "clash.resolved",
  "code.progress",
  "feed.line",
  "run.completed",
  "run.failed",
] as const;
export type EventName = (typeof EVENT_NAMES)[number];

// Client MUST close the EventSource on these, and ONLY on these.
export const TERMINAL_EVENTS = ["run.completed", "run.failed"] as const;
export type TerminalEvent = (typeof TERMINAL_EVENTS)[number];
export function isTerminal(name: string): name is TerminalEvent {
  return (TERMINAL_EVENTS as readonly string[]).includes(name);
}

export interface Point2D { x: number; y: number }
export interface Point3D { x: number; y: number; z: number }
export interface Size3D { length_mm: number; width_mm: number; height_mm: number }

export interface Wall {
  id: string; start: Point2D; end: Point2D;
  thickness: number; height: number; is_exterior: boolean;
}
export interface Opening {
  id: string; wall_id: string; at: number; width: number;
  kind: "door" | "window" | "passage"; sill: number; head: number;
}
export interface Room {
  id: string; name: string; type: string; polygon: Point2D[];
  wet: boolean; area_m2: number; ceiling_z: number;
}
export interface Fixture {
  id: string; kind: string; at: Point2D;
  room_id: string | null; z: number; props: Record<string, unknown>;
}
export interface Equipment {
  id: string; kind: string; at: Point2D;
  room_id: string | null; z: number; props: Record<string, unknown>;
}
export interface Shaft {
  id: string; polygon: Point2D[]; from_z: number; to_z: number;
  systems: SystemKind[];
}
export interface Level {
  id: string; name: string; elevation: number;
  floor_to_floor: number; ceiling_void: number;
  walls: Wall[]; openings: Opening[];
  rooms: Room[]; fixtures: Fixture[];
  equipment: Equipment[]; shafts: Shaft[];
}
export interface Scale {
  p1: Point2D; p2: Point2D; distance_m: number;
  calibrated_by: "user" | "fixture";
}
export interface SiteConnections {
  sewer_point: Point2D | null; septic_point: Point2D | null;
  water_meter: Point2D | null; power_incomer: Point2D | null;
}
export interface Assumption {
  id: string; field: string; value: string; reason: string;
  source: "default" | "user" | "skip" | "agent"; created_at: number;
}
export interface BuildingModel {
  schema_version: "1.0";
  id: string; name: string;
  building_type: BuildingType; country: Country;
  storeys: number; ceiling_height: number;
  levels: Level[];
  scale: Scale | null;
  site: SiteConnections;
  assumptions: Assumption[];
  source: "fixture" | "upload" | "quickdraw";
  created_at: number; updated_at: number;
}

export interface Segment {
  id: string; system: SystemKind;
  kind: "pipe" | "duct" | "cable" | "conduit" | "refrigerant" | "sprinkler_pipe";
  polyline: Point3D[];
  diameter_mm: number | null;
  width_mm: number | null; height_mm: number | null;
  slope_pct: number; material: string;
  meta: Record<string, unknown>;
}
export interface Device {
  id: string; system: SystemKind; kind: string;
  at: Point3D; size: Size3D; meta: Record<string, unknown>;
}
export interface Layer {
  system: SystemKind; agent: string;
  status: "pending" | "running" | "done" | "failed";
  segments: Segment[]; devices: Device[];
  metrics: Record<string, number>;
  notes: string[];
  strategy: Record<string, unknown>;
}

export interface Clash {
  id: string; kind: ClashKind;
  a: string; b: string;
  system_a: SystemKind; system_b: SystemKind;
  at: Point3D; distance_mm: number;
  resolved: boolean; fix: string | null;
}
export interface RuleOutcome {
  rule_id: string; status: RuleStatus; detail: string;
  evidence: string[]; severity: Severity;
}
export interface CodeReport {
  country: Country; rule_pack_version: string;
  total: number; passed: number; failed: number; not_checked: number;
  outcomes: RuleOutcome[]; headline: string;
}
export interface MissingItem {
  id: string; kind: string; label: string; prompt: string;
  severity: Severity; system: SystemKind | null;
  suggested_at: Point2D | null;
  snap: "wall" | "corner" | "wet_room" | "outside" | "roof" | "any";
  options: ("drag" | "suggest" | "skip")[];
}

export interface AgentEvent {
  run_id: string; seq: number; ts: number;
  name: EventName;
  agent: string;
  phase: AgentPhase;
  layouts_tested: number;
  best_score: number | null;
  message: string;
  metrics: Record<string, number>;
  layer: Layer | null;
}

export interface RunResult {
  run_id: string; model_id: string; status: RunStatus;
  country: Country; enabled_systems: SystemKind[];
  layers: Partial<Record<SystemKind, Layer>>;
  clashes_before: Clash[]; clashes_after: Clash[];
  code: CodeReport | null;
  events: AgentEvent[];
  assumptions: Assumption[];
  metrics: Record<string, number>;
  started_at: number; finished_at: number | null;
  error: string | null;
}

/**
 * The first (and in v1 the only) level. Mirrors `BuildingModel.level0` on the
 * server, which is a property there — TS cannot mirror a property that reads
 * off `levels[0]` without a getter, so this is the one place the two differ in
 * shape. v1 designs level 0 only (MODULE_CONTRACT.md §2).
 */
export function level0(model: BuildingModel): Level {
  return model.levels[0];
}

/**
 * Plan bounding box from architectural geometry (walls, room polygons, shafts).
 *
 * Mirrors `BuildingModel.bbox()` on the server, including the exclusion of site
 * connection points and outdoor equipment — those legitimately sit outside the
 * footprint, and letting them stretch the view shrinks the plan to a corner.
 * Returns metres, `[minx, miny, maxx, maxy]`.
 */
export function modelBBox(model: BuildingModel): [number, number, number, number] {
  let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
  const add = (x: number, y: number) => {
    if (x < minx) minx = x;
    if (x > maxx) maxx = x;
    if (y < miny) miny = y;
    if (y > maxy) maxy = y;
  };
  for (const lvl of model.levels) {
    for (const w of lvl.walls) { add(w.start.x, w.start.y); add(w.end.x, w.end.y); }
    for (const r of lvl.rooms) for (const p of r.polygon) add(p.x, p.y);
    for (const s of lvl.shafts) for (const p of s.polygon) add(p.x, p.y);
  }
  if (!Number.isFinite(minx)) return [0, 0, 0, 0];
  return [minx, miny, maxx, maxy];
}

export const DEFAULT_BANDS: Readonly<Record<SystemKind, readonly [number, number]>> = {
  drainage: [2.55, 2.95],
  water: [2.3, 2.6],
  air: [2.05, 2.35],
  power: [2.6, 2.9],
  fire: [2.9, 3.1],
} as const;
