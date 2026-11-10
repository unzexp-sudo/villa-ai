"""M1 foundation contract tests.

Covers: fixture validity, enum values, constants, BuildingModel helpers,
CodeReport.headline, event invariants, JsonFileStore round-trip + cascade,
EventBus (SSE-critical), validators, and the FastAPI boot endpoints.
"""

import json

import pytest
from pydantic import ValidationError

from app.contract.building import (
    BuildingModel,
    Level,
    Room,
    Scale,
)
from app.contract.enums import (
    AgentPhase,
    BuildingType,
    ClashKind,
    Country,
    EquipmentKind,
    FixtureKind,
    RoomType,
    RuleStatus,
    Severity,
    SYSTEM_COLORS,
    SYSTEM_ORDER,
    SystemKind,
)
from app.contract.events import (
    EVENT_NAMES,
    TERMINAL_EVENTS,
    AgentEvent,
    is_terminal,
)
from app.contract.layers import Layer, Segment
from app.contract.results import CodeReport, RunResult, RunStatus


# ---- fixtures -----------------------------------------------------------
def test_fixtures_validate(sample_model, apartment_model, minimal_model):
    for m in (sample_model, apartment_model, minimal_model):
        assert isinstance(m, BuildingModel)
        assert m.schema_version == "1.0"
        assert isinstance(m.created_at, float) and isinstance(m.updated_at, float)
        assert "fixture" in m.name  # clearly hand-authored test data
        assert m.scale is not None and m.scale.calibrated_by == "fixture"
        assert len(m.assumptions) > 0  # defaults recorded with reasons
        assert len(m.levels) >= 1
    assert apartment_model.building_type == BuildingType.APARTMENT
    # villa_minimal deliberately omits ceiling_height and key equipment
    assert minimal_model.ceiling_height == 2.7  # engine default
    missing = {e.kind.value for e in minimal_model.level0.equipment}
    assert not {"water_tank", "electrical_panel", "sewer_point"} & missing


# ---- enums / constants --------------------------------------------------
def test_enum_values():
    assert [s.value for s in SystemKind] == ["drainage", "water", "air", "power", "fire"]
    assert BuildingType.VILLA.value == "villa"
    assert BuildingType.APARTMENT.value == "apartment"
    assert [c.value for c in Country] == ["ID", "VN", "TH"]
    assert RoomType.BEDROOM.value == "bedroom"
    assert RoomType.BATHROOM.value == "bathroom"
    assert FixtureKind.WASHING_MACHINE.value == "washing_machine"
    assert FixtureKind.KITCHEN_SINK.value == "kitchen_sink"
    assert EquipmentKind.ELECTRICAL_PANEL.value == "electrical_panel"
    assert EquipmentKind.WATER_TANK.value == "water_tank"
    assert AgentPhase.QUEUED.value == "queued"
    assert RuleStatus.NOT_CHECKED.value == "not_checked"
    assert Severity.INFO.value == "info"
    assert ClashKind.HARD.value == "hard"
    assert RunStatus.COMPLETE.value == "complete"


def test_system_colors_and_order():
    assert len(SYSTEM_COLORS) == 5  # exactly the five systems
    assert set(SYSTEM_COLORS) == set(SystemKind)
    assert SYSTEM_COLORS[SystemKind.DRAINAGE] == "#f97316"
    assert SYSTEM_COLORS[SystemKind.WATER] == "#3b82f6"
    assert SYSTEM_COLORS[SystemKind.AIR] == "#22d3ee"
    assert SYSTEM_COLORS[SystemKind.POWER] == "#eab308"
    assert SYSTEM_COLORS[SystemKind.FIRE] == "#e6379c"  # red reserved for clashes
    assert [s.value for s in SYSTEM_ORDER] == [
        "drainage", "water", "air", "power", "fire",
    ]


# ---- BuildingModel helpers ---------------------------------------------
def test_bbox_and_clone(sample_model):
    assert sample_model.bbox() == (0.0, 0.0, 12.0, 10.0)
    assert sample_model.level0 is sample_model.levels[0]
    assert len(sample_model.wet_rooms()) == 4
    assert len(sample_model.fixtures_in("bath1")) == 4
    assert len(sample_model.equipment_of("water_tank")) == 1
    assert len(sample_model.equipment_of(EquipmentKind.WATER_TANK)) == 1

    clone = sample_model.clone()
    assert clone is not sample_model
    assert clone.model_dump() == sample_model.model_dump()
    # deep copy: mutating the clone must not touch the original
    clone.level0.rooms[0].name = "MUTATED"
    clone.level0.walls[0].thickness = 9.9
    assert sample_model.level0.rooms[0].name != "MUTATED"
    assert sample_model.level0.walls[0].thickness != 9.9


def test_validators():
    wall = {"id": "w", "start": {"x": 0, "y": 0}, "end": {"x": 1, "y": 0}}
    # Level: >= 1 room OR >= 1 wall (fixtures may be sparse)
    Level(id="l", name="walls only", walls=[wall])
    with pytest.raises(ValidationError):
        Level(id="l", name="empty")
    # Room.polygon >= 3 points
    Room(id="r", name="r", type=RoomType.OTHER,
         polygon=[{"x": 0, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 1}])
    with pytest.raises(ValidationError):
        Room(id="r", name="r", type=RoomType.OTHER,
             polygon=[{"x": 0, "y": 0}, {"x": 1, "y": 0}])
    # Segment.polyline >= 2 points
    with pytest.raises(ValidationError):
        Segment(id="s", system=SystemKind.WATER, kind="pipe",
                polyline=[{"x": 0, "y": 0, "z": 0}])
    # Scale.distance_m > 0
    with pytest.raises(ValidationError):
        Scale(p1={"x": 0, "y": 0}, p2={"x": 1, "y": 0}, distance_m=0)
    # BuildingModel needs >= 1 level and forbids extra top-level keys
    with pytest.raises(ValidationError):
        BuildingModel(id="m", name="m", created_at=1.0, updated_at=1.0, levels=[])
    with pytest.raises(ValidationError):
        BuildingModel(id="m", name="m", created_at=1.0, updated_at=1.0,
                      levels=[{"id": "l", "name": "l", "rooms": []}],
                      bogus_extra=1)


# ---- CodeReport.headline ------------------------------------------------
def test_code_report_headline():
    cr = CodeReport(country="ID", rule_pack_version="v1", total=40, passed=38,
                    failed=1, not_checked=1, outcomes=[])
    assert cr.headline == "passed 38 of 40 automated checks"
    assert "code compliant" not in cr.headline.lower()
    assert "compliant" not in cr.headline.lower()
    # The value is forced even if a caller supplies a bogus headline.
    cr2 = CodeReport(country="ID", rule_pack_version="v1", total=40, passed=38,
                     failed=1, not_checked=1, outcomes=[],
                     headline="this plan is code compliant")
    assert cr2.headline == "passed 38 of 40 automated checks"
    # Survives a JSON round-trip.
    assert CodeReport.model_validate(cr.model_dump()).headline == cr.headline


# ---- events -------------------------------------------------------------
def test_event_invariants():
    assert len(EVENT_NAMES) == 12
    assert len(set(EVENT_NAMES)) == len(EVENT_NAMES)  # no duplicates
    assert set(TERMINAL_EVENTS).issubset(set(EVENT_NAMES))
    assert TERMINAL_EVENTS == ("run.completed", "run.failed")
    assert is_terminal("run.completed") and is_terminal("run.failed")
    assert not is_terminal("feed.line") and not is_terminal("run.started")
    with pytest.raises(ValidationError):
        AgentEvent(run_id="r", seq=1, ts=1.0, name="not.a.real.event",
                   agent="x", message="m")


# ---- store --------------------------------------------------------------
def test_store_roundtrip_and_cascade(fresh_store, sample_model):
    s = fresh_store
    s.save_model(sample_model)
    got = s.get_model(sample_model.id)
    assert got is not None and got.model_dump() == sample_model.model_dump()
    assert got.bbox() == sample_model.bbox()

    layer = Layer(system=SystemKind.WATER, agent="water", status="done")
    run = RunResult(
        run_id="run-1", model_id=sample_model.id, status=RunStatus.COMPLETE,
        country=Country.ID,
        enabled_systems=[SystemKind.WATER, SystemKind.DRAINAGE],
        layers={SystemKind.WATER: layer}, started_at=1.0,
    )
    s.save_run(run)
    r2 = s.get_run("run-1")
    assert r2 is not None and list(r2.layers.keys()) == [SystemKind.WATER]
    # JSON keys for RunResult.layers are enum VALUES.
    raw = json.loads((s.data_dir / "runs" / "run-1.json").read_text())
    assert list(raw["layers"].keys()) == ["water"]

    for i, name in enumerate(["run.started", "feed.line", "run.completed"], 1):
        s.append_event("run-1", AgentEvent(run_id="run-1", seq=i, ts=float(i),
                                           name=name, agent="t", message=name))
    assert [e.seq for e in s.events_since("run-1", 0)] == [1, 2, 3]
    assert [e.seq for e in s.events_since("run-1", 2)] == [3]

    # A second run for the same model + one for a different model.
    s.save_run(RunResult(run_id="run-2", model_id=sample_model.id,
                         status=RunStatus.PENDING, country=Country.ID,
                         enabled_systems=[], started_at=2.0))
    s.append_event("run-2", AgentEvent(run_id="run-2", seq=1, ts=1.0,
                                       name="run.started", agent="t", message="x"))
    s.save_run(RunResult(run_id="other-1", model_id="other",
                         status=RunStatus.PENDING, country=Country.ID,
                         enabled_systems=[], started_at=3.0))
    s.append_event("other-1", AgentEvent(run_id="other-1", seq=1, ts=1.0,
                                         name="run.started", agent="t", message="x"))
    assert [r.run_id for r in s.list_runs_for_model(sample_model.id)] == ["run-1", "run-2"]

    # Cascade delete removes model + its runs + those runs' events ONLY.
    s.delete_model(sample_model.id)
    assert s.get_model(sample_model.id) is None
    assert s.get_run("run-1") is None and s.get_run("run-2") is None
    assert s.events_since("run-1", -1) == [] and s.events_since("run-2", -1) == []
    assert s.get_run("other-1") is not None  # unrelated run survives
    assert len(s.events_since("other-1", -1)) == 1


# ---- EventBus (SSE-critical) -------------------------------------------
def _evt(run_id, name):
    return AgentEvent(run_id=run_id, seq=0, ts=0.0, name=name,
                      agent="t", message=name)


def test_event_bus_seq_feed_and_wait(fresh_bus):
    b = fresh_bus
    s = b._store
    e1 = b.emit(_evt("r", "run.started"))
    e2 = b.emit(_evt("r", "agent.status"))
    e3 = b.feed("r", "water", "template line, no invented metrics")
    assert [e1.seq, e2.seq, e3.seq] == [1, 2, 3]  # monotonic per run
    assert e1.ts > 0 and e2.ts > 0
    assert e3.name == "feed.line" and e3.message.startswith("template line")
    # persisted
    assert [e.seq for e in s.events_since("r", 0)] == [1, 2, 3]
    assert b.terminal_seen("r") is False

    # wait() blocks on the Condition and returns on timeout when nothing new
    assert b.wait("r", 3, timeout=0.01) == []
    b.emit(_evt("r", "agent.progress"))
    assert [e.seq for e in b.wait("r", 3, timeout=0.5)] == [4]


def test_sse_terminal_invariant(fresh_bus):
    """After a terminal event, terminal_seen is true and replay has no dup seqs."""
    b = fresh_bus
    run_id = "sse-run"
    names = [
        "run.started", "agent.progress", "candidate.flicker",
        "layer.committed", "merge.done", "clash.found", "clash.resolved",
        "code.progress", "run.completed",
    ]
    for name in names:
        b.emit(_evt(run_id, name))

    assert b.terminal_seen(run_id) is True
    assert b.terminal_seen("never-existed") is False

    seqs = [e.seq for e in b.since(run_id, 0)]
    assert len(seqs) == len(set(seqs))  # no duplicate seq numbers
    assert seqs == sorted(seqs) == list(range(1, len(names) + 1))
    # Replay from a mid-seq id is also duplicate-free (Last-Event-ID).
    tail = [e.seq for e in b.since(run_id, 4)]
    assert len(tail) == len(set(tail)) and min(tail) == 5


# ---- FastAPI boot -------------------------------------------------------
def test_api_health_and_config():
    from fastapi.testclient import TestClient
    from main import app

    c = TestClient(app)
    health = c.get("/api/health")
    assert health.status_code == 200
    assert health.json() == {"ok": True, "engine": "villa-ai", "version": "0.1.0"}

    cfg = c.get("/api/config")
    assert cfg.status_code == 200
    body = cfg.json()
    assert body["product_name"] == "Villa AI"
    assert body["systems"] == ["drainage", "water", "air", "power", "fire"]
    assert len(body["systems"]) == 5
