// Landing page (M7).
//
// The claim this page makes has to survive the app behind it: five agents, real
// geometry, a real clash check, and a code report that says what it did NOT
// check. Anything asserted here is a statement about the product, so the limits
// are stated in the same breath as the capabilities.

import { useEffect, useState } from "react";
import { API_BASE, PRODUCT_NAME, SYSTEM_COLORS, SYSTEM_LABELS, SYSTEM_ORDER } from "../contract";
import { api } from "../lib/api";
import { session } from "../lib/session";
import { DemoRun } from "../demo/DemoRun";
import "./landing.css";

interface Health {
  up: boolean;
  version?: string;
}

export default function Landing() {
  const [health, setHealth] = useState<Health>({ up: false });

  useEffect(() => {
    let live = true;
    api
      .health()
      .then((h) => live && setHealth({ up: h.ok, version: h.version }))
      .catch(() => live && setHealth({ up: false }));
    return () => {
      live = false;
    };
  }, []);

  const resetSession = () => {
    session.resetSession();
    window.location.assign("/app");
  };

  const resumeSession = () => {
    session.getActiveModel();
    window.location.assign("/app?resume=1");
  };

  return (
    <main className="landing">
      <section className="hero">
        <p className="eyebrow">Multi-agent MEP coordination · villas &amp; apartments</p>
        <h1>
          Five agents.
          <br />
          One coordinated plan.
        </h1>
        <p className="lede">
          Upload a floor plan and {PRODUCT_NAME} designs drainage, water, ventilation,
          power and fire protection in parallel, merges them, finds the clashes, and tells
          you exactly which code checks it ran — and which it could not.
        </p>

        <div className="hero-actions">
          <a className="btn-primary big" href="/app">
            Design a plan
          </a>
          <button type="button" className="btn ghost" onClick={resetSession}>
            Start fresh
          </button>
          <button type="button" className="btn ghost" onClick={resumeSession}>
            Resume last session
          </button>
          <a className="btn ghost" href="/interior">
            See the 3D walkthrough
          </a>
        </div>

        <p className={`engine-state ${health.up ? "up" : "down"}`}>
          <i />
          {health.up
            ? `Engine online · v${health.version ?? "0.1.0"} · ${API_BASE.replace(/^https?:\/\//, "")}`
            : "Engine offline — start the API to run a design"}
        </p>

        <DemoRun />
      </section>

      <section className="systems">
        <div className="section-head">
          <p className="eyebrow">The five agents</p>
          <h2>One specialist per discipline, each searching its own layouts</h2>
        </div>
        <div className="system-grid">
          {SYSTEM_ORDER.map((s) => (
            <article
              key={s}
              className="system-card"
              style={{ borderTopColor: SYSTEM_COLORS[s] }}
            >
              <span className="system-index" style={{ color: SYSTEM_COLORS[s] }}>
                Agent {SYSTEM_ORDER.indexOf(s) + 1}
              </span>
              <h3>{SYSTEM_LABELS[s]}</h3>
              <p className="muted small">{AGENT_NOTE[s]}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="how">
        <div className="section-head">
          <p className="eyebrow">How a run goes</p>
          <h2>From a messy plan to a coordinated model</h2>
        </div>
        <ol className="steps">
          <li>
            <strong>Read the plan</strong>
            <p className="muted small">
              DXF is parsed for exact geometry. PDF and raster images (PNG/JPG) are
              vectorised from the content stream or edge-detected — no vision model,
              real line geometry, never a guess.
            </p>
          </li>
          <li>
            <strong>Fill the gaps</strong>
            <p className="muted small">
              Missing tank, meter, panel or floor drain? Drag it onto the plan, accept a
              suggested spot, or skip it — and the assumption is written down.
            </p>
          </li>
          <li>
            <strong>Watch the agents work</strong>
            <p className="muted small">
              Five panels, live layout counts and a coordinator merging them. Every number
              on screen comes from the engine, not from a script.
            </p>
          </li>
          <li>
            <strong>Check the result</strong>
            <p className="muted small">
              Merged 2D and 3D, the clash list before and after the resolver, the automated
              code checks, and every assumption in one table.
            </p>
          </li>
        </ol>
      </section>

      <section className="limits">
        <div className="section-head">
          <p className="eyebrow">What this is not</p>
          <h2>Read this before you rely on the output</h2>
        </div>
        <div className="limit-grid">
          <p>
            <strong>Not stamped drawings.</strong> Output is a preliminary design. A
            licensed engineer reviews it before construction.
          </p>
          <p>
            <strong>Not a compliance certificate.</strong> The code report names which
            automated checks passed. Rule references are unverified starting points, and
            checks that cannot be evaluated say <em>not checked</em>.
          </p>
          <p>
            <strong>Not a scan reader.</strong> PDF and image plans are vectorised
            or edge-detected — real line geometry, but no vision model. Scale must
            be calibrated before design runs.
          </p>
          <p>
            <strong>Not every export.</strong> PDF, DXF and glTF are real files. IFC
            is a minimal IFC4 transport that opens in any viewer — a BIM manager
            must enrich it before coordination.
          </p>
        </div>
      </section>

      <section className="cta">
        <h2>Try it on a sample plan</h2>
        <p className="muted">
          A 120 m² villa with four agents, or an 85 m² apartment with sprinklers on. No
          account needed for a local run.
        </p>
        <a className="btn-primary big" href="/app">
          Open the studio
        </a>
      </section>
    </main>
  );
}

const AGENT_NOTE: Record<(typeof SYSTEM_ORDER)[number], string> = {
  drainage: "Soil, waste and vent routes with fall and cleanout spacing.",
  water: "Mains to fixtures, sized for velocity and hot-water wait.",
  air: "Ducts, grilles and condensate, kept inside the ceiling void.",
  power: "Circuit loads, cable sizes, breakers and RCD protection.",
  fire: "Sprinkler spacing and coverage, on for apartments by default.",
};
