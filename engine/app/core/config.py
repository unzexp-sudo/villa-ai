"""Runtime settings, run limits and shared config constants (M1 core).

Reads ``os.environ`` for a few overrides; everything has a sensible default so
the app boots with zero configuration and zero external services.
"""

import os
from pathlib import Path

from pydantic import BaseModel, Field

from app.contract.enums import Country, DEFAULT_BANDS

# config.py lives at <workspace>/engine/app/core/config.py
WORKSPACE = Path(__file__).resolve().parents[3]
DEFAULT_DATA_DIR = str(WORKSPACE / "data")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _default_country() -> Country:
    raw = os.environ.get("VILLA_AI_DEFAULT_COUNTRY", Country.ID.value)
    try:
        return Country(raw)
    except ValueError:
        return Country.ID


class Settings(BaseModel):
    PRODUCT_NAME: str = "Villa AI"
    REQUIRE_LEAD_GATE: bool = Field(
        default_factory=lambda: _env_bool("VILLA_AI_REQUIRE_LEAD_GATE", False)
    )
    DEFAULT_COUNTRY: Country = Field(default_factory=_default_country)
    DATA_DIR: str = Field(
        default_factory=lambda: os.environ.get("VILLA_AI_DATA_DIR", DEFAULT_DATA_DIR)
    )
    CORS_ORIGINS: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]
    )
    MAX_FREE_RUNS: int = 3


class RunLimits(BaseModel):
    max_candidates_per_agent: int = 200
    max_resolver_loops: int = 6
    max_performance_loops: int = 3
    max_run_seconds: float = 120.0
    clearance_mm: float = 100.0


# Fire agent default: ON for apartments, OFF for villas unless explicitly
# enabled (MODULE_CONTRACT.md §0 decision 3).
FIRE_DEFAULT_BY_TYPE: dict[str, bool] = {"apartment": True, "villa": False}

# Free tier: 1 plan / 3 runs, enforced in config only (decision 5).
MAX_FREE_RUNS = 3

settings = Settings()


def get_limits() -> RunLimits:
    """Fresh RunLimits (cheap value object)."""
    return RunLimits()


__all__ = [
    "Settings", "RunLimits", "settings", "get_limits", "DEFAULT_BANDS",
    "FIRE_DEFAULT_BY_TYPE", "MAX_FREE_RUNS", "DEFAULT_DATA_DIR", "WORKSPACE",
]
