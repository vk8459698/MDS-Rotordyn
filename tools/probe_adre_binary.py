"""Hex/interpretation recon of the ADRE Sxp .dat files.

    python tools/probe_adre_binary.py <dataset dir>

Dumps the bookkeeping files whole and the head of a channel header and its
payload, decoding each offset as several candidate types at once so the real
record layout can be spotted by eye.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

DEFAULT = Path("CT2B_DukeCitrus_04292026")
FILETIME_EPOCH = 11644473600  # seconds between 1601-01-01 and 1970-01-01


def hexdump(data: bytes, base: int = 0, limit: int | None = None) -> None:
    view = data if limit is None else data[:limit]
    for off in range(0, len(view), 16):
        chunk = view[off:off + 16]
        hexed = " ".join(f"{b:02x}" for b in chunk)
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        print(f"  {base + off:08x}  {hexed:<47}  |{text}|")


def as_filetime(value: int) -> str:
    """FILETIME -> ISO date, or empty when the value is not a plausible date."""
    seconds = value / 1e7 - FILETIME_EPOCH
    if not 9.4e8 < seconds < 2.5e9:  # ~2000..2049
        return ""
    import datetime

    return datetime.datetime.utcfromtimestamp(seconds).isoformat()


def interpret(data: bytes, count: int = 48, base: int = 0) -> None:
    """Show each 4/8-byte window as int, float and date candidates."""
    print(f"  {'off':>5}  {'u32':>12} {'i32':>12} {'f32':>14}   "
          f"{'u64':>22} {'f64':>14}  date")
    for off in range(0, min(count * 4, len(data) - 8), 4):
        u32, = struct.unpack_from("<I", data, off)
        i32, = struct.unpack_from("<i", data, off)
        f32, = struct.unpack_from("<f", data, off)
        u64, = struct.unpack_from("<Q", data, off)
        f64, = struct.unpack_from("<d", data, off)
        f32s = f"{f32:14.5g}" if abs(f32) < 1e30 else " " * 14
        f64s = f"{f64:14.5g}" if abs(f64) < 1e30 else " " * 14
        print(f"  {off:5d}  {u32:12d} {i32:12d} {f32s}   "
              f"{u64:22d} {f64s}  {as_filetime(u64)}")


def main(folder: str = str(DEFAULT)) -> int:
    data_dir = Path(folder) / "Data"

    for name in ("DatabaseVersion.dat", "CG0MaxSample.dat",
                 "WaveformCG0MaxSample.dat"):
        raw = (data_dir / name).read_bytes()
        print(f"\n=== {name}  ({len(raw)} bytes)")
        hexdump(raw)
        for size, fmt in ((4, "<I"), (8, "<Q")):
            if len(raw) % size == 0:
                vals = struct.unpack(f"<{len(raw) // size}{fmt[1]}", raw)
                print(f"  as {size}-byte ints: {vals}")

    raw = (data_dir / "DatabaseSize.dat").read_bytes()
    print(f"\n=== DatabaseSize.dat  ({len(raw)} bytes)")
    hexdump(raw, limit=160)
    print("  ... first 40 u32:")
    print("  ", struct.unpack_from("<40I", raw, 0))

    header = data_dir / "Box0Slot0Chan0.dat"
    raw = header.read_bytes()
    print(f"\n=== {header.name}  ({len(raw)} bytes)  FULL")
    hexdump(raw)
    print("\n  interpreted:")
    interpret(raw, count=40)

    payload = data_dir / "Box0Slot0Chan0_0.dat"
    with payload.open("rb") as fh:
        head = fh.read(512)
    print(f"\n=== {payload.name}  (first 512 of "
          f"{payload.stat().st_size} bytes)")
    hexdump(head)
    print("\n  interpreted:")
    interpret(head, count=40)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:]))
