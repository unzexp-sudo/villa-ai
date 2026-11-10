"""Orthogonal trunk-and-branch routing over the floor plan (M2 geometry).

Design notes
------------
* Geometry is in METRES, ``z`` metres above finished floor, component sizes
  in MILLIMETRES (see ``sizing.py`` + ``Segment``).
* ``route_orthogonal`` produces an axis-aligned Manhattan polyline with at
  most 4 bends. Wall centreline crossings are *penalised, not forbidden*: the
  router tries both elbow orders and a couple of dogleg lanes and picks the
  one crossing the fewest walls, then the shortest, then the least-bent.
* The five *default* trunks are deliberately engineered to cross in plan
  (XY) over ``villa_120`` so the merge/clash demo has a real before/after
  delta. Sources are read from equipment (SE water tank, NE electrical panel,
  E AC outdoor, SW sewer, NW fire fallback), the fixture's own ``a_crossing``
  assumption. Trunks live in the *soft overlapping* DEFAULT_BANDS, never in
  segregated non-overlapping bands.
* Candidate variation shifts the trunk origin by a small deterministic delta,
  flips branch elbow order, and varies z within DEFAULT_BANDS. The main
  crossing legs are preserved, so every candidate still crosses.
* Nothing here mutates the input ``BuildingModel``: ``build_candidates``
  clones it before this module sees it. Uses only ``random.Random(seed)``.

Sibling modules that may not exist yet are NEVER imported here at module
scope; strategy / limits are duck-typed by the caller (``toolkit.py``).
"""

import math
from typing import Any

from app.contract import DEFAULT_BANDS, SystemKind
from app.contract.building import Point2D, Point3D, Size3D
from app.contract.layers import Device, Layer, Segment

from .sizing import (
    exhaust_rate_lps,
    size_cable,
    size_duct,
    size_drainage,
    size_water,
    sprinkler_spacing,
)

__all__ = [
    "route_orthogonal", "segment_length_m", "count_bends", "element_bounds",
    "build_layer", "segments_intersect_xy",
]

_EPS = 1e-9

# Segment.kind is a closed Literal in the frozen contract.
_KIND = {
    SystemKind.DRAINAGE: "pipe",
    SystemKind.WATER: "pipe",
    SystemKind.AIR: "duct",
    SystemKind.POWER: "cable",
    SystemKind.FIRE: "sprinkler_pipe",
}


# ---------------------------------------------------------------------------
# Pure 2D geometry helpers
# ---------------------------------------------------------------------------

def _orient(p, q, r) -> int:
    """Orientation of ordered triplet (p, q, r) using 2D (x, y) tuples."""
    val = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    if abs(val) < _EPS:
        return 0
    return 1 if val > 0 else -1


def _on_segment(a, b, p) -> bool:
    """True when collinear point p lies on segment a-b (inclusive)."""
    return (min(a[0], b[0]) - _EPS <= p[0] <= max(a[0], b[0]) + _EPS
            and min(a[1], b[1]) - _EPS <= p[1] <= max(a[1], b[1]) + _EPS)


def segments_intersect_xy(a, b, c, d) -> bool:
    """XY segment intersection (proper crossing OR endpoint touch).

    Collinear overlaps count as intersecting only when an endpoint of one
    lies inside the other. Used by routing wall checks, scoring and the M4
    clash detector's fine pass. Points are 2D (x, y) tuples or objects with
    ``.x`` / ``.y``.
    """
    a, b, c, d = (_xy(p) for p in (a, b, c, d))
    o1, o2, o3, o4 = (_orient(a, b, c), _orient(a, b, d),
                      _orient(c, d, a), _orient(c, d, b))
    if o1 != o2 and o3 != o4:
        return True
    if o1 == 0 and _on_segment(a, b, c):
        return True
    if o2 == 0 and _on_segment(a, b, d):
        return True
    if o3 == 0 and _on_segment(c, d, a):
        return True
    if o4 == 0 and _on_segment(c, d, b):
        return True
    return False


def _xy(p):
    return (float(p.x), float(p.y)) if hasattr(p, "x") else (float(p[0]), float(p[1]))


def _wall_pairs(avoid) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Extract (p1, p2) centreline pairs from Wall-like objects, dicts, or
    2-tuples of points. Unrecognised entries are ignored.
    """
    pairs = []
    for w in (avoid or []):
        start = getattr(w, "start", None)
        end = getattr(w, "end", None)
        if start is None and isinstance(w, dict):
            start, end = w.get("start"), w.get("end")
        if start is None and isinstance(w, (tuple, list)) and len(w) == 2:
            start, end = w[0], w[1]
        if start is None or end is None:
            continue
        pairs.append((_xy(start), _xy(end)))
    return pairs


def _polyline_wall_crossings(points, walls) -> int:
    """Count how many wall centrelines a 2D polyline crosses."""
    n = 0
    pts = [_xy(p) for p in points]
    for (a, b) in zip(pts, pts[1:]):
        for (c, d) in walls:
            if segments_intersect_xy(a, b, c, d):
                n += 1
    return n


def _len_2d(points) -> float:
    pts = [_xy(p) for p in points]
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))


def segment_length_m(polyline) -> float:
    """Total 3D centreline length in METRES."""
    pts = list(polyline)
    total = 0.0
    for a, b in zip(pts, pts[1:]):
        dx, dy, dz = b.x - a.x, b.y - a.y, b.z - a.z
        total += math.sqrt(dx * dx + dy * dy + dz * dz)
    return total


def count_bends(polyline) -> int:
    """Number of direction changes (corners) in a polyline.

    Collinear intermediate vertices and zero-length steps do not count.
    Returns 0 for a straight 2-point run.
    """
    pts = list(polyline)
    bends = 0
    for i in range(1, len(pts) - 1):
        a, b, c = pts[i - 1], pts[i], pts[i + 1]
        v1 = (b.x - a.x, b.y - a.y, b.z - a.z)
        v2 = (c.x - b.x, c.y - b.y, c.z - b.z)
        if _zero(v1) or _zero(v2):
            continue
        cx = v1[1] * v2[2] - v1[2] * v2[1]
        cy = v1[2] * v2[0] - v1[0] * v2[2]
        cz = v1[0] * v2[1] - v1[1] * v2[0]
        if abs(cx) + abs(cy) + abs(cz) > _EPS:
            bends += 1
    return bends


def _zero(v) -> bool:
    return abs(v[0]) + abs(v[1]) + abs(v[2]) < _EPS


def element_bounds(segment_or_device) -> tuple[float, float, float, float, float, float]:
    """Axis-aligned bounding box (minx, miny, minz, maxx, maxy, maxz) for a
    ``Segment`` or ``Device``, inflated by its size in MILLIMETRES.

    Used by M4's clash detector. Round segments inflate by ``diameter_mm/2``
    on every axis; rectangular ducts by ``width_mm/2`` in plan and
    ``height_mm/2`` vertically; devices by half their ``Size3D`` extents.
    All conversions are /1000 (mm -> m).
    """
    obj = segment_or_device
    if hasattr(obj, "polyline"):
        pts = obj.polyline
        xs = [p.x for p in pts]
        ys = [p.y for p in pts]
        zs = [p.z for p in pts]
        minx, maxx = min(xs), max(xs)
        miny, maxy = min(ys), max(ys)
        minz, maxz = min(zs), max(zs)
        d = getattr(obj, "diameter_mm", None)
        w = getattr(obj, "width_mm", None)
        h = getattr(obj, "height_mm", None)
        if d:
            r = float(d) / 2000.0
            minx -= r; maxx += r; miny -= r; maxy += r; minz -= r; maxz += r
        elif w and h:
            ex, ez = float(w) / 2000.0, float(h) / 2000.0
            minx -= ex; maxx += ex; miny -= ex; maxy += ex; minz -= ez; maxz += ez
        return (minx, miny, minz, maxx, maxy, maxz)
    if hasattr(obj, "at") and hasattr(obj, "size"):
        p, s = obj.at, obj.size
        ex, ey, ez = (s.length_mm / 2000.0, s.width_mm / 2000.0,
                      s.height_mm / 2000.0)
        return (p.x - ex, p.y - ey, p.z - ez,
                p.x + ex, p.y + ey, p.z + ez)
    raise TypeError("element_bounds expects a Segment or Device")


# ---------------------------------------------------------------------------
# Orthogonal (Manhattan) routing
# ---------------------------------------------------------------------------

def _l_route(s, e, order):
    """Single-bend L. order 'x' = horizontal first, 'y' = vertical first."""
    if order == "y":
        mid = (s[0], e[1])
    else:
        mid = (e[0], s[1])
    return [s, mid, e]


def _dogleg(s, e, order, off):
    """Three-bend dogleg through an offset lane (to detour around walls)."""
    if order == "y":
        lane_x = s[0] + off
        return [s, (lane_x, s[1]), (lane_x, e[1]), e]
    lane_y = s[1] + off
    return [s, (s[0], lane_y), (e[0], lane_y), e]


def _norm_order(prefer) -> str:
    p = str(prefer or "x").lower()
    if p in ("y", "v", "vertical"):
        return "y"
    return "x"


def route_orthogonal(start, end, z, prefer: str = "x", avoid: list | None = None):
    """Axis-aligned Manhattan polyline from ``start`` to ``end`` at height z.

    Returns ``list[Point3D]`` (>= 2 points), <= 4 bends. ``prefer`` selects
    the elbow order ('x' horizontal-first / 'y' vertical-first). ``avoid`` is
    a list of Wall-like objects whose centrelines are *penalised* (the router
    prefers the fewest crossings) rather than hard-blocked.
    """
    s, e = _xy(start), _xy(end)
    z = float(z)
    if math.hypot(e[0] - s[0], e[1] - s[1]) < _EPS:
        return [Point3D(x=s[0], y=s[1], z=z),
                Point3D(x=s[0] + 0.02, y=s[1], z=z)]
    walls = _wall_pairs(avoid)
    pref = _norm_order(prefer)
    other = "y" if pref == "x" else "x"

    candidates = []  # (2d points, tiebreak rank)
    for rank, order in enumerate((pref, other)):
        candidates.append((_l_route(s, e, order), float(rank)))
        for off in (0.6, -0.6, 1.2, -1.2):
            candidates.append((_dogleg(s, e, order, off), float(rank) + 0.5))

    def penalty(item):
        pts, rank = item
        wc = _polyline_wall_crossings(pts, walls)
        ln = _len_2d(pts)
        bd = count_bends([Point3D(x=p[0], y=p[1], z=0.0) for p in pts])
        return (wc, ln, bd, rank)

    best = min(candidates, key=penalty)[0]
    return [Point3D(x=p[0], y=p[1], z=z) for p in best]


# ---------------------------------------------------------------------------
# Trunk + branch layer construction
# ---------------------------------------------------------------------------

def _source(model, system) -> Point2D:
    """Source equipment point per system, with a deterministic fallback."""
    kinds = {
        SystemKind.DRAINAGE: ["sewer_point", "septic_tank"],
        SystemKind.WATER: ["water_tank", "pump", "water_meter"],
        SystemKind.AIR: ["ac_outdoor", "exhaust_fan"],
        SystemKind.POWER: ["electrical_panel", "distribution_board"],
        SystemKind.FIRE: ["riser", "sprinkler"],
    }
    for kind in kinds[system]:
        eqs = model.equipment_of(kind)  # accepts str (str-Enum equality)
        if eqs:
            return Point2D(x=eqs[0].at.x, y=eqs[0].at.y)
    minx, miny, maxx, maxy = model.bbox()
    w, h = maxx - minx, maxy - miny
    site = model.site
    fallbacks = {
        SystemKind.DRAINAGE: (site.sewer_point
                             or Point2D(x=minx + 0.05 * w, y=miny + 0.05 * h)),
        SystemKind.WATER: (site.water_meter
                          or Point2D(x=maxx, y=miny + 0.1 * h)),
        SystemKind.AIR: Point2D(x=maxx + 0.05 * w, y=(miny + maxy) / 2.0),
        SystemKind.POWER: (site.power_incomer
                           or Point2D(x=maxx - 0.05 * w, y=maxy - 0.05 * h)),
        SystemKind.FIRE: Point2D(x=minx + 0.05 * w, y=maxy - 0.05 * h),
    }
    fb = fallbacks[system]
    return Point2D(x=fb.x, y=fb.y)


def _engineered_trunk(system, model, shift) -> list[Point2D]:
    """The five default trunks, engineered to cross in plan over villa_120.

    Sources: SE water, NE power, E air, SW drainage, NW fire. Each trunk has
    a long horizontal leg and a long vertical leg that cross the legs of the
    others in the central hall / wet-core region (see module docstring).
    ``shift`` is a small per-candidate delta applied uniformly; the crossing
    legs survive it.
    """
    minx, miny, maxx, maxy = model.bbox()
    w, h = maxx - minx, maxy - miny
    cx, cy = (minx + maxx) / 2.0, (miny + maxy) / 2.0
    sx, sy = shift
    src = _source(model, system)

    def p(x, y):
        return Point2D(x=x + sx, y=y + sy)

    s = Point2D(x=src.x + sx, y=src.y + sy)
    if system == SystemKind.WATER:                       # SE -> W, main horiz @ cy
        return [s, p(src.x, cy), p(minx + 0.17 * w, cy)]
    if system == SystemKind.POWER:                       # NE -> S, main vert @ cx+
        return [s, p(cx + 0.167 * w, src.y), p(cx + 0.167 * w, miny + 0.1 * h)]
    if system == SystemKind.AIR:                         # E -> S, horiz @ src.y, vert @ cx-
        return [s, p(cx - 0.083 * w, src.y), p(cx - 0.083 * w, miny + 0.05 * h)]
    if system == SystemKind.DRAINAGE:                    # SW -> N, vert @ wet core
        return [s, p(minx + 0.25 * w, src.y), p(minx + 0.25 * w, maxy - 0.1 * h)]
    # FIRE: NW -> E across upper band -> S (two bends)
    return [s, p(src.x, cy + 0.35 * h), p(maxx - 0.04 * w, cy + 0.35 * h),
            p(maxx - 0.04 * w, cy - 0.2 * h)]


def _room_center(room) -> Point2D:
    pts = room.polygon
    return Point2D(x=sum(q.x for q in pts) / len(pts),
                   y=sum(q.y for q in pts) / len(pts))


def _room_area(room) -> float:
    if room.area_m2 and room.area_m2 > 0:
        return float(room.area_m2)
    pts = room.polygon
    a = 0.0
    for i in range(len(pts)):
        q, r = pts[i], pts[(i + 1) % len(pts)]
        a += q.x * r.y - r.x * q.y
    return abs(a) / 2.0


def _targets(system, model):
    """Return [(target Point2D, context dict, device_info|None), ...]."""
    level = model.level0
    rooms, fixtures = level.rooms, level.fixtures
    wet = [r for r in rooms if r.wet]
    out = []

    def kind_str(f):
        return str(getattr(f.kind, "value", f.kind)).lower()

    if system == SystemKind.DRAINAGE:
        dk = {"wc", "basin", "shower", "floor_drain", "bath",
              "kitchen_sink", "washing_machine", "sink"}
        fs = [f for f in fixtures if kind_str(f) in dk] or list(fixtures)
        for f in fs:
            out.append((Point2D(x=f.at.x, y=f.at.y),
                        {"fixture_units": float(f.props.get("fixture_units", 2.0)),
                         "fixture_id": f.id}, None))
    elif system == SystemKind.WATER:
        fs = list(fixtures)
        if fs:
            for f in fs:
                out.append((Point2D(x=f.at.x, y=f.at.y),
                            {"fixture_units": float(f.props.get("fixture_units", 2.0)),
                             "fixture_id": f.id}, None))
        else:
            for r in (wet or rooms):
                out.append((_room_center(r), {"fixture_units": 2.0}, None))
    elif system == SystemKind.AIR:
        for r in rooms:
            flow = exhaust_rate_lps(r.type) if r.wet else 25.0
            out.append((_room_center(r), {"flow_lps": flow, "room_id": r.id},
                        ("diffuser", r)))
    elif system == SystemKind.POWER:
        for r in rooms:
            out.append((_room_center(r),
                        {"load_w": 300.0 + _room_area(r) * 15.0,
                         "room_id": r.id, "wet": bool(r.wet)}, None))
    elif system == SystemKind.FIRE:
        for r in rooms:
            out.append((_room_center(r),
                        {"area_m2": _room_area(r), "room_id": r.id},
                        ("sprinkler", r)))
    return out


def _trunk_context(system, targets) -> dict:
    if system in (SystemKind.DRAINAGE, SystemKind.WATER):
        return {"fixture_units": sum(t[1].get("fixture_units", 2.0) for t in targets)}
    if system == SystemKind.AIR:
        return {"flow_lps": sum(t[1].get("flow_lps", 25.0) for t in targets)}
    if system == SystemKind.POWER:
        return {"load_w": sum(t[1].get("load_w", 500.0) for t in targets), "wet": False}
    return {"area_m2": sum(t[1].get("area_m2", 16.0) for t in targets)}


def _make_segment(system, seg_id, pts3d, role, ctx) -> Segment:
    """Attach honest sizing (in MILLIMETRES) to every straight run."""
    length = segment_length_m(pts3d)
    diameter_mm = width_mm = height_mm = None
    slope_pct = 0.0
    material = ""
    meta: dict[str, Any] = {"length_m": round(length, 4), "bends": count_bends(pts3d),
                            "role": role}
    if system == SystemKind.DRAINAGE:
        s = size_drainage(ctx.get("fixture_units", 3.0))
        diameter_mm, slope_pct, material = s["diameter_mm"], s["slope_pct"], "PVC-U"
        meta.update(s)
    elif system == SystemKind.WATER:
        s = size_water(ctx.get("fixture_units", 2.0), length)
        diameter_mm, material = s["diameter_mm"], "PEX"
        meta.update(s)
    elif system == SystemKind.AIR:
        s = size_duct(ctx.get("flow_lps", 50.0))
        width_mm, height_mm, material = s["width_mm"], s["height_mm"], "galv. steel"
        meta.update(s)
    elif system == SystemKind.POWER:
        s = size_cable(ctx.get("load_w", 500.0), 230.0)
        diameter_mm, material = s["conduit_diameter_mm"], "Cu/PVC"
        meta.update(s)
        if ctx.get("wet"):
            meta["rcd_required"] = True
    elif system == SystemKind.FIRE:
        s = sprinkler_spacing(ctx.get("area_m2", 16.0))
        diameter_mm, material = s["branch_pipe_diameter_mm"], "CPVC"
        meta.update(s)
    return Segment(
        id=seg_id, system=system, kind=_KIND[system], polyline=list(pts3d),
        diameter_mm=diameter_mm, width_mm=width_mm, height_mm=height_mm,
        slope_pct=slope_pct, material=material, meta=meta,
    )


def _split_into_legs(pts3d) -> list[list[Point3D]]:
    """Split a polyline into straight, non-degenerate 2-point legs so each
    emitted Segment has a tight bounding box (real, precise clashes for M4).
    """
    legs = []
    for a, b in zip(pts3d, pts3d[1:]):
        if math.hypot(b.x - a.x, b.y - a.y) + abs(b.z - a.z) > 1e-6:
            legs.append([a, b])
    return legs or [[pts3d[0], pts3d[-1]]]


def _count_wall_crossings(segments, walls) -> int:
    n = 0
    for seg in segments:
        pts = [_xy(p) for p in seg.polyline]
        for (a, b) in zip(pts, pts[1:]):
            for (c, d) in walls:
                if segments_intersect_xy(a, b, c, d):
                    n += 1
    return n


def _count_intra_crossings(segments) -> int:
    """Crossings between a layer's own segments (a real messiness proxy for
    the 'crossings between systems' score term, which a single layer cannot
    observe directly).
    """
    n = 0
    for i in range(len(segments)):
        pi = [_xy(p) for p in segments[i].polyline]
        for j in range(i + 1, len(segments)):
            pj = [_xy(p) for p in segments[j].polyline]
            for (a, b) in zip(pi, pi[1:]):
                for (c, d) in zip(pj, pj[1:]):
                    if segments_intersect_xy(a, b, c, d):
                        n += 1
    return n


def build_layer(system, model, *, z, elbow, origin_shift, params, avoid_walls,
                strategy_summary, seed, candidate_index, max_bends=4) -> Layer:
    """Build one deterministic candidate Layer for ``system``.

    Never mutates ``model`` (the caller passes a clone). ``z`` is the centreline
    height in METRES, ``origin_shift`` a (dx, dy) perturbation in METRES.
    """
    trunk2d = _engineered_trunk(system, model, origin_shift)
    trunk3d = [Point3D(x=p.x, y=p.y, z=z) for p in trunk2d]
    targets = _targets(system, model)
    trunk_ctx = _trunk_context(system, targets)

    runs = [(trunk3d, "trunk", trunk_ctx)]
    for j, (target, ctx, _dev) in enumerate(targets):
        origin = trunk2d[j % len(trunk2d)]
        pts3d = route_orthogonal(origin, target, z, prefer=elbow, avoid=avoid_walls)
        runs.append((pts3d, "branch", ctx))

    segments: list[Segment] = []
    for ridx, (pts3d, role, ctx) in enumerate(runs):
        for k, leg in enumerate(_split_into_legs(pts3d)):
            seg_id = f"{system.value}-{candidate_index}-{role}-{ridx}-leg{k}"
            segments.append(_make_segment(system, seg_id, leg, role, ctx))

    devices: list[Device] = []
    for j, (target, ctx, dev_info) in enumerate(targets):
        if not dev_info:
            continue
        tag, room = dev_info
        if tag == "diffuser":
            dev = Device(id=f"{system.value}-dev-{j}", system=system,
                         kind="diffuser",
                         at=Point3D(x=target.x, y=target.y, z=z),
                         size=Size3D(length_mm=300.0, width_mm=300.0, height_mm=150.0),
                         meta=size_duct(ctx.get("flow_lps", 50.0)))
        else:
            dev = Device(id=f"{system.value}-dev-{j}", system=system,
                         kind="sprinkler_head",
                         at=Point3D(x=target.x, y=target.y, z=z),
                         size=Size3D(length_mm=60.0, width_mm=60.0, height_mm=60.0),
                         meta=sprinkler_spacing(ctx.get("area_m2", 16.0)))
        devices.append(dev)

    walls = _wall_pairs(avoid_walls)
    metrics = {
        "total_length_m": round(sum(segment_length_m(s.polyline) for s in segments), 4),
        "total_bends": float(sum(count_bends(s.polyline) for s in segments)),
        "wall_crossings": float(_count_wall_crossings(segments, walls)),
        "system_crossings": float(_count_intra_crossings(segments)),
        "z_m": float(z),
        "segment_count": float(len(segments)),
    }
    layer = Layer(
        system=system, agent=f"{system.value}-agent", status="done",
        segments=segments, devices=devices, metrics=metrics,
        notes=[f"deterministic candidate #{candidate_index}; seed={seed}"],
        strategy={
            "candidate_index": candidate_index,
            "trunk": strategy_summary.get("trunk"),
            "stack_location": strategy_summary.get("stack_location"),
            "z_band": list(DEFAULT_BANDS[system]),
            "z": z,
            "elbow": elbow,
            "max_bends": max_bends,
            "seed": seed,
            "params": dict(params or {}),
        },
    )
    return layer
