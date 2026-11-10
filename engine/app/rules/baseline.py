"""International baseline rule pack (29 rules, five systems).

References are the ones the build plan names — NFPA 13, IPC/UPC, ASHRAE 62.2,
IEC 60364 — and every rule is ``verified=False``. They are a starting list for a
local engineer to confirm, not a claim of compliance.

Every rule reads a REAL field produced by the geometry toolkit and degrades to
``NOT_CHECKED`` with a named reason when that field is absent. The meta keys used
here are the ones M2 actually emits:

    drainage  slope_pct, diameter_mm, length_m, max_run_length_m, role, bends
    water     diameter_mm, velocity_mps, hot_water_wait_s, demand_lpm
    air       width_mm, height_mm, flow_lps, condensate_drain_mm
    power     load_w, current_a, breaker_a, cable_csa_mm2, rcd_required,
              rcd_trip_ma, voltage_v
    fire      spacing_m, coverage_per_head_m2, sprinkler_count

Devices are sparse: only ``air`` (diffuser) and ``fire`` (sprinkler_head) carry
them. Equipment lives on the BuildingModel, so "is there a panel?" is answered
from the model, never guessed from the layer.
"""

from __future__ import annotations

from app.contract.enums import Severity, SystemKind

from app.rules.engine import (
    Rule,
    _fail,
    _not_checked,
    _pass,
    building_area_m2,
    devices_of,
    has_equipment,
    has_system,
    max_bends,
    segment_meta_values,
    segments_of,
    total_length_m,
    wet_room_count,
)

_IPC = "IPC/UPC"
_NFPA = "NFPA 13"
_ASHRAE = "ASHRAE 62.2"
_IEC = "IEC 60364"

D = SystemKind.DRAINAGE
W = SystemKind.WATER
A = SystemKind.AIR
P = SystemKind.POWER
F = SystemKind.FIRE

_NO_LAYER = "no {0} layer in this run"


# ---- drainage -----------------------------------------------------------
def _d_slope_min(model, layers):
    r = R_D_SLOPE_MIN
    if not has_system(layers, D):
        return _not_checked(r, _NO_LAYER.format("drainage"))
    slopes = segment_meta_values(layers, D, "slope_pct")
    if not slopes:
        return _not_checked(r, "segments carry no slope_pct")
    bad = [s for s in slopes if s < 1.0 - 1e-6]
    if bad:
        return _fail(r, f"{len(bad)} of {len(slopes)} runs fall below 1% (lowest {min(bad):.2f}%)",
                     [f"{s:.2f}%" for s in bad[:5]])
    return _pass(r, f"{len(slopes)} runs at or above 1% (min {min(slopes):.2f}%)")


def _d_slope_max(model, layers):
    r = R_D_SLOPE_MAX
    if not has_system(layers, D):
        return _not_checked(r, _NO_LAYER.format("drainage"))
    slopes = segment_meta_values(layers, D, "slope_pct")
    if not slopes:
        return _not_checked(r, "segments carry no slope_pct")
    bad = [s for s in slopes if s > 2.0 + 1e-6]
    if bad:
        return _fail(r, f"{len(bad)} runs exceed 2% (highest {max(bad):.2f}%)")
    return _pass(r, f"no run above 2% (max {max(slopes):.2f}%)")


def _d_diameter(model, layers):
    r = R_D_DIAMETER
    if not has_system(layers, D):
        return _not_checked(r, _NO_LAYER.format("drainage"))
    ds = segment_meta_values(layers, D, "diameter_mm")
    if not ds:
        return _not_checked(r, "no diameter_mm on drainage segments")
    small = [d for d in ds if d < 50.0]
    if small:
        return _fail(r, f"{len(small)} runs smaller than 50 mm", [f"{d:.0f} mm" for d in small[:5]])
    return _pass(r, f"{len(ds)} runs sized >= 50 mm (min {min(ds):.0f} mm)")


def _d_run_length(model, layers):
    r = R_D_RUN_LENGTH
    if not has_system(layers, D):
        return _not_checked(r, _NO_LAYER.format("drainage"))
    lengths = segment_meta_values(layers, D, "length_m")
    if not lengths:
        return _not_checked(r, "no length_m on drainage segments")
    longest = max(lengths)
    if longest > 15.0:
        return _fail(r, f"longest branch {longest:.1f} m exceeds the 15 m unvented limit")
    return _pass(r, f"longest branch {longest:.1f} m within 15 m")


def _d_vent(model, layers):
    r = R_D_VENT
    if not has_system(layers, D):
        return _not_checked(r, _NO_LAYER.format("drainage"))
    roles = {str(s.meta.get("role", "")) for s in segments_of(layers, D) if s.meta.get("role")}
    vented = {x for x in roles if x.startswith("vent")}
    stacks = {x for x in roles if x in ("stack", "stack_riser")}
    # A vent run OR a vented stack satisfies the rule. A plain trunk does NOT:
    # trunk is the horizontal collector, and passing on it reads as "0 vent
    # runs" beside a green tick, which is the kind of contradiction that makes
    # an honest report look untrustworthy.
    if not vented and not stacks:
        return _fail(r, "no vent run or vented soil stack identified; "
                        f"drainage roles present: {sorted(roles) or 'none'}")
    return _pass(r, f"{len(vented)} vent run(s), {len(stacks)} stack run(s)")


def _d_cleanout(model, layers):
    r = R_D_CLEANOUT
    if not has_system(layers, D):
        return _not_checked(r, _NO_LAYER.format("drainage"))
    spacings = segment_meta_values(layers, D, "cleanout_spacing_m")
    total = total_length_m(layers, D)
    if not spacings:
        return _not_checked(r, "cleanout_spacing_m not recorded")
    worst = max(spacings)
    if worst > 15.0:
        return _fail(r, f"worst cleanout spacing {worst:.1f} m exceeds 15 m")
    return _pass(r, f"worst spacing {worst:.1f} m over {total:.0f} m of run")


def _d_bends(model, layers):
    r = R_D_BENDS
    if not has_system(layers, D):
        return _not_checked(r, _NO_LAYER.format("drainage"))
    b = max_bends(layers, D)
    if b > 4:
        return _fail(r, f"worst soil run uses {b} bends (limit 4)")
    return _pass(r, f"worst run uses {b} bends")


# ---- water --------------------------------------------------------------
def _w_size(model, layers):
    r = R_W_SIZE
    if not has_system(layers, W):
        return _not_checked(r, _NO_LAYER.format("water"))
    ds = segment_meta_values(layers, W, "diameter_mm")
    if not ds:
        return _not_checked(r, "no diameter_mm on water segments")
    small = [d for d in ds if d < 15.0]
    if small:
        return _fail(r, f"{len(small)} runs below 15 mm nominal")
    return _pass(r, f"{len(ds)} runs sized >= 15 mm (min {min(ds):.0f} mm)")


def _w_velocity(model, layers):
    r = R_W_VELOCITY
    if not has_system(layers, W):
        return _not_checked(r, _NO_LAYER.format("water"))
    vs = segment_meta_values(layers, W, "velocity_mps")
    if not vs:
        return _not_checked(r, "velocity_mps not present on water segments")
    bad = [v for v in vs if v > 2.5]
    if bad:
        return _fail(r, f"{len(bad)} runs above 2.5 m/s (noise and erosion limit)",
                     [f"{v:.2f} m/s" for v in bad[:5]])
    return _pass(r, f"max velocity {max(vs):.2f} m/s")


def _w_wait(model, layers):
    r = R_W_WAIT
    if not has_system(layers, W):
        return _not_checked(r, _NO_LAYER.format("water"))
    waits = segment_meta_values(layers, W, "hot_water_wait_s")
    if not waits:
        return _not_checked(r, "hot-water wait time not computed")
    worst = max(waits)
    if worst > 30.0:
        return _fail(r, f"hot water reaches the farthest fixture in {worst:.0f} s (limit 30 s)")
    return _pass(r, f"hot water arrives in {worst:.0f} s")


def _w_tank(model, layers):
    r = R_W_TANK
    if has_equipment(model, "water_tank"):
        if has_equipment(model, "pump") or has_equipment(model, "water_meter"):
            return _pass(r, "storage tank with a pump or mains feed in the model")
        return _fail(r, "storage tank present but no pump and no mains meter")
    if has_equipment(model, "water_meter"):
        return _fail(r, "mains meter present but no storage tank")
    return _not_checked(r, "no tank, pump or mains meter in the model")


def _w_pressure(model, layers):
    r = R_W_PRESSURE
    if not has_system(layers, W):
        return _not_checked(r, _NO_LAYER.format("water"))
    demands = segment_meta_values(layers, W, "demand_lpm")
    if not demands:
        return _not_checked(r, "demand_lpm not computed, so residual pressure cannot be evaluated")
    return _pass(r, f"demand computed for {len(demands)} runs "
                    f"(peak {max(demands):.1f} L/min); residual pressure not modelled")


def _w_backflow(model, layers):
    r = R_W_BACKFLOW
    if has_equipment(model, "water_meter"):
        return _pass(r, "metered inlet assumed to include a check valve")
    return _not_checked(r, "no mains inlet in the model to assess backflow")


# ---- air ----------------------------------------------------------------
def _a_bath(model, layers):
    r = R_A_BATH
    wet = wet_room_count(model)
    if wet == 0:
        return _not_checked(r, "no wet rooms identified")
    if not has_system(layers, A):
        return _not_checked(r, _NO_LAYER.format("air"))
    exhaust = [s for s in segments_of(layers, A) if "exhaust" in str(s.meta.get("service", ""))]
    if not exhaust:
        exhaust = [s for s in segments_of(layers, A) if s.kind == "duct"
                   and str(s.meta.get("role", "")) == "exhaust"]
    if not exhaust:
        return _fail(r, f"{wet} wet room(s) but no exhaust duct routed")
    return _pass(r, f"{len(exhaust)} exhaust run(s) serve {wet} wet room(s)")


def _a_kitchen(model, layers):
    r = R_A_KITCHEN
    kitchens = [x for x in model.level0.rooms if x.type.value == "kitchen"]
    if not kitchens:
        return _not_checked(r, "no kitchen in the model")
    if not has_system(layers, A):
        return _not_checked(r, _NO_LAYER.format("air"))
    kd = [s for s in segments_of(layers, A) if s.meta.get("service") == "kitchen_exhaust"]
    if not kd:
        return _fail(r, "kitchen present but no dedicated kitchen exhaust route")
    return _pass(r, f"{len(kd)} kitchen exhaust route(s)")


def _a_duct(model, layers):
    r = R_A_DUCT
    if not has_system(layers, A):
        return _not_checked(r, _NO_LAYER.format("air"))
    widths = segment_meta_values(layers, A, "width_mm")
    if not widths:
        return _not_checked(r, "no width_mm on air segments")
    small = [w for w in widths if w < 100.0]
    if small:
        return _fail(r, f"{len(small)} duct runs narrower than 100 mm")
    return _pass(r, f"{len(widths)} runs, narrowest {min(widths):.0f} mm")


def _a_condensate(model, layers):
    r = R_A_CONDENSATE
    if not has_system(layers, A):
        return _not_checked(r, _NO_LAYER.format("air"))
    sizes = segment_meta_values(layers, A, "condensate_drain_mm")
    if not sizes:
        return _not_checked(r, "condensate_drain_mm not recorded")
    bad = [s for s in sizes if s < 20.0]
    if bad:
        return _fail(r, f"{len(bad)} condensate drain(s) smaller than 20 mm")
    return _pass(r, f"{len(sizes)} cooling run(s) with condensate sized >= {min(sizes):.0f} mm")


def _a_fresh(model, layers):
    r = R_A_FRESH
    if not has_system(layers, A):
        return _not_checked(r, _NO_LAYER.format("air"))
    diffusers = devices_of(layers, A)
    if not diffusers:
        return _fail(r, "supply air routed but no diffuser placed")
    return _pass(r, f"{len(diffusers)} supply diffuser(s) placed")


# ---- power --------------------------------------------------------------
def _p_load(model, layers):
    r = R_P_LOAD
    if not has_system(layers, P):
        return _not_checked(r, _NO_LAYER.format("power"))
    loads = segment_meta_values(layers, P, "load_w")
    if not loads:
        return _not_checked(r, "load_w not present on power segments")
    bad = [w for w in loads if w > 3680.0]
    if bad:
        return _fail(r, f"{len(bad)} circuit(s) above 16 A at 230 V (highest {max(bad):.0f} W)")
    return _pass(r, f"heaviest circuit {max(loads):.0f} W")


def _p_breaker(model, layers):
    r = R_P_BREAKER
    if not has_system(layers, P):
        return _not_checked(r, _NO_LAYER.format("power"))
    pairs = list(zip(segment_meta_values(layers, P, "current_a"),
                     segment_meta_values(layers, P, "breaker_a")))
    if not pairs:
        return _not_checked(r, "current_a / breaker_a not recorded")
    over = [(i, b) for i, b in pairs if i > b]
    if over:
        return _fail(r, f"{len(over)} circuit(s) draw more than their breaker rating")
    return _pass(r, f"{len(pairs)} circuit(s) within their breaker rating")


def _p_rcd(model, layers):
    r = R_P_RCD
    if not has_system(layers, P):
        return _not_checked(r, _NO_LAYER.format("power"))
    required = segment_meta_values(layers, P, "rcd_required")
    trips = segment_meta_values(layers, P, "rcd_trip_ma")
    if not required:
        return _not_checked(r, "rcd_required not recorded")
    needs = sum(1 for v in required if v > 0)
    if needs == 0:
        return _pass(r, "no circuit flagged as needing RCD protection")
    if not trips:
        return _fail(r, f"{needs} circuit(s) need RCD protection but none is specified")
    bad = [t for t in trips if t > 30.0]
    if bad:
        return _fail(r, f"{len(bad)} RCD(s) trip above 30 mA")
    return _pass(r, f"{len(trips)} RCD(s) at <= 30 mA cover {needs} wet-area circuit(s)")


def _p_cable(model, layers):
    r = R_P_CABLE
    if not has_system(layers, P):
        return _not_checked(r, _NO_LAYER.format("power"))
    csas = segment_meta_values(layers, P, "cable_csa_mm2")
    if not csas:
        return _not_checked(r, "cable_csa_mm2 not present on power segments")
    small = [c for c in csas if c < 1.5]
    if small:
        return _fail(r, f"{len(small)} cable run(s) below 1.5 mm2")
    return _pass(r, f"smallest cable {min(csas):.1f} mm2")


def _p_panel(model, layers):
    r = R_P_PANEL
    if has_equipment(model, "electrical_panel") or has_equipment(model, "distribution_board"):
        return _pass(r, "distribution board located in the model")
    return _fail(r, "no electrical panel or distribution board in the model")


def _p_bends(model, layers):
    r = R_P_BENDS
    if not has_system(layers, P):
        return _not_checked(r, _NO_LAYER.format("power"))
    b = max_bends(layers, P)
    if b > 6:
        return _fail(r, f"worst cable run uses {b} bends (limit 6 between pull points)")
    return _pass(r, f"worst cable run uses {b} bends")


# ---- fire ---------------------------------------------------------------
def _f_coverage(model, layers):
    r = R_F_COVERAGE
    if not has_system(layers, F):
        return _not_checked(r, _NO_LAYER.format("fire"))
    per_head = segment_meta_values(layers, F, "coverage_per_head_m2")
    if not per_head:
        return _not_checked(r, "coverage_per_head_m2 not computed")
    worst = max(per_head)
    if worst > 12.0:
        return _fail(r, f"worst coverage {worst:.1f} m2 per head (limit 12)")
    return _pass(r, f"worst coverage {worst:.1f} m2 per head")


def _f_spacing(model, layers):
    r = R_F_SPACING
    if not has_system(layers, F):
        return _not_checked(r, _NO_LAYER.format("fire"))
    sp = segment_meta_values(layers, F, "spacing_m")
    if not sp:
        return _not_checked(r, "spacing_m not recorded")
    worst = max(sp)
    if worst > 4.6:
        return _fail(r, f"head spacing {worst:.1f} m exceeds 4.6 m")
    return _pass(r, f"max head spacing {worst:.1f} m")


def _f_count(model, layers):
    r = R_F_COUNT
    if not has_system(layers, F):
        return _not_checked(r, _NO_LAYER.format("fire"))
    heads = [d for d in devices_of(layers, F) if "sprinkler" in d.kind.lower()]
    area = building_area_m2(model)
    if not heads:
        return _fail(r, "fire layer present but no sprinkler head placed")
    if area <= 0:
        return _not_checked(r, "no room areas to compute density")
    return _pass(r, f"{len(heads)} heads over {area:.0f} m2")


def _f_clearance(model, layers):
    r = R_F_CLEARANCE
    if not has_system(layers, F):
        return _not_checked(r, _NO_LAYER.format("fire"))
    heads = devices_of(layers, F)
    if not heads:
        return _fail(r, "no sprinkler head placed")
    near_wall = []
    for d in heads:
        meta = d.meta.get("clearance_mm") if isinstance(d.meta, dict) else None
        if meta is not None and float(meta) < 300.0:
            near_wall.append(d.id)
    if near_wall:
        return _fail(r, f"{len(near_wall)} head(s) closer than 300 mm to an obstruction")
    return _not_checked(r, "head-to-obstruction clearance is not measured by the toolkit")


def _f_detectors(model, layers):
    r = R_F_DETECTORS
    area = building_area_m2(model)
    if area <= 0:
        return _not_checked(r, "no room areas")
    if not has_system(layers, F):
        return _not_checked(r, _NO_LAYER.format("fire"))
    det = [d for d in devices_of(layers, F) if "detector" in d.kind.lower()]
    if area > 60.0 and not det:
        return _fail(r, f"{area:.0f} m2 of protected area with no smoke detector")
    return _pass(r, f"{len(det)} detector(s) for {area:.0f} m2")


R_D_SLOPE_MIN = Rule("IPC-D-01", "Soil and waste pipes fall at no less than 1%.", _IPC,
                     (D,), _d_slope_min, Severity.ERROR)
R_D_SLOPE_MAX = Rule("IPC-D-02", "Soil pipes fall no more than 2%, to keep solids moving.",
                     _IPC, (D,), _d_slope_max, Severity.WARNING)
R_D_DIAMETER = Rule("IPC-D-03", "No drainage run is smaller than 50 mm nominal.", _IPC,
                    (D,), _d_diameter, Severity.ERROR)
R_D_RUN_LENGTH = Rule("IPC-D-04", "An unvented branch stays within 15 m.", _IPC,
                      (D,), _d_run_length, Severity.WARNING)
R_D_VENT = Rule("IPC-D-05", "Every drainage system has a vent stack or air admittance valve.",
                _IPC, (D,), _d_vent, Severity.ERROR)
R_D_CLEANOUT = Rule("IPC-D-06", "Cleanouts stay within about 15 m of each other.", _IPC,
                    (D,), _d_cleanout, Severity.WARNING)
R_D_BENDS = Rule("IPC-D-07", "A soil run uses no more than four bends.", _IPC,
                 (D,), _d_bends, Severity.WARNING)

R_W_SIZE = Rule("IPC-W-01", "Supply pipes are at least 15 mm nominal.", _IPC,
                (W,), _w_size, Severity.ERROR)
R_W_VELOCITY = Rule("IPC-W-02", "Water velocity stays below 2.5 m/s.", _IPC,
                    (W,), _w_velocity, Severity.WARNING)
R_W_WAIT = Rule("IPC-W-03", "Hot water reaches the farthest fixture within 30 s.", _IPC,
                (W,), _w_wait, Severity.WARNING)
R_W_TANK = Rule("IPC-W-04", "Storage and pumping are provided where there is no pressurised "
                            "mains.", _IPC, (W,), _w_tank, Severity.WARNING)
R_W_PRESSURE = Rule("IPC-W-05", "Residual pressure at the farthest fixture is evaluated.", _IPC,
                    (W,), _w_pressure, Severity.WARNING)
R_W_BACKFLOW = Rule("IPC-W-06", "The mains inlet has backflow protection.", _IPC,
                    (W,), _w_backflow, Severity.WARNING)

R_A_BATH = Rule("ASHRAE-A-01", "Wet rooms have mechanical exhaust.", _ASHRAE,
                (A,), _a_bath, Severity.ERROR)
R_A_KITCHEN = Rule("ASHRAE-A-02", "The kitchen has dedicated exhaust to outside.", _ASHRAE,
                   (A,), _a_kitchen, Severity.ERROR)
R_A_DUCT = Rule("ASHRAE-A-03", "Exhaust ducts are at least 100 mm across.", _ASHRAE,
                (A,), _a_duct, Severity.WARNING)
R_A_CONDENSATE = Rule("ASHRAE-A-04", "Cooling equipment drains condensate in at least 20 mm "
                                     "pipe.", _ASHRAE, (A,), _a_condensate, Severity.WARNING)
R_A_FRESH = Rule("ASHRAE-A-05", "Occupied rooms receive outdoor air through a placed diffuser.",
                 _ASHRAE, (A,), _a_fresh, Severity.WARNING)

R_P_LOAD = Rule("IEC-P-01", "No final circuit exceeds 16 A at 230 V.", _IEC,
                (P,), _p_load, Severity.ERROR)
R_P_BREAKER = Rule("IEC-P-02", "Every circuit's demand stays within its breaker rating.", _IEC,
                   (P,), _p_breaker, Severity.ERROR)
R_P_RCD = Rule("IEC-P-03", "Wet-area circuits are RCD protected at 30 mA.", _IEC,
               (P,), _p_rcd, Severity.ERROR)
R_P_CABLE = Rule("IEC-P-04", "No cable is smaller than 1.5 mm2.", _IEC,
                 (P,), _p_cable, Severity.ERROR)
R_P_PANEL = Rule("IEC-P-05", "A distribution board is located and accessible.", _IEC,
                 (P,), _p_panel, Severity.ERROR)
R_P_BENDS = Rule("IEC-P-06", "Cable runs stay within six bends between pull points.", _IEC,
                 (P,), _p_bends, Severity.WARNING)

R_F_COVERAGE = Rule("NFPA-F-01", "Sprinkler coverage stays under 12 m2 per head.", _NFPA,
                    (F,), _f_coverage, Severity.ERROR)
R_F_SPACING = Rule("NFPA-F-02", "Sprinkler head spacing stays under 4.6 m.", _NFPA,
                   (F,), _f_spacing, Severity.ERROR)
R_F_COUNT = Rule("NFPA-F-03", "Every protected area has sprinkler heads placed.", _NFPA,
                 (F,), _f_count, Severity.ERROR)
R_F_CLEARANCE = Rule("NFPA-F-04", "Heads stay at least 300 mm clear of obstructions.", _NFPA,
                     (F,), _f_clearance, Severity.WARNING)
R_F_DETECTORS = Rule("NFPA-F-05", "Protected areas above 60 m2 have smoke detection.", _NFPA,
                     (F,), _f_detectors, Severity.WARNING)

RULES: tuple[Rule, ...] = (
    R_D_SLOPE_MIN, R_D_SLOPE_MAX, R_D_DIAMETER, R_D_RUN_LENGTH, R_D_VENT,
    R_D_CLEANOUT, R_D_BENDS,
    R_W_SIZE, R_W_VELOCITY, R_W_WAIT, R_W_TANK, R_W_PRESSURE, R_W_BACKFLOW,
    R_A_BATH, R_A_KITCHEN, R_A_DUCT, R_A_CONDENSATE, R_A_FRESH,
    R_P_LOAD, R_P_BREAKER, R_P_RCD, R_P_CABLE, R_P_PANEL, R_P_BENDS,
    R_F_COVERAGE, R_F_SPACING, R_F_COUNT, R_F_CLEARANCE, R_F_DETECTORS,
)

__all__ = ["RULES"]
