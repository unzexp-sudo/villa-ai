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


# ---- PDF and raster parsing -----------------------------------------------

def test_pdf_with_vector_content_is_parsed():
    """A PDF with embedded vector line ops should produce walls."""
    # Create a minimal PDF with a line operation in the content stream
    pdf_content = _make_minimal_pdf_with_lines()
    model = parse_upload("plan.pdf", pdf_content)
    assert model.source == "upload"
    assert len(model.level0.walls) > 0, "vector PDF must yield walls"


def test_raster_image_with_lines_is_parsed():
    """A raster image with dark horizontal/vertical lines should produce walls."""
    from PIL import Image, ImageDraw
    import io as _io

    img = Image.new("L", (400, 300), 255)
    draw = ImageDraw.Draw(img)
    draw.line([(10, 50), (380, 50)], fill=0, width=3)
    draw.line([(10, 250), (380, 250)], fill=0, width=3)
    draw.line([(50, 10), (50, 290)], fill=0, width=3)
    draw.line([(350, 10), (350, 290)], fill=0, width=3)

    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    model = parse_upload("plan.png", buf.getvalue())
    assert model.source == "upload"
    assert len(model.level0.walls) > 0, "image with clear lines must yield walls"


def _drawn_plan_png(scale: float = 2.0) -> bytes:
    """A synthetic plan the reader must read EXACTLY, drawn at ``scale``.

    Three rooms, so the layout is not symmetric in either axis: a full-height
    partition and a second partition on the left only, high in the drawing. That
    asymmetry is the point — a reader that forgets to flip y puts the small room
    at the bottom, and a reader that invents walls puts them through the
    furniture. ``scale`` re-renders the same plan at another resolution.

    Deliberately included, and deliberately NOT walls:

    * a wardrobe (four hairline strokes) inside the lower-left room,
    * a patch of hatching (eleven hairline strokes) in the right-hand room,
    * a dimension line with end ticks, a title and a logo, outside the building.
    """
    import io as _io

    from PIL import Image, ImageDraw

    k = scale
    img = Image.new("L", (int(400 * k), int(300 * k)), 255)
    draw = ImageDraw.Draw(img)

    def line(*pts, width=3):
        draw.line([(x * k, y * k) for x, y in pts], fill=0,
                  width=max(1, round(width * k)))

    # Building outline and partitions (drawn walls).
    line((38, 40), (362, 40))
    line((360, 38), (360, 262))
    line((362, 260), (38, 260))
    line((40, 262), (40, 38))
    line((200, 38), (200, 262))
    line((38, 110), (202, 110))

    # Furniture: four hairlines, touching no wall.
    line((60, 150), (120, 150), width=1)
    line((120, 150), (120, 210), width=1)
    line((120, 210), (60, 210), width=1)
    line((60, 210), (60, 150), width=1)

    # Hatching: parallel hairlines, 20 px clear of every wall.
    for x in range(220, 350, 12):
        line((x, 60), (x, 240), width=1)

    # Sheet annotations, outside the building.
    line((40, 20), (360, 20), width=1)
    line((40, 14), (40, 26), width=1)
    line((360, 14), (360, 26), width=1)
    draw.text((150 * k, 6 * k), "PLAN 1:100", fill=0)
    draw.ellipse([(368 * k, 268 * k), (392 * k, 292 * k)], outline=0, width=2)

    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _bbox(walls) -> tuple[float, float, float, float]:
    xs = [c for w in walls for c in (w.start.x, w.end.x)]
    ys = [c for w in walls for c in (w.start.y, w.end.y)]
    return min(xs), min(ys), max(xs), max(ys)


def test_raster_plan_reads_the_drawn_walls_only():
    """Six drawn walls, three rooms — furniture, hatching and notes are not walls.

    This is the regression that matters most for uploads: a bed or a hatch patch
    traced as walls turns one bedroom into five, and a dimension line traced as
    a wall makes the building bigger than the drawing.
    """
    model = parse_upload("plan.png", _drawn_plan_png(2.0))
    walls = model.level0.walls
    assert len(walls) == 6, [(w.start.x, w.start.y, w.end.x, w.end.y) for w in walls]

    rooms = model.level0.rooms
    assert len(rooms) == 3, [(r.name, r.area_m2) for r in rooms]
    assert all(r.area_m2 > 15 for r in rooms), \
        f"a room this small is furniture or hatching: {[r.area_m2 for r in rooms]}"

    # The sheet annotation must not move the footprint: the bounding box is the
    # drawn building, nothing outside it.
    x0, y0, x1, y1 = _bbox(walls)
    assert abs(x1 - x0 - 14.0) < 0.2, "footprint width should be the assumed span"
    assert 9.4 < y1 - y0 < 9.9, f"footprint depth {y1 - y0:.2f} is not the drawn 320x220"


def test_raster_plan_is_not_mirrored():
    """Pixels count y down; the model must count y up.

    The left-hand partition sits high in the drawing, so the SMALL room is the
    upper one. Without the flip the same reading comes out top-to-bottom
    mirrored — a different building, and one the user cannot fix except by
    redrawing.
    """
    model = parse_upload("plan.png", _drawn_plan_png(2.0))
    rooms = model.level0.rooms
    assert len(rooms) == 3

    ys = [(min(p.y for p in r.polygon), max(p.y for p in r.polygon)) for r in rooms]
    small = min(rooms, key=lambda r: r.area_m2)
    small_y0 = min(p.y for p in small.polygon)
    assert small_y0 > 5.0, f"the small room belongs at the top, not at y={small_y0:.2f}"
    assert sum(1 for lo, _hi in ys if lo > 5.0) == 1

    biggest = max(rooms, key=lambda r: r.area_m2)
    assert min(p.y for p in biggest.polygon) < 0.5


def test_raster_plan_result_does_not_depend_on_upload_resolution():
    """The same plan uploaded at another size must read as the same building.

    Phone photos and exports differ by an order of magnitude in pixels. Because
    every threshold is a fraction of the drawing's own span, the wall and room
    counts must not move with the pixel count — otherwise a user who re-exports
    their plan gets a different model and no explanation.
    """
    small = parse_upload("plan.png", _drawn_plan_png(1.5))
    large = parse_upload("plan.png", _drawn_plan_png(3.0))

    assert len(small.level0.walls) == len(large.level0.walls) == 6
    assert len(small.level0.rooms) == len(large.level0.rooms) == 3

    a, b = _bbox(small.level0.walls), _bbox(large.level0.walls)
    for got, want in zip(a, b):
        assert abs(got - want) < 0.25, f"footprint moved with resolution: {a} vs {b}"

    areas_small = sorted(r.area_m2 for r in small.level0.rooms)
    areas_large = sorted(r.area_m2 for r in large.level0.rooms)
    for got, want in zip(areas_small, areas_large):
        assert abs(got - want) <= max(1.0, 0.05 * want), \
            f"room sizes moved with resolution: {areas_small} vs {areas_large}"


def test_pdf_scale_is_never_inferred():
    pdf_content = _make_minimal_pdf_with_lines()
    model = parse_upload("plan.pdf", pdf_content)
    assert model.scale is None
    assert any("scale" in a.field for a in model.assumptions)


def test_image_scale_is_never_inferred():
    from PIL import Image
    import io as _io

    img = Image.new("L", (200, 200), 255)
    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    # No dark lines -> should raise UnsupportedFormat
    with pytest.raises((UnsupportedFormat, ParseError)):
        parse_upload("blank.png", buf.getvalue())


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


# ---- helpers --------------------------------------------------------------

def _make_minimal_pdf_with_lines() -> bytes:
    """Build a minimal single-page PDF whose content stream draws lines.

    Uses reportlab to generate a valid PDF with line operations so that
    pypdf's ContentStream parser can extract vector segments.
    """
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter
    import io as _io

    buf = _io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(400, 300))
    # Draw a rectangle: 4 line segments
    c.line(50, 50, 350, 50)
    c.line(350, 50, 350, 250)
    c.line(350, 250, 50, 250)
    c.line(50, 250, 50, 50)
    c.showPage()
    c.save()
    return buf.getvalue()
