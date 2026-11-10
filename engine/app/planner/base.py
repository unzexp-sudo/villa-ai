"""Strategy schema + validation (MODULE_CONTRACT.md §4, plan §6).

The LLM chooses *strategy and parameters*; the deterministic toolkit (M2)
turns that into candidate layouts and scores them. Nothing reaches the toolkit
until it has passed :func:`validate_strategy` -- an out-of-range or malformed
value returns ``None`` (never raises) so the caller can fall back safely.
"""

from __future__ import annotations

import json
import math
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.contract import Planner, SystemKind  # frozen Planner Protocol: implement
                                              # it by duck typing, do NOT subclass

# Trunk / stack enums are CLOSED (frozen contract §4).
_TRUNKS = ("perimeter", "central", "wet_core", "radial", "branch")
_STACKS = ("wet_core", "exterior_wall", "utility", "shaft")


class Strategy(BaseModel):
    system: SystemKind
    trunk: Literal["perimeter", "central", "wet_core", "radial", "branch"]
    stack_location: Literal["wet_core", "exterior_wall", "utility", "shaft"]
    z_band: tuple[float, float]
    max_bends: int = Field(default=4, ge=0, le=64)
    prefer_short_runs: float = Field(default=0.5, ge=0.0, le=1.0)
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("z_band")
    @classmethod
    def _valid_band(cls, v: tuple[float, float]) -> tuple[float, float]:
        lo, hi = float(v[0]), float(v[1])
        if not (math.isfinite(lo) and math.isfinite(hi)) or lo > hi:
            raise ValueError("z_band must be finite with lo <= hi")
        return (lo, hi)

    @field_validator("prefer_short_runs")
    @classmethod
    def _valid_weight(cls, v: float) -> float:
        f = float(v)
        if not math.isfinite(f) or not (0.0 <= f <= 1.0):
            raise ValueError("prefer_short_runs must be in [0, 1]")
        return f


def validate_strategy(raw: Any) -> "Strategy | None":
    """Return a validated ``Strategy`` or ``None`` on ANY invalid /
    out-of-range value. Accepts a dict, a JSON string, or an existing
    ``Strategy``; never raises.
    """
    if raw is None:
        return None
    if isinstance(raw, (str, bytes, bytearray)):
        try:
            raw = json.loads(raw)
        except Exception:
            return None
    try:
        return Strategy.model_validate(raw)
    except Exception:
        return None


__all__ = ["Strategy", "validate_strategy", "Planner"]
