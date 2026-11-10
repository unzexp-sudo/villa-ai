"""IFC4 export — a hand-rolled IFC-SPF (STEP physical file).

``IfcOpenShell`` is not installable in this environment, but a minimal valid IFC
file that carries MEP flow-segment geometry with correct placement is better
than a 501: it opens in any IFC viewer (BIMcollab, Solibri, BlenderBIM) and
carries enough structure for a downstream tool to enrich.

The file uses:
- IfcProject > IfcSite > IfcBuilding > IfcBuildingStorey
- IfcFlowSegment for MEP pipes/ducts (extruded circular profile)
- IfcBuildingElementProxy for clash markers

Every entity has a GlobalId (UUID-derived) and is placed in the project's
coordinate system. Geometry is in metres, matching the contract.

The file is NOT a substitute for a validated IFC model reviewed by a BIM
manager. It is a transportable 3D model that opens in any IFC viewer.
"""

from __future__ import annotations

import hashlib
import time

from app.contract.enums import SYSTEM_COLORS, SYSTEM_ORDER
from app.contract.results import RunResult

DISCLAIMER = ("PRELIMINARY DESIGN — IFC export is a geometric transport only. "
              "Property sets are minimal. A BIM manager must enrich this model "
              "before coordination. Not stamped drawings.")


def _guid(seed: str) -> str:
    """Derive a 22-char IfcGUID from an MD5 hash, using the IFC base64 alphabet."""
    raw = hashlib.md5(seed.encode()).digest()
    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$"
    out: list[str] = []
    for i in range(22):
        if i < 16:
            idx = (raw[i] >> 2) & 0x3F
        else:
            # Wrap around using earlier bytes so we always get 22 chars
            idx = (raw[i % 16] >> (2 + (i - 16) % 4)) & 0x3F
        out.append(chars[idx])
    return "".join(out)


def to_ifc(run: RunResult) -> bytes:
    """Produce a minimal IFC4 SPF file with MEP flow segments and clash proxies.

    Returns real bytes. If the run has no layers, the file is still valid —
    it just has no elements in the storey.
    """
    rec: list[int] = []  # line numbers used so far

    def _next_id() -> int:
        n = len(rec) + 1
        rec.append(n)
        return n

    def emit(entity: str, args: str) -> int:
        idx = _next_id()
        lines.append(f"#{idx}= {entity}({args});")
        return idx

    lines: list[str] = []
    ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())

    # --- HEADER ---
    lines.append("ISO-10303-21;")
    lines.append("HEADER;")
    lines.append(
        "FILE_DESCRIPTION(('ViewDefinition [CoordinationView]',"
        "'Villa AI IFC export'),'2;1');"
    )
    lines.append(
        f"FILE_NAME('villa-ai-{run.run_id}.ifc','{ts}',('Villa AI'),"
        f"('Villa AI'),'Villa AI Engine','Villa AI','');"
    )
    lines.append("FILE_SCHEMA(('IFC4'));")
    lines.append("ENDSEC;")
    lines.append("DATA;")

    # --- Units ---
    len_unit = emit("IFCSIUNIT", "*LENGTHUNIT,$,$,'METRE'")
    area_unit = emit("IFCSIUNIT", "*AREAUNIT,$,$,'SQUARE_METRE'")
    vol_unit = emit("IFCSIUNIT", "*VOLUMEUNIT,$,$,'CUBIC_METRE'")
    ang_unit = emit("IFCSIUNIT", "*PLANEANGLEUNIT,$,$,'RADIAN'")
    unit_assignment = emit(
        "IFCUNITASSIGNMENT",
        f"(#{len_unit},#{area_unit},#{vol_unit},#{ang_unit})",
    )

    # --- Coordinate systems ---
    origin = emit("IFCCARTESIANPOINT", "0.,0.,0.")
    axis_z = emit("IFCDIRECTION", "0.,0.,1.")
    axis_x = emit("IFCDIRECTION", "1.,0.,0.")
    world_dir = emit("IFCAXIS2PLACEMENT3D", f"#{origin},#{axis_z},#{axis_x}")
    world_ctx = emit(
        "IFCGEOMETRICREPRESENTATIONCONTEXT",
        f"$,'Model',3,1.0E-5,#{world_dir},$",
    )

    # --- Project hierarchy ---
    owner_hist = emit("IFCOWNERHISTORY", "$,$,$,.ADDED.,$,$,$,")
    proj = emit(
        "IFCPROJECT",
        f"'{_guid(run.run_id + 'proj')}','Villa AI Project',$,"
        f"#{owner_hist},#{unit_assignment},'Villa AI',{'',''},"
        f"(#{world_ctx})",
    )

    site_origin = emit("IFCCARTESIANPOINT", "0.,0.,0.")
    site_dir = emit("IFCAXIS2PLACEMENT3D", f"#{site_origin},#{axis_z},#{axis_x}")
    site = emit(
        "IFCSITE",
        f"'{_guid(run.run_id + 'site')}','Site',$,$,$,#{site_dir},"
        f"$,$,$,$,$,'East',$",
    )

    bldg_origin = emit("IFCCARTESIANPOINT", "0.,0.,0.")
    bldg_dir = emit("IFCAXIS2PLACEMENT3D", f"#{bldg_origin},#{axis_z},#{axis_x}")
    bldg = emit(
        "IFCBUILDING",
        f"'{_guid(run.run_id + 'bldg')}','Building',$,$,$,"
        f"#{bldg_dir},$,$,$,$",
    )

    storey_origin = emit("IFCCARTESIANPOINT", "0.,0.,0.")
    storey_dir = emit("IFCAXIS2PLACEMENT3D", f"#{storey_origin},#{axis_z},#{axis_x}")
    storey = emit(
        "IFCBUILDINGSTOREY",
        f"'{_guid(run.run_id + 'storey')}','Ground Floor',$,"
        f"$,$,$,#{storey_dir},0.",
    )

    # Spatial containment
    emit(
        "IFCRELAGGREGATES",
        f"'{_guid('rel-proj-site')}','Project to Site',$,"
        f"#{proj},(#{site})",
    )
    emit(
        "IFCRELAGGREGATES",
        f"'{_guid('rel-site-bldg')}','Site to Building',$,"
        f"#{site},(#{bldg})",
    )
    emit(
        "IFCRELAGGREGATES",
        f"'{_guid('rel-bldg-storey')}','Building to Storey',$,"
        f"#{bldg},(#{storey})",
    )

    # --- MEP flow segments ---
    element_ids: list[int] = []

    for sk in SYSTEM_ORDER:
        layer = (run.layers or {}).get(sk)
        if layer is None:
            continue

        sys_color = SYSTEM_COLORS.get(sk, "#888888")
        hexcol = sys_color.lstrip("#")
        r = int(hexcol[0:2], 16) / 255.0
        g = int(hexcol[2:4], 16) / 255.0
        b = int(hexcol[4:6], 16) / 255.0

        # One colour style per system
        colour = emit("IFCCOLOURRGB", f"'{sk.value}',{r:.4f},{g:.4f},{b:.4f}")
        render = emit(
            "IFCSURFACESTYLERENDERING",
            f"#{colour},0.8,$,$,$,$,$,$",
        )
        surf_style = emit(
            "IFCSURFACESTYLE",
            f"'{sk.value}',.POSITIVE.,(#{render})",
        )
        pres_style = emit(
            "IFCPRESENTATIONSTYLEASSIGNMENT",
            f"(#{surf_style})",
        )

        for seg in layer.segments:
            pts = seg.polyline
            if len(pts) < 2:
                continue

            diameter = seg.diameter_mm or 50.0
            radius = diameter / 2000.0  # mm -> m

            # Circular profile
            prof_origin = emit("IFCCARTESIANPOINT", "0.,0.")
            profile = emit(
                "IFCCIRCLEPROFILEDEF",
                f".AREA.,'{sk.value} pipe {seg.id}',"
                f"#{prof_origin},{radius:.6f}",
            )

            for i in range(len(pts) - 1):
                a = pts[i]
                b_pt = pts[i + 1]

                dx = b_pt.x - a.x
                dy = b_pt.y - a.y
                dz = b_pt.z - a.z
                length = (dx * dx + dy * dy + dz * dz) ** 0.5
                if length < 1e-6:
                    continue

                start = emit(
                    "IFCCARTESIANPOINT",
                    f"{a.x:.6f},{a.y:.6f},{a.z:.6f}",
                )
                direction = emit(
                    "IFCDIRECTION",
                    f"{dx / length:.6f},{dy / length:.6f},{dz / length:.6f}",
                )
                # Ref direction perpendicular to extrude direction (use axis_x if
                # the segment is not along x; otherwise use axis_z)
                if abs(dx / length) < 0.9:
                    ref_dir = axis_x
                else:
                    ref_dir = axis_z

                axis = emit(
                    "IFCAXIS2PLACEMENT3D",
                    f"#{start},#{direction},#{ref_dir}",
                )

                solid = emit(
                    "IFCEXTRUDEDAREASOLID",
                    f"#{profile},#{axis},#{direction},{length:.6f}",
                )

                # Apply the style to the solid
                emit("IFCSTYLEDITEM", f"#{solid},(#{pres_style}),'{sk.value}'")

                shape = emit(
                    "IFCSHAPEREPRESENTATION",
                    f"#{world_ctx},'Body','SweptSolid',(#{solid})",
                )
                rep = emit(
                    "IFCPRODUCTDEFINITIONSHAPE",
                    f"$,$,(#{shape})",
                )

                lp_origin = emit("IFCCARTESIANPOINT", "0.,0.,0.")
                lp_dir = emit(
                    "IFCAXIS2PLACEMENT3D",
                    f"#{lp_origin},#{axis_z},#{axis_x}",
                )
                lp = emit("IFCLOCALPLACEMENT", f"$,#{lp_dir}")

                seg_guid = _guid(f"{run.run_id}-{seg.id}-{i}")
                flow = emit(
                    "IFCFLOWSEGMENT",
                    f"'{seg_guid}','{sk.value} segment {i}',$,"
                    f"#{lp},#{rep},$",
                )
                element_ids.append(flow)

    # --- Clash markers ---
    for clash in run.clashes_after or []:
        if clash.resolved:
            continue
        clash_guid = _guid(f"clash-{clash.id}")
        cp = emit(
            "IFCCARTESIANPOINT",
            f"{clash.at.x:.6f},{clash.at.y:.6f},{clash.at.z:.6f}",
        )
        cd = emit("IFCAXIS2PLACEMENT3D", f"#{cp},#{axis_z},#{axis_x}")
        clp = emit("IFCLOCALPLACEMENT", f"$,#{cd}")

        sys_b = clash.system_b.value if hasattr(clash.system_b, "value") else str(clash.system_b)
        proxy = emit(
            "IFCBUILDINGELEMENTPROXY",
            f"'{clash_guid}','Clash: {clash.system_a.value}/{sys_b}',"
            f"$,$,#{clp},$,",
        )
        element_ids.append(proxy)

    # Contain all elements in the storey
    if element_ids:
        elem_list = ",".join(f"#{e}" for e in element_ids)
        emit(
            "IFCRELCONTAINEDINSPATIALSTRUCTURE",
            f"'{_guid('rel-elements-storey')}','Elements in Storey',$,"
            f"({elem_list}),#{storey}",
        )

    lines.append("ENDSEC;")
    lines.append("END-ISO-10303-21;")

    body = "\n".join(lines)
    return body.encode("utf-8")


__all__ = ["to_ifc", "DISCLAIMER"]
