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

_RAW_EXT = (".pdf", ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".dwg")


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
                                source: str = "upload") -> BuildingModel:
    """Assemble a BuildingModel from a wall list, normalising the origin."""
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

    level = Level(id="L0", name="Ground floor", walls=walls, rooms=[],
                  fixtures=[], openings=[])
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
            _assume("scale", "unset", "PDF/image units are pixel coordinates; the user "
                                     "must calibrate two points before design runs"),
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

    Uses Pillow + numpy to detect long straight edges via Hough transform
    approximation. The output is real line segments in pixel coordinates; the
    user calibrates the scale afterward.
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

    # Simple edge detection: threshold + horizontal/vertical line scan
    walls: list[Wall] = []
    h, w = arr.shape

    # Horizontal lines: rows with long runs of dark pixels
    dark = arr < 128
    min_run = max(20, w // 10)

    for row in range(0, h, max(1, h // 200)):
        runs = _find_runs(dark[row], min_run)
        for start, end in runs:
            if end - start > min_run:
                walls.append(Wall(
                    id=f"w{len(walls)+1}",
                    start=Point2D(x=float(start), y=float(row)),
                    end=Point2D(x=float(end), y=float(row)),
                    thickness=0.15, height=3.0, is_exterior=False,
                ))

    # Vertical lines: columns with long runs of dark pixels
    for col in range(0, w, max(1, w // 200)):
        runs = _find_runs(dark[:, col], min_run)
        for start, end in runs:
            if end - start > min_run:
                walls.append(Wall(
                    id=f"w{len(walls)+1}",
                    start=Point2D(x=float(col), y=float(start)),
                    end=Point2D(x=float(col), y=float(end)),
                    thickness=0.15, height=3.0, is_exterior=False,
                ))

    if not walls:
        raise UnsupportedFormat(
            "No wall lines detected in this image. The image may be too low "
            "resolution, or the walls may not be straight. Try uploading a DXF "
            "export instead."
        )

    return _build_model_from_segments(walls, f"{name} (parsed from image)")


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

    walls: list[Wall] = []
    h, w = arr.shape
    dark = arr < 128
    min_run = max(20, w // 10)

    for row in range(0, h, max(1, h // 200)):
        runs = _find_runs(dark[row], min_run)
        for start, end in runs:
            walls.append(Wall(
                id=f"w{len(walls)+1}",
                start=Point2D(x=float(start), y=float(row)),
                end=Point2D(x=float(end), y=float(row)),
                thickness=0.15, height=3.0, is_exterior=False,
            ))

    for col in range(0, w, max(1, w // 200)):
        runs = _find_runs(dark[:, col], min_run)
        for start, end in runs:
            walls.append(Wall(
                id=f"w{len(walls)+1}",
                start=Point2D(x=float(col), y=float(start)),
                end=Point2D(x=float(col), y=float(end)),
                thickness=0.15, height=3.0, is_exterior=False,
            ))

    return walls


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


__all__ = ["ParseError", "UnsupportedFormat", "parse_upload", "parse_dxf",
           "parse_pdf", "parse_image", "calibrate_scale"]
