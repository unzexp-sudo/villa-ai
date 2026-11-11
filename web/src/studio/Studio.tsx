// MEP Studio — the post-upload run experience.
// Visual grammar follows docs/UX_SPEC.md (the reference recording): five agent
// panels + a coordinator, then merged 2D, then 3D clash check, then results.
// Every number shown comes from a server event or the RunResult; nothing here
// invents a metric.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  SYSTEM_COLORS,
  SYSTEM_LABELS,
  SYSTEM_ORDER,
  DEFAULT_BANDS,
  type AgentEvent,
  type BuildingModel,
  type Layer,
  type MissingItem,
  type RunResult,
  type SystemKind,
} from "../contract";
import { level0 } from "../contract";
import { api, lead } from "../lib/api";
import { session } from "../lib/session";
import { streamRun, type StreamHandle } from "../lib/sse";
import { PlanCanvas } from "./PlanCanvas";
import { AgentPanel } from "./AgentPanel";
import { Coordinator } from "./Coordinator";
import { Viewer3D } from "./Viewer3D";
import { GapCards, currentDragId } from "./GapCards";
import { Results } from "./Results";
import { LeadForm } from "./LeadForm";
import { UploadBox } from "./UploadBox";
import "./studio.css";

type Stage = "pick" | "confirm" | "running" | "result";

interface AgentState {
  phase: string;
  layouts: number;
  best: number | null;
  message: string;
}

const EMPTY_AGENT: AgentState = {
  phase: "queued",
  layouts: 0,
  best: null,
  message: "waiting",
};

export default function Studio() {
  const [stage, setStage] = useState<Stage>("pick");
  const [model, setModel] = useState<BuildingModel | null>(null);
  const [gaps, setGaps] = useState<MissingItem[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [run, setRun] = useState<RunResult | null>(null);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [agents, setAgents] = useState<Record<string, AgentState>>({});
  const [feed, setFeed] = useState<{ agent: string; text: string }[]>([]);
  const [view, setView] = useState<"grid" | "plan2d" | "plan3d">("grid");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [clashCount, setClashCount] = useState<{ before: number; now: number } | null>(null);
  const [engineUp, setEngineUp] = useState<boolean | null>(null);
  const [hasToken, setHasToken] = useState<boolean>(!!lead.getToken());
  const streamRef = useRef<StreamHandle | null>(null);

  // Persist model to localStorage so tab switches and the Interior 3D page
  // can access it without re-uploading.
  useEffect(() => {
    if (model) session.setActiveModel(model);
  }, [model]);

  /**
   * Load the missing-item cards and any parse warnings for a model.
   *
   * The warnings come from the server's parse-quality assessment: they say
   * what could NOT be read from the file (rooms, scale, fixtures) so the user
   * knows how much to trust the run before starting it.
   */
  const loadGaps = useCallback(async (m: BuildingModel) => {
    const g = await api.gaps(m.id);
    setGaps(g.items);
    setWarnings(g.warnings ?? []);
    return g;
  }, []);

  useEffect(() => {
    api.health().then(() => setEngineUp(true)).catch(() => setEngineUp(false));
    // Restore a previously saved model so switching tabs does not lose work.
    const saved = session.getActiveModel();
    if (saved) {
      setModel(saved);
      // If we have a saved model, jump straight to confirm stage.
      void loadGaps(saved).then(() => {
        setStage("confirm");
      }).catch(() => {
        // Model may have been purged server-side; stay on pick.
        session.clearActiveModel();
      });
    }
    return () => {
      if (streamRef.current) streamRef.current.close();
    };
  }, [loadGaps]);

  const startRun = useCallback(async (m: BuildingModel) => {
    setBusy(true);
    setError(null);
    setEvents([]);
    setFeed([]);
    setAgents({});
    setRun(null);
    setClashCount(null);
    setView("grid");
    setStage("running");
    try {
      const r = await api.startRun(m.id);
      streamRef.current = streamRun(r.run_id, {
        onEvent: (ev) => {
          setEvents((prev) => [...prev, ev]);
          if (ev.name === "feed.line") {
            setFeed((prev) => [...prev.slice(-40), { agent: ev.agent, text: ev.message }]);
            return;
          }
          if (ev.name === "clash.found") {
            const n = ev.metrics.clashes ?? ev.metrics.clashes_before ?? 0;
            setClashCount({ before: n, now: n });
            return;
          }
          if (ev.name === "clash.resolved") {
            const now = ev.metrics.clashes_after ?? ev.metrics.remaining;
            setClashCount((c) => (c ? { before: c.before, now: now ?? c.now } : c));
            return;
          }
          if (ev.agent && ev.agent in SYSTEM_COLORS) {
            setAgents((prev) => ({
              ...prev,
              [ev.agent]: {
                phase: ev.phase,
                layouts: ev.layouts_tested || prev[ev.agent]?.layouts || 0,
                best: ev.best_score ?? prev[ev.agent]?.best ?? null,
                message: ev.message,
              },
            }));
          }
        },
        onError: (msg) => setError(msg),
        onDone: () => {
          void api
            .getRun(r.run_id)
            .then((res) => {
              setRun(res);
              setStage("result");
            })
            .catch((e) => setError(String((e as Error).message)));
        },
      });
    } catch (e) {
      setError((e as Error).message);
      setStage("confirm");
    } finally {
      setBusy(false);
    }
  }, []);

  const sample = useCallback(
    async (slug: string) => {
      setBusy(true);
      setError(null);
      try {
        const m = await api.sample(slug);
        const created = await api.createModel(m);
        setModel(created.model);
        const g = await loadGaps(created.model);
        if (g.items.length) {
          setStage("confirm");
        } else {
          await startRun(created.model);
        }
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setBusy(false);
      }
    },
    [startRun, loadGaps],
  );

  const quickDraw = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const created = await api.quickdraw({ width_m: 10, depth_m: 8, building_type: "villa", country: "ID" });
      setModel(created.model);
      const g = await loadGaps(created.model);
      if (g.items.length) {
        setStage("confirm");
      } else {
        await startRun(created.model);
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }, [startRun, loadGaps]);

  const enabled: SystemKind[] = useMemo(() => {
    if (run) return run.enabled_systems;
    return model?.building_type === "apartment"
      ? [...SYSTEM_ORDER]
      : SYSTEM_ORDER.filter((s) => s !== "fire");
  }, [run, model]);

  const totalLayouts = useMemo(
    () => enabled.reduce((sum, s) => sum + (agents[s]?.layouts ?? 0), 0),
    [agents, enabled],
  );

  const placeGap = useCallback(
    async (
      item: MissingItem,
      action: "place" | "suggest" | "skip",
      at?: { x: number; y: number },
    ) => {
      if (!model) return;
      setBusy(true);
      try {
        const next = await api.addItem(model.id, {
          kind: item.kind,
          action,
          at: at ?? (action === "suggest" ? item.suggested_at ?? undefined : undefined),
        });
        setModel(next);
        setGaps((prev) => prev.filter((g) => g.id !== item.id));
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setBusy(false);
      }
    },
    [model],
  );

  /** A card dropped on the plan places the item at the drop point. */
  const dropGap = useCallback(
    (p: { x: number; y: number }, dt: DataTransfer | null) => {
      // Payload first (the spec-compliant path), gesture second (the fallback
      // for browsers that deliver an empty DataTransfer).
      const id = dt?.getData("text/villa-item") || currentDragId();
      const item = gaps.find((g) => g.id === id);
      if (!item) return;
      void placeGap(item, "place", p);
    },
    [gaps, placeGap],
  );

  if (engineUp === false) {
    return (
      <main className="studio-empty">
        <h2>The engine is not answering</h2>
        <p className="muted">
          Start it from the project root with{" "}
          <code className="mono">uvicorn main:app --app-dir engine --port 8000</code>, then
          reload this page.
        </p>
      </main>
    );
  }

  return (
    <main className="studio">
      {error && (
        <div className="studio-error" role="alert">
          <strong>Something went wrong.</strong> {error}
          <button className="link" onClick={() => setError(null)}>
            dismiss
          </button>
        </div>
      )}

      {stage === "pick" && (
        <section className="pick">
          <header>
            <p className="eyebrow">Step 1 · choose a plan</p>
            <h1>Design all MEP for one plan. Five agents, one system each.</h1>
            <p className="muted">
              Pick a sample to watch a real run. DXF upload gives exact geometry;
              PDF and image plans are vectorised or edge-detected — scale must be
              calibrated before design runs.
            </p>
          </header>

          <div className={hasToken ? "pick-body" : "pick-body with-gate"}>
            {!hasToken && (
              <aside className="lead-gate card">
                <LeadForm onDone={() => setHasToken(true)} />
              </aside>
            )}

            <div className="pick-plans">
              <div className="sample-row">
                <button
                  key="quickdraw"
                  className="sample-card"
                  disabled={busy}
                  onClick={() => void quickDraw()}
                >
                  <strong>Blank plan · 10 × 8 m</strong>
                  <span className="muted small">Start from a clean sketch and refine it</span>
                </button>
                {[
                  { slug: "villa_120", title: "Villa · 120 m²", note: "3 bed, 2 bath, wet core" },
                  { slug: "apartment_85", title: "Apartment · 85 m²", note: "2 bed, sprinklers on" },
                  { slug: "villa_minimal", title: "Villa · sparse plan", note: "shows the gap prompts" },
                ].map((s) => (
                  <button
                    key={s.slug}
                    className="sample-card"
                    disabled={busy}
                    onClick={() => void sample(s.slug)}
                  >
                    <strong>{s.title}</strong>
                    <span className="muted small">{s.note}</span>
                  </button>
                ))}
              </div>

              <div className="pick-upload card">
                <p className="eyebrow">Or bring your own plan</p>
                <p className="muted small">
                  DXF gives exact geometry. PDF and PNG/JPG are vectorised or edge-detected.
                  DWG needs to be converted to DXF first.
                </p>
                <UploadBox onUploaded={(m) => {
                  setModel(m);
                  void loadGaps(m).then(() => {
                    setStage("confirm");
                  });
                }} />
              </div>
            </div>
          </div>
        </section>
      )}

      {stage === "confirm" && model && (
        <section className="confirm">
          <header className="row spread">
            <div>
              <p className="eyebrow">Step 2 · check the read</p>
              <h2>{model.name}</h2>
            </div>
            <div className="row">
              <span className="tag">{model.building_type}</span>
              <span className="tag">{model.country} rules</span>
              <span className="tag">{level0(model).rooms.length} rooms</span>
            </div>
          </header>
          <div className="confirm-grid">
            <PlanCanvas model={model} layers={{}} showLabels onDropAt={dropGap} />
            <GapCards items={gaps} busy={busy} warnings={warnings} onAction={placeGap} onDone={() => void startRun(model)} />
          </div>
        </section>
      )}

      {stage === "running" && model && (
        <section className="run">
          <header className="run-bar">
            <div className="row">
              <span className="eyebrow">Running</span>
              <h2>{model.name}</h2>
              <span className="tag">{enabled.length} systems</span>
            </div>
            <div className="seg">
              {(["grid", "plan2d", "plan3d"] as const).map((v) => (
                <button key={v} className={view === v ? "on" : ""} onClick={() => setView(v)}>
                  {v === "grid" ? "Agents" : v === "plan2d" ? "Merged 2D" : "3D"}
                </button>
              ))}
            </div>
          </header>

          {view === "grid" && (
            <div className="agent-grid">
              {enabled.map((sys) => (
                <AgentPanel
                  key={sys}
                  system={sys}
                  model={model}
                  state={agents[sys] ?? EMPTY_AGENT}
                  color={SYSTEM_COLORS[sys]}
                  codeRef={codeRefFor(sys)}
                  band={DEFAULT_BANDS[sys]}
                />
              ))}
              <Coordinator
                agents={enabled.map((sys) => ({
                  system: sys,
                  layouts: agents[sys]?.layouts ?? 0,
                  phase: agents[sys]?.phase ?? "queued",
                  best: agents[sys]?.best ?? null,
                }))}
                total={totalLayouts}
                clash={clashCount}
              />
            </div>
          )}

          {view === "plan2d" && (
            <div className="merged">
              <PlanCanvas model={model} layers={liveLayers(events)} showLabels />
              <div className="legend card">
                <p className="eyebrow">Merged model</p>
                {enabled.map((s) => (
                  <p key={s} className="legend-row">
                    <i style={{ background: SYSTEM_COLORS[s] }} /> {SYSTEM_LABELS[s]}
                  </p>
                ))}
              </div>
            </div>
          )}

          {view === "plan3d" && (
            <div className="merged">
              <Viewer3D model={model} layers={liveLayers(events)} clash={clashCount} />
            </div>
          )}

          <Feed lines={feed} />
        </section>
      )}

      {stage === "result" && run && model && (
        <Results
          run={run}
          model={model}
          onAgain={() => {
            void loadGaps(model).then(() => {
              setStage("confirm");
            });
          }}
        />
      )}
    </main>
  );
}

function codeRefFor(s: SystemKind): string {
  // Verified standard references (full numbers confirmed via web search).
  // Numeric thresholds remain starting points for a local engineer.
  const map: Record<SystemKind, string> = {
    drainage: "SNI 03-6481-2000",
    water: "SNI 03-6481-2000",
    air: "SNI 03-6572-2001",
    power: "SNI 0225:2011 (PUIL 2011)",
    fire: "SNI 03-3989-2000",
  };
  return map[s];
}

/** Layers committed so far, from the event stream — the honest live view. */
function liveLayers(events: AgentEvent[]): Partial<Record<SystemKind, Layer>> {
  const out: Partial<Record<SystemKind, Layer>> = {};
  for (const ev of events) {
    if (ev.name === "layer.committed" && ev.layer) {
      out[ev.layer.system] = ev.layer;
    }
  }
  return out;
}

function Feed({ lines }: { lines: { agent: string; text: string }[] }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.scrollTo({ top: ref.current.scrollHeight, behavior: "smooth" });
  }, [lines.length]);
  return (
    <div className="feed" ref={ref}>
      {lines.length === 0 && <p className="muted small">Waiting for the first event…</p>}
      {lines.map((l, i) => (
        <p key={i} className="feed-line">
          <span
            className="feed-dot"
            style={{ background: SYSTEM_COLORS[l.agent as SystemKind] ?? "#8b98a8" }}
          />
          <strong>{l.agent}</strong> {l.text}
        </p>
      ))}
    </div>
  );
}
