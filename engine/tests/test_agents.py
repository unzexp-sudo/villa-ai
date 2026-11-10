"""M3 planner + five specialist agents tests.

Uses the read-only ``sample_model`` / ``apartment_model`` fixtures from
conftest (never modified) and a LOCAL fake emitter / fake RunContext -- M4 is
never imported. No network: ``get_planner()`` defaults to
``DeterministicPlanner`` and ``ApiPlanner`` returns None with no key.
"""

from __future__ import annotations

import pytest

from app.agents import (
    AirAgent,
    DrainageAgent,
    FireAgent,
    PowerAgent,
    SpecialistAgent,
    WaterAgent,
    enabled_systems,
    get_agent,
)
from app.contract import (
    DEFAULT_BANDS,
    SYSTEM_ORDER,
    AgentEvent,
    AgentPhase,
    Layer,
    SystemKind,
)
from app.planner import (
    ApiPlanner,
    DeterministicPlanner,
    Strategy,
    get_planner,
    validate_strategy,
)


class _FakeEmitter:
    """Local fake emitter (implements the Emitter Protocol by duck typing)."""

    def __init__(self):
        self.events: list[AgentEvent] = []
        self.feeds: list[tuple[str, str, str]] = []

    def emit(self, event: AgentEvent):
        assert isinstance(event, AgentEvent)
        self.events.append(event)
        return event

    def feed(self, run_id: str, agent: str, text: str):
        self.feeds.append((run_id, agent, text))
        # Mimic EventBus.feed(): it emits a feed.line event internally.
        ev = AgentEvent(
            run_id=run_id, seq=0, ts=0, name="feed.line", agent=agent, message=text
        )
        self.events.append(ev)
        return ev


class _Limits:
    def __init__(self, cap: int = 8):
        self.max_candidates_per_agent = cap


class _Ctx:
    """Duck-typed stand-in for M4's RunContext (M4 is never imported)."""

    def __init__(self, run_id="run-m3", cap=8, seed=42, country="ID"):
        self.run_id = run_id
        self.limits = _Limits(cap)
        self.seed = seed
        self.country = country


def _valid_raw() -> dict:
    return {
        "system": "drainage",
        "trunk": "wet_core",
        "stack_location": "wet_core",
        "z_band": [2.55, 2.95],
        "max_bends": 4,
        "prefer_short_runs": 0.5,
        "params": {"elbow": "x"},
    }


# ---------------------------------------------------------------------------
# validate_strategy
# ---------------------------------------------------------------------------

def test_validate_strategy_accepts_valid():
    s = validate_strategy(_valid_raw())
    assert isinstance(s, Strategy)
    assert s.system == SystemKind.DRAINAGE
    assert s.z_band == (2.55, 2.95)


@pytest.mark.parametrize(
    "bad",
    [
        {**_valid_raw(), "trunk": "bogus"},            # bad enum
        {**_valid_raw(), "stack_location": "roof"},    # bad enum
        {**_valid_raw(), "system": "plumbing"},        # bad enum
        {**_valid_raw(), "prefer_short_runs": 1.5},    # out-of-range weight
        {**_valid_raw(), "prefer_short_runs": -0.1},   # out-of-range weight
        {**_valid_raw(), "prefer_short_runs": 2.0},    # out-of-range weight
        {**_valid_raw(), "max_bends": -3},
        {**_valid_raw(), "z_band": [3.0, 2.0]},        # lo > hi
        {**_valid_raw(), "z_band": [2.55]},            # wrong length
        {**_valid_raw(), "params": "not-a-dict"},
    ],
)
def test_validate_strategy_rejects_bad_enums_and_out_of_range_weights(bad):
    assert validate_strategy(bad) is None


def test_validate_strategy_never_raises_on_garbage():
    for raw in (None, 42, "not json", "{bad}", [], object()):
        assert validate_strategy(raw) is None


# ---------------------------------------------------------------------------
# DeterministicPlanner
# ---------------------------------------------------------------------------

def test_deterministic_planner_is_deterministic_and_covers_all_systems(sample_model):
    p = DeterministicPlanner()
    obs = {"country": "ID"}
    for sk in SYSTEM_ORDER:
        s1 = p.choose_strategy(sk, sample_model, obs)
        s2 = p.choose_strategy(sk, sample_model, obs)
        assert isinstance(s1, Strategy)
        assert s1.system == sk
        assert s1.z_band == DEFAULT_BANDS[sk]
        assert s1.trunk in ("perimeter", "central", "wet_core", "radial", "branch")
        assert s1.stack_location in (
            "wet_core", "exterior_wall", "utility", "shaft"
        )
        assert 0.0 <= s1.prefer_short_runs <= 1.0
        assert s1.model_dump() == s2.model_dump()


def test_deterministic_trunk_rules(sample_model):
    p = DeterministicPlanner()
    assert p.choose_strategy(SystemKind.AIR, sample_model, {}).trunk == "perimeter"
    assert p.choose_strategy(SystemKind.POWER, sample_model, {}).trunk == "central"
    assert p.choose_strategy(SystemKind.FIRE, sample_model, {}).trunk == "radial"
    # villa_120 wet rooms are clustered in the west wing -> wet_core trunk.
    assert p.choose_strategy(SystemKind.DRAINAGE, sample_model, {}).trunk == "wet_core"
    assert p.choose_strategy(SystemKind.WATER, sample_model, {}).trunk == "wet_core"


def test_get_planner_default_is_deterministic():
    assert isinstance(get_planner(), DeterministicPlanner)


def test_api_planner_no_key_returns_none(monkeypatch, sample_model):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert ApiPlanner().choose_strategy(SystemKind.AIR, sample_model, {}) is None


def test_llm_fallback_records_rejected_assumption(monkeypatch, sample_model):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    m = sample_model.clone()
    strategy = get_planner(use_api=True).choose_strategy(SystemKind.WATER, m, {})
    assert isinstance(strategy, Strategy)
    assert any(a.reason == "llm_output_rejected" for a in m.assumptions)


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------

def test_get_agent_returns_right_class():
    assert isinstance(get_agent(SystemKind.DRAINAGE), DrainageAgent)
    assert isinstance(get_agent(SystemKind.WATER), WaterAgent)
    assert isinstance(get_agent(SystemKind.AIR), AirAgent)
    assert isinstance(get_agent(SystemKind.POWER), PowerAgent)
    assert isinstance(get_agent(SystemKind.FIRE), FireAgent)
    assert all(isinstance(get_agent(s), SpecialistAgent) for s in SYSTEM_ORDER)


def test_enabled_systems_4_for_villa_5_for_apartment(sample_model, apartment_model):
    assert len(enabled_systems(sample_model)) == 4
    assert SystemKind.FIRE not in enabled_systems(sample_model)
    assert len(enabled_systems(apartment_model)) == 5
    assert SystemKind.FIRE in enabled_systems(apartment_model)


def test_fire_is_optional_default_off_for_villa(sample_model, apartment_model):
    fire = FireAgent()
    assert fire.is_enabled(sample_model) is False
    assert fire.is_enabled(apartment_model) is True


# ---------------------------------------------------------------------------
# full plan()
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sk", list(SYSTEM_ORDER))
def test_full_plan(sample_model, sk):
    emitter = _FakeEmitter()
    agent = get_agent(sk)
    layer = agent.plan(sample_model, _Ctx(), emitter)

    assert isinstance(layer, Layer)
    assert layer.status == "done"
    assert layer.system == sk
    assert layer.metrics["layouts_tested"] > 0
    assert 0.0 <= layer.metrics["best_score"] <= 1.0
    assert len(layer.segments) >= 1
    assert layer.metrics["total_length_m"] > 0
    assert layer.strategy  # committed Strategy for traceability
    assert layer.notes  # real metric summary notes
    assert all(isinstance(e, AgentEvent) for e in emitter.events)

    # layer.committed is LAST among that agent's non-feed emitted events.
    non_feed = [e for e in emitter.events if e.name != "feed.line"]
    assert non_feed, "no events emitted"
    assert non_feed[-1].name == "layer.committed"
    assert non_feed[-1].layer is layer
    assert emitter.feeds, "a feed.line must be emitted"


def test_plan_emits_status_progression(sample_model):
    emitter = _FakeEmitter()
    get_agent(SystemKind.DRAINAGE).plan(sample_model, _Ctx(), emitter)
    statuses = [e.phase for e in emitter.events if e.name == "agent.status"]
    assert statuses == [
        AgentPhase.EXPLORING,
        AgentPhase.TESTING,
        AgentPhase.COMMITTING,
        AgentPhase.DONE,
    ]


def test_plan_never_mutates_input_model(sample_model):
    before = sample_model.model_dump()
    get_agent(SystemKind.WATER).plan(sample_model, _Ctx(), _FakeEmitter())
    assert sample_model.model_dump() == before


def test_plan_returns_failed_layer_when_m2_absent(monkeypatch, sample_model):
    import app.agents.base as base

    def _boom():
        raise RuntimeError(
            "M3 agent: M2 geometry toolkit is unavailable while the tree is being built"
        )

    monkeypatch.setattr(base, "_load_geometry", _boom)
    emitter = _FakeEmitter()
    layer = get_agent(SystemKind.AIR).plan(sample_model, _Ctx(), emitter)
    assert layer.status == "failed"
    assert any(e.phase == AgentPhase.FAILED for e in emitter.events)
