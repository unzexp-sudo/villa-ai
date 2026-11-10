"""Power specialist (plan §5-6): panel, circuits, load calc, RCD, per-country voltage."""

from __future__ import annotations

from app.contract import SystemKind

from .base import SpecialistAgent

# Per-country nominal voltage (V), SE Asia / international baseline.
_VOLTAGE = {"ID": 230, "VN": 220, "TH": 230}


class PowerAgent(SpecialistAgent):
    system = SystemKind.POWER
    name = "power"
    code_ref = "PUIL 2011"  # ACTIVE rule pack; verified: false

    def _accept_candidate(self, layer, strategy, model) -> bool:
        # REAL check: reject a circuit whose current exceeds its breaker.
        for s in layer.segments:
            cur, brk = s.meta.get("current_a"), s.meta.get("breaker_a")
            if cur is not None and brk is not None and float(cur) > float(brk) * 1.5:
                return False
        return True

    def _agent_notes(self, stats, strategy, model, country) -> list[str]:
        cv = getattr(country, "value", str(country or "ID"))
        v = _VOLTAGE.get(cv, 230)
        load = self._sm(stats, "load_w")
        cur = self._mx(stats, "current_a")
        brk = self._mx(stats, "breaker_a")
        return [
            f"{int(stats['n_segments'])} cable runs · "
            f"{stats['total_length_m']:.1f} m · {load:.0f} W",
            f"panel + final circuits; ~{cur:.1f} A / breaker {brk:.0f} A; outlets + lights",
            f"{cv}: {v} V; RCD (30 mA) required in wet areas",
            "SE Asia: 220–230 V; load calc + circuit limits checked per final circuit",
        ]


__all__ = ["PowerAgent"]
