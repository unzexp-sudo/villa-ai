"""PDF report.

The disclaimer is not decoration: the build plan's whole liability position is
"preliminary design, engineer review required". It is the first paragraph after
the title, in the largest type on the page after the title itself, and it also
appears in the page footer so a photocopy keeps it.

The code section prints ``run.code.headline`` — the contract model forces that
string to "passed N of M automated checks", so the report cannot accidentally
say "code compliant" even if a caller tries.
"""

from __future__ import annotations

import io
import time

from app.contract.enums import RuleStatus, SYSTEM_COLORS, SYSTEM_ORDER
from app.contract.results import RunResult

DISCLAIMER = ("PRELIMINARY DESIGN — licensed engineer review required before "
              "construction. Not stamped drawings.")
_FOOTER = "Villa AI · preliminary design · engineer review required"


def _fmt_mm(v) -> str:
    return "—" if v is None else f"{float(v):.0f} mm"


def _system_label(k) -> str:
    return k.value if hasattr(k, "value") else str(k)


def build_report(run: RunResult) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
    )

    ss = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=ss["BodyText"], fontSize=9, leading=12)
    small = ParagraphStyle("small", parent=body, fontSize=8, textColor=colors.HexColor("#555555"))
    h1 = ParagraphStyle("h1", parent=ss["Heading1"], fontSize=17, leading=20,
                        spaceAfter=2)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=11, leading=14,
                        spaceBefore=10, spaceAfter=4)
    warn = ParagraphStyle("warn", parent=body, fontSize=9.5, leading=13,
                          textColor=colors.HexColor("#8a1c1c"))

    story: list = []
    story.append(Paragraph("Villa AI — Preliminary MEP Design", h1))
    story.append(Paragraph(DISCLAIMER, warn))
    story.append(Spacer(1, 6))

    layers = run.layers or {}
    enabled = ", ".join(_system_label(s) for s in (run.enabled_systems or [])) or "none"
    meta = [
        ["Run", run.run_id],
        ["Model", run.model_id],
        ["Status", run.status.value if hasattr(run.status, "value") else str(run.status)],
        ["Country rule pack", _system_label(run.country)],
        ["Systems designed", enabled],
        ["Started", time.strftime("%Y-%m-%d %H:%M", time.localtime(run.started_at or 0))],
    ]
    if run.finished_at:
        meta.append(["Finished", time.strftime("%Y-%m-%d %H:%M",
                                               time.localtime(run.finished_at))])
    t = Table([[Paragraph(f"<b>{k}</b>", body), Paragraph(str(v), body)] for k, v in meta],
              colWidths=[45 * mm, 130 * mm])
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.HexColor("#dddddd")),
    ]))
    story.append(t)

    # ---- per-system summary ---------------------------------------------
    story.append(Paragraph("Systems", h2))
    rows = [["System", "Runs", "Total length", "Layouts tested", "Best score"]]
    for sk in SYSTEM_ORDER:
        layer = layers.get(sk)
        if layer is None:
            rows.append([_system_label(sk), "not run", "—", "—", "—"])
            continue
        length = sum(float(s.meta.get("length_m", 0.0) or 0.0) for s in layer.segments)
        rows.append([
            _system_label(sk), f"{len(layer.segments)}",
            f"{length:.1f} m",
            f"{int(layer.metrics.get('layouts_tested', 0))}",
            f"{float(layer.metrics.get('best_score', 0.0)):.2f}",
        ])
    st = Table(rows, colWidths=[38 * mm, 22 * mm, 34 * mm, 42 * mm, 32 * mm])
    style = [
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#888888")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
    ]
    for i, sk in enumerate(SYSTEM_ORDER, start=1):
        hexcol = SYSTEM_COLORS.get(sk) if isinstance(SYSTEM_COLORS, dict) else None
        if hexcol:
            style.append(("BACKGROUND", (0, i), (0, i), colors.HexColor(hexcol)))
    st.setStyle(TableStyle(style))
    story.append(st)

    for sk in SYSTEM_ORDER:
        layer = layers.get(sk)
        if layer is None or not layer.notes:
            continue
        story.append(Paragraph(f"{_system_label(sk).capitalize()}: "
                               + "; ".join(layer.notes[:3]), small))

    # ---- clash log -------------------------------------------------------
    story.append(Paragraph("Clash log", h2))
    before = run.clashes_before or []
    after = run.clashes_after or []
    remaining = [c for c in after if not c.resolved]
    fixed = [c for c in after if c.resolved]
    story.append(Paragraph(
        f"{len(before)} clash(es) found after merge. "
        f"{len(fixed)} resolved, {len(remaining)} remaining.", body))
    if fixed:
        frows = [["Pair", "Kind", "Fix"]]
        for c in fixed[:12]:
            frows.append([f"{_system_label(c.system_a)} / {_system_label(c.system_b)}",
                          c.kind.value if hasattr(c.kind, "value") else str(c.kind),
                          (c.fix or "")[:70]])
        ft = Table(frows, colWidths=[40 * mm, 22 * mm, 110 * mm])
        ft.setStyle(TableStyle([
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
            ("FONTSIZE", (0, 1), (-1, -1), 7.5),
            ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#888888")),
        ]))
        story.append(ft)
    if remaining:
        story.append(Spacer(1, 4))
        story.append(Paragraph(
            f"{len(remaining)} clash(es) could not be fixed within the resolver "
            "limits. They are listed in the viewer and must be resolved by the "
            "reviewing engineer.", warn))

    # ---- code checks -----------------------------------------------------
    story.append(PageBreak())
    story.append(Paragraph("Automated code checks", h2))
    code = run.code
    if code is None:
        story.append(Paragraph("No code report was produced for this run.", warn))
    else:
        story.append(Paragraph(f"<b>{code.headline}</b>", body))
        story.append(Paragraph(
            f"Pack: {code.rule_pack_version} · country {_system_label(code.country)} · "
            f"{code.passed} passed, {code.failed} failed, {code.not_checked} not checked.",
            small))
        story.append(Spacer(1, 4))
        rows = [["Rule", "Status", "Detail"]]
        for o in code.outcomes:
            rows.append([o.rule_id,
                         o.status.value if hasattr(o.status, "value") else str(o.status),
                         o.detail[:110]])
        ct = Table(rows, colWidths=[24 * mm, 20 * mm, 128 * mm], repeatRows=1)
        cstyle = [
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
            ("FONTSIZE", (0, 1), (-1, -1), 7.5),
            ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#888888")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]
        for i, o in enumerate(code.outcomes, start=1):
            if o.status == RuleStatus.FAIL:
                cstyle.append(("TEXTCOLOR", (1, i), (1, i), colors.HexColor("#b91c1c")))
            elif o.status == RuleStatus.NOT_CHECKED:
                cstyle.append(("TEXTCOLOR", (1, i), (1, i), colors.HexColor("#666666")))
        ct.setStyle(TableStyle(cstyle))
        story.append(ct)
        story.append(Spacer(1, 5))
        story.append(Paragraph(
            "Rule references are unverified starting points for a local engineer. "
            "This report states which automated checks passed; it does not certify "
            "that the design complies with any code.", small))

    # ---- assumptions -----------------------------------------------------
    story.append(Paragraph("Assumptions", h2))
    if run.assumptions:
        arows = [["Field", "Value", "Source", "Reason"]]
        for a in run.assumptions[:40]:
            arows.append([a.field, a.value, a.source, a.reason[:80]])
        at = Table(arows, colWidths=[34 * mm, 28 * mm, 20 * mm, 90 * mm])
        at.setStyle(TableStyle([
            ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8),
            ("FONTSIZE", (0, 1), (-1, -1), 7.5),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.HexColor("#888888")),
        ]))
        story.append(at)
    else:
        story.append(Paragraph("No defaults were assumed for this run.", body))

    buf = io.BytesIO()

    def _footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#888888"))
        canvas.drawString(15 * mm, 10 * mm, _FOOTER)
        canvas.drawRightString(195 * mm, 10 * mm, f"page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=15 * mm, bottomMargin=18 * mm,
                            title="Villa AI — Preliminary MEP Design",
                            author="Villa AI")
    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()


__all__ = ["build_report", "DISCLAIMER"]
