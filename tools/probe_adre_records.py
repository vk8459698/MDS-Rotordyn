"""Find the record boundaries inside an ADRE Sxp channel payload.

    python tools/probe_adre_records.py [Box0Slot0Chan0]

Timestamps are the reliable landmark: every record carries a Windows FILETIME,
so scanning for plausible ones reveals the record stride, and the gaps between
them expose the fields that vary from record to record.
"""

from __future__ import annotations

import datetime
import struct
import sys
from collections import Counter
from pathlib import Path

DEFAULT_DIR = Path("CT2B_DukeCitrus_04292026") / "Data"
CHUNK_HEADER = 140
FILETIME_EPOCH = 11644473600
LOW = int((946684800 + FILETIME_EPOCH) * 1e7)   # 2000-01-01
HIGH = int((2524608000 + FILETIME_EPOCH) * 1e7)  # 2050-01-01


def to_iso(value: int) -> str:
    return datetime.datetime.utcfromtimestamp(
        value / 1e7 - FILETIME_EPOCH).isoformat(timespec="milliseconds")


def chunk_header(raw: bytes) -> dict:
    return {
        "header_len": struct.unpack_from("<I", raw, 68)[0],
        "payload_len": struct.unpack_from("<I", raw, 72)[0],
        "t_first": struct.unpack_from("<Q", raw, 52)[0],
        "t_last": struct.unpack_from("<Q", raw, 60)[0],
    }


def find_filetimes(data: bytes, limit: int) -> list[tuple[int, int]]:
    """Offsets of every 8-byte window that decodes to a year-2000..2050 date."""
    hits = []
    end = min(limit, len(data) - 8)
    for off in range(0, end, 2):
        value = int.from_bytes(data[off:off + 8], "little")
        if LOW < value < HIGH:
            hits.append((off, value))
    return hits


def main(stem: str = "Box0Slot0Chan0", folder: str = str(DEFAULT_DIR)) -> int:
    data_dir = Path(folder)
    first = data_dir / f"{stem}_0.dat"
    raw = first.read_bytes()

    print(f"=== {first.name}  {len(raw)} bytes")
    info = chunk_header(raw)
    print(f"  header_len  = {info['header_len']}")
    print(f"  payload_len = {info['payload_len']}"
          f"   (header+payload = {info['header_len'] + info['payload_len']})")
    print(f"  t_first     = {to_iso(info['t_first'])}")
    print(f"  t_last      = {to_iso(info['t_last'])}")

    body = raw[CHUNK_HEADER:]
    hits = find_filetimes(body, 3_000_000)
    print(f"\n  {len(hits)} FILETIME candidates in first 3 MB of payload")
    print(f"  {'offset':>10}  {'delta':>8}  timestamp")
    previous = None
    for off, value in hits[:40]:
        delta = "" if previous is None else f"{off - previous:8d}"
        print(f"  {off:10d}  {delta:>8}  {to_iso(value)}")
        previous = off

    strides = Counter(b[0] - a[0] for a, b in zip(hits, hits[1:]))
    print("\n  most common offset gaps between timestamps:")
    for stride, n in strides.most_common(12):
        print(f"    {stride:8d}  x{n}")

    # Two records side by side: identical bytes are structure, differing bytes
    # are the per-record fields.
    if len(hits) > 4:
        starts = [off for off, _ in hits]
        stride = strides.most_common(1)[0][0]
        a, b = starts[0], starts[0] + stride
        print(f"\n  comparing two records {stride} bytes apart "
              f"(from timestamp anchors {a} and {b}):")
        width = min(96, len(body) - b)
        ra, rb = body[a:a + width], body[b:b + width]
        for off in range(0, width, 2):
            va = int.from_bytes(ra[off:off + 2], "little", signed=True)
            vb = int.from_bytes(rb[off:off + 2], "little", signed=True)
            flag = "  <-- differs" if va != vb else ""
            print(f"    +{off:4d}  {va:8d}  {vb:8d}{flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:]))
