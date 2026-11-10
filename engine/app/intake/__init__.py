"""Intake: read a damaged or partial plan, and find what is missing from it.

Two separate jobs, deliberately kept apart:

* ``parse.py`` — turn an upload into a ``BuildingModel``. Whatever it cannot
  read honestly, it leaves unset and records as an assumption. It never invents
  a wall that is not there, and it always leaves ``scale`` unset until the user
  calibrates two points (the plan is explicit: "Never trust an AI-guessed
  scale").
* ``auditor.py`` — compare a model against the required-items list for its
  building type and return ``MissingItem`` cards. Rules first, LLM second; in
  v1 it is rules only, which is the honest floor.

DXF is parsed for real (``ezdxf``): LINE, LWPOLYLINE and INSERT names give walls
and room labels. PDF and raster plans cannot be read reliably without a vision
model, so ``parse_upload`` refuses them with an explicit, actionable error
rather than emitting a plausible-looking empty plan. That refusal is the honest
behaviour, and it is what the plan itself warns about in its risk table.
"""

from __future__ import annotations

from app.intake.auditor import audit, apply_item
from app.intake.parse import (
    ParseError,
    UnsupportedFormat,
    parse_dxf,
    parse_upload,
)
from app.intake.quickdraw import blank_model

__all__ = [
    "ParseError", "UnsupportedFormat", "parse_dxf", "parse_upload",
    "audit", "apply_item", "blank_model",
]
