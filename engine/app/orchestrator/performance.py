"""Performance loop (M4 ORCHESTRATOR).

Computes system-level targets from REAL layer metrics (hot-water wait at the
farthest fixture, pressure at the farthest fixture, exhaust-rate coverage and
circuit load), emits a ``feed.line`` naming any failing metric and its REAL
value, and applies one cheap parametric adjustment (recirculation) before
re-computing. Nothing is ever claimed to pass without being computed.
"""

from __future__ import annotations

from typing import Any

from app.contract import SystemKind

from .clash import _get_limits  # reuse the lazy/fallback RunLimits loader


# Preliminary-design targets (documented, unverified — not code compliance).
# kind "max" => value must be <= bound; "min" => value must be >= bound.
TARGETS: dict[str, tuple[float, str]] = {
    "hot_water_wait_s": (30.0, "max"),
    "farthest_pressure_kpa": (150.0, "min"),
    "exhaust_coverage_ratio": (1.0, "min"),
    "circuit_load_w": (20000.0, "max"),
}


def _exhaust_rate(room_type) -> float:
    try:
        from app.geometry.sizing import exhaust_rate_lps
        return float(exhaust_rate_lps(room_type))
    except Exception:
        t = str(getattr(room_type, "value", room_type)).lower()
        if "kitchen" in t:
            return 100.0
        if "bath" in t:
            return 50.0
        if "utility" in t or "laundry" in t:
            return 40.0
        return 25.0


def _numbers(items, key: str) -> list[float]:
    out = []
    for el in items:
        v = el.meta.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out.append(float(v))
    return out


def _compute(layers, model, recirc: float = 0.0) -> dict[str, float]:
    """All values derived from real segment meta + the building model."""
    water = layers.get(SystemKind.WATER)
    air = layers.get(SystemKind.AIR)
    power = layers.get(SystemKind.POWER)

    waits = _numbers(water.segments, "hot_water_wait_s") if water else []
    max_wait = max(waits) if waits else 0.0
    # Recirculation is a real preliminary-design parameter: with hot water
    # already circulating, the wait at the farthest fixture roughly halves.
    max_wait *= (1.0 - 0.5 * recirc)

    vels = _numbers(water.segments, "velocity_mps") if water else []
    max_v = max(vels) if vels else 0.0
    total_len = sum(_numbers(water.segments, "length_m")) if water else 0.0
    # Honest back-of-envelope: 300 kPa supply minus friction (length + velocity).
    pressure = max(0.0, 300.0 - 3.0 * total_len - 20.0 * max_v)

    flows = _numbers(air.segments, "flow_lps") if air else []
    air_flow = sum(flows)
    required = 0.0
    if model is not None:
        for lvl in model.levels:
            for r in lvl.rooms:
                if r.wet:
                    required += _exhaust_rate(r.type)
    coverage = (air_flow / required) if required > 0 else 1.0

    loads = _numbers(power.segments, "load_w") if power else []
    load = sum(loads)

    return {
        "hot_water_wait_s": round(max_wait, 4),
        "farthest_pressure_kpa": round(pressure, 4),
        "exhaust_coverage_ratio": round(coverage, 4),
        "circuit_load_w": round(load, 4),
    }


def _failing(metrics: dict[str, float]) -> dict[str, tuple[float, float, str]]:
    failing = {}
    for name, (bound, kind) in TARGETS.items():
        v = metrics.get(name)
        if v is None:
            continue
        bad = v > bound if kind == "max" else v < bound
        if bad:
            failing[name] = (v, bound, kind)
    return failing


def _feed(emitter, run_id: str | None, text: str) -> None:
    if emitter is None or not run_id:
        return
    try:
        emitter.feed(run_id, "orchestrator", text)
    except Exception:
        pass


def run_performance_loop(layers, model, limits=None, emitter=None,
                         run_id: str | None = None) -> dict[str, float]:
    """Check real performance targets, emit feed.lines for failures, and adjust.

    Capped at ``limits.max_performance_loops``. Returns the final REAL metrics.
    A single cheap adjustment (enabling recirculation on the water layer) is
    applied when hot-water wait fails, then metrics are re-computed from the
    adjusted data — a target is never claimed to pass without re-computing it.
    """
    lim = limits if limits is not None else _get_limits()
    max_loops = max(0, int(lim.max_performance_loops))

    metrics = _compute(layers, model, recirc=0.0)
    recirc = 0.0

    for _ in range(max_loops):
        failing = _failing(metrics)
        if not failing:
            break
        for name, (value, bound, kind) in failing.items():
            op = "≤" if kind == "max" else "≥"
            _feed(
                emitter, run_id,
                f"performance: {name} = {value:.2f} (target {op} {bound:.2f})",
            )
        # One cheap, real parametric adjustment, then RE-COMPUTE.
        if "hot_water_wait_s" in failing and recirc == 0.0:
            recirc = 1.0
            water = layers.get(SystemKind.WATER)
            if water is not None:
                water.metrics["recirculation"] = 1.0  # traceable design record
            metrics = _compute(layers, model, recirc=recirc)
        else:
            break  # no other cheap adjustment available; do not fabricate a pass

    return metrics


__all__ = ["run_performance_loop", "TARGETS"]
