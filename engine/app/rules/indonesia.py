"""Indonesia rule pack (13 rules) — SNI plumbing, sprinkler, ventilation, PUIL.

Every ``standard_ref`` is unverified. The build plan states these numbers were
not confirmed with a local engineer, and the pack is written to be corrected
locally without touching the engine: each rule is one function over real data.
"""

from __future__ import annotations

from app.contract.enums import Country, Severity, SystemKind
from app.rules.engine import (
    Rule, _fail, _not_checked, _pass, building_area_m2, devices_of, has_equipment,
    has_system, segment_meta_values, segments_of, total_length_m, wet_room_count,
)

_SNI_PLUMB = "SNI 03-6481"
_SNI_SPRINK = "SNI 03-3989"
_SNI_VENT = "SNI 03-6572"
_PUIL = "PUIL 2011"

D = SystemKind.DRAINAGE
W = SystemKind.WATER
A = SystemKind.AIR
P = SystemKind.POWER
F = SystemKind.FIRE


def _id_septic(model, layers):
    r = R_ID_SEPTIC
    if has_equipment(model, "septic_tank"):
        return _pass(r, "septic tank located in the model")
    if has_equipment(model, "sewer_point"):
        return _pass(r, "public sewer connection point in the model")
    return _fail(r, "neither a septic tank nor a sewer connection point is present")


def _id_soakaway(model, layers):
    r = R_ID_SOAKAWAY
    if has_equipment(model, "septic_tank"):
        soak = has_equipment(model, "sewer_point") is False
        if soak:
            return _not_checked(r, "septic tank present; soak pit or drainage field not "
                                   "modelled, so its capacity cannot be checked")
    if has_equipment(model, "sewer_point"):
        return _pass(r, "connected to a public sewer, so no soak pit is required")
    return _not_checked(r, "no on-site treatment in the model")


def _id_roof_tank(model, layers):
    r = R_ID_ROOF_TANK
    if not has_equipment(model, "water_tank"):
        return _fail(r, "no storage tank for the low-pressure mains supply")
    tank = model.equipment_of("water_tank")[0]
    if tank.z > 0.5:
        return _pass(r, "storage tank located above floor level")
    if has_equipment(model, "pump"):
        return _pass(r, "ground-level tank with a pump")
    return _fail(r, "ground-level tank with no pump; supply pressure would be inadequate")


def _id_water_meter(model, layers):
    r = R_ID_WATER_METER
    if has_equipment(model, "water_meter"):
        return _pass(r, "metered supply point in the model")
    return _fail(r, "no water meter point; supply cannot be billed or isolated")


def _id_stack(model, layers):
    r = R_ID_STACK
    if not has_system(layers, D):
        return _not_checked(r, "no drainage layer")
    stacks = [s for s in segments_of(layers, D)
              if str(s.meta.get("role", "")) in ("stack", "stack_riser", "trunk")]
    if not stacks:
        return _fail(r, "no soil stack identified")
    return _pass(r, f"{len(stacks)} stack/trunk run(s)")


def _id_wet_floor_drain(model, layers):
    r = R_ID_FLOOR_DRAIN
    wet = [x for x in model.level0.rooms
           if x.wet and x.type.value in ("bathroom", "utility")]
    if not wet:
        return _not_checked(r, "no bathroom or utility room in the model")
    drains = {}
    for room in wet:
        has = any(f.kind.value == "floor_drain" for f in model.fixtures_in(room.id))
        drains[room.id] = has
    missing = [rid for rid, ok in drains.items() if not ok]
    if missing:
        return _fail(r, f"{len(missing)} wet room(s) without a floor drain", missing[:5])
    return _pass(r, f"floor drains in all {len(wet)} wet room(s)")


def _id_bath_exhaust(model, layers):
    r = R_ID_BATH_EXHAUST
    wet = wet_room_count(model)
    if wet == 0:
        return _not_checked(r, "no wet rooms in the model")
    if not has_system(layers, A):
        return _not_checked(r, "no air layer")
    exhaust = [s for s in segments_of(layers, A) if s.kind == "duct"]
    if not exhaust:
        return _fail(r, f"{wet} wet room(s) with no exhaust duct")
    return _pass(r, f"{len(exhaust)} exhaust duct run(s) for {wet} wet room(s)")


def _id_ac_outdoor(model, layers):
    r = R_ID_AC_OUTDOOR
    outdoor = has_equipment(model, "ac_outdoor")
    indoor = has_equipment(model, "ac_indoor")
    if not indoor:
        return _not_checked(r, "no indoor cooling unit in the model")
    if not outdoor:
        return _fail(r, "indoor unit present but no outdoor condenser location")
    return _pass(r, "indoor and outdoor cooling units both located")


def _id_voltage(model, layers):
    r = R_ID_VOLTAGE
    if not has_system(layers, P):
        return _not_checked(r, "no power layer")
    vs = segment_meta_values(layers, P, "voltage_v")
    if not vs:
        return _not_checked(r, "voltage_v not recorded")
    off = [v for v in vs if abs(v - 230.0) > 1.0 and abs(v - 400.0) > 1.0]
    if off:
        return _fail(r, f"{len(off)} circuit(s) not on the 230/400 V nominal voltage",
                     [f"{v:.0f} V" for v in off[:5]])
    return _pass(r, f"{len(vs)} circuit(s) at 230 V or 400 V")


def _id_grounding(model, layers):
    r = R_ID_GROUNDING
    if not has_equipment(model, "electrical_panel") and not has_equipment(model, "distribution_board"):
        return _fail(r, "no distribution board, so no earth bar can be verified")
    if not has_system(layers, P):
        return _not_checked(r, "no power layer")
    cables = segment_meta_values(layers, P, "cable_csa_mm2")
    if not cables:
        return _not_checked(r, "cable_csa_mm2 not recorded")
    if min(cables) < 1.5:
        return _fail(r, f"smallest cable {min(cables):.1f} mm2 cannot carry the protective "
                        "conductor")
    return _pass(r, "board present and smallest cable can carry an earth conductor")


def _id_sprinkler(model, layers):
    r = R_ID_SPRINKLER
    if model.building_type.value == "villa":
        if not has_system(layers, F):
            return _not_checked(r, "fire system not enabled for this villa")
    if not has_system(layers, F):
        return _not_checked(r, "no fire layer")
    heads = [d for d in devices_of(layers, F) if "sprinkler" in d.kind.lower()]
    area = building_area_m2(model)
    if not heads:
        return _fail(r, "apartment requires sprinklers but none are placed")
    return _pass(r, f"{len(heads)} heads over {area:.0f} m2")


def _id_fire_tank(model, layers):
    r = R_ID_FIRE_TANK
    if not has_system(layers, F):
        return _not_checked(r, "no fire layer")
    if has_equipment(model, "water_tank"):
        return _pass(r, "on-site water storage is present for fire demand")
    return _fail(r, "sprinklered building with no on-site water storage")


def _id_sewer_length(model, layers):
    r = R_ID_SEWER_LENGTH
    if not has_system(layers, D):
        return _not_checked(r, "no drainage layer")
    lengths = segment_meta_values(layers, D, "length_m")
    if not lengths:
        return _not_checked(r, "no length_m on drainage segments")
    longest = max(lengths)
    if longest > 20.0:
        return _fail(r, f"longest drain {longest:.1f} m exceeds the 20 m on-site limit")
    return _pass(r, f"longest drain {longest:.1f} m; total {total_length_m(layers, D):.0f} m")


R_ID_SEPTIC = Rule("SNI-D-ID-01", "Every dwelling connects to a sewer or an on-site septic "
                                  "tank.", _SNI_PLUMB, (D,), _id_septic, Severity.ERROR,
                  country=Country.ID)
R_ID_SOAKAWAY = Rule("SNI-D-ID-02", "Septic effluent discharges to a soak pit or drainage "
                                    "field sized for the dwelling.", _SNI_PLUMB, (D,),
                     _id_soakaway, Severity.WARNING, country=Country.ID)
R_ID_STACK = Rule("SNI-D-ID-03", "Rooms stack through a single vented soil stack per wet "
                                 "core.", _SNI_PLUMB, (D,), _id_stack, Severity.ERROR,
                  country=Country.ID)
R_ID_FLOOR_DRAIN = Rule("SNI-D-ID-04", "Bathrooms and utility rooms have a floor drain.",
                        _SNI_PLUMB, (D,), _id_wet_floor_drain, Severity.WARNING,
                        country=Country.ID)
R_ID_SEWER_LENGTH = Rule("SNI-D-ID-05", "On-site drainage runs stay within 20 m to the "
                                        "connection point.", _SNI_PLUMB, (D,),
                         _id_sewer_length, Severity.WARNING, country=Country.ID)
R_ID_ROOF_TANK = Rule("SNI-W-ID-01", "Storage is provided for a low-pressure mains supply, "
                                     "with a pump when ground-mounted.", _SNI_PLUMB, (W,),
                      _id_roof_tank, Severity.ERROR, country=Country.ID)
R_ID_WATER_METER = Rule("SNI-W-ID-02", "The supply has an isolating metered connection.",
                        _SNI_PLUMB, (W,), _id_water_meter, Severity.WARNING,
                        country=Country.ID)
R_ID_BATH_EXHAUST = Rule("SNI-A-ID-01", "Bathrooms are mechanically ventilated.", _SNI_VENT,
                         (A,), _id_bath_exhaust, Severity.ERROR, country=Country.ID)
R_ID_AC_OUTDOOR = Rule("SNI-A-ID-02", "Split-system condensers have an outdoor location.",
                       _SNI_VENT, (A,), _id_ac_outdoor, Severity.WARNING, country=Country.ID)
R_ID_VOLTAGE = Rule("PUIL-ID-01", "Circuits run at the 230/400 V nominal household voltage.",
                    _PUIL, (P,), _id_voltage, Severity.ERROR, country=Country.ID)
R_ID_GROUNDING = Rule("PUIL-ID-02", "An earth bar and protective conductor are provided.",
                      _PUIL, (P,), _id_grounding, Severity.ERROR, country=Country.ID)
R_ID_SPRINKLER = Rule("SNI-F-ID-01", "Apartments and larger buildings are sprinklered.",
                      _SNI_SPRINK, (F,), _id_sprinkler, Severity.ERROR, country=Country.ID)
R_ID_FIRE_TANK = Rule("SNI-F-ID-02", "A sprinklered building keeps its fire water on site.",
                      _SNI_SPRINK, (F,), _id_fire_tank, Severity.ERROR, country=Country.ID)

RULES: tuple[Rule, ...] = (
    R_ID_SEPTIC, R_ID_SOAKAWAY, R_ID_STACK, R_ID_FLOOR_DRAIN, R_ID_SEWER_LENGTH,
    R_ID_ROOF_TANK, R_ID_WATER_METER, R_ID_BATH_EXHAUST, R_ID_AC_OUTDOOR,
    R_ID_VOLTAGE, R_ID_GROUNDING, R_ID_SPRINKLER, R_ID_FIRE_TANK,
)

__all__ = ["RULES"]
