"""FROZEN additive interior scene shapes (docs/INTERIOR_MODULE_CONTRACT.md §4).

Read-only projection of ``BuildingModel``. This module is ORCHESTRATOR-owned and
must not be edited by I1/I2/I3/I4. It changes nothing in the MEP contract: no
``SystemKind``, no ``EVENT_NAMES``, no ``RunResult``, no SSE events.

Units: geometry in METRES. ``+z`` is up (metres above finished floor level 0),
``+x`` right and ``+y`` up on the plan. All shapes forbid extra fields so the
TS mirror in ``web/src/interior/types.ts`` stays byte-for-byte derivable.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

DISCLAIMER = (
    "Visual model only, not for construction. "
    "Preliminary design; licensed engineer review required."
)
EYE_HEIGHT_M = 1.6
SCHEMA_VERSION = "1.0"


class _Shape(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Pt2(_Shape):
    x: float
    y: float


class Pt3(_Shape):
    x: float
    y: float
    z: float


class WallSegment(_Shape):
    """One solid box of a wall after openings are cut out.

    ``p1`` / ``p2`` are centreline endpoints in PLAN space (not 3D world space).
    ``z0`` / ``z1`` are the vertical extent, with ``z0 < z1``.
    """

    p1: Pt2
    p2: Pt2
    z0: float
    z1: float


class SceneWall(_Shape):
    id: str
    start: Pt2
    end: Pt2
    thickness: float
    height: float
    is_exterior: bool
    angle_rad: float  # atan2(dy, dx) of start -> end
    segments: list[WallSegment]  # solid pieces only; never decorative overlays


class SceneOpening(_Shape):
    id: str
    wall_id: str
    kind: Literal["door", "window", "passage"]
    at: float
    width: float
    sill: float
    head: float
    center: Pt3  # mid-span point at z = (sill + head) / 2
    angle_rad: float


class SceneRoom(_Shape):
    id: str
    name: str
    type: str  # RoomType's lowercase value
    polygon: list[Pt2]
    area_m2: float
    ceiling_z: float
    centroid: Pt2
    eye: Pt3  # centroid at EYE_HEIGHT_M


class InteriorScene(_Shape):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    model_id: str
    name: str
    units: Literal["metres"] = "metres"
    bbox: tuple[float, float, float, float]  # [min_x, min_y, max_x, max_y]
    ceiling_height: float
    walls: list[SceneWall]
    openings: list[SceneOpening]
    rooms: list[SceneRoom]
    disclaimer: str = DISCLAIMER
    generated_by: Literal["deterministic"] = "deterministic"


__all__ = [
    "DISCLAIMER",
    "EYE_HEIGHT_M",
    "SCHEMA_VERSION",
    "Pt2",
    "Pt3",
    "WallSegment",
    "SceneWall",
    "SceneOpening",
    "SceneRoom",
    "InteriorScene",
]
