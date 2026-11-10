"""Rules-based deterministic planner (MODULE_CONTRACT.md §4, plan §6).

Default planner: zero API key, zero network, fully deterministic. It reads the
BuildingModel (never mutates it) plus caller-supplied ``observations`` hints.
All fields are passed through ``validate_strategy`` before reaching the
toolkit ("no strategy, no run").
"""

from __future__ import annotations

from typing import Any

from app.contract import (
    DEFAULT_BANDS,
    BuildingModel,
    EquipmentKind,
    RoomType,
    SystemKind,
)

from .base import Strategy, validate_strategy

# plan §6 trunk rules.
_TRUNK_BY_SYSTEM: dict[SystemKind, str] = {
    SystemKind.AIR: "perimeter",
    SystemKind.POWER: "central",
    SystemKind.FIRE: "radial",
}
_WET_TRUNK = "wet_core"
_SCATTERED_TRUNK = "branch"

_PREFER = {
    SystemKind.DRAINAGE: 0.7,
    SystemKind.WATER: 0.5,
    SystemKind.AIR: 0.4,
    SystemKind.POWER: 0.5,
    SystemKind.FIRE: 0.3,
}


def _room_center_x(room) -> float:
    pts = room.polygon
    return sum(p.x for p in pts) / len(pts)


def _wet_clustered(model: BuildingModel) -> bool:
    """True when the wet rooms share one side / wet core of the plan."""
    wet = model.wet_rooms()
    if not wet:
        return False
    if len(wet) == 1:
        return True
    minx, _miny, maxx, _maxy = model.bbox()
    width = max(1e-6, maxx - minx)
    xs = [_room_center_x(r) for r in wet]
    return (max(xs) - min(xs)) <= 0.5 * width


def _stack_location(model: BuildingModel, system: SystemKind) -> str:
    """Derive the stack location from equipment / shaft / utility presence."""
    kinds = {e.kind for lvl in model.levels for e in lvl.equipment}
    has_shaft = (
        any(bool(lvl.shafts) for lvl in model.levels)
        or EquipmentKind.SHAFT in kinds
        or EquipmentKind.RISER in kinds
    )
    has_utility = any(
        r.type == RoomType.UTILITY for lvl in model.levels for r in lvl.rooms
    )
    has_exterior = bool(
        kinds
        & {
            EquipmentKind.AC_OUTDOOR,
            EquipmentKind.SEWER_POINT,
            EquipmentKind.SEPTIC_TANK,
            EquipmentKind.WATER_TANK,
            EquipmentKind.WATER_METER,
        }
    )
    if system == SystemKind.AIR:
        return "exterior_wall" if has_exterior else "wet_core"
    if system == SystemKind.FIRE:
        return "shaft" if has_shaft else "wet_core"
    if has_shaft:
        return "shaft"
    if has_utility:
        return "utility"
    if has_exterior and system in (SystemKind.DRAINAGE, SystemKind.WATER):
        return "exterior_wall"
    return "wet_core"


class DeterministicPlanner:
    """Structurally implements the frozen ``Planner`` Protocol (duck typing)."""

    def choose_strategy(
        self,
        system: SystemKind,
        model: BuildingModel,
        observations: dict[str, Any] | None,
    ) -> Strategy:
        obs = dict(observations or {})
        clustered = bool(obs.get("wet_clustered", _wet_clustered(model)))

        if system in (SystemKind.DRAINAGE, SystemKind.WATER):
            trunk = _WET_TRUNK if clustered else _SCATTERED_TRUNK
        else:
            trunk = _TRUNK_BY_SYSTEM[system]

        lo, hi = DEFAULT_BANDS[system]
        raw: dict[str, Any] = {
            "system": system.value,
            "trunk": trunk,
            "stack_location": _stack_location(model, system),
            "z_band": (float(lo), float(hi)),
            "max_bends": 4,
            "prefer_short_runs": _PREFER[system],
            "params": {
                "elbow": "x"
                if system
                in (SystemKind.DRAINAGE, SystemKind.WATER, SystemKind.FIRE)
                else "y",
                "wet_clustered": clustered,
            },
        }
        strategy = validate_strategy(raw)
        if strategy is None:  # pragma: no cover - our own dict is always valid
            strategy = Strategy(**raw)
        return strategy


__all__ = ["DeterministicPlanner"]
