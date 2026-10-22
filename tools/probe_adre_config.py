"""Recon of an ADRE Sxp ConfigDBFile.xml: what is in it and how it nests.

    python tools/probe_adre_config.py <dataset dir>

Prints the segment tree, the tag/attribute vocabulary, and every distinct
PropID with a sample value, which is what the property numbers have to be
decoded from -- there is no public dictionary for them.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

DEFAULT = Path("CT2B_DukeCitrus_04292026")


def load_xml(path: Path) -> ET.Element:
    """Parse an ADRE config, which is UTF-16 and declares encoding="unicode".

    That declaration is a Microsoft spelling Python's parser rejects, so the
    bytes are decoded here and the declaration dropped before parsing.
    """
    raw = path.read_bytes()
    for encoding in ("utf-16", "utf-8-sig", "cp1252"):
        try:
            text = raw.decode(encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue
        if "\x00" in text:
            continue
        break
    else:
        raise ValueError(f"Could not decode {path}")
    if text.lstrip().startswith("<?xml"):
        text = text.split("?>", 1)[1]
    return ET.fromstring(text)


def strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def text_of(node: ET.Element, child: str) -> str:
    found = node.find(f"{{*}}{child}")
    return (found.text or "").strip() if found is not None else ""


def prop_values(prop: ET.Element) -> list[str]:
    out = []
    for value in prop:
        if strip_ns(value.tag).startswith("value"):
            out.append((value.text or "").strip())
    return out


def walk(node: ET.Element, depth: int, lines: list[str], props: dict) -> None:
    """Depth-first over <segment> nodes, recording names and properties."""
    for child in node:
        if strip_ns(child.tag) != "segment":
            walk(child, depth, lines, props)
            continue
        name = text_of(child, "name")
        sid = child.get("SID", "?")
        stype = child.get("Type", "?")
        lines.append(f"{'  ' * depth}[{stype:>3}] SID {sid:<5} {name}")

        holder = child.find("{*}properties")
        if holder is not None:
            for prop in holder:
                pid = prop.get("PropID", "?")
                values = prop_values(prop)
                props[pid].append((name, values))

        kids = child.find("{*}children")
        if kids is not None:
            walk(kids, depth + 1, lines, props)


def main(folder: str = str(DEFAULT)) -> int:
    path = Path(folder) / "ConfigDBFile.xml"
    print(f"reading {path}  ({path.stat().st_size / 1e6:.1f} MB)\n")
    root = load_xml(path)

    print("ROOT ATTRIBUTES")
    for key, value in root.attrib.items():
        print(f"  {strip_ns(key):<22} {value}")

    tags = Counter(strip_ns(e.tag) for e in root.iter())
    print(f"\nTAG COUNTS ({len(tags)} distinct)")
    for tag, n in tags.most_common(25):
        print(f"  {tag:<28} {n}")

    lines: list[str] = []
    props: dict[str, list] = defaultdict(list)
    walk(root, 0, lines, props)

    print(f"\nSEGMENT TREE ({len(lines)} segments)")
    for line in lines[:400]:
        print("  " + line)
    if len(lines) > 400:
        print(f"  ... {len(lines) - 400} more")

    print(f"\nPROPERTY IDS ({len(props)} distinct)")
    for pid in sorted(props, key=lambda p: int(p) if p.isdigit() else 0):
        entries = props[pid]
        sample = next((v for _, v in entries if v and v[0]), [""])
        shown = ", ".join(sample[:4])[:70]
        print(f"  PropID {pid:<6} x{len(entries):<5} e.g. {shown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:]))
