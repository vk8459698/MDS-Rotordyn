"""Dump every property of selected ADRE config segments.

    python tools/dump_adre_channel.py [name ...]

PropIDs are undocumented, so they get decoded by dumping a segment whose
physical values are already known (probe angle, scale factor, OK limits) and
matching numbers to meanings.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.probe_adre_config import load_xml, prop_values, strip_ns, text_of

DEFAULT = Path("CT2B_DukeCitrus_04292026")
WANTED = ["ADRE 408", "Dynamic Sampling Card", "BRG1X", "BRG1Y", "Keyphasor"]


def segments(node, depth=0, parent=""):
    for child in node:
        if strip_ns(child.tag) != "segment":
            yield from segments(child, depth, parent)
            continue
        name = text_of(child, "name")
        yield child, name, depth, parent
        kids = child.find("{*}children")
        if kids is not None:
            yield from segments(kids, depth + 1, name)


def main(*names: str) -> int:
    wanted = list(names) or WANTED
    root = load_xml(DEFAULT / "ConfigDBFile.xml")

    seen = set()
    for node, name, depth, parent in segments(root):
        if name not in wanted or name in seen:
            continue
        seen.add(name)
        print("=" * 78)
        print(f"{name}   (parent: {parent or '-'})")
        print(f"  SID={node.get('SID')}  Type={node.get('Type')}  "
              f"Order={node.get('Order')}  Level={node.get('Level')}")

        holder = node.find("{*}properties")
        if holder is not None:
            print("  properties:")
            for prop in holder:
                values = [v for v in prop_values(prop) if v != ""]
                if not values:
                    continue
                print(f"    PropID {prop.get('PropID'):<6} "
                      f"type {prop.get('DataType'):<3} "
                      f"unit {prop.get('UnitID', ''):<4} "
                      f"{', '.join(values)[:80]}")

        assoc = node.find("{*}associations")
        if assoc is not None and len(assoc):
            print("  associations:")
            for a in assoc:
                print(f"    {a.attrib}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:]))
