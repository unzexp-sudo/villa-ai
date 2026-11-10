"""M2 GEOMETRY TOOLKIT — deterministic layouts, routing, sizing, scoring.

Public API (frozen names; siblings import from here or from
``app.geometry.toolkit``). No sibling module that may not exist yet is imported
at module scope.
"""

from .toolkit import (
    LayoutSpec,
    Toolkit,
    get_toolkit,
    build_candidates,
    route_orthogonal,
    segment_length_m,
    count_bends,
    size_drainage,
    size_water,
    size_duct,
    size_cable,
    sprinkler_spacing,
    element_bounds,
    score_layer,
    score_breakdown,
    score_breakdown_for,
    exhaust_rate_lps,
    segments_intersect_xy,
)

__all__ = [
    "LayoutSpec", "Toolkit", "get_toolkit", "build_candidates",
    "route_orthogonal", "segment_length_m", "count_bends",
    "size_drainage", "size_water", "size_duct", "size_cable",
    "sprinkler_spacing", "element_bounds", "score_layer", "score_breakdown",
    "score_breakdown_for", "exhaust_rate_lps", "segments_intersect_xy",
]
