"""Render each web tab to PNG, for the README and for eyeballing changes.

    python tools/preview_web_figures.py [output_dir]

Needs ``kaleido`` on top of the normal requirements.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rotordyn.dataset import build_run
from rotordyn.model import ViewSettings
from rotordyn.webapp import build_figure

ROOT = Path(__file__).resolve().parents[1]

TABS = {
    "polar": (ViewSettings(), None, None),
    "polar_rotated": (ViewSettings(rotation_deg=90.0, machine_view=True), None, None),
    "bode": (ViewSettings(), None, None),
    "trend": (ViewSettings(segment="Full record"), ["1X amplitude", "Gap voltage"], None),
    "centreline": (ViewSettings(segment="Full record"), None, 12.0),
}


def main(out_dir: str = "figures/web") -> int:
    run = build_run(ROOT)
    out = ROOT / out_dir
    out.mkdir(parents=True, exist_ok=True)
    for name, (settings, channels, clearance) in TABS.items():
        tab = name.split("_")[0]
        figure, _ = build_figure(run, tab, settings, channels, clearance)
        path = out / f"{name}.png"
        figure.write_image(path, width=1180, height=figure.layout.height or 600, scale=2)
        print(f"wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:]))
