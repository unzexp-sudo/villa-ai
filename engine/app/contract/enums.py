"""Frozen enums and shared visual / vertical constants (MODULE_CONTRACT.md §4).

Everything here is mirrored byte-for-byte in ``web/src/contract.ts``. Do not
hand-copy a second list of event names or system kinds.
"""

from enum import Enum


class SystemKind(str, Enum):
    DRAINAGE = "drainage"
    WATER = "water"
    AIR = "air"
    POWER = "power"
    FIRE = "fire"


class BuildingType(str, Enum):
    APARTMENT = "apartment"
    VILLA = "villa"


class Country(str, Enum):
    ID = "ID"
    VN = "VN"
    TH = "TH"


class RoomType(str, Enum):
    BEDROOM = "bedroom"
    BATHROOM = "bathroom"
    KITCHEN = "kitchen"
    LIVING = "living"
    DINING = "dining"
    UTILITY = "utility"
    HALL = "hall"
    BALCONY = "balcony"
    GARAGE = "garage"
    OTHER = "other"


class FixtureKind(str, Enum):
    WC = "wc"
    BASIN = "basin"
    SHOWER = "shower"
    SINK = "sink"
    WASHING_MACHINE = "washing_machine"
    FLOOR_DRAIN = "floor_drain"
    BATH = "bath"
    KITCHEN_SINK = "kitchen_sink"


class EquipmentKind(str, Enum):
    WATER_TANK = "water_tank"
    PUMP = "pump"
    WATER_HEATER = "water_heater"
    SEWER_POINT = "sewer_point"
    SEPTIC_TANK = "septic_tank"
    WATER_METER = "water_meter"
    ELECTRICAL_PANEL = "electrical_panel"
    AC_INDOOR = "ac_indoor"
    AC_OUTDOOR = "ac_outdoor"
    SHAFT = "shaft"
    RISER = "riser"
    DISTRIBUTION_BOARD = "distribution_board"
    SPRINKLER = "sprinkler"
    SMOKE_DETECTOR = "smoke_detector"
    EXHAUST_FAN = "exhaust_fan"


class AgentPhase(str, Enum):
    QUEUED = "queued"
    EXPLORING = "exploring"
    TESTING = "testing"
    COMMITTING = "committing"
    DONE = "done"
    FAILED = "failed"


class RuleStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    NOT_CHECKED = "not_checked"


class Severity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class ClashKind(str, Enum):
    HARD = "hard"
    CLEARANCE = "clearance"


# Canonical draw / processing order of the five systems.
SYSTEM_ORDER: list[SystemKind] = [
    SystemKind.DRAINAGE,
    SystemKind.WATER,
    SystemKind.AIR,
    SystemKind.POWER,
    SystemKind.FIRE,
]

# Frozen shared visual language. Exactly the five SystemKind colours.
# ``clash`` / ``resolver`` are overlay colours (not systems); the frozen
# contract lists them alongside SYSTEM_COLORS, so they are exposed separately
# here and merged into GET /api/config ``colors`` for the frontend.
SYSTEM_COLORS: dict[SystemKind, str] = {
    SystemKind.DRAINAGE: "#f97316",
    SystemKind.WATER: "#3b82f6",
    SystemKind.AIR: "#22d3ee",
    SystemKind.POWER: "#eab308",
    SystemKind.FIRE: "#e6379c",
}

# Red is reserved for clashes / failures. Fire uses magenta so fire routes can
# never be mistaken for a clash marker (see docs/UX_SPEC.md).
CLASH_COLOR = "#ff3b30"
RESOLVER_COLOR = "#a78bfa"
OVERLAY_COLORS: dict[str, str] = {
    "clash": CLASH_COLOR,
    "resolver": RESOLVER_COLOR,
}

# Default preferred height bands, in metres (centreline z). Soft hints for
# scoring only in v1 -- they do NOT forbid a route (MODULE_CONTRACT.md §1).
#
# Research-based vertical zoning:
# - Drainage: UNDERFLOOR / below slab. Gravity-led; needs continuous fall
#   to the sewer/septic outlet. Routing it in the ceiling was wrong.
# - Water: ceiling void. Pressurised supply; runs overhead and drops to
#   fixtures. Cold and hot share the same band but are separate branches.
# - Air: ceiling void, slightly below water to avoid crossing.
# - Power: ceiling void, above water (conduit flex, least constraint).
# - Fire: highest, near structural soffit (sprinkler head clearance).
DEFAULT_BANDS: dict[SystemKind, tuple[float, float]] = {
    SystemKind.DRAINAGE: (0.15, 0.45),
    SystemKind.WATER: (2.45, 2.65),
    SystemKind.AIR: (2.35, 2.50),
    SystemKind.POWER: (2.60, 2.75),
    SystemKind.FIRE: (2.90, 3.10),
}

__all__ = [
    "SystemKind", "BuildingType", "Country", "RoomType", "FixtureKind",
    "EquipmentKind", "AgentPhase", "RuleStatus", "Severity", "ClashKind",
    "SYSTEM_ORDER", "SYSTEM_COLORS", "OVERLAY_COLORS", "CLASH_COLOR",
    "RESOLVER_COLOR", "DEFAULT_BANDS",
]
