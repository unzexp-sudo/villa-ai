# INTERIOR MODULE CONTRACT — additive 3D model + walkthrough

**Status: FROZEN for this feature. This is an ADDITION, not a pivot.**
`docs/MODULE_CONTRACT.md` (the MEP engine) is untouched and remains authoritative for
everything it defines. Nothing in this file changes `SystemKind`, `EVENT_NAMES`,
`BuildingModel`, the SSE protocol, the lead gate, or the five-agent pipeline.

Decisions locked with the user on 2026-11-11:

| Decision | Locked value |
|---|---|
| Build target | This workspace (Villa AI), a new `/interior` section on the existing website |
| Reference product | `https://homistry.ai/` — feature vocabulary and visual-disclaimer stance only |
| v1 scope | **3D model + walkthrough ONLY** |
| Explicitly out of scope | Furniture to scale, finishes/materials, AI photoreal renders, style presets, sun study, private sharing, AI credits, payments |
| Image provider | **None.** Offline-first. No external calls, no credentials, no fake images |
| Determinism | Pure function of `BuildingModel`. `seed=42` spirit: no wall clock, no randomness |

## 0. Why this is additive

The interior feature reads `BuildingModel` (which already contains walls, openings,
rooms and a shared coordinate frame). It writes **nothing** to `RunResult`, emits no
SSE events, and adds no `SystemKind`. It is a read-only projection of the plan into a
3D scene. The MEP engine cannot observe that it exists.

## 1. Units and frame (inherited from MODULE_CONTRACT.md §1 — FROZEN)

- Geometry in **METRES**. `Point2D{x,y}` on the plan, `z` metres above finished floor.
- Origin `(0,0)` = lower-left of the plan bounding box; `+x` right, `+y` up on screen; `+z` up.
- Wall `thickness` and `height` are metres. Opening `at`, `width`, `sill`, `head` are metres.
- **`EYE_HEIGHT_M = 1.6`** — walkthrough camera height above finished floor.
- `bbox` is `[min_x, min_y, max_x, max_y]`.

## 2. Disclaimer (mandatory, visible in the UI)

Exact string, produced by the backend and rendered by the frontend:

```
Visual model only, not for construction. Preliminary design; licensed engineer review required.
```

Never emit "code compliant", "construction drawing", or "stamped drawings" anywhere in
this feature. This matches both homistry.ai's "Visual model only, not for construction"
and our own liability stance.

## 3. Directory layout and OWNERSHIP (no two modules own one file)

```
docs/INTERIOR_MODULE_CONTRACT.md     [ORCHESTRATOR]
engine/app/interior/
├── __init__.py                     [ORCHESTRATOR]
├── contract.py                     [ORCHESTRATOR - FROZEN shapes, do not edit]
├── scene.py                        [I1]  build_scene(model) -> InteriorScene
└── routes.py                       [I1]  router with the two GET endpoints
engine/main.py                      [ORCHESTRATOR - router registration only]
engine/tests/test_interior_scene.py [I4]
web/src/interior/
├── types.ts                        [ORCHESTRATOR - FROZEN TS mirror, do not edit]
├── api.ts                          [ORCHESTRATOR - do not edit]
├── Viewer3D.tsx                    [I2]  MUST default-export Viewer3D
├── Interior.tsx                    [I3]  MUST default-export Interior
└── interior.css                    [I3]
web/src/App.tsx                     [ORCHESTRATOR - route + nav link only]
web/src/landing/Landing.tsx         [temporary placeholder by ORCHESTRATOR; M7 owns]
web/src/studio/Studio.tsx           [temporary placeholder by ORCHESTRATOR; M8 owns]
```

**Rules:** do not edit any file outside your ownership. Do not edit `contract.py`,
`types.ts`, `api.ts`, `main.py`, `App.tsx`, `styles.css`, `web/src/contract.ts`, or
`engine/app/contract/*`. A failure in a sibling's test file is that agent mid-flight —
ignore it, but your own tests must be green.

## 4. Frozen shapes (`engine/app/interior/contract.py` — read the file, it is authoritative)

```python
DISCLAIMER = "Visual model only, not for construction. Preliminary design; licensed engineer review required."
EYE_HEIGHT_M = 1.6

class Pt2(BaseModel):            x: float; y: float
class Pt3(BaseModel):            x: float; y: float; z: float

class WallSegment(BaseModel):
    p1: Pt2; p2: Pt2            # segment endpoints along the wall centreline
    z0: float; z1: float        # vertical extent, z0 < z1

class SceneWall(BaseModel):
    id: str; start: Pt2; end: Pt2
    thickness: float; height: float; is_exterior: bool
    angle_rad: float            # atan2(dy, dx) of start -> end
    segments: list[WallSegment] # solid pieces after openings are cut out

class SceneOpening(BaseModel):
    id: str; wall_id: str; kind: Literal["door","window","passage"]
    at: float; width: float; sill: float; head: float
    center: Pt3                 # centre of the opening in 3D
    angle_rad: float

class SceneRoom(BaseModel):
    id: str; name: str; type: str   # type is RoomType's lowercase value
    polygon: list[Pt2]; area_m2: float; ceiling_z: float
    centroid: Pt2; eye: Pt3         # eye = centroid at EYE_HEIGHT_M

class InteriorScene(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    model_id: str; name: str
    units: Literal["metres"] = "metres"
    bbox: tuple[float, float, float, float]
    ceiling_height: float
    walls: list[SceneWall]; openings: list[SceneOpening]; rooms: list[SceneRoom]
    disclaimer: str = DISCLAIMER
    generated_by: Literal["deterministic"] = "deterministic"
```

All shapes use `ConfigDict(extra="forbid")`. TS mirror is byte-for-byte in
`web/src/interior/types.ts`; derive from it, never hand-copy a second list of fields.

### Opening cutting (I1 must implement, not approximate)

A wall is a box of length `L`, thickness `t`, height `H`. Each `Opening` on that wall
spans `[at - width/2, at + width/2]` along the centreline. **Real gaps must exist**:

- `door` / `passage` → remove `[0, head]`, keep a lintel `[head, H]`.
- `window` → remove `[sill, head]`, keep `[0, sill]` and a lintel `[head, H]`.
- Clamp to `[0, L]`, sort by start, merge overlaps, keep every remaining solid interval.
- Every `WallSegment` has `z0 < z1`; a fully-open wall yields zero segments for that span.
- `SceneOpening.center` is the mid-span point at `z = (sill + head) / 2`.

`build_scene(model: BuildingModel) -> InteriorScene` must be **pure**: never mutate the
input model (clone if needed), never read the clock, never call the network.

## 5. HTTP contract

Base URL `http://localhost:8000`; CORS already allows `http://localhost:5173`.
Router is registered with prefix `/api` by `engine/main.py` (already done by the
orchestrator), so route paths below are relative to `/api`.

| Method | Path | Returns |
|---|---|---|
| GET | `/interior/scene` | `InteriorScene` for the bundled sample model `engine/fixtures/villa_120.json` |
| GET | `/interior/scene/{model_id}` | `InteriorScene` for a model in `JsonFileStore`; `404` if missing |

No POST, no upload, no auth, no rate limiting: these endpoints are free, deterministic and
read-only. They MUST work with zero external services and zero credentials.

`API_BASE` from `web/src/contract.ts` is the BARE ORIGIN (`http://localhost:8000`),
not `/api`. The frontend therefore calls `${API_BASE}/api/interior/scene` — the
`/api` prefix is added inside `web/src/interior/api.ts`, the only allowed gateway.
Never change `API_BASE` in the frozen MEP contract.

## 6. Frontend contract

- `Viewer3D` (I2) default export, props:
  `{ scene: InteriorScene; mode: "orbit" | "walk"; roomId: string | null; buildProgress: number }`.
  Renders walls, floors and openings with `react-three-fiber` 8 / `drei` 9 / `three` 0.169
  (already installed). `orbit` = exterior orbit; `walk` = eye-level camera at the selected
  room's `eye`. `buildProgress` in `[0,1]` scales wall height (the "walls rise" effect).
  Use only the existing dark theme tokens; do not edit `styles.css`.
- `Interior` (I3) default export, no props. Owns the page chrome: heading, the mandatory
  disclaimer banner, mode toggle (`3D model` / `Walk-through`), room tabs, build slider,
  loading/error branches, and a link back to the MEP studio. Mounts `Viewer3D`.
- No router dependency: `App.tsx` switches on `window.location.pathname`.

### 6.1 Three.js axis mapping (FROZEN — I2 uses exactly this)

The JSON scene uses the MEP frame (`+x` right, `+y` up on the plan, `+z` up).
Three.js is Y-up, so EVERY point converts through one function:

```ts
const toThree = (x: number, y: number, z: number): [number, number, number] => [x, z, -y];
```

A floor point `(x, y)` becomes `[x, 0, -y]`. Height `z` becomes three.js `y`.
This is a proper rotation (determinant +1), so plan handedness is preserved.

`WallSegment.p1` / `p2` are PLAN-space centreline endpoints; `z0` / `z1` are
heights. Render each segment as ONE box: `boxGeometry([len, z1 - z0, thickness])`,
where `len = hypot(p2.x - p1.x, p2.y - p1.y)`. Centre it at
`toThree(mid.x, mid.y, (z0 + z1) / 2)` and set `rotation.y = angle_rad`
(a plan direction `(cos a, sin a)` maps to three.js `(cos a, 0, -sin a)`, which is
exactly `R_y(a)` applied to local +X). Never draw a full-height wall across a gap.

`buildProgress` `p ∈ [0,1]` scales vertical extent only: rendered height =
`(z1 - z0) * p`, centre height = `(z0 + z1) / 2 * p`. Floors stay at `y = 0`.
Opening markers MUST use the same scaling (`height = (head - sill) * p`,
`centre z = center.z * p`) or they float above shortened walls.

`orbit` = exterior diagonal camera derived from `bbox` (≈0.65× span so the model
fills the viewer), `<OrbitControls makeDefault />` targeting the bbox centre.
`walk` = camera at the selected room's `eye` (converted), `OrbitControls` with
`enablePan={false}` targeting a point ~2 m ahead toward the **nearest opening**
(a real door/window gap; falls back to the bbox centre when there are none), plus
WASD / arrow keys that translate BOTH `camera.position` and `controls.target` by
the same horizontal delta, clamped inside `bbox` with a 0.4 m inset. Changing
`roomId` resets the camera to that room's eye.

## 7. Local dev / test rules (non-negotiable)

1. `GET /api/health` and both interior endpoints return 200 with zero credentials.
2. `build_scene` is deterministic: identical input → byte-identical JSON.
3. Full pytest suite stays green except for any sibling module mid-flight; I4 must
   report failures it did not cause rather than "fixing" them.
4. `npm run typecheck` must pass once all four modules land (orchestrator verifies).
5. Never claim the 3D model is a construction drawing. The disclaimer is visible.
6. No new npm or pip dependency. Everything needed is already installed.
