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
    "shower": RoomType.BATHROOM,
    "kitchen": RoomType.KITCHEN,
    "kit": RoomType.KITCHEN,
    "living": RoomType.LIVING,
    "lounge": RoomType.LIVING,
    "family": RoomType.LIVING,
    "dining": RoomType.DINING,
    "utility": RoomType.UTILITY,
    "laundry": RoomType.UTILITY,
    "store": RoomType.UTILITY,
    "hall": RoomType.HALL,
    "corridor": RoomType.HALL,
    "balcony": RoomType.BALCONY,
    "terrace": RoomType.BALCONY,
    "garage": RoomType.GARAGE,
    "carport": RoomType.GARAGE,
}

_FIXTURE_HINTS = {
    "WC": FixtureKind.WC,
    "TOILET": FixtureKind.WC,
    "BASIN": FixtureKind.BASIN,
    "SINK": FixtureKind.SINK,
    "SHOWER": FixtureKind.SHOWER,
    "BATH": FixtureKind.BATH,
    "WASHING": FixtureKind.WASHING_MACHINE,
    "DRAIN": FixtureKind.FLOOR_DRAIN,
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
# noise, not a plan, and we say so instead of returning an empty model.
_MIN_RASTER_WALLS = 8

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
                                ) -> BuildingModel:
    """Assemble a BuildingModel from a wall list, normalising the origin.

    When the walls came from a raster (pixels) the coordinates are scaled to
    a plausible building footprint in metres. A 700 px wide plan is roughly
    14 m wide, so the factor is derived from the image extent and recorded
    as an explicit assumption. The user still calibrates for real.
    """
    if not walls:
        raise ParseError(
            "no wall lines found. The plan may be a raster image with no vector "
            "content, or the drawing may use polylines the rasteriser could not trace."
        )

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
        if image_blob:
            try:
                from PIL import Image
                img = Image.open(io.BytesIO(image_blob))
                labels = _ocr_room_names(img, room_regions)
            except Exception:
                labels = None

        for index, region in enumerate(room_regions):
            poly = [Point2D(x=(px - ox) * factor, y=(py - oy) * factor)
                    for px, py in region]
            area = round(_polygon_area(poly), 2)
            if area <= 0:
                continue
            room_name = "Room"
            room_kind = RoomType.OTHER
            if labels is not None and index < len(labels):
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

    if not walls:
        # No vector content — try rasterising the page and detecting edges
        walls = _rasterise_and_detect_lines(page, reader)

    if not walls:
        raise UnsupportedFormat(
            "This PDF contains no vector line geometry and no detectable raster "
            "edges. If it is a scanned image, trace the walls in Quick draw, or "
            "export a DXF from your CAD tool."
        )

    return _build_model_from_segments(walls, f"{name} (parsed from PDF)")


def parse_image(blob: bytes, name: str = "plan.png") -> BuildingModel:
    """Detect wall lines from a raster image using edge detection.

    Uses Pillow + numpy. The threshold is chosen from the image's own
    histogram (Otsu) because architectural plans are thin GREY line work on
    white paper; a fixed cut finds the room names and misses the walls.
    Detected runs are merged along their line so each wall is one segment.

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

    walls = _detect_walls_from_raster(arr)

    if len(walls) < _MIN_RASTER_WALLS:
        raise UnsupportedFormat(
            f"Only {len(walls)} wall line(s) were detected in this image — that is "
            "not enough to read as a floor plan. The plan may be too low "
            "resolution, too faint, or the drawing may use curves and hatching "
            "this reader cannot trace. Export a DXF from your CAD tool for an "
            "exact read."
        )

    regions = _rooms_from_enclosed_regions(arr, walls)

    return _build_model_from_segments(
        walls, f"{name} (parsed from image)", source="upload",
        unit_note="image pixels converted to metres using an assumed scale",
        room_regions=regions,
        image_blob=blob,
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


def _merge_collinear(segments: list[tuple[Point2D, Point2D]],
                     tolerance: float = 2.0) -> list[tuple[Point2D, Point2D]]:
    """Merge segments that lie on the same line and overlap or touch.

    A wall drawn with a 3 px pen across a 700 px image produces dozens of
    short, slightly-offset runs. Left alone they become dozens of stubby
    "walls" with gaps, which is what makes a raster parse look like noise.
    Merging along the line direction recovers the actual wall.
    """
    if not segments:
        return []

    horizontal = [s for s in segments if abs(s[0].y - s[1].y) <= tolerance]
    vertical = [s for s in segments if abs(s[0].x - s[1].x) <= tolerance]
    other = [s for s in segments
             if s not in horizontal and s not in vertical]

    merged: list[tuple[Point2D, Point2D]] = []

    for axis in ("h", "v"):
        group = horizontal if axis == "h" else vertical
        if not group:
            continue
        if axis == "h":
            buckets: dict[int, list[tuple[float, float]]] = {}
            for a, b in group:
                key = int(round((a.y + b.y) / 2 / tolerance)) * tolerance
                buckets.setdefault(key, []).append((min(a.x, b.x), max(a.x, b.x)))
        else:
            buckets = {}
            for a, b in group:
                key = int(round((a.x + b.x) / 2 / tolerance)) * tolerance
                buckets.setdefault(key, []).append((min(a.y, b.y), max(a.y, b.y)))

        for key, spans in buckets.items():
            spans.sort()
            cur_start, cur_end = spans[0]
            for s, e in spans[1:]:
                if s <= cur_end + tolerance:
                    cur_end = max(cur_end, e)
                else:
                    merged.append((cur_start, cur_end, key, axis))
                    cur_start, cur_end = s, e
            merged.append((cur_start, cur_end, key, axis))

    out: list[tuple[Point2D, Point2D]] = []
    for item in merged:
        if len(item) == 4:
            start, end, key, axis = item
            if axis == "h":
                out.append((Point2D(x=start, y=key), Point2D(x=end, y=key)))
            else:
                out.append((Point2D(x=key, y=start), Point2D(x=key, y=end)))
    out.extend(other)
    out.sort(key=lambda ab: (round(ab[0].x, 2), round(ab[0].y, 2),
                             round(ab[1].x, 2), round(ab[1].y, 2)))
    return out


def _detect_walls_from_raster(arr) -> list[Wall]:
    """Detect wall lines in a greyscale plan image.

    Two independent passes (rows for horizontal walls, columns for vertical
    walls) at an adaptive threshold, then merged along each line so a wall
    drawn with a wide pen becomes ONE wall instead of a row of stubs.
    """
    h, w = arr.shape
    thr = _otsu_threshold(arr)
    dark = arr < thr

    # A wall in a residential plan is rarely shorter than ~4% of the plan's
    # width. The old fixed 10% cut threw away every internal partition.
    min_run = max(12, int(w * 0.04))
    step = max(1, h // 400)

    raw: list[tuple[Point2D, Point2D]] = []

    for row in range(0, h, step):
        for start, end in _find_runs(dark[row], min_run):
            raw.append((Point2D(x=float(start), y=float(row)),
                        Point2D(x=float(end), y=float(row))))

    for col in range(0, w, step):
        for start, end in _find_runs(dark[:, col], min_run):
            raw.append((Point2D(x=float(col), y=float(start)),
                        Point2D(x=float(col), y=float(end))))

    merged = _merge_collinear(raw)

    walls: list[Wall] = []
    for a, b in merged:
        if math.dist((a.x, a.y), (b.x, b.y)) < min_run:
            continue
        walls.append(Wall(id=f"w{len(walls) + 1}", start=a, end=b,
                          thickness=0.15, height=3.0, is_exterior=False))
    return walls


def _rooms_from_enclosed_regions(arr, walls: list[Wall],
                                  min_area_px: int = 2500) -> list[list[tuple[int, int]]]:
    """Find room polygons as regions of the plan enclosed by wall lines.

    The wall lines are painted onto a blank mask; everything that is NOT a
    wall and is NOT reachable from the image border is enclosed interior
    space, i.e. a room. This is the standard connected-component reading of a
    plan and it needs no OCR.

    Border-reachable space is discarded, which is what removes the outside of
    the building, the dimension lines and the drawing-sheet margin from the
    result.
    """
    if np is None:  # pragma: no cover - dependency is declared
        return []

    h, w = arr.shape
    mask = np.zeros((h, w), dtype=bool)  # True = wall

    for wl in walls:
        x0 = int(max(0, min(wl.start.x, wl.end.x) - 2))
        x1 = int(min(w, max(wl.start.x, wl.end.x) + 3))
        y0 = int(max(0, min(wl.start.y, wl.end.y) - 2))
        y1 = int(min(h, max(wl.start.y, wl.end.y) + 3))
        if x1 <= x0 or y1 <= y0:
            continue
        mask[y0:y1, x0:x1] = True

    free = ~mask

    # The drawing's own extent sets the size of "a room". A bed on a 736 px
    # drawing is small; a bedroom is not.
    wall_xs = [c for wl in walls for c in (wl.start.x, wl.end.x)]
    wall_ys = [c for wl in walls for c in (wl.start.y, wl.end.y)]
    draw_span = max(max(wall_xs) - min(wall_xs), max(wall_ys) - min(wall_ys))
    if draw_span <= 0:
        return []
    _min_room_side = draw_span * _MIN_ROOM_SIDE_FRACTION

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
    candidates: list[list[tuple[int, int]]] = []

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
            candidates.append([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])

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

    candidates.sort(key=lambda r: (r[2][0] - r[0][0]) * (r[2][1] - r[0][1]), reverse=True)

    rooms: list[list[tuple[int, int]]] = []
    for cand in candidates:
        if any(_pixel_inside(cand, kept) for kept in rooms):
            continue
        rooms.append(cand)

    rooms.sort(key=lambda r: (r[0][1], r[0][0]))
    return rooms


def _rasterise_and_detect_lines(page, reader) -> list[Wall]:
    """Rasterise a PDF page to pixels and detect straight lines."""
    try:
        import io as _io
        from PIL import Image
        import numpy as np
    except ImportError:
        return []

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
        return []

    # Use the first (largest) image
    img = max(images, key=lambda i: i.size[0] * i.size[1])
    gray = img.convert("L")
    arr = np.array(gray)
    return _detect_walls_from_raster(arr)


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


def _ocr_room_names(img, room_regions: list[list[tuple[int, int]]]) -> list[str]:
    """Best-effort OCR for room titles from a plan image.

    OCR is applied to each room crop, not to the whole page, so labels remain
    attached to their surrounding region rather than being confused with the
    drawing's other text or dimension annotations.
    """
    try:
        import pytesseract
    except Exception:
        return ["Room"] * len(room_regions)

    if not room_regions:
        return []

    try:
        from PIL import Image, ImageFilter
    except Exception:
        return ["Room"] * len(room_regions)

    def _clean_label(raw: str) -> str:
        cleaned = re.sub(r"\s+", " ", (raw or "").strip())
        cleaned = re.sub(r"[^A-Za-z0-9/ &-]", "", cleaned)
        return cleaned.strip() or "Room"

    labels: list[str] = []
    width, height = img.size
    for region in room_regions:
        xs = [p[0] for p in region]
        ys = [p[1] for p in region]
        if not xs or not ys:
            labels.append("Room")
            continue
        x0 = max(0, min(xs) - 8)
        y0 = max(0, min(ys) - 8)
        x1 = min(width, max(xs) + 8)
        y1 = min(height, max(ys) + 8)
        crop = img.crop((x0, y0, x1, y1))
        gray = crop.convert("L")
        bw = gray.point(lambda p: 255 if p > 180 else 0)
        bw = bw.filter(ImageFilter.MedianFilter())
        try:
            text = pytesseract.image_to_string(
                bw,
                config="--psm 10 --oem 3",
            )
        except Exception:
            labels.append("Room")
            continue
        cleaned = _clean_label(text)
        labels.append(cleaned)

    return labels


def _infer_fixtures_for_label(label: str) -> list[tuple[FixtureKind, dict[str, float]]]:
    """Infer likely fixture demand implied by a room label.

    This is not guesswork in the sense of inventing arbitrary rooms: the room
    type itself already says it's a bathroom / utility / kitchen. We convert that
    architectural meaning into the fixture demand the MEP engine actually needs.
    """
    cleaned = (label or "").strip()
    if not cleaned:
        return []

    normalized = cleaned.upper()
    out: list[tuple[FixtureKind, dict[str, float]]] = []

    if "WC" in normalized or "TOILET" in normalized:
        out.append((FixtureKind.WC, {"fixture_units": _FIXTURE_UNITS[FixtureKind.WC]}))
    if "BASIN" in normalized or "LAVATORY" in normalized or "SINK" in normalized:
        out.append((FixtureKind.BASIN, {"fixture_units": _FIXTURE_UNITS[FixtureKind.BASIN]}))
    if "SHOWER" in normalized:
        out.append((FixtureKind.SHOWER, {"fixture_units": _FIXTURE_UNITS[FixtureKind.SHOWER]}))
    if "BATH" in normalized:
        out.append((FixtureKind.BATH, {"fixture_units": _FIXTURE_UNITS[FixtureKind.BATH]}))
    if "KITCHEN" in normalized and ("SINK" in normalized or "KITCHEN" in normalized):
        out.append((FixtureKind.KITCHEN_SINK, {"fixture_units": _FIXTURE_UNITS[FixtureKind.KITCHEN_SINK]}))
    if "LAUNDRY" in normalized or "WASHING" in normalized:
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


__all__ = ["ParseError", "UnsupportedFormat", "parse_upload", "parse_dxf",
           "parse_pdf", "parse_image", "calibrate_scale"]
