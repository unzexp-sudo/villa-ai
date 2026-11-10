"""Deterministic layer scoring (M2 geometry).

Weighted score in ``[0.0, 1.0]``. Every sub-score is a *monotonically
decreasing* function of a "bad" quantity (length, bends, wall crossings, cost)
or a plain 0..1 adherence ratio (z-band, code hints). Therefore a shorter /
less-bent / cleaner layer ALWAYS scores >= a worse one, in the good direction.

``score_breakdown(layer) -> dict[str, float]`` exposes the sub-scores for the
live UI (candidate flicker / verdict line). ``score_layer`` is the weighted
combination. Both are pure and deterministic (no RNG, no wall clock).
"""

from app.contract import DEFAULT_BANDS
from app.contract.layers import Layer

from .routing import count_bends, segment_length_m

__all__ = ["score_layer", "score_breakdown", "score_breakdown_for"]

# Scales (normalisation denominators) for the monotone 1/(1+x/scale) terms.
_LENGTH_SCALE = 80.0    # metres
_BENDS_SCALE = 16.0
_WALL_SCALE = 6.0
_SYSTEM_SCALE = 8.0
_COST_SCALE = 400.0     # mm * m proxy


def _seg_size_mm(seg) -> float:
    if seg.diameter_mm:
        return float(seg.diameter_mm)
    if seg.width_mm or seg.height_mm:
        return (float(seg.width_mm or 0.0) + float(seg.height_mm or 0.0)) / 2.0
    return 10.0


def _z_band_adherence(layer) -> float:
    lo, hi = DEFAULT_BANDS[layer.system]
    segs = layer.segments
    if not segs:
        return 0.0
    inside = 0
    for s in segs:
        zs = [p.z for p in s.polyline]
        if all(lo - 1e-6 <= z <= hi + 1e-6 for z in zs):
            inside += 1
    return inside / len(segs)


def _code_hint_ratio(layer) -> float:
    """Fraction of segments passing a cheap, honest code-rule hint."""
    segs = layer.segments
    if not segs:
        return 0.0
    system = layer.system
    passed = 0
    for s in segs:
        m = s.meta
        if system.value == "drainage":
            ok = 1.0 <= (s.slope_pct or 0.0) <= 2.0
        elif system.value == "water":
            ok = float(m.get("velocity_mps", 0.0)) <= 2.0
        elif system.value == "air":
            ok = float(m.get("velocity_mps", 0.0)) <= 8.0
        elif system.value == "power":
            ok = float(m.get("current_a", 0.0)) <= float(m.get("breaker_a", 63.0))
        elif system.value == "fire":
            ok = float(m.get("spacing_m", 0.0)) <= float(m.get("max_spacing_m", 4.6))
        else:
            ok = True
        if ok:
            passed += 1
    return passed / len(segs)


def _raw_metrics(layer) -> dict[str, float]:
    segs = layer.segments
    total_length = sum(segment_length_m(s.polyline) for s in segs)
    total_bends = sum(count_bends(s.polyline) for s in segs)
    cost = sum(_seg_size_mm(s) * segment_length_m(s.polyline) for s in segs)
    # wall / system-crossing counts are precomputed at build time (they need
    # the model / peer layers). Fall back to 0 for hand-built layers.
    wall = float(layer.metrics.get("wall_crossings", 0.0))
    system = float(layer.metrics.get("system_crossings", 0.0))
    return {
        "total_length_m": total_length,
        "total_bends": float(total_bends),
        "cost_proxy": cost,
        "wall_crossings": wall,
        "system_crossings": system,
        "z_band_adherence": _z_band_adherence(layer),
        "code_hint_ratio": _code_hint_ratio(layer),
    }


def score_breakdown_for(layer, model=None, strategy=None) -> dict[str, float]:
    """Full weighted breakdown. ``strategy.prefer_short_runs`` (0..1) tilts
    weight toward the length/bends terms. ``model`` is accepted for API
    symmetry; crossing counts come from the layer's build-time metrics.
    """
    m = _raw_metrics(layer)
    prefer = getattr(strategy, "prefer_short_runs", 0.5) if strategy is not None else 0.5
    try:
        prefer = min(1.0, max(0.0, float(prefer)))
    except (TypeError, ValueError):
        prefer = 0.5

    subs = {
        "length": 1.0 / (1.0 + m["total_length_m"] / _LENGTH_SCALE),
        "bends": 1.0 / (1.0 + m["total_bends"] / _BENDS_SCALE),
        "wall_crossings": 1.0 / (1.0 + m["wall_crossings"] / _WALL_SCALE),
        "system_crossings": 1.0 / (1.0 + m["system_crossings"] / _SYSTEM_SCALE),
        "cost": 1.0 / (1.0 + m["cost_proxy"] / _COST_SCALE),
        "z_band": max(0.0, min(1.0, m["z_band_adherence"])),
        "code_hints": max(0.0, min(1.0, m["code_hint_ratio"])),
    }
    weights = {
        "length": 0.24 + 0.10 * prefer,
        "bends": 0.14 + 0.06 * prefer,
        "wall_crossings": 0.14,
        "system_crossings": 0.10,
        "cost": 0.10,
        "z_band": 0.16,
        "code_hints": 0.12,
    }
    wsum = sum(weights.values())
    score = sum(weights[k] * subs[k] for k in subs) / wsum
    score = max(0.0, min(1.0, score))

    out = dict(subs)
    out["score"] = score
    # A few raw values (still floats) are handy for the live UI.
    out["total_length_m"] = round(m["total_length_m"], 4)
    out["total_bends"] = m["total_bends"]
    out["wall_crossings"] = m["wall_crossings"]
    out["system_crossings"] = m["system_crossings"]
    return out


def score_breakdown(layer: Layer) -> dict[str, float]:
    """Breakdown for one layer (live-UI friendly ``dict[str, float]``)."""
    return score_breakdown_for(layer)


def score_layer(layer: Layer, model=None, strategy=None) -> float:
    """Weighted score in ``[0, 1]``. Monotone in the good direction."""
    return score_breakdown_for(layer, model=model, strategy=strategy)["score"]
