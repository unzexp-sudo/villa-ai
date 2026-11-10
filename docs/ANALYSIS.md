# Build-plan analysis — gaps, risks, and locked decisions

Source: `Build Plan Multi-Agent MEP Design Tool.pdf`, Draft v1, 12 pp, 2026-11-10.

## Verdict

The plan is unusually complete: it has a phased roadmap, a shared data-contract section,
a stack suggestion, a risk table, metrics, and an explicit "decisions still open" list where
**every item already carries a recommendation**. Nothing blocks a first coding pass. All six
open decisions were locked from the plan's own recommendations (see MODULE_CONTRACT.md §0).
Working name taken from the target repo: **Villa AI**.

## No blocking questions

| Candidate question | Why not blocking |
|---|---|
| Which country pack first? | Plan recommends Indonesia + international baseline. Locked. |
| v1 building scope? | Plan recommends apartments/villas, single level, <=300 m2. Locked. |
| Fire agent default? | Plan: on for apartments, off for houses/villas. Locked. |
| Engineer partner / monetization / domain? | Not code. Deferred with a stated placeholder. |
| Which stack? | Plan says "no preference was given, so this is a practical default" — explicitly a suggestion. |
| LLM provider / API key? | No key locally. Planner is a Protocol with a deterministic default; a key can be added without touching agent code. |
| Redis / Postgres / Docker? | None installed. Ports + in-process runner + JSON store; swappable. |
| Real floor plans for tests? | Plan's first step says "collect 3–5". None supplied. Hand-authored fixtures, clearly labelled as such; parsing claims are explicitly unverified. |

## Gaps that would silently break a later phase

These are omissions in the plan, not disagreements with it. Each is pinned in
`MODULE_CONTRACT.md` so parallel modules cannot improvise incompatible answers.

1. **SSE has no terminal-event or reconnect semantics.** §7 says "WebSocket or SSE" and
   gives one example event, but never says how a stream *ends*. `EventSource` treats a clean
   server close as a native `error` and reconnects by design. Without an explicit terminal
   frame the browser replays finished runs forever, each replay costing work. Pinned:
   `run.completed` / `run.failed` are the only terminal events; `id:` is the monotonic `seq`;
   `Last-Event-ID` replays events only, never re-runs the engine; client closes on terminal.

2. **"Re-run only the affected agents" has no dependency graph.** §5 promises partial
   re-runs after drag-and-drop, but never defines which systems depend on which items, how
   layer invalidation works, or how a partial result merges with cached layers. v1 pins an
   honest full re-run (deterministic, < 10 s) with `invalidated_by` recorded. Faking a
   partial re-run would be worse than none.

3. **The LLM's output is never typed.** §6 correctly says agents must not draw coordinates;
   it says the LLM "chooses strategy and parameters" but gives no schema. Without a typed,
   validated `Strategy` the deterministic toolkit cannot be driven, and a bad model response
   has no defined fallback. Pinned: `Strategy` with closed enums, validated, with
   `llm_output_rejected` assumptions on fallback.

4. **No shared coordinate frame, units, or vertical convention.** Five agents write
   independent layers and §6 merges them into one 3D model, but the plan never fixes the
   origin, axis direction, z datum, or unit. §6 also defers the "vertical zoning convention"
   to post-v1 — yet clash detection needs z and clearance bands from day one. Pinned: metres
   for geometry, millimetres for component sizes, z above finished floor, plus *soft* default
   height bands used for scoring only (not constraints).

5. **"Report not checked honestly" has no data shape.** §7 wants it, but a rules engine with
   only pass/fail will silently coerce un-evaluable rules into a pass. Pinned:
   `RuleStatus.NOT_CHECKED` plus a mandatory reason string.

6. **The lead gate has no enforcement point.** §3 requires email verification "before any
   plan can be submitted" but defines no session/token contract. A form-only gate is
   cosmetic: `POST /api/runs` would still work from curl. Pinned: `X-Session-Token`,
   `verified` lead state, `403 LEAD_GATE`, off by default locally so the prototype is usable.

7. **Retention/deletion is policy text only.** §3 and §11 promise deletion and a retention
   policy; nothing in the stack section produces it. Pinned: `DELETE /api/models/{id}`
   really removes the model and its runs.

8. **No test data exists.** §14 step 1 is "collect 3–5 real floor plans". None were provided.
   Pinned: three hand-authored JSON Building Models. The README must state that PDF/image
   parsing has NOT been validated against real plans.

9. **Cost caps are named but not numbered.** §11 says "cap layouts and loops"; §6 says "cap
   the number of loops". Pinned `RunLimits`: 200 candidates/agent, 6 resolver loops,
   3 performance loops, 120 s, 100 mm clearance.

10. **The plan admits the SNI / PUIL standard numbers are unverified.** Rule packs must carry
    `verified: false` on every standard reference, and no output may say "code compliant" —
    only "passed N of M automated checks". This is a liability control, not a nicety.

## Other observations

- §6's "optimization to add after v1" (vertical zoning) and the "independent then merge"
  default are in tension: independence is a *demo* property that deliberately creates
  avoidable clashes. Keeping it as default is right for the honest animation, but the
  resolver must be able to show a real before/after delta, or the strongest proof point in
  §3 is empty. Seed fixtures should therefore contain at least one guaranteed crossing.
- §8 lists IFC export via IfcOpenShell. That library is not installable in this
  environment. Contract defines a `501` with a clear message rather than a fake file.
- §9's magic-link auth cannot send email without a provider. Dev mode returns the token in
  the response and logs it; Resend is a later swap.
- The plan never defines a product name. "Villa AI" comes from the destination repo and is
  isolated to one constant.
