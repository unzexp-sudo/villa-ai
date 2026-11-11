"""Public geometry toolkit API for Villa AI (M2).

FREEZE: siblings call exactly these names. All of them are re-exported from
``app.geometry`` (see ``__init__.py``). The Toolkit structurally implements
the frozen ``app.contract.ports.Toolkit`` Protocol (it does NOT subclass it).

Sibling modules that may not exist yet (planner Strategy, core RunLimits, ...)
are deliberately NOT imported at module scope. Strategy and limits are duck-
typed via ``getattr`` with defaults, so this module and its tests run before
the rest of the tree lands. If a caller ever needs the real class, they import
it lazily inside a function and raise a clear RuntimeError on absence.
"""

import random
from dataclasses import dataclass
from typing import Any, Iterator

from app.contract import DEFAULT_BANDS, BuildingModel, Layer, SystemKind

# Re-export the primitives/calculations so `from app.geometry.toolkit import X`
# and `from app.geometry import X` both work.
from .routing import (
    build_layer,
    count_bends,
    element_bounds,
    route_orthogonal,
    segment_length_m,
    segments_intersect_xy,
)
from .scoring import score_breakdown, score_breakdown_for, score_layer
from .sizing import (
    exhaust_rate_lps,
    size_cable,
    size_duct,
    size_drainage,
    size_water,
    sprinkler_spacing,
)

__all__ = [
    "LayoutSpec", "Toolkit", "get_toolkit", "build_candidates",
    "route_orthogonal", "segment_length_m", "count_bends",
    "size_drainage", "size_water", "size_duct", "size_cable",
    "sprinkler_spacing", "element_bounds", "score_layer", "score_breakdown",
    "score_breakdown_for", "exhaust_rate_lps", "segments_intersect_xy",
]

# Design centreline heights for candidate #0 (the default/best). Each value
# lies INSIDE that system's soft DEFAULT_BANDS.
#
# Research-based vertical zoning:
# - Drainage routed UNDERFLOOR at z=0.30 (gravity fall to sewer outlet).
# - Water in ceiling void at z=2.55 (pressurised, drops to fixtures).
# - Air slightly below water at z=2.42 (tight clearance for clash demo).
# - Power above water at z=2.68 (flexible conduit, least constraint).
# - Fire highest at z=2.95 (sprinkler head clearance).
_DESIGN_Z = {
    SystemKind.DRAINAGE: 0.30,
    SystemKind.WATER: 2.55,
    SystemKind.AIR: 2.42,
    SystemKind.POWER: 2.68,
    SystemKind.FIRE: 2.95,
}


@dataclass
class LayoutSpec:
    """Input to ``Toolkit.candidates``. Strategy/limits are duck-typed so M3's
    real ``Strategy`` / M1's ``RunLimits`` (which may not exist yet at import
    time) can be passed straight through.
    """

    system: SystemKind
    model: BuildingModel
    strategy: Any = None
    limits: Any = None
    seed: int = 42
    max_candidates: int = 200


def _strategy_fields(strategy, system) -> dict[str, Any]:
    """Read Strategy via getattr with defaults (M3 owns the real class)."""
    trunk = getattr(strategy, "trunk", "central") if strategy else "central"
    stack = (getattr(strategy, "stack_location", "wet_core") if strategy
             else "wet_core")
    band = getattr(strategy, "z_band", None) if strategy else None
    if not band or len(band) != 2:
        band = DEFAULT_BANDS[system]
    band = (float(band[0]), float(band[1]))
    max_bends = getattr(strategy, "max_bends", 4) if strategy else 4
    prefer = getattr(strategy, "prefer_short_runs", 0.5) if strategy else 0.5
    params = getattr(strategy, "params", {}) if strategy else {}
    if not isinstance(params, dict):
        params = {}
    try:
        max_bends = int(max_bends)
    except (TypeError, ValueError):
        max_bends = 4
    try:
        prefer = float(prefer)
    except (TypeError, ValueError):
        prefer = 0.5
    return {
        "trunk": trunk, "stack_location": stack, "z_band": band,
        "max_bends": max_bends, "prefer_short_runs": prefer, "params": params,
    }


def _effective_cap(limits, max_candidates) -> int:
    per_agent = getattr(limits, "max_candidates_per_agent", 200) if limits is not None else 200
    try:
        cap = int(min(per_agent, max_candidates))
    except (TypeError, ValueError):
        cap = 200
    return max(1, cap)


def _iter_layers(spec: LayoutSpec) -> Iterator[Layer]:
    """Deterministic candidate generator.

    Candidate #0 is the engineered crossing default (no origin shift, design
    z). Later candidates vary trunk origin (small delta), branch elbow order
    and z within DEFAULT_BANDS, using only ``random.Random(spec.seed)``. Every
    candidate keeps the long crossing trunk legs, so the merged default layout
    always yields real XY crossings. The input model is cloned first.
    """
    system = spec.system
    model = spec.model.clone()                # never mutate the caller's model
    sf = _strategy_fields(spec.strategy, system)
    cap = _effective_cap(spec.limits, spec.max_candidates)
    rng = random.Random(spec.seed)
    band_lo, band_hi = DEFAULT_BANDS[system]  # frozen soft bands (geometry z)
    design_z = _DESIGN_Z[system]
    avoid_walls = model.level0.walls
    base_elbow = str(sf["params"].get("elbow", "x") or "x")

    layers: list[Layer] = []
    for i in range(cap):
        if i == 0:
            z = design_z
            shift = (0.0, 0.0)
            elbow = base_elbow
        else:
            z = rng.uniform(band_lo, band_hi)
            shift = (rng.uniform(-0.3, 0.3), rng.uniform(-0.3, 0.3))
            elbow = "x" if i % 2 == 0 else "y"
        layer = build_layer(
            system, model, z=z, elbow=elbow, origin_shift=shift,
            params=sf["params"], avoid_walls=avoid_walls,
            strategy_summary=sf, seed=spec.seed, candidate_index=i,
            max_bends=sf["max_bends"],
        )
        layers.append(layer)

    # Score for the live UI / traceability. No sorting: candidate #0 is the
    # designated default/best and is yielded first (it has the fully
    # engineered crossing trunks). Every candidate still crosses, so any
    # consumer that re-picks by score also gets a crossing layout.
    scores = [score_layer(layer, model, spec.strategy) for layer in layers]
    best_score = round(max(scores), 6) if scores else 0.0
    for idx, (layer, sc) in enumerate(zip(layers, scores)):
        layer.metrics["score"] = round(sc, 6)
        layer.metrics["layouts_tested"] = float(cap)
        layer.metrics["best_score"] = best_score
        if idx == 0:
            layer.metrics["is_default"] = 1.0

    for layer in layers:
        yield layer


class Toolkit:
    """Structurally implements the frozen ``app.contract.ports.Toolkit``."""

    def candidates(self, spec: LayoutSpec) -> Iterator[Layer]:
        return _iter_layers(spec)

    def score(self, layer: Layer, model: BuildingModel) -> float:
        return score_layer(layer, model)


_instance: Toolkit | None = None


def get_toolkit() -> Toolkit:
    """Lazy singleton."""
    global _instance
    if _instance is None:
        _instance = Toolkit()
    return _instance


def build_candidates(system: SystemKind, model: BuildingModel, strategy,
                     limits, seed: int = 42) -> Iterator[Layer]:
    """Yield deterministic Layer candidates, capped by
    ``limits.max_candidates_per_agent`` (and ``LayoutSpec.max_candidates``).
    Never mutates ``model``.
    """
    spec = LayoutSpec(system=system, model=model, strategy=strategy,
                      limits=limits, seed=seed)
    yield from get_toolkit().candidates(spec)
