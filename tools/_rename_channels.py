"""Rewrite split channel files under the BRG<n>X / BRG<n>Y names the site pairs on.

The ADRE export carries plant tags such as ``39VS21-1HD-LP3``.  Trend and polar
plots work with those, but the orbit and shaft-centreline panels look for a
bearing's two probes by name, the way the reference files (``BRG1X``, ``BRG1Y``,
``Kph 1``) are labelled.  This writes renamed copies so that pairing can be tested
without touching the originals.

    python tools/_rename_channels.py
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHANNELS = ROOT / "imports" / "imp_20260917_194708_bb59d499" / "exports" / "channels"
TARGET = ROOT / "tools" / "_live_split_out" / "renamed"

#: source file -> channel name the dashboard understands.  H probes become X,
#: V probes become Y, matching the reference files' 45 deg Right / Left pair.
RENAMES = {
    "39VS21-1HD-LP3.csv": "BRG1X",
    "39VS22-1VD-LP4.csv": "BRG1Y",
    "39VS11-2HD-LP1.csv": "BRG2X",
    "39VS12-2VD-LP2.csv": "BRG2Y",
    "39VS92-3HD-LP6.csv": "BRG3X",
    "39VS91-3VD-LP5.csv": "BRG3Y",
    "39VS102-4HD-LP8.csv": "BRG4X",
    "39VS101-4VD-LP7.csv": "BRG4Y",
    "KEYPH.-LP13.csv": "Kph 1",
}


def rename_channel(source: Path, new_name: str, target: Path) -> None:
    # splitlines(), not split("\r\n"): reading in text mode already normalised
    # the line endings, and they are restored on the way out.
    old_name = ""
    out: list[str] = []
    for line in source.read_text(encoding="utf-16").splitlines():
        cells = line.split("\t")
        # Column 1 holds the channel name in both the configuration and sample rows.
        if len(cells) > 2 and cells[0].strip().isdigit():
            if not old_name:
                old_name = cells[1]
            if cells[1] == old_name:
                cells[1] = new_name
            line = "\t".join(cells)
        out.append(line)
    target.write_text("\r\n".join(out) + "\r\n", encoding="utf-16", newline="")


def main() -> None:
    TARGET.mkdir(parents=True, exist_ok=True)
    for name, new_name in RENAMES.items():
        source = CHANNELS / name
        target = TARGET / f"{new_name}.csv"
        rename_channel(source, new_name, target)
        print(f"{name:24s} -> {target.name}")
    print(f"\nwrote {len(RENAMES)} files to {TARGET}")


if __name__ == "__main__":
    main()
