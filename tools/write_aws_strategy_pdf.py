"""Plain PDF: AWS plan for website upload → remote extract/analyse."""

from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.graphics.shapes import Drawing, Line, Polygon, Rect, String
from reportlab.lib.units import inch
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Table, TableStyle

BLACK = colors.black
WHITE = colors.white
GRAY = colors.HexColor("#E8E8E8")

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outbox" / "AWS_Upload_Extraction_Strategy.pdf"


def esc(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def P(text: str, style) -> Paragraph:
    return Paragraph(esc(text).replace("\n", "<br/>"), style)


def _styles() -> dict:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "title", parent=base["Normal"], fontName="Times-Bold",
            fontSize=14, leading=18, textColor=BLACK, spaceAfter=4,
        ),
        "sub": ParagraphStyle(
            "sub", parent=base["Normal"], fontName="Times-Italic",
            fontSize=10, leading=13, textColor=BLACK, spaceAfter=10,
        ),
        "h1": ParagraphStyle(
            "h1", parent=base["Normal"], fontName="Times-Bold",
            fontSize=11.5, leading=15, textColor=BLACK, spaceBefore=10, spaceAfter=5,
        ),
        "body": ParagraphStyle(
            "body", parent=base["Normal"], fontName="Times-Roman",
            fontSize=10, leading=13.5, textColor=BLACK, alignment=TA_JUSTIFY, spaceAfter=6,
        ),
        "bullet": ParagraphStyle(
            "bullet", parent=base["Normal"], fontName="Times-Roman",
            fontSize=10, leading=13.5, textColor=BLACK, leftIndent=14,
            alignment=TA_LEFT, spaceAfter=3,
        ),
        "cap": ParagraphStyle(
            "cap", parent=base["Normal"], fontName="Times-Italic",
            fontSize=8.5, leading=11, textColor=BLACK, spaceBefore=2, spaceAfter=8,
        ),
        "th": ParagraphStyle(
            "th", parent=base["Normal"], fontName="Times-Bold",
            fontSize=8, leading=10.5, textColor=BLACK,
        ),
        "cell": ParagraphStyle(
            "cell", parent=base["Normal"], fontName="Times-Roman",
            fontSize=8, leading=10.5, textColor=BLACK,
        ),
        "cellb": ParagraphStyle(
            "cellb", parent=base["Normal"], fontName="Times-Bold",
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
    canvas.drawString(0.7 * inch, h - 0.34 * inch, "Website upload to remote extraction")
    canvas.drawRightString(w - 0.7 * inch, h - 0.34 * inch, "AWS, onsite, architecture")
    canvas.line(0.7 * inch, 0.42 * inch, w - 0.7 * inch, 0.42 * inch)
    canvas.drawRightString(w - 0.7 * inch, 0.28 * inch, f"{doc.page}")
    canvas.restoreState()


def _table(s, header, rows, col_widths):
    data = [[Paragraph(esc(c), s["th"]) for c in header]]
    for row in rows:
        cells = []
        for i, c in enumerate(row):
            cells.append(Paragraph(esc(c), s["cellb"] if i == 0 else s["cell"]))
        data.append(cells)
    tbl = Table(data, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), GRAY),
                ("GRID", (0, 0), (-1, -1), 0.4, BLACK),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return tbl


def _arrow(d, x1, y1, x2, y2):
    d.add(Line(x1, y1, x2, y2, strokeColor=BLACK, strokeWidth=0.8))
    dx, dy = x2 - x1, y2 - y1
    length = (dx * dx + dy * dy) ** 0.5 or 1
    ux, uy = dx / length, dy / length
    px, py = -uy, ux
    tip = (x2, y2)
    left = (x2 - 7 * ux + 3.2 * px, y2 - 7 * uy + 3.2 * py)
    right = (x2 - 7 * ux - 3.2 * px, y2 - 7 * uy - 3.2 * py)
    d.add(Polygon([tip[0], tip[1], left[0], left[1], right[0], right[1]], fillColor=BLACK, strokeColor=BLACK, strokeWidth=0.2))


def _box(d, x, y, w, h, title, sub=""):
    d.add(Rect(x, y, w, h, strokeColor=BLACK, strokeWidth=0.9, fillColor=WHITE))
    if sub:
        d.add(String(x + w / 2, y + h / 2 + 4, title, fontName="Times-Bold", fontSize=8, textAnchor="middle"))
        d.add(String(x + w / 2, y + h / 2 - 8, sub, fontName="Times-Roman", fontSize=7, textAnchor="middle"))
    else:
        d.add(String(x + w / 2, y + h / 2 - 3, title, fontName="Times-Bold", fontSize=8, textAnchor="middle"))


def _arch_drawing() -> Drawing:
    """Website upload → API → store/queue → one Windows worker → notify."""
    d = Drawing(500, 248)
    _box(d, 4, 192, 118, 48, "Customer", "browser / upload")
    _box(d, 176, 192, 148, 48, "Website + API", "login and job status")
    _box(d, 380, 192, 116, 48, "Email notice", "done or failed")
    _arrow(d, 122, 216, 174, 216)
    _arrow(d, 324, 216, 378, 216)
    d.add(String(148, 224, "HTTPS", fontName="Times-Italic", fontSize=6.5, textAnchor="middle"))

    _box(d, 62, 104, 148, 50, "File store (S3)", "zip, CSVs, plots")
    _box(d, 290, 104, 148, 50, "Job queue", "one ADRE job at a time")
    _arrow(d, 220, 192, 136, 156)
    _arrow(d, 280, 192, 364, 156)

    _box(d, 135, 8, 230, 62, "One Windows worker", "ADRE Sxp extract  then  RotorDyn analysis")
    _arrow(d, 136, 104, 210, 72)
    _arrow(d, 364, 104, 290, 72)
    _arrow(d, 250, 70, 250, 190)
    d.add(String(268, 128, "results", fontName="Times-Italic", fontSize=6.5, textAnchor="start"))
    return d


def build():
    s = _styles()
    story = []
    story.append(P("AWS plan: website upload, remote extraction, and user notice", s["title"]))
    story.append(
        P(
            "What to run, what it costs, and how several customers use one Windows box. "
            "Prices are US East (N. Virginia) Windows on-demand, about 730 hours a month. "
            "Your AWS bill will differ slightly by region.",
            s["sub"],
        )
    )

    story.append(P("1. What the customer path is", s["h1"]))
    story.append(
        P(
            "The user uploads an ADRE database on the website. The file is stored, then a "
            "Windows server opens it in ADRE Sxp, exports CSV, and RotorDyn runs the analysis. "
            "When that finishes (or fails), the user is told — they should not have to sit on "
            "Remote Desktop and watch it.",
            s["body"],
        )
    )
    story.append(P("Website (upload + status) → file store → job queue → one Windows worker (ADRE extract, then analysis) → results back to the website → email “done”.", s["bullet"]))
    story.append(
        P(
            "ADRE Sxp is a Windows desktop program with a licence. Extraction is one screen "
            "at a time. That is why we do not start by buying several Windows servers.",
            s["body"],
        )
    )

    story.append(P("2. Architecture (and the small API)", s["h1"]))
    story.append(
        P(
            "The website is the front door. The Windows box never faces the customer. "
            "Files sit in object storage. Jobs sit in a queue. One worker runs ADRE, then analysis. "
            "The website only needs three calls.",
            s["body"],
        )
    )
    story.append(
        KeepTogether(
            [
                _arch_drawing(),
                P(
                    "Figure 1. Customer stays on the website. ADRE runs only on the Windows worker.",
                    s["cap"],
                ),
            ]
        )
    )
    story.append(
        _table(
            s,
            ["API", "Who calls it", "What it does"],
            [
                [
                    "POST /api/jobs",
                    "Website after login",
                    "Accept the ADRE zip (or folder). Create job_id. Put file in S3. Enqueue. Return queued.",
                ],
                [
                    "GET /api/jobs/{id}",
                    "Website (poll every few seconds)",
                    "queued / running / done / failed, queue position, short error if failed.",
                ],
                [
                    "GET /api/jobs/{id}/results",
                    "Website when status is done",
                    "Summary, plots, and CSV links. Same analysis the desktop tool already produces.",
                ],
            ],
            [1.55 * inch, 1.7 * inch, 3.75 * inch],
        )
    )
    story.append(
        P(
            "Table 1. That is the whole contract. No customer RDP. The worker is a private machine "
            "that pulls the next job; it does not need a public URL.",
            s["cap"],
        )
    )

    story.append(P("3. The box we have now is too small", s["h1"]))
    story.append(
        P(
            "Today the remote machine is about 2 GB RAM and 30 GB disk. Windows Server alone "
            "uses most of that memory. ADRE plus Python analysis then swap to disk or crash. "
            "30 GB fills after a couple of plant databases and CSV exports. A recent async "
            "export was already tens of thousands of rows; waveform dumps are much larger.",
            s["body"],
        )
    )
    story.append(P("More RAM does not make ADRE click faster. Extraction is one GUI job. Extra RAM stops Windows from thrashing and lets analysis load large CSVs without paging. Faster disk helps ADRE load plot groups. A second computer does not help until this one is sized correctly and we have another ADRE licence.", s["body"]))

    story.append(P("4. One instance versus several — recommendation", s["h1"]))
    story.append(
        _table(
            s,
            ["Option", "When it fits", "Cost / risk"],
            [
                [
                    "One Windows worker + a queue (recommended)",
                    "Several users upload; jobs wait in line; one ADRE licence",
                    "Simple. Users see “queued” then “running”. AWS stays one bill.",
                ],
                [
                    "One big Windows box, no queue",
                    "Only staff, one job at a time, by hand",
                    "Cheapest ops, but two customers collide on the same ADRE window.",
                ],
                [
                    "Several Windows instances",
                    "Queue wait is hours every day and you have bought extra ADRE licences",
                    "Compute roughly doubles. ADRE licences usually cost more than AWS. Two GUIs are two full installs.",
                ],
                [
                    "Website on Linux, worker on Windows",
                    "Always — keep the public site off the ADRE PC",
                    "Users are not blocked if someone is logged into the worker. Linux web hosting is cheap.",
                ],
            ],
            [1.35 * inch, 2.35 * inch, 3.3 * inch],
        )
    )
    story.append(
        P(
            "Table 2. Single worker with a queue is the right first production shape. "
            "Do not add a second Windows instance until this one is 16 GB RAM and the queue is still too slow.",
            s["cap"],
        )
    )

    story.append(P("5. What to buy on AWS (resize the current machine)", s["h1"]))
    story.append(
        P(
            "Stop the instance, change type, start it. Same Windows, same ADRE install, "
            "same disk contents. Then grow the disk from 30 GB to 200 GB. No new server.",
            s["body"],
        )
    )
    story.append(
        _table(
            s,
            ["Setup", "RAM / CPU", "Disk", "Compute / mo", "Disk / mo", "About / mo"],
            [
                ["Current (t3.small)", "2 GB, 2 vCPU burst", "30 GB", "$29", "$2", "$31"],
                ["Budget (t3.xlarge)", "16 GB, 4 vCPU burst", "200 GB", "$175", "$16", "$191"],
                [
                    "Recommended (m6i.xlarge)",
                    "16 GB, 4 vCPU steady",
                    "200 GB",
                    "$275",
                    "$16",
                    "$291",
                ],
                ["If analysis still swaps (m6i.2xlarge)", "32 GB, 8 vCPU", "200 GB", "$549", "$16", "$565"],
                ["Two recommended boxes", "two × 16 GB", "2 × 200 GB", "$550", "$32", "$582 + 2nd ADRE licence"],
            ],
            [1.55 * inch, 1.35 * inch, 0.75 * inch, 1.05 * inch, 0.85 * inch, 1.45 * inch],
        )
    )
    story.append(
        P(
            "Table 3. On-demand Windows, US East, round numbers. Disk is gp3 at about $0.08 per GB-month. "
            "t3 is cheaper but slows down on long ADRE jobs once burst credits run out. "
            "m6i.xlarge is the better default for extraction that runs many minutes.",
            s["cap"],
        )
    )
    story.append(P("Other AWS lines (small next to the server): S3 for uploads ~$0.023 per GB-month. Email (SES) ~$0.10 per thousand messages. Data out to the internet ~$0.09 per GB after a small free amount. A year Savings Plan on the worker typically cuts compute 15–20% if we will keep it 24/7.", s["body"]))
    story.append(P("If this is only used in office hours, stopping the worker overnight cuts compute by about two thirds. Disk is still billed while stopped. A public customer website should stay up; stop only a staff-only box.", s["body"]))

    story.append(P("6. Several users on one machine", s["h1"]))
    story.append(P("Each upload becomes a job with a status: queued, running, done, or failed. The Windows worker takes one job at a time. The next user’s file waits. The website shows position in the queue so nobody thinks the site is broken.", s["body"]))
    story.append(P("Each job uses its own folder. Do not let two people drive ADRE by Remote Desktop at the same time. The website talks to the worker; customers never log into the Windows box.", s["body"]))
    story.append(P("Isolation is by job folder and login on the website, not by buying a VM per customer. That is how we keep one ADRE licence and a predictable bill.", s["body"]))

    story.append(P("7. Tell the user when it is finished", s["h1"]))
    story.append(P("Email when the job is done or when it fails (what went wrong, in one line). Status on the website the user can refresh or that updates by itself. Optional later: SMS. Do not use “watch Remote Desktop” as the notice.", s["body"]))
    story.append(P("The website already has a place for the user to wait. Wire it to GET /api/jobs/{id}. That is a small backend piece: save the upload, enqueue, worker reports done, send mail.", s["body"]))

    story.append(P("8. Sending a person on site (consultant)", s["h1"]))
    story.append(
        P(
            "A consultant at the plant is still the right move when the files cannot leave the site, "
            "there is no ADRE copy we can licence in the cloud, or the first visit is commissioning "
            "and teaching. It is the wrong default for every later run of the same machine. "
            "Travel and gate time dwarf the actual ADRE clicks.",
            s["body"],
        )
    )
    story.append(
        _table(
            s,
            ["Step", "On site (one unit, ~30 channels)", "Remote website job (same data)"],
            [
                [
                    "Travel, gate, IT access",
                    "Half day to two days, plus airfare/hotel",
                    "None",
                ],
                [
                    "Get the ADRE database",
                    "30 min–2 h (copy, USB, permissions)",
                    "Customer uploads a zip, a few minutes",
                ],
                [
                    "Extract in ADRE (tabular + sync + async)",
                    "45–90 min by hand, plus “Loading plot groups” waits",
                    "About 20–45 min automated (one recent 7HA-class job, including a 2 min async wait)",
                ],
                [
                    "Analysis and plots",
                    "15–30 min on a laptop",
                    "A few minutes on the worker after CSVs exist",
                ],
                [
                    "Discuss / write-up with the plant",
                    "2–4 h if that is the purpose of the visit",
                    "Email + website; optional call. No extra travel.",
                ],
                [
                    "Second unit the same trip",
                    "+1–2 h hands-on, still on site",
                    "Next job in the queue, no extra trip",
                ],
                [
                    "Typical elapsed",
                    "One working day plus travel for 1–2 machines",
                    "Same day, often under an hour of machine time after upload",
                ],
                [
                    "Cost shape",
                    "Specialist day rate plus travel, every visit",
                    "AWS worker ~$190–290/month, unlimited queued jobs once built",
                ],
            ],
            [1.45 * inch, 2.85 * inch, 2.7 * inch],
        )
    )
    story.append(
        P(
            "Table 4. Hands-on ADRE time is similar either way. The visit cost is travel and access, "
            "not the export. Use on site for air-gapped plants and first commissioning. Use the website "
            "for repeats.",
            s["cap"],
        )
    )
    story.append(
        P(
            "If the consultant is on site only to copy the database, they can upload from a hotel "
            "or office that same day and skip doing ADRE by hand — unless the plant forbids the file "
            "leaving. Do not pay a travel day to sit through “Loading plot groups” when the worker "
            "already does that.",
            s["body"],
        )
    )

    story.append(P("9. What to approve", s["h1"]))
    story.append(P("1. Resize now: 2 GB / 30 GB → 16 GB RAM / 200 GB disk. Use m6i.xlarge if we will extract often (~$290/month). Use t3.xlarge only if we must stay near $190/month and accept slower long jobs.", s["bullet"]))
    story.append(P("2. Keep a single Windows worker. Put jobs in a queue. Website API: POST /api/jobs, GET status, GET results. Customers never RDP.", s["bullet"]))
    story.append(P("3. Send a consultant on site only when data cannot leave, ADRE is not on our worker, or the visit is commissioning/training. Do not send someone for routine extract-and-plot.", s["bullet"]))
    story.append(P("4. Do not launch a second instance until this one is busy all day and a second ADRE licence is paid. Revisit 32 GB only if analysis still runs out of memory after 16 GB.", s["bullet"]))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(OUT),
        pagesize=letter,
        leftMargin=0.7 * inch,
        rightMargin=0.7 * inch,
        topMargin=0.58 * inch,
        bottomMargin=0.58 * inch,
        title="AWS plan: website upload and remote extraction",
        author="",
    )
    doc.build(story, onFirstPage=_header_footer, onLaterPages=_header_footer)
    print(OUT)


if __name__ == "__main__":
    build()
