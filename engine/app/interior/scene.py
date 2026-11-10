"""Deterministic projection of a ``BuildingModel`` into an interior 3D scene.

This module is a pure, read-only function of the model: it never mutates the
input, never reads the clock and never calls the network. Only ``model.level0``
is used (v1 is single-level). All output shapes are imported from the FROZEN
``contract`` module and are never redefined here; every shape forbids extra
fields, so field names must match exactly.

Opening cutting produces REAL gaps: solid ``WallSegment`` boxes tile the wall's
(x-span x z-height) rectangle minus each opening's void rectangle. See
``docs/INTERIOR_MODULE_CONTRACT.md`` section 4 "Opening cutting".
"""

import math
from enum import Enum

from app.contract.building import BuildingModel
from app.interior.contract import (
    EYE_HEIGHT_M,
    InteriorScene,
    Pt2,
    Pt3,
    SceneOpening,
    SceneRoom,
    SceneWall,
    WallSegment,
)

_EPS = 1e-9

__all__ = ["build_scene"]


def _clamp(v: float, lo: float, hi: float) -> float:
    if v < lo:
        return lo
    if v > hi:
        return hi
    return v


def _wall_geometry(wall) -> tuple[float, float, float, float]:
    """Return ``(L, ux, uy, angle_rad)`` for a wall.

    Zero-length walls are guarded: they yield ``L == 0`` and a zero unit
    vector so no caller ever divides by zero.
    """
    dx = wall.end.x - wall.start.x
    dy = wall.end.y - wall.start.y
    length = math.hypot(dx, dy)
    if length <= _EPS:
        return 0.0, 0.0, 0.0, 0.0
    ux = dx / length
    uy = dy / length
    return length, ux, uy, math.atan2(dy, dx)


def _opening_void(op, length: float, height: float) -> tuple[float, float, float, float]:
    """Clamped ``(x0, x1, z0, z1)`` rectangle of an opening's void.

    The x-span is ``[at - width/2, at + width/2]`` clamped to ``[0, L]``.
    The z-void is ``[0, head]`` for door/passage and ``[sill, head]`` for a
    window, each clamped to ``[0, wall.height]``.
    """
    x0 = _clamp(op.at - op.width / 2.0, 0.0, length)
    x1 = _clamp(op.at + op.width / 2.0, 0.0, length)
    if op.kind == "window":
        vz0, vz1 = op.sill, op.head
    else:
        vz0, vz1 = 0.0, op.head
    z0 = _clamp(vz0, 0.0, height)
    z1 = _clamp(vz1, 0.0, height)
    return x0, x1, z0, z1


def _solid_z_intervals(voids: list[tuple[float, float]], height: float) -> list[tuple[float, float]]:
    """Complement of merged ``(z0, z1)`` voids inside ``[0, height]``."""
    merged: list[tuple[float, float]] = []
    for z0, z1 in sorted(voids):
        if z1 - z0 <= _EPS:
            continue
        if merged and z0 <= merged[-1][1] + _EPS:
            merged[-1] = (merged[-1][0], max(merged[-1][1], z1))
        else:
            merged.append((z0, z1))

    solids: list[tuple[float, float]] = []
    cursor = 0.0
    for z0, z1 in merged:
        if z0 - cursor > _EPS:
            solids.append((cursor, z0))
        cursor = max(cursor, z1)
    if height - cursor > _EPS:
        solids.append((cursor, height))
    return [(a, b) for a, b in solids if b - a > _EPS]


def _cut_segments(wall, length: float, ux: float, uy: float, ops: list) -> list[WallSegment]:
    """Tile the wall rectangle minus every opening void with solid boxes."""
    height = wall.height
    voids: list[tuple[float, float, float, float]] = []
    breakpoints = {0.0, length}
    for op in ops:
        x0, x1, z0, z1 = _opening_void(op, length, height)
        if x1 - x0 <= _EPS or z1 - z0 <= _EPS:
            continue
        voids.append((x0, x1, z0, z1))
        breakpoints.add(x0)
        breakpoints.add(x1)

    # Sorted, de-duplicated x-breakpoints (tolerant of floating collisions).
    ordered: list[float] = []
    for b in sorted(breakpoints):
        if not ordered or b - ordered[-1] > _EPS:
            ordered.append(b)

    # Each column is [xa, xb, tuple-of-solid-z-intervals]. Adjacent columns
    # with identical z-intervals are merged to keep the segment count sane.
    columns: list[list] = []
    for i in range(len(ordered) - 1):
        xa, xb = ordered[i], ordered[i + 1]
        if xb - xa <= _EPS:
            continue
        midpoint = (xa + xb) / 2.0
        zvoids = [(v[2], v[3]) for v in voids if v[0] <= midpoint <= v[1]]
        ztuple = tuple(_solid_z_intervals(zvoids, height))
        if columns and columns[-1][2] == ztuple and abs(columns[-1][1] - xa) <= _EPS:
            columns[-1][1] = xb
        else:
            columns.append([xa, xb, ztuple])

    sx, sy = wall.start.x, wall.start.y
    segments: list[WallSegment] = []
    for xa, xb, ztuple in columns:
        for z0, z1 in ztuple:
            if z1 - z0 <= _EPS:
                continue
            segments.append(
                WallSegment(
                    p1=Pt2(x=sx + ux * xa, y=sy + uy * xa),
                    p2=Pt2(x=sx + ux * xb, y=sy + uy * xb),
                    z0=z0,
                    z1=z1,
                )
            )
    return segments


def _room_scene(room) -> SceneRoom:
    pts = [(p.x, p.y) for p in room.polygon]
    n = len(pts)
    double_area = 0.0  # signed, 2 * area
    cx_num = 0.0
    cy_num = 0.0
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        cross = x0 * y1 - x1 * y0
        double_area += cross
        cx_num += (x0 + x1) * cross
        cy_num += (y0 + y1) * cross

    area = room.area_m2 if room.area_m2 > 0 else abs(double_area) / 2.0
    if abs(double_area) > _EPS:
        cx = cx_num / (3.0 * double_area)
        cy = cy_num / (3.0 * double_area)
    else:
        cx = sum(p[0] for p in pts) / n
        cy = sum(p[1] for p in pts) / n

    rtype = room.type.value if isinstance(room.type, Enum) else str(room.type)
    return SceneRoom(
        id=room.id,
        name=room.name,
        type=rtype,
        polygon=[Pt2(x=p[0], y=p[1]) for p in pts],
        area_m2=area,
        ceiling_z=room.ceiling_z,
        centroid=Pt2(x=cx, y=cy),
        eye=Pt3(x=cx, y=cy, z=EYE_HEIGHT_M),
    )


def build_scene(model: BuildingModel) -> InteriorScene:
    """Pure, deterministic projection of ``model.level0`` into an InteriorScene."""
    level = model.level0

    openings_by_wall: dict[str, list] = {}
    for op in level.openings:
        openings_by_wall.setdefault(op.wall_id, []).append(op)

    walls_out: list[SceneWall] = []
    geometry: dict[str, tuple[float, float, float, float, float]] = {}
    for wall in level.walls:
        length, ux, uy, angle_rad = _wall_geometry(wall)
        geometry[wall.id] = (wall.start.x, wall.start.y, ux, uy, angle_rad)
        walls_out.append(
            SceneWall(
                id=wall.id,
                start=Pt2(x=wall.start.x, y=wall.start.y),
                end=Pt2(x=wall.end.x, y=wall.end.y),
                thickness=wall.thickness,
                height=wall.height,
                is_exterior=wall.is_exterior,
                angle_rad=angle_rad,
                segments=_cut_segments(wall, length, ux, uy, openings_by_wall.get(wall.id, [])),
            )
        )

    openings_out: list[SceneOpening] = []
    for op in level.openings:
        sx, sy, ux, uy, angle_rad = geometry.get(op.wall_id, (0.0, 0.0, 1.0, 0.0, 0.0))
        openings_out.append(
            SceneOpening(
                id=op.id,
                wall_id=op.wall_id,
                kind=op.kind,
                at=op.at,
                width=op.width,
                sill=op.sill,
                head=op.head,
                center=Pt3(x=sx + ux * op.at, y=sy + uy * op.at, z=(op.sill + op.head) / 2.0),
                angle_rad=angle_rad,
            )
        )

    rooms_out = [_room_scene(room) for room in level.rooms]

    return InteriorScene(
        model_id=model.id,
        name=model.name,
        bbox=model.bbox(),
        ceiling_height=model.ceiling_height,
        walls=walls_out,
        openings=openings_out,
        rooms=rooms_out,
    )
