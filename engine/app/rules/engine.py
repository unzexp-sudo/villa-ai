"""Code rule engine (M5).

Rules are DATA. Adding a country means adding a pack, not changing the engine.

Two invariants this module exists to protect:

1. **No fabricated passes.** A rule that cannot be evaluated returns
   ``RuleStatus.NOT_CHECKED`` with a ``detail`` naming what was missing. It never
   degrades into a pass. ``CodeReport.headline`` is forced by the contract model
   to "passed N of M automated checks" -- the words "code compliant" must not
   appear anywhere a user can read them, because these packs are unvalidated.
2. **Every number comes from the model or the layer.** ``Segment.meta`` carries
   the real sizing output from the geometry toolkit; a rule reads it or reports
   ``not_checked``. Nothing here invents a value to make a check pass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable

from app.contract.building import BuildingModel, Room
from app.contract.enums import Country, RuleStatus, Severity, SystemKind
from app.contract.layers import Device, Layer, Segment
from app.contract.results import CodeReport, RuleOutcome

CheckFn = Callable[[BuildingModel, dict[SystemKind, Layer]], RuleOutcome]


@dataclass(frozen=True)
class Rule:
    """One checkable requirement.

    ``verified`` is False everywhere: the plan admits the exact SNI / PUIL
    numbers were not confirmed with a local engineer. A rule with
    ``verified=False`` may still pass, but its reference is labelled unverified
    in the report.
    """

    id: str
    description: str
    standard_ref: str
    systems: tuple[SystemKind, ...]
    check: CheckFn
    severity: Severity = Severity.WARNING
    country: Country | None = None
    verified: bool = False
    tags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def ref_label(self) -> str:
        return self.standard_ref if self.verified else f"{self.standard_ref} (unverified)"


# --------------------------------------------------------------------------
# Evaluation helpers. Every rule below is written against these, so a missing
# input always surfaces as not_checked rather than a silent pass.
# --------------------------------------------------------------------------

def _outcome(rule: Rule, status: RuleStatus, detail: str,
             evidence: Iterable[str] = ()) -> RuleOutcome:
    return RuleOutcome(rule_id=rule.id, status=status, detail=detail,
                       evidence=list(evidence), severity=rule.severity)


def _not_checked(rule: Rule, why: str) -> RuleOutcome:
    return _outcome(rule, RuleStatus.NOT_CHECKED, f"not checked: {why}")


def _pass(rule: Rule, detail: str, evidence: Iterable[str] = ()) -> RuleOutcome:
    return _outcome(rule, RuleStatus.PASS, detail, evidence)


def _fail(rule: Rule, detail: str, evidence: Iterable[str] = ()) -> RuleOutcome:
    return _outcome(rule, RuleStatus.FAIL, detail, evidence)


def has_system(layers: dict[SystemKind, Layer], s: SystemKind) -> bool:
    layer = layers.get(s)
    return layer is not None and (bool(layer.segments) or bool(layer.devices))


def segments_of(layers: dict[SystemKind, Layer], s: SystemKind) -> list[Segment]:
    layer = layers.get(s)
    return list(layer.segments) if layer else []


def devices_of(layers: dict[SystemKind, Layer], s: SystemKind) -> list[Device]:
    layer = layers.get(s)
    return list(layer.devices) if layer else []


def total_length_m(layers: dict[SystemKind, Layer], s: SystemKind) -> float:
    total = 0.0
    for seg in segments_of(layers, s):
        v = seg.meta.get("length_m")
        if v is None:
            v = _polyline_length(seg.polyline)
        total += float(v)
    return total


def _polyline_length(polyline) -> float:
    dx = dy = dz = 0.0
    for a, b in zip(polyline, polyline[1:]):
        dx += (b.x - a.x) ** 2
        dy += (b.y - a.y) ** 2
        dz += (b.z - a.z) ** 2
    return (dx + dy + dz) ** 0.5


def max_bends(layers: dict[SystemKind, Layer], s: SystemKind) -> int:
    best = 0
    for seg in segments_of(layers, s):
        v = seg.meta.get("bends")
        best = max(best, int(v) if v is not None else max(0, len(seg.polyline) - 2))
    return best


def wet_room_count(model: BuildingModel) -> int:
    return len(model.wet_rooms())


def dry_room_count(model: BuildingModel) -> int:
    return max(0, len(model.level0.rooms) - wet_room_count(model))


def equipment_kinds(model: BuildingModel) -> set[str]:
    kinds: set[str] = set()
    for lvl in model.levels:
        kinds.update(e.kind.value for e in lvl.equipment)
    return kinds


def has_equipment(model: BuildingModel, kind: str) -> bool:
    return kind in equipment_kinds(model)


def kitchen_rooms(model: BuildingModel) -> list[Room]:
    return [r for r in model.level0.rooms if r.type.value == "kitchen"]


def building_area_m2(model: BuildingModel) -> float:
    return sum(r.area_m2 or 0.0 for r in model.level0.rooms)


def segment_meta_values(layers: dict[SystemKind, Layer], s: SystemKind,
                        key: str) -> list[float]:
    vals: list[float] = []
    for seg in segments_of(layers, s):
        v = seg.meta.get(key)
        if v is not None:
            try:
                vals.append(float(v))
            except (TypeError, ValueError):
                continue
    return vals


# --------------------------------------------------------------------------
# Active pack assembly + evaluation
# --------------------------------------------------------------------------

def _pack_modules():
    """Lazily import packs so a half-written pack cannot block the engine."""
    from app.rules import baseline, indonesia, thailand, vietnam

    return {Country.ID: indonesia, Country.VN: vietnam, Country.TH: thailand,
            None: baseline}, baseline


def all_rules(country: Country | str | None = Country.ID) -> list[Rule]:
    """Baseline + the country pack. Dedupe by id, country rules win."""
    country = Country(country) if country is not None else Country.ID
    packs, baseline = _pack_modules()

    rules: dict[str, Rule] = {r.id: r for r in baseline.RULES}
    pack = packs.get(country)
    if pack is not None:
        for r in pack.RULES:
            rules[r.id] = r
    return list(rules.values())


def rule_by_id(rule_id: str) -> Rule | None:
    for r in all_rules():
        if r.id == rule_id:
            return r
    return None


def check_code(model: BuildingModel, layers: dict[SystemKind, Layer],
               country: Country | str = Country.ID,
               emitter=None, run_id: str | None = None) -> CodeReport:
    """Evaluate the active pack. Never raises on a single bad rule."""
    country = Country(country)
    rules = all_rules(country)
    outcomes: list[RuleOutcome] = []

    for rule in rules:
        try:
            outcome = rule.check(model, layers)
        except Exception as exc:  # a broken check must not fake a pass
            outcome = _not_checked(rule, f"check raised {type(exc).__name__}: {exc}")
        outcomes.append(outcome)

    passed = sum(1 for o in outcomes if o.status == RuleStatus.PASS)
    failed = sum(1 for o in outcomes if o.status == RuleStatus.FAIL)
    not_checked = sum(1 for o in outcomes if o.status == RuleStatus.NOT_CHECKED)

    if emitter is not None and run_id:
        from app.contract.events import AgentEvent

        emitter.emit(AgentEvent(
            run_id=run_id, seq=0, ts=0.0, name="code.progress", agent="codes",
            message=f"Codes: {passed} of {len(outcomes)} checks passed so far.",
            metrics={"checks_passed": float(passed),
                     "checks_total": float(len(outcomes))},
        ))

    return CodeReport(
        country=country,
        rule_pack_version="baseline+ID-v1",
        total=len(outcomes),
        passed=passed,
        failed=failed,
        not_checked=not_checked,
        outcomes=outcomes,
    )


__all__ = [
    "Rule", "all_rules", "rule_by_id", "check_code", "has_system", "segments_of",
    "devices_of", "total_length_m", "max_bends", "wet_room_count", "dry_room_count",
    "equipment_kinds", "has_equipment", "kitchen_rooms", "building_area_m2",
    "segment_meta_values", "_pass", "_fail", "_not_checked",
]
