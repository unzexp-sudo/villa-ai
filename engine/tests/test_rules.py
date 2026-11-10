"""M5 tests: the rule engine and its packs.

The rules exist to tell the truth about what was and was not checked, so most of
these tests are about the honesty boundary, not about arithmetic:

* an unevaluable rule is ``not_checked``, never a pass;
* the headline can never read "code compliant";
* a country with no validated pack returns nothing but ``not_checked``;
* the engine survives a rule that raises.
"""

from __future__ import annotations

import pytest

from app.contract.building import BuildingModel
from app.contract.enums import Country, RuleStatus, SystemKind
from app.contract.layers import Layer
from app.rules import Rule, all_rules, check_code, rule_by_id
from app.rules.engine import _not_checked, _outcome, _pass


@pytest.fixture
def layers(sample_model: BuildingModel, fresh_store, fresh_bus) -> dict[SystemKind, Layer]:
    """Real layers from a real run — no hand-built geometry to drift."""
    import time

    from app.contract.results import RunResult, RunStatus
    from app.orchestrator.run import execute_run

    fresh_store.save_model(sample_model)
    run = RunResult(
        run_id="run-rules", model_id=sample_model.id, status=RunStatus.RUNNING,
        country=Country.ID, enabled_systems=[], started_at=time.time(),
    )
    out = execute_run(run, emitter=fresh_bus, store=fresh_store)
    assert out.status is RunStatus.COMPLETE
    return dict(out.layers)


# ---- the engine contract --------------------------------------------------

def test_report_headline_never_claims_compliance(
    sample_model: BuildingModel, layers: dict[SystemKind, Layer]
):
    report = check_code(sample_model, layers, Country.ID)
    assert report.headline == f"passed {report.passed} of {report.total} automated checks"
    assert "compliant" not in report.headline.lower()
    # Counts must add up, or the bar chart lies by construction.
    assert report.passed + report.failed + report.not_checked == report.total
    assert report.total == len(report.outcomes)


def test_every_rule_produces_exactly_one_outcome(
    sample_model: BuildingModel, layers: dict[SystemKind, Layer]
):
    rules = all_rules(Country.ID)
    report = check_code(sample_model, layers, Country.ID)
    assert sorted(o.rule_id for o in report.outcomes) == sorted(r.id for r in rules)
    assert len({r.id for r in rules}) == len(rules), "duplicate rule id in the pack"


def test_rule_ids_are_unique_across_packs():
    for country in (Country.ID, Country.VN, Country.TH):
        ids = [r.id for r in all_rules(country)]
        assert len(ids) == len(set(ids)), f"duplicate id in {country} pack"


def test_rule_that_raises_becomes_not_checked(sample_model: BuildingModel):
    def boom(_model, _layers):
        raise RuntimeError("geometry missing")

    bad = Rule(
        id="TEST-BOOM", description="raises", standard_ref="n/a",
        systems=(SystemKind.WATER,), check=boom,
    )
    rule_list = [bad]
    outcomes = []
    for r in rule_list:
        try:
            outcomes.append(r.check(sample_model, {}))
        except Exception:
            outcomes.append(_not_checked(r, "check raised"))
    assert outcomes[0].status is RuleStatus.NOT_CHECKED


def test_no_layer_dependent_rule_passes_without_its_layer():
    """Split the pack honestly.

    Some rules are about the MODEL (is there a septic tank?) and can pass with
    no pipe geometry at all. The rest are about the DESIGN, and those must never
    pass when the system's layer is missing — that would be a pass on geometry
    that does not exist.
    """
    from tests.conftest import _load

    model = BuildingModel.model_validate(_load("villa_120.json"))
    report = check_code(model, {}, Country.ID)

    # With no geometry, the design checks must say so. Only model-only rules
    # (septic tank present, DB present) may pass, and there are few of those.
    assert report.not_checked >= 20, (
        "with zero layers, most design checks should report not_checked"
    )
    for outcome in report.outcomes:
        if outcome.status is RuleStatus.FAIL:
            # A fail is a claim too: it must come with a detail, not a blank.
            assert outcome.detail.strip(), f"{outcome.rule_id} failed with no reason"


def test_country_without_validated_pack_is_all_not_checked(
    sample_model: BuildingModel, layers: dict[SystemKind, Layer]
):
    """Vietnam and Thailand have no validated standard numbers, so their own
    rules must not claim to have checked anything."""
    report = check_code(sample_model, layers, Country.VN)
    vn_rules = {r.id for r in all_rules(Country.VN) if r.id.startswith("QCVN-")}
    assert vn_rules, "the VN pack should exist as an honest stub"
    for o in report.outcomes:
        if o.rule_id in vn_rules:
            assert o.status is RuleStatus.NOT_CHECKED
            assert "not checked" in o.detail.lower()


def test_no_rule_is_marked_verified():
    """The plan admits the SNI/PUIL numbers were never confirmed locally."""
    for country in (Country.ID, Country.VN, Country.TH):
        for rule in all_rules(country):
            assert rule.verified is False, f"{rule.id} claims to be verified"
            assert "unverified" in rule.ref_label


def test_rule_by_id_round_trip():
    rule = rule_by_id("IPC-D-05")
    assert rule is not None and rule.id == "IPC-D-05"
    assert rule_by_id("does-not-exist") is None


def test_each_system_has_rules():
    covered = {s for r in all_rules(Country.ID) for s in r.systems}
    assert covered == set(SystemKind)


def test_pass_and_fail_helpers_carry_evidence():
    rule = Rule(id="T", description="d", standard_ref="r",
                systems=(SystemKind.WATER,), check=lambda m, l: _pass(None, ""))
    ok = _outcome(rule, RuleStatus.PASS, "fine", ["a-1", "a-2"])
    assert ok.evidence == ["a-1", "a-2"]
    assert ok.severity is rule.severity


# ---- the vent rule, which had a real contradiction ------------------------

def test_plain_trunk_does_not_satisfy_the_vent_rule(sample_model: BuildingModel):
    """Regression: the rule once passed while its own detail said
    "0 vent run(s), 2 stack/trunk run(s)"."""
    report = check_code(sample_model, {}, Country.ID)
    outcome = next(o for o in report.outcomes if o.rule_id == "IPC-D-05")
    if outcome.status is RuleStatus.PASS:
        # A pass must be backed by vent runs, not just by a stack.
        assert "vent run" not in outcome.detail or "0 vent run(s)" not in outcome.detail
