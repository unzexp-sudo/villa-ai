"""Deterministic 3D clash detection (M4 ORCHESTRATOR).

Units follow MODULE_CONTRACT.md §1: centreline/geometry in METRES, sizes and
clearances in MILLIMETRES, ``z`` metres above finished floor. A clash is an
overlap (HARD) or a too-small gap (CLEARANCE) between the axis-aligned bounding
volumes of two elements belonging to DIFFERENT systems. 100 mm clearance is
required between different systems, 25 mm within one system (contract §1).

``element_bounds`` from M2 is used lazily when available, with a byte-for-byte
equivalent local AABB as fallback so detection is deterministic and works
before the whole tree lands. Nothing here mutates its inputs.
"""

from __future__ import annotations

import math
from typing import Any

from app.contract import Clash, ClashKind, Point3D, SystemKind
from app.contract.layers import Device, Segment

from .merge import all_elements

# Contract §1: 100 mm between systems (from RunLimits), 25 mm within one system.
INTRA_SYSTEM_CLEARANCE_MM = 25.0
# Cap the returned list; the real total is always reported via ``stats``.
MAX_CLASHES = 200


class _FallbackLimits:
    max_candidates_per_agent = 200
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


# ---- AABB (metres, 6-tuple: minx,miny,minz,maxx,maxy,maxz) ---------------

def _local_aabb(el) -> tuple[float, float, float, float, float, float]:
    """Axis-aligned bounding box inflated by the element's size in mm.

    Mirrors M2 ``routing.element_bounds`` exactly: round segments inflate by
    ``diameter_mm/2`` on every axis; rectangular ducts by ``width_mm/2`` in
    plan and ``height_mm/2`` vertically; devices by half their ``Size3D``.
    All conversions are mm -> m (/2000).
    """
    if isinstance(el, Segment) or hasattr(el, "polyline"):
        pts = el.polyline
        xs = [p.x for p in pts]
        ys = [p.y for p in pts]
        zs = [p.z for p in pts]
        minx, maxx = min(xs), max(xs)
        miny, maxy = min(ys), max(ys)
        minz, maxz = min(zs), max(zs)
        d = getattr(el, "diameter_mm", None)
        w = getattr(el, "width_mm", None)
        h = getattr(el, "height_mm", None)
        if d:
            r = float(d) / 2000.0
            minx -= r; maxx += r; miny -= r; maxy += r; minz -= r; maxz += r
        elif w and h:
            ex, ez = float(w) / 2000.0, float(h) / 2000.0
            minx -= ex; maxx += ex; miny -= ex; maxy += ex; minz -= ez; maxz += ez
        return (minx, miny, minz, maxx, maxy, maxz)
    if hasattr(el, "at") and hasattr(el, "size"):
        p, s = el.at, el.size
        ex, ey, ez = (s.length_mm / 2000.0, s.width_mm / 2000.0,
                      s.height_mm / 2000.0)
        return (p.x - ex, p.y - ey, p.z - ez,
                p.x + ex, p.y + ey, p.z + ez)
    raise TypeError("element_bounds expects a Segment or Device")


_BOUNDS = None  # cached lazy M2 element_bounds (or local fallback)


def _aabb(el) -> tuple[float, float, float, float, float, float]:
    global _BOUNDS
    if _BOUNDS is None:
        try:
            from app.geometry.routing import element_bounds
            _BOUNDS = element_bounds
        except Exception:
            _BOUNDS = _local_aabb
    try:
        return _BOUNDS(el)
    except Exception:
        return _local_aabb(el)


def _axis_gap(a0: float, a1: float, b0: float, b1: float) -> float:
    """Separation of two intervals on one axis (0 when they overlap/touch)."""
    return max(0.0, b0 - a1, a0 - b1)


def _box_gap_m(a, b) -> float:
    """Euclidean surface-to-surface gap between two AABBs, in METRES."""
    gx = _axis_gap(a[0], a[3], b[0], b[3])
    gy = _axis_gap(a[1], a[4], b[1], b[4])
    gz = _axis_gap(a[2], a[5], b[2], b[5])
    return math.sqrt(gx * gx + gy * gy + gz * gz)


def element_distance_mm(a, b) -> float:
    """Surface-to-surface distance between two elements, in MILLIMETRES.

    Public helper for the resolver: 0.0 means the bounding volumes intersect
    (a HARD clash). Uses the same AABB/gap maths as ``detect_clashes``.
    """
    return _box_gap_m(_aabb(a), _aabb(b)) * 1000.0


def _eid(el) -> str:
    return str(el.id)


def _midpoint(a, b) -> Point3D:
    cax, cay, caz = (a[0] + a[3]) / 2, (a[1] + a[4]) / 2, (a[2] + a[5]) / 2
    cbx, cby, cbz = (b[0] + b[3]) / 2, (b[1] + b[4]) / 2, (b[2] + b[5]) / 2
    return Point3D(x=(cax + cbx) / 2, y=(cay + cby) / 2, z=(caz + cbz) / 2)


def detect_clashes(layers, model=None, limits=None, *,
                   include_intra: bool = False,
                   stats: dict[str, Any] | None = None) -> list[Clash]:
    """Detect HARD / CLEARANCE clashes between elements of DIFFERENT systems.

    Iterates every unordered element pair once (symmetric pairs are deduped by
    the i<j loop and by an id-pair set). A HARD clash is an AABB intersection;
    a CLEARANCE clash is a gap smaller than the threshold ``clearance_mm``
    (100 mm between systems, 25 mm within one system). ``model`` is accepted
    for the frozen call signature; bounding volumes already carry all geometry.

    The returned list is capped at ``MAX_CLASHES``; the REAL total is written
    into ``stats["total"]`` when a dict is passed, so metrics never under-report
    a capped run. Every number is computed from real geometry.
    """
    lim = limits if limits is not None else _get_limits()
    clearance = float(lim.clearance_mm)

    items = list(all_elements(layers))
    boxes = [_aabb(el) for _s, el in items]
    systems = [s for s, _el in items]
    ids = [_eid(el) for _s, el in items]

    clashes: list[Clash] = []
    seen: set[tuple[str, str]] = set()
    real_total = 0

    n = len(items)
    for i in range(n):
        sa, boxa, ida = systems[i], boxes[i], ids[i]
        for j in range(i + 1, n):
            sb, boxb, idb = systems[j], boxes[j], ids[j]
            if sa == sb and not include_intra:
                continue  # cross-system clashes only (default)
            threshold = clearance if sa != sb else INTRA_SYSTEM_CLEARANCE_MM

            gap_m = _box_gap_m(boxa, boxb)
            distance_mm = gap_m * 1000.0
            hard = gap_m <= 1e-12
            if not hard and distance_mm >= threshold:
                continue

            key = (ida, idb) if ida <= idb else (idb, ida)
            if key in seen:
                continue
            seen.add(key)
            real_total += 1
            if len(clashes) >= MAX_CLASHES:
                continue

            kind = ClashKind.HARD if hard else ClashKind.CLEARANCE
            clashes.append(
                Clash(
                    id=f"clash-{real_total:04d}",
                    kind=kind,
                    a=ida,
                    b=idb,
                    system_a=sa,
                    system_b=sb,
                    at=_midpoint(boxa, boxb),
                    distance_mm=round(distance_mm, 4),
                    resolved=False,
                    fix=None,
                )
            )

    if stats is not None:
        stats["total"] = real_total
    return clashes


__all__ = [
    "detect_clashes", "element_distance_mm", "INTRA_SYSTEM_CLEARANCE_MM",
    "MAX_CLASHES",
]
