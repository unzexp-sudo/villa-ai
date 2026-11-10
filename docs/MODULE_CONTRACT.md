# MODULE CONTRACT — Villa AI (Multi-Agent MEP Design Tool)

**Status: FROZEN. Read this before writing any code.** Written by the orchestrator before
fan-out. If reality diverges from this file, the *code* is the bug — report it, do not
silently re-interpret.

Source plan: `Build Plan Multi-Agent MEP Design Tool.pdf` (Draft v1, 2026-11-10, 12 pp).

---

## 0. Locked assumptions (the plan's "decisions still open", resolved)

| # | Decision | Locked value |
|---|---|---|
| 1 | Country pack first | `ID` (Indonesia) + international baseline. `VN`, `TH` are declared stubs. |
| 2 | v1 building scope | `apartment`, `villa`. Single level. <= 300 m2. |
| 3 | Fire agent default | ON for `apartment`, OFF for `villa` unless explicitly enabled. |
| 4 | Engineer partner | Out of scope for code. All standard refs carry `verified: false`. |
| 5 | Monetization | Out of scope. Free tier = 1 plan / 3 runs, enforced in config only. |
| 6 | Name / domain | Working name **Villa AI**. Single constant `PRODUCT_NAME`. No domain. |

**Environment reality (recon 2026-11-10):** empty workspace, no git history, Node 22.22.2,
Python 3.13.12, **no Redis, no Docker, no Postgres, no LLM API key**. Therefore:
in-process `ThreadPoolExecutor`, JSON-file store, pluggable LLM port with a deterministic
fallback planner. Every external dependency sits behind a Protocol so Celery / Postgres /
Anthropic can be swapped in without touching the engine. **The app MUST boot and the full
suite MUST pass with zero external services and zero credentials.**

### Stack deviations from the plan's "practical default" (explicitly a suggestion)

| Plan suggests | We use | Why |
|---|---|---|
| Next.js | Vite + React 18 + TypeScript | No SSR need in v1; one less build system; the API contract is framework-agnostic, so swapping to Next.js is a frontend-only change. |
| Tailwind | Plain CSS with CSS custom properties | Avoids a PostCSS toolchain in a constrained sandbox. |
| Celery + Redis / Postgres | `ThreadPoolExecutor` + `JsonFileStore` behind Protocols | Nothing installed locally. |
| Claud* LLM | `Planner` Protocol + `DeterministicPlanner` (default) | No key. `ApiPlanner` is a lazy, optional import. |

---

## 1. Units, coordinates, and the vertical frame (FROZEN)

This is the single most important convention. Five agents write layers independently;
without a shared frame the clash detector compares nonsense.

- **All geometry in the Building Model is METRES.** `Point2D{x,y}` in metres on the plan.
- **All component sizes are MILLIMETRES** (`diameter_mm`, `width_mm`, `height_mm`,
  `clearance_mm`, `distance_mm`). Never mix. Prefix makes the unit explicit.
- **`z` is metres above finished floor level of level 0.** `+z` is up.
- Origin `(0,0)` = lower-left of the plan bounding box. `+x` right, `+y` up on screen.
- Polyline segments are 3D centrelines ordered from source to terminal.
- **Default preferred height bands** (`DEFAULT_BANDS`, in metres, centreline z). These
  are *soft hints for scoring only* in v1 — they do NOT forbid a route. The plan's
  "vertical zoning convention" is post-v1; we do not implement it as a constraint.

| System | Band (m) | Rationale |
|---|---|---|
| `drainage` | 2.55 – 2.95 | Gravity, high in ceiling void, must keep slope. |
| `water` | 2.30 – 2.60 | Above drainage conflict zone. |
| `air` | 2.05 – 2.35 | Ducts are largest; lowest band. |
| `power` | 2.60 – 2.90 | Cables small, can share high band. |
| `fire` | 2.90 – 3.10 | Sprinklers need ceiling proximity. |

- `ceiling_height` default `2.7`, `ceiling_void` default `0.35`, `slab_thickness` `0.15`.
- **Clash clearance default `100 mm`** between different systems, `25 mm` within one system.

---

## 2. Directory layout and OWNERSHIP (no two modules own one file)

```
AI-home-design/
├── docs/MODULE_CONTRACT.md        [ORCHESTRATOR - do not edit]
├── docs/ANALYSIS.md               [ORCHESTRATOR]
├── README.md                      [M9]
├── .gitignore                     [ORCHESTRATOR]
├── engine/
│   ├── pyproject.toml             [ORCHESTRATOR]
│   ├── main.py                    [M1]  app factory, stub router registration
│   ├── fixtures/*.json            [M1]  3 hand-authored Building Models
│   ├── app/
│   │   ├── contract/              [M1]  enums.py building.py layers.py
│   │   │                               events.py results.py ports.py __init__.py
│   │   ├── core/                  [M1]  config.py store.py emitter.py bootstrap.py
│   │   ├── geometry/              [M2]  __init__.py toolkit.py routing.py sizing.py scoring.py
│   │   ├── planner/               [M3]  __init__.py base.py deterministic.py api.py
│   │   ├── agents/                [M3]  __init__.py base.py drainage.py water.py
│   │   │                               air.py power.py fire.py registry.py
│   │   ├── orchestrator/          [M4]  __init__.py run.py merge.py clash.py
│   │   │                               resolver.py performance.py
│   │   ├── rules/                 [M5]  __init__.py engine.py baseline.py indonesia.py
│   │   │                               vietnam.py thailand.py
│   │   ├── exports/               [M5]  __init__.py report.py dxf.py gltf.py ifc.py
│   │   ├── intake/                [M6]  __init__.py parse.py auditor.py quickdraw.py
│   │   └── api/                   [M6]  __init__.py routes.py sse.py leads.py
│   └── tests/                     [each module owns its own test_*.py]
│       ├── conftest.py            [M1 - off limits to everyone else]
│       └── test_contract.py       [M1]
├── web/
│   ├── package.json / index.html / vite.config.ts / tsconfig.json
│   │                              [ORCHESTRATOR]
│   ├── src/main.tsx / App.tsx / styles.css / contract.ts
│   │                              [ORCHESTRATOR - off limits to everyone else]
│   ├── src/landing/**             [M7]  must default-export Landing
│   ├── src/demo/**                [M7]
│   ├── src/studio/**              [M8]  must default-export Studio
│   └── src/lib/api.ts, sse.ts     [M8]
└── data/                          [runtime, gitignored]
```

**Rules:** do not edit `main.py`, `pyproject.toml`, `conftest.py`, `web/package.json`,
`web/src/contract.ts`, `web/src/App.tsx`, `web/src/styles.css`, or any sibling module's
files. Create a file outside your ownership only if you must, and disclose it in your report.

---

## 3. Python runtime / commands

All commands use the managed env. No venv creation, no `pip install` inside agents.

```
PY=/Users/harshjani/.workbuddy-ai/binaries/python/envs/default/bin/python
$PY -m pytest engine/tests -q --basetemp=/tmp/villaai-pytest
$PY -m uvicorn main:app --app-dir engine --port 8000
```

Dependencies already installed in that env: `fastapi 0.141`, `pydantic 2.13`, `pytest 9.1`,
`httpx 0.28`, `uvicorn`, `reportlab`, `pypdf`, `Pillow`, `python-multipart`, `numpy`,
`shapely`, `networkx`, `ezdxf`. Import `trimesh` / `IfcOpenShell` / `anthropic` **lazily and
optionally** with a graceful fallback; do not add them to `pyproject.toml` as hard deps.

`conftest.py` points `tempfile.tempdir` at repo-local `.pytest_tmp`. If the sandbox denies
that, use `--basetemp=/tmp/villaai-pytest` as above.

---

## 4. Frozen data shapes (`engine/app/contract/`)

Pydantic v2 `BaseModel` everywhere. `model_config = ConfigDict(extra="forbid")` on the
top-level `BuildingModel` only; nested models use `extra="allow"` for forward compatibility.

### `enums.py`

```python
class SystemKind(str, Enum):
    DRAINAGE = "drainage"; WATER = "water"; AIR = "air"; POWER = "power"; FIRE = "fire"

class BuildingType(str, Enum):
    APARTMENT = "apartment"; VILLA = "villa"

class Country(str, Enum):
    ID = "ID"; VN = "VN"; TH = "TH"

class RoomType(str, Enum):
    BEDROOM, BATHROOM, KITCHEN, LIVING, DINING, UTILITY, HALL, BALCONY, GARAGE, OTHER
    # values are lowercase strings identical to the member name

class FixtureKind(str, Enum):
    WC, BASIN, SHOWER, SINK, WASHING_MACHINE, FLOOR_DRAIN, BATH, KITCHEN_SINK

class EquipmentKind(str, Enum):
    WATER_TANK, PUMP, WATER_HEATER, SEWER_POINT, SEPTIC_TANK, WATER_METER,
    ELECTRICAL_PANEL, AC_INDOOR, AC_OUTDOOR, SHAFT, RISER, DISTRIBUTION_BOARD,
    SPRINKLER, SMOKE_DETECTOR, EXHAUST_FAN

class AgentPhase(str, Enum):
    QUEUED="queued"; EXPLORING="exploring"; TESTING="testing"
    COMMITTING="committing"; DONE="done"; FAILED="failed"

class RuleStatus(str, Enum):
    PASS="pass"; FAIL="fail"; NOT_CHECKED="not_checked"

class Severity(str, Enum):
    ERROR="error"; WARNING="warning"; INFO="info"

class ClashKind(str, Enum):
    HARD="hard"; CLEARANCE="clearance"
```

`SYSTEM_COLORS: dict[SystemKind, str]` — FROZEN, shared visual language:
`drainage=#f97316`, `water=#3b82f6`, `air=#22d3ee`, `power=#eab308`, `fire=#e6379c`,
plus `clash=#ff3b30` and `resolver=#a78bfa`. **Red is reserved for clashes and failures;
fire is magenta** so fire routes cannot be read as clash markers (amended after the
reference recording — see `docs/UX_SPEC.md`). Mirrored byte-for-byte in
`web/src/contract.ts`.
`SYSTEM_ORDER = [drainage, water, air, power, fire]`.

### `building.py`

```python
class Point2D(BaseModel): x: float; y: float
class Point3D(BaseModel): x: float; y: float; z: float
class Size3D(BaseModel): length_mm: float; width_mm: float; height_mm: float

class Wall(BaseModel):
    id: str; start: Point2D; end: Point2D
    thickness: float = 0.15          # metres
    height: float = 3.0              # metres
    is_exterior: bool = False

class Opening(BaseModel):
    id: str; wall_id: str
    at: float                        # metres along wall centreline from start
    width: float; kind: Literal["door","window","passage"]
    sill: float = 0.0; head: float = 2.1

class Room(BaseModel):
    id: str; name: str; type: RoomType
    polygon: list[Point2D]           # >= 3 points, closed implicitly, metres
    wet: bool = False
    area_m2: float = 0.0             # computed if 0
    ceiling_z: float = 2.7

class Fixture(BaseModel):
    id: str; kind: FixtureKind; at: Point2D
    room_id: str | None = None; z: float = 0.0
    props: dict[str, Any] = {}       # fixture_units, demand_lpm, ...

class Equipment(BaseModel):
    id: str; kind: EquipmentKind; at: Point2D
    room_id: str | None = None; z: float = 0.0
    props: dict[str, Any] = {}

class Shaft(BaseModel):
    id: str; polygon: list[Point2D]; from_z: float = 0.0; to_z: float = 3.0
    systems: list[SystemKind] = []

class Level(BaseModel):
    id: str; name: str; elevation: float = 0.0
    floor_to_floor: float = 3.0; ceiling_void: float = 0.35
    walls: list[Wall] = []; openings: list[Opening] = []
    rooms: list[Room] = []; fixtures: list[Fixture] = []
    equipment: list[Equipment] = []; shafts: list[Shaft] = []

class Scale(BaseModel):
    p1: Point2D; p2: Point2D; distance_m: float
    calibrated_by: Literal["user","fixture"] = "user"

class SiteConnections(BaseModel):
    sewer_point: Point2D | None = None; septic_point: Point2D | None = None
    water_meter: Point2D | None = None; power_incomer: Point2D | None = None

class Assumption(BaseModel):
    id: str; field: str; value: str; reason: str
    source: Literal["default","user","skip","agent"] = "default"
    created_at: float

class BuildingModel(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    id: str; name: str
    building_type: BuildingType = BuildingType.VILLA
    country: Country = Country.ID
    storeys: int = 1
    ceiling_height: float = 2.7
    levels: list[Level]                     # >= 1
    scale: Scale | None = None
    site: SiteConnections = SiteConnections()
    assumptions: list[Assumption] = []
    source: Literal["fixture","upload","quickdraw"] = "fixture"
    created_at: float; updated_at: float
```

Helper (M1 implements, everyone uses): `model.level0 -> Level`,
`model.wet_rooms() -> list[Room]`, `model.fixtures_in(room_id)`,
`model.equipment_of(kind)`, `model.bbox() -> tuple[float,float,float,float]`,
`model.clone() -> BuildingModel`.

### `layers.py`

```python
class Segment(BaseModel):
    id: str; system: SystemKind
    kind: Literal["pipe","duct","cable","conduit","refrigerant","sprinkler_pipe"]
    polyline: list[Point3D]          # >= 2, ordered source -> terminal
    diameter_mm: float | None = None
    width_mm: float | None = None; height_mm: float | None = None
    slope_pct: float = 0.0
    material: str = ""
    meta: dict[str, Any] = {}        # length_m, bends, flow_lpm, circuit_id, ...

class Device(BaseModel):
    id: str; system: SystemKind; kind: str
    at: Point3D; size: Size3D
    meta: dict[str, Any] = {}

class Layer(BaseModel):
    system: SystemKind; agent: str
    status: Literal["pending","running","done","failed"] = "pending"
    segments: list[Segment] = []; devices: list[Device] = []
    metrics: dict[str, float] = {}   # layouts_tested, best_score, total_length_m, ...
    notes: list[str] = []
    strategy: dict[str, Any] = {}    # the committed Strategy, for traceability
```

`RunResult.layers: dict[SystemKind, Layer]`. JSON keys are enum **values** (`"drainage"…`).

### `results.py`

```python
class Clash(BaseModel):
    id: str; kind: ClashKind
    a: str; b: str                  # element ids (Segment.id or Device.id)
    system_a: SystemKind; system_b: SystemKind
    at: Point3D; distance_mm: float
    resolved: bool = False
    fix: str | None = None

class RuleOutcome(BaseModel):
    rule_id: str; status: RuleStatus; detail: str
    evidence: list[str] = []
    severity: Severity = Severity.INFO

class CodeReport(BaseModel):
    country: Country; rule_pack_version: str
    total: int; passed: int; failed: int; not_checked: int
    outcomes: list[RuleOutcome]
    headline: str                   # EXACTLY "passed {passed} of {total} automated checks"
    # NEVER the words "code compliant" / "compliant".

class MissingItem(BaseModel):
    id: str; kind: EquipmentKind | Literal["ceiling_height","building_type","storeys"]
    label: str; prompt: str
    severity: Severity
    system: SystemKind | None
    suggested_at: Point2D | None    # deterministic suggestion, user may nudge
    snap: Literal["wall","corner","wet_room","outside","roof","any"]
    options: list[Literal["drag","suggest","skip"]] = ["drag","suggest","skip"]

class RunStatus(str, Enum):
    PENDING="pending"; RUNNING="running"; COMPLETE="complete"; FAILED="failed"

class RunResult(BaseModel):
    run_id: str; model_id: str; status: RunStatus
    country: Country; enabled_systems: list[SystemKind]
    layers: dict[SystemKind, Layer] = {}
    clashes_before: list[Clash] = []; clashes_after: list[Clash] = []
    code: CodeReport | None = None
    events: list[AgentEvent] = []
    assumptions: list[Assumption] = []
    metrics: dict[str, float] = {}
    started_at: float; finished_at: float | None = None
    error: str | None = None
```

### `events.py`

Event vocabulary is FROZEN. Derive it from ONE Python tuple and ONE TS const; never
hand-copy a second list.

```python
EVENT_NAMES = (
  "run.started", "agent.status", "agent.progress", "candidate.flicker",
  "layer.committed", "merge.done", "clash.found", "clash.resolved",
  "code.progress", "feed.line", "run.completed", "run.failed",
)
TERMINAL_EVENTS = ("run.completed", "run.failed")

class AgentEvent(BaseModel):
    run_id: str; seq: int; ts: float
    name: Literal[EVENT_NAMES]        # emitted as SSE `event:`
    agent: str                         # system name OR "orchestrator"/"fixer"/"codes"
    phase: AgentPhase = AgentPhase.QUEUED
    layouts_tested: int = 0
    best_score: float | None = None
    message: str
    metrics: dict[str, float] = {}
    layer: Layer | None = None        # only on layer.committed
```

**SSE protocol (bug class 6 — do not break):**
- `GET /api/runs/{run_id}/stream` returns `text/event-stream`.
- Each frame is `id: <seq>\nevent: <name>\ndata: <json>\n\n`.
- `id:` is the monotonic `seq`. Client sends `Last-Event-ID` on reconnect; server replays
  from `seq+1` and **never re-runs the engine**.
- Stream ends ONLY on `run.completed` or `run.failed`; server then closes. Client MUST
  call `EventSource.close()` on either. A named `error` event does NOT exist — transport
  errors are native. Do not treat a native error as terminal; do treat a clean close
  without a terminal frame as an error and surface it.
- `feed.line` payload has `text: str` inside `metrics`? NO — `message` holds the line.
  Feed lines are generated from templates over real events. **Never invent metrics.**

### `ports.py` (Protocols — implement, do not subclass)

```python
class Emitter(Protocol):
    def emit(self, event: AgentEvent) -> None: ...
    def feed(self, run_id: str, agent: str, text: str) -> None: ...

class RunStore(Protocol):
    def save_model(self, m: BuildingModel) -> None: ...
    def get_model(self, model_id: str) -> BuildingModel | None: ...
    def save_run(self, r: RunResult) -> None: ...
    def get_run(self, run_id: str) -> RunResult | None: ...
    def append_event(self, run_id: str, e: AgentEvent) -> None: ...
    def events_since(self, run_id: str, seq: int) -> list[AgentEvent]: ...
    def delete_model(self, model_id: str) -> None: ...   # privacy promise is real

class Planner(Protocol):
    def choose_strategy(self, system: SystemKind, model: BuildingModel,
                        observations: dict[str, Any]) -> "Strategy": ...

class JobRunner(Protocol):
    def run_parallel(self, jobs: list[Callable[[], Layer]]) -> list[Layer]: ...

class Toolkit(Protocol):
    def candidates(self, spec: "LayoutSpec") -> Iterator[Layer]: ...
    def score(self, layer: Layer, model: BuildingModel) -> float: ...
```

`Strategy` (in `planner/base.py`, imported lazily by others):

```python
class Strategy(BaseModel):
    system: SystemKind
    trunk: Literal["perimeter","central","wet_core","radial","branch"]
    stack_location: Literal["wet_core","exterior_wall","utility","shaft"]
    z_band: tuple[float, float]
    max_bends: int = 4
    prefer_short_runs: float = 0.5      # 0..1 weighting, validated
    params: dict[str, Any] = {}
```

All `Strategy` fields are validated by `DeterministicPlanner` before reaching the toolkit.
An LLM planner output that fails validation falls back to the deterministic strategy and
records an `Assumption` with reason `llm_output_rejected`. **No strategy, no run.**

### Module-to-module call signatures (import lazily, degrade gracefully)

| Caller | Callee | Signature |
|---|---|---|
| M3 agents | M2 geometry | `toolkit.candidates(spec: LayoutSpec) -> Iterator[Layer]`, `toolkit.score(layer, model) -> float`, `build_candidates(system, model, strategy, limits) -> Iterator[Layer]` |
| M3 agents | M3 planner | `get_planner() -> Planner` |
| M4 orchestrator | M3 agents | `get_agent(system: SystemKind) -> SpecialistAgent`, `agent.plan(model, ctx: RunContext, emitter: Emitter) -> Layer` |
| M4 orchestrator | M4 clash | `detect_clashes(layers, model) -> list[Clash]`, `resolve_clashes(layers, clashes, model, emitter) -> tuple[dict, list[Clash]]` |
| M4 orchestrator | M5 rules | `check_code(model, layers, country) -> CodeReport` |
| M4 orchestrator | M1 core | `store.save_run(r)`, `emitter.emit(e)` |
| M6 API | M4 orchestrator | `start_run(model_id, enabled, country) -> RunResult`, `get_run(run_id)` |
| M6 API | M6 intake | `parse_upload(filename, blob) -> BuildingModel`, `audit(model) -> list[MissingItem]`, `place_item(model, kind, at) -> BuildingModel` |
| M6 API | M5 exports | `build_report(run) -> bytes`, `to_dxf(run) -> bytes`, `to_gltf(run) -> bytes`, `to_ifc(run) -> bytes` |

`RunContext` (M4 owns, M3 consumes):
```python
@dataclass
class RunContext:
    run_id: str; model: BuildingModel; country: Country
    limits: "RunLimits"; seed: int = 42
```

`RunLimits` (M1 `core/config.py` owns):
```python
class RunLimits(BaseModel):
    max_candidates_per_agent: int = 200
    max_resolver_loops: int = 6
    max_performance_loops: int = 3
    max_run_seconds: float = 120.0
    clearance_mm: float = 100.0
```

---

## 5. HTTP contract (`engine/app/api/`, M6)

Base URL `http://localhost:8000`. CORS allows `http://localhost:5173`.

| Method | Path | Body / Query | Returns |
|---|---|---|---|
| GET | `/api/health` | — | `{"ok":true,"engine":"villa-ai","version":"0.1.0"}` |
| GET | `/api/config` | — | `{"systems":[...],"colors":{...},"countries":[...],"bands":{...},"product_name":"Villa AI"}` |
| GET | `/api/sample-model` | — | a fixture `BuildingModel` (villa) |
| POST | `/api/models` | `BuildingModel` JSON | `{"model_id":...}` |
| GET | `/api/models/{id}` | — | `BuildingModel` |
| PATCH | `/api/models/{id}` | partial JSON | `BuildingModel` |
| DELETE | `/api/models/{id}` | — | `204` (real deletion, incl. runs) |
| POST | `/api/models/{id}/scale` | `Scale` | `BuildingModel` |
| POST | `/api/models/{id}/upload` | multipart `file` | `BuildingModel` (parsed, `scale: null`) |
| GET | `/api/models/{id}/gaps` | — | `{"items": MissingItem[]}` |
| POST | `/api/models/{id}/items` | `{"kind":..., "at":{x,y}?, "action":"place"\|"suggest"\|"skip"}` | `BuildingModel` |
| POST | `/api/runs` | `{"model_id", "enabled_systems"?, "country"?}` | `{"run_id", "status"}` |
| GET | `/api/runs/{id}` | — | `RunResult` |
| GET | `/api/runs/{id}/stream` | SSE | see §4 |
| GET | `/api/runs/{id}/report.pdf` | — | PDF bytes |
| GET | `/api/runs/{id}/export.dxf` | — | DXF bytes |
| GET | `/api/runs/{id}/export.gltf` | — | glTF JSON bytes |
| GET | `/api/runs/{id}/export.ifc` | — | `501` with a clear message until IfcOpenShell is wired |
| POST | `/api/leads` | `{"name","email","role","company","country","project_type","consent"}` | `{"lead_id","status":"pending_verification"}` |
| POST | `/api/leads/verify` | `{"token"}` | `{"status":"verified"}` |

**Lead gate:** `POST /api/runs` returns `403 {"code":"LEAD_GATE"}` unless the request carries
`X-Session-Token` for a `verified` lead. Config flag `REQUIRE_LEAD_GATE=False` by default so
the prototype is usable locally; set it `True` in production. The gate is enforced
**server-side**, not just in the UI. `POST /api/leads` issues a magic-link token; in dev the
token is returned in the response under `dev_token` and logged, never emailed.

---

## 6. Frontend contract (`web/`, M7 + M8)

`web/src/contract.ts` (ORCHESTRATOR-owned) exports: `SystemKind`, `SYSTEM_ORDER`,
`SYSTEM_COLORS`, `AgentPhase`, `EVENT_NAMES`, `TERMINAL_EVENTS`, all TS interfaces mirroring
§4, `PRODUCT_NAME = "Villa AI"`, and `API_BASE = "http://localhost:8000"`.

`web/src/App.tsx` (ORCHESTRATOR-owned) renders:
- `/` → `import Landing from "./landing/Landing"` (**M7** must default-export this)
- `/app` → `import Studio from "./studio/Studio"` (**M8** must default-export this)

`web/src/styles.css` (ORCHESTRATOR-owned) defines the dark theme tokens:
`--bg #0b0f14`, `--surface #141a22`, `--border #232c38`, `--text #e6edf3`,
`--muted #8b98a8`, `--accent #6ee7b7`. **Dark theme only.** Both modules import their own
CSS files; never edit `styles.css`.

M7 landing requirements: hero with the scripted five-lane animation (loop, labelled
"example run"), how-it-works, before/after clash toggle, who-it's-for, trust & limits
("Preliminary design. Licensed engineer review required."), FAQ, lead form repeated at the
bottom, and one "Try it with your plan" CTA linking to `/app`. Animation is a **scripted
loop** from `src/demo/demoScript.ts` — clearly labelled as an example; no invented metrics.

M8 studio requirements: sample-plan picker → confirmation overlay → missing-item cards with
drag / suggest / skip → five agent cards + live SSE feed + candidate flicker → 2D SVG viewer
with layer toggles + 3D react-three-fiber viewer → before/after clash toggle → code-check
report ("passed N of M automated checks") → assumptions list → export links.

**Client SSE rule (FROZEN):** `EventSource` closes on `run.completed` and `run.failed`
only. Handle `Last-Event-ID` replay idempotently (dedupe by `seq`). Native `error` is not
terminal.

---

## 7. Local dev / test rules (non-negotiable)

1. `GET /api/health` returns 200 with no external services, no DB, no API key.
2. A full run (`POST /api/runs` on `/api/sample-model` → SSE → `GET /api/runs/{id}`)
   completes in < 10 s with `DeterministicPlanner`, produces 5 layers, a before/after clash
   list, and a `CodeReport` with `total >= 40`.
3. Rule packs: >= 40 rules total across the five systems. Rules are **data**. Every rule
   has `id, description, standard_ref, severity, systems, check`. Every `standard_ref`
   carries `verified: bool = False` metadata (the plan admits the SNI numbers are unverified).
4. **Never** emit the phrase "code compliant" anywhere in code, UI, or reports. Use
   "passed N of M automated checks". Rules that cannot be evaluated return `not_checked`
   with a reason.
5. Every engine default is recorded as an `Assumption` with a `reason`.
6. `pytest` passes with `--basetemp=/tmp/villaai-pytest`. Concurrency uses threads; agents
   must be pure with respect to the `BuildingModel` (never mutate the input — clone first).
7. **Each module owns its own `test_*.py` file.** A failure in a sibling's test file is
   that agent mid-flight — ignore it, but your own tests must be green.
8. Imports of sibling modules that may not exist yet MUST be inside function bodies with a
   clear `RuntimeError` on absence, so your tests can run before the whole tree lands.
9. Deterministic: `seed=42`, no wall-clock dependence in geometry or scoring. `ts` may use
   wall clock.

---

## 8. Explicit gaps in the source plan (orchestrator analysis — see docs/ANALYSIS.md)

These were NOT in the plan and are pinned here so no agent improvises:

1. **No SSE terminal-event / reconnect contract** → infinite reconnect loop. Pinned in §4.
2. **No partial re-run semantics** ("move an item and re-run only the affected agents").
   v1 pins: `POST /api/models/{id}/items` invalidates the whole cached run; a re-run is
   full but deterministic and cheap. `RunResult.invalidated_by: str | None` records why.
   A dependency-graph partial re-run is post-v1. Do not fake it.
3. **No typed Strategy schema** → LLM output cannot drive a deterministic toolkit. Pinned.
4. **No shared coordinate / unit / vertical frame** → layers incomparable. Pinned in §1.
5. **"Not checked" had no data shape** → pinned `RuleStatus.NOT_CHECKED` + reason.
6. **Lead gate had no enforcement point** → pinned `X-Session-Token` + `LEAD_GATE`.
7. **Deletion/retention was policy text only** → `DELETE /api/models/{id}` is real.
8. **No test floor plans** → `engine/fixtures/*.json`, hand-authored, clearly labelled;
   do not claim PDF/image parsing is validated against real plans.
9. **Cost caps mentioned with no numbers** → `RunLimits` pinned above.
10. **Standard numbers unverified** → `verified: false` everywhere; never claim compliance.
