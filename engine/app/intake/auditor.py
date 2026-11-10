"""Intake auditor: what is missing, what can we suggest, what can be skipped.

Rules first, LLM second. In v1 it is rules only — which is the correct floor,
because every gap this reports is checkable against the model and every
suggestion is derived from real geometry (wall runs, room centroids, the
footprint), not from a model's idea of where a tank usually goes.

The three verbs the UI offers come straight from the build plan:
``drag`` the item onto the plan, ``suggest`` a spot, or ``skip`` and record the
default in the assumptions log. Skipping is never silent: it writes an
``Assumption`` with ``source="skip"``.
"""

from __future__ import annotations

import math
import uuid

from app.contract.building import (
    Assumption,
    BuildingModel,
    Equipment,
    Point2D,
    Room,
)
from app.contract.enums import BuildingType, EquipmentKind, Severity, SystemKind
from app.contract.results import MissingItem

# The required-items list from the build plan §5, per building type.
_WET_KINDS = {EquipmentKind.SEWER_POINT, EquipmentKind.SEPTIC_TANK,
              EquipmentKind.WATER_METER, EquipmentKind.WATER_TANK,
              EquipmentKind.WATER_HEATER, EquipmentKind.PUMP}
_DRY_KINDS = {EquipmentKind.ELECTRICAL_PANEL, EquipmentKind.AC_OUTDOOR,
              EquipmentKind.SHAFT, EquipmentKind.RISER}

_LABELS: dict[EquipmentKind, tuple[str, str, SystemKind | None, str]] = {
    EquipmentKind.SEWER_POINT: ("Sewer connection", "We couldn't find a sewer or septic connection point. Where does waste leave the building?", SystemKind.DRAINAGE, "outside"),
    EquipmentKind.SEPTIC_TANK: ("Septic tank", "No sewer nearby? Place the septic tank and its soak pit.", SystemKind.DRAINAGE, "outside"),
    EquipmentKind.WATER_METER: ("Water meter", "Where does the mains supply enter?", SystemKind.WATER, "wall"),
    EquipmentKind.WATER_TANK: ("Water tank", "We couldn't find a water tank. Place one?", SystemKind.WATER, "outside"),
    EquipmentKind.PUMP: ("Booster pump", "A ground-level tank needs a pump to reach the fixtures.", SystemKind.WATER, "corner"),
    EquipmentKind.WATER_HEATER: ("Water heater", "No water heater located. Place a local electric or solar heater?", SystemKind.WATER, "wall"),
    EquipmentKind.ELECTRICAL_PANEL: ("Electrical panel", "Where is the distribution board?", SystemKind.POWER, "wall"),
    EquipmentKind.AC_OUTDOOR: ("Outdoor AC units", "Where do the condenser units sit?", SystemKind.AIR, "outside"),
    EquipmentKind.SHAFT: ("Service shaft", "No shaft or riser found. Place one?", SystemKind.DRAINAGE, "corner"),
}


def _centroid(room: Room) -> Point2D:
    if not room.polygon:
        return Point2D(x=0.0, y=0.0)
    return Point2D(x=sum(p.x for p in room.polygon) / len(room.polygon),
                   y=sum(p.y for p in room.polygon) / len(room.polygon))


def _footprint(model: BuildingModel) -> tuple[float, float, float, float]:
    try:
        return model.bbox()
    except Exception:
        return (0.0, 0.0, 1.0, 1.0)


def _suggest_outside(model: BuildingModel, room: Room | None = None) -> Point2D:
    """Just outside the nearest wall, on the wet side if we know it."""
    minx, miny, maxx, maxy = _footprint(model)
    c = _centroid(room) if room is not None else Point2D(x=(minx + maxx) / 2,
                                                         y=(miny + maxy) / 2)
    gaps = [(c.x - minx, Point2D(x=minx - 1.0, y=c.y)),
            (maxx - c.x, Point2D(x=maxx + 1.0, y=c.y)),
            (c.y - miny, Point2D(x=c.x, y=miny - 1.0)),
            (maxy - c.y, Point2D(x=c.x, y=maxy + 1.0))]
    gaps.sort(key=lambda g: g[0])
    return gaps[0][1]


def _suggest_on_wall(model: BuildingModel) -> Point2D:
    """Midpoint of the longest exterior wall, inset to the dry side."""
    level = model.level0
    best = None
    for w in level.walls:
        length = math.dist((w.start.x, w.start.y), (w.end.x, w.end.y))
        if best is None or length > best[0]:
            best = (length, w)
    if best is None:
        minx, miny, maxx, maxy = _footprint(model)
        return Point2D(x=minx + 0.3, y=(miny + maxy) / 2)
    w = best[1]
    mid = Point2D(x=(w.start.x + w.end.x) / 2, y=(w.start.y + w.end.y) / 2)
    c = Point2D(x=(model.bbox()[0] + model.bbox()[2]) / 2,
                y=(model.bbox()[1] + model.bbox()[3]) / 2)
    dx, dy = c.x - mid.x, c.y - mid.y
    norm = math.hypot(dx, dy) or 1.0
    return Point2D(x=mid.x + dx / norm * 0.4, y=mid.y + dy / norm * 0.4)


def _suggest_corner(model: BuildingModel) -> Point2D:
    minx, miny, maxx, maxy = _footprint(model)
    wet = model.wet_rooms()
    if wet:
        c = _centroid(wet[0])
        return Point2D(x=min(max(c.x, 0.4), maxx - 0.4), y=min(max(c.y, 0.4), maxy - 0.4))
    return Point2D(x=minx + 0.3, y=miny + 0.3)


def _present_kinds(model: BuildingModel) -> set[str]:
    kinds: set[str] = set()
    for lvl in model.levels:
        kinds.update(e.kind.value for e in lvl.equipment)
        if lvl.shafts:
            kinds.add("shaft")
    if model.site.sewer_point is not None:
        kinds.add("sewer_point")
    if model.site.septic_point is not None:
        kinds.add("septic_tank")
    if model.site.water_meter is not None:
        kinds.add("water_meter")
    if model.site.power_incomer is not None:
        kinds.add("electrical_panel")
    return kinds


def _required(model: BuildingModel) -> list[EquipmentKind]:
    kinds = [
        EquipmentKind.WATER_TANK,
        EquipmentKind.WATER_METER,
        EquipmentKind.ELECTRICAL_PANEL,
        EquipmentKind.WATER_HEATER,
    ]
    # A connection point is needed, but sewer OR septic satisfies it.
    kinds.append(EquipmentKind.SEWER_POINT)
    if model.building_type == BuildingType.APARTMENT:
        kinds.append(EquipmentKind.SHAFT)
    if any(r.wet and r.type.value in ("bathroom",) for r in model.level0.rooms):
        kinds.append(EquipmentKind.PUMP)
    if model.wet_rooms():
        pass  # floor drains are reported per room below
    return kinds


def audit(model: BuildingModel) -> list[MissingItem]:
    """Return the missing-item cards for this model, most severe first."""
    present = _present_kinds(model)
    items: list[MissingItem] = []

    # Basic facts first: a plan with no ceiling height cannot be designed.
    if not model.ceiling_height or model.ceiling_height <= 0:
        items.append(MissingItem(
            id="mi-ceiling", kind="ceiling_height", label="Ceiling height",
            prompt="How high is the ceiling? This sets every service zone.",
            severity=Severity.ERROR, system=None, suggested_at=None, snap="any",
        ))
    if model.storeys < 1:
        items.append(MissingItem(
            id="mi-storeys", kind="storeys", label="Number of storeys",
            prompt="How many storeys does this building have?",
            severity=Severity.ERROR, system=None, suggested_at=None, snap="any",
        ))

    for kind in _required(model):
        if kind.value in present:
            continue
        # sewer OR septic is enough: don't ask twice.
        if kind == EquipmentKind.SEWER_POINT and "septic_tank" in present:
            continue
        label, prompt, system, snap = _LABELS[kind]
        if snap == "outside":
            at = _suggest_outside(model, model.wet_rooms()[0] if model.wet_rooms() else None)
        elif snap == "wall":
            at = _suggest_on_wall(model)
        else:
            at = _suggest_corner(model)
        items.append(MissingItem(
            id=f"mi-{kind.value}", kind=kind.value, label=label, prompt=prompt,
            severity=Severity.WARNING if kind in _DRY_KINDS else Severity.ERROR,
            system=system, suggested_at=at, snap=snap,  # type: ignore[arg-type]
        ))

    for room in model.level0.rooms:
        if room.wet and room.type.value in ("bathroom", "utility"):
            if not any(f.kind.value == "floor_drain" for f in model.fixtures_in(room.id)):
                items.append(MissingItem(
                    id=f"mi-fd-{room.id}", kind="floor_drain",
                    label=f"{room.name}: floor drain",
                    prompt=f"{room.name} is a wet room with no floor drain. Add one?",
                    severity=Severity.WARNING, system=SystemKind.DRAINAGE,
                    suggested_at=_centroid(room), snap="wet_room",
                ))
    return items


def apply_item(model: BuildingModel, kind: str, at: Point2D | None = None,
               action: str = "place") -> BuildingModel:
    """Place, suggest, or skip one missing item. Returns a NEW model.

    ``skip`` is recorded as an assumption rather than dropped, so the report can
    always say which defaults were assumed and why.
    """
    out = model.clone()
    out.updated_at = __import__("time").time()

    if kind == "ceiling_height":
        if action == "skip":
            out.assumptions.append(Assumption(
                id="as-" + uuid.uuid4().hex[:8], field="ceiling_height", value="2.7",
                reason="user skipped; default 2.7 m used so the ceiling void has a datum",
                source="skip", created_at=out.updated_at))
            out.ceiling_height = 2.7
        return out
    if kind == "storeys":
        if action == "skip":
            out.storeys = 1
            out.assumptions.append(Assumption(
                id="as-" + uuid.uuid4().hex[:8], field="storeys", value="1",
                reason="user skipped; single storey assumed", source="skip",
                created_at=out.updated_at))
        return out

    try:
        eq_kind = EquipmentKind(kind)
    except ValueError:
        if kind == "floor_drain":
            if at is None:
                raise ValueError("a floor drain needs a position")
            room = next((r for r in out.level0.rooms if r.wet), None)
            out.level0.fixtures.append({
                "id": "fx-" + uuid.uuid4().hex[:8], "kind": "floor_drain",
                "at": {"x": at.x, "y": at.y}, "room_id": room.id if room else None,
            })
            out.assumptions.append(Assumption(
                id="as-" + uuid.uuid4().hex[:8], field="floor_drain", value=f"{at.x:.2f},{at.y:.2f}",
                reason="user placed a floor drain", source="user", created_at=out.updated_at))
            return out
        raise ValueError(f"unknown missing-item kind {kind!r}")

    if action == "skip":
        label, _, _, _ = _LABELS.get(eq_kind, (kind, "", None, "any"))
        out.assumptions.append(Assumption(
            id="as-" + uuid.uuid4().hex[:8], field=kind, value="assumed",
            reason=f"user skipped {label}; a default location is assumed and must be "
                   "reviewed by the engineer", source="skip", created_at=out.updated_at))
        return out

    if at is None:
        raise ValueError(f"placing {kind} needs a position")
    out.level0.equipment.append(Equipment(
        id="eq-" + uuid.uuid4().hex[:8], kind=eq_kind,
        at=Point2D(x=at.x, y=at.y), z=0.0,
        props={"placed_by": action},
    ))
    # Keep the site-connection mirror in step so rules and routing see it.
    if eq_kind == EquipmentKind.SEWER_POINT:
        out.site.sewer_point = Point2D(x=at.x, y=at.y)
    elif eq_kind == EquipmentKind.SEPTIC_TANK:
        out.site.septic_point = Point2D(x=at.x, y=at.y)
    elif eq_kind == EquipmentKind.WATER_METER:
        out.site.water_meter = Point2D(x=at.x, y=at.y)
    elif eq_kind == EquipmentKind.ELECTRICAL_PANEL:
        out.site.power_incomer = Point2D(x=at.x, y=at.y)
    out.assumptions.append(Assumption(
        id="as-" + uuid.uuid4().hex[:8], field=kind, value=f"{at.x:.2f},{at.y:.2f}",
        reason=f"user placed the {kind} on the plan", source="user",
        created_at=out.updated_at))
    return out


__all__ = ["audit", "apply_item"]
