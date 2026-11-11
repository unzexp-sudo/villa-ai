"""Parse quality gate: is this model usable for design, and if not, why.

A plan reader that silently returns a half-empty model is worse than one that
refuses. This module gives two verdicts, and the difference matters:

* ``reject`` — the file is not a floor plan at all, or was read so poorly
  that a design run on it would be meaningless. The upload is refused with a
  message that names the cause.
* ``warn`` — the file IS a floor plan, but parts of it were not recovered
  (no rooms, no scale, no fixtures). The model is accepted and the gaps are
  surfaced in the confirmation screen so the user can see and correct them.

The rule is deliberately conservative in the direction of refusing: a design
run over a misread plan produces confidently wrong numbers, and the user has
no way to tell the difference.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.contract.building import BuildingModel

# A floor plan of a dwelling has far more wall lines than this. Below it the
# read is noise (a logo, a title block, a hatched pattern).
MIN_PLAUSIBLE_WALLS = 8

# A dwelling plan has several rooms. Below this the room pass found nothing
# usable, which usually means the door gaps broke the flood fill.
MIN_PLAUSIBLE_ROOMS = 1

# The building footprint, in metres, must be within a sane range for a
# villa. A plan read in pixels without scaling lands far outside this.
MIN_FOOTPRINT_M = 3.0
MAX_FOOTPRINT_M = 120.0


@dataclass
class ParseVerdict:
    """The outcome of reading a file into a model."""

    usable: bool
    reject_reason: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def reason(self) -> str | None:
        return self.reject_reason


def assess(model: BuildingModel) -> ParseVerdict:
    """Judge whether a parsed model can carry a design run."""
    level = model.level0
    walls = len(level.walls)
    rooms = len(level.rooms)

    if walls < MIN_PLAUSIBLE_WALLS:
        return ParseVerdict(
            usable=False,
            reject_reason=(
                f"Only {walls} wall line(s) were found in this file, which is too "
                "few to be a floor plan. If this is a raster image or a scanned "
                "PDF, export a DXF from your CAD tool for an exact read. If it "
                "is a DXF, make sure walls are drawn as LINE or LWPOLYLINE "
                "entities in model space."
            ),
        )

    try:
        minx, miny, maxx, maxy = model.bbox()
        span = max(maxx - minx, maxy - miny)
    except Exception:
        span = 0.0

    if span <= 0:
        return ParseVerdict(
            usable=False,
            reject_reason=(
                "The plan has no extent — every wall collapsed to a single "
                "point. The file's geometry could not be read."
            ),
        )

    if span < MIN_FOOTPRINT_M or span > MAX_FOOTPRINT_M:
        return ParseVerdict(
            usable=False,
            reject_reason=(
                f"The plan measures {span:.1f} m across, which is outside the "
                f"{MIN_FOOTPRINT_M:.0f}–{MAX_FOOTPRINT_M:.0f} m range a "
                "building can occupy. The drawing's scale was probably not "
                "applied. Export a DXF in metres, or upload a PDF that carries "
                "its real dimensions."
            ),
        )

    warnings: list[str] = []
    if rooms < MIN_PLAUSIBLE_ROOMS:
        warnings.append(
            "No rooms were recovered from this plan. The wall lines were read, "
            "but the enclosed spaces could not be separated — usually because "
            "door gaps are open in the drawing. Room-based routing will fall "
            "back to the wall geometry."
        )
    if model.scale is None:
        warnings.append(
            "The scale is not calibrated. The drawing was read as an "
            "approximate footprint; calibrate two points to a known distance "
            "before treating any pipe size as final."
        )
    if not level.fixtures:
        warnings.append(
            "No fixtures were found. Drainage and water routes will be sized "
            "from the room areas rather than from real fixture demand."
        )

    return ParseVerdict(usable=True, warnings=warnings)


__all__ = ["ParseVerdict", "assess", "MIN_PLAUSIBLE_WALLS", "MIN_PLAUSIBLE_ROOMS"]
