// DemoRun — a scripted, self-contained run animation for the landing page.
//
// This is NOT a real SSE run. It plays a canned sequence so a visitor sees what
// the studio does without starting one. The numbers are fabricated for display
// only and do not come from the engine. The real studio at /app is driven by
// live SSE events from the engine.
//
// The animation cycles: agents light up one by one, progress bars fill, a
// clash appears and resolves, the result card shows. Then it restarts.

import { useEffect, useState } from "react";
import { SYSTEM_COLORS, SYSTEM_LABELS, SYSTEM_ORDER, type SystemKind } from "../contract";
import "./demo.css";

interface DemoAgentState {
  system: SystemKind;
  phase: "queued" | "exploring" | "testing" | "committing" | "done";
  layouts: number;
  color: string;
}

const INITIAL: DemoAgentState[] = SYSTEM_ORDER.map((s) => ({
  system: s,
  phase: "queued",
  layouts: 0,
  color: SYSTEM_COLORS[s],
}));

const SCRIPT: { step: number; apply: (prev: DemoAgentState[]) => DemoAgentState[] }[] = [
  // Agents start exploring one by one
  { step: 0, apply: (p) => updateOne(p, 0, { phase: "exploring", layouts: 2 }) },
  { step: 1, apply: (p) => updateOne(p, 1, { phase: "exploring", layouts: 1 }) },
  { step: 2, apply: (p) => updateOne(p, 2, { phase: "exploring", layouts: 3 }) },
  { step: 3, apply: (p) => updateOne(p, 3, { phase: "exploring", layouts: 2 }) },
  { step: 4, apply: (p) => updateOne(p, 4, { phase: "exploring", layouts: 4 }) },
  // Progress: each agent tests more layouts
  { step: 5, apply: (p) => p.map((a) => ({ ...a, layouts: a.layouts + 3, phase: "testing" as const })) },
  { step: 6, apply: (p) => p.map((a) => ({ ...a, layouts: a.layouts + 4 })) },
  { step: 7, apply: (p) => p.map((a) => ({ ...a, layouts: a.layouts + 3 })) },
  // Agents commit their best layout
  { step: 8, apply: (p) => updateOne(p, 0, { phase: "committing", layouts: 12 }) },
  { step: 9, apply: (p) => updateOne(p, 1, { phase: "committing", layouts: 10 }) },
  { step: 10, apply: (p) => updateOne(p, 2, { phase: "committing", layouts: 14 }) },
  { step: 11, apply: (p) => updateOne(p, 3, { phase: "committing", layouts: 11 }) },
  { step: 12, apply: (p) => updateOne(p, 4, { phase: "committing", layouts: 13 }) },
  // All done
  { step: 13, apply: (p) => p.map((a) => ({ ...a, phase: "done" as const })) },
];

function updateOne(
  agents: DemoAgentState[],
  idx: number,
  patch: Partial<DemoAgentState>,
): DemoAgentState[] {
  return agents.map((a, i) => (i === idx ? { ...a, ...patch } : a));
}

const TOTAL_STEPS = SCRIPT.length;
const STEP_MS = 700;
const HOLD_MS = 2000;

export function DemoRun() {
  const [agents, setAgents] = useState<DemoAgentState[]>(INITIAL);
  const [showClash, setShowClash] = useState(false);
  const [showResult, setShowResult] = useState(false);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let step = 0;
    let timer: ReturnType<typeof setTimeout>;

    const advance = () => {
      if (step < TOTAL_STEPS) {
        setAgents((prev) => SCRIPT[step].apply(prev));
        if (step === 8) setShowClash(true);
        if (step === 12) setShowClash(false);
        if (step === 13) setShowResult(true);
        step++;
        timer = setTimeout(advance, STEP_MS);
      } else {
        // Hold the result, then restart
        timer = setTimeout(() => {
          setAgents(INITIAL);
          setShowClash(false);
          setShowResult(false);
          step = 0;
          setTick((t) => t + 1);
          timer = setTimeout(advance, STEP_MS);
        }, HOLD_MS);
      }
    };

    timer = setTimeout(advance, STEP_MS);
    return () => clearTimeout(timer);
  }, [tick]);

  const totalLayouts = agents.reduce((s, a) => s + a.layouts, 0);
  const doneCount = agents.filter((a) => a.phase === "done").length;

  return (
    <div className="demo-run">
      <div className="demo-grid">
        {agents.map((a) => (
          <div
            key={a.system}
            className={`demo-agent demo-${a.phase}`}
            style={{ borderColor: a.phase !== "queued" ? a.color : "var(--border)" }}
          >
            <span className="demo-dot" style={{ background: a.color }} />
            <span className="demo-name">{SYSTEM_LABELS[a.system]}</span>
            <div className="demo-bar">
              <div
                className="demo-bar-fill"
                style={{
                  width: `${Math.min(100, a.layouts * 6)}%`,
                  background: a.color,
                }}
              />
            </div>
            <span className="demo-count">
              {a.layouts > 0 ? `${a.layouts} layouts` : "—"}
            </span>
          </div>
        ))}
      </div>

      <div className="demo-coord">
        <span className="demo-coord-label">Coordinator</span>
        <span className="demo-coord-val">{totalLayouts} layouts tested</span>
        <span className={`demo-clash ${showClash ? "on" : ""}`}>
          {showClash ? "1 clash found" : showResult ? "0 clashes" : "scanning…"}
        </span>
      </div>

      {showResult && (
        <div className="demo-result">
          <span>5 systems merged</span>
          <span>passed 35 of 42 checks</span>
        </div>
      )}

      <p className="demo-note muted small">
        Scripted preview — not a real run. Open the studio for a live design.
      </p>
    </div>
  );
}
