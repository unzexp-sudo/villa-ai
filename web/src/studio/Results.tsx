import { useMemo, useState } from "react";
import {
  API_BASE,
  CLASH_COLOR,
  SYSTEM_COLORS,
  SYSTEM_LABELS,
  SYSTEM_ORDER,
  type BuildingModel,
  type RuleStatus,
  type RunResult,
} from "../contract";
import { PlanCanvas } from "./PlanCanvas";
import { Viewer3D } from "./Viewer3D";

interface Props {
  run: RunResult;
  model: BuildingModel;
  onAgain: () => void;
}

type Tab = "result" | "clash" | "code" | "assumptions";

export function Results({ run, model, onAgain }: Props) {
  const [tab, setTab] = useState<Tab>("result");
  const [showBefore, setShowBefore] = useState(false);

  // The contract stores EVERY clash ever found, each flagged resolved or not.
  // "Remaining" is therefore a filter, and both the tab label and the badge must
  // come from this one value — reading `.length` here once made the tab say 9
  // while the 3D badge said 5.
  const remaining = useMemo(
    () => (run.clashes_after ?? []).filter((c) => !c.resolved),
    [run],
  );
  const fixed = useMemo(() => (run.clashes_after ?? []).filter((c) => c.resolved), [run]);

  const layers = run.layers;
  const code = run.code;

  return (
    <section className="results">
      <header className="run-bar">
        <div className="row">
          <span className="eyebrow">Done</span>
          <h2>{model.name}</h2>
          <span className="tag">
            {run.enabled_systems.length} systems · {fmtSeconds(run)}
          </span>
        </div>
        <div className="row">
          <a className="btn" href={`${API_BASE}/api/runs/${run.run_id}/report.pdf`} target="_blank" rel="noreferrer">
            PDF report
          </a>
          <a className="btn" href={`${API_BASE}/api/runs/${run.run_id}/export.dxf`}>DXF</a>
          <a className="btn" href={`${API_BASE}/api/runs/${run.run_id}/export.gltf`}>glTF</a>
          <button className="btn" onClick={onAgain}>Adjust &amp; run again</button>
        </div>
      </header>

      <nav className="tabs">
        {(
          [
            ["result", "MEP result"],
            ["clash", `Clashes · ${remaining.length}`],
            ["code", code ? code.headline : "Code checks"],
            ["assumptions", `Assumptions · ${run.assumptions.length}`],
          ] as [Tab, string][]
        ).map(([k, label]) => (
          <button key={k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>
            {label}
          </button>
        ))}
      </nav>

      {tab === "result" && (
        <div className="result-grid">
          <div className="result-3d">
            <Viewer3D
              model={model}
              layers={layers}
              clash={{ before: run.clashes_before.length, now: remaining.length }}
            />
          </div>
          <div className="result-summary card">
            <p className="eyebrow">Last run · MEP result</p>
            {SYSTEM_ORDER.map((s) => {
              const l = layers[s];
              if (!l) return null;
              const length = l.segments.reduce((n, seg) => n + Number(seg.meta?.length_m ?? 0), 0);
              return (
                <div key={s} className="summary-row">
                  <span className="summary-swatch" style={{ background: SYSTEM_COLORS[s] }} />
                  <div>
                    <strong>Agent {SYSTEM_ORDER.indexOf(s) + 1} · {SYSTEM_LABELS[s]}</strong>
                    <p className="muted small">
                      {l.segments.length} runs · {length.toFixed(1)} m ·{" "}
                      {l.devices.length} device{l.devices.length === 1 ? "" : "s"}
                    </p>
                  </div>
                </div>
              );
            })}
            <hr className="divider" />
            <p className="muted small">
              {(run.metrics.layouts_tested ?? 0).toLocaleString()} layouts tested across{" "}
              {run.enabled_systems.length} agents · {run.clashes_before.length} clashes before,{" "}
              {remaining.length} after.
            </p>
            <p className="disclaimer">
              Preliminary design. Licensed engineer review required before construction.
            </p>
          </div>
        </div>
      )}

      {tab === "clash" && (
        <div className="clash-view">
          <div className="row spread">
            <h3>
              {remaining.length} clash{remaining.length === 1 ? "" : "es"} remaining of{" "}
              {run.clashes_before.length} found
            </h3>
            <div className="seg">
              <button className={!showBefore ? "on" : ""} onClick={() => setShowBefore(false)}>
                After ({remaining.length})
              </button>
              <button className={showBefore ? "on" : ""} onClick={() => setShowBefore(true)}>
                Before ({run.clashes_before.length})
              </button>
            </div>
          </div>

          <div className="clash-grid">
            <div className="card plan-clash">
              <PlanCanvas
                model={model}
                layers={showBefore ? beforeLayers(run) : layers}
                markers={(showBefore ? run.clashes_before : remaining).map((c) => ({
                  x: c.at.x,
                  y: c.at.y,
                  label: c.id,
                  color: CLASH_COLOR,
                }))}
              />
            </div>
            <div className="clash-log card">
              <p className="eyebrow">
                {showBefore ? "Found after merge" : `Resolved · ${fixed.length}`}
              </p>
              <div className="clash-items">
                {(showBefore ? run.clashes_before : fixed).map((c) => (
                  <div key={c.id} className="clash-item">
                    <span className="dot" style={{ background: SYSTEM_COLORS[c.system_a] }} />
                    <span className="dot" style={{ background: SYSTEM_COLORS[c.system_b] }} />
                    <div>
                      <p className="small">
                        <strong>
                          {SYSTEM_LABELS[c.system_a].split(" ")[0]} ↔{" "}
                          {SYSTEM_LABELS[c.system_b].split(" ")[0]}
                        </strong>{" "}
                        <span className="muted">{c.kind}</span>
                      </p>
                      <p className="muted x-small">
                        {showBefore
                          ? `${c.distance_mm.toFixed(0)} mm apart at the merge`
                          : c.fix ?? "resolved"}
                      </p>
                    </div>
                  </div>
                ))}
                {(showBefore ? run.clashes_before : fixed).length === 0 && (
                  <p className="muted small">Nothing here.</p>
                )}
              </div>
              {remaining.length > 0 && (
                <p className="warn small">
                  {remaining.length} clash{remaining.length === 1 ? "" : "es"} stayed inside the
                  resolver&apos;s limits. The engineer resolves these.
                </p>
              )}
            </div>
          </div>
        </div>
      )}

      {tab === "code" && (
        <div className="code-view card">
          {!code ? (
            <p className="muted">No code report was produced for this run.</p>
          ) : (
            <>
              <header className="row spread">
                <div>
                  <h3>{code.headline}</h3>
                  <p className="muted small">
                    Pack {code.rule_pack_version} · {code.country} · {code.passed} passed,{" "}
                    {code.failed} failed, {code.not_checked} not checked
                  </p>
                </div>
                <div className="code-bars">
                  <Bar n={code.passed} of={code.total} color="#4ade80" />
                  <Bar n={code.failed} of={code.total} color={CLASH_COLOR} />
                </div>
              </header>
              <div className="code-list">
                {code.outcomes.map((o) => (
                  <div key={o.rule_id} className={`code-row ${statusClass(o.status)}`}>
                    <span className="mono code-id">{o.rule_id}</span>
                    <span className="code-status">{statusLabel(o.status)}</span>
                    <span className="code-detail">{o.detail}</span>
                  </div>
                ))}
              </div>
              <p className="disclaimer">
                Rule references are unverified starting points for a local engineer. This
                lists which automated checks passed; it does not certify compliance with any
                code.
              </p>
            </>
          )}
        </div>
      )}

      {tab === "assumptions" && (
        <div className="card">
          {run.assumptions.length === 0 ? (
            <p className="muted">No defaults were assumed for this run.</p>
          ) : (
            <table className="assume">
              <thead>
                <tr>
                  <th>Field</th>
                  <th>Value</th>
                  <th>Source</th>
                  <th>Why</th>
                </tr>
              </thead>
              <tbody>
                {run.assumptions.map((a) => (
                  <tr key={a.id}>
                    <td className="mono">{a.field}</td>
                    <td className="mono">{a.value}</td>
                    <td>
                      <span className="tag">{a.source}</span>
                    </td>
                    <td className="muted small">{a.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </section>
  );
}

function beforeLayers(run: RunResult) {
  // The merged-but-unresolved geometry is not persisted separately (the run
  // stores the resolved layers). The BEFORE view therefore marks the clash
  // positions on the resolved plan and says so, rather than drawing geometry we
  // did not keep — inventing the "before" state would be the lie this whole
  // feature exists to avoid.
  return run.layers;
}

function statusLabel(s: RuleStatus): string {
  return s === "pass" ? "pass" : s === "fail" ? "fail" : "not checked";
}

function statusClass(s: RuleStatus): string {
  return s === "pass" ? "ok" : s === "fail" ? "bad" : "nc";
}

function Bar({ n, of, color }: { n: number; of: number; color: string }) {
  const pct = of ? (n / of) * 100 : 0;
  return (
    <span className="bar">
      <i style={{ width: `${pct}%`, background: color }} />
    </span>
  );
}

function fmtSeconds(run: RunResult): string {
  if (!run.finished_at) return "—";
  return `${Math.max(0.1, run.finished_at - run.started_at).toFixed(1)} s`;
}
