"""Water specialist (plan §5-6): cold/hot, tank+pump, fixture-unit demand."""

from __future__ import annotations

from app.contract import SystemKind

from .base import SpecialistAgent


class WaterAgent(SpecialistAgent):
    system = SystemKind.WATER
    name = "water"
    code_ref = "SNI 03-6481"  # ACTIVE rule pack; verified: false

    def _accept_candidate(self, layer, strategy, model) -> bool:
        # REAL check: reject a clearly unsafe velocity (M2 stays well below).
        for s in layer.segments:
            v = s.meta.get("velocity_mps")
            if v is not None and float(v) > 10.0:
                return False
        return True

    def _agent_notes(self, stats, strategy, model, country) -> list[str]:
        fu = self._sm(stats, "fixture_units")
        demand = self._mx(stats, "demand_lpm")
        dia = self._mx(stats, "diameter_mm")
        wait = self._mx(stats, "hot_water_wait_s")
        vmax = self._mx(stats, "velocity_mps")
        recirc = (
            "recirculation only on larger layouts"
            if stats["total_length_m"] > 40.0
            else "no recirculation (compact layout)"
        )
        return [
            f"{stats['total_length_m']:.1f} m {stats['material']} · "
            f"{fu:.0f} fixture units · Ø{dia:.0f} mm",
            f"cold + hot; demand ~{demand:.0f} L/min; hot-water wait ~{wait:.0f} s",
            "roof tank OR ground tank + pump; local electric/solar heaters",
            f"velocity ≤10 m/s (max {vmax:.1f} m/s); SE Asia: point-of-use heaters, {recirc}",
        ]


__all__ = ["WaterAgent"]
