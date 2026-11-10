"""Agent registry + fire default (MODULE_CONTRACT.md §0 decision 3)."""

from __future__ import annotations

from app.contract import SYSTEM_ORDER, SystemKind

from .air import AirAgent
from .drainage import DrainageAgent
from .fire import FireAgent, fire_is_enabled
from .power import PowerAgent
from .water import WaterAgent

_MAP: dict[SystemKind, type] = {
    SystemKind.DRAINAGE: DrainageAgent,
    SystemKind.WATER: WaterAgent,
    SystemKind.AIR: AirAgent,
    SystemKind.POWER: PowerAgent,
    SystemKind.FIRE: FireAgent,
}


def get_agent(system: SystemKind) -> "SpecialistAgent":
    cls = _MAP.get(system)
    if cls is None:
        raise ValueError(f"no specialist agent for system {system!r}")
    return cls()


def enabled_systems(model) -> list[SystemKind]:
    """4 systems for villa, 5 for apartment (fire is optional, default OFF
    for villa). Returned in frozen SYSTEM_ORDER."""
    result: list[SystemKind] = []
    for sk in SYSTEM_ORDER:
        if sk == SystemKind.FIRE and not fire_is_enabled(model):
            continue
        result.append(sk)
    return result


__all__ = ["get_agent", "enabled_systems"]
