# Villa AI

Multi-agent MEP design for villas and apartments. Upload a floor plan; five
specialist agents design drainage, water, ventilation, power and fire
protection in parallel, a coordinator merges them, a resolver fixes what it can,
and a code engine reports **which automated checks passed — and which it could
not evaluate**.

> **Preliminary design.** Not stamped drawings. A licensed engineer reviews the
> output before construction. The code report is not a compliance certificate.

---

## What actually works today

| Area | Status |
|---|---|
| Building model, geometry toolkit, sizing, scoring | Real, deterministic |
| Five specialist agents (drainage / water / air / power / fire) | Real, parallel |
| Merge → clash detect → resolve → performance loop | Real |
| Code rule engine (42 rules: IPC baseline + SNI/PUIL Indonesia + honest VN/TH stubs) | Real |
| Live run stream over SSE, terminal-event protocol, replay by `Last-Event-ID` | Real |
| DXF intake | Real (`ezdxf`) |
| PDF / image intake | Real — vector extraction + edge detection, no vision model |
| DWG intake | **Refused with a reason** (convert to DXF) |
| Gap auditor (drag / suggest / skip) + assumption log | Real |
| Exports: PDF report, DXF, glTF, IFC | Real files (IFC is a minimal IFC4 transport) |
| 3D interior walkthrough (`/interior`) | Real, deterministic |
| LLM planner | Not wired; the deterministic planner runs instead |
| Persistence | JSON files on disk (no Postgres/Redis needed) |

---

## Run it locally

Two processes: the Python engine on **:8000** and the Vite dev server on
**:5173**. The engine has no external service dependencies.

### 1. Engine

```bash
cd engine
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m uvicorn main:app --port 8000
```

Check it:

```bash
curl -s http://localhost:8000/api/health
```

### 2. Frontend

```bash
cd web
npm install
npm run dev
```

Open **http://localhost:5173** — the landing page — or
**http://localhost:5173/app** to go straight into the studio.

`npm run typecheck` and `npm run build` both pass clean.

### 3. Tests

```bash
cd engine && python -m pytest tests/ -q
```

135 tests. They cover the geometry toolkit, the agents, the orchestrator, the
rule packs, all four exporters, the intake parser and auditor, and the HTTP +
SSE surface including the lead gate and `Last-Event-ID` replay.

---

## The rules this codebase follows

These are not style preferences — each one exists because breaking it produces
a *plausible but false* result, which is the only genuinely dangerous failure
mode for a tool like this.

**1. Geometry is computed, never written by a model.**
All coordinates come from the deterministic toolkit in `engine/app/geometry/`.
A language model may pick a *strategy* (trunk type, z-band, max bends); it
never supplies a coordinate. A planner that hallucinates a pipe route is
indistinguishable from one that doesn't — so it doesn't get to.

**2. Units are explicit and frozen.**
Geometry in **metres**, component sizes in **millimetres**, `z` metres above
the finished floor of level 0. `docs/MODULE_CONTRACT.md` §4 is the source of
truth, mirrored byte-for-byte in `web/src/contract.ts`.

**3. A rule that cannot be evaluated reports `not_checked`.**
Never a pass, never a silent skip. `CodeReport.headline` is forced by the
contract model to `passed N of M automated checks`; the words "code compliant"
must not appear anywhere a user can read them, because every rule reference in
these packs is an *unverified starting point* for a local engineer.

**4. Every number on screen came from the engine.**
"4,871 layouts tested" reads as impressive whether or not it happened. The
coordinator sums the agents' own `layouts_tested` counters, and the 3D badge
counts the clashes the resolver actually reported.

**5. Red means a clash or a failure. Nothing else.**
Fire protection is magenta (`#e6379c`) so a fire route can never be mistaken
for a clash marker.

**6. A skipped input is written down, never dropped.**
"Skip & assume" appends an `Assumption` with `source="skip"`. The results
screen lists every one. Nothing is silently guessed.

**7. The stream ends only on a terminal event.**
`run.completed` or `run.failed`, and nothing else. The browser client closes
the `EventSource` on exactly those two names, dedupes by `seq`, and treats a
native `error` as a reconnect rather than an end. A stream that ends without a
terminal event is a failure the user must see.

**8. Unsupported input is refused with a next step.**
DWG needs a conversion service. PDF and raster images are vectorised or
edge-detected — real line geometry, not a vision-model guess. Scale is never
inferred; the user calibrates two points before design runs.

**9. Exports are real files.**
PDF, DXF, glTF and IFC are all generated from the actual run geometry. IFC is a
minimal IFC4 SPF transport — it opens in any viewer but property sets are
minimal. A BIM manager must enrich it before coordination.

**10. Deleting a model deletes its runs and events.**
The privacy promise is enforced server-side, in the store, by cascade.

---

## Layout

```
docs/
  MODULE_CONTRACT.md       frozen contract — read this first
  ANALYSIS.md              what the source plan got wrong, and what we chose
  UX_SPEC.md               the reference recording, turned into requirements
engine/
  app/
    contract/              frozen enums, building model, layers, events, results
    core/                  config, JSON store, event bus, bootstrap
    geometry/              deterministic toolkit: routing, sizing, scoring
    planner/               strategy layer (deterministic planner today)
    agents/                the five specialists
    orchestrator/          merge, clash, resolver, performance, run
    rules/                 rule engine + baseline/ID/VN/TH packs
    exports/               PDF, DXF, glTF, IFC (minimal IFC4 SPF)
    intake/                DXF parser, PDF/image vectoriser, gap auditor
    api/                   routes, SSE, lead gate
    interior/              additive 3D model + walkthrough
  fixtures/                hand-authored sample plans
  tests/                   135 tests
web/src/
  contract.ts              TS mirror of the frozen contract
  landing/                 landing page
  studio/                  the run experience: agent grid, plan, 3D, results
  interior/                the 3D walkthrough
  lib/                     typed API client, SSE client
```

---

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | liveness |
| `GET` | `/api/config` | systems, colours, bands, countries |
| `GET` | `/api/samples` · `/api/samples/{slug}` | sample plans |
| `POST` | `/api/models` | create a model |
| `GET`/`PATCH`/`DELETE` | `/api/models/{id}` | read, edit, delete (cascades) |
| `POST` | `/api/models/{id}/scale` | calibrate two points |
| `POST` | `/api/models/{id}/upload` | DXF / PDF / image intake |
| `GET` | `/api/models/{id}/gaps` | missing-item cards |
| `POST` | `/api/models/{id}/items` | place / suggest / skip an item |
| `POST` | `/api/runs` | start a run (lead-gated when enabled) |
| `GET` | `/api/runs/{id}` | the full `RunResult` |
| `GET` | `/api/runs/{id}/stream` | SSE; supports `Last-Event-ID` |
| `GET` | `/api/runs/{id}/report.pdf` | PDF report |
| `GET` | `/api/runs/{id}/export.dxf` · `.gltf` · `.ifc` | exports |
| `POST` | `/api/leads` · `/api/leads/verify` | lead capture + magic link |

The lead gate is enforced **server-side** on `POST /api/runs` via the
`X-Session-Token` header. It is off by default locally
(`VILLA_AI_REQUIRE_LEAD_GATE=1` turns it on). No mail provider is wired, so
the verification token is returned as `dev_token` and logged — the flow is
testable today and only `_send_magic_link` needs replacing.

---

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `VILLA_AI_DATA_DIR` | `<repo>/data` | where JSON state is written |
| `VILLA_AI_REQUIRE_LEAD_GATE` | `0` | enforce the lead gate on runs |
| `VILLA_AI_DEFAULT_COUNTRY` | `ID` | default rule pack |

---

## Deploying

`Dockerfile` builds the engine, installs the frontend bundle, and serves both
from one origin on :8000 — so `API_BASE` resolves to the page's own origin and
no CORS is needed in production. `railway.json` is configured for it.

---

## Known limitations

- **The planner is deterministic, not an LLM.** It searches real layout
  variants and scores them, but it does not consult a language model. The
  strategy interface (`app/planner/base.py`) is where a planner would plug in.
- **PDF/image intake is vectorisation, not vision.** Vector PDFs are parsed
  from their content stream; raster images use edge detection. No vision model
  is involved. Scale must be calibrated before design runs.
- **IFC is a minimal transport.** The IFC4 SPF file opens in any viewer but
  has minimal property sets. It is not a coordination-ready BIM model.
- **The rule packs are unverified.** The SNI/PUIL references are starting
  points, not confirmed numbers. The VN and TH packs deliberately return
  `not_checked` because no local engineer has validated them.
- **The resolver does not fix everything.** Clashes it cannot resolve inside
  its limits are reported and left to the engineer, with the count shown.
- **Only level 0 is designed.** Multi-storey geometry parses, but the
  agents route on the ground floor.
