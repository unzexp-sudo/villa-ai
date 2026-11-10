import { SYSTEM_COLORS, SYSTEM_LABELS, type SystemKind } from "../contract";

interface Props {
  agents: { system: SystemKind; layouts: number; phase: string; best: number | null }[];
  total: number;
  clash: { before: number; now: number } | null;
}

/**
 * The coordinator panel: five progress rows with the real layout counts, the
 * real clash delta, and how many agents have finished.
 *
 * This panel is the one place a number could be faked most easily — "4,871
 * layouts tested" reads as impressive whether or not it happened. Everything
 * here is summed from the agents' own `layouts_tested` counters, which come
 * from the engine's candidate generator.
 */
export function Coordinator({ agents, total, clash }: Props) {
  const done = agents.filter((a) => a.phase === "done").length;
  const peak = Math.max(1, ...agents.map((a) => a.layouts));

  return (
    <article className="coordinator">
      <header className="coord-head">
        <span className="coord-dot" />
        <strong>Coordinator</strong>
        <span className="muted small mono">merge · clash check</span>
      </header>

      <p className="eyebrow">Agents · running in parallel</p>
      <div className="coord-rows">
        {agents.map((a) => (
          <div key={a.system} className="coord-row">
            <span className="coord-swatch" style={{ background: SYSTEM_COLORS[a.system] }} />
            <span className="coord-name">
              Agent {index(a.system)} · {SYSTEM_LABELS[a.system].split(" ")[0]}
            </span>
            <span className="coord-track">
              <i
                style={{
                  width: `${Math.max(3, (a.layouts / peak) * 100)}%`,
                  background: SYSTEM_COLORS[a.system],
                  opacity: a.phase === "done" ? 1 : 0.65,
                }}
              />
            </span>
            <span className="coord-count mono">{a.layouts.toLocaleString()}</span>
          </div>
        ))}
      </div>

      <p className="coord-total">
        {total.toLocaleString()} <span className="muted">layouts tested so far</span>
      </p>

      <p className="coord-status">
        {done === agents.length && agents.length > 0
          ? `${done} / ${agents.length} done · merging models…`
          : `${done} / ${agents.length} agents done`}
      </p>

      {clash && (
        <div className="coord-clash">
          <p className="eyebrow">Clash check</p>
          <p className="coord-clash-num">
            {clash.now}
            <span className="muted small"> clash{clash.now === 1 ? "" : "es"}</span>
          </p>
          <p className="muted small">
            found between the systems
            {clash.before > clash.now && ` · ${clash.before - clash.now} of ${clash.before} resolved`}
          </p>
        </div>
      )}
    </article>
  );
}

function index(s: SystemKind): number {
  return (["drainage", "water", "air", "power", "fire"] as SystemKind[]).indexOf(s) + 1;
}
