"""Floor-plan parsing: DXF for exact geometry, PDF via vector extraction,
raster images via edge detection.

DXF is the gold path: ``ezdxf`` gives exact geometry, so walls, rooms and
openings come out of the file rather than out of a guess. PDF is parsed by
extracting vector operations (moveto/lineto/curveto) from the content stream;
if the PDF is a scanned raster, Pillow + numpy edge detection finds long
straight lines. Raster images (PNG/JPG) use the same edge-detection path.

None of these paths use a vision model. They extract real line geometry —
vector ops from the PDF content stream, or pixel runs from a raster image.
Scale is NEVER inferred. ``calibrate_scale`` is the only thing that sets it.
"""

from __future__ import annotations

import io
import math
import re
import time
import uuid

try:
    import numpy as np
except ImportError:  # pragma: no cover - dependency is declared
    np = None  # type: ignore[assignment]

from app.contract.building import (
    Assumption,
    BuildingModel,
    Fixture,
    Level,
    Opening,
    Point2D,
    Room,
    Scale,
    Wall,
)
from app.contract.enums import BuildingType, Country, FixtureKind, RoomType, SystemKind


class ParseError(ValueError):
    """The file arrived but could not be read into a plan."""


class UnsupportedFormat(ParseError):
    """The format needs a capability this build does not have yet."""


# Room labels we can recognise in a DXF. Ambiguity is resolved by NOT guessing:
# an unrecognised label becomes RoomType.OTHER and shows up in the confirmation
# screen for the user to correct.
_LABEL_MAP: dict[str, RoomType] = {
    "bedroom": RoomType.BEDROOM,
    "bed": RoomType.BEDROOM,
    "master": RoomType.BEDROOM,
    "br": RoomType.BEDROOM,
    "bath": RoomType.BATHROOM,
    "bathroom": RoomType.BATHROOM,
    "wc": RoomType.BATHROOM,
    "toilet": RoomType.BATHROOM,
    "lavatory": RoomType.BATHROOM,
    "powder": RoomType.BATHROOM,
    "shower": RoomType.BATHROOM,
    "kitchen": RoomType.KITCHEN,
    "kit": RoomType.KITCHEN,
    "living": RoomType.LIVING,
    "lounge": RoomType.LIVING,
    "family": RoomType.LIVING,
    "dining": RoomType.DINING,
    "utility": RoomType.UTILITY,
    "laundry": RoomType.UTILITY,
    "wash": RoomType.UTILITY,
    "store": RoomType.UTILITY,
    "hall": RoomType.HALL,
    "corridor": RoomType.HALL,
    "balcony": RoomType.BALCONY,
    "terrace": RoomType.BALCONY,
    "garage": RoomType.GARAGE,
    "carport": RoomType.GARAGE,
    "room": RoomType.OTHER,
}

_FIXTURE_HINTS = {
    "WC": FixtureKind.WC,
    "TOILET": FixtureKind.WC,
    "LAVATORY": FixtureKind.BASIN,
    "BASIN": FixtureKind.BASIN,
    "SINK": FixtureKind.SINK,
    "TAP": FixtureKind.BASIN,
    "FAUCET": FixtureKind.BASIN,
    "MIXER": FixtureKind.BASIN,
    "SHOWER": FixtureKind.SHOWER,
    "BATH": FixtureKind.BATH,
    "WASHING": FixtureKind.WASHING_MACHINE,
    "LAUNDRY": FixtureKind.WASHING_MACHINE,
    "DRAIN": FixtureKind.FLOOR_DRAIN,
    "FLOOR DRAIN": FixtureKind.FLOOR_DRAIN,
}

_FIXTURE_UNITS: dict[FixtureKind, float] = {
    FixtureKind.WC: 3.0,
    FixtureKind.BASIN: 1.5,
    FixtureKind.SHOWER: 2.0,
    FixtureKind.BATH: 3.0,
    FixtureKind.SINK: 1.5,
    FixtureKind.WASHING_MACHINE: 1.5,
    FixtureKind.FLOOR_DRAIN: 1.0,
    FixtureKind.KITCHEN_SINK: 1.5,
}

# A floor plan has more than a handful of wall lines. Below this the parse is
# noise, not a plan, and we say so instead of returning an empty model. Four is
# the floor because a single rectangular room is four walls — and unlike the
# old reader, this one no longer counts each pen stroke three times, so the
# guard measures real walls rather than duplicates.
_MIN_RASTER_WALLS = 4

# Assumed longest dimension of a single-floor villa footprint, in metres.
# Used ONLY to bring raster pixel coordinates into a plausible metre range so
# sizing and 3D framing behave. The user calibrates the real scale after.
_ASSUMED_BUILDING_SPAN_M = 14.0

# Furniture drawn as a closed box (a bed, a sofa) is also an enclosed region.
# The minimum side length, relative to the drawing's longest extent, separates
# a room from a tiny furniture box. A bed is small; a bedroom is not.
_MIN_ROOM_SIDE_FRACTION = 0.055

# A region nested inside another region is furniture, not a room, when it
# occupies less than this fraction of the outer region.
_MAX_NESTED_AREA_RATIO = 0.6

# Nothing narrower than this is a room: it is the cavity between the two faces
# of a wall, or the gap between a wall and a hatch. Measured on the SHORT side
# of the region, as a fraction of the drawing's longest extent, so it scales
# with the drawing (a 0.9 m corridor is about 0.06 of a 14 m villa).
_MIN_ROOM_SHORT_SIDE_FRACTION = 0.045

# ...AND when it is drawn with a thinner pen than the walls are. A plan strokes
# walls heavily and furniture lightly; a bathroom that merely sits inside
# another region's bounding box keeps the wall weight and is not furniture.
_FURNITURE_STROKE_RATIO = 0.6

# A line drawn with less than this share of the walls' ink is a hairline: a
# hatch stroke, a chair back, a door leaf. Hairlines are only dropped inside a
# room, where no wall can be, since a real wall is drawn with the wall pen.
_HAIRLINE_STROKE_RATIO = 0.5

# A plan SHEET carries ink that is not the building: dimension lines, the
# room titles, the title block, the plan-library logo. Tracing that ink as
# walls is what used to make a raster read look like a different building.
# The building's line work is one large connected ink blob; annotations are
# thin, isolated blobs. A blob is kept when it carries at least this share of
# the biggest blob's ink and sits within this margin of the building's box.
_STRUCTURE_MIN_SHARE = 0.06
_STRUCTURE_MARGIN_FRACTION = 0.03

# Two parallel runs belong to the SAME wall when they are this close: a wall is
# drawn with a pen, and often as a pair of faces, so one wall lands on one
# centreline. Measured as a fraction of the drawing's longest extent.
_WALL_BAND_FRACTION = 0.012

# Analysis resolution. A phone photo of a plan can be 4000 px wide; the
# connected-component and flood-fill passes are pure Python, so they are run on
# a capped copy and the result is scaled back to the original pixels.
_MAX_ANALYSIS_PX = 1600


def _now() -> float:
    return time.time()


def _assume(field: str, value: str, reason: str, source: str = "default") -> Assumption:
    return Assumption(id="as-" + uuid.uuid4().hex[:8], field=field, value=value,
                      reason=reason, source=source, created_at=_now())


def _classify(label: str) -> RoomType:
    low = label.strip().lower()
    for key, kind in _LABEL_MAP.items():
        if key in low:
            return kind
    return RoomType.OTHER


def _fixture_kind(label: str) -> FixtureKind | None:
    up = label.upper()
    for key, kind in _FIXTURE_HINTS.items():
        if key in up:
            return kind
    return None


def _polygon_area(pts: list[Point2D]) -> float:
    a = 0.0
    for p, q in zip(pts, pts[1:] + pts[:1]):
        a += p.x * q.y - q.x * p.y
    return abs(a) / 2.0


def parse_upload(filename: str, blob: bytes) -> BuildingModel:
    """Parse an upload. DXF is read directly; PDF is rasterised and vectorised;
    raster images (PNG/JPG) are vectorised. DWG is refused."""
    lower = (filename or "").lower()
    if lower.endswith(".dxf"):
        return parse_dxf(blob, name=filename)
    if lower.endswith(".pdf"):
        return parse_pdf(blob, name=filename)
    if lower.endswith((".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff")):
        return parse_image(blob, name=filename)
    if lower.endswith(".dwg"):
        raise UnsupportedFormat(
            "DWG needs a conversion service. Save as DXF (R2013 or earlier) and upload that."
        )
    raise ParseError(f"unsupported file type: {filename!r}. Accepted: DXF, PDF, PNG, JPG.")


def parse_dxf(blob: bytes, name: str = "plan.dxf") -> BuildingModel:
    """Build a model from DXF LINE / LWPOLYLINE / INSERT geometry and TEXT labels."""
    try:
        import ezdxf
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise ParseError("ezdxf is not installed, so DXF cannot be read") from exc

    try:
        doc = ezdxf.read(io.StringIO(blob.decode("utf-8", errors="replace")))
    except Exception as exc:
        raise ParseError(f"DXF could not be opened: {exc}") from exc

    msp = doc.modelspace()
    walls: list[Wall] = []
    raw_segments: list[tuple[Point2D, Point2D]] = []

    for e in msp:
        kind = e.dxftype()
        try:
            if kind == "LINE":
                s = e.dxf.start
                t = e.dxf.end
                raw_segments.append((Point2D(x=float(s.x), y=float(s.y)),
                                     Point2D(x=float(t.x), y=float(t.y))))
            elif kind == "LWPOLYLINE":
                pts = [Point2D(x=float(p[0]), y=float(p[1])) for p in e.get_points("xy")]
                for a, b in zip(pts, pts[1:]):
                    raw_segments.append((a, b))
        except Exception:
            continue

    # Deterministic order: geometry order in the file is a drawing artefact.
    raw_segments.sort(key=lambda ab: (round(ab[0].x, 4), round(ab[0].y, 4),
                                      round(ab[1].x, 4), round(ab[1].y, 4)))

    seen: set[tuple] = set()
    for a, b in raw_segments:
        if math.dist((a.x, a.y), (b.x, b.y)) < 0.05:
            continue
        key = (round(a.x, 3), round(a.y, 3), round(b.x, 3), round(b.y, 3))
        rev = (key[2], key[3], key[0], key[1])
        if key in seen or rev in seen:
            continue
        seen.add(key)
        walls.append(Wall(id=f"w{len(walls) + 1}", start=a, end=b, thickness=0.15,
                          height=3.0, is_exterior=False))

    if not walls:
        raise ParseError(
            "no wall lines found. Draw walls as LINE or LWPOLYLINE entities in model "
            "space and export the DXF again."
        )

    # Room labels -> rooms. A label with no closed boundary stays a marker the
    # user places in the confirmation screen; we do not fabricate a polygon.
    rooms: list[Room] = []
    fixtures: list[Fixture] = []
    for e in msp:
        if e.dxftype() not in ("TEXT", "MTEXT"):
            continue
        try:
            text = e.plain_text() if e.dxftype() == "MTEXT" else e.dxf.text
            pos = e.dxf.insert
        except Exception:
            continue
        label = re.sub(r"\s+", " ", str(text or "")).strip()
        if not label:
            continue
        fk = _fixture_kind(label)
        if fk is not None:
            fixtures.append(Fixture(id=f"f{len(fixtures) + 1}", kind=fk,
                                    at=Point2D(x=float(pos.x), y=float(pos.y))))
            continue
        rooms.append(Room(id=f"r{len(rooms) + 1}", name=label, type=_classify(label),
                          polygon=[Point2D(x=float(pos.x) - 0.5, y=float(pos.y) - 0.5),
                                   Point2D(x=float(pos.x) + 0.5, y=float(pos.y) - 0.5),
                                   Point2D(x=float(pos.x) + 0.5, y=float(pos.y) + 0.5),
                                   Point2D(x=float(pos.x) - 0.5, y=float(pos.y) + 0.5)],
                          wet=_classify(label) in (RoomType.BATHROOM, RoomType.KITCHEN,
                                                   RoomType.UTILITY)))

    xs = [c for w in walls for c in (w.start.x, w.end.x)]
    ys = [c for w in walls for c in (w.start.y, w.end.y)]
    ox, oy = min(xs), min(ys)
    for w in walls:  # normalise the origin to the plan's lower-left corner
        w.start = Point2D(x=w.start.x - ox, y=w.start.y - oy)
        w.end = Point2D(x=w.end.x - ox, y=w.end.y - oy)
    for r in rooms:
        r.polygon = [Point2D(x=p.x - ox, y=p.y - oy) for p in r.polygon]
        r.area_m2 = round(_polygon_area(r.polygon), 2)
    for f in fixtures:
        f.at = Point2D(x=f.at.x - ox, y=f.at.y - oy)

    level = Level(id="L0", name="Ground floor", walls=walls, rooms=rooms,
                  fixtures=fixtures, openings=[])
    model = BuildingModel(
        id="mdl-" + uuid.uuid4().hex[:10],
        name=f"{name} (parsed from DXF)",
        building_type=BuildingType.VILLA,
        country=Country.ID,
        storeys=1,
        ceiling_height=2.7,
        levels=[level],
        scale=None,  # user must calibrate: two points and a real distance
        source="upload",
        created_at=_now(),
        updated_at=_now(),
        assumptions=[
            _assume("scale", "unset", "DXF units are taken as metres; the user must "
                                     "calibrate two points before design runs"),
            _assume("building_type", "villa", "not stated in the file"),
            _assume("ceiling_height", "2.7", "not stated in the file"),
            _assume("wall_thickness", "0.15", "DXF lines carry no thickness"),
        ],
    )
    return model


def _build_model_from_segments(walls: list[Wall], name: str,
                                source: str = "upload",
                                unit_note: str | None = None,
                                room_regions: list[list[tuple[int, int]]] | None = None,
                                image_blob: bytes | None = None,
                                source_height: float | None = None,
                                extra_assumptions: list[Assumption] | None = None,
                                ) -> BuildingModel:
    """Assemble a BuildingModel from a wall list, normalising the origin.

    When the walls came from a raster (pixels) the coordinates are scaled to
    a plausible building footprint in metres. A 700 px wide plan is roughly
    14 m wide, so the factor is derived from the image extent and recorded
    as an explicit assumption. The user still calibrates for real.

    ``source_height`` is the pixel height of the image the segments were read
    from. Pass it for raster input: pixels count y DOWN, while model space is
    y UP from the lower-left, and without the flip the whole plan comes out
    mirrored top-to-bottom.
    """
    if not walls:
        raise ParseError(
            "no wall lines found. The plan may be a raster image with no vector "
            "content, or the drawing may use polylines the rasteriser could not trace."
        )

    ocr_regions = room_regions
    if source_height is not None:
        for w in walls:
            w.start = Point2D(x=w.start.x, y=source_height - w.start.y)
            w.end = Point2D(x=w.end.x, y=source_height - w.end.y)
        if room_regions:
            room_regions = [[(px, source_height - py) for px, py in region]
                            for region in room_regions]

    xs = [c for w in walls for c in (w.start.x, w.end.x)]
    ys = [c for w in walls for c in (w.start.y, w.end.y)]
    ox, oy = min(xs), min(ys)
    for w in walls:
        w.start = Point2D(x=w.start.x - ox, y=w.start.y - oy)
        w.end = Point2D(x=w.end.x - ox, y=w.end.y - oy)

    width = max(xs) - ox
    depth = max(ys) - oy
    factor = 1.0
    if unit_note is not None:
        # Assume the drawn extent is a single villa footprint. A 700 px plan
        # that spans 45'-8" is about 14 m, so this lands close to reality and
        # is then corrected by the user's two-point calibration.
        longest = max(width, depth)
        if longest > 0:
            factor = _ASSUMED_BUILDING_SPAN_M / longest
        for w in walls:
            w.start = Point2D(x=w.start.x * factor, y=w.start.y * factor)
            w.end = Point2D(x=w.end.x * factor, y=w.end.y * factor)

    rooms: list[Room] = []
    fixtures: list[Fixture] = []
    if room_regions:
        labels: list[str] | None = None
        if image_blob and ocr_regions:
            try:
                from PIL import Image
                img = Image.open(io.BytesIO(image_blob))
                # Crop from the un-flipped regions: the image on disk has the
                # same y-down origin the pixels were read with.
                labels = _ocr_room_names(img, ocr_regions)
            except Exception:
                labels = None

        for index, region in enumerate(room_regions):
            poly = [Point2D(x=(px - ox) * factor, y=(py - oy) * factor)
                    for px, py in region]
            area = round(_polygon_area(poly), 2)
            if area <= 0:
                continue
            # No OCR text means we do not know what the room is called. Number
            # it instead of returning nine rooms all named "Room", so the
            # confirmation screen can at least talk about them one at a time.
            room_name = f"Room {len(rooms) + 1}"
            room_kind = RoomType.OTHER
            if labels is not None and index < len(labels) and labels[index]:
                room_name = labels[index]
                room_kind = _classify(room_name)
            room = Room(id=f"r{len(rooms) + 1}", name=room_name, type=room_kind,
                        polygon=poly, area_m2=area,
                        wet=room_kind in (RoomType.BATHROOM, RoomType.KITCHEN, RoomType.UTILITY))
            rooms.append(room)

            for fixture_kind, props in _infer_fixtures_for_label(room_name):
                cx = sum(p.x for p in poly) / len(poly)
                cy = sum(p.y for p in poly) / len(poly)
                fixtures.append(Fixture(
                    id=f"f{len(fixtures) + 1}",
                    kind=fixture_kind,
                    at=Point2D(x=cx, y=cy),
                    room_id=room.id,
                    props=dict(props),
                ))

    level = Level(id="L0", name="Ground floor", walls=walls, rooms=rooms,
                  fixtures=fixtures, openings=[])
    return BuildingModel(
        id="mdl-" + uuid.uuid4().hex[:10],
        name=name,
        building_type=BuildingType.VILLA,
        country=Country.ID,
        storeys=1,
        ceiling_height=2.7,
        levels=[level],
        scale=None,
        source=source,
        created_at=_now(),
        updated_at=_now(),
        assumptions=[
            _assume("scale", "unset", unit_note or
                    "Drawing units are taken as metres; the user must calibrate "
                    "two points before design runs"),
            _assume("building_type", "villa", "not stated in the file"),
            _assume("ceiling_height", "2.7", "not stated in the file"),
            _assume("wall_thickness", "0.15", "not stated in the file; traced lines have no width"),
            *(extra_assumptions or []),
        ],
    )


def _detect_line_segments_from_vector_ops(ops) -> list[tuple[Point2D, Point2D]]:
    """Extract line segments from PDF vector operations.

    pypdf's ContentStream.operations yields (operands, operator) tuples,
    e.g. ([50, 50], b'm') for moveto, ([350, 50], b'l') for lineto.
    """
    segments: list[tuple[Point2D, Point2D]] = []
    current = Point2D(x=0, y=0)
    path_start = Point2D(x=0, y=0)

    for operands, operator in ops:
        if not isinstance(operator, bytes):
            continue
        if operator == b"m":  # moveto
            if len(operands) >= 2:
                current = Point2D(x=float(operands[0]), y=float(operands[1]))
                path_start = Point2D(x=current.x, y=current.y)
        elif operator == b"l":  # lineto
            if len(operands) >= 2:
                end = Point2D(x=float(operands[0]), y=float(operands[1]))
                segments.append((current, end))
                current = end
        elif operator == b"c":  # curveto — approximate as a line from start to end
            if len(operands) >= 6:
                end = Point2D(x=float(operands[4]), y=float(operands[5]))
                segments.append((current, end))
                current = end
        elif operator == b"h":  # closepath
            if math.dist((current.x, current.y), (path_start.x, path_start.y)) > 0.5:
                segments.append((current, path_start))
            current = Point2D(x=path_start.x, y=path_start.y)

    return segments


def parse_pdf(blob: bytes, name: str = "plan.pdf") -> BuildingModel:
    """Rasterise a PDF page and vectorise wall lines from its content stream.

    This uses pypdf to extract vector operations (moveto/lineto/curveto) from
    the page content stream. It does NOT use a vision model — it reads the
    actual vector geometry embedded in the PDF, which is how most CAD-to-PDF
    exports store walls.

    A PDF with only a raster image (scanned plan) will produce no vector ops;
    in that case we fall back to pixel-based rasterisation with Pillow and
    edge detection. That path is still better than refusing, because it extracts
    real line geometry rather than guessing walls.
    """
    try:
        import io as _io
        from pypdf import PdfReader
        from pypdf.errors import PdfStreamError
    except ImportError as exc:
        raise ParseError("pypdf is not installed, so PDF cannot be read") from exc

    try:
        reader = PdfReader(_io.BytesIO(blob))
    except (PdfStreamError, Exception) as exc:
        raise UnsupportedFormat(
            "This PDF could not be opened. It may be corrupted or not a real PDF. "
            "Export a DXF from your CAD tool, or trace the walls in Quick draw."
        ) from exc
    if not reader.pages:
        raise ParseError("the PDF has no pages")

    page = reader.pages[0]

    # Try vector extraction first: parse the content stream for line ops
    walls: list[Wall] = []
    raw_segments: list[tuple[Point2D, Point2D]] = []

    try:
        from pypdf.generic import ContentStream
        contents = page["/Contents"]
        if hasattr(contents, "get_object"):
            contents = contents.get_object()
        cs = ContentStream(contents, reader)
        ops = list(cs.operations)
        raw_segments = _detect_line_segments_from_vector_ops(ops)
    except Exception:
        pass  # fall through to raster path

    if raw_segments:
        # Deduplicate and filter short segments, same as DXF
        raw_segments.sort(key=lambda ab: (round(ab[0].x, 2), round(ab[0].y, 2),
                                          round(ab[1].x, 2), round(ab[1].y, 2)))
        seen: set[tuple] = set()
        for a, b in raw_segments:
            if math.dist((a.x, a.y), (b.x, b.y)) < 2.0:
                continue
            key = (round(a.x, 2), round(a.y, 2), round(b.x, 2), round(b.y, 2))
            rev = (key[2], key[3], key[0], key[1])
            if key in seen or rev in seen:
                continue
            seen.add(key)
            walls.append(Wall(id=f"w{len(walls)+1}", start=a, end=b,
                              thickness=0.15, height=3.0, is_exterior=False))

    raster_height: float | None = None
    if not walls:
        # No vector content — try rasterising the page and detecting edges
        walls, raster_height = _rasterise_and_detect_lines(page, reader)

    if not walls:
        raise UnsupportedFormat(
            "This PDF contains no vector line geometry and no detectable raster "
            "edges. If it is a scanned image, trace the walls in Quick draw, or "
            "export a DXF from your CAD tool."
        )

    return _build_model_from_segments(
        walls, f"{name} (parsed from PDF)",
        unit_note=None if raster_height is None
        else "scanned page pixels converted to metres using an assumed scale",
        source_height=raster_height,
    )


def parse_image(blob: bytes, name: str = "plan.png") -> BuildingModel:
    """Detect wall lines from a raster image using edge detection.

    Uses Pillow + numpy. The threshold is chosen from the image's own
    histogram (Otsu) because architectural plans are thin GREY line work on
    white paper; a fixed cut finds the room names and misses the walls.
    Detected runs are merged along their line so each wall is one segment.

    Two things this reader must get right, because getting them wrong is what
    makes the read look like a DIFFERENT building:

    * Sheet annotations are not walls. Dimension lines, room titles and the
      plan-library logo are dropped before tracing (see ``_structure_mask``).
    * Pixels count y DOWN and model space counts y UP. The flip happens in
      ``_build_model_from_segments`` via ``source_height``; without it the plan
      comes out mirrored top-to-bottom.

    The output is real line segments in PIXEL coordinates. They are converted
    to a plausible building footprint in metres by an assumed scale, which the
    user corrects by calibrating two points. Treating pixels as metres would
    make a 700 px plan a 700 m building, which breaks every downstream size
    and every 3D camera.
    """
    try:
        import io as _io
        from PIL import Image
        import numpy as np
    except ImportError as exc:
        raise ParseError("Pillow and numpy are required for image parsing") from exc

    img = Image.open(_io.BytesIO(blob))
    gray = img.convert("L")
    arr = np.array(gray)
    source_height = float(arr.shape[0])

    # Cap the analysis resolution: the ink and flood-fill passes are pure
    # Python loops, and a 4000 px photo would take tens of seconds.
    inverse_scale = 1.0
    longest = max(arr.shape)
    if longest > _MAX_ANALYSIS_PX:
        ratio = _MAX_ANALYSIS_PX / float(longest)
        small = gray.resize((max(1, int(arr.shape[1] * ratio)),
                             max(1, int(arr.shape[0] * ratio))),
                            Image.LANCZOS)
        arr = np.array(small)
        inverse_scale = 1.0 / ratio

    mask, ignored = _structure_mask(arr)
    walls = _walls_from_mask(mask)
    walls, regions, _furniture = _filter_raster_walls(arr.shape, mask, walls)

    if len(walls) < _MIN_RASTER_WALLS:
        raise UnsupportedFormat(
            f"Only {len(walls)} wall line(s) were detected in this image — that is "
            "not enough to read as a floor plan. The plan may be too low "
            "resolution, too faint, or the drawing may use curves and hatching "
            "this reader cannot trace. Export a DXF from your CAD tool for an "
            "exact read."
        )

    # Put everything back in ORIGINAL pixel coordinates so the room crops used
    # for OCR and the flip below both speak the same language as the upload.
    if inverse_scale != 1.0:
        walls = [Wall(id=w.id,
                      start=Point2D(x=w.start.x * inverse_scale, y=w.start.y * inverse_scale),
                      end=Point2D(x=w.end.x * inverse_scale, y=w.end.y * inverse_scale),
                      thickness=w.thickness, height=w.height,
                      is_exterior=w.is_exterior)
                 for w in walls]
        regions = [[(px * inverse_scale, py * inverse_scale) for px, py in region]
                   for region in regions]

    notes = [
        _assume("ink_filter",
                f"{ignored} stray pen blob(s) ignored",
                "dimension lines, room titles and logos are not walls; only the "
                "building's own connected line work is traced",
                source="agent"),
    ]

    return _build_model_from_segments(
        walls, f"{name} (parsed from image)", source="upload",
        unit_note="image pixels converted to metres using an assumed scale",
        room_regions=regions,
        image_blob=blob,
        source_height=source_height,
        extra_assumptions=notes,
    )


def _otsu_threshold(arr) -> int:
    """Pick the binarisation threshold from the image itself.

    A fixed threshold of 128 is wrong for architectural plans: they are mostly
    white paper with thin GREY line work, so a hard 128 finds the room names
    and the logo but misses most walls. Otsu splits the histogram at the point
    of maximum between-class variance, which lands on the line work rather
    than on the darkest text.

    For a nearly-all-white image (sparse dark content), Otsu degenerates: the
    variance is maximised at threshold 0 or 1 because the "dark" class is tiny.
    In that case we fall back to a percentile cut: the threshold is the
    brightest value that still marks at least 2% of the image as "ink".
    """
    hist = np.histogram(arr, bins=256, range=(0, 256))[0].astype(np.float64)
    total = hist.sum()
    if total == 0:
        return 128
    levels = np.arange(256, dtype=np.float64)
    w0 = np.cumsum(hist)
    w1 = total - w0
    sum0 = np.cumsum(hist * levels)
    sum_total = sum0[-1]
    with np.errstate(invalid="ignore", divide="ignore"):
        mean0 = sum0 / w0
        mean1 = (sum_total - sum0) / w1
        between = w0 * w1 * (mean0 - mean1) ** 2
    between = np.nan_to_num(between)
    thr = int(np.argmax(between))
    if thr > 240:
        return 235

    # Degenerate Otsu: when the dark class is < 0.5% of the image, the
    # threshold is too low to capture line work. Fall back to a percentile
    # cut that marks at least 2% of pixels as ink.
    dark_frac = float((arr < thr).sum()) / float(total)
    if dark_frac < 0.005:
        cumulative = np.cumsum(hist[::-1]).astype(np.float64) / total
        # Walk from the darkest end until we have >= 2% coverage.
        for k in range(256):
            if cumulative[k] >= 0.02:
                return 255 - k
        return 128

    return thr


def _find_runs(arr_col, min_len: int) -> list[tuple[int, int]]:
    """Find runs of True values in a boolean array, longer than min_len."""
    if np is None:  # pragma: no cover - dependency is declared
        runs: list[tuple[int, int]] = []
        start = None
        for i, v in enumerate(arr_col):
            if v and start is None:
                start = i
            elif not v and start is not None:
                if i - start >= min_len:
                    runs.append((start, i))
                start = None
        if start is not None and len(arr_col) - start >= min_len:
            runs.append((start, len(arr_col)))
        return runs

    # Vectorised: diffing the padded boolean line marks where runs start and
    # end. Scanning EVERY row and column then stays cheap, which matters
    # because sampling columns would step straight over a 1 px vertical wall.
    padded = np.concatenate(([False], np.asarray(arr_col, dtype=bool), [False]))
    edges = np.diff(padded.astype(np.int8))
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1)
    return [(int(s), int(e)) for s, e in zip(starts, ends) if e - s >= min_len]


def _ink_mask(arr):
    """True where the sheet has ink, using the image's own threshold."""
    if np is None:  # pragma: no cover - dependency is declared
        return arr < 128
    return arr < _otsu_threshold(arr)


def _ink_components(dark):
    """Label the connected ink blobs on a binarised sheet.

    Returns ``(labels, blobs)`` where ``labels`` is an int array (0 = paper)
    and ``blobs`` is a list of ``(size, label, x0, y0, x1, y1)`` ordered by
    size, largest first.

    Why this exists: a plan sheet is the building PLUS annotations, and the
    annotations are what a naive run-scanner reads as walls. The building's
    line work is one big connected blob; a room title, a dimension line and
    the plan-library logo are small blobs of their own, so labelling tells
    them apart without guessing what a wall looks like.
    """
    if np is None:  # pragma: no cover - dependency is declared
        return np.zeros(dark.shape, dtype=np.int32), []

    h, w = dark.shape
    labels = np.zeros((h, w), dtype=np.int32)
    blobs: list[tuple[int, int, int, int, int, int]] = []
    next_label = 0

    for sy in range(h):
        row = dark[sy]
        if not row.any():
            continue
        for sx in np.flatnonzero(row & (labels[sy] == 0)):
            sx = int(sx)
            if labels[sy, sx]:
                continue
            next_label += 1
            size = 0
            x0 = x1 = sx
            y0 = y1 = sy
            stack = [(sy, sx)]
            labels[sy, sx] = next_label
            while stack:
                y, x = stack.pop()
                size += 1
                if x < x0:
                    x0 = x
                elif x > x1:
                    x1 = x
                if y < y0:
                    y0 = y
                elif y > y1:
                    y1 = y
                if y > 0 and dark[y - 1, x] and not labels[y - 1, x]:
                    labels[y - 1, x] = next_label
                    stack.append((y - 1, x))
                if y + 1 < h and dark[y + 1, x] and not labels[y + 1, x]:
                    labels[y + 1, x] = next_label
                    stack.append((y + 1, x))
                if x > 0 and dark[y, x - 1] and not labels[y, x - 1]:
                    labels[y, x - 1] = next_label
                    stack.append((y, x - 1))
                if x + 1 < w and dark[y, x + 1] and not labels[y, x + 1]:
                    labels[y, x + 1] = next_label
                    stack.append((y, x + 1))
            blobs.append((size, next_label, x0, y0, x1, y1))

    blobs.sort(key=lambda b: b[0], reverse=True)
    return labels, blobs


def _structure_mask(arr):
    """Return ``(mask, ignored)``: the sheet's ink with annotations removed.

    The building is the largest connected ink blob on the sheet. Everything
    else — dimension lines, room titles, the logo, the north arrow, the title
    block — is a separate blob that is either tiny or parked outside the
    building's footprint, and is dropped.

    ``ignored`` is the number of blobs discarded, so the model can say so
    instead of pretending the whole sheet was read.
    """
    dark = _ink_mask(arr)
    if np is None:  # pragma: no cover - dependency is declared
        return dark, 0

    labels, blobs = _ink_components(dark)
    if not blobs:
        return dark, 0

    main_size, _main_label, mx0, my0, mx1, my1 = blobs[0]
    span = max(mx1 - mx0, my1 - my0) or 1
    margin = span * _STRUCTURE_MARGIN_FRACTION
    min_size = max(60, int(main_size * _STRUCTURE_MIN_SHARE))

    keep_ids: list[int] = []
    ignored = 0
    for size, label, x0, y0, x1, y1 in blobs:
        inside_box = not (x1 < mx0 - margin or x0 > mx1 + margin
                          or y1 < my0 - margin or y0 > my1 + margin)
        if size >= min_size and inside_box:
            keep_ids.append(label)
        else:
            ignored += 1

    if not keep_ids:
        return dark, ignored
    return np.isin(labels, keep_ids), ignored


def _merge_collinear(segments: list[tuple[Point2D, Point2D]],
                     tolerance: float = 2.0,
                     band: float = 0.0) -> list[tuple[Point2D, Point2D]]:
    """Merge runs that lie on the same wall line.

    A wall drawn with a 3 px pen yields three runs a pixel apart, and a
    double-line wall yields two more. Left alone they become a stack of
    near-duplicate stubs, which renders as a thick blobby band and doubles the
    wall count. ``band`` is how far apart two parallel runs may sit and still
    count as the same wall; the runs are collapsed onto one centreline.

    Runs on the same line merge only when their spans touch or overlap, so a
    wall either side of a door stays two walls instead of becoming one
    invented solid wall across the opening.
    """
    if not segments:
        return []

    band = max(tolerance, band)

    def _cluster(items: list[tuple[float, float, float]]) -> list[tuple[float, float, float]]:
        # items: (line coordinate, span start, span end) -> (mean coord, lo, hi)
        items.sort()
        clusters: list[list[float]] = []  # [mean coord, lo, hi, count]
        for coord, lo, hi in items:
            for cluster in clusters:
                if (abs(cluster[0] - coord) <= band
                        and lo <= cluster[2] + tolerance
                        and hi >= cluster[1] - tolerance):
                    cluster[0] = (cluster[0] * cluster[3] + coord) / (cluster[3] + 1)
                    cluster[1] = min(cluster[1], lo)
                    cluster[2] = max(cluster[2], hi)
                    cluster[3] += 1
                    break
            else:
                clusters.append([coord, lo, hi, 1])
        return [(c[0], c[1], c[2]) for c in clusters]

    horizontal: list[tuple[float, float, float]] = []
    vertical: list[tuple[float, float, float]] = []
    other: list[tuple[Point2D, Point2D]] = []

    for a, b in segments:
        if abs(a.y - b.y) <= tolerance:
            horizontal.append(((a.y + b.y) / 2.0, min(a.x, b.x), max(a.x, b.x)))
        elif abs(a.x - b.x) <= tolerance:
            vertical.append(((a.x + b.x) / 2.0, min(a.y, b.y), max(a.y, b.y)))
        else:
            other.append((a, b))

    out: list[tuple[Point2D, Point2D]] = []
    for coord, lo, hi in _cluster(horizontal):
        out.append((Point2D(x=lo, y=coord), Point2D(x=hi, y=coord)))
    for coord, lo, hi in _cluster(vertical):
        out.append((Point2D(x=coord, y=lo), Point2D(x=coord, y=hi)))
    out.extend(other)

    out.sort(key=lambda ab: (round(ab[0].x, 2), round(ab[0].y, 2),
                             round(ab[1].x, 2), round(ab[1].y, 2)))
    return out


def _walls_from_mask(mask) -> list[Wall]:
    """Trace wall lines from a boolean ink mask.

    Two independent passes (rows for horizontal walls, columns for vertical
    walls), then merged along each line so a wall drawn with a wide pen
    becomes ONE wall instead of a row of stubs.
    """
    h, w = mask.shape

    # A wall in a residential plan is rarely shorter than ~4% of the plan's
    # width. Shorter runs are door leaves, furniture edges and text strokes.
    min_run = max(12, int(w * 0.04))
    band = max(4.0, max(h, w) * _WALL_BAND_FRACTION)

    raw: list[tuple[Point2D, Point2D]] = []

    for row in range(h):
        for start, end in _find_runs(mask[row], min_run):
            raw.append((Point2D(x=float(start), y=float(row)),
                        Point2D(x=float(end), y=float(row))))

    for col in range(w):
        for start, end in _find_runs(mask[:, col], min_run):
            raw.append((Point2D(x=float(col), y=float(start)),
                        Point2D(x=float(col), y=float(end))))

    merged = _merge_collinear(raw, band=band)

    walls: list[Wall] = []
    for a, b in merged:
        if math.dist((a.x, a.y), (b.x, b.y)) < min_run:
            continue
        walls.append(Wall(id=f"w{len(walls) + 1}", start=a, end=b,
                          thickness=0.15, height=3.0, is_exterior=False))
    return walls


def _drop_pattern_ink(walls: list[Wall], span: float,
                      min_lines: int = 3,
                      close_spacing_fraction: float = 0.03,
                      same_span_fraction: float = 0.02,
                      margin_fraction: float = 0.02) -> list[Wall]:
    """Drop hatching and stair treads, which are pattern, not walls.

    A stair, a tiled balcony or a section hatch is drawn as a stack of short
    parallel lines of nearly equal length, a few pixels apart. Each one looks
    exactly like a wall in isolation, and when the patch touches a wall the
    sheet filter cannot isolate it, so a naive trace returns a dozen invented
    walls in a bay window.

    The signature is unmistakable and purely local: several parallel lines of
    the same length, evenly and tightly spaced. Real walls never do that —
    they are at least a room apart — so the test is safe. The lines inside a
    recognised patch are the hatch's own, and are dropped with it.
    """
    if span <= 0 or len(walls) < min_lines:
        return walls

    tol = span * same_span_fraction
    horizontal: list[tuple[float, float, float]] = []
    vertical: list[tuple[float, float, float]] = []
    for wl in walls:
        if abs(wl.start.y - wl.end.y) <= tol:
            horizontal.append(((wl.start.y + wl.end.y) / 2.0,
                               min(wl.start.x, wl.end.x),
                               max(wl.start.x, wl.end.x)))
        elif abs(wl.start.x - wl.end.x) <= tol:
            vertical.append(((wl.start.x + wl.end.x) / 2.0,
                             min(wl.start.y, wl.end.y),
                             max(wl.start.y, wl.end.y)))

    def _clusters(items: list[tuple[float, float, float]]):
        clusters: list[list[tuple[float, float, float]]] = []
        for coord, lo, hi in sorted(items, key=lambda e: (e[1], e[2])):
            for cluster in clusters:
                if (abs(lo - cluster[0][1]) <= tol
                        and abs(hi - cluster[0][2]) <= tol):
                    cluster.append((coord, lo, hi))
                    break
            else:
                clusters.append([(coord, lo, hi)])
        return clusters

    patches: list[tuple[float, float, float, float]] = []
    for items, is_horizontal in ((horizontal, True), (vertical, False)):
        for cluster in _clusters(items):
            if len(cluster) < min_lines:
                continue
            coords = sorted(c for c, _lo, _hi in cluster)
            spacing = [(b - a) for a, b in zip(coords, coords[1:])]
            if sum(spacing) / len(spacing) > span * close_spacing_fraction:
                continue
            lo = min(entry[1] for entry in cluster)
            hi = max(entry[2] for entry in cluster)
            patches.append((lo, coords[0], hi, coords[-1]) if is_horizontal
                           else (coords[0], lo, coords[-1], hi))

    if not patches:
        return walls

    margin = span * margin_fraction
    boxes = [(x0 - margin, y0 - margin, x1 + margin, y1 + margin)
             for x0, y0, x1, y1 in patches]

    def _in_pattern(x: float, y: float) -> bool:
        return any(x0 <= x <= x1 and y0 <= y <= y1 for x0, y0, x1, y1 in boxes)

    kept = [wl for wl in walls
            if not (_in_pattern(wl.start.x, wl.start.y)
                    and _in_pattern(wl.end.x, wl.end.y))]

    for index, wl in enumerate(kept):
        wl.id = f"w{index + 1}"
    return kept


def _draw_span(walls: list[Wall]) -> float:
    """Longest extent of the traced drawing, used to scale every threshold."""
    xs = [c for wl in walls for c in (wl.start.x, wl.end.x)]
    ys = [c for wl in walls for c in (wl.start.y, wl.end.y)]
    if not xs or not ys:
        return 0.0
    return max(max(xs) - min(xs), max(ys) - min(ys))


def _detect_walls_from_raster(arr) -> list[Wall]:
    """Detect wall lines in a greyscale plan image, annotations excluded."""
    mask, _ignored = _structure_mask(arr)
    walls = _walls_from_mask(mask)
    walls, _regions, _furniture = _filter_raster_walls(arr.shape, mask, walls)
    return walls


def _filter_raster_walls(shape, mask, walls: list[Wall]):
    """Drop everything that was traced but is not a wall.

    Shared by the image reader and the scanned-PDF reader so both see the same
    drawing as the same building. Order matters: the pattern filter runs first
    because hatching would otherwise join the wall outlines and invent rooms;
    the room probe then has clean outlines to measure, and the thin-ink filter
    uses those rooms to reject the last few hairline strokes.

    Returns the kept walls plus the free regions and the nested (furniture)
    regions that were found, since the image reader needs them for OCR.
    """
    walls = _drop_pattern_ink(walls, _draw_span(walls))
    regions, furniture = _rooms_from_enclosed_regions(shape, walls, ink=mask)
    walls = _drop_interior_furniture(walls, regions, furniture)
    walls = _drop_thin_interior_ink(walls, regions, mask, _draw_span(walls))
    return walls, regions, furniture


def _bridge_gaps(walls: list[Wall], max_gap: float, paint,
                 collinear_tol: float = 2.0) -> None:
    """Close doorway-sized gaps between collinear walls, in a mask only.

    Two walls either side of a door end a doorway apart on the same line.
    Region detection needs the room boundary closed or every room floods into
    the next one; the wall list itself is left alone so the opening survives
    into the model.

    ``collinear_tol`` is how far apart two centrelines may sit and still count
    as one line. It must scale with the analysis resolution: the same drawing
    read at 900 px and at 1600 px puts the two halves of a doorway a couple of
    pixels apart in one case and four in the other, and a fixed tolerance
    would bridge the door at one size and not at the other — the floor would
    then read as one huge room at 1600 px and as a dozen rooms at 900 px.

    ``paint(x0, y0, x1, y1)`` draws into the caller's mask.
    """
    if max_gap <= 0:
        return

    horizontal: list[tuple[float, float, float]] = []
    vertical: list[tuple[float, float, float]] = []
    for wl in walls:
        if abs(wl.start.y - wl.end.y) <= collinear_tol:
            horizontal.append(((wl.start.y + wl.end.y) / 2.0,
                               min(wl.start.x, wl.end.x),
                               max(wl.start.x, wl.end.x)))
        elif abs(wl.start.x - wl.end.x) <= collinear_tol:
            vertical.append(((wl.start.x + wl.end.x) / 2.0,
                             min(wl.start.y, wl.end.y),
                             max(wl.start.y, wl.end.y)))

    for line, group in (("h", horizontal), ("v", vertical)):
        for i, (coord_a, lo_a, hi_a) in enumerate(group):
            for coord_b, lo_b, hi_b in group[i + 1:]:
                if abs(coord_a - coord_b) > collinear_tol:
                    continue

                # Only two spans that face each other across the gap count.
                gap = max(lo_a, lo_b) - min(hi_a, hi_b)
                if not 0.0 < gap <= max_gap:
                    continue
                start, end = min(hi_a, hi_b), max(lo_a, lo_b)
                if line == "h":
                    paint(start, coord_a, end, coord_a)
                else:
                    paint(coord_a, start, coord_a, end)


def _drop_interior_furniture(walls: list[Wall],
                             regions: list[list[tuple[int, int]]],
                             furniture: list[list[tuple[int, int]]] | None = None,
                             inset: float = 4.0) -> list[Wall]:
    """Drop traced lines that are furniture, not walls.

    A bed, a sofa or a wardrobe drawn as a closed box often touches a wall, so
    it joins the building's ink, survives the sheet filter, and comes out as
    four invented walls in the middle of a bedroom.

    Two signals, both structural:

    * the box was found as a region NESTED inside a room (``furniture``), so
      the lines that enclose it are its own outline;
    * a line lies strictly inside a room's boundary, inset by a few pixels,
      which a real partition never does — a partition IS the boundary.
    """
    boxes: list[tuple[float, float, float, float]] = []

    for region in furniture or []:
        xs = [p[0] for p in region]
        ys = [p[1] for p in region]
        if xs and ys:
            boxes.append((min(xs) - 3, min(ys) - 3, max(xs) + 3, max(ys) + 3))

    for region in regions:
        xs = [p[0] for p in region]
        ys = [p[1] for p in region]
        if xs and ys:
            boxes.append((min(xs) + inset, min(ys) + inset,
                          max(xs) - inset, max(ys) - inset))

    if not boxes:
        return walls

    def _inside(x: float, y: float) -> bool:
        return any(x0 <= x <= x1 and y0 <= y <= y1 for x0, y0, x1, y1 in boxes)

    kept: list[Wall] = []
    for wl in walls:
        if _inside(wl.start.x, wl.start.y) and _inside(wl.end.x, wl.end.y):
            continue
        kept.append(wl)

    for index, wl in enumerate(kept):
        wl.id = f"w{index + 1}"
    return kept


def _ink_weight(dark, wl: Wall, reach: int = 4, samples: int = 5) -> float:
    """How much ink a line is drawn with, sampled across it.

    A wall is a heavy line, or a pair of faces with a cavity between them, so a
    slice across it crosses two or more dark pixels. A hatch stroke or a chair
    back is a single hairline. Sampling the median of a few perpendicular
    slices gives the weight without needing to know the pen width.
    """
    if np is None:  # pragma: no cover - dependency is declared
        return 0.0

    h, w = dark.shape
    x0, y0 = wl.start.x, wl.start.y
    x1, y1 = wl.end.x, wl.end.y
    horizontal = abs(y1 - y0) <= abs(x1 - x0)
    counts: list[int] = []

    for i in range(samples):
        t = (i + 0.5) / samples
        x = int(round(x0 + (x1 - x0) * t))
        y = int(round(y0 + (y1 - y0) * t))
        if not (0 <= x < w and 0 <= y < h):
            continue
        if horizontal:
            lo, hi = max(0, y - reach), min(h, y + reach + 1)
            counts.append(int(dark[lo:hi, x].sum()))
        else:
            lo, hi = max(0, x - reach), min(w, x + reach + 1)
            counts.append(int(dark[y, lo:hi].sum()))

    if not counts:
        return 0.0
    counts.sort()
    return float(counts[len(counts) // 2])


def _drop_thin_interior_ink(walls: list[Wall],
                            regions: list[list[tuple[int, int]]],
                            dark, span: float,
                            ratio: float = _HAIRLINE_STROKE_RATIO) -> list[Wall]:
    """Drop hairlines that sit inside a room and cannot be a wall.

    The last lines of a hatch patch, a chair back, a table edge: drawn with a
    hairline rather than the wall pen, they survive the sheet filter because
    they touch the building, and they are too short to be a partition. Both
    facts together are the test — a short line drawn with the WALL pen is a
    real stub (a nib, a partition end) and is kept.

    ``ratio`` is compared against the walls' own median ink weight, so it holds
    for a faint 1 px plan as well as for a heavy scan.
    """
    if not regions or not walls:
        return walls

    rects = []
    for region in regions:
        xs = [p[0] for p in region]
        ys = [p[1] for p in region]
        if xs and ys:
            rects.append((min(xs), min(ys), max(xs), max(ys)))
    if not rects:
        return walls

    weights = [_ink_weight(dark, wl) for wl in walls]
    long_weights = [wt for wt, wl in zip(weights, walls)
                    if wt > 0 and math.dist((wl.start.x, wl.start.y),
                                            (wl.end.x, wl.end.y)) > span * 0.1]
    if not long_weights:
        return walls
    long_weights.sort()
    reference = long_weights[len(long_weights) // 2]

    kept: list[Wall] = []
    for wl, weight in zip(walls, weights):
        if weight <= 0 or weight >= reference * ratio:
            kept.append(wl)
            continue
        length = math.dist((wl.start.x, wl.start.y), (wl.end.x, wl.end.y))
        interior = False
        for x0, y0, x1, y1 in rects:
            if not (x0 <= wl.start.x <= x1 and x0 <= wl.end.x <= x1
                    and y0 <= wl.start.y <= y1 and y0 <= wl.end.y <= y1):
                continue
            # A wall that spans its room is a partition; one that fits inside
            # the room with room to spare is not.
            if length <= min(x1 - x0, y1 - y0) * 0.9:
                interior = True
                break
        if interior:
            continue
        kept.append(wl)

    for index, wl in enumerate(kept):
        wl.id = f"w{index + 1}"
    return kept



def _ink_band_thickness(dark, box) -> float | None:
    """Median thickness of the ink band hugging a region's four sides.

    This is the signal that separates a room from a bed standing in one. Both
    are boxes of free space ringed by ink, so geometry alone cannot tell them
    apart — but a plan draws walls thick (often as poché) and furniture thin.
    Sampling runs of ink outward from the middle of each side of the region
    gives that thickness directly, in whatever pixels the image happens to
    have.

    Returns ``None`` when no ink hugs the region, which the caller treats as
    "no evidence either way".
    """
    if np is None:  # pragma: no cover - dependency is declared
        return None

    h, w = dark.shape
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    samples: list[int] = []

    # Skip the corners: a corner sits on a junction, where the ink is always
    # at least two strokes thick and would inflate the reading.
    inset_x = max(1, (x1 - x0) // 4)
    inset_y = max(1, (y1 - y0) // 4)
    reach = max(4, int(max(x1 - x0, y1 - y0) * 0.25))

    def _walk(x: int, y: int, dx: int, dy: int) -> int:
        run = 0
        while 0 <= x < w and 0 <= y < h and dark[y, x] and run <= reach:
            run += 1
            x += dx
            y += dy
        return run

    for x in range(x0 + inset_x, x1 - inset_x + 1):
        if 0 <= x < w:
            for dy in (-1, 1):
                run = _walk(x, (y0 if dy < 0 else y1) + dy, 0, dy)
                if run:
                    samples.append(run)

    for y in range(y0 + inset_y, y1 - inset_y + 1):
        if 0 <= y < h:
            for dx in (-1, 1):
                run = _walk((x0 if dx < 0 else x1) + dx, y, dx, 0)
                if run:
                    samples.append(run)

    if not samples:
        return None
    samples.sort()
    return float(samples[len(samples) // 2])


def _rooms_from_enclosed_regions(shape, walls: list[Wall], ink=None,
                                  min_area_px: int | None = None
                                  ) -> tuple[list[list[tuple[int, int]]],
                                             list[list[tuple[int, int]]]]:
    """Find room polygons as regions of the plan enclosed by wall lines.

    Returns ``(rooms, furniture)``. ``furniture`` holds the boxes that were
    nested inside a room and therefore rejected as rooms; the caller uses them
    to drop their outlines from the wall list.

    ``ink`` is the filtered dark-pixel mask the walls came from. It is used to
    measure how thickly each enclosed box is drawn: a nested box ringed by
    THIN lines is a bed or a wardrobe, one ringed by wall-thickness lines is a
    room that happens to sit inside another room's extent.

    The wall lines are painted onto a blank mask; everything that is NOT a
    wall and is NOT reachable from the image border is enclosed interior
    space, i.e. a room. This is the standard connected-component reading of a
    plan and it needs no OCR.

    The painted lines are the merged CENTRELINES, not the raw ink: a
    double-line wall encloses a thin cavity, and at pixel resolution that
    cavity is itself an "enclosed region" which would come back as a phantom
    long, thin room. Collapsing each wall to one line closes the cavity.

    Border-reachable space is discarded, which is what removes the outside of
    the building, the dimension lines and the drawing-sheet margin from the
    result.
    """
    if np is None:  # pragma: no cover - dependency is declared
        return [], []

    h, w = shape
    mask = np.zeros((h, w), dtype=bool)  # True = wall

    def _paint(x0: float, y0: float, x1: float, y1: float) -> None:
        ix0 = int(max(0, min(x0, x1) - 2))
        ix1 = int(min(w, max(x0, x1) + 3))
        iy0 = int(max(0, min(y0, y1) - 2))
        iy1 = int(min(h, max(y0, y1) + 3))
        if ix1 > ix0 and iy1 > iy0:
            mask[iy0:iy1, ix0:ix1] = True

    for wl in walls:
        _paint(wl.start.x, wl.start.y, wl.end.x, wl.end.y)

    # The drawing's own extent sets the size of "a room". A bed on a 736 px
    # drawing is small; a bedroom is not.
    wall_xs = [c for wl in walls for c in (wl.start.x, wl.end.x)]
    wall_ys = [c for wl in walls for c in (wl.start.y, wl.end.y)]
    if not wall_xs or not wall_ys:
        return [], []
    draw_span = max(max(wall_xs) - min(wall_xs), max(wall_ys) - min(wall_ys))
    if draw_span <= 0:
        return [], []
    _min_room_side = draw_span * _MIN_ROOM_SIDE_FRACTION

    if min_area_px is None:
        # Scale the floor with the drawing instead of hard-coding pixels, so
        # the same plan read at 700 px and at 1600 px finds the same rooms.
        min_area_px = int(max(400, (_min_room_side ** 2) * 0.8))

    # Doors and windows are gaps in the wall line, so without help the flood
    # fill walks from the hall into every bedroom and the whole floor comes
    # back as ONE room. Bridge collinear wall ends across a gap no wider than
    # a doorway, in the mask only — the model keeps the opening. The
    # collinearity tolerance scales with the drawing for the same reason the
    # gap does: at 1600 px the two halves of one doorway sit further apart in
    # pixels than they do at 900 px.
    _bridge_gaps(walls, draw_span * 0.05, _paint,
                 collinear_tol=max(2.0, draw_span * 0.006))

    free = ~mask

    # Multi-source flood fill from every border pixel: whatever it reaches is
    # OUTSIDE (or an open margin), and whatever it does not reach is enclosed.
    outside = np.zeros((h, w), dtype=bool)
    stack: list[tuple[int, int]] = []

    def _push(y: int, x: int) -> None:
        if not outside[y, x] and free[y, x]:
            outside[y, x] = True
            stack.append((y, x))

    for x in range(w):
        _push(0, x)
        _push(h - 1, x)
    for y in range(h):
        _push(y, 0)
        _push(y, w - 1)

    while stack:
        y, x = stack.pop()
        if y > 0:
            _push(y - 1, x)
        if y + 1 < h:
            _push(y + 1, x)
        if x > 0:
            _push(y, x - 1)
        if x + 1 < w:
            _push(y, x + 1)

    enclosed = free & ~outside

    # Label the enclosed components with a second flood fill, one component
    # at a time, so each becomes one room.
    seen = np.zeros((h, w), dtype=bool)
    candidates: list[tuple[list[tuple[int, int]], float | None]] = []

    for sy in range(h):
        row = enclosed[sy]
        if not row.any():
            continue
        for sx in range(w):
            if not enclosed[sy, sx] or seen[sy, sx]:
                continue
            comp: list[tuple[int, int]] = []
            comp_stack = [(sy, sx)]
            seen[sy, sx] = True
            while comp_stack:
                y, x = comp_stack.pop()
                comp.append((y, x))
                for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                    if 0 <= ny < h and 0 <= nx < w and enclosed[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        comp_stack.append((ny, nx))

            if len(comp) < min_area_px:
                continue
            cy = [p[0] for p in comp]
            cx = [p[1] for p in comp]
            x0, x1 = min(cx), max(cx)
            y0, y1 = min(cy), max(cy)
            bw = x1 - x0
            bh = y1 - y0
            if bw * bh < min_area_px:
                continue
            longest = max(bw, bh)
            if longest < _min_room_side:
                continue
            # A sliver is a wall cavity or a hatch gap, not a room.
            if min(bw, bh) < draw_span * _MIN_ROOM_SHORT_SIDE_FRACTION:
                continue
            box = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
            thickness = _ink_band_thickness(ink, (x0, y0, x1, y1)) if ink is not None else None
            candidates.append((box, thickness))

    # A region that lies INSIDE another region is furniture, not a room: a
    # bed drawn inside a bedroom is an enclosed box within an enclosed box.
    # Keeping the outer one and dropping the inner one is both simpler and
    # correct, and it avoids the aspect-ratio guesswork that would otherwise
    # throw away a genuine narrow bathroom.
    #
    # The test is CONTAINMENT of the inner region's own pixels, not of its
    # bounding box: a bathroom that happens to sit within a large room's
    # bounding box is still a bathroom, and only a region whose pixels are
    # genuinely all inside the other one is furniture.
    def _pixel_inside(inner: list, outer: list) -> bool:
        ox0, oy0 = outer[0]
        ox1, oy1 = outer[2]
        ix0, iy0 = inner[0]
        ix1, iy1 = inner[2]
        # The outer region must cover the inner region's box on BOTH axes
        # with a real margin, and the inner must be materially smaller.
        if not (ox0 <= ix0 and oy0 <= iy0 and ox1 >= ix1 and oy1 >= iy1):
            return False
        outer_area = (ox1 - ox0) * (oy1 - oy0)
        inner_area = (ix1 - ix0) * (iy1 - iy0)
        if outer_area <= 0:
            return False
        # Furniture occupies a small part of the room it stands in.
        return inner_area / float(outer_area) < _MAX_NESTED_AREA_RATIO

    candidates.sort(key=lambda c: (c[0][2][0] - c[0][0][0]) * (c[0][2][1] - c[0][0][1]),
                    reverse=True)

    # How thickly this drawing strokes a wall. Rooms outnumber furniture on
    # any real plan, so the upper quartile of the enclosed boxes' line
    # thickness is the wall weight, and furniture is drawn thinner than that.
    strokes = sorted(t for _box, t in candidates if t)
    wall_stroke = strokes[int(len(strokes) * 0.75)] if strokes else None

    def _thin(box: list[tuple[int, int]], thickness: float | None) -> bool:
        """Is this nested box drawn like furniture rather than like walls?"""
        if thickness is None or wall_stroke is None or wall_stroke <= 0:
            # No measurement: keep the old, size-only reading. It is wrong
            # only for plans that draw furniture as thickly as walls.
            return True
        return thickness < wall_stroke * _FURNITURE_STROKE_RATIO

    rooms: list[list[tuple[int, int]]] = []
    furniture: list[list[tuple[int, int]]] = []
    for cand, thickness in candidates:
        if any(_pixel_inside(cand, kept) and _thin(cand, thickness) for kept in rooms):
            furniture.append(cand)
            continue
        rooms.append(cand)

    rooms.sort(key=lambda r: (r[0][1], r[0][0]))
    # The nested boxes are returned too: they are the beds, sofas and tables
    # whose outlines were traced as walls, and the caller drops those lines.
    return rooms, furniture


def _rasterise_and_detect_lines(page, reader) -> tuple[list[Wall], float | None]:
    """Rasterise a PDF page to pixels and detect straight lines.

    Returns the walls and the PIXEL height of the raster they came from, which
    the caller needs to flip y into model space.
    """
    try:
        import io as _io
        from PIL import Image
        import numpy as np
    except ImportError:
        return [], None

    # Extract images from the page
    images = []
    try:
        for img_name, img_obj in page.images.items():
            data = img_obj.data
            pil = Image.open(_io.BytesIO(data))
            images.append(pil)
    except Exception:
        pass

    if not images:
        return [], None

    # Use the first (largest) image
    img = max(images, key=lambda i: i.size[0] * i.size[1])
    gray = img.convert("L")
    arr = np.array(gray)

    inverse_scale = 1.0
    longest = max(arr.shape)
    if longest > _MAX_ANALYSIS_PX:
        ratio = _MAX_ANALYSIS_PX / float(longest)
        small = gray.resize((max(1, int(arr.shape[1] * ratio)),
                             max(1, int(arr.shape[0] * ratio))),
                            Image.LANCZOS)
        arr = np.array(small)
        inverse_scale = 1.0 / ratio

    source_height = float(arr.shape[0] * inverse_scale)
    walls = _detect_walls_from_raster(arr)

    if inverse_scale != 1.0:
        walls = [Wall(id=w.id,
                      start=Point2D(x=w.start.x * inverse_scale, y=w.start.y * inverse_scale),
                      end=Point2D(x=w.end.x * inverse_scale, y=w.end.y * inverse_scale),
                      thickness=w.thickness, height=w.height,
                      is_exterior=w.is_exterior)
                 for w in walls]
    return walls, source_height


def calibrate_scale(model: BuildingModel, p1: Point2D, p2: Point2D,
                    distance_m: float) -> BuildingModel:
    """Apply a user-calibrated scale. Non-positive distances are refused."""
    if distance_m <= 0:
        raise ParseError("the calibration distance must be greater than zero")
    drawn = math.dist((p1.x, p1.y), (p2.x, p2.y))
    if drawn <= 0:
        raise ParseError("the two calibration points are the same point")
    out = model.clone()
    out.scale = Scale(p1=p1, p2=p2, distance_m=distance_m, calibrated_by="user")
    factor = distance_m / drawn
    if abs(factor - 1.0) > 1e-9:
        # The drawing is in drawing units; scale every plan coordinate so that
        # everything downstream stays in real metres.
        for lvl in out.levels:
            for w in lvl.walls:
                w.start = Point2D(x=w.start.x * factor, y=w.start.y * factor)
                w.end = Point2D(x=w.end.x * factor, y=w.end.y * factor)
            for r in lvl.rooms:
                r.polygon = [Point2D(x=p.x * factor, y=p.y * factor) for p in r.polygon]
                r.area_m2 = round(r.area_m2 * factor * factor, 2)
            for f in lvl.fixtures:
                f.at = Point2D(x=f.at.x * factor, y=f.at.y * factor)
            for e in lvl.equipment:
                e.at = Point2D(x=e.at.x * factor, y=e.at.y * factor)
    out.assumptions.append(_assume("scale", f"{factor:.4f}", 
                                   f"user calibrated {distance_m} m over {drawn:.3f} drawing "
                                   "units", source="user"))
    out.updated_at = _now()
    return out


def _normalise_room_label(raw: str) -> str:
    """Normalise OCR text so room labels and fixture markers read consistently."""
    cleaned = re.sub(r"\s+", " ", (raw or "").strip())
    cleaned = cleaned.replace("_", " ")
    cleaned = cleaned.replace("-", " ")
    cleaned = cleaned.replace("|", " ")
    cleaned = cleaned.replace("•", " ")
    cleaned = cleaned.replace("0", "O")
    cleaned = cleaned.replace("1", "I")
    cleaned = cleaned.replace("5", "S")
    cleaned = cleaned.replace("7", "T")
    cleaned = cleaned.replace("8", "B")
    cleaned = re.sub(r"[^A-Za-z0-9 /&]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or "Room"


def _best_room_name_from_text(raw: str) -> str:
    """Pick the most likely room label from noisy OCR output."""
    cleaned = _normalise_room_label(raw)
    if not cleaned or cleaned == "Room":
        return "Room"

    tokens = cleaned.upper().split()
    for keyword in ["BEDROOM", "BATHROOM", "KITCHEN", "LIVING", "DINING",
                    "UTILITY", "LAUNDRY", "WASH", "HALL", "CORRIDOR",
                    "BALCONY", "TERRACE", "GARAGE", "ROOM",
                    "WC", "TOILET", "SHOWER", "BASIN", "SINK"]:
        if keyword in tokens:
            if keyword in {"ROOM", "WC", "TOILET", "SHOWER", "BASIN", "SINK"}:
                return keyword.title()
            return keyword.title()

    if any(token in {"BATH", "WC", "TOILET", "KITCHEN", "LIVING", "BED"} for token in tokens):
        match = next(token for token in tokens if token in {"BATH", "WC", "TOILET", "KITCHEN", "LIVING", "BED"})
        return match.title()

    if "ROOM" in cleaned.upper():
        return "Room"
    return cleaned


def _candidate_room_names(raw: str) -> list[str]:
    """Return ordered candidate labels from OCR so the strongest room match wins."""
    if not raw:
        return []
    cleaned = _normalise_room_label(raw)
    if not cleaned or cleaned == "Room":
        return ["Room"]

    # Keep the full label and a reduced token form so the strongest keyword is surfaced.
    pieces = [cleaned]
    for token in re.split(r"\s+", cleaned):
        if len(token) > 2:
            pieces.append(token)
    return pieces


def _fixture_kind(label: str) -> FixtureKind | None:
    up = label.upper()
    for key, kind in _FIXTURE_HINTS.items():
        if key in up:
            return kind
    return None


def _run_ocr_variants(img, crop_box) -> list[str]:
    """Try several OCR modes for small plan labels to reduce false text like 'mooЯ'."""
    try:
        import pytesseract
    except Exception:
        return []

    try:
        from PIL import Image, ImageFilter
    except Exception:
        return []

    crop = img.crop(crop_box)
    candidates: list[str] = []
    variants = []
    gray = crop.convert("L")
    for thresh in (160, 180, 200):
        bw = gray.point(lambda p: 255 if p > thresh else 0)
        bw = bw.filter(ImageFilter.MedianFilter())
        variants.append(bw)
    variants.append(gray)
    variants.append(gray.transpose(Image.FLIP_LEFT_RIGHT))

    for variant in variants:
        for psm in (7, 6, 10, 11):
            try:
                text = pytesseract.image_to_string(variant, config=f"--psm {psm} --oem 3")
            except Exception:
                continue
            if text and text.strip():
                candidates.append(text)
    return candidates


def _room_name_from_ocr_candidates(candidates: list[str]) -> str:
    """Choose the best room label from OCR text candidates.

    Returns an EMPTY string when nothing readable was found. The caller turns
    that into a numbered placeholder; returning "Room" here would make every
    unread room identical, which the confirmation screen cannot act on.
    """
    best = ""
    best_score = -1
    seen: set[str] = set()
    for raw in candidates:
        cleaned = _normalise_room_label(raw)
        if not cleaned or cleaned == "Room":
            continue
        if cleaned.upper() in seen:
            continue
        seen.add(cleaned.upper())
        score = 0
        for keyword in ["BEDROOM", "BATHROOM", "KITCHEN", "LIVING", "DINING",
                        "UTILITY", "LAUNDRY", "HALL", "CORRIDOR",
                        "BALCONY", "TERRACE", "GARAGE", "TOILET", "WC",
                        "SHOWER", "BASIN", "SINK"]:
            if keyword in cleaned.upper():
                score += 10
        if "ROOM" in cleaned.upper():
            score += 1
        if score > best_score:
            best_score = score
            best = cleaned
    if best_score < 0:
        return ""
    normalized = _best_room_name_from_text(best)
    return normalized if normalized and normalized != "Room" else best


def _infer_fixtures_for_label(label: str) -> list[tuple[FixtureKind, dict[str, float]]]:
    """Infer likely fixture demand implied by a room label.

    This is not guesswork in the sense of inventing arbitrary rooms: the room
    type itself already says it's a bathroom / utility / kitchen. We convert that
    architectural meaning into the fixture demand the MEP engine actually needs.
    """
    cleaned = (label or "").strip()
    if not cleaned:
        return []

    normalized = _normalise_room_label(cleaned).upper()
    out: list[tuple[FixtureKind, dict[str, float]]] = []

    if "WC" in normalized or "TOILET" in normalized:
        out.append((FixtureKind.WC, {"fixture_units": _FIXTURE_UNITS[FixtureKind.WC]}))
    if "BASIN" in normalized or "LAVATORY" in normalized or "SINK" in normalized or "TAP" in normalized or "FAUCET" in normalized or "MIXER" in normalized:
        out.append((FixtureKind.BASIN, {"fixture_units": _FIXTURE_UNITS[FixtureKind.BASIN]}))
    if "SHOWER" in normalized:
        out.append((FixtureKind.SHOWER, {"fixture_units": _FIXTURE_UNITS[FixtureKind.SHOWER]}))
    if "BATH" in normalized:
        out.append((FixtureKind.BATH, {"fixture_units": _FIXTURE_UNITS[FixtureKind.BATH]}))
    if "KITCHEN" in normalized and ("SINK" in normalized or "TAP" in normalized or "FAUCET" in normalized or "MIXER" in normalized):
        out.append((FixtureKind.KITCHEN_SINK, {"fixture_units": _FIXTURE_UNITS[FixtureKind.KITCHEN_SINK]}))
    if "LAUNDRY" in normalized or "WASHING" in normalized or "WASH" in normalized:
        out.append((FixtureKind.WASHING_MACHINE, {"fixture_units": _FIXTURE_UNITS[FixtureKind.WASHING_MACHINE]}))
    if "DRAIN" in normalized and "FLOOR" in normalized:
        out.append((FixtureKind.FLOOR_DRAIN, {"fixture_units": _FIXTURE_UNITS[FixtureKind.FLOOR_DRAIN]}))

    room_kind = _classify(cleaned)
    if not out:
        if room_kind == RoomType.BATHROOM:
            out.extend([
                (FixtureKind.WC, {"fixture_units": _FIXTURE_UNITS[FixtureKind.WC]}),
                (FixtureKind.BASIN, {"fixture_units": _FIXTURE_UNITS[FixtureKind.BASIN]}),
            ])
        elif room_kind == RoomType.KITCHEN:
            out.append((FixtureKind.KITCHEN_SINK, {"fixture_units": _FIXTURE_UNITS[FixtureKind.KITCHEN_SINK]}))
        elif room_kind == RoomType.UTILITY:
            out.append((FixtureKind.WASHING_MACHINE, {"fixture_units": _FIXTURE_UNITS[FixtureKind.WASHING_MACHINE]}))

    deduped: list[tuple[FixtureKind, dict[str, float]]] = []
    seen: set[FixtureKind] = set()
    for kind, props in out:
        if kind in seen:
            continue
        seen.add(kind)
        deduped.append((kind, props))
    return deduped


def _ocr_room_names(img, room_regions: list[list[tuple[int, int]]]) -> list[str]:
    """Best-effort OCR for room titles from a plan image.

    OCR is applied to each room crop, not to the whole page, so labels remain
    attached to their surrounding region rather than being confused with the
    drawing's other text or dimension annotations.

    Unknown titles come back as an EMPTY string, never a made-up name: the
    caller numbers those rooms instead.
    """
    if not room_regions:
        return []

    labels: list[str] = []
    width, height = img.size
    for region in room_regions:
        xs = [p[0] for p in region]
        ys = [p[1] for p in region]
        if not xs or not ys:
            labels.append("")
            continue
        x0 = max(0, min(xs) - 10)
        y0 = max(0, min(ys) - 10)
        x1 = min(width, max(xs) + 10)
        y1 = min(height, max(ys) + 10)
        crop_box = (x0, y0, x1, y1)
        candidates = _run_ocr_variants(img, crop_box)
        if not candidates:
            labels.append("")
            continue

        best = _room_name_from_ocr_candidates(candidates)
        labels.append(best)
    return labels


__all__ = ["ParseError", "UnsupportedFormat", "parse_upload", "parse_dxf",
           "parse_pdf", "parse_image", "calibrate_scale"]
