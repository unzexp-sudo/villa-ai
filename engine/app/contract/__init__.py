"""Frozen data contract for Villa AI (MODULE_CONTRACT.md §4).

Re-exports are ordered by dependency (enums -> building -> layers -> events ->
results -> ports) so all public shapes are importable directly, e.g.::

    from app.contract import BuildingModel, SystemKind, RunResult, AgentEvent
"""

from .enums import (
    AgentPhase,
    BuildingType,
    CLASH_COLOR,
    ClashKind,
    Country,
    DEFAULT_BANDS,
    EquipmentKind,
    FixtureKind,
    OVERLAY_COLORS,
    RESOLVER_COLOR,
    RoomType,
    RuleStatus,
    Severity,
    SYSTEM_COLORS,
    SYSTEM_ORDER,
    SystemKind,
)
from .building import (
    Assumption,
    BuildingModel,
    Equipment,
    Fixture,
    Level,
    Opening,
    Point2D,
    Point3D,
    Room,
    Scale,
    Shaft,
    SiteConnections,
    Size3D,
    Wall,
)
from .layers import Device, Layer, Segment
from .events import AgentEvent, EVENT_NAMES, TERMINAL_EVENTS, is_terminal
from .results import (
    Clash,
    CodeReport,
    MissingItem,
    RuleOutcome,
    RunResult,
    RunStatus,
)
from .ports import Emitter, JobRunner, Planner, RunStore, Toolkit

__all__ = [
    # enums
    "AgentPhase", "BuildingType", "ClashKind", "Country", "EquipmentKind",
    "FixtureKind", "RoomType", "RuleStatus", "Severity", "SystemKind",
    "SYSTEM_COLORS", "SYSTEM_ORDER", "DEFAULT_BANDS", "OVERLAY_COLORS",
    "CLASH_COLOR", "RESOLVER_COLOR",
    # building
    "Point2D", "Point3D", "Size3D", "Wall", "Opening", "Room", "Fixture",
    "Equipment", "Shaft", "Level", "Scale", "SiteConnections", "Assumption",
    "BuildingModel",
    # layers
    "Segment", "Device", "Layer",
    # events
    "EVENT_NAMES", "TERMINAL_EVENTS", "AgentEvent", "is_terminal",
    # results
    "RunStatus", "Clash", "RuleOutcome", "CodeReport", "MissingItem",
    "RunResult",
    # ports
    "Emitter", "RunStore", "Planner", "JobRunner", "Toolkit",
]
