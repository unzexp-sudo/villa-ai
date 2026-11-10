"""glTF 2.0 export for sharing the 3D view.

Line primitives only: pipes and ducts are centreline routes, and a tube mesh per
segment would multiply the file size for no visual gain at this stage. Each
system is one mesh with its own material colour.
"""

from __future__ import annotations

import base64
import json
import struct

from app.contract.enums import CLASH_COLOR, SYSTEM_COLORS, SYSTEM_ORDER
from app.contract.results import RunResult


def _hex_to_rgb(hexcol: str) -> list[float]:
    h = (hexcol or "#888888").lstrip("#")
    if len(h) != 6:
        h = "888888"
    return [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)] + [1.0]


def _lines_buffer(segments) -> bytes:
    """Pack LINE primitives as float32 positions: [x,y,z, x,y,z] per pair."""
    out = bytearray()
    for seg in segments:
        pts = seg.polyline
        for a, b in zip(pts, pts[1:]):
            out += struct.pack("<6f", a.x, a.y, a.z, b.x, b.y, b.z)
    return bytes(out)


def to_gltf(run: RunResult) -> bytes:
    buffers: list[bytes] = []
    buffer_views: list[dict] = []
    accessors: list[dict] = []
    meshes: list[dict] = []
    materials: list[dict] = []
    nodes: list[dict] = []

    def _add_mesh(name: str, segments, hexcol: str):
        data = _lines_buffer(segments)
        if not data:
            return
        offset = sum(len(b) for b in buffers)
        buffers.append(data)
        buffer_views.append({"buffer": 0, "byteOffset": offset,
                             "byteLength": len(data), "target": 34962})
        count = len(data) // 4
        accessors.append({"bufferView": len(buffer_views) - 1,
                          "componentType": 5126, "count": count, "type": "VEC3",
                          "min": [-100.0, -100.0, -100.0],
                          "max": [100.0, 100.0, 100.0]})
        materials.append({"name": f"{name}-mat", "pbrMetallicRoughness": {
            "baseColorFactor": _hex_to_rgb(hexcol), "metallicFactor": 0.0,
            "roughnessFactor": 0.7}, "doubleSided": True})
        meshes.append({"name": name, "primitives": [{
            "attributes": {"POSITION": len(accessors) - 1},
            "mode": 1,  # LINES
            "material": len(materials) - 1,
        }]})
        nodes.append({"name": name, "mesh": len(meshes) - 1})

    for sk in SYSTEM_ORDER:
        layer = (run.layers or {}).get(sk)
        if layer is None:
            continue
        hexcol = SYSTEM_COLORS.get(sk, "#888888") if isinstance(SYSTEM_COLORS, dict) else "#888888"
        _add_mesh(sk.value, layer.segments, hexcol)

    # Clash markers: short vertical ticks at each unresolved clash.
    class _Tick:
        __slots__ = ("polyline",)

        def __init__(self, at):
            from app.contract.building import Point3D

            self.polyline = [Point3D(x=at.x, y=at.y, z=at.z - 0.3),
                             Point3D(x=at.x, y=at.y, z=at.z + 0.3)]

    ticks = [_Tick(c.at) for c in (run.clashes_after or []) if not c.resolved]
    _add_mesh("clashes", ticks, CLASH_COLOR)

    owned = sum(len(b) for b in buffers)
    pad = (4 - owned % 4) % 4
    blob = b"".join(buffers) + b"\x00" * pad
    uri = "data:application/octet-stream;base64," + base64.b64encode(blob).decode()

    gltf = {
        "asset": {"version": "2.0", "generator": "Villa AI"},
        "scene": 0,
        "scenes": [{"nodes": list(range(len(nodes)))}],
        "nodes": nodes,
        "meshes": meshes,
        "materials": materials,
        "accessors": accessors,
        "bufferViews": buffer_views,
        "buffers": [{"byteLength": len(blob), "uri": uri}],
        "extras": {"run_id": run.run_id, "note": "centreline routes, preliminary design only"},
    }
    return json.dumps(gltf, separators=(",", ":")).encode("utf-8")


__all__ = ["to_gltf"]
