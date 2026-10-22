"""Rewrite Input Data section without popup/toast issues."""
from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Pt, RGBColor

DOC = Path(r"D:\Downloads\RotorDyn\Updates and Changes Aug Phase I.docx")
SHOTS = Path(r"D:\Downloads\RotorDyn\tools\_dash_probe_out")


def delete_paragraph(paragraph) -> None:
    el = paragraph._element
    parent = el.getparent()
    if parent is not None:
        parent.remove(el)


def trim_from_marker(doc: Document, marker: str) -> None:
    start = None
    for i, p in enumerate(doc.paragraphs):
        if marker in p.text:
            start = i
            break
    if start is None:
        return
    for p in list(doc.paragraphs[start:]):
        delete_paragraph(p)


def add_caption(doc: Document, text: str) -> None:
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(text)
    run.italic = True
    run.font.size = Pt(10)
    run.font.color.rgb = RGBColor(0x44, 0x44, 0x44)


def add_figure(doc: Document, path: Path, caption: str, width_in: float = 6.2) -> None:
    if not path.exists():
        doc.add_paragraph(f"[Missing screenshot: {path.name}]")
        return
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.add_run().add_picture(str(path), width=Inches(width_in))
    add_caption(doc, caption)


def issue(doc: Document, number: int, title: str, what: str, why: str, where: str) -> None:
    doc.add_heading(f"Issue {number}: {title}", level=2)
    p1 = doc.add_paragraph()
    b = p1.add_run("What I saw: ")
    b.bold = True
    p1.add_run(what)

    p2 = doc.add_paragraph()
    b = p2.add_run("Why this is a problem: ")
    b.bold = True
    p2.add_run(why)

    p3 = doc.add_paragraph()
    b = p3.add_run("Which files: ")
    b.bold = True
    p3.add_run(where)


def main() -> None:
    doc = Document(str(DOC))
    trim_from_marker(doc, "Input Data testing")

    doc.add_heading("Input Data testing notes — 30 August 2026", level=1)
    doc.add_paragraph(
        "I tested the website by uploading the machine files from the “Input Data” folder. "
        "Below are the problems I found, written in simple words. "
        "Each issue has a screenshot so you can see exactly what went wrong."
    )
    doc.add_paragraph(
        "Files I tried: CT2B CSVs, WC1C CSVs, Siemens 501FD2 CSVs, "
        "Vent33 Excel, Ventanilla Excel, Santa Rosa Excel, and DR_AF R2 Excel."
    )

    issue(
        doc,
        1,
        "Orbit plot looks almost empty, and the rotation arrow looks wrong",
        "When I open the Orbit plot for CT2B, I mostly see a tiny orange dot instead of a clear orbit loop. "
        "There is a small straight black arrow labeled “ROTATION”. "
        "The numbers at the top also say the vibration phase is “N/A” (not available).",
        "An orbit plot is supposed to show how the shaft is moving in a circle/oval. "
        "If it only shows a dot, I cannot tell if the machine looks healthy. "
        "The rotation arrow is also hard to understand — it looks like a tiny scratch, not a clear curved arrow "
        "showing which way the shaft turns.",
        "CT2B (Orbit page). Siemens Orbit has a similar tiny mark / straight arrow.",
    )
    add_figure(
        doc,
        SHOTS / "v_CT2B_orbit.png",
        "Screenshot 1 — CT2B Orbit: tiny orange point, straight ROTATION arrow, phase shows N/A.",
    )

    issue(
        doc,
        2,
        "On Siemens, one half of the Orbit screen is blank",
        "I set the layout to show 2 plots side by side. The left side shows the Siemens orbit. "
        "The right side only says “PANEL 2 — No plot assigned” with empty buttons.",
        "Half the screen is wasted. It looks broken, like the website forgot to put the second plot there.",
        "Siemens 501FD2 files (G1XRR / G1XRL / Keyphasor).",
    )
    add_figure(
        doc,
        SHOTS / "v_Siemens_orbit.png",
        "Screenshot 2 — Siemens Orbit: right panel empty (“No plot assigned”).",
    )

    issue(
        doc,
        3,
        "Vent33 Excel file: Orbit page says “not available” (empty white boxes)",
        "Vent33 opened, but when I go to Orbit I only get white panels saying "
        "“Not available for this sensor – casing velocity, not shaft position”.",
        "The message itself may be correct for this kind of sensor (seismic / casing vibration, not shaft probes). "
        "The confusing part is that the website still takes me to Orbit and shows empty boxes. "
        "A beginner would think the upload failed. It would be clearer to open Trend or Bode first for this file.",
        "Vent33 Mar 31 2025.xlsx.",
    )
    add_figure(
        doc,
        SHOTS / "v_Vent33_orbit.png",
        "Screenshot 3 — Vent33 Orbit page is empty with “Not available for this sensor”.",
    )
    doc.add_paragraph(
        "For the same Vent33 file, the Bode page actually works and shows useful curves "
        "(so the file did load — Orbit is just the wrong first page):"
    )
    add_figure(
        doc,
        SHOTS / "v_Vent33_bode.png",
        "Screenshot 3b — Vent33 Bode works fine for the same file.",
    )

    issue(
        doc,
        4,
        "Ventanilla Excel: a speed-like signal is shown as if it were vibration",
        "On Ventanilla, the left chart is titled like a normal vibration trend (C31K), "
        "but the values jump from about 130 up to about 3500 and stay flat. "
        "That looks like machine speed (rpm), not vibration. "
        "The status also says “Other” instead of Startup/Steady/Shutdown.",
        "If the website mixes up speed and vibration, the plots can mislead people. "
        "You might think vibration is huge (3500) when you are actually looking at rpm.",
        "1_01-03-2026_Ventanilla_34_Fuel_Gas_SU.xlsx.",
    )
    add_figure(
        doc,
        SHOTS / "v_Ventanilla_trend.png",
        "Screenshot 4 — Ventanilla: left chart looks like speed plotted as vibration.",
    )

    issue(
        doc,
        5,
        "Uploaded multi-file sets get a useless name like “merged_5_files”",
        "When I upload several CT2B or Siemens CSVs together, the top of the page names the asset "
        "“merged_5_files” or “merged_3_files” instead of something clear like “CT2B” or “Siemens 501FD2”.",
        "Later it is hard to know which machine you are looking at, especially if you uploaded several sets.",
        "CT2B and Siemens multi-CSV uploads.",
    )
    add_figure(
        doc,
        SHOTS / "v_CT2B_bode.png",
        "Screenshot 5 — Top bar shows ASSET name “merged_5_files” instead of the machine name.",
    )

    issue(
        doc,
        6,
        "Critical speed mark looks different on different machines",
        "On the Bode plot, the website draws a dashed line where it thinks the “critical speed” is "
        "(a speed where vibration often peaks). "
        "For Siemens this mark was around 2249 rpm. For CT2B it was around 1323 rpm. "
        "On Siemens, a popup message earlier also talked about much higher speeds (around 3490 / 3616 rpm), "
        "which did not match the dashed line on the Bode plot.",
        "People use that mark to judge the machine. If the website gives two different answers, "
        "it is confusing and hard to trust.",
        "Compare CT2B Bode vs Siemens Bode.",
    )
    add_figure(
        doc,
        SHOTS / "v_CT2B_bode.png",
        "Screenshot 6a — CT2B Bode: dashed critical mark near 1323 rpm.",
    )
    add_figure(
        doc,
        SHOTS / "v_Siemens_bode.png",
        "Screenshot 6b — Siemens Bode: dashed critical mark near 2249 rpm.",
    )

    doc.add_heading("Quick list (same issues, short form)", level=2)
    for line in [
        "Orbit often shows only a tiny dot + a weak straight rotation arrow; phase can say N/A.",
        "Siemens 2-plot layout leaves the right Orbit panel empty.",
        "Vent33 Orbit opens empty (seismic); Bode works — wrong default page.",
        "Ventanilla plots a speed-like channel as if it were vibration.",
        "Multi-file uploads named “merged_N_files” instead of the machine name.",
        "Critical-speed mark / messages do not always agree across machines (or even on the same Siemens load).",
    ]:
        doc.add_paragraph(line, style="List Paragraph")

    doc.add_heading("What worked", level=2)
    for line in [
        "The website accepted the CSV and Excel files from the Input Data folder without crashing.",
        "Trend and Bode plots generally appeared after upload.",
        "There is a colored speed bar at the top, and when plots are playing a pause button appears.",
        "Vent33 correctly explains that Orbit is not for seismic/casing-velocity sensors (even though landing on that page is confusing).",
    ]:
        doc.add_paragraph(line, style="List Paragraph")

    note = doc.add_paragraph()
    r = note.add_run(
        "These notes are only from my Input Data upload test on 30 August 2026. "
        "They are separate from the older Phase I checklist written above."
    )
    r.italic = True

    try:
        doc.save(str(DOC))
        print("Saved", DOC)
    except PermissionError:
        alt = DOC.with_name(DOC.stem + " - updated.docx")
        doc.save(str(alt))
        print("Original file is open/locked. Saved instead to:", alt)


if __name__ == "__main__":
    main()
