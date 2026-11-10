"""M3 PLANNER — LLM strategy selection over a deterministic geometry toolkit.

``get_planner()`` returns a ``DeterministicPlanner`` by default (no key, no
network, fully deterministic). With ``use_api=True`` (or VILLA_AI_USE_LLM=1)
it returns a ``FallbackPlanner`` which tries ``ApiPlanner`` first and, on a
None / invalid LLM output, records an ``Assumption`` with reason
``llm_output_rejected`` on the model and falls back to deterministic.
No strategy, no run: unvalidated values never reach the toolkit.
"""

from __future__ import annotations

import os
import time
from typing import Any

from app.contract import Assumption, SystemKind

from .api import ApiPlanner
from .base import Planner, Strategy, validate_strategy
from .deterministic import DeterministicPlanner


def _record_llm_rejected(model: Any, system: SystemKind) -> None:
    """Append a traceability Assumption to ``model``. Callers pass a clone so
    the caller's input BuildingModel is never mutated (§7)."""
    val = getattr(system, "value", str(system))
    try:
        model.assumptions.append(
            Assumption(
                id=f"llm-output-rejected-{val}",
                field=f"strategy.{val}",
                value="deterministic_fallback",
                reason="llm_output_rejected",
                source="agent",
                created_at=time.time(),
            )
        )
    except Exception:
        pass  # traceability must never break a run


class FallbackPlanner:
    """Duck-typed Planner: validated LLM output first, deterministic fallback."""

    def __init__(self, api: ApiPlanner, deterministic: DeterministicPlanner):
        self.api = api
        self.deterministic = deterministic

    def choose_strategy(
        self,
        system: SystemKind,
        model: Any,
        observations: dict[str, Any] | None,
    ) -> Strategy:
        candidate = None
        try:
            candidate = self.api.choose_strategy(system, model, observations)
        except Exception:
            candidate = None
        # Re-validate defensively: an unvalidated value must never reach M2.
        strategy = validate_strategy(candidate) if candidate is not None else None
        if strategy is None or strategy.system != system:
            _record_llm_rejected(model, system)
            return self.deterministic.choose_strategy(system, model, observations)
        return strategy


def get_planner(use_api: bool | None = None) -> Planner:
    """``DeterministicPlanner`` by default; opt-in LLM with deterministic
    fallback. The default path performs no network call and needs no key."""
    deterministic = DeterministicPlanner()
    if use_api is None:
        use_api = os.environ.get("VILLA_AI_USE_LLM", "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
    if not use_api:
        return deterministic
    return FallbackPlanner(ApiPlanner(), deterministic)


__all__ = [
    "Strategy",
    "validate_strategy",
    "Planner",
    "DeterministicPlanner",
    "ApiPlanner",
    "FallbackPlanner",
    "get_planner",
]
