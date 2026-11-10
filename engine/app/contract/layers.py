"""MEP layer shapes (MODULE_CONTRACT.md §4).

A Layer is one system's committed preliminary design. Component sizes are in
MILLIMETRES; polyline centreline coordinates are in METRES (z above FFL).
"""

from typing import Any, Literal

from pydantic import ConfigDict, Field, field_validator

from .building import Point3D, Size3D, _Nested
from .enums import SystemKind


class Segment(_Nested):
    id: str
    system: SystemKind
    kind: Literal["pipe", "duct", "cable", "conduit", "refrigerant", "sprinkler_pipe"]
    polyline: list[Point3D]  # >= 2, ordered source -> terminal
    diameter_mm: float | None = None
    width_mm: float | None = None
    height_mm: float | None = None
    slope_pct: float = 0.0
    material: str = ""
    meta: dict[str, Any] = Field(default_factory=dict)

    @field_validator("polyline")
    @classmethod
    def _at_least_two_points(cls, v: list[Point3D]) -> list[Point3D]:
        if len(v) < 2:
            raise ValueError("Segment.polyline needs >= 2 points")
        return v


class Device(_Nested):
    id: str
    system: SystemKind
    kind: str
    at: Point3D
    size: Size3D
    meta: dict[str, Any] = Field(default_factory=dict)


class Layer(_Nested):
    model_config = ConfigDict(extra="allow")

    system: SystemKind
    agent: str
    status: Literal["pending", "running", "done", "failed"] = "pending"
    segments: list[Segment] = Field(default_factory=list)
    devices: list[Device] = Field(default_factory=list)
    metrics: dict[str, float] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    strategy: dict[str, Any] = Field(default_factory=dict)


__all__ = ["Segment", "Device", "Layer"]
