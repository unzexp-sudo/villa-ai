"""IFC export — deliberately not implemented.

``IfcOpenShell`` is not installable here, and a hand-rolled IFC file that opens
in Revit but carries wrong property sets is worse than no file: someone would
trust it. The API maps this ``NotImplementedError`` to a 501 with this message.
"""

from __future__ import annotations

from app.contract.results import RunResult

MESSAGE = ("IFC export requires IfcOpenShell, which is not wired into this build. "
           "Use the glTF export for the 3D view or the DXF export for 2D. An IFC file "
           "that could not be validated against a schema is not produced on purpose.")


def to_ifc(run: RunResult) -> bytes:
    raise NotImplementedError(MESSAGE)


__all__ = ["to_ifc", "MESSAGE"]
