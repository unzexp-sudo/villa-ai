// FROZEN — TS mirror of engine/app/interior/contract.py.
// Orchestrator-owned. Do NOT edit. Derive everything from these shapes.
// Scene frame: +x right, +y up on the plan, +z up (metres above finished floor).

export const INTERIOR_DISCLAIMER =
  "Visual model only, not for construction. Preliminary design; licensed engineer review required.";
export const EYE_HEIGHT_M = 1.6;

export interface Pt2 { x: number; y: number }
export interface Pt3 { x: number; y: number; z: number }

/** One solid box of a wall after openings are cut out. p1/p2 are PLAN-space
 *  centreline endpoints; z0/z1 are the vertical extent, z0 < z1. */
export interface WallSegment { p1: Pt2; p2: Pt2; z0: number; z1: number }

export interface SceneWall {
  id: string;
  start: Pt2;
  end: Pt2;
  thickness: number;
  height: number;
  is_exterior: boolean;
  angle_rad: number;
  segments: WallSegment[];
}

export interface SceneOpening {
  id: string;
  wall_id: string;
  kind: "door" | "window" | "passage";
  at: number;
  width: number;
  sill: number;
  head: number;
  center: Pt3;
  angle_rad: number;
}

export interface SceneRoom {
  id: string;
  name: string;
  type: string;
  polygon: Pt2[];
  area_m2: number;
  ceiling_z: number;
  centroid: Pt2;
  eye: Pt3;
}

export interface InteriorScene {
  schema_version: "1.0";
  model_id: string;
  name: string;
  units: "metres";
  bbox: [number, number, number, number];
  ceiling_height: number;
  walls: SceneWall[];
  openings: SceneOpening[];
  rooms: SceneRoom[];
  disclaimer: string;
  generated_by: "deterministic";
}

export type ViewMode = "orbit" | "walk";
