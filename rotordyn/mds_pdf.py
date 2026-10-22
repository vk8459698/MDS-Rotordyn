"""Customer-facing PDF for an MDS Engineer report."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .mds import MdsReport

NAVY = colors.HexColor("#1B3A4B")
SLATE = colors.HexColor("#334155")
MUTED = colors.HexColor("#64748B")
LINE = colors.HexColor("#E2E8F0")
ALARM = colors.HexColor("#B42318")
WATCH = colors.HexColor("#B54708")
CLEAR = colors.HexColor("#027A48")
ALARM_BG = colors.HexColor("#FEF3F2")
WATCH_BG = colors.HexColor("#FFFAEB")
CLEAR_BG = colors.HexColor("#ECFDF3")
HEADER_BG = colors.HexColor("#F1F5F9")

_COND_COLOR = {
    "unsatisfactory": ALARM,
    "unqualified": ALARM,
    "restricted": WATCH,
    "satisfactory": CLEAR,
}
_COND_BG = {
    "unsatisfactory": ALARM_BG,
    "unqualified": ALARM_BG,
    "restricted": WATCH_BG,
    "satisfactory": CLEAR_BG,
}


def write_pdf(report: MdsReport, dest: str | Path) -> Path:
    """Write the MDS customer report. Returns the output path."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    s = _styles()
    story = _cover(report, s)
    story.append(Paragraph("1. Executive summary", s["h1"]))
    story.append(Paragraph(_esc(report.executive_summary), s["body"]))
    story.append(Paragraph("2. Data quality", s["h1"]))
    story.append(Paragraph(_esc(report.data_quality), s["body"]))
    if report.observations:
        story.append(Paragraph("3. Observations", s["h1"]))
        for item in report.observations:
            story.append(Paragraph("•  " + _esc(item), s["bullet"]))
    story.append(Paragraph("4. Diagnosis", s["h1"]))
    if not report.hypotheses:
        story.append(Paragraph(
            "No malfunction signature rose above screening thresholds on this dump.",
            s["body"],
        ))
    else:
        story.append(_diagnosis_table(report, s))
        for i, hyp in enumerate(report.hypotheses, 1):
            brg = f" — {', '.join(hyp.bearings)}" if hyp.bearings else ""
            story.append(Paragraph(f"{i}. {hyp.name}{brg} ({hyp.confidence} confidence)", s["h2"]))
            story.append(Paragraph(_esc(hyp.summary), s["body"]))
            for ev in hyp.evidence[:4]:
                story.append(Paragraph("•  " + _esc(ev), s["bullet"]))
            story.append(Paragraph(_esc("Confirm: " + hyp.confirm), s["caption"]))
            story.append(Paragraph(_esc("Rule out: " + hyp.rule_out), s["caption"]))
    story.append(Paragraph("5. Recommendations", s["h1"]))
    story.append(_recs_table(report, s))
    story.append(Paragraph("6. Limitations", s["h1"]))
    for item in report.limitations:
        story.append(Paragraph("•  " + _esc(item), s["bullet"]))
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        "This is a screening diagnosis from ADRE exports. It is not a field sign-off. "
        "Confirm alarms on polar, Bode, orbit and waterfall before acting on the machine.",
        s["caption"],
    ))
    doc = SimpleDocTemplate(
        str(dest),
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.70 * inch,
        bottomMargin=0.72 * inch,
        title=f"MDS Report — {report.source}",
        author="MDS Engineer (RotorDyn)",
    )
    doc.build(story, onFirstPage=_header_footer, onLaterPages=_header_footer)
    return dest


def _esc(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _styles() -> dict:
    base = getSampleStyleSheet()
    return {
        "cover": ParagraphStyle(
            "mds_cover", parent=base["Title"], fontName="Times-Bold",
            fontSize=18, leading=22, textColor=NAVY, spaceAfter=2,
        ),
        "sub": ParagraphStyle(
            "mds_sub", parent=base["Normal"], fontName="Times-Italic",
            fontSize=11, leading=14, textColor=SLATE, spaceAfter=6,
        ),
        "meta": ParagraphStyle(
            "mds_meta", parent=base["Normal"], fontName="Times-Roman",
            fontSize=9, leading=12, textColor=MUTED, spaceAfter=10,
        ),
        "h1": ParagraphStyle(
            "mds_h1", parent=base["Heading1"], fontName="Times-Bold",
            fontSize=13, leading=17, textColor=NAVY, spaceBefore=10, spaceAfter=6,
        ),
        "h2": ParagraphStyle(
            "mds_h2", parent=base["Heading2"], fontName="Times-Bold",
            fontSize=11, leading=14, textColor=NAVY, spaceBefore=8, spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "mds_body", parent=base["Normal"], fontName="Times-Roman",
            fontSize=10, leading=13, textColor=SLATE, alignment=TA_JUSTIFY, spaceAfter=6,
        ),
        "bullet": ParagraphStyle(
            "mds_bullet", parent=base["Normal"], fontName="Times-Roman",
            fontSize=9, leading=12, textColor=SLATE, leftIndent=12, spaceAfter=3,
        ),
        "cell": ParagraphStyle(
            "mds_cell", parent=base["Normal"], fontName="Times-Roman",
            fontSize=8, leading=11, textColor=SLATE,
        ),
        "cellh": ParagraphStyle(
            "mds_cellh", parent=base["Normal"], fontName="Times-Bold",
            fontSize=8, leading=11, textColor=NAVY,
        ),
        "status": ParagraphStyle(
            "mds_status", parent=base["Normal"], fontName="Times-Bold",
            fontSize=8, leading=11, alignment=TA_CENTER,
        ),
        "caption": ParagraphStyle(
            "mds_caption", parent=base["Normal"], fontName="Times-Italic",
            fontSize=8, leading=11, textColor=MUTED, spaceAfter=6, spaceBefore=1,
        ),
        "callout": ParagraphStyle(
            "mds_callout", parent=base["Normal"], fontName="Times-Roman",
            fontSize=10, leading=13, textColor=colors.HexColor("#7A271A"),
        ),
        "callout_t": ParagraphStyle(
            "mds_callout_t", parent=base["Normal"], fontName="Times-Bold",
            fontSize=10, leading=13, textColor=ALARM, spaceAfter=4,
        ),
    }


def _header_footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(NAVY)
    canvas.setLineWidth(1.6)
    canvas.line(0.75 * inch, letter[1] - 0.48 * inch, letter[0] - 0.75 * inch, letter[1] - 0.48 * inch)
    canvas.setFont("Times-Italic", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(0.75 * inch, letter[1] - 0.40 * inch, "Machinery Diagnostics Service  ·  screening report")
    canvas.drawRightString(letter[0] - 0.75 * inch, letter[1] - 0.40 * inch, date.today().isoformat())
    canvas.setStrokeColor(LINE)
    canvas.setLineWidth(0.6)
    canvas.line(0.75 * inch, 0.55 * inch, letter[0] - 0.75 * inch, 0.55 * inch)
    canvas.setFont("Times-Roman", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(0.75 * inch, 0.38 * inch, "Not a field sign-off. Confirm on polar, Bode, orbit and waterfall.")
    canvas.drawRightString(letter[0] - 0.75 * inch, 0.38 * inch, f"Page {doc.page}")
    canvas.restoreState()


def _cover(report: MdsReport, s) -> list:
    tone = _COND_COLOR.get(report.condition, SLATE)
    bg = _COND_BG.get(report.condition, HEADER_BG)
    n_hyp = len(report.hypotheses)
    n_p1 = sum(1 for r in report.recommendations if r.priority == 1)
    kpis = Table([[
        Paragraph(
            f"<b>{report.condition.upper()}</b><br/>Condition",
            ParagraphStyle("k", parent=s["cell"], alignment=TA_CENTER, fontSize=10, leading=14, textColor=tone),
        ),
        Paragraph(
            f"<b>{n_hyp}</b><br/>Diagnoses",
            ParagraphStyle("k2", parent=s["cell"], alignment=TA_CENTER, fontSize=10, leading=14, textColor=NAVY),
        ),
        Paragraph(
            f"<b>{n_p1}</b><br/>Priority-1 actions",
            ParagraphStyle("k3", parent=s["cell"], alignment=TA_CENTER, fontSize=10, leading=14, textColor=ALARM if n_p1 else CLEAR),
        ),
        Paragraph(
            f"<b>{len(report.buddy.bearings) if report.buddy else '—'}</b><br/>Bearings",
            ParagraphStyle("k4", parent=s["cell"], alignment=TA_CENTER, fontSize=10, leading=14, textColor=NAVY),
        ),
    ]], colWidths=[1.72 * inch] * 4)
    kpis.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), bg),
        ("BACKGROUND", (1, 0), (1, 0), HEADER_BG),
        ("BACKGROUND", (2, 0), (2, 0), ALARM_BG if n_p1 else CLEAR_BG),
        ("BACKGROUND", (3, 0), (3, 0), HEADER_BG),
        ("BOX", (0, 0), (-1, -1), 0.4, LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))
    callout = Table([
        [Paragraph("Headline", s["callout_t"])],
        [Paragraph(_esc(report.headline), s["callout"])],
    ], colWidths=[6.9 * inch])
    callout.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), bg),
        ("BOX", (0, 0), (-1, -1), 1.0, tone),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (0, 0), 8),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 10),
    ]))
    rpm = ""
    if report.buddy is not None:
        rpm = f"{report.buddy.rpm_min:.0f}–{report.buddy.rpm_max:.0f} rpm  ·  "
    return [
        Paragraph("Machinery Diagnostics Service", s["cover"]),
        Paragraph("Screening report — diagnosis and recommendations", s["sub"]),
        Paragraph(
            f"Source: <b>{_esc(report.source)}</b>  ·  {rpm}{_esc(report.job_type)}<br/>"
            "Prepared by: MDS Engineer agent (RotorDyn)  ·  Rotor Buddy checklist as input  ·  "
            "ISO 20816-2 Table B.1",
            s["meta"],
        ),
        kpis,
        Spacer(1, 10),
        callout,
    ]


def _diagnosis_table(report: MdsReport, s) -> Table:
    header = [
        Paragraph("#", s["cellh"]),
        Paragraph("Malfunction", s["cellh"]),
        Paragraph("Confidence", s["cellh"]),
        Paragraph("Bearings", s["cellh"]),
    ]
    data = [header]
    for i, hyp in enumerate(report.hypotheses, 1):
        data.append([
            Paragraph(str(i), s["cell"]),
            Paragraph(_esc(hyp.name), s["cell"]),
            Paragraph(hyp.confidence, s["status"]),
            Paragraph(_esc(", ".join(hyp.bearings) or "—"), s["cell"]),
        ])
    table = Table(data, colWidths=[0.40 * inch, 3.10 * inch, 1.20 * inch, 2.20 * inch], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
        ("BOX", (0, 0), (-1, -1), 0.4, LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.35, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def _recs_table(report: MdsReport, s) -> Table:
    header = [
        Paragraph("P", s["cellh"]),
        Paragraph("Action", s["cellh"]),
        Paragraph("Why", s["cellh"]),
    ]
    data = [header]
    prios = []
    for rec in report.recommendations:
        prios.append(rec.priority)
        brg = f" [{', '.join(rec.bearings)}]" if rec.bearings else ""
        data.append([
            Paragraph(f"P{rec.priority}", s["status"]),
            Paragraph(_esc(rec.action + brg), s["cell"]),
            Paragraph(_esc(rec.why), s["cell"]),
        ])
    table = Table(data, colWidths=[0.45 * inch, 3.25 * inch, 3.20 * inch], repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
        ("BOX", (0, 0), (-1, -1), 0.4, LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.35, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
    ]
    for i, prio in enumerate(prios, start=1):
        style.append(("BACKGROUND", (0, i), (0, i), ALARM_BG if prio == 1 else (WATCH_BG if prio == 2 else CLEAR_BG)))
    table.setStyle(TableStyle(style))
    return table
