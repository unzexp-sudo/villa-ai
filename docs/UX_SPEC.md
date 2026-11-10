# UX SPEC — post-upload run experience

Source: `Screen Recording 2026-11-10 at 11.12.00 pm.mov` (1850x1026, 60 fps, 40.3 s,
~98 MB). Analysed from 20 extracted frames at 2 s intervals plus full-resolution reads
of frames 02, 03, 06, 08, 09, 10, 11, 13, 16, 17, 19. The on-screen counter reads
`NNN / 031`; the clip loops back to `001 / 031` at ~t=32 s. It is a **scripted 31-frame
animation**, not a live capture — matching the plan's "scripted example" wording.

**This document overrides the plan's §7 screen-layout description for the in-app live
run view.** The landing page (§3) is unaffected.

---

## 1. Sequence

| t | Frame | Screen |
|---|---|---|
| 0–2 s | 001–002 | Dark. Floor plan fades in as a ghosted stack (zoom/blur transition). Room labels: Den, Foyer, Kitchen, Primary Bath, Hall Bath, Hall, Bedroom 2, Bedroom 3, Living / Dining, Primary Bedroom. |
| 2 s | 002 | Bottom prompt bar, dimmed: "Design all MEP for Unit 4B to US code. 5 agents, one system each, run the …" with a circular ↑ submit button. |
| 4–14 s | 003–008 | **5-agent parallel grid.** All panels show the SAME floor plan simultaneously. |
| 16 s | 009 | **Merged 2D plan**, light grey background, all five systems overlaid. |
| 18–30 s | 010–016 | **3D isometric** view, translucent walls/slab, orbiting camera, clash check → resolver → result. |
| 32 s | 017 | Loops: clean dark plan + prompt bar, `001 / 031`. |

## 2. The 5-agent grid (the core of the feel)

Layout: **3 panels on the top row, 2 panels + a Coordinator panel on the bottom row**.
Dark charcoal cards on a near-black background. Each agent card:

- Header: small coloured square + `Agent N · <system>` + a short code reference in muted
  mono (`IPC`, `UPC`, `NFPA / ASHRAE`, `NEC 2023`, `NFPA 13`). Right side: a phase label
  in the agent's colour — `searching layouts…` → `drawing best layout…` → `done` (green).
- Body: the floor plan, thin white/grey walls on dark. A thin segmented progress strip
  runs along the bottom edge of the plan.
- **A floating coloured chip `Agent N` moves around inside the plan** like a cursor.
  This is the single strongest anthropomorphising device — each agent visibly "walks"
  its own copy of the plan while exploring. It is the plan's "candidate routes flicker"
  idea, made legible.
- Below the plan: a large counter `layout NNN / TOTAL` in **red while searching**, then
  a one-line technical verdict in red `✗ …` or green `✓ passes code · score NNN`.
  Examples: `✗ slab hd vent 9.33 ft out of floor depth`, `✗ VD 9.7% on General
  bedroom/kit circuit`, `✓ passes code · score 704`.
- When done: a **summary line** replaces the counter — e.g.
  `199 ft PVC · 2 stacks · 1 AAV` / `476 ft PEX · trunk-and-branch · recirc` /
  `200 ft duct · 10 supplies · FR 0.066` / `23 circuits · 1,644 ft cable · 150 A` /
  `18 sprinklers · 183 ft CPVC`. A muted second line adds detail. Bottom-right:
  `N / M checks` next to a **segmented green progress bar** (one segment per check).

**Coordinator panel** (bottom-right, same card style):
- Header `Coordinator` + muted `merge · clash check`.
- `AGENTS · ROUND 06 FRAME 15` caption, then five rows: coloured square + `Agent N ·
  <short name>` + a horizontal progress bar in that agent's colour, with the agent's
  layout count at the right end.
- Big number: `4,871 layouts tested so far`.
- Status line: `0 / 5 agents done` → `5 / 5 done · merging models…`.

Counters climb monotonically (1,089 → 2,498 → 4,871). **Nothing is invented at render
time** — these must come from real engine events.

## 3. Merged 2D view (t=16 s)

- Light grey canvas, floor plan in pale grey/white, all five systems drawn as thin
  coloured orthogonal routes with small endpoint dots. Overlapping systems are visible
  at once — this is the "one model" moment.
- Legend card top-left, dark: `MERGED MODEL`, five rows of coloured square + short name
  (`Drains`, `Water`, `Air`, `Power`, `Fire`) + right-aligned `Agent: N`.
- No clash markers yet; that arrives in 3D.

## 4. 3D view (t=18–30 s)

- Isometric, light grey background, semi-transparent slab/walls, white interior planes.
  Coloured pipes/ducts/cables at ceiling level with vertical drops to fixtures. Grey
  rectangular ducts are the only solid volumes.
- Camera slowly orbits and tilts; the whole model rotates rather than cutting between
  views.
- **Clash overlay** (t=18 s), dark card top-right: `COORDINATOR · CLASH CHECK`,
  `9 clashes` in red, `found between the 5 systems`. Red outlined circles sit on the
  clash points in the model.
- **Resolver overlay** (t=20 s): `7 clashes` in red, `4 of 11 resolved`, then a clash
  log. Each entry = two small coloured squares + `Fire ↔ Air` style pair + a one-line
  description, e.g. `sprinkler drain offsets 12 in around the supply duct`,
  `Supply duct offsets 10 in around the sprinkler drop`. The count drops as fixes land.
- **Live status pill** (t=24 s), bottom-left: `● Live: water, drains, air and power
  flowing · sprinklers charged`.
- **Final result** (t=30 s), top-right: `LAST 4s · MEP RESULT`, the five per-agent
  summaries with metrics, a dashed green progress bar, and
  `✓ 36 / 64 code checks passed` in green.

## 5. Visual language (adopt verbatim)

| Token | Value |
|---|---|
| Agent-grid background | near-black `#11151b`, cards `#1b2028` |
| Merged / 3D background | light grey `#d8dce0` |
| Plan walls | thin white / grey, no fill |
| Route lines | ~2 px, orthogonal, endpoint dots |
| Labels | small uppercase mono, letter-spaced |
| Drainage | orange `#f97316` |
| Water | blue `#3b82f6` |
| Air | cyan `#22d3ee` |
| Power | yellow `#eab308` |
| Fire | **magenta `#e6379c`** |
| Clash / failure | **pure red `#ff3b30`** |
| Pass / done | green `#4ade80` |

Red is reserved for clashes, failures and the searching counter. Fire must NOT be red —
otherwise fire routes read as clash markers. This changed the frozen contract; both
`engine/app/contract/enums.py` and `web/src/contract.ts` now use the values above.

## 6. Conflicts with the written plan, and locked defaults

| # | Video | Plan | Locked default | Question? |
|---|---|---|---|---|
| 1 | 5 independent plan panels + Coordinator | "five lanes animate in parallel with a live feed at the bottom" (§7) | **Video wins for the in-app run view.** Build the 3+2 grid + Coordinator. Keep a compact scrolling feed strip as a secondary element so §7's chat feed is not lost. | Yes — see below |
| 2 | Imperial units everywhere (`ft`, `in`, `psi`, `FR 0.066`) | Metric everywhere; users in ID/VN/TH | **Metric** (`m`, `mm`, `kPa`, `L/s`). All displayed values derived from real engine metrics. | Yes |
| 3 | US codes in headers (`IPC`, `UPC`, `NEC 2023`, `NFPA 13`, `ASHRAE`) | International baseline + Indonesia first | Keep the **code-reference chip in each header**, but populate from the active rule pack (`SNI 03-6481`, `PUIL 2011`, `IEC 60364` …) with `verified: false`. | Yes |
| 4 | No lead gate, no missing-item cards, no exports, no landing page | All present | Video is explicitly "after they have uploaded their file" — those surfaces stay in the plan and are NOT removed. | No |
| 5 | Prompt/command bar | Not in plan | Add as a **read-only run summary bar** at the bottom of the studio, not an LLM chat box. | No |
| 6 | `36 / 64 code checks passed` | "passed N of M automated checks"; never "code compliant" | Compatible. Use the plan's exact wording. | No |
| 7 | Scripted 31-frame loop with `NNN / 031` counter | Landing animation labelled "example run" | Landing demo stays scripted and labelled. The in-app run is driven by **real SSE events**, replayed at a fixed cadence; no invented metrics. | No |

## 7. Open questions for the user

1. **Grid vs lanes.** The video's 3+2 agent panels are much stronger than the plan's five
   lanes + chat feed. I am building the video's grid as the primary run view and keeping
   the feed as a thin strip. Confirm, or should the plan's layout win?
2. **Units.** Video is imperial, plan says metric for ID/VN/TH. I am using metric.
   Confirm, or do you want a unit toggle / US-code mode?
3. **Code pack.** Video shows US codes. I am keeping Indonesia + international baseline
   but showing the reference in each agent header. Confirm, or should a US pack be first?
4. **Fidelity target.** The recording is a polished scripted animation. For v1 I am
   building a real SSE-driven run with the same visual grammar, not a frame-perfect
   reproduction of the 31-frame loop. Confirm that's the intent for this pass.

None of these block coding — defaults are stated above and are cheap to reverse.
