"""First-pass inventory of a native ADRE Sxp database folder."""

from __future__ import annotations

import re
import struct
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "CT2B_DukeCitrus_04292026"


def load_config(path: Path) -> ET.Element:
    text = path.read_text(encoding="utf-16")
    text = re.sub(r'\sxmlns="[^"]+"', "", text, count=1)
    return ET.fromstring(text)


def main() -> int:
    cfg = ROOT / "ConfigDBFile.xml"
    tree = load_config(cfg)

    segments = []
    for seg in tree.iter("segment"):
        name_el = seg.find("name")
        name = (name_el.text or "").strip() if name_el is not None else ""
        props = {}
        for prop in seg.findall("./properties/property"):
            value = prop.find("value1")
            if value is not None and value.text:
                props[prop.get("PropID", "?")] = value.text.strip()
        segments.append(
            {
                "sid": seg.get("SID"),
                "type": seg.get("Type"),
                "level": int(seg.get("Level") or 0),
                "name": name,
                "props": props,
            }
        )

    print(f"Config: {cfg.name}  segments={len(segments)}")
    print("Type histogram:", Counter(s["type"] for s in segments).most_common())
    print()

    print("=== Named segments (level <= 3) ===")
    for s in segments:
        if not s["name"] or s["level"] > 3:
            continue
        print(f"L{s['level']} T{s['type']} SID{s['sid']}: {s['name']}")
        for key, value in list(s["props"].items())[:12]:
            print(f"    Prop {key}: {value[:100]}")

    # Heuristic: channel-like names often contain BRG / KPH / X / Y / Vib
    print()
    print("=== Segments whose names look like channels ===")
    channelish = [
        s
        for s in segments
        if s["name"]
        and re.search(r"(BRG|KPH|Kph|Prox|Vib|1X|1Y|2X|2Y|Key)", s["name"], re.I)
    ]
    for s in channelish:
        print(f"L{s['level']} T{s['type']} SID{s['sid']}: {s['name']}")
        for key, value in s["props"].items():
            print(f"    Prop {key}: {value[:120]}")

    # Binary inventory
    print()
    print("=== Data/*.dat inventory ===")
    data = ROOT / "Data"
    files = sorted(data.glob("*.dat"))
    total = sum(p.stat().st_size for p in files)
    print(f"{len(files)} files, {total / 1e6:.1f} MB")
    for p in files:
        head = p.read_bytes()[:16]
        print(f"  {p.name:32} {p.stat().st_size:10}  {head.hex()}")

    # Peek at a large chunk file as ints/floats
    sample = data / "Box0Slot0Chan0_0.dat"
    blob = sample.read_bytes()[:64]
    print()
    print(f"First 64 bytes of {sample.name}:")
    print("  hex:", blob.hex())
    print("  u32:", struct.unpack_from("<16I", blob[:64]))
    print("  f32:", struct.unpack_from("<16f", blob[:64]))

    version = struct.unpack("<f", (data / "DatabaseVersion.dat").read_bytes())[0]
    print()
    print(f"DatabaseVersion.dat as float32: {version}")
    print(f"CG0MaxSample: {struct.unpack('<2Q', (data / 'CG0MaxSample.dat').read_bytes())}")
    print(
        f"WaveformCG0MaxSample: "
        f"{struct.unpack('<2QI', (data / 'WaveformCG0MaxSample.dat').read_bytes())}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
