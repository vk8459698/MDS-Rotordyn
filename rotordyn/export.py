"""Headless figure export - used to produce the images in the write-up.

The interactive application is the deliverable; this module exists so the same
computation path can be rendered without a display.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from matplotlib.figure import Figure

from .dataset import RunData
from .model import PolarModel, ViewSettings
from .plots import BodePlot, annotate_conventions, build_polar_figure
from .vectors import PROBE_1X_DEG

_CONVENTION_NOTE = (
    f"Viewed from the end where 1X is {abs(PROBE_1X_DEG):.0f}\u00b0 right of TDC  |  "
    "shaft rotation CCW  |  "
    "0\u00b0 = Keyphasor pulse, phase lag increases clockwise (against rotation)"
)


def _slug(text: str) -> str:
    keep = [c.lower() if c.isalnum() else "_" for c in text]
    return "".join(keep).strip("_").replace("__", "_")


def export_polar(run: RunData, settings: ViewSettings, path: Path) -> Path:
    model = PolarModel(run)
    result = model.compute(settings)
    fig, plot_x, plot_y, colourbar = build_polar_figure(run.amp_unit)
    plot_x.set_orientation(result.theta_1x, settings.machine_view)
    plot_y.set_orientation(result.theta_1y, settings.machine_view)
    plot_x.update(
        result.amp_1x, result.phase_1x, result.rpm, result.theta_1x,
        result.r_max, settings.show_speed_labels,
    )
    plot_y.update(
        result.amp_1y, result.phase_1y, result.rpm, result.theta_1y,
        result.r_max, settings.show_speed_labels,
    )
    if result.rpm.size:
        colourbar.update_normal(plot_x._points)
    subtitle = f"{settings.segment}  -  probe rotation {settings.rotation_deg:+.0f}\u00b0"
    if settings.slow_roll:
        low, high = settings.slow_roll_rpm
        subtitle += f"  -  slow-roll compensated ({low:.0f}-{high:.0f} rpm)"
    fig.text(0.5, 0.93, subtitle, ha="center", fontsize=10, color="#333")
    annotate_conventions(fig, _CONVENTION_NOTE)
    path.parent.mkdir(parents=True, exist_ok=True)
    # No bbox_inches="tight" here: the figure uses fixed margins, and cropping
    # would move the title and footnote relative to the axes.
    fig.savefig(path, dpi=140, facecolor="white")
    return path


def export_bode(run: RunData, settings: ViewSettings, path: Path) -> Path:
    model = PolarModel(run)
    result = model.compute(settings)
    fig = Figure(figsize=(11.0, 6.4), dpi=100)
    fig.patch.set_facecolor("white")
    bode = BodePlot(fig, run.amp_unit)
    order = np.argsort(result.rpm)
    bode.update(
        result.rpm[order],
        result.amp_1x[order],
        result.phase_1x[order],
        result.amp_1y[order],
        result.phase_1y[order],
    )
    bode.amp_axis.set_title(
        f"{settings.segment}  -  probe rotation {settings.rotation_deg:+.0f}\u00b0", fontsize=10
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=140, bbox_inches="tight", facecolor="white")
    return path


def export_all(run: RunData, out_dir: str | Path = "figures") -> list[Path]:
    """Write the standard set of figures referenced by OBSERVATIONS.md."""
    out_dir = Path(out_dir)
    written: list[Path] = []
    for segment in ("Coast-up (startup)", "Coast-down (shutdown)"):
        tag = _slug(segment.split(" ")[0])
        for rotation in (0.0, 90.0):
            settings = ViewSettings(segment=segment, rotation_deg=rotation)
            written.append(
                export_polar(run, settings, out_dir / f"polar_{tag}_rot{rotation:.0f}.png")
            )
        written.append(
            export_bode(
                run,
                ViewSettings(segment=segment, rotation_deg=0.0),
                out_dir / f"bode_{tag}.png",
            )
        )
        written.append(
            export_polar(
                run,
                ViewSettings(segment=segment, rotation_deg=0.0, slow_roll=True),
                out_dir / f"polar_{tag}_slowroll.png",
            )
        )
    return written
