"""Quick draw: a starting model for someone with only a sketch.

Deliberately tiny. It produces an empty-but-valid rectangular footprint with one
room, so the confirmation screen and the drag-and-drop editor have somewhere to
draw. It invents nothing else: no fixtures, no equipment, no scale — the intake
auditor then reports every missing item, which is the point.
"""

from __future__ import annotations

import time
import uuid

from app.contract.building import (
    Assumption,
    BuildingModel,
    Level,
    Point2D,
    Room,
    Wall,
)
from app.contract.enums import BuildingType, Country, RoomType


def blank_model(width_m: float = 10.0, depth_m: float = 8.0,
                building_type: BuildingType = BuildingType.VILLA,
                country: Country = Country.ID) -> BuildingModel:
    if width_m <= 0 or depth_m <= 0:
        raise ValueError("width and depth must be greater than zero")
    now = time.time()
    w, d = float(width_m), float(depth_m)

    corners = [Point2D(x=0.0, y=0.0), Point2D(x=w, y=0.0),
               Point2D(x=w, y=d), Point2D(x=0.0, y=d)]
    walls = [
        Wall(id=f"w{i + 1}", start=corners[i], end=corners[(i + 1) % 4],
             thickness=0.15, height=3.0, is_exterior=True)
        for i in range(4)
    ]
    room = Room(id="r1", name="Whole plan", type=RoomType.LIVING,
                polygon=corners, wet=False, area_m2=round(w * d, 2))

    return BuildingModel(
        id="mdl-" + uuid.uuid4().hex[:10],
        name="Quick draw plan",
        building_type=building_type,
        country=country,
        storeys=1,
        ceiling_height=2.7,
        levels=[Level(id="L0", name="Ground floor", walls=walls, rooms=[room])],
        scale=None,
        source="quickdraw",
        created_at=now,
        updated_at=now,
        assumptions=[
            Assumption(id="as-" + uuid.uuid4().hex[:8], field="footprint",
                       value=f"{w}x{d} m",
                       reason="quick-draw rectangle; drag the walls to match the sketch",
                       source="user", created_at=now),
            Assumption(id="as-" + uuid.uuid4().hex[:8], field="scale",
                       value="1.0",
                       reason="quick draw is drawn in real metres by construction",
                       source="user", created_at=now),
        ],
    )


__all__ = ["blank_model"]
