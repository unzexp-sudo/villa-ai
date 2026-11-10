"""Clash resolver (M4 ORCHESTRATOR).

Fixes merged clashes with minimal, ordered moves, re-running clash detection
and the owning system's own rule after EVERY move. A move that breaks a rule
(or increases the total clash count) is reverted and the clash is marked
``"sent back with keep-out zone"``; the next move in the vocabulary is tried.

Crucially the input ``clashes`` (clashes_before) is NEVER mutated: a separate,
deep-copied ``clashes_after`` list is returned. Every geometry edit is applied
to a deep clone of the layers, so the caller's merged layers stay intact too.
"""

from __future__ import annotations

import math
from typing import Any

from app.contract import (
    DEFAULT_BANDS,
    AgentEvent,
    AgentPhase,
    Clash,
    Layer,
    SystemKind,
)

from .clash import _aabb, _box_gap_m, detect_clashes
from .merge import all_elements

# Cross-system clearance (contract §1). A pair closer than this is still a
# clash; a pair further apart is resolved whatever the record said before.
INTRA_OR_CROSS_MM = 100.0


class _FallbackLimits:
    max_resolver_loops = 6
    max_performance_loops = 3
    max_run_seconds = 120.0
    clearance_mm = 100.0


def _get_limits():
    try:
        from app.core.config import get_limits
        return get_limits()
    except Exception:
        return _FallbackLimits()


# Ordered minimal-move vocabulary (task §3). The 50 mm raise is repeated so
# successive passes ACCUMULATE separation and can clear a same-z HARD clash;
# the duct shift and short-segment re-route are the escalating alternatives.
MOVE_PLAN: list[tuple[str, dict[str, float]]] = [
    ("raised a segment by 50 mm", {"dz": 0.05}),
    ("raised a segment by 50 mm", {"dz": 0.05}),
    ("raised a segment by 50 mm", {"dz": 0.05}),
    ("raised a segment by 50 mm", {"dz": 0.05}),
    ("shifted a duct by 100 mm", {"dx": 0.10}),
    ("re-routed a short segment", {"dx": 0.40, "dy": 0.40}),
]

_EPS = 1e-6


def _pair(c: Clash) -> tuple[str, str]:
    return (c.a, c.b) if c.a <= c.b else (c.b, c.a)


def _clone_layers(layers) -> dict[SystemKind, Layer]:
    return {k: v.model_copy(deep=True) for k, v in layers.items()}


def _find(layer: Layer, eid: str):
    for s in layer.segments:
        if s.id == eid:
            return s
    for d in layer.devices:
        if d.id == eid:
            return d
    return None


def _elem_size(el) -> float:
    b = _aabb(el)
    return math.sqrt((b[3] - b[0]) ** 2 + (b[4] - b[1]) ** 2 + (b[5] - b[2]) ** 2)


def _choose_movable(c: Clash, work) -> tuple[SystemKind, Any] | None:
    """Pick the smaller, non-fire element of the pair (easiest / safest to move)."""
    candidates = []
    for sysv, eid in ((c.system_a, c.a), (c.system_b, c.b)):
        layer = work.get(sysv)
        if layer is None:
            continue
        el = _find(layer, eid)
        if el is None:
            continue
        fire_penalty = 1 if sysv == SystemKind.FIRE else 0
        candidates.append((fire_penalty, _elem_size(el), sysv, el))
    if not candidates:
        return None
    candidates.sort(key=lambda t: (t[0], t[1]))
    return candidates[0][2], candidates[0][3]


# ---- geometry moves + revert ---------------------------------------------

def _snapshot(el):
    if hasattr(el, "polyline"):
        return [(p.x, p.y, p.z) for p in el.polyline]
    return (el.at.x, el.at.y, el.at.z)


def _restore(el, snap) -> None:
    if hasattr(el, "polyline"):
        for p, (x, y, z) in zip(el.polyline, snap):
            p.x, p.y, p.z = x, y, z
    else:
        el.at.x, el.at.y, el.at.z = snap


def _shift(el, dx: float = 0.0, dy: float = 0.0, dz: float = 0.0) -> None:
    """Translate a Segment polyline or a Device centre by metres."""
    if hasattr(el, "polyline"):
        for p in el.polyline:
            p.x += dx
            p.y += dy
            p.z += dz
    else:
        el.at.x += dx
        el.at.y += dy
        el.at.z += dz


# ---- owning-system rule validity -----------------------------------------

def _rule_valid(work, system: SystemKind, model) -> bool:
    """Verify the moved element's system still satisfies its own real rule."""
    layer = work.get(system)
    if layer is None:
        return True
    if system == SystemKind.DRAINAGE:
        # Gravity drainage must keep a 1-2% fall (uniform z-shifts preserve it).
        for s in layer.segments:
            if s.slope_pct is not None and not (1.0 - _EPS <= float(s.slope_pct) <= 2.0 + _EPS):
                return False
        return True
    if system == SystemKind.FIRE:
        lo, hi = DEFAULT_BANDS[SystemKind.FIRE]
        tol = 0.05
        for s in layer.segments:
            for p in s.polyline:
                if not (lo - tol <= p.z <= hi + tol):
                    return False
        for d in layer.devices:
            if not (lo - tol <= d.at.z <= hi + tol):
                return False
        return True
    if system == SystemKind.WATER:
        for s in layer.segments:
            v = s.meta.get("velocity_mps")
            if v is not None and float(v) > 2.0 + _EPS:
                return False
        return True
    if system == SystemKind.AIR:
        for s in layer.segments:
            v = s.meta.get("velocity_mps")
            if v is not None and float(v) > 8.0 + _EPS:
                return False
        return True
    if system == SystemKind.POWER:
        for s in layer.segments:
            cur, brk = s.meta.get("current_a"), s.meta.get("breaker_a")
            if cur is not None and brk is not None and float(cur) > float(brk) + _EPS:
                return False
        return True
    return True


def _emit_resolved(emitter, run_id: str | None, c: Clash, after: list[Clash]) -> None:
    if emitter is None or not run_id:
        return
    remaining = sum(1 for x in after if not x.resolved)
    emitter.emit(
        AgentEvent(
            run_id=run_id,
            seq=0,
            ts=0,
            name="clash.resolved",
            agent="fixer",
            phase=AgentPhase.COMMITTING,
            message=f"{c.id}: {c.fix} ({remaining} clash(es) remaining)",
            metrics={
                "remaining": float(remaining),
                "clashes_after": float(remaining),
            },
        )
    )


def resolve_clashes(layers, clashes: list[Clash], model,
                    emitter=None, limits=None, run_id: str | None = None
                    ) -> tuple[dict[SystemKind, Layer], list[Clash]]:
    """Resolve clashes with bounded minimal moves.

    Returns ``(resolved_layers, clashes_after)`` where ``clashes_after`` is a
    SEPARATE deep copy of ``clashes`` with ``resolved`` / ``fix`` set on fixed
    clashes. ``clashes`` (clashes_before) is left untouched, as are the input
    ``layers`` (all edits happen on a deep clone).
    """
    lim = limits if limits is not None else _get_limits()
    max_loops = max(0, int(lim.max_resolver_loops))

    work = _clone_layers(layers)
    after = [c.model_copy(deep=True) for c in clashes]  # separate list, no mutation

    attempt_of: dict[int, int] = {id(c): 0 for c in after}

    for _pass in range(max_loops):
        current = detect_clashes(work, model, limits=lim)
        if not current:
            break
        cur_pairs = {_pair(x) for x in current}
        moved_this_pass: set[tuple[str, str]] = set()

        for c in after:
            if c.resolved:
                continue
            key = _pair(c)
            if key not in cur_pairs:
                # Genuinely gone (cleared by an earlier fix in this pass).
                c.resolved = True
                c.fix = c.fix or "cleared by an earlier fix"
                _emit_resolved(emitter, run_id, c, after)
                continue

            target = _choose_movable(c, work)
            if target is None:
                c.fix = "sent back with keep-out zone"
                continue
            system, el = target
            ekey = (system.value, str(el.id))
            if ekey in moved_this_pass:
                continue  # one edit per element per pass; accumulate across passes
            moved_this_pass.add(ekey)

            text, kw = MOVE_PLAN[min(attempt_of[id(c)], len(MOVE_PLAN) - 1)]
            attempt_of[id(c)] += 1

            snap = _snapshot(el)
            before_total = len(current)
            _shift(el, **kw)
            new = detect_clashes(work, model, limits=lim)  # ALWAYS re-run detection
            rule_ok = _rule_valid(work, system, model)     # ALWAYS verify own rule

            if (not rule_ok) or len(new) > before_total:
                _restore(el, snap)  # revert the bad move
                if not rule_ok:
                    c.fix = "sent back with keep-out zone"
                continue  # try the next move on a later pass

            current = new
            cur_pairs = {_pair(x) for x in new}
            if key not in cur_pairs:
                c.resolved = True
                c.fix = text
                _emit_resolved(emitter, run_id, c, after)

    return work, after


def refresh_distances(clashes: list[Clash],
                      layers: dict[SystemKind, Layer]) -> list[Clash]:
    """Re-measure every clash against the RESOLVED layers.

    ``resolve_clashes`` returns the original clash records with ``resolved`` /
    ``fix`` set, but their ``distance_mm`` still describes the BEFORE geometry.
    Rendering that as "7 clashes, 25 mm apart" is a lie about the fixed state:
    30 clashes resolved with their distances untouched reads as 39 unresolved
    problems in the UI. Recomputing from the post-resolve layers is the only
    reading that is true to what the viewer is drawing.

    Elements that no longer exist (a re-route replaced a segment) keep their
    recorded distance and are reported as ``resolved``.
    """
    index: dict[str, Any] = {}
    for layer in layers.values():
        for el in (*layer.segments, *layer.devices):
            index[str(el.id)] = el

    out: list[Clash] = []
    for c in clashes:
        ea, eb = index.get(str(c.a)), index.get(str(c.b))
        if ea is None or eb is None:
            out.append(c.model_copy(update={"resolved": True,
                                            "fix": c.fix or "re-routed"}))
            continue
        gap_mm = _box_gap_m(_aabb(ea), _aabb(eb)) * 1000.0
        still_clashing = gap_mm < INTRA_OR_CROSS_MM
        if not still_clashing and not c.resolved:
            # Geometry moved apart by another fix; record it honestly.
            out.append(c.model_copy(update={"resolved": True,
                                            "fix": c.fix or "cleared by an earlier fix",
                                            "distance_mm": round(gap_mm, 2)}))
        else:
            out.append(c.model_copy(update={"distance_mm": round(gap_mm, 2)}))
    return out


__all__ = ["resolve_clashes", "refresh_distances", "MOVE_PLAN"]
