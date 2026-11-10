"""M4 ORCHESTRATOR tests — merge, clash detection / resolution, performance, run.

Every pipeline test calls ``execute_run`` DIRECTLY (synchronously) with an
injected ``EventBus`` / ``JsonFileStore``; NOTHING here depends on
ThreadPoolExecutor timing. The shared conftest fixtures (fresh_store /
fresh_bus / sample_model / apartment_model) are used with a REAL
``store.save_model`` so execute_run reloads its model from persistence, just
like the worker thread does.
"""

from __future__ import annotations

import math
import time

import pytest

from app.contract import (
    ClashKind,
    CodeReport,
    Device,
    Layer,
    Point3D,
    RuleStatus,
    RunResult,
    RunStatus,
    Segment,
    Size3D,
    SystemKind,
)
from app.contract.events import TERMINAL_EVENTS
from app.core.config import get_limits
from app.orchestrator import (
    RunContext,
    all_elements,
    detect_clashes,
    element_count,
    element_distance_mm,
    execute_run,
    get_run,
    merge_layers,
    resolve_clashes,
    run_performance_loop,
    start_run,
)


# ---- helpers ------------------------------------------------------------

def _seg(eid, system, pts, dia=100.0, meta=None):
    return Segment(
        id=eid, system=system, kind="pipe",
        polyline=[Point3D(x=x, y=y, z=z) for (x, y, z) in pts],
        diameter_mm=dia, meta=dict(meta or {}),
    )


def _layer(system, segments=None, devices=None):
    return Layer(
        system=system, agent=system.value, status="done",
        segments=segments or [], devices=devices or [],
    )


def _running(model, run_id):
    return RunResult(
        run_id=run_id, model_id=model.id, status=RunStatus.RUNNING,
        country=model.country, enabled_systems=[], started_at=time.time(),
    )


def _assert_code_report(run):
    """A real CodeReport (or the honest all-not_checked fallback)."""
    assert isinstance(run.code, CodeReport)
    assert run.code.total >= 1
    assert run.code.headline == (
        f"passed {run.code.passed} of {run.code.total} automated checks"
    )
    assert "compliant" not in run.code.headline.lower()
    # No fabricated passes: 'passed' must equal the number of PASS outcomes.
    assert run.code.passed == sum(
        1 for o in run.code.outcomes if o.status == RuleStatus.PASS
    )
    # If M5 is absent, EVERY outcome is not_checked (never a made-up pass).
    if run.code.passed == 0 and run.code.failed == 0:
        assert run.code.not_checked == run.code.total
        assert all(o.status == RuleStatus.NOT_CHECKED for o in run.code.outcomes)


def _numeric_metrics(event):
    return {
        k: float(v) for k, v in event.metrics.items()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    }


# ---- 1. deterministic 3D clash detection --------------------------------

def test_detect_clashes_hard_clearance_and_far_apart():
    lim = get_limits()

    # A CLEARANCE clash: two systems cross in plan at (5,5) but are separated
    # by a real 60 mm surface gap (below the 100 mm cross-system clearance).
    w = _seg("w-cross", SystemKind.WATER, [(0, 5, 2.5), (10, 5, 2.5)])
    p = _seg("p-cross", SystemKind.POWER, [(5, 4, 2.66), (5, 6, 2.66)])
    merged = merge_layers([_layer(SystemKind.WATER, [w]),
                           _layer(SystemKind.POWER, [p])])
    stats = {}
    clashes = detect_clashes(merged, None, limits=lim, stats=stats)
    assert len(clashes) >= 1
    assert stats["total"] == len(clashes) >= 1
    assert clashes[0].system_a != clashes[0].system_b
    assert clashes[0].kind == ClashKind.CLEARANCE
    assert element_distance_mm(w, p) == pytest.approx(60.0, abs=1e-6)

    # A HARD clash: same-z crossing segments whose volumes actually intersect.
    wh = _seg("w-hard", SystemKind.WATER, [(0, 5, 2.5), (10, 5, 2.5)])
    ph = _seg("p-hard", SystemKind.POWER, [(5, 0, 2.5), (5, 10, 2.5)])
    mh = merge_layers([_layer(SystemKind.WATER, [wh]),
                       _layer(SystemKind.POWER, [ph])])
    hard = detect_clashes(mh, None, limits=lim)
    assert any(c.kind == ClashKind.HARD for c in hard)
    assert element_distance_mm(wh, ph) == pytest.approx(0.0, abs=1e-9)

    # ZERO clashes for two far-apart segments.
    w2 = _seg("w-far", SystemKind.WATER, [(0, 0, 2.5), (2, 0, 2.5)])
    p2 = _seg("p-far", SystemKind.POWER, [(20, 20, 2.66), (22, 20, 2.66)])
    m2 = merge_layers([_layer(SystemKind.WATER, [w2]),
                       _layer(SystemKind.POWER, [p2])])
    st2 = {}
    assert detect_clashes(m2, None, limits=lim, stats=st2) == []
    assert st2["total"] == 0


# ---- 2. merge + all_elements --------------------------------------------

def test_merge_layers_enum_keys_and_all_elements():
    wseg = _seg("ws", SystemKind.WATER, [(0, 0, 2.5), (2, 0, 2.5)])
    wdev = Device(
        id="wd", system=SystemKind.WATER, kind="pump",
        at=Point3D(x=1, y=1, z=0.5),
        size=Size3D(length_mm=300, width_mm=200, height_mm=200),
    )
    pseg = _seg("ps", SystemKind.POWER, [(0, 0, 2.6), (2, 0, 2.6)])
    merged = merge_layers([
        _layer(SystemKind.WATER, segments=[wseg], devices=[wdev]),
        _layer(SystemKind.POWER, segments=[pseg]),
    ])
    # Preserve enum keys (not raw strings).
    assert set(merged) == {SystemKind.WATER, SystemKind.POWER}
    assert all(isinstance(k, SystemKind) for k in merged)

    items = list(all_elements(merged))
    assert len(items) == 3
    assert {e.id for _s, e in items} == {"ws", "wd", "ps"}
    assert all(isinstance(s, SystemKind) for s, _e in items)
    assert element_count(merged) == {"segments": 2, "devices": 1, "elements": 3}


# ---- 3. performance loop returns REAL targets ---------------------------

def test_performance_loop_returns_real_targets(fresh_bus, sample_model):
    water = _layer(SystemKind.WATER, segments=[
        _seg("w-perf", SystemKind.WATER, [(0, 0, 2.5), (6, 0, 2.5)],
             meta={"hot_water_wait_s": 40.0, "velocity_mps": 1.0,
                   "length_m": 6.0}),
    ])
    air = _layer(SystemKind.AIR, segments=[
        _seg("a-perf", SystemKind.AIR, [(0, 0, 2.2), (6, 0, 2.2)],
             meta={"flow_lps": 50.0}),
    ])
    power = _layer(SystemKind.POWER, segments=[
        _seg("p-perf", SystemKind.POWER, [(0, 0, 2.7), (6, 0, 2.7)],
             meta={"load_w": 3000.0}),
    ])
    merged = merge_layers([water, air, power])
    metrics = run_performance_loop(
        merged, sample_model, limits=get_limits(),
        emitter=fresh_bus, run_id="perf-run",
    )
    for key in ("hot_water_wait_s", "farthest_pressure_kpa",
                "exhaust_coverage_ratio", "circuit_load_w"):
        assert key in metrics and math.isfinite(metrics[key])

    # A failing real metric must be named in a feed.line (never a fake pass).
    feeds = [e.message for e in fresh_bus.since("perf-run", -1)
             if e.name == "feed.line"]
    assert any("hot_water_wait_s" in msg for msg in feeds)
    # The cheap recirculation adjustment is applied and RE-COMPUTED.
    assert metrics["hot_water_wait_s"] <= 40.0


# ---- 4. resolver reduces clashes, never mutates clashes_before ----------

def test_resolve_clashes_reduces_and_does_not_mutate():
    lim = get_limits()
    w = _seg("w1", SystemKind.WATER, [(0, 5, 2.5), (10, 5, 2.5)])
    p = _seg("p1", SystemKind.POWER, [(5, 4, 2.66), (5, 6, 2.66)])
    merged = merge_layers([_layer(SystemKind.WATER, [w]),
                           _layer(SystemKind.POWER, [p])])
    before = detect_clashes(merged, None, limits=lim)
    assert len(before) >= 1
    before_snapshot = [c.model_dump() for c in before]

    work, after = resolve_clashes(merged, before, None, limits=lim)

    # clashes_before is a SEPARATE list and is left byte-for-byte untouched.
    assert after is not before
    assert [c.model_dump() for c in before] == before_snapshot
    assert all(not c.resolved and c.fix is None for c in before)

    # The clash count really drops on the resolved layers.
    remaining = detect_clashes(work, None, limits=lim)
    assert len(remaining) < len(before)
    assert sum(1 for c in after if not c.resolved) < len(before)
    assert any(c.resolved and c.fix for c in after)


# ---- 5. full villa run: layers, code, terminal + event invariants -------

def test_execute_run_villa_pipeline_and_events(fresh_store, fresh_bus, sample_model):
    fresh_store.save_model(sample_model)  # REAL persisted model
    run = _running(sample_model, "run-villa")
    out = execute_run(run, emitter=fresh_bus, store=fresh_store)

    # (a) COMPLETE + real packaged results
    assert out.status == RunStatus.COMPLETE
    assert out.error is None
    assert out.finished_at is not None
    assert set(out.layers) == set(out.enabled_systems)
    assert len(out.layers) == 4                       # villa
    assert SystemKind.FIRE not in out.layers          # fire OFF for villa
    assert out.metrics["clashes_before"] >= 1
    assert out.metrics["clashes_after"] <= out.metrics["clashes_before"]
    assert out.metrics["layers"] == 4
    assert out.metrics["duration_seconds"] >= 0
    assert len(out.clashes_before) >= 1
    _assert_code_report(out)

    # Persisted (get_run reads the same store).
    persisted = fresh_store.get_run(out.run_id)
    assert persisted is not None and persisted.status == RunStatus.COMPLETE
    assert persisted.metrics["clashes_before"] == out.metrics["clashes_before"]

    # (b) TERMINAL-EVENT invariant: exactly one terminal, LAST, no event after.
    events = fresh_bus.since(out.run_id, -1)
    seqs = [e.seq for e in events]
    assert seqs == sorted(seqs) == list(range(1, len(seqs) + 1))  # unique + sorted
    terminals = [e for e in events if e.name in TERMINAL_EVENTS]
    assert len(terminals) == 1
    assert events[-1] is terminals[0]
    assert events[-1].name == "run.completed"
    assert all(e.seq <= events[-1].seq for e in events)
    # Last-Event-ID replay has no duplicates and nothing after the terminal.
    mid = len(seqs) // 2
    replay = [e.seq for e in fresh_bus.since(out.run_id, mid)]
    assert replay == sorted(set(replay))
    assert min(replay) == mid + 1
    assert fresh_bus.since(out.run_id, events[-1].seq) == []

    # (c) NO INVENTED METRICS (task §6).
    # Authoritative real values = final RunResult.metrics + the actual clash
    # list (each distance_mm and the list length).
    real_values = {float(v) for v in out.metrics.values()}
    real_values.update(float(c.distance_mm) for c in out.clashes_before)
    real_values.add(float(len(out.clashes_before)))
    real_values.add(float(len(out.clashes_after)))
    for _k, val in out.metrics.items():
        assert math.isfinite(val) and val >= 0

    clash_found = [e for e in events if e.name == "clash.found"]
    progress = [e for e in events if e.name == "agent.progress"]
    assert clash_found, "pipeline must report the REAL clash count"
    assert progress, "agents must report REAL progress"

    # M4 owns clash.found: every numeric value is a real value from
    # RunResult.metrics / the clash list (never a fabricated sentinel).
    for e in clash_found:
        for key, val in _numeric_metrics(e).items():
            assert math.isfinite(val)
            assert val in real_values, (e.name, key, val)
        assert float(e.metrics["clashes_before"]) == out.metrics["clashes_before"]

    # M3 owns agent.progress: each value is a REAL incremental counter/score —
    # finite, non-negative, internally consistent with its own event fields,
    # and bounded by / summed EXACTLY into the faithful aggregate recorded in
    # RunResult.metrics. Nothing here is invented by the orchestrator.
    agg_layouts = out.metrics["layouts_tested"]
    per_agent_max: dict[str, float] = {}
    for e in progress:
        lt = float(e.layouts_tested)
        bs = float(e.best_score)
        assert lt >= 1 and math.isfinite(lt)
        assert bs >= 0 and math.isfinite(bs)
        assert float(e.metrics["layouts_tested"]) == lt
        assert float(e.metrics["best_score"]) == bs
        assert lt <= agg_layouts + 1e-6           # bounded by the real aggregate
        per_agent_max[e.agent] = max(per_agent_max.get(e.agent, 0.0), lt)
        for _k, val in _numeric_metrics(e).items():
            assert math.isfinite(val) and val >= 0
    assert sum(per_agent_max.values()) == pytest.approx(agg_layouts, abs=1e-6)
    # The aggregates are themselves derived from real per-layer values.
    assert agg_layouts == pytest.approx(
        sum(float(l.metrics.get("layouts_tested", 0.0)) for l in out.layers.values()),
        abs=1e-6,
    )
    assert out.metrics["best_score"] == pytest.approx(
        sum(float(l.metrics.get("best_score", 0.0)) for l in out.layers.values()),
        abs=1e-6,
    )


# ---- 6. full apartment run: fire ON, 5 layers ---------------------------

def test_execute_run_apartment_pipeline(fresh_store, fresh_bus, apartment_model):
    fresh_store.save_model(apartment_model)
    run = _running(apartment_model, "run-apartment")
    out = execute_run(run, emitter=fresh_bus, store=fresh_store)

    assert out.status == RunStatus.COMPLETE
    assert out.error is None
    assert len(out.layers) == 5                       # apartment
    assert SystemKind.FIRE in out.layers              # fire ON for apartment
    assert set(out.layers) == set(out.enabled_systems)
    assert out.metrics["clashes_before"] >= 1
    _assert_code_report(out)

    events = fresh_bus.since(out.run_id, -1)
    terminals = [e for e in events if e.name in TERMINAL_EVENTS]
    assert len(terminals) == 1
    assert events[-1].name == "run.completed"
    assert events[-1] is terminals[0]


# ---- 7. forced agent exception still yields run.failed LAST -------------

def test_forced_agent_exception_yields_failed_terminal(
    fresh_store, fresh_bus, sample_model, monkeypatch,
):
    import app.orchestrator.run as run_mod

    fresh_store.save_model(sample_model)
    dispatched = {"n": 0}

    def flaky_plan_one(system, model, ctx, emitter):
        dispatched["n"] += 1
        if system == SystemKind.WATER:
            raise RuntimeError("forced agent exception")
        # Fast, minimal done layer for the other agents (no heavy planning).
        return Layer(system=system, agent=system.value, status="done")

    monkeypatch.setattr(run_mod, "_plan_one", flaky_plan_one)
    run = _running(sample_model, "run-forced")
    out = execute_run(run, emitter=fresh_bus, store=fresh_store)

    assert dispatched["n"] == 4                      # villa systems all dispatched
    assert out.status == RunStatus.FAILED
    assert out.error and "forced agent exception" in out.error
    assert out.finished_at is not None

    events = fresh_bus.since(out.run_id, -1)
    assert events[-1].name == "run.failed"
    assert sum(1 for e in events if e.name in TERMINAL_EVENTS) == 1
    seqs = [e.seq for e in events]
    assert seqs == sorted(seqs) == list(range(1, len(seqs) + 1))
    persisted = fresh_store.get_run(out.run_id)
    assert persisted.status == RunStatus.FAILED


# ---- 8. start_run persists RUNNING, does not block; get_run -------------

def test_start_run_and_get_run(tmp_path, monkeypatch, sample_model):
    from app.core import bootstrap, config
    import app.orchestrator.run as run_mod

    bootstrap.reset_state()
    monkeypatch.setattr(config.settings, "DATA_DIR", str(tmp_path))
    bootstrap.get_store().save_model(sample_model)

    class _Recorder:
        def __init__(self):
            self.submitted = []

        def submit(self, fn, *args):
            self.submitted.append((fn, args))  # NO execution -> no thread timing

    rec = _Recorder()
    monkeypatch.setattr(run_mod, "_run_executor", lambda: rec)
    try:
        run = start_run(sample_model.id)  # must return immediately
        assert run.status == RunStatus.RUNNING
        assert run.model_id == sample_model.id
        assert SystemKind.FIRE not in run.enabled_systems  # villa default

        got = get_run(run.run_id)
        assert got is not None and got.run_id == run.run_id
        assert got.status == RunStatus.RUNNING

        # execute_run was scheduled exactly once, with THIS run, and NOT run
        # inline (still RUNNING -> start_run did not block).
        assert len(rec.submitted) == 1
        fn, args = rec.submitted[0]
        assert fn is run_mod.execute_run
        assert args[0].run_id == run.run_id
        assert run.status == RunStatus.RUNNING
        assert get_run("does-not-exist") is None
    finally:
        bootstrap.reset_state()
