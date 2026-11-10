"""Building Model shapes (MODULE_CONTRACT.md §4).

Units: geometry in METRES, ``z`` metres above finished floor of level 0,
origin (0,0) at the lower-left of the plan bounding box. Component sizes carry
an explicit ``_mm`` suffix and are MILLIMETRES.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .enums import BuildingType, Country, EquipmentKind, FixtureKind, RoomType, SystemKind


class _Nested(BaseModel):
    """Nested models allow extra fields for forward compatibility."""

    model_config = ConfigDict(extra="allow")


class Point2D(_Nested):
    x: float
    y: float


class Point3D(_Nested):
    x: float
    y: float
    z: float


class Size3D(_Nested):
    length_mm: float
    width_mm: float
    height_mm: float


class Wall(_Nested):
    id: str
    start: Point2D
    end: Point2D
    thickness: float = 0.15  # metres
    height: float = 3.0      # metres
    is_exterior: bool = False


class Opening(_Nested):
    id: str
    wall_id: str
    at: float  # metres along wall centreline from start
    width: float
    kind: Literal["door", "window", "passage"]
    sill: float = 0.0
    head: float = 2.1


class Room(_Nested):
    id: str
    name: str
    type: RoomType
    polygon: list[Point2D]  # >= 3 points, closed implicitly, metres
    wet: bool = False
    area_m2: float = 0.0  # computed if 0
    ceiling_z: float = 2.7

    @field_validator("polygon")
    @classmethod
    def _at_least_three_points(cls, v: list[Point2D]) -> list[Point2D]:
        if len(v) < 3:
            raise ValueError("Room.polygon needs >= 3 points")
        return v


class Fixture(_Nested):
    id: str
    kind: FixtureKind
    at: Point2D
    room_id: str | None = None
    z: float = 0.0
    props: dict[str, Any] = Field(default_factory=dict)


class Equipment(_Nested):
    id: str
    kind: EquipmentKind
    at: Point2D
    room_id: str | None = None
    z: float = 0.0
    props: dict[str, Any] = Field(default_factory=dict)


class Shaft(_Nested):
    id: str
    polygon: list[Point2D]
    from_z: float = 0.0
    to_z: float = 3.0
    systems: list[SystemKind] = Field(default_factory=list)


class Level(_Nested):
    id: str
    name: str
    elevation: float = 0.0
    floor_to_floor: float = 3.0
    ceiling_void: float = 0.35
    walls: list[Wall] = Field(default_factory=list)
    openings: list[Opening] = Field(default_factory=list)
    rooms: list[Room] = Field(default_factory=list)
    fixtures: list[Fixture] = Field(default_factory=list)
    equipment: list[Equipment] = Field(default_factory=list)
    shafts: list[Shaft] = Field(default_factory=list)

    @model_validator(mode="after")
    def _has_geometry(self) -> "Level":
        # Fixtures may be sparse: a level is valid with rooms OR walls.
        if len(self.rooms) < 1 and len(self.walls) < 1:
            raise ValueError("Level needs >= 1 room OR >= 1 wall")
        return self


class Scale(_Nested):
    p1: Point2D
    p2: Point2D
    distance_m: float
    calibrated_by: Literal["user", "fixture"] = "user"

    @field_validator("distance_m")
    @classmethod
    def _positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("Scale.distance_m must be > 0")
        return v


class SiteConnections(_Nested):
    sewer_point: Point2D | None = None
    septic_point: Point2D | None = None
    water_meter: Point2D | None = None
    power_incomer: Point2D | None = None


class Assumption(_Nested):
    id: str
    field: str
    value: str
    reason: str
    source: Literal["default", "user", "skip", "agent"] = "default"
    created_at: float


class BuildingModel(BaseModel):
    """Top-level model. Extra fields are forbidden here (and only here)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    id: str
    name: str
    building_type: BuildingType = BuildingType.VILLA
    country: Country = Country.ID
    storeys: int = 1
    ceiling_height: float = 2.7
    levels: list[Level]
    scale: Scale | None = None
    site: SiteConnections = Field(default_factory=SiteConnections)
    assumptions: list[Assumption] = Field(default_factory=list)
    source: Literal["fixture", "upload", "quickdraw"] = "fixture"
    created_at: float
    updated_at: float

    @field_validator("levels")
    @classmethod
    def _at_least_one_level(cls, v: list[Level]) -> list[Level]:
        if len(v) < 1:
            raise ValueError("BuildingModel needs >= 1 level")
        return v

    # ---- Helpers (M1 implements, everyone uses) -------------------------

    @property
    def level0(self) -> Level:
        """First (and in v1 only) level."""
        return self.levels[0]

    def wet_rooms(self) -> list[Room]:
        """All rooms flagged wet across every level."""
        return [r for lvl in self.levels for r in lvl.rooms if r.wet]

    def fixtures_in(self, room_id: str) -> list[Fixture]:
        """Fixtures assigned to ``room_id`` across every level."""
        return [
            f for lvl in self.levels for f in lvl.fixtures if f.room_id == room_id
        ]

    def equipment_of(self, kind: EquipmentKind | str) -> list[Equipment]:
        """All equipment of ``kind`` across every level."""
        return [
            e for lvl in self.levels for e in lvl.equipment if e.kind == kind
        ]

    def bbox(self) -> tuple[float, float, float, float]:
        """Plan bounding box (minx, miny, maxx, maxy) from architectural
        geometry: walls, room polygons and shafts. Site-connection points and
        outdoor equipment are intentionally excluded (they may lie outside
        the footprint)."""
        xs: list[float] = []
        ys: list[float] = []

        def _add(p: Point2D) -> None:
            xs.append(p.x)
            ys.append(p.y)

        for lvl in self.levels:
            for w in lvl.walls:
                _add(w.start)
                _add(w.end)
            for r in lvl.rooms:
                for p in r.polygon:
                    _add(p)
            for s in lvl.shafts:
                for p in s.polygon:
                    _add(p)

        if not xs:  # defensive; Level validator guarantees geometry
            return (0.0, 0.0, 0.0, 0.0)
        return (min(xs), min(ys), max(xs), max(ys))

    def clone(self) -> "BuildingModel":
        """Deep copy via model_dump / model_validate. Agents must never mutate
        the input model; they clone first (MODULE_CONTRACT.md §7)."""
        return BuildingModel.model_validate(self.model_dump())


__all__ = [
    "Point2D", "Point3D", "Size3D", "Wall", "Opening", "Room", "Fixture",
    "Equipment", "Shaft", "Level", "Scale", "SiteConnections", "Assumption",
    "BuildingModel",
]
