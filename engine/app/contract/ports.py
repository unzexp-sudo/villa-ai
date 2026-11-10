"""Structural Protocols (MODULE_CONTRACT.md §4).

Implement these; do NOT subclass them. Every external dependency sits behind a
Protocol so Celery / Postgres / an LLM planner can be swapped in without
touching the engine. ``Strategy`` and ``LayoutSpec`` are owned by sibling
modules, so they are referenced as strings (never imported here at runtime).
"""

from typing import Any, Callable, Iterator, Protocol, runtime_checkable

from .building import BuildingModel
from .enums import SystemKind
from .events import AgentEvent
from .layers import Layer
from .results import RunResult


@runtime_checkable
class Emitter(Protocol):
    def emit(self, event: AgentEvent) -> None: ...

    def feed(self, run_id: str, agent: str, text: str) -> None: ...


@runtime_checkable
class RunStore(Protocol):
    def save_model(self, m: BuildingModel) -> None: ...

    def get_model(self, model_id: str) -> BuildingModel | None: ...

    def save_run(self, r: RunResult) -> None: ...

    def get_run(self, run_id: str) -> RunResult | None: ...

    def append_event(self, run_id: str, e: AgentEvent) -> None: ...

    def events_since(self, run_id: str, seq: int) -> list[AgentEvent]: ...

    def delete_model(self, model_id: str) -> None: ...  # privacy promise is real


@runtime_checkable
class Planner(Protocol):
    def choose_strategy(
        self,
        system: SystemKind,
        model: BuildingModel,
        observations: dict[str, Any],
    ) -> "Strategy": ...


@runtime_checkable
class JobRunner(Protocol):
    def run_parallel(self, jobs: list[Callable[[], Layer]]) -> list[Layer]: ...


@runtime_checkable
class Toolkit(Protocol):
    def candidates(self, spec: "LayoutSpec") -> Iterator[Layer]: ...

    def score(self, layer: Layer, model: BuildingModel) -> float: ...


__all__ = ["Emitter", "RunStore", "Planner", "JobRunner", "Toolkit"]
