"""Intake: read a floor plan, and find what is missing from it.

Two separate jobs, deliberately kept apart:

* ``parse.py`` — turn an upload into a ``BuildingModel``. Whatever it cannot
  read honestly, it leaves unset and records as an assumption. It never invents
  a wall that is not there, and it always leaves ``scale`` unset until the user
  calibrates two points (the plan is explicit: "Never trust an AI-guessed
  scale").
* ``auditor.py`` — compare a model against the required-items list for its
  building type and return ``MissingItem`` cards. Rules first, LLM second; in
  v1 it is rules only, which is the honest floor.

DXF is parsed for exact geometry (``ezdxf``). PDF is parsed by extracting
vector operations from the content stream, or by rasterising and edge-detecting
if the PDF is scanned. Raster images (PNG/JPG) use the same edge-detection path.
No vision model is involved — real line geometry is extracted, never guessed.
"""

from __future__ import annotations

from app.intake.auditor import audit, apply_item
from app.intake.parse import (
    ParseError,
    UnsupportedFormat,
    parse_dxf,
    parse_image,
    parse_pdf,
    parse_upload,
)
from app.intake.quickdraw import blank_model

__all__ = [
    "ParseError", "UnsupportedFormat", "parse_dxf", "parse_image", "parse_pdf",
    "parse_upload", "audit", "apply_item", "blank_model",
]
