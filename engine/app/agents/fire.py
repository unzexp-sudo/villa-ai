"""Fire specialist (plan §5-6): sprinkler/detector coverage. OPTIONAL.

Default ON for ``apartment``, OFF for ``villa`` unless explicitly enabled via
a model assumption or a ctx flag (MODULE_CONTRACT.md §0 decision 3).
"""

from __future__ import annotations

from app.contract import SystemKind

from .base import SpecialistAgent

# Explicit opt-in flag fields (model assumptions) / ctx attributes.
_EXPLICIT_FIELDS = {"fire_enabled", "fire.enabled", "systems.fire", "enable_fire"}
_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}


def fire_is_enabled(model, ctx=None) -> bool:
    """Fire default: ON for apartment, OFF for villa unless explicitly on."""
    bt = getattr(model, "building_type", None)
    bt_val = getattr(bt, "value", str(bt or ""))
    if bt_val == "apartment":
        return True
    for a in getattr(model, "assumptions", None) or []:
        field = str(getattr(a, "field", ""))
        value = str(getattr(a, "value", "")).strip().lower()
        if field in _EXPLICIT_FIELDS and value in _TRUE_VALUES:
            return True
    if ctx is not None:
        if getattr(ctx, "fire_enabled", False) or getattr(ctx, "enable_fire", False):
            return True
        enabled = getattr(ctx, "enabled_systems", None)
        if enabled is not None and SystemKind.FIRE in enabled:
            return True
    return False


class FireAgent(SpecialistAgent):
    system = SystemKind.FIRE
    name = "fire"
    code_ref = "SNI 03-3989"  # ACTIVE rule pack; verified: false

    def is_enabled(self, model, ctx=None) -> bool:
        return fire_is_enabled(model, ctx)

    def _accept_candidate(self, layer, strategy, model) -> bool:
        # REAL check: reject coverage beyond the max head spacing.
        for s in layer.segments:
            sp, mx = s.meta.get("spacing_m"), s.meta.get("max_spacing_m")
            if sp is not None and mx is not None and float(sp) > float(mx) * 1.5:
                return False
        return True

    def _agent_notes(self, stats, strategy, model, country) -> list[str]:
        sp = self._mx(stats, "spacing_m")
        heads = self._mx(stats, "sprinkler_count")
        return [
            f"{int(stats['n_devices'])} sprinkler zones · "
            f"{stats['total_length_m']:.1f} m {stats['material']} · "
            f"spacing {sp:.1f} m",
            f"~{heads:.0f} heads/zone · ~12 m²/head; detectors + sprinkler layout",
            "coverage / spacing / hydraulics / clearances checked (preliminary)",
            "SE Asia: often retrofit; apartment default ON, villa OFF unless enabled",
        ]


__all__ = ["FireAgent", "fire_is_enabled"]
