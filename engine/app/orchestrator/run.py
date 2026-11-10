"""M4 ORCHESTRATOR — the run pipeline (plan §6).

``start_run`` persists a RUNNING RunResult and schedules the full pipeline on a
background ThreadPoolExecutor, returning immediately. ``execute_run`` runs the
pipeline synchronously (in that worker, or directly in tests):

    start -> resolve systems -> 5 agents in parallel -> merge -> clash detect
    -> clash resolver -> performance loop -> code check -> package -> terminal.

EXACTLY ONE terminal event (``run.completed`` / ``run.failed``) is emitted LAST,
even on any exception. A missing terminal event makes the browser SSE client
reconnect forever (MODULE_CONTRACT.md §4 / §8 gap 1). Every numeric value in an
event is computed from real data; sibling modules that may not have landed yet
are imported lazily inside function bodies.
"""

from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

from app.contract import (
    SYSTEM_ORDER,
    AgentEvent,
    AgentPhase,
    BuildingModel,
    CodeReport,
    Country,
    RuleOutcome,
    RuleStatus,
    RunResult,
    RunStatus,
    Severity,
    SystemKind,
)

from .clash import detect_clashes
from .merge import element_count, merge_layers
from .performance import run_performance_loop
from .resolver import refresh_distances, resolve_clashes

# Keep each agent's exploration bounded so the <10 s / max_run_seconds budget
# is honoured. The four pinned RunLimits (resolver/performance/run/clearance)
# are used verbatim; only the per-agent candidate cap is tightened here. 6 is
# enough to produce real, scored layouts (and enough crossings to exercise the
# merge/clash/resolve pipeline) while a full villa+apartment run stays <10 s.
_AGENT_CANDIDATE_BUDGET = 6

_RUN_EXECUTOR: ThreadPoolExecutor | None = None
_EXEC_LOCK = threading.Lock()


@dataclass
class RunContext:
    """Consumed by M3 agents (frozen core fields + M4 convenience fields)."""

    run_id: str
    model: BuildingModel
    country: Country
    limits: Any
    seed: int = 42
    enabled_systems: list[SystemKind] | None = None
    emitter: Any = None
    store: Any = None


def _run_executor() -> ThreadPoolExecutor:
    """Persistent executor for start_run (must NOT block the caller)."""
    global _RUN_EXECUTOR
    with _EXEC_LOCK:
        if _RUN_EXECUTOR is None:
            _RUN_EXECUTOR = ThreadPoolExecutor(
                max_workers=4, thread_name_prefix="villa-run"
            )
        return _RUN_EXECUTOR


def _event(run_id: str, name: str, agent: str, message: str, *,
           metrics: dict[str, float] | None = None,
           phase: AgentPhase = AgentPhase.QUEUED) -> AgentEvent:
    """seq=0 / ts=0 — EventBus assigns the real, monotonic values."""
    return AgentEvent(
        run_id=run_id, seq=0, ts=0, name=name, agent=agent, phase=phase,
        message=message, metrics=dict(metrics or {}),
    )


def _check_deadline(start_ts: float, limits) -> None:
    """Respect ``RunLimits.max_run_seconds`` at the pipeline stage boundaries.

    If the budget is already blown, raise; the exception flows straight into
    ``execute_run``'s ``except`` branch, which STILL emits the single
    ``run.failed`` terminal event. Honouring the wall-clock budget therefore
    never breaks the SSE terminal-event invariant.
    """
    budget = float(getattr(limits, "max_run_seconds", 0) or 0)
    if budget > 0 and (time.time() - start_ts) > budget:
        raise RuntimeError(f"run exceeded max_run_seconds ({budget:.1f}s)")


def _resolve_enabled(model, override) -> list[SystemKind]:
    """Fire OFF for villa, ON for apartment (registry), honouring an override.

    ``override`` is the explicit ``enabled`` list (or the list already recorded
    on the RunResult). The registry is consulted on every call so the fire
    default is never silently re-interpreted.
    """
    from app.agents.registry import enabled_systems as _enabled
    base = list(_enabled(model))
    if override is None:
        return base
    norm: set[SystemKind] = set()
    for s in override:
        norm.add(s if isinstance(s, SystemKind) else SystemKind(s))
    return [s for s in SYSTEM_ORDER if s in norm]


def _plan_one(system: SystemKind, model, ctx: RunContext, emitter) -> Any:
    """One agent job. get_agent is imported lazily (sibling may be mid-flight)."""
    from app.agents.registry import get_agent
    agent = get_agent(system)
    return agent.plan(model, ctx, emitter)


def _not_checked_report(country, reason: str) -> CodeReport:
    """Honest all-``not_checked`` report when M5 is unavailable.

    NEVER fabricates a pass: passed=0, failed=0, every outcome NOT_CHECKED,
    and the reason is carried in every detail. Headline is forced by the frozen
    CodeReport validator to "passed 0 of N automated checks".
    """
    outcomes = [
        RuleOutcome(
            rule_id=f"not_checked.{s.value}",
            status=RuleStatus.NOT_CHECKED,
            detail=reason,
            severity=Severity.INFO,
        )
        for s in SYSTEM_ORDER
    ]
    return CodeReport(
        country=country,
        rule_pack_version="unavailable",
        total=len(outcomes),
        passed=0,
        failed=0,
        not_checked=len(outcomes),
        outcomes=outcomes,
    )


def _code_check(model, layers, country) -> CodeReport:
    """Call M5's check_code LAZILY; degrade to all-not_checked if absent."""
    try:
        from app.rules.engine import check_code
    except Exception as exc:
        return _not_checked_report(
            country,
            "M5 rule engine not importable; automated checks were not run "
            f"({type(exc).__name__}: {exc})",
        )
    try:
        report = check_code(model, layers, country)
    except Exception as exc:
        return _not_checked_report(
            country, f"M5 check_code raised {type(exc).__name__}: {exc}"
        )
    if report is None:
        return _not_checked_report(country, "M5 check_code returned no report")
    return report


# ---- public API ----------------------------------------------------------

def start_run(model_id: str, enabled: list[SystemKind] | None = None,
              country: Country | None = None) -> RunResult:
    """Load the model, persist a RUNNING run, schedule execute_run, return now."""
    from app.core import bootstrap
    db = bootstrap.get_store()
    bootstrap.get_bus()  # ensure the shared bus exists before the worker runs
    model = db.get_model(model_id)
    if model is None:
        raise KeyError(f"no model with id {model_id!r}")
    c = country if country is not None else model.country
    systems = _resolve_enabled(model, enabled)
    run = RunResult(
        run_id="run-" + uuid.uuid4().hex[:12],
        model_id=model_id,
        status=RunStatus.RUNNING,
        country=c,
        enabled_systems=systems,
        started_at=time.time(),
    )
    db.save_run(run)
    _run_executor().submit(execute_run, run)  # non-blocking
    return run


def get_run(run_id: str) -> RunResult | None:
    """Fetch a persisted run by id."""
    from app.core import bootstrap
    return bootstrap.get_store().get_run(run_id)


def execute_run(run: RunResult, *, emitter=None, store=None) -> RunResult:
    """Run the full pipeline. Callable synchronously (tests) or in a worker.

    Optional ``emitter`` / ``store`` overrides let tests inject their own
    EventBus / JsonFileStore; otherwise the bootstrap singletons are used.
    ALWAYS emits exactly one terminal event LAST, on success or any exception.
    """
    from app.core import bootstrap
    bus = emitter if emitter is not None else bootstrap.get_bus()
    db = store if store is not None else bootstrap.get_store()

    def emit(name, agent, message, metrics=None, phase=AgentPhase.QUEUED):
        return bus.emit(_event(run.run_id, name, agent, message,
                               metrics=metrics, phase=phase))

    def feed(agent, text):
        return bus.feed(run.run_id, agent, text)

    start_ts = time.time()
    try:
        # (a) start + a feed line
        emit("run.started", "orchestrator",
             f"Run {run.run_id} started for model {run.model_id}")
        feed("orchestrator",
             f"Orchestrator: dispatching {len(run.enabled_systems)} systems in parallel.")

        model = db.get_model(run.model_id)
        if model is None:
            raise RuntimeError(f"model {run.model_id!r} not found in store")

        limits = bootstrap.get_limits()  # real RunLimits
        # respect the pinned limits verbatim; only tighten the per-agent cap
        ctx_limits = limits.model_copy(update={
            "max_candidates_per_agent": min(
                int(limits.max_candidates_per_agent), _AGENT_CANDIDATE_BUDGET
            )
        })

        # (b) enabled systems (fire off for villa, on for apartment) + override
        systems = _resolve_enabled(model, run.enabled_systems or None)
        run.enabled_systems = systems
        bt = getattr(model.building_type, "value", str(model.building_type))
        fire_on = SystemKind.FIRE in systems
        feed("orchestrator",
             f"Enabled: {', '.join(s.value for s in systems)} "
             f"(fire {'on' if fire_on else 'off'} for {bt}).")

        ctx = RunContext(
            run_id=run.run_id, model=model, country=run.country,
            limits=ctx_limits, seed=42, enabled_systems=systems,
            emitter=bus, store=db,
        )

        # (c) five agents IN PARALLEL (each clones the shared model)
        layers_collected: list = []
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {
                pool.submit(_plan_one, s, model, ctx, bus): s for s in systems
            }
            for fut in as_completed(futures):
                layers_collected.append(fut.result())  # propagates forced errors
        for layer in layers_collected:
            if layer.status != "done":
                raise RuntimeError(
                    f"agent {layer.system.value} returned a non-done layer "
                    f"(status={layer.status})"
                )
        layers_collected.sort(key=lambda l: SYSTEM_ORDER.index(l.system))
        feed("orchestrator", f"{len(layers_collected)} layers returned; merging.")
        _check_deadline(start_ts, limits)  # respect max_run_seconds

        # (d) merge with real counts
        merged = merge_layers(layers_collected)
        counts = element_count(merged)
        emit("merge.done", "orchestrator",
             f"Merged {len(merged)} layers: {counts['segments']} segments, "
             f"{counts['devices']} devices.",
             metrics={
                 "layers": float(len(merged)),
                 **{k: float(v) for k, v in counts.items()},
             })

        # (e) clash detection (real count + the handover feed line)
        stats: dict[str, Any] = {}
        clashes_before = detect_clashes(merged, model, limits=limits, stats=stats)
        real_total = int(stats["total"])
        run.clashes_before = clashes_before
        emit("clash.found", "fixer",
             f"{real_total} clash(es) found between the systems.",
             metrics={"clashes_before": float(real_total),
                      "clashes": float(real_total)})
        feed("orchestrator",
             f"{real_total} found after merge. Handing over to the fixer.")

        # (f) resolver -> remaining count
        resolved_layers, clashes_after = resolve_clashes(
            merged, clashes_before, model, emitter=bus, limits=limits,
            run_id=run.run_id,
        )
        # The resolver returns the BEFORE records with resolved/fix set; their
        # distance_mm still describes the old geometry. Re-measure against the
        # resolved layers so the UI cannot show "unresolved" for a fixed clash.
        clashes_after = refresh_distances(clashes_after, resolved_layers)
        remaining = sum(1 for c in clashes_after if not c.resolved)
        resolved_n = len(clashes_after) - remaining
        emit("merge.done", "orchestrator",
             f"Resolver finished: {resolved_n} of {len(clashes_after)} fixed, "
             f"{remaining} clash(es) remaining.",
             metrics={"clashes_after": float(remaining),
                      "resolved": float(resolved_n)})
        feed("orchestrator", f"{remaining} clash(es) remain after resolution.")
        _check_deadline(start_ts, limits)  # respect max_run_seconds

        # (g) performance loop (feed.line on any failing real metric)
        perf = run_performance_loop(
            resolved_layers, model, limits, emitter=bus, run_id=run.run_id
        )
        _check_deadline(start_ts, limits)  # respect max_run_seconds

        # (h) code check (lazy M5, honest not_checked fallback)
        code = _code_check(model, resolved_layers, run.country)
        run.code = code
        emit("code.progress", "codes",
             f"Code check: passed {code.passed} of {code.total} automated checks.",
             metrics={"checks_passed": float(code.passed),
                      "checks_total": float(code.total)})

        # (i) package real metrics + persist
        run.layers = resolved_layers
        run.clashes_after = clashes_after
        duration = time.time() - start_ts
        layouts_tested = sum(
            float(l.metrics.get("layouts_tested", 0.0))
            for l in resolved_layers.values()
        )
        best_score_agg = sum(
            float(l.metrics.get("best_score", 0.0))
            for l in resolved_layers.values()
        )
        run.metrics = {
            "duration_seconds": round(duration, 4),
            "layouts_tested": round(layouts_tested, 4),
            "best_score": best_score_agg,  # exact sum of the real per-layer scores
            "clashes_before": float(real_total),
            "clashes_after": float(remaining),
            "checks_passed": float(code.passed),
            "checks_total": float(code.total),
            "layers": float(len(resolved_layers)),
            **{f"perf_{k}": float(v) for k, v in perf.items()},
        }
        run.status = RunStatus.COMPLETE
        run.finished_at = time.time()
        db.save_run(run)

        # (j) EXACTLY ONE terminal event, LAST
        emit("run.completed", "orchestrator", "Run complete.",
             metrics={
                 "duration_seconds": run.metrics["duration_seconds"],
                 "layouts_tested": run.metrics["layouts_tested"],
                 "best_score": run.metrics["best_score"],
                 "clashes_before": run.metrics["clashes_before"],
                 "clashes_after": run.metrics["clashes_after"],
                 "checks_passed": run.metrics["checks_passed"],
                 "checks_total": run.metrics["checks_total"],
             },
             phase=AgentPhase.DONE)
        return run

    except Exception as exc:  # noqa: BLE001 — terminal invariant must hold
        run.status = RunStatus.FAILED
        run.error = f"{type(exc).__name__}: {exc}"
        run.finished_at = time.time()
        try:
            db.save_run(run)
        except Exception:
            pass
        # LAST event on any failure — no event is emitted after this one.
        emit("run.failed", "orchestrator", f"Run failed: {run.error}",
             metrics={"clashes_before": float(len(run.clashes_before))},
             phase=AgentPhase.FAILED)
        return run


__all__ = ["RunContext", "start_run", "get_run", "execute_run"]
