"""Deterministic, engineering-flavoured sizing for the MEP toolkit.

All numbers are honest, back-of-envelope preliminary-design values (no code
compliance is claimed). Geometry is in METRES, component sizes are in
MILLIMETRES. Every returned key carries an explicit unit suffix
(``_mm``, ``_m``, ``_lps``, ``_w``, ``_pct``) so no downstream renderer has
to invent units. No wall-clock and no global RNG are used here.

These dicts are meant to be merged into ``Segment.meta`` / ``Device.meta`` so
the UI only ever displays values the engine actually computed.
"""

import math

__all__ = [
    "size_drainage", "size_water", "size_duct", "size_cable",
    "sprinkler_spacing", "exhaust_rate_lps",
]


def size_drainage(fixture_units: float) -> dict:
    """Gravity drain: fixture units -> diameter (50/75/110 mm), slope 1-2%.

    Slope is steeper for small bores (self-cleansing), flatter for the 110 mm
    main. ``cleanout_spacing_m`` and ``max_run_length_m`` are rodding limits.
    """
    fu = max(0.0, float(fixture_units))
    if fu <= 2.0:
        diameter_mm, slope_pct = 50.0, 2.0
    elif fu <= 6.0:
        diameter_mm, slope_pct = 75.0, 1.5
    else:
        diameter_mm, slope_pct = 110.0, 1.0
    cleanout_spacing_m = {50.0: 10.0, 75.0: 15.0, 110.0: 20.0}[diameter_mm]
    max_run_length_m = 3.0 + diameter_mm / 20.0
    return {
        "fixture_units": fu,
        "diameter_mm": diameter_mm,
        "slope_pct": slope_pct,                 # always within 1..2
        "cleanout_spacing_m": cleanout_spacing_m,
        "max_run_length_m": max_run_length_m,
        "max_velocity_mps": 1.2,               # gravity self-cleansing guide
    }


def size_water(fixture_units: float, length_m: float) -> dict:
    """Pressurised water: demand -> bore (15/20/25/32 mm), velocity check,
    and hot-water wait time derived from the branch length.
    """
    fu = max(0.0, float(fixture_units))
    length = max(0.0, float(length_m))
    demand_lpm = fu * 8.0                       # 8 L/min per fixture unit (diversity)
    if demand_lpm <= 10.0:
        diameter_mm = 15.0
    elif demand_lpm <= 20.0:
        diameter_mm = 20.0
    elif demand_lpm <= 40.0:
        diameter_mm = 25.0
    else:
        diameter_mm = 32.0
    area_m2 = math.pi * (diameter_mm / 2000.0) ** 2
    q_m3s = demand_lpm / 60000.0
    velocity_mps = q_m3s / area_m2 if area_m2 > 0 else 0.0
    hot_water_wait_s = (length / velocity_mps) if velocity_mps > 0 else 0.0
    return {
        "fixture_units": fu,
        "length_m": length,
        "demand_lpm": demand_lpm,
        "diameter_mm": diameter_mm,
        "velocity_mps": round(velocity_mps, 4),
        "hot_water_wait_s": round(hot_water_wait_s, 2),
        "max_velocity_mps": 2.0,               # design velocity cap
    }


def exhaust_rate_lps(room_type) -> float:
    """Wet-room exhaust rate (L/s). Kitchens 100, bathrooms 50, utility 40,
    everything else 25. Accepts a ``RoomType`` enum or a plain string.
    """
    t = str(getattr(room_type, "value", room_type)).lower()
    if "kitchen" in t:
        return 100.0
    if "bath" in t:
        return 50.0
    if "utility" in t or "laundry" in t:
        return 40.0
    return 25.0


def size_duct(flow_lps: float) -> dict:
    """Rectangular duct from an exhaust/supply flow at a 5 m/s design
    velocity, aspect ratio ~2:1, plus a small condensate drain size.
    """
    flow = max(0.0, float(flow_lps))
    design_velocity_mps = 5.0
    area_m2 = max(0.01, (flow / 1000.0) / design_velocity_mps)
    width_mm = max(100.0, round(math.sqrt(2.0 * area_m2) * 1000.0 / 10.0) * 10.0)
    for std in (100, 150, 200, 250, 300, 350, 400, 450, 500):
        if width_mm <= std:
            width_mm = float(std)
            break
    else:
        width_mm = 500.0
    height_mm = max(100.0, round((area_m2 * 1e6) / width_mm / 10.0) * 10.0)
    cross_m2 = width_mm * height_mm / 1e6
    velocity_mps = (flow / 1000.0) / cross_m2 if cross_m2 > 0 else 0.0
    return {
        "flow_lps": flow,
        "width_mm": width_mm,
        "height_mm": height_mm,
        "area_m2": round(area_m2, 5),
        "velocity_mps": round(velocity_mps, 3),
        "design_velocity_mps": design_velocity_mps,
        "condensate_drain_mm": 25.0,
    }


def size_cable(load_w: float, voltage: float) -> dict:
    """Final-circuit cable CSA / breaker / RCD sizing from load and voltage.

    ``wet_room_rcd_required`` is the baseline for any final circuit that lands
    in a wet room; the routing layer stamps ``rcd_required`` into the segment
    meta when it actually serves a wet room.
    """
    v = float(voltage) if voltage else 230.0
    load = max(0.0, float(load_w))
    current_a = load / v
    if current_a <= 10.0:
        csa, breaker = 1.5, 10.0
    elif current_a <= 16.0:
        csa, breaker = 2.5, 16.0
    elif current_a <= 25.0:
        csa, breaker = 4.0, 25.0
    elif current_a <= 40.0:
        csa, breaker = 6.0, 40.0
    else:
        csa, breaker = 10.0, 63.0
    return {
        "load_w": load,
        "voltage_v": v,
        "current_a": round(current_a, 3),
        "cable_csa_mm2": csa,
        "breaker_a": breaker,
        "conduit_diameter_mm": 20.0 + csa,
        "rcd_recommended": True,
        "rcd_trip_ma": 30.0,
        "wet_room_rcd_required": True,
    }


def sprinkler_spacing(area_m2: float) -> dict:
    """Sprinkler coverage / head count / spacing from a room area (NFPA-style
    ordinary-hazard coverage of ~12 m2 per head, unverified).
    """
    area = max(0.0, float(area_m2))
    coverage_per_head_m2 = 12.0
    heads = max(1, math.ceil(area / coverage_per_head_m2))
    spacing_m = math.sqrt(coverage_per_head_m2)
    return {
        "area_m2": area,
        "sprinkler_count": float(heads),
        "coverage_per_head_m2": coverage_per_head_m2,
        "spacing_m": round(spacing_m, 3),
        "max_spacing_m": 4.6,
        "min_spacing_m": 1.8,
        "design_density_mm_per_min": 5.0,
        "branch_pipe_diameter_mm": 25.0,
    }
