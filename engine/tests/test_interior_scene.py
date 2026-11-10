"""I4 — contract tests for the additive /interior feature.

These tests pin BEHAVIOUR from docs/INTERIOR_MODULE_CONTRACT.md and the FROZEN
shapes in app/interior/contract.py. They deliberately avoid asserting I1's
internal segmentation strategy (column counts, ordering, exact segment list) and
instead test observable geometric facts: real vertical gaps at openings, valid
wall segments, deterministic byte-identical output, room/eye geometry, and the
two read-only GET endpoints.

Fixtures (sample_model / apartment_model / minimal_model / fresh_store) come from
engine/tests/conftest.py and are not redefined here.
"""

import math

import pytest
from fastapi.testclient import TestClient

from app.interior.contract import DISCLAIMER, EYE_HEIGHT_M, InteriorScene
from app.interior.scene import build_scene
from main import app

# Geometric tolerance for centreline / span projections (metres).
GEOM_TOL = 1e-6


# ---------------------------------------------------------------------------
# Helpers: project a plan-space point onto a wall's centreline.
# These work for ANY wall direction (horizontal, vertical, reversed) and do not
# assume a particular start/end ordering, so they don't overfit to the fixture.
# ---------------------------------------------------------------------------

def _wall_geom(wall):
    """Return (length, ux, uy) for a wall's start->end direction."""
    dx = wall.end.x - wall.start.x
    dy = wall.end.y - wall.start.y
    length = math.hypot(dx, dy)
    if length <= GEOM_TOL:
        return 0.0, 0.0, 0.0
    return length, dx / length, dy / length


def _along(wall, pt):
    """Signed distance of pt along the wall centreline, measured from start."""
    _, ux, uy = _wall_geom(wall)
    return (pt.x - wall.start.x) * ux + (pt.y - wall.start.y) * uy


def _offset(wall, pt):
    """Perpendicular distance of pt from the wall centreline."""
    _, ux, uy = _wall_geom(wall)
    return (pt.x - wall.start.x) * (-uy) + (pt.y - wall.start.y) * ux


def _seg_span(wall, seg):
    """(lo, hi) along-span of a WallSegment, order-independent."""
    a = _along(wall, seg.p1)
    b = _along(wall, seg.p2)
    return min(a, b), max(a, b)


def _opening_span(op):
    """(lo, hi) along-span of an opening void."""
    return op.at - op.width / 2.0, op.at + op.width / 2.0


def _segments_overlapping(wall, lo, hi):
    """Solid segments whose along-span meaningfully overlaps [lo, hi]."""
    out = []
    for seg in wall.segments:
        s0, s1 = _seg_span(wall, seg)
        if min(s1, hi) - max(s0, lo) > GEOM_TOL:
            out.append(seg)
    return out


def _intrudes_into_void(seg, sill, head):
    """True if a solid segment's z-range intersects the opening void (sill, head)."""
    return seg.z0 < head - GEOM_TOL and seg.z1 > sill + GEOM_TOL


def _find_wall(scene, wall_id):
    return next(w for w in scene.walls if w.id == wall_id)


def _find_opening(scene, opening_id):
    return next(o for o in scene.openings if o.id == opening_id)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# 1. Scene identity, units, disclaimer, bbox, model_id and counts.
# ---------------------------------------------------------------------------

def test_scene_identity_and_counts(sample_model):
    scene = build_scene(sample_model)

    assert isinstance(scene, InteriorScene)
    assert scene.schema_version == "1.0"
    assert scene.units == "metres"
    assert scene.generated_by == "deterministic"
    assert scene.disclaimer == DISCLAIMER

    assert scene.bbox == sample_model.bbox()
    assert scene.bbox == pytest.approx((0.0, 0.0, 12.0, 10.0))
    assert scene.model_id == sample_model.id

    assert len(scene.walls) == 12
    assert len(scene.openings) == 13
    assert len(scene.rooms) == 9


# ---------------------------------------------------------------------------
# 2. Determinism (byte-identical JSON) and no mutation of the input model.
# ---------------------------------------------------------------------------

def test_determinism_and_no_mutation(sample_model):
    before = sample_model.model_dump()

    s1 = build_scene(sample_model)
    s2 = build_scene(sample_model)

    assert s1.model_dump_json() == s2.model_dump_json()
    assert s1.model_dump_json() == build_scene(sample_model).model_dump_json()

    after = sample_model.model_dump()
    assert before == after


# ---------------------------------------------------------------------------
# 3a. REAL GAPS — window win_s1 on w_south (sill 0.9, head 2.1, wall H=3.0).
# ---------------------------------------------------------------------------

def test_window_win_s1_has_sill_lintel_and_no_solid_in_void(sample_model):
    scene = build_scene(sample_model)
    wall = _find_wall(scene, "w_south")
    win = _find_opening(scene, "win_s1")

    assert win.wall_id == "w_south"
    assert win.kind == "window"

    lo, hi = _opening_span(win)
    segs = _segments_overlapping(wall, lo, hi)
    assert segs, "no solid segments found in the win_s1 x-span"

    # SILL piece: [0.0, sill] = [0.0, 0.9].
    assert any(
        seg.z0 == pytest.approx(0.0) and seg.z1 == pytest.approx(win.sill)
        for seg in segs
    ), "missing sill piece [0.0, 0.9] over the win_s1 span"

    # LINTEL piece: [head, H] = [2.1, 3.0].
    assert any(
        seg.z0 == pytest.approx(win.head) and seg.z1 == pytest.approx(wall.height)
        for seg in segs
    ), "missing lintel piece [2.1, 3.0] over the win_s1 span"

    # NO solid material inside the void [0.9, 2.1] over the opening span.
    for seg in segs:
        assert not _intrudes_into_void(seg, win.sill, win.head), (
            f"segment z=[{seg.z0}, {seg.z1}] intrudes into the win_s1 void "
            f"[{win.sill}, {win.head}]"
        )


# ---------------------------------------------------------------------------
# 3b. REAL GAPS — four doors on w_div_x4 (sill 0.0, head 2.1).
# ---------------------------------------------------------------------------

def test_doors_w_div_x4_have_lintel_and_no_full_height_at_door(sample_model):
    scene = build_scene(sample_model)
    wall = _find_wall(scene, "w_div_x4")
    doors = [o for o in scene.openings if o.wall_id == "w_div_x4"]

    assert len(doors) == 4
    assert all(o.kind == "door" and o.sill == pytest.approx(0.0) for o in doors)

    # At least one segment is a lintel with z0 == head (2.1).
    assert any(seg.z0 == pytest.approx(2.1) for seg in wall.segments)

    for op in doors:
        lo, hi = _opening_span(op)
        segs = _segments_overlapping(wall, lo, hi)

        # A lintel [head, H] exists over the door span ...
        assert any(
            seg.z0 == pytest.approx(op.head) and seg.z1 == pytest.approx(wall.height)
            for seg in segs
        ), f"missing lintel over door {op.id}"

        # ... and NO full-height [0.0, 3.0] segment covers the door span.
        for seg in segs:
            full_height = (
                abs(seg.z0) < GEOM_TOL and abs(seg.z1 - wall.height) < GEOM_TOL
            )
            assert not full_height, (
                f"full-height wall runs across door {op.id} — no real gap was cut"
            )


# ---------------------------------------------------------------------------
# 3c. REAL GAPS — general invariant across ALL 13 openings (doors, passage,
#     windows). For each opening, no solid segment overlapping its x-span may
#     intrude into the void (sill, head). This also covers p_baths (passage).
# ---------------------------------------------------------------------------

def test_all_openings_have_real_vertical_gaps(sample_model):
    scene = build_scene(sample_model)
    assert scene.openings  # sanity: the fixture has openings

    for op in scene.openings:
        wall = _find_wall(scene, op.wall_id)
        lo, hi = _opening_span(op)
        for seg in _segments_overlapping(wall, lo, hi):
            assert not _intrudes_into_void(seg, op.sill, op.head), (
                f"segment z=[{seg.z0}, {seg.z1}] on {wall.id} intrudes into "
                f"opening {op.id} ({op.kind}) void [{op.sill}, {op.head}]"
            )


# ---------------------------------------------------------------------------
# 3d. EVERY WallSegment is geometrically valid across ALL walls.
# ---------------------------------------------------------------------------

def test_every_wall_segment_is_valid(sample_model):
    scene = build_scene(sample_model)
    assert scene.walls

    for wall in scene.walls:
        length, _, _ = _wall_geom(wall)
        for seg in wall.segments:
            assert seg.z0 < seg.z1
            assert seg.z0 >= -GEOM_TOL
            assert seg.z1 <= wall.height + 1e-9

            for p in (seg.p1, seg.p2):
                # Lies ON the centreline ...
                assert abs(_offset(wall, p)) <= GEOM_TOL
                # ... and its distance from start along the direction is in [0, L].
                assert -GEOM_TOL <= _along(wall, p) <= length + GEOM_TOL


# ---------------------------------------------------------------------------
# 4. Rooms: polygon, centroid, eye height, eye == centroid, known kitchen area.
# ---------------------------------------------------------------------------

def test_rooms(sample_model):
    scene = build_scene(sample_model)
    assert scene.rooms

    for room in scene.rooms:
        assert len(room.polygon) >= 3

        xs = [p.x for p in room.polygon]
        ys = [p.y for p in room.polygon]
        assert min(xs) - GEOM_TOL <= room.centroid.x <= max(xs) + GEOM_TOL
        assert min(ys) - GEOM_TOL <= room.centroid.y <= max(ys) + GEOM_TOL

        assert room.eye.z == pytest.approx(EYE_HEIGHT_M)
        assert room.eye.x == pytest.approx(room.centroid.x)
        assert room.eye.y == pytest.approx(room.centroid.y)

        assert room.type == room.type.lower()

    kitchen = next(r for r in scene.rooms if r.id == "kitchen")
    assert kitchen.area_m2 == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# 5. Openings: center.z == (sill + head)/2 and angle_rad matches parent wall.
# ---------------------------------------------------------------------------

def test_openings_center_and_angle(sample_model):
    scene = build_scene(sample_model)

    # Wall angle_rad is atan2(dy, dx) of start -> end (contract §4).
    for wall in scene.walls:
        expected = math.atan2(
            wall.end.y - wall.start.y, wall.end.x - wall.start.x
        )
        assert wall.angle_rad == pytest.approx(expected)

    for op in scene.openings:
        assert op.center.z == pytest.approx((op.sill + op.head) / 2.0)
        wall = _find_wall(scene, op.wall_id)
        assert op.angle_rad == pytest.approx(wall.angle_rad)


# ---------------------------------------------------------------------------
# 6. HTTP endpoints via TestClient(app): bundled sample 200, missing model 404.
#    (The stored-model positive path is intentionally not exercised: save_model
#    on fresh_store is not visible to the app's singleton store, and monkeypatch
#    of the bootstrap singleton is fragile. 200 + 404 are the stable contract.)
# ---------------------------------------------------------------------------

def test_endpoint_scene_default_returns_200_and_counts(client):
    resp = client.get("/api/interior/scene")
    assert resp.status_code == 200

    data = resp.json()
    assert len(data["walls"]) == 12
    assert len(data["openings"]) == 13
    assert len(data["rooms"]) == 9
    assert data["disclaimer"] == DISCLAIMER


def test_endpoint_missing_model_returns_404(client):
    resp = client.get("/api/interior/scene/nope-does-not-exist")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 7. A second (and third) fixture also builds deterministically — proves the
#    projection is not hardcoded to villa_120.json.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fixture_name", ["apartment_model", "minimal_model"])
def test_other_fixtures_build_and_are_deterministic(request, fixture_name):
    model = request.getfixturevalue(fixture_name)

    s1 = build_scene(model)
    s2 = build_scene(model)

    assert isinstance(s1, InteriorScene)
    assert s1.model_id == model.id
    assert s1.model_dump_json() == s2.model_dump_json()
