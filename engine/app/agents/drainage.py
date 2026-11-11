"""Drainage specialist (plan §5-6): soil/waste + vent, gravity slope, cleanouts."""

from __future__ import annotations

from app.contract import SystemKind

from .base import SpecialistAgent


class DrainageAgent(SpecialistAgent):
    system = SystemKind.DRAINAGE
    name = "drainage"
    code_ref = "SNI 03-6481"  # ACTIVE rule pack; verified: false

    def _accept_candidate(self, layer, strategy, model) -> bool:
        # REAL check: every gravity pipe keeps a sane fall (M2: 1-2%).
        for s in layer.segments:
            if s.slope_pct is not None and not (0.5 <= float(s.slope_pct) <= 3.0):
                return False
        return True

    def _agent_notes(self, stats, strategy, model, country) -> list[str]:
        slope = stats["mean_slope_pct"]
        cleanout = self._mx(stats, "cleanout_spacing_m")
        maxrun = self._mx(stats, "max_run_length_m")
        return [
            f"{int(stats['n_trunk'])} stacks · "
            f"{stats['total_length_m']:.1f} m {stats['material']} · "
            f"slope {slope:.1f}%",
            "soil + waste + vent to stack; sewer OR septic endpoint (both considered)",
            f"cleanouts every ≤{cleanout:.0f} m; max gravity run {maxrun:.1f} m",
            f"gravity fall 1–2% (design {slope:.1f}%); SE Asia: PVC-U underfloor / below slab",
        ]


__all__ = ["DrainageAgent"]
