"""Black-and-white diagnosis review PDF with code screenshots."""

from __future__ import annotations

import struct
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

BLACK = colors.black
WHITE = colors.white
GRAY = colors.HexColor("#E8E8E8")

ROOT = Path(__file__).resolve().parents[1]
DASH = ROOT / "rotordyn website" / "Rotordyn-2" / "frontend" / "src" / "pages" / "Dashboard.jsx"
HELP = ROOT / "rotordyn website" / "Rotordyn-2" / "frontend" / "src" / "components" / "HelpBot.jsx"
OUT = ROOT / "outbox" / "Malfunction_Diagnosis_Algorithm_Review.pdf"
SHOT_DIR = ROOT / "outbox" / "_diag_shots"

WRAP = 92


def _styles() -> dict:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "title", parent=base["Normal"], fontName="Times-Bold",
            fontSize=14, leading=18, textColor=BLACK, spaceAfter=4,
        ),
        "sub": ParagraphStyle(
            "sub", parent=base["Normal"], fontName="Times-Roman",
            fontSize=10, leading=13, textColor=BLACK, spaceAfter=8, alignment=TA_JUSTIFY,
        ),
        "h1": ParagraphStyle(
            "h1", parent=base["Normal"], fontName="Times-Bold",
            fontSize=11.5, leading=15, textColor=BLACK, spaceBefore=10, spaceAfter=5,
        ),
        "body": ParagraphStyle(
            "body", parent=base["Normal"], fontName="Times-Roman",
            fontSize=10, leading=13.5, textColor=BLACK, alignment=TA_JUSTIFY, spaceAfter=6,
        ),
        "cap": ParagraphStyle(
            "cap", parent=base["Normal"], fontName="Times-Italic",
            fontSize=8.5, leading=11, textColor=BLACK, spaceBefore=2, spaceAfter=8,
        ),
        "th": ParagraphStyle(
            "th", parent=base["Normal"], fontName="Times-Bold",
            fontSize=8, leading=10.5, textColor=BLACK,
        ),
        "cellb": ParagraphStyle(
            "cellb", parent=base["Normal"], fontName="Times-Bold",
            fontSize=8, leading=10.5, textColor=BLACK,
        ),
        "cell": ParagraphStyle(
            "cell", parent=base["Normal"], fontName="Times-Roman",
            fontSize=8, leading=10.5, textColor=BLACK,
        ),
    }


def _header_footer(canvas, doc):
    canvas.saveState()
    w, h = letter
    canvas.setStrokeColor(BLACK)
    canvas.setLineWidth(0.6)
    canvas.line(0.7 * inch, h - 0.42 * inch, w - 0.7 * inch, h - 0.42 * inch)
    canvas.setFont("Times-Roman", 8)
    canvas.drawString(0.7 * inch, h - 0.34 * inch, "Malfunction diagnosis algorithm")
    canvas.drawRightString(w - 0.7 * inch, h - 0.34 * inch, "Issues and fix")
    canvas.line(0.7 * inch, 0.42 * inch, w - 0.7 * inch, 0.42 * inch)
    canvas.drawRightString(w - 0.7 * inch, 0.28 * inch, f"{doc.page}")
    canvas.restoreState()


def _wrap_line(text: str, width: int = WRAP) -> list[str]:
    if len(text) <= width:
        return [text]
    lead = len(text) - len(text.lstrip(" "))
    pad = " " * min(lead + 4, width // 2)
    body = text.strip()
    out, cur = [], ""
    for word in body.split(" "):
        trial = (cur + " " + word).strip()
        limit = width if not out else width - len(pad)
        if len(trial) <= limit:
            cur = trial
        else:
            out.append((text[:lead] if not out else pad) + cur)
            cur = word
    if cur:
        out.append((text[:lead] if not out else pad) + cur)
    return out or [text]


def render_shot(
    path: Path,
    start: int,
    end: int,
    highlights: list[int],
    dest: Path,
    label: str,
) -> Path:
    raw = path.read_text(encoding="utf-8").splitlines()
    rows: list[tuple[int, str, bool]] = []
    for n in range(start, end + 1):
        if n < 1 or n > len(raw):
            continue
        wrapped = _wrap_line(raw[n - 1].replace("\t", "    "))
        for i, piece in enumerate(wrapped):
            rows.append((n if i == 0 else 0, piece, n in highlights))

    n_rows = max(len(rows), 1)
    row_h = 0.28
    fig_h = 0.42 + n_rows * row_h + 0.18
    fig_w = 11.2
    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=140)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, fig_h)
    ax.axis("off")
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    ax.add_patch(Rectangle((0.01, 0.04), 0.98, fig_h - 0.08, fill=False, edgecolor="black", lw=0.8))
    ax.add_patch(Rectangle((0.01, fig_h - 0.40), 0.98, 0.32, facecolor="#E6E6E6", edgecolor="black", lw=0.6))
    ax.text(0.03, fig_h - 0.26, label, fontsize=8, fontfamily="monospace", color="black", va="center")

    y0 = fig_h - 0.52
    for i, (ln, text, hot) in enumerate(rows):
        y = y0 - i * row_h
        if hot:
            ax.add_patch(Rectangle((0.012, y - 0.08), 0.976, row_h, facecolor="#D8D8D8", edgecolor="none"))
        num = f"{ln:4d}" if ln else "    "
        ax.text(0.03, y + 0.04, num, fontsize=7, fontfamily="monospace", color="#555555", va="center")
        ax.text(0.11, y + 0.04, text, fontsize=7, fontfamily="monospace", color="black", va="center")

    dest.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(dest, format="png", dpi=140, bbox_inches="tight", facecolor="white", pad_inches=0.08)
    plt.close(fig)
    return dest


def shot_image(path: Path, width=7.1 * inch) -> Image:
    with path.open("rb") as f:
        f.read(16)
        w, h = struct.unpack(">II", f.read(8))
    height = width * (h / w)
    max_h = 3.35 * inch
    if height > max_h:
        width = width * (max_h / height)
        height = max_h
    return Image(str(path), width=width, height=height)


def _table(headers, rows, s, widths):
    head = [Paragraph(h, s["th"]) for h in headers]
    body = [[Paragraph(str(c), s["cellb"] if i == 0 else s["cell"]) for i, c in enumerate(row)] for row in rows]
    t = Table([head] + body, colWidths=widths, repeatRows=1)
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), GRAY),
                ("TEXTCOLOR", (0, 0), (-1, -1), BLACK),
                ("GRID", (0, 0), (-1, -1), 0.4, BLACK),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return t


def build_shots() -> dict[str, Path]:
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    specs = {
        "avg": (
            DASH, 3471, 3488, list(range(3475, 3488)),
            "Dashboard.jsx  —  runAIDiagnostics()  —  averages the whole file",
        ),
        "unbalance": (
            DASH, 3496, 3511, list(range(3496, 3512)),
            "Dashboard.jsx  —  Rule 1 unbalance  (1X/Direct + crossed a peak)",
        ),
        "peaks": (
            DASH, 3410, 3418, [3413, 3414],
            "Dashboard.jsx  —  1X peaks sorted by amplitude, not speed; no phase roll",
        ),
        "misalign": (
            DASH, 3513, 3532, list(range(3513, 3533)),
            "Dashboard.jsx  —  Rule 2 misalignment and Rule 3 looseness share 2X",
        ),
        "rub": (
            DASH, 3535, 3554, list(range(3535, 3555)),
            "Dashboard.jsx  —  Rule 4 rub and Rule 5 whirl both use Direct-1X-2X",
        ),
        "forest": (
            DASH, 3569, 3578, list(range(3569, 3579)),
            "Dashboard.jsx  —  “Random Forest” features are the same three ratios",
        ),
        "forest_leaf": (
            DASH, 3600, 3603, [3602, 3603],
            "Dashboard.jsx  —  right-hand leaf: unbalance 0.95",
        ),
        "wave": (
            DASH, 717, 747, [719, 741, 742, 743, 744, 745, 746],
            "Dashboard.jsx  —  synthesizeChannelWaveform() injects fake 0.45X",
        ),
        "help": (
            HELP, 56, 70, [58, 69, 70],
            "HelpBot.jsx  —  90° at critical; rub and oil whirl in one answer",
        ),
    }
    out = {}
    for key, (path, a, b, hl, label) in specs.items():
        dest = SHOT_DIR / f"{key}.png"
        render_shot(path, a, b, hl, dest, label)
        out[key] = dest
    return out


def build_story(s, shots):
    story = []
    story.append(Paragraph("Malfunction Diagnosis Algorithm — Issues and Fix", s["title"]))
    story.append(Paragraph(
        "The website diagnosis engine is <b>runAIDiagnostics()</b> in "
        "frontend/src/pages/Dashboard.jsx (about lines 3359–3678). That function fills the "
        "AI Diagnostics tab and the <b>primary_diagnosis</b> on generated reports. "
        "It does not call Rotor Buddy.",
        s["sub"],
    ))

    story.append(Paragraph("Where it is wrong", s["h1"]))
    story.append(Paragraph(
        "Five labels (unbalance, misalignment, looseness, rub, oil whirl) are scored from "
        "three numbers averaged over the <b>entire</b> speed sweep: 1X/Direct, 2X/1X, and "
        "Direct − 1X − 2X. Startup, dwell and shutdown are mixed. Direct and 1X/2X are "
        "peak-to-peak; they do not add, so the leftover is not residual energy. "
        "The file already tags rows as startup / steady / coastdown; this scorer ignores that.",
        s["body"],
    ))
    story.append(shot_image(shots["avg"]))
    story.append(Paragraph(
        "Figure 1. Highlighted: 1X, 2X and Direct are meaned over every row (df.map), not at rated speed.",
        s["cap"],
    ))

    story.append(Paragraph("1. Unbalance — default false positive", s["h1"]))
    story.append(Paragraph(
        "1X/Direct &gt; 0.7 gives +45. Speed crossing any picked 1X peak gives +30. "
        "A healthy 1X-dominated run is typically ~0.8–0.9 1X/Direct, so it scores 75 and "
        "is published as Rotor Mass Unbalance. High 1X/Direct only means 1X dominates Direct, "
        "which is normal. Unbalance is high 1X amplitude (ISO / baseline) plus a circular orbit.",
        s["body"],
    ))
    story.append(shot_image(shots["unbalance"]))
    story.append(Paragraph(
        "Figure 2. Rule 1. The +30 bonus fires on almost every startup Bode.",
        s["cap"],
    ))
    story.append(Paragraph(
        "The “critical” used above is not a phase-validated first critical. Peaks are sorted "
        "by amplitude, not speed, with no Bode phase roll. allCriticalSpeeds[0] is often "
        "rated speed (e.g. 3600 rpm).",
        s["body"],
    ))
    story.append(shot_image(shots["peaks"]))
    story.append(Paragraph(
        "Figure 3. validPeaks.sort by amp; firstCrit = allCriticalSpeeds[0] is that list’s first entry.",
        s["cap"],
    ))

    story.append(Paragraph("2. Misalignment and looseness share the same 2X gate", s["h1"]))
    story.append(Paragraph(
        "Misalignment is mean 2X/1X over the whole sweep (no orbit, no axial, no at-speed window). "
        "Looseness is the same 2X/1X &gt; 0.25 plus leftover Direct. There is no 3X/4X. "
        "nX is parsed in getChannelColumns() and never scored. Averaging through a critical "
        "(1X explodes) can hide real 2X; a startup 2X spike can invent it.",
        s["body"],
    ))
    story.append(shot_image(shots["misalign"]))
    story.append(Paragraph(
        "Figure 4. Rule 2 and Rule 3. Looseness comment says “subharmonic” but the code never measures 0.5X.",
        s["cap"],
    ))

    story.append(Paragraph("3. Rub and oil whirl both use leftover Direct", s["h1"]))
    story.append(Paragraph(
        "Rub: residual/Direct &gt; 0.35. Whirl: speed &gt; 2× firstRes and residual/Direct &gt; 0.25. "
        "Classic rub is 0.5X or 1/3X plus truncated orbit and startup/shutdown hysteresis. "
        "Oil whirl is persistent ~0.38–0.48X at speed. Neither frequency is measured. "
        "If firstRes is 3600 rpm, 2× is 7200 and whirl never fires on a 3600 machine.",
        s["body"],
    ))
    story.append(shot_image(shots["rub"]))
    story.append(Paragraph(
        "Figure 5. Rules 4 and 5. Same residual = meanDirect − mean1X − mean2X for both faults.",
        s["cap"],
    ))

    story.append(Paragraph("4. “Random Forest” repeats the same three ratios", s["h1"]))
    story.append(Paragraph(
        "Not a trained model. Three hand-written stumps on 1X/Direct, 2X/1X, residual, "
        "hasCritical, isHighSpeed. If 1X/Direct &gt; 0.6 and any peak exists, one leaf is "
        "about 95% unbalance. Same bias, drawn as a probability bar.",
        s["body"],
    ))
    story.append(shot_image(shots["forest"]))
    story.append(Paragraph(
        "Figure 6a. Features fed to the trees: same 1X/Direct, 2X/1X, leftover Direct.",
        s["cap"],
    ))
    story.append(shot_image(shots["forest_leaf"]))
    story.append(Paragraph(
        "Figure 6b. If 1X/Direct is high and a peak exists, the leaf is unbalance 0.95.",
        s["cap"],
    ))

    story.append(Paragraph("5. Plots and help text invent clues the data does not have", s["h1"]))
    story.append(Paragraph(
        "synthesizeChannelWaveform() injects 0.45X at 25% of 1X when speed &gt; 2400 rpm and "
        "nX is missing, so waterfall can look like oil whirl on a healthy machine. Missing 2X "
        "defaults to 15% of 1X. Peak-to-peak values are used as cosine zero-to-peak (orbits ~2× true). "
        "HelpBot says a 90° phase shift at the critical diagnoses unbalance (Bode roll through "
        "a critical is ~180°) and puts rub and oil whirl in one FAQ.",
        s["body"],
    ))
    story.append(shot_image(shots["wave"]))
    story.append(Paragraph(
        "Figure 7. Fake 0.45X (lines 741–746) and default 2X = 0.15 × 1X (line 719).",
        s["cap"],
    ))
    story.append(shot_image(shots["help"]))
    story.append(Paragraph(
        "Figure 8. HelpBot.jsx: “phase shift of 90 degrees”; rub and whirl lumped together.",
        s["cap"],
    ))

    story.append(Paragraph("Current score table (for reference)", s["h1"]))
    story.append(_table(
        ["Rule", "Points", "Label"],
        [
            ["Mean 1X / mean Direct &gt; 0.7 (or &gt; 0.5)", "+45 (or +25)", "Unbalance"],
            ["Max speed exceeds first picked 1X peak", "+30", "Unbalance"],
            ["Mean 2X / mean 1X &gt; 0.45 (or &gt; 0.25)", "+55 (or +30)", "Misalignment"],
            ["2X/1X &gt; 0.25 and residual &gt; 0.4 × 1X", "+50", "Looseness"],
            ["Residual / Direct &gt; 0.35", "+45", "Rub"],
            ["Speed &gt; 2 × first picked peak and residual/Direct &gt; 0.25", "+40", "Oil whirl"],
        ],
        s,
        [3.4 * inch, 1.9 * inch, 1.8 * inch],
    ))
    story.append(Paragraph(
        "Primary diagnosis is the highest score if it is &gt; 30. Highest score on a normal "
        "1X-dominated startup is unbalance.",
        s["cap"],
    ))

    story.append(Paragraph("How to fix (5 points)", s["h1"]))
    story.append(_table(
        ["#", "Issue", "Fix"],
        [
            [
                "1",
                "Whole-file 1X/2X/Direct averages. Direct − 1X − 2X is not residual energy.",
                "Score 1X, 2X, Direct and nX only at rated-speed dwell. Drop Direct − 1X − 2X.",
            ],
            [
                "2",
                "Unbalance from 1X/Direct and “crossed a peak”. Healthy 1X-dominant runs score ~75.",
                "Call unbalance only if 1X is high vs ISO 20816/baseline, orbit is circular, and 1X dominates at dwell. Remove the +30 bonus.",
            ],
            [
                "3",
                "Misalignment and looseness share 2X/1X. No orbit, no 3X+.",
                "Misalignment: 2X/1X at dwell plus flattened/banana orbit. Looseness: 3X/4X and/or 0.5X plus a chopped orbit.",
            ],
            [
                "4",
                "Rub and whirl use leftover Direct. Critical pick has no phase roll.",
                "Rub: 0.5X or 1/3X, and/or 0.5X growth on shutdown. Whirl: persistent 0.38–0.48X at dwell. Pick criticals by amplitude peak plus ~70–180° phase roll, sorted by speed.",
            ],
            [
                "5",
                "Fake forest, fake 0.45X in waveforms, HelpBot 90° text. Website scorer is a second, weaker copy of Buddy.",
                "Drive the Diagnostics tab from Rotor Buddy findings (already has 0.5X, whirl band, orbit). Stop injecting 0.45X / default 2X. Use peak-to-peak/2 for cosine amplitude. Fix HelpBot.",
            ],
        ],
        s,
        [0.35 * inch, 3.35 * inch, 3.40 * inch],
    ))
    return story


def main() -> Path:
    shots = build_shots()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    s = _styles()
    doc = SimpleDocTemplate(
        str(OUT),
        pagesize=letter,
        leftMargin=0.7 * inch,
        rightMargin=0.7 * inch,
        topMargin=0.55 * inch,
        bottomMargin=0.55 * inch,
        title="Malfunction Diagnosis Algorithm — Issues and Fix",
    )
    doc.build(build_story(s, shots), onFirstPage=_header_footer, onLaterPages=_header_footer)
    return OUT


if __name__ == "__main__":
    print(main())
