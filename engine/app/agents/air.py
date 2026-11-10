"""Air specialist (plan §5-6): exhaust/fresh air, ducts, condensate; SE Asia split/VRF."""

from __future__ import annotations

from app.contract import SystemKind

from .base import SpecialistAgent


class AirAgent(SpecialistAgent):
    system = SystemKind.AIR
    name = "air"
    code_ref = "SNI 03-6572"  # ACTIVE rule pack; verified: false

    def _accept_candidate(self, layer, strategy, model) -> bool:
        # REAL check: reject an unsafe duct velocity (M2 design target 5 m/s).
        for s in layer.segments:
            v = s.meta.get("velocity_mps")
            if v is not None and float(v) > 12.0:
                return False
        return True

    def _agent_notes(self, stats, strategy, model, country) -> list[str]:
        w = self._mx(stats, "width_mm")
        h = self._mx(stats, "height_mm")
        flow = self._sm(stats, "flow_lps")
        vmax = self._mx(stats, "velocity_mps")
        return [
            f"{stats['total_length_m']:.1f} m duct · "
            f"{int(stats['n_devices'])} diffusers · {w:.0f}×{h:.0f} mm",
            f"bathroom/kitchen exhaust + fresh air; total ~{flow:.0f} L/s",
            "Ø25 mm condensate drains from every indoor unit",
            f"velocity ≤12 m/s (max {vmax:.1f} m/s); SE Asia: split/VRF default — "
            "refrigerant lines + condensate + short exhaust ducts; ducted systems are the exception",
        ]


__all__ = ["AirAgent"]
