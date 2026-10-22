"""Reader for the Bently Nevada ADRE tab-delimited channel exports.

The supplied files carry a ``.csv`` extension but are actually UTF-16LE,
tab-delimited, and contain two stacked tables:

    <blank lines>
    CH#  Channel Name  Machine Name  Status  Angle  Direction  ...   <- channel metadata header
    1    BRG1X         Turbine       OK      45     Right      ...   <- one metadata row
    <blank line>
    CH#  Channel Name  Sample#  Sample Cause  Date  Speed(P) ...     <- sample header
    1    BRG1X         3296     DR-T          ...                    <- one row per sample

Numeric cells may carry a trailing vendor status code with no separator
(``0.083K25``, ``280BMA``).  The code is stripped into a companion
``*__flag`` column so that nothing is silently discarded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

# Vendor status codes seen in this dataset.  Meanings are inferred from where
# they occur (see README) rather than from a vendor spec.
STATUS_CODES = {
    "K25": "Keyphasor speed changed by more than 25% between samples",
    "BMA": "Below minimum amplitude - phase is not meaningful",
    "A1H": "Below the minimum speed for valid 1X vector processing",
    "FNX": "nX filter not tracking / nX vector not available",
}

_ENCODINGS = ("utf-16", "utf-8-sig", "cp1252")

# number, then an optional alphanumeric status code glued to the end of it
_VALUE_RE = re.compile(r"^([+-]?(?:\d+\.?\d*|\.\d+))([A-Za-z][A-Za-z0-9]*)?$")

_ANGLE_RE = re.compile(r"([+-]?\d+(?:\.\d+)?)")

#: Columns that hold a synchronous (1X) vibration vector.
AMPLITUDE_COL = "1XAmplitude"
PHASE_COL = "1X Phase"
#: Column the keyphasor channel uses to report shaft speed.
SPEED_COL = "Speed"


@dataclass
class AdreChannel:
    """One exported ADRE channel: metadata block plus the sample table."""

    path: Path
    name: str
    machine: str
    #: Probe mounting angle, degrees, signed clockwise-positive from Top Dead
    #: Centre as seen in the viewing direction described in the README.
    #: ``Right`` in the export is clockwise-positive, ``Left`` is negative.
    angle_deg: float | None
    angle_text: str
    amp_unit: str
    phase_unit: str
    speed_unit: str
    samples: pd.DataFrame = field(repr=False)

    @property
    def is_keyphasor(self) -> bool:
        """A keyphasor channel reports speed and has no 1X vibration vector."""
        return SPEED_COL in self.samples.columns and AMPLITUDE_COL not in self.samples.columns

    @property
    def n_samples(self) -> int:
        return len(self.samples)

    def flag_summary(self) -> dict[str, int]:
        """Count of each vendor status code across every flag column."""
        counts: dict[str, int] = {}
        for col in self.samples.columns:
            if not col.endswith("__flag"):
                continue
            for code, n in self.samples[col].value_counts().items():
                if code:
                    counts[code] = counts.get(code, 0) + int(n)
        return dict(sorted(counts.items()))


def decode_export(data: bytes) -> list[str]:
    """Decode raw export bytes, trying the encodings these files show up in.

    Kept separate from file reading so that browser uploads, which arrive as
    bytes with no path, go through exactly the same decoding logic.
    """
    last_error: Exception | None = None
    for encoding in _ENCODINGS:
        try:
            text = data.decode(encoding)
        except (UnicodeDecodeError, UnicodeError) as exc:  # pragma: no cover
            last_error = exc
            continue
        # A mis-guessed encoding shows up as interleaved NUL characters.
        if "\x00" in text:
            continue
        return text.splitlines()
    raise ValueError(f"Could not decode export: {last_error}")


def _read_lines(path: Path) -> list[str]:
    """Read the export, trying the encodings these files show up in."""
    try:
        return decode_export(path.read_bytes())
    except ValueError as exc:
        raise ValueError(f"Could not decode {path}: {exc}") from exc


def _delimiter_for(line: str) -> str:
    """ADRE assignment exports use tabs; Sxp Tabular List exports use ``|``."""
    if line.count("|") >= line.count("\t") and "|" in line:
        return "|"
    return "\t"


def _split_row(line: str, width: int, delimiter: str = "\t") -> list[str]:
    """Split a delimited row and pad/trim it to the header width."""
    cells = [c.strip() for c in line.split(delimiter)]
    cells = cells[:width]
    cells += [""] * (width - len(cells))
    return cells


def _parse_table(
    lines: list[str], header_idx: int, stop_idx: int, delimiter: str | None = None
) -> pd.DataFrame:
    delimiter = delimiter or _delimiter_for(lines[header_idx])
    header = [c.strip() for c in lines[header_idx].split(delimiter)]
    # Exports end every row with a trailing delimiter, which yields an empty column.
    while header and header[-1] == "":
        header.pop()
    rows = [
        _split_row(line, len(header), delimiter)
        for line in lines[header_idx + 1 : stop_idx]
        if line.strip()
    ]
    return pd.DataFrame(rows, columns=header)


def split_value(cell: str) -> tuple[float, str]:
    """Split ``'0.083K25'`` into ``(0.083, 'K25')``.

    Cells that are not a number with an optional trailing code come back as
    ``(nan, <original text>)`` so unexpected content stays visible.
    """
    text = str(cell).strip()
    if not text:
        return float("nan"), ""
    match = _VALUE_RE.match(text)
    if match is None:
        return float("nan"), text
    return float(match.group(1)), match.group(2) or ""


def _numeric_with_flags(frame: pd.DataFrame, columns: Iterable[str]) -> pd.DataFrame:
    out = frame.copy()
    for col in columns:
        if col not in out.columns:
            continue
        split = out[col].map(split_value)
        out[col] = [v for v, _ in split]
        out[f"{col}__flag"] = [f for _, f in split]
    return out


def _parse_angle(angle_text: str, direction: str) -> float | None:
    """Convert ``('45°', 'Right')`` to a signed clockwise-from-TDC angle."""
    match = _ANGLE_RE.search(str(angle_text))
    if match is None:
        return None
    angle = float(match.group(1))
    if str(direction).strip().lower().startswith("l"):
        angle = -angle
    return angle


def _channel_from_lines(lines: list[str], path: Path) -> AdreChannel:
    """Build a channel from already-decoded single-channel export lines."""
    header_rows = [i for i, line in enumerate(lines) if line.lstrip().startswith("CH#")]
    if len(header_rows) < 2:
        raise ValueError(f"{path.name}: expected a metadata header and a sample header")

    delimiter = _delimiter_for(lines[header_rows[0]])
    meta_frame = _parse_table(lines, header_rows[0], header_rows[1], delimiter)
    if meta_frame.empty:
        raise ValueError(f"{path.name}: metadata block is empty")
    meta = meta_frame.iloc[0]

    samples = _parse_table(lines, header_rows[1], len(lines), delimiter)
    if samples.empty:
        raise ValueError(f"{path.name}: sample table is empty")

    return _finish_channel(
        path=path,
        name=str(meta.get("Channel Name", path.stem)).strip(),
        machine=str(meta.get("Machine Name", "")).strip(),
        angle_deg=_parse_angle(meta.get("Angle", ""), meta.get("Direction", "")),
        angle_text=(
            f"{str(meta.get('Angle', '')).strip()} "
            f"{str(meta.get('Direction', '')).strip()}"
        ).strip(),
        amp_unit=str(meta.get("Amp Unit", "")).strip(),
        phase_unit=str(meta.get("Phase Unit", "")).strip(),
        speed_unit=str(meta.get("Speed Units(P)", "")).strip(),
        samples=samples,
    )


def _is_sxp_tabular(lines: list[str]) -> bool:
    """True for multi-channel Tabular List exports (pipe-delimited, repeating blocks)."""
    for line in lines[:80]:
        if line.startswith("CH#|Channel Name|Sample#") or line.startswith(
            "CH#|Channel Name|Machine Name"
        ):
            return True
    return False


def _finish_channel(
    path: Path,
    name: str,
    machine: str,
    angle_deg: float | None,
    angle_text: str,
    amp_unit: str,
    phase_unit: str,
    speed_unit: str,
    samples: pd.DataFrame,
) -> AdreChannel:
    numeric_cols = [
        c
        for c in samples.columns
        if c
        not in {"CH#", "Channel Name", "Sample#", "Sample Cause", "Date", "Direction"}
    ]
    samples = _numeric_with_flags(samples, numeric_cols)
    samples["Sample#"] = pd.to_numeric(samples["Sample#"], errors="coerce").astype("Int64")
    samples["timestamp"] = pd.to_datetime(
        samples["Date"], format="%d%b%Y %H:%M:%S.%f", errors="coerce"
    )
    return AdreChannel(
        path=path,
        name=name,
        machine=machine,
        angle_deg=angle_deg,
        angle_text=angle_text,
        amp_unit=amp_unit,
        phase_unit=phase_unit,
        speed_unit=speed_unit,
        samples=samples.reset_index(drop=True),
    )


def _synthetic_keyphasor(path: Path, donor: AdreChannel) -> AdreChannel:
    """Build a keyphasor channel from ``Speed(P)`` when the export has no Kph row."""
    frame = donor.samples.copy()
    if "Speed(P)" not in frame.columns:
        raise ValueError(f"{path.name}: cannot synthesise a keyphasor without Speed(P)")
    samples = pd.DataFrame(
        {
            "CH#": "0",
            "Channel Name": "Speed(P)",
            "Sample#": frame["Sample#"],
            "Sample Cause": frame.get("Sample Cause", ""),
            "Date": frame["Date"],
            "Speed(P)": frame["Speed(P)"],
            SPEED_COL: frame["Speed(P)"],
        }
    )
    return _finish_channel(
        path=path,
        name="Speed(P)",
        machine=donor.machine,
        angle_deg=None,
        angle_text="",
        amp_unit="",
        phase_unit="",
        speed_unit=donor.speed_unit or "rpm",
        samples=samples,
    )


def load_sxp_tabular(path: str | Path) -> list[AdreChannel]:
    """Parse an ADRE Sxp Tabular List CSV that packs every channel into one file.

    The export repeats a metadata block + a sample block for every sample number.
    Speed lives on each vibration row as ``Speed(P)``; if no keyphasor channel is
    present, one is synthesised from ``Speed(P)`` so :func:`build_run` can align.
    """
    path = Path(path)
    lines = _read_lines(path)
    if not _is_sxp_tabular(lines):
        raise ValueError(f"{path.name}: not an Sxp Tabular List export")

    meta_by_name: dict[str, dict[str, str]] = {}
    sample_rows: dict[str, list[dict[str, str]]] = {}
    sample_header: list[str] | None = None
    mode: str | None = None
    meta_header: list[str] | None = None

    for line in lines:
        if not line.strip():
            mode = None
            continue
        if line.startswith("CH#|Channel Name|Machine Name"):
            mode = "meta"
            meta_header = [c.strip() for c in line.split("|")]
            while meta_header and meta_header[-1] == "":
                meta_header.pop()
            continue
        if line.startswith("CH#|Channel Name|Sample#"):
            mode = "sample"
            sample_header = [c.strip() for c in line.split("|")]
            while sample_header and sample_header[-1] == "":
                sample_header.pop()
            continue
        if mode == "meta" and meta_header is not None:
            cells = _split_row(line, len(meta_header), "|")
            row = dict(zip(meta_header, cells))
            name = row.get("Channel Name", "").strip()
            if name and name not in meta_by_name:
                meta_by_name[name] = row
            continue
        if mode == "sample" and sample_header is not None:
            cells = _split_row(line, len(sample_header), "|")
            row = dict(zip(sample_header, cells))
            name = row.get("Channel Name", "").strip()
            sample_no = row.get("Sample#", "").strip()
            if not name or not sample_no.isdigit():
                continue
            sample_rows.setdefault(name, []).append(row)

    if not sample_rows:
        raise ValueError(f"{path.name}: no sample rows found")

    channels: list[AdreChannel] = []
    for name, rows in sample_rows.items():
        meta = meta_by_name.get(name, {})
        samples = pd.DataFrame(rows)
        channels.append(
            _finish_channel(
                path=path,
                name=name,
                machine=str(meta.get("Machine Name", "")).strip(),
                angle_deg=_parse_angle(meta.get("Angle", ""), meta.get("Direction", "")),
                angle_text=(
                    f"{str(meta.get('Angle', '')).strip()} "
                    f"{str(meta.get('Direction', '')).strip()}"
                ).strip(),
                amp_unit=str(meta.get("Amp Unit", "")).strip(),
                phase_unit=str(meta.get("Phase Unit", "")).strip(),
                speed_unit=str(meta.get("Speed Units(P)", "")).strip(),
                samples=samples,
            )
        )

    if not any(c.is_keyphasor for c in channels):
        donor = next(
            (c for c in channels if c.name == "BRG1X"),
            next((c for c in channels if "Speed(P)" in c.samples.columns), None),
        )
        if donor is None:
            raise ValueError(f"{path.name}: no keyphasor and no Speed(P) to synthesise one")
        channels.append(_synthetic_keyphasor(path, donor))

    return channels


def load_export(path: str | Path) -> list[AdreChannel]:
    """Load one export file as one or more channels."""
    path = Path(path)
    from .waveforms import is_timebase_export

    if is_timebase_export(path):
        raise ValueError(
            f"{path.name}: concatenated timebase dump; use python main.py --buddy"
        )
    lines = _read_lines(path)
    if _is_sxp_tabular(lines):
        return load_sxp_tabular(path)
    return [_channel_from_lines(lines, path)]


def load_channel(path: str | Path) -> AdreChannel:
    """Parse a single-channel ADRE export from disk."""
    channels = load_export(path)
    if len(channels) != 1:
        raise ValueError(
            f"{Path(path).name}: contains {len(channels)} channels; use load_export()"
        )
    return channels[0]


def load_channel_bytes(data: bytes, filename: str) -> AdreChannel:
    """Parse a single-channel ADRE export from raw bytes, e.g. a browser upload."""
    lines = decode_export(data)
    head = next((ln for ln in lines[:40] if ln.strip()), "")
    if head.startswith("Plot Name|") or head.startswith("Plot Name\t"):
        raise ValueError(
            f"{filename}: concatenated timebase dump; run python main.py --buddy"
        )
    if _is_sxp_tabular(lines):
        raise ValueError(
            f"{filename}: multi-channel Tabular List upload; split or use load_export()"
        )
    return _channel_from_lines(lines, Path(filename))


def load_directory(directory: str | Path, pattern: str = "*.csv") -> list[AdreChannel]:
    """Load every ADRE export in ``directory``, skipping unreadable files.

    Channels are identified from their own metadata rather than from file
    names, so the tool does not depend on how the files happen to be called.
    A single Sxp Tabular List file expands to one channel object per probe.
    """
    directory = Path(directory)
    channels: list[AdreChannel] = []
    from .waveforms import is_timebase_export

    for path in sorted(directory.glob(pattern)):
        if is_timebase_export(path):
            continue
        try:
            channels.extend(load_export(path))
        except (ValueError, KeyError):
            continue
    if not channels:
        raise FileNotFoundError(f"No readable ADRE exports found in {directory}")
    return channels


def sample_index(channel: AdreChannel) -> np.ndarray:
    """Sample numbers as a plain integer array, used to align channels."""
    return channel.samples["Sample#"].to_numpy(dtype="int64")
