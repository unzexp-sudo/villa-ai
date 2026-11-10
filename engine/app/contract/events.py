"""Event vocabulary and AgentEvent (MODULE_CONTRACT.md §4 / SSE protocol).

The event vocabulary is FROZEN and derived from ONE Python tuple. ``id:`` in the
SSE frame is the monotonic ``seq``; a stream ends ONLY on a terminal event.
"""

from typing import Literal

from pydantic import Field

from .building import _Nested
from .enums import AgentPhase
from .layers import Layer

# Single source of truth. Never hand-copy a second list.
EVENT_NAMES: tuple[str, ...] = (
    "run.started",
    "agent.status",
    "agent.progress",
    "candidate.flicker",
    "layer.committed",
    "merge.done",
    "clash.found",
    "clash.resolved",
    "code.progress",
    "feed.line",
    "run.completed",
    "run.failed",
)

TERMINAL_EVENTS: tuple[str, ...] = ("run.completed", "run.failed")


class AgentEvent(_Nested):
    run_id: str
    seq: int
    ts: float
    name: Literal[*EVENT_NAMES]  # emitted as SSE `event:`
    agent: str  # system name OR "orchestrator"/"fixer"/"codes"
    phase: AgentPhase = AgentPhase.QUEUED
    layouts_tested: int = 0
    best_score: float | None = None
    message: str
    metrics: dict[str, float] = Field(default_factory=dict)
    layer: Layer | None = None  # only on layer.committed


def is_terminal(name: str) -> bool:
    """True for the only two events that may end an SSE stream."""
    return name in TERMINAL_EVENTS


__all__ = ["EVENT_NAMES", "TERMINAL_EVENTS", "AgentEvent", "is_terminal"]
