"""M2 geometry toolkit tests.

Uses the read-only ``sample_model`` fixture from conftest (villa_120). All
sibling modules that may not exist yet are duck-typed via tiny stand-ins
(``_Strategy`` / ``_Limits``); no import of planner/core/agents is needed.
"""

import pytest

from app.contract import (
    SYSTEM_ORDER,
    Device,
    Layer,
    Point3D,
    Segment,
    Size3D,
    SystemKind,
)
from app.geometry import (
    LayoutSpec,
    Toolkit,
    build_candidates,
    count_bends,
    element_bounds,
    get_toolkit,
    score_breakdown,
    score_layer,
    segment_length_m,
    size_cable,
    size_duct,
    size_drainage,
    size_water,
    sprinkler_spacing,
)


class _Limits:
    def __init__(self, cap=6):
        self.max_candidates_per_agent = cap


class _Strategy:
    trunk = "central"
    stack_location = "wet_core"
    z_band = (2.3, 2.6)
    max_bends = 4
    prefer_short_runs = 0.5
    params = {}


# ---------------------------------------------------------------------------
# Independent XY crossing helper for the integration probe (deliberately NOT
# reusing the toolkit's segments_intersect_xy, so it is a genuine check).
# ---------------------------------------------------------------------------

def _orient(p, q, r):
    v = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    if abs(v) < 1e-9:
        return 0
    return 1 if v > 0 else -1


def _on(a, b, p):
    return (min(a[0], b[0]) - 1e-9 <= p[0] <= max(a[0], b[0]) + 1e-9
            and min(a[1], b[1]) - 1e-9 <= p[1] <= max(a[1], b[1]) + 1e-9)


def _xy_cross(a, b, c, d):
    o1, o2, o3, o4 = (_orient(a, b, c), _orient(a, b, d),
                      _orient(c, d, a), _orient(c, d, b))
    if o1 != o2 and o3 != o4:
        return True
    if o1 == 0 and _on(a, b, c):
        return True
    if o2 == 0 and _on(a, b, d):
        return True
    if o3 == 0 and _on(c, d, a):
        return True
    if o4 == 0 and _on(c, d, b):
        return True
    return False


def _legs(polyline):
    pts = [(p.x, p.y) for p in polyline]
    return [(pts[i], pts[i + 1]) for i in range(len(pts) - 1)]


def _hand_segment(points, diameter=20.0):
    return Segment(
        id="s", system=SystemKind.WATER, kind="pipe",
        polyline=[Point3D(x=x, y=y, z=z) for (x, y, z) in points],
        diameter_mm=diameter,
    )


def _hand_layer(seg):
    return Layer(system=SystemKind.WATER, agent="water", segments=[seg],
                 metrics={"wall_crossings": 0.0, "system_crossings": 0.0})


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_get_toolkit_is_lazy_singleton_and_structural():
    t1, t2 = get_toolkit(), get_toolkit()
    assert t1 is t2
    assert isinstance(t1, Toolkit)
    # Structurally implements the frozen Protocol without subclassing it.
    from app.contract.ports import Toolkit as ToolkitProtocol
    assert isinstance(t1, ToolkitProtocol)


def test_determinism_same_seed(sample_model):
    a = list(build_candidates(SystemKind.DRAINAGE, sample_model,
                              _Strategy(), _Limits(7), seed=42))
    b = list(build_candidates(SystemKind.DRAINAGE, sample_model,
                              _Strategy(), _Limits(7), seed=42))
    assert len(a) == len(b) == 7
    assert [l.model_dump() for l in a] == [l.model_dump() for l in b]


def test_different_seed_changes_candidates(sample_model):
    a = list(build_candidates(SystemKind.WATER, sample_model,
                              _Strategy(), _Limits(6), seed=42))
    b = list(build_candidates(SystemKind.WATER, sample_model,
                              _Strategy(), _Limits(6), seed=7))
    assert [l.model_dump() for l in a] != [l.model_dump() for l in b]


def test_candidate_cap_is_respected(sample_model):
    layers = list(build_candidates(SystemKind.AIR, sample_model,
                                   _Strategy(), _Limits(3), seed=1))
    assert len(layers) == 3
    assert all(isinstance(l, Layer) for l in layers)


def test_layout_spec_via_toolkit_candidates(sample_model):
    spec = LayoutSpec(system=SystemKind.POWER, model=sample_model,
                      strategy=_Strategy(), limits=_Limits(4), seed=42,
                      max_candidates=4)
    layers = list(get_toolkit().candidates(spec))
    assert len(layers) == 4
    assert all(l.system == SystemKind.POWER for l in layers)


def test_every_emitted_polyline_has_at_least_two_points(sample_model):
    for sk in SYSTEM_ORDER:
        for layer in build_candidates(sk, sample_model, _Strategy(),
                                      _Limits(3), seed=42):
            assert layer.segments, "each layer must have segments"
            for seg in layer.segments:
                assert len(seg.polyline) >= 2
                assert seg.system == sk


def test_all_sizes_use_unit_suffixed_keys():
    dicts = [
        size_drainage(2), size_drainage(10),
        size_water(3, 5.0), size_duct(100.0),
        size_cable(500.0, 230.0), sprinkler_spacing(16.0),
    ]
    naked = {"diameter", "width", "height", "length", "slope"}
    for d in dicts:
        assert isinstance(d, dict)
        assert any(k.endswith("_mm") for k in d), "every sizing dict needs an _mm key"
        for k in d:
            assert k not in naked, "no naked (unitless) size keys"
            assert isinstance(d[k], (int, float))
    # Drainage slope is 1..2 % as required.
    for fu in (0, 2, 3, 6, 7, 50):
        assert 1.0 <= size_drainage(fu)["slope_pct"] <= 2.0


def test_segment_length_and_bends():
    straight = [Point3D(x=0, y=0, z=2), Point3D(x=3, y=0, z=2)]
    bent = [Point3D(x=0, y=0, z=2), Point3D(x=3, y=0, z=2),
            Point3D(x=3, y=4, z=2)]
    assert segment_length_m(straight) == pytest.approx(3.0)
    assert segment_length_m(bent) == pytest.approx(7.0)
    assert count_bends(straight) == 0
    assert count_bends(bent) == 1


def test_score_in_range_and_prefers_shorter_less_bent():
    short = _hand_layer(_hand_segment([(0, 0, 2.5), (2, 0, 2.5)]))
    long_bent = _hand_layer(_hand_segment(
        [(0, 0, 2.5), (2, 0, 2.5), (2, 2, 2.5), (4, 2, 2.5)]))
    s_short, s_long = score_layer(short), score_layer(long_bent)
    assert 0.0 <= s_short <= 1.0
    assert 0.0 <= s_long <= 1.0
    assert s_short > s_long
    bd = score_breakdown(short)
    assert set(("score", "length", "bends", "wall_crossings", "z_band")) <= set(bd)
    assert 0.0 <= bd["score"] <= 1.0
    # strategy tilt does not break the [0,1] bound
    assert 0.0 <= score_layer(short, strategy=_Strategy()) <= 1.0


def test_element_bounds_for_known_segment_and_device():
    seg = Segment(
        id="s", system=SystemKind.WATER, kind="pipe",
        polyline=[Point3D(x=0, y=0, z=2.7), Point3D(x=2, y=0, z=2.7)],
        diameter_mm=100.0,
    )
    assert element_bounds(seg) == pytest.approx(
        (-0.05, -0.05, 2.65, 2.05, 0.05, 2.75))
    dev = Device(
        id="d", system=SystemKind.FIRE, kind="sprinkler_head",
        at=Point3D(x=1, y=1, z=3.0),
        size=Size3D(length_mm=100.0, width_mm=80.0, height_mm=60.0),
    )
    assert element_bounds(dev) == pytest.approx(
        (0.95, 0.96, 2.97, 1.05, 1.04, 3.03))


def test_model_is_never_mutated(sample_model):
    before = sample_model.model_dump()
    list(build_candidates(SystemKind.DRAINAGE, sample_model,
                          _Strategy(), _Limits(3), seed=42))
    assert sample_model.model_dump() == before


def test_integration_default_layers_have_real_xy_crossings(sample_model):
    """KEY PROBE: the default (first/best) candidate of all five systems
    must produce >= 2 genuine XY segment crossings when merged.
    """
    flat = []
    for sk in SYSTEM_ORDER:
        best = list(build_candidates(sk, sample_model, _Strategy(),
                                     _Limits(6), seed=42))[0]
        for seg in best.segments:
            flat.append((sk.value, _legs(seg.polyline)))

    crossings = 0
    for i in range(len(flat)):
        for j in range(i + 1, len(flat)):
            if flat[i][0] == flat[j][0]:
                continue  # same system, not a merge clash
            for (a, b) in flat[i][1]:
                for (c, d) in flat[j][1]:
                    if _xy_cross(a, b, c, d):
                        crossings += 1
    assert crossings >= 2, f"expected real merge clashes, got {crossings}"


def test_default_z_bands_overlap_not_segregated(sample_model):
    """The five defaults must share overlapping z (a real clash-demo delta),
    not sit in disjoint non-overlapping bands.
    """
    zs = {}
    for sk in SYSTEM_ORDER:
        best = list(build_candidates(sk, sample_model, _Strategy(),
                                     _Limits(1), seed=42))[0]
        zs[sk] = best.metrics["z_m"]
    # drainage and power deliberately coincide; overall spread is < 1 m and
    # the drainage/water/air/power/fire bands overlap by contract.
    assert zs[SystemKind.DRAINAGE] == zs[SystemKind.POWER]
    assert max(zs.values()) - min(zs.values()) < 1.0
