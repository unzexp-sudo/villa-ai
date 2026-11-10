import { useEffect, useMemo, useState } from "react";
import type { BuildingModel, SystemKind } from "../contract";
import { modelBBox } from "../contract";
import { PlanCanvas } from "./PlanCanvas";

export interface AgentPanelState {
  phase: string;
  layouts: number;
  best: number | null;
  message: string;
}

interface Props {
  system: SystemKind;
  model: BuildingModel;
  state: AgentPanelState;
  color: string;
  codeRef: string;
  band: readonly [number, number];
}

const PHASE_LABEL: Record<string, string> = {
  queued: "queued",
  exploring: "searching layouts…",
  testing: "testing…",
  committing: "drawing best layout…",
  done: "done",
  failed: "failed",
};

/**
 * One agent's panel: its own copy of the plan, a moving "Agent N" chip, and a
 * counter that only ever shows numbers the server sent.
 *
 * The chip roams the plan because that is what makes five parallel agents
 * legible — it is the plan's "candidate routes flicker" idea made visible. Its
 * path is deterministic (seeded by the system name), so a reload never
 * reshuffles it, and it never implies progress the engine has not reported.
 */
export function AgentPanel({ system, model, state, color, codeRef, band }: Props) {
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (state.phase === "done" || state.phase === "failed") return;
    const id = window.setInterval(() => setTick((t) => t + 1), 900);
    return () => window.clearInterval(id);
  }, [state.phase]);

  const bounds = useMemo(() => {
    const [minx, miny, maxx, maxy] = modelBBox(model);
    if (!(maxx > minx && maxy > miny)) return { minx: 0, miny: 0, maxx: 10, maxy: 8 };
    return { minx, miny, maxx, maxy };
  }, [model]);

  const seed = useMemo(() => {
    let h = 0;
    for (const ch of system) h = (h * 31 + ch.charCodeAt(0)) % 997;
    return h;
  }, [system]);

  const chip = useMemo(() => {
    const r = (n: number) => {
      const x = Math.sin(seed + n * 12.9898 + tick * 0.7) * 43758.5453;
      return x - Math.floor(x);
    };
    const pad = 0.12;
    return {
      x: bounds.minx + pad + r(1) * (bounds.maxx - bounds.minx - pad * 2),
      y: bounds.miny + pad + r(2) * (bounds.maxy - bounds.miny - pad * 2),
    };
  }, [bounds, seed, tick]);

  const done = state.phase === "done";
  const failed = state.phase === "failed";

  return (
    <article className="agent-panel" style={{ borderColor: done ? "rgba(74,222,128,0.35)" : undefined }}>
      <header className="agent-head">
        <span className="agent-swatch" style={{ background: color }} />
        <strong>Agent {systemIndex(system)} · {shortName(system)}</strong>
        <span className="agent-ref mono">{codeRef}</span>
        <span
          className="agent-phase"
          style={{ color: failed ? "#ff3b30" : done ? "#4ade80" : color }}
        >
          {PHASE_LABEL[state.phase] ?? state.phase}
        </span>
      </header>

      <div className="agent-plan">
        <PlanCanvas
          model={model}
          layers={{}}
          height={200}
          markers={done || failed ? [] : [{ x: chip.x, y: chip.y, label: `Agent ${systemIndex(system)}`, color, pulsing: false }]}
        />
        <div className="agent-chip" style={{ background: color }}>
          Agent {systemIndex(system)}
        </div>
      </div>

      <div className="agent-foot">
        {done ? (
          <>
            <p className="agent-verdict pass">
              ✓ done · score {fmt(state.best)} · {fmtBand(band)}
            </p>
            <p className="muted small mono">{state.message}</p>
          </>
        ) : failed ? (
          <p className="agent-verdict fail">✗ {state.message}</p>
        ) : (
          <>
            <p className="agent-counter">
              layout {state.layouts.toLocaleString()} · {state.phase === "committing" ? "committing" : "searching"}
            </p>
            <p className="agent-verdict neutral mono">{state.message || "—"}</p>
          </>
        )}
        <div className="agent-bar">
          {Array.from({ length: 16 }).map((_, i) => (
            <i
              key={i}
              className={
                done
                  ? "on"
                  : state.phase === "queued"
                    ? ""
                    : i < Math.min(16, Math.round((state.layouts % 400) / 25))
                      ? "on"
                      : ""
              }
            />
          ))}
        </div>
      </div>
    </article>
  );
}

function systemIndex(s: SystemKind): number {
  const order: SystemKind[] = ["drainage", "water", "air", "power", "fire"];
  return order.indexOf(s) + 1;
}

function shortName(s: SystemKind): string {
  return {
    drainage: "Drain · waste · vent",
    water: "Domestic water",
    air: "HVAC · ventilation",
    power: "Electrical",
    fire: "Fire sprinklers",
  }[s];
}

function fmt(n: number | null): string {
  return n === null ? "—" : n.toFixed(2);
}

function fmtBand(b: readonly [number, number]): string {
  return `z ${b[0].toFixed(2)}–${b[1].toFixed(2)} m`;
}
