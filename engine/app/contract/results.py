"""Run result / code-report shapes (MODULE_CONTRACT.md §4)."""

from enum import Enum
from typing import Literal

from pydantic import Field, model_validator

from .building import Assumption, Point2D, Point3D, _Nested
from .enums import ClashKind, Country, EquipmentKind, RuleStatus, Severity, SystemKind
from .events import AgentEvent
from .layers import Layer


class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"


class Clash(_Nested):
    id: str
    kind: ClashKind
    a: str
    b: str  # element ids (Segment.id or Device.id)
    system_a: SystemKind
    system_b: SystemKind
    at: Point3D
    distance_mm: float
    resolved: bool = False
    fix: str | None = None


class RuleOutcome(_Nested):
    rule_id: str
    status: RuleStatus
    detail: str
    evidence: list[str] = Field(default_factory=list)
    severity: Severity = Severity.INFO


class CodeReport(_Nested):
    country: Country
    rule_pack_version: str
    total: int
    passed: int
    failed: int
    not_checked: int
    outcomes: list[RuleOutcome]
    # Computed below; kept as a real field so the shape matches the frozen
    # contract, but its value is ALWAYS forced from passed/total.
    headline: str = ""

    @model_validator(mode="before")
    @classmethod
    def _force_headline(cls, data):
        if isinstance(data, dict) and "passed" in data and "total" in data:
            data = dict(data)
            data["headline"] = (
                f"passed {data['passed']} of {data['total']} automated checks"
            )
        return data


class MissingItem(_Nested):
    id: str
    kind: EquipmentKind | Literal["ceiling_height", "building_type", "storeys"]
    label: str
    prompt: str
    severity: Severity
    system: SystemKind | None = None
    suggested_at: Point2D | None = None
    snap: Literal["wall", "corner", "wet_room", "outside", "roof", "any"]
    options: list[Literal["drag", "suggest", "skip"]] = ["drag", "suggest", "skip"]


class RunResult(_Nested):
    run_id: str
    model_id: str
    status: RunStatus
    country: Country
    enabled_systems: list[SystemKind]
    layers: dict[SystemKind, Layer] = Field(default_factory=dict)
    clashes_before: list[Clash] = Field(default_factory=list)
    clashes_after: list[Clash] = Field(default_factory=list)
    code: CodeReport | None = None
    events: list[AgentEvent] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    metrics: dict[str, float] = Field(default_factory=dict)
    started_at: float
    finished_at: float | None = None
    error: str | None = None


__all__ = [
    "RunStatus", "Clash", "RuleOutcome", "CodeReport", "MissingItem",
    "RunResult",
]
