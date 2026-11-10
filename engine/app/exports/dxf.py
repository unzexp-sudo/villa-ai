"""DXF export: one layer per system, clash markers on their own layer.

Colour is carried as an ACI index derived from the frozen ``SYSTEM_COLORS`` so a
downstream CAD user sees the same five colours as the web app.
"""

from __future__ import annotations

import io

from app.contract.enums import CLASH_COLOR, SYSTEM_COLORS, SYSTEM_ORDER
from app.contract.results import RunResult

# ACI indices chosen to match the frozen palette closely enough to be legible in
# CAD, where only 256 indexed colours exist.
_ACI: dict[str, int] = {
    "#f97316": 30,   # orange
    "#3b82f6": 5,    # blue
    "#22d3ee": 4,    # cyan
    "#eab308": 2,    # yellow
    "#e6379c": 6,    # magenta
    "#ff3b30": 1,    # red
}


def _aci(hexcol: str, default: int = 7) -> int:
    return _ACI.get((hexcol or "").lower(), default)


def to_dxf(run: RunResult) -> bytes:
    import ezdxf

    doc = ezdxf.new("R2013")
    msp = doc.modelspace()

    doc.layers.add("WALLS", color=8)
    doc.layers.add("CLASHES", color=_aci(CLASH_COLOR, 1))

    for sk in SYSTEM_ORDER:
        hexcol = SYSTEM_COLORS.get(sk, "#888888") if isinstance(SYSTEM_COLORS, dict) else "#888888"
        doc.layers.add(sk.value.upper(), color=_aci(hexcol))
        layer = (run.layers or {}).get(sk)
        if layer is None:
            continue
        for seg in layer.segments:
            pts = [(p.x, p.y) for p in seg.polyline]
            if len(pts) < 2:
                continue
            msp.add_lwpolyline(pts, dxfattribs={"layer": sk.value.upper()})
        for dev in layer.devices:
            r = max(dev.size.length_mm, dev.size.width_mm, 100.0) / 2000.0
            msp.add_circle((dev.at.x, dev.at.y), radius=max(r, 0.05),
                           dxfattribs={"layer": sk.value.upper()})

    for clash in (run.clashes_after or []):
        if clash.resolved:
            continue
        msp.add_circle((clash.at.x, clash.at.y), radius=0.25,
                       dxfattribs={"layer": "CLASHES"})
        msp.add_text(clash.id, height=0.18,
                     dxfattribs={"layer": "CLASHES"}).set_placement(
                         (clash.at.x + 0.3, clash.at.y + 0.3))

    buf = io.StringIO()
    doc.write(buf)
    return buf.getvalue().encode("utf-8")


__all__ = ["to_dxf"]
