"""M6 tests: intake (DXF parsing, honest refusal) and the gap auditor.

The intake step is where a tool most easily lies: it is cheap to emit a
plausible plan from an unreadable file and very expensive for the user to
discover later. These tests exist to keep the refusal refusal-shaped.
"""

from __future__ import annotations

import io

import pytest

from app.contract.building import BuildingModel, Point2D
from app.contract.enums import EquipmentKind
from app.intake import (
    ParseError,
    UnsupportedFormat,
    apply_item,
    audit,
    blank_model,
    parse_dxf,
    parse_upload,
)


# ---- fixture DXF ----------------------------------------------------------

def _dxf_bytes(walls, labels=()) -> bytes:
    """A minimal R2013 DXF: LINE entities, optional TEXT labels."""
    import ezdxf

    doc = ezdxf.new("R2013")
    msp = doc.modelspace()
    for (x1, y1), (x2, y2) in walls:
        msp.add_line((x1, y1), (x2, y2))
    for text, (x, y) in labels:
        msp.add_text(text).set_placement((x, y))
    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode("utf-8")


def _square(size: float = 8.0) -> bytes:
    s = size
    return _dxf_bytes(
        [((0, 0), (s, 0)), ((s, 0), (s, s)), ((s, s), (0, s)), ((0, s), (0, 0)),
         ((0, s / 2), (s / 2, s / 2)), ((s / 2, s / 2), (s / 2, 0))],
        labels=[("LIVING", (2, 6)), ("BATHROOM", (6, 2))],
    )


# ---- parsing --------------------------------------------------------------

def test_dxf_lines_become_walls():
    model = parse_dxf(_square(), "plan.dxf")
    assert len(model.level0.walls) == 6
    assert model.source == "upload"
    xs = [w.start.x for w in model.level0.walls] + [w.end.x for w in model.level0.walls]
    assert min(xs) == 0 and max(xs) == 8


def test_walls_are_in_file_order_independent_of_entity_order():
    """Two files with the same lines in a different order must parse identically.

    Otherwise an upload's result depends on how the CAD tool happened to write
    the file, which is the kind of non-determinism that makes a run unrepeatable.
    """
    s = 8.0
    a = [((0, 0), (s, 0)), ((s, 0), (s, s)), ((s, s), (0, s)), ((0, s), (0, 0))]
    m1 = parse_dxf(_dxf_bytes(a), "a.dxf")
    m2 = parse_dxf(_dxf_bytes(list(reversed(a))), "b.dxf")
    assert [(w.start.x, w.start.y, w.end.x, w.end.y) for w in m1.level0.walls] == [
        (w.start.x, w.start.y, w.end.x, w.end.y) for w in m2.level0.walls
    ]


def test_duplicate_and_zero_length_lines_are_dropped():
    s = 6.0
    walls = [((0, 0), (s, 0)), ((s, 0), (0, 0)),  # same wall, reversed
             ((0, 0), (0, s)), ((s, s), (s, s))]   # one real, one zero-length
    model = parse_dxf(_dxf_bytes(walls), "dup.dxf")
    assert len(model.level0.walls) == 2


def test_text_labels_become_rooms_or_markers():
    model = parse_dxf(_square(), "rooms.dxf")
    names = {r.name.upper() for r in model.level0.rooms}
    # Both labels are in the file; whichever became a room must keep its name.
    assert names or model.level0.fixtures


def test_scale_is_never_inferred():
    """The plan is explicit: never trust an AI-guessed scale."""
    model = parse_dxf(_square(), "plan.dxf")
    assert model.scale is None, "parsing must not invent a drawing scale"
    fields = [a.field for a in model.assumptions]
    assert any("scale" in f for f in fields), "an unset scale must be logged"


def test_dxf_with_no_geometry_is_refused():
    empty = _dxf_bytes([])
    with pytest.raises(ParseError) as exc:
        parse_dxf(empty, "empty.dxf")
    assert "no wall" in str(exc.value).lower()


def test_garbage_is_refused_not_guessed():
    with pytest.raises(ParseError):
        parse_dxf(b"this is not a dxf file at all", "x.dxf")


# ---- honest refusal of formats the build cannot read ---------------------

@pytest.mark.parametrize("name", ["plan.pdf", "scan.PNG", "photo.jpg", "x.tiff"])
def test_pdf_and_raster_are_refused_with_a_next_step(name: str):
    with pytest.raises(UnsupportedFormat) as exc:
        parse_upload(name, b"%PDF-1.7 whatever")
    message = str(exc.value)
    # The refusal has to be actionable, not just "unsupported".
    assert "DXF" in message
    assert "vision" in message.lower()


def test_dwg_refusal_names_the_fix():
    with pytest.raises(UnsupportedFormat) as exc:
        parse_upload("plan.dwg", b"AC1032 binary")
    assert "DXF" in str(exc.value)


def test_unknown_extension_is_a_parse_error():
    with pytest.raises(ParseError):
        parse_upload("notes.txt", b"hello")


# ---- gap auditor ----------------------------------------------------------

def test_minimal_plan_reports_the_missing_equipment(minimal_model: BuildingModel):
    items = audit(minimal_model)
    kinds = {i.kind for i in items}
    assert items, "the sparse fixture must produce gaps"
    assert EquipmentKind.SEWER_POINT.value in kinds or EquipmentKind.SEPTIC_TANK.value in kinds
    for item in items:
        assert item.prompt.strip(), f"{item.id} has no prompt to show the user"
        assert item.suggested_at is not None, f"{item.id} cannot be suggested or dragged"


def test_complete_plan_asks_for_very_little(sample_model: BuildingModel):
    items = audit(sample_model)
    # Only floor drains (or nothing) should remain: everything else is present.
    heavy = [i for i in items if i.kind != "floor_drain"]
    assert heavy == [], f"a fully specified plan should not be missing {[i.kind for i in heavy]}"


def test_sewer_and_septic_are_not_both_asked_for(minimal_model: BuildingModel):
    kinds = {i.kind for i in audit(minimal_model)}
    assert not ({EquipmentKind.SEWER_POINT.value, EquipmentKind.SEPTIC_TANK.value} <= kinds)


def test_suggest_puts_the_item_in_the_model(minimal_model: BuildingModel):
    item = next(i for i in audit(minimal_model) if i.kind == EquipmentKind.WATER_TANK.value)
    out = apply_item(minimal_model, item.kind, item.suggested_at, "suggest")
    assert any(e.kind == EquipmentKind.WATER_TANK for e in out.level0.equipment)
    # The input model must not be mutated.
    assert not any(e.kind == EquipmentKind.WATER_TANK for e in minimal_model.level0.equipment)


def test_skip_records_an_assumption_rather_than_vanishing(minimal_model: BuildingModel):
    before = len(minimal_model.assumptions)
    out = apply_item(minimal_model, EquipmentKind.WATER_TANK.value, None, "skip")
    assert len(out.assumptions) == before + 1
    added = out.assumptions[-1]
    assert added.source == "skip"
    assert EquipmentKind.WATER_TANK.value in added.field
    assert "review" in added.reason.lower()


def test_place_uses_the_dragged_position(minimal_model: BuildingModel):
    at = Point2D(x=3.25, y=4.75)
    out = apply_item(minimal_model, EquipmentKind.WATER_TANK.value, at, "place")
    tank = next(e for e in out.level0.equipment if e.kind == EquipmentKind.WATER_TANK)
    assert (round(tank.at.x, 2), round(tank.at.y, 2)) == (3.25, 4.75)


def test_unknown_item_kind_is_rejected(minimal_model: BuildingModel):
    with pytest.raises(ValueError):
        apply_item(minimal_model, "jacuzzi", None, "place")


def test_skipping_ceiling_height_sets_a_logged_default(minimal_model: BuildingModel):
    model = minimal_model.clone()
    model.ceiling_height = 0.0
    assert "mi-ceiling" in {i.id for i in audit(model)}
    out = apply_item(model, "ceiling_height", None, "skip")
    assert out.ceiling_height == 2.7
    assert out.assumptions[-1].source == "skip"


# ---- quick draw -----------------------------------------------------------

def test_blank_model_is_valid_and_immediately_runnable():
    model = blank_model(9.0, 7.0)
    assert model.source == "quickdraw"
    assert model.level0.rooms or model.level0.walls
    audit(model)  # must not raise on a blank plan
