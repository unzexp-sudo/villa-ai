"""Additive interior feature: 3D model + walkthrough.

Read-only projection of a BuildingModel. See docs/INTERIOR_MODULE_CONTRACT.md.
The MEP engine contract is untouched; this package emits no runs, layers or events.
"""

__all__ = ["contract", "scene", "routes"]
