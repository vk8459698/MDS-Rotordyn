"""PDF briefing for a Rotor Buddy run — findings table plus graphs from the dump."""

from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Image,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .buddy import (
    RATED_FRACTION,
    BuddyReport,
    _iso_limits_mil,
    _mask_runs,
    _segments,
    iso_zone,
)
from .waveforms import bearing_pairs

NAVY = colors.HexColor("#1B3A4B")
SLATE = colors.HexColor("#334155")
MUTED = colors.HexColor("#64748B")
LINE = colors.HexColor("#E2E8F0")
ALARM = colors.HexColor("#B42318")
WATCH = colors.HexColor("#B54708")
CLEAR = colors.HexColor("#027A48")
INFO = colors.HexColor("#175CD3")
ALARM_BG = colors.HexColor("#FEF3F2")
WATCH_BG = colors.HexColor("#FFFAEB")
CLEAR_BG = colors.HexColor("#ECFDF3")
INFO_BG = colors.HexColor("#EFF6FF")
HEADER_BG = colors.HexColor("#F1F5F9")

COLORS = ["#1B4F72", "#B42318", "#B54708", "#027A48", "#64748B", "#7C3AED", "#0F766E", "#9F1239"]
_SEV_RANK = {"alarm": 0, "watch": 1, "info": 2, "skip": 3, "ok": 4}
_SEV_LABEL = {"alarm": "Alarm", "watch": "Watch", "info": "Info", "skip": "Skipped", "ok": "Clear"}
_SEV_COLOR = {"alarm": ALARM, "watch": WATCH, "info": INFO, "skip": INFO, "ok": CLEAR}
_SEV_BG = {"alarm": ALARM_BG, "watch": WATCH_BG, "info": INFO_BG, "skip": HEADER_BG, "ok": CLEAR_BG}


def write_pdf(report: BuddyReport, dest: str | Path) -> Path:
    """Write a graphed briefing PDF for ``report``. Returns the output path."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    s = _styles()
    pairs = bearing_pairs(report.frame) if _plottable(report.frame) else []
    by_name = {name: (fx, fy) for name, fx, fy in pairs}
    story = _cover(report, s)
    story.append(Paragraph("1. Checklist on this run", s["h1"]))
    story.append(_findings_table(report, s))
    story.append(Paragraph(
        "This is a pointer, not a sign-off. Confirm alarms on polar, Bode, orbit and waterfall.",
        s["caption"],
    ))
    if by_name:
        story.append(Paragraph("2. Graphs from this dump", s["h1"]))
        story.append(Paragraph(
            "Same FFT table the checker used. Pink bands are full-speed soaks.",
            s["body"],
        ))
        story.extend(_graph_blocks(report, by_name, s))
    notes = [n for n in report.notes if n]
    if notes:
        story.append(Paragraph("Notes", s["h2"]))
        for n in notes:
            story.append(Paragraph("•  " + n, s["bullet"]))
    story.append(Paragraph(
        "Rotor Buddy is ordinary Python with documented thresholds. It does not replace an engineer.",
        s["caption"],
    ))
    doc = SimpleDocTemplate(
        str(dest),
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.70 * inch,
        bottomMargin=0.72 * inch,
        title=f"Rotor Buddy — {report.source}",
        author="Rotor Buddy",
    )
    doc.build(story, onFirstPage=_header_footer, onLaterPages=_header_footer)
    return dest


def _plottable(frame: pd.DataFrame | None) -> bool:
    if frame is None or frame.empty:
        return False
    need = {"channel", "rpm", "amp_1x", "phase_1x"}
    return need.issubset(frame.columns)


def _color(names: list[str], name: str) -> str:
    try:
        return COLORS[names.index(name) % len(COLORS)]
    except ValueError:
        return COLORS[0]


def _style_ax(ax):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(True, alpha=0.28, linestyle=":")
    ax.tick_params(labelsize=8, colors="#334155")


def _fig_image(fig, width_in: float, height_in: float) -> Image:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=140, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return Image(buf, width=width_in * inch, height=height_in * inch)


def _segs_of(fx: pd.DataFrame) -> dict:
    return _segments(fx["rpm"].to_numpy(dtype=float))


def _first_up(segs: dict) -> np.ndarray:
    islands = _mask_runs(segs["up"])
    return islands[0] if islands else segs["up"]


def _circ_median(deg) -> float:
    ang = np.deg2rad(np.asarray(deg, dtype=float))
    ang = ang[np.isfinite(ang)]
    if ang.size == 0:
        return float("nan")
    return float(np.rad2deg(np.angle(np.mean(np.exp(1j * ang))))) % 360.0


def _counts(report: BuddyReport) -> dict[str, int]:
    return {k: sum(1 for f in report.findings if f.severity == k) for k in _SEV_RANK}


def _collapsed(report: BuddyReport) -> list[tuple[str, str, str, str]]:
    """One row per check: worst severity and the bearings at that severity."""
    groups: dict[str, list] = {}
    titles: dict[str, str] = {}
    for finding in report.findings:
        groups.setdefault(finding.check_id, []).append(finding)
        titles.setdefault(finding.check_id, finding.title.split(":", 1)[-1].strip())
    rows = []
    for check_id, items in sorted(groups.items(), key=lambda kv: (kv[0].zfill(3), kv[0])):
        worst = min(items, key=lambda f: _SEV_RANK.get(f.severity, 9))
        tagged = [f for f in items if f.severity == worst.severity]
        bearings = []
        for finding in tagged:
            head = finding.title.split(":", 1)[0].strip()
            if head and head not in bearings and len(head) < 24:
                bearings.append(head)
        who = ", ".join(bearings) if bearings else ""
        label = _SEV_LABEL[worst.severity]
        if who and worst.severity != "ok":
            label = f"{label} {who}" if worst.severity != "skip" else label
        detail = worst.summary
        if who and worst.severity == "ok":
            detail = f"{who}: {detail}"
        rows.append((check_id, titles[check_id], label, detail))
    return rows


def _headline(report: BuddyReport) -> str:
    alarms = [f for f in report.findings if f.severity == "alarm"]
    watches = [f for f in report.findings if f.severity == "watch"]
    picks = alarms or watches
    if not picks:
        return "No alarms or watches. Probes and ISO look clear on this file; still confirm on the plots."
    seen: list[str] = []
    for finding in picks:
        if finding.summary not in seen:
            seen.append(finding.summary)
        if len(seen) >= 4:
            break
    return " ".join(seen)


def _styles():
    base = getSampleStyleSheet()
    return {
        "cover": ParagraphStyle("cover", parent=base["Title"], fontName="Times-Bold", fontSize=20, leading=24, textColor=NAVY, spaceAfter=4),
        "sub": ParagraphStyle("sub", parent=base["Normal"], fontName="Times-Italic", fontSize=11, leading=14, textColor=SLATE, spaceAfter=6),
        "meta": ParagraphStyle("meta", parent=base["Normal"], fontName="Times-Roman", fontSize=9, leading=12, textColor=MUTED, spaceAfter=10),
        "h1": ParagraphStyle("h1", parent=base["Heading1"], fontName="Times-Bold", fontSize=13, leading=17, textColor=NAVY, spaceBefore=10, spaceAfter=6),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontName="Times-Bold", fontSize=11, leading=14, textColor=NAVY, spaceBefore=10, spaceAfter=5),
        "body": ParagraphStyle("body", parent=base["Normal"], fontName="Times-Roman", fontSize=10, leading=13, textColor=SLATE, alignment=TA_JUSTIFY, spaceAfter=6),
        "bullet": ParagraphStyle("bullet", parent=base["Normal"], fontName="Times-Roman", fontSize=9, leading=12, textColor=SLATE, leftIndent=12, spaceAfter=3),
        "cell": ParagraphStyle("cell", parent=base["Normal"], fontName="Times-Roman", fontSize=8, leading=11, textColor=SLATE),
        "cellh": ParagraphStyle("cellh", parent=base["Normal"], fontName="Times-Bold", fontSize=8, leading=11, textColor=NAVY),
        "status": ParagraphStyle("status", parent=base["Normal"], fontName="Times-Bold", fontSize=8, leading=11, alignment=TA_CENTER),
        "caption": ParagraphStyle("caption", parent=base["Normal"], fontName="Times-Italic", fontSize=8, leading=11, textColor=MUTED, spaceAfter=8, spaceBefore=2),
        "callout": ParagraphStyle("callout", parent=base["Normal"], fontName="Times-Roman", fontSize=10, leading=13, textColor=colors.HexColor("#7A271A")),
        "callout_t": ParagraphStyle("callout_t", parent=base["Normal"], fontName="Times-Bold", fontSize=10, leading=13, textColor=ALARM, spaceAfter=4),
    }


def _header_footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(NAVY)
    canvas.setLineWidth(1.6)
    canvas.line(0.75 * inch, letter[1] - 0.48 * inch, letter[0] - 0.75 * inch, letter[1] - 0.48 * inch)
    canvas.setFont("Times-Italic", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(0.75 * inch, letter[1] - 0.40 * inch, "Rotor Buddy  ·  automated checklist")
    canvas.drawRightString(letter[0] - 0.75 * inch, letter[1] - 0.40 * inch, date.today().isoformat())
    canvas.setStrokeColor(LINE)
    canvas.setLineWidth(0.6)
    canvas.line(0.75 * inch, 0.55 * inch, letter[0] - 0.75 * inch, 0.55 * inch)
    canvas.setFont("Times-Roman", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(0.75 * inch, 0.38 * inch, "Flags where to look. Not a substitute for polar, Bode, orbit and waterfall.")
    canvas.drawRightString(letter[0] - 0.75 * inch, 0.38 * inch, f"Page {doc.page}")
    canvas.restoreState()


def _cover(report: BuddyReport, s) -> list:
    counts = _counts(report)
    kpis = Table([[
        Paragraph(f"<b>{counts['alarm']}</b><br/>Alarms", ParagraphStyle("k", parent=s["cell"], alignment=TA_CENTER, fontSize=11, leading=15, textColor=ALARM)),
        Paragraph(f"<b>{counts['watch']}</b><br/>Watches", ParagraphStyle("k2", parent=s["cell"], alignment=TA_CENTER, fontSize=11, leading=15, textColor=WATCH)),
        Paragraph(f"<b>{counts['ok']}</b><br/>Clear", ParagraphStyle("k3", parent=s["cell"], alignment=TA_CENTER, fontSize=11, leading=15, textColor=CLEAR)),
        Paragraph(
            f"<b>{len(report.bearings)}</b><br/>Bearings · {report.rpm_min:.0f}–{report.rpm_max:.0f} rpm",
            ParagraphStyle("k4", parent=s["cell"], alignment=TA_CENTER, fontSize=11, leading=15, textColor=NAVY),
        ),
    ]], colWidths=[1.72 * inch] * 4)
    kpis.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, 0), ALARM_BG),
        ("BACKGROUND", (1, 0), (1, 0), WATCH_BG),
        ("BACKGROUND", (2, 0), (2, 0), CLEAR_BG),
        ("BACKGROUND", (3, 0), (3, 0), HEADER_BG),
        ("BOX", (0, 0), (-1, -1), 0.4, LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, LINE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))
    tone = ALARM if counts["alarm"] else (WATCH if counts["watch"] else CLEAR)
    callout = Table([
        [Paragraph("Read these first", s["callout_t"])],
        [Paragraph(_headline(report), s["callout"])],
    ], colWidths=[6.9 * inch])
    callout.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), ALARM_BG if counts["alarm"] else (WATCH_BG if counts["watch"] else CLEAR_BG)),
        ("BOX", (0, 0), (-1, -1), 1.0, tone),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (0, 0), 8),
        ("BOTTOMPADDING", (0, -1), (-1, -1), 10),
    ]))
    return [
        Paragraph("Rotor Buddy", s["cover"]),
        Paragraph("Checklist report — where to look on this dump", s["sub"]),
        Paragraph(
            f"Source: <b>{report.source}</b>  ·  {report.n_samples} aligned samples  ·  "
            f"bearings {', '.join(report.bearings) or '—'}  ·  amplitude in {report.amp_unit}<br/>"
            "Scope: Kiran Toram, 29 August 2026, v001  ·  ISO 20816-2 Table B.1 (gas turbine, 3600 r/min class when speed allows)",
            s["meta"],
        ),
        kpis,
        Spacer(1, 10),
        callout,
    ]


def _findings_table(report: BuddyReport, s) -> Table:
    header = [
        Paragraph("#", s["cellh"]),
        Paragraph("Check", s["cellh"]),
        Paragraph("Result", s["cellh"]),
        Paragraph("What the data shows", s["cellh"]),
    ]
    data = [header]
    sevs = []
    for check_id, title, label, detail in _collapsed(report):
        key = "ok"
        for name, word in (("alarm", "Alarm"), ("watch", "Watch"), ("skip", "Skipped"), ("info", "Info")):
            if label.startswith(word):
                key = name
                break
        sevs.append(key)
        data.append([
            Paragraph(check_id, s["cell"]),
            Paragraph(title, s["cell"]),
            Paragraph(label, ParagraphStyle("st", parent=s["status"], textColor=_SEV_COLOR[key])),
            Paragraph(detail, s["cell"]),
        ])
    table = Table(data, colWidths=[0.45 * inch, 1.55 * inch, 1.35 * inch, 3.55 * inch], repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
        ("BOX", (0, 0), (-1, -1), 0.4, LINE),
        ("INNERGRID", (0, 0), (-1, -1), 0.35, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
    ]
    for i, key in enumerate(sevs, start=1):
        style.append(("BACKGROUND", (2, i), (2, i), _SEV_BG[key]))
    table.setStyle(TableStyle(style))
    return table


def _graph_blocks(report: BuddyReport, by_name: dict, s) -> list:
    names = list(by_name)
    fx0, _fy0 = next(iter(by_name.values()))
    samples = fx0.index.to_numpy()
    rpm0 = fx0["rpm"].to_numpy(float)
    segs0 = _segs_of(fx0)
    blocks: list = []

    def add(title: str, image: Image, caption: str):
        blocks.append(KeepTogether([Paragraph(title, s["h2"]), image, Paragraph(caption, s["caption"])]))

    add("Run profile", _chart_speed(samples, rpm0, segs0),
        "Keyphasor speed on each sync timebase. Pink = full-speed soak.")
    add("Overall vibration", _chart_direct(by_name, names),
        f"Direct (peak-to-peak of the raw timebase), larger of X and Y, in {report.amp_unit}.")
    iso = _chart_iso(by_name, names, rpm0)
    if iso is not None:
        add("Full-speed overall vs ISO 20816-2", iso,
            "Median Direct at ≥97% of peak speed. Load is not in the dump, so this dwell is treated as FSNL.")
    add("Startup 1X Bode (X probe)", _chart_bode(by_name, names),
        "Median 1X on the first coast-up. Amplitude hump plus phase roll is a critical — confirm on Bode/polar.")
    polar_names = _focus_bearings(report, names)
    add("1X polar — first coast-up", _chart_polars(by_name, polar_names),
        "Radius = 1X amplitude, angle = 1X phase lag (Bently, clockwise). Colour is shaft speed.")
    if "subsync_max" in fx0.columns:
        add("Oil-whirl band", _chart_whirl(by_name, names),
            "Median 0.30–0.48X / 1X in speed bins. High in the pink band is the waterfall look.")
    soak_name = _soak_bearing(report, names)
    if soak_name:
        add(f"{soak_name} 1X at the full-speed soaks", _chart_soaks(by_name[soak_name], soak_name),
            "Red bars are median 1X inside each full-speed island. A jump at constant speed is not a Bode peak.")
        add(f"{soak_name} 1X orbit (reconstructed)", _chart_orbits(by_name[soak_name], soak_name),
            "1X-filtered orbit from the X and Y vectors, not the raw timebase. Confirm on the ADRE orbit plot.")
    return blocks


def _focus_bearings(report: BuddyReport, names: list[str]) -> list[str]:
    scored: list[tuple[int, str]] = []
    for finding in report.findings:
        head = finding.title.split(":", 1)[0].strip()
        if head in names:
            scored.append((_SEV_RANK.get(finding.severity, 9), head))
    ordered = []
    for _rank, name in sorted(scored):
        if name not in ordered:
            ordered.append(name)
    picked = (ordered or names)[:2]
    if len(picked) < 2 and names:
        for name in names:
            if name not in picked:
                picked.append(name)
            if len(picked) == 2:
                break
    return picked


def _soak_bearing(report: BuddyReport, names: list[str]) -> str | None:
    for finding in sorted(report.findings, key=lambda f: _SEV_RANK.get(f.severity, 9)):
        if finding.check_id != "9":
            continue
        head = finding.title.split(":", 1)[0].strip()
        if head in names:
            return head
    return names[0] if names else None


def _chart_speed(samples, rpm, segs) -> Image:
    fig, ax = plt.subplots(figsize=(7.2, 2.2), dpi=140)
    ax.plot(samples, rpm, color="#1B3A4B", lw=1.4)
    for island in _mask_runs(segs["dwell"]):
        xs = samples[island]
        if xs.size:
            ax.axvspan(float(xs.min()), float(xs.max()), color="#FEF3F2", zorder=0)
    ax.set_xlabel("Waveform sample number")
    ax.set_ylabel("Shaft speed (rpm)")
    ax.set_title("Run profile", loc="left", fontsize=10, color="#1B3A4B")
    ax.set_ylim(0, max(4000, float(np.nanmax(rpm)) * 1.1) if np.isfinite(rpm).any() else 4000)
    _style_ax(ax)
    fig.tight_layout()
    return _fig_image(fig, 6.9, 2.1)


def _chart_direct(by_name: dict, names: list[str]) -> Image:
    fig, ax = plt.subplots(figsize=(7.2, 2.35), dpi=140)
    painted = False
    for name, (fx, fy) in by_name.items():
        dx = fx["direct"].to_numpy(float) if "direct" in fx.columns else fx["amp_1x"].to_numpy(float)
        dy = fy["direct"].to_numpy(float) if "direct" in fy.columns else fy["amp_1x"].to_numpy(float)
        ax.plot(fx.index.to_numpy(), np.maximum(dx, dy), lw=1.15, color=_color(names, name), label=name)
        if not painted:
            for island in _mask_runs(_segs_of(fx)["dwell"]):
                xs = fx.index.to_numpy()[island]
                if xs.size:
                    ax.axvspan(float(xs.min()), float(xs.max()), color="#FEF3F2", zorder=0)
            painted = True
    ax.set_xlabel("Waveform sample number")
    ax.set_ylabel("Direct (mil pp)")
    ax.set_title("Overall vibration through the run", loc="left", fontsize=10, color="#1B3A4B")
    ax.legend(frameon=False, fontsize=8, ncol=min(5, max(1, len(names))), loc="upper left")
    _style_ax(ax)
    fig.tight_layout()
    return _fig_image(fig, 6.9, 2.25)


def _chart_iso(by_name: dict, names: list[str], rpm0) -> Image | None:
    rated = float(np.nanmax(rpm0)) if np.isfinite(rpm0).any() else 0.0
    if rated <= 0:
        return None
    ab, bc, _cd = _iso_limits_mil(rated, "gas")
    labels, vals = [], []
    for name, (fx, fy) in by_name.items():
        dwell = _segs_of(fx)["dwell"]
        if not dwell.any():
            continue
        dx = fx["direct"].to_numpy(float) if "direct" in fx.columns else fx["amp_1x"].to_numpy(float)
        dy = fy["direct"].to_numpy(float) if "direct" in fy.columns else fy["amp_1x"].to_numpy(float)
        labels.append(name)
        vals.append(float(np.nanmedian(np.maximum(dx, dy)[dwell])))
    if not labels:
        return None
    fig, ax = plt.subplots(figsize=(7.2, 2.3), dpi=140)
    ax.bar(labels, vals, color=[_color(names, n) for n in labels], width=0.62)
    ax.axhline(ab, color="#027A48", ls="--", lw=1.2, label=f"ISO A/B  {ab:.2f}")
    ax.axhline(bc, color="#B54708", ls="--", lw=1.2, label=f"ISO B/C  {bc:.2f}")
    for lab, val in zip(labels, vals):
        zone = iso_zone(val, rated, "gas")
        ax.text(lab, val + max(vals) * 0.04, f"{val:.2f}\n{zone}", ha="center", va="bottom", fontsize=7.5, color="#334155")
    ax.set_ylabel("Direct, mil peak-to-peak")
    ax.set_ylim(0, max(6.5, max(vals) * 1.35, bc * 1.1))
    ax.set_title("FSNL overall vs ISO 20816-2", loc="left", fontsize=10, color="#1B3A4B")
    ax.legend(frameon=False, fontsize=8, loc="upper right")
    _style_ax(ax)
    fig.tight_layout()
    return _fig_image(fig, 6.9, 2.2)


def _chart_bode(by_name: dict, names: list[str]) -> Image:
    fig, ax = plt.subplots(figsize=(7.2, 2.6), dpi=140)
    bins = np.linspace(200, max(3600, max(float(np.nanmax(fx["rpm"])) for fx, _ in by_name.values()) + 50), 25)
    centres = 0.5 * (bins[:-1] + bins[1:])
    for name, (fx, _fy) in by_name.items():
        rpm = fx["rpm"].to_numpy(float)
        amp = fx["amp_1x"].to_numpy(float)
        up = _first_up(_segs_of(fx))
        ys = []
        for lo, hi in zip(bins[:-1], bins[1:]):
            sel = up & (rpm >= lo) & (rpm < hi) & np.isfinite(amp)
            ys.append(float(np.nanmedian(amp[sel])) if sel.any() else np.nan)
        ax.plot(centres, ys, lw=1.6, color=_color(names, name), label=f"{name}X")
    ax.set_xlabel("Shaft speed (rpm)")
    ax.set_ylabel("1X amplitude (mil pp)")
    ax.set_title("Startup 1X Bode — first coast-up", loc="left", fontsize=10, color="#1B3A4B")
    ax.legend(frameon=False, fontsize=8, ncol=min(5, max(1, len(names))), loc="upper left")
    _style_ax(ax)
    fig.tight_layout()
    return _fig_image(fig, 6.9, 2.45)


def _chart_polars(by_name: dict, focus: list[str]) -> Image:
    n = max(1, len(focus))
    fig, axes = plt.subplots(1, n, figsize=(7.2, 3.2), dpi=140, subplot_kw={"projection": "polar"})
    if n == 1:
        axes = [axes]
    last = None
    names = list(by_name)
    for ax, name in zip(axes, focus):
        fx, _fy = by_name[name]
        up = _first_up(_segs_of(fx))
        if not up.any():
            up = np.ones(len(fx), dtype=bool)
        rpm = fx["rpm"].to_numpy(float)[up]
        amp = fx["amp_1x"].to_numpy(float)[up]
        phase = np.deg2rad(fx["phase_1x"].to_numpy(float)[up])
        ax.set_theta_direction(-1)
        ax.set_theta_offset(np.pi / 2)
        last = ax.scatter(phase, amp, c=rpm, s=14, cmap="viridis", edgecolors="none")
        ax.set_title(f"{name} 1X polar", fontsize=9, color="#1B3A4B", pad=12)
        ax.tick_params(labelsize=7)
        top = float(np.nanpercentile(amp, 98)) if np.isfinite(amp).any() else 1.0
        ax.set_ylim(0, max(0.2, top * 1.15))
    if last is not None:
        fig.subplots_adjust(wspace=0.35, right=0.88)
        fig.colorbar(last, ax=list(axes), shrink=0.72, pad=0.08, label="rpm")
    return _fig_image(fig, 6.9, 3.0)


def _chart_whirl(by_name: dict, names: list[str]) -> Image:
    fig, ax = plt.subplots(figsize=(7.2, 2.5), dpi=140)
    peak = max(float(np.nanmax(fx["rpm"])) for fx, _ in by_name.values())
    bins = np.linspace(200, peak + 50, 28)
    centres = 0.5 * (bins[:-1] + bins[1:])
    for name, (fx, fy) in by_name.items():
        rpm = fx["rpm"].to_numpy(float)
        a1 = np.maximum(fx["amp_1x"].to_numpy(float), fy["amp_1x"].to_numpy(float))
        whirl = np.maximum(fx["subsync_max"].to_numpy(float), fy["subsync_max"].to_numpy(float))
        ratio = np.where(a1 > 0.05, whirl / a1, np.nan)
        ys = []
        for lo, hi in zip(bins[:-1], bins[1:]):
            sel = (rpm >= lo) & (rpm < hi) & np.isfinite(ratio)
            ys.append(float(np.nanmedian(ratio[sel])) if sel.any() else np.nan)
        ax.plot(centres, ys, lw=1.6, color=_color(names, name), label=name)
    ax.axvspan(RATED_FRACTION * peak, peak + 20, color="#FEF3F2", zorder=0)
    ax.axhline(0.15, color="#B54708", ls="--", lw=0.9, label="watch ~15% of 1X")
    ax.set_xlabel("Shaft speed (rpm)")
    ax.set_ylabel("0.30–0.48X / 1X")
    ax.set_title("Oil-whirl band vs running speed", loc="left", fontsize=10, color="#1B3A4B")
    ax.set_ylim(0, None)
    ax.legend(frameon=False, fontsize=8, ncol=min(3, max(1, len(names))), loc="upper left")
    _style_ax(ax)
    fig.tight_layout()
    return _fig_image(fig, 6.9, 2.4)


def _chart_soaks(pair: tuple[pd.DataFrame, pd.DataFrame], name: str) -> Image:
    fx, _fy = pair
    amp = fx["amp_1x"].to_numpy(float)
    samples = fx.index.to_numpy()
    fig, ax = plt.subplots(figsize=(7.2, 2.3), dpi=140)
    ax.plot(samples, amp, color=_color([name], name), lw=1.4)
    islands = [m for m in _mask_runs(_segs_of(fx)["dwell"]) if m.sum() >= 2]
    for i, island in enumerate(islands, 1):
        xs = samples[island]
        med = float(np.nanmedian(amp[island]))
        ax.axvspan(float(xs.min()), float(xs.max()), color="#FEF3F2" if i == 1 else "#FFFAEB", zorder=0)
        ax.hlines(med, float(xs.min()), float(xs.max()), colors="#B42318", lw=1.6)
        ax.text(float(np.mean(xs)), med, f"  soak {i}: {med:.2f}", fontsize=8, color="#B42318", va="bottom")
    ax.set_xlabel("Waveform sample number")
    ax.set_ylabel("1X amplitude (mil pp)")
    ax.set_title(f"{name}X 1X through full-speed soaks", loc="left", fontsize=10, color="#1B3A4B")
    _style_ax(ax)
    fig.tight_layout()
    return _fig_image(fig, 6.9, 2.2)


def _orbit_xy(ampx, phx, ampy, phy, n=256):
    tau = np.linspace(0, 2 * np.pi, n, endpoint=False)
    x = (ampx / 2.0) * np.cos(tau - np.deg2rad(phx))
    y = (ampy / 2.0) * np.cos(tau - np.deg2rad(phy))
    return x, y


def _chart_orbits(pair: tuple[pd.DataFrame, pd.DataFrame], name: str) -> Image:
    fx, fy = pair
    segs = _segs_of(fx)
    masks = [("Slow roll", segs["slow"])]
    islands = [m for m in _mask_runs(segs["dwell"]) if m.sum() >= 2]
    for i, island in enumerate(islands[:2], 1):
        masks.append((f"FSNL soak {i}", island))
    if len(masks) == 1:
        masks.append(("At speed", segs["dwell"] if segs["dwell"].any() else segs["full"]))
    fig, axes = plt.subplots(1, len(masks), figsize=(7.2, 2.45), dpi=140)
    if len(masks) == 1:
        axes = [axes]
    orbits = []
    lim = 0.05
    for title, mask in masks:
        if not np.asarray(mask).any():
            orbits.append((title, np.array([0.0]), np.array([0.0]), 0.0, 0.0))
            continue
        ax_ = float(np.nanmedian(fx["amp_1x"].to_numpy(float)[mask]))
        ay_ = float(np.nanmedian(fy["amp_1x"].to_numpy(float)[mask]))
        px = _circ_median(fx["phase_1x"].to_numpy(float)[mask])
        py = _circ_median(fy["phase_1x"].to_numpy(float)[mask])
        x, y = _orbit_xy(ax_, px, ay_, py)
        orbits.append((title, x, y, ax_, ay_))
        lim = max(lim, float(np.nanmax(np.abs(x))), float(np.nanmax(np.abs(y))))
    lim *= 1.25
    for ax, (title, x, y, ax_, ay_) in zip(axes, orbits):
        ax.plot(x, y, color=_color([name], name), lw=1.5)
        ax.plot([x[0]], [y[0]], "o", color="#027A48", ms=4)
        ax.axhline(0, color="#CBD5E1", lw=0.6)
        ax.axvline(0, color="#CBD5E1", lw=0.6)
        ax.set_aspect("equal")
        ax.set_xlim(-lim, lim)
        ax.set_ylim(-lim, lim)
        ax.set_title(title, fontsize=9, color="#1B3A4B")
        ax.set_xlabel("X (mil)")
        ax.set_ylabel("Y (mil)")
        ax.text(0.5, 0.04, f"1X {ax_:.2f} / {ay_:.2f}", transform=ax.transAxes, ha="center", fontsize=7.5, color="#64748B")
        _style_ax(ax)
    fig.suptitle(f"{name} 1X orbit", fontsize=10, color="#1B3A4B", y=1.02)
    fig.tight_layout()
    return _fig_image(fig, 6.9, 2.35)
