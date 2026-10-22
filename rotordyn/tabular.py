"""Normalise an ADRE Sxp Tabular List export into per-channel files.

The Sxp "Tabular List" export packs every channel into one pipe-delimited
UTF-16 file and repeats the whole transducer configuration block ahead of each
sample block, so a 21-channel, 35-sample export carries 735 configuration rows
where 21 would do.  Tools that expect one table per file cannot read it.

:func:`split_tabular_export` rewrites it as the per-channel, tab-delimited
layout the rest of the toolchain (and the website) already accepts: the
configuration row appears once per channel, followed by that channel's samples.
Cell text is copied verbatim, including vendor status codes such as ``280BMA``.
"""

from __future__ import annotations

import csv
import re
import shutil
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from .adre import _is_sxp_tabular, _read_lines, _split_row

META_HEADER_PREFIX = "CH#|Channel Name|Machine Name"
SAMPLE_HEADER_PREFIX = "CH#|Channel Name|Sample#"

#: Never dropped, even when a channel leaves them blank.
_KEEP_ALWAYS = ("CH#", "Channel Name", "Sample#", "Sample Cause", "Date")

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_BEARING_TAG = re.compile(r"(?:^|[-_ ])(\d{1,2})([HV])D(?:$|[-_ ])", re.IGNORECASE)
_KEYPHASOR = re.compile(r"key\s*ph|kph", re.IGNORECASE)
_CASING_TAG = re.compile(r"(?:^|[-_ ])\d*v-\d+[ab](?:$|[-_. ])", re.IGNORECASE)
GROUP_ORDER = ("rotor", "casing", "thrust", "other")


@dataclass(frozen=True)
class SplitResult:
    """What :func:`split_tabular_export` wrote, and what it removed."""

    source: Path
    folder: Path
    channel_files: list[Path]
    config_file: Path
    n_channels: int
    n_samples: int
    config_rows_in: int
    config_rows_out: int
    renames: dict[str, str] = field(default_factory=dict)
    groups: dict[str, list[Path]] = field(default_factory=dict)
    groups_folder: Path | None = None

    @property
    def duplicate_config_rows(self) -> int:
        return self.config_rows_in - self.config_rows_out

    def format_text(self) -> str:
        text = (
            f"{self.source.name}: {self.n_channels} channels, {self.n_samples} samples\n"
            f"  configuration rows {self.config_rows_in} -> {self.config_rows_out} "
            f"({self.duplicate_config_rows} duplicates removed)\n"
            f"  wrote {len(self.channel_files)} channel files + {self.config_file.name}\n"
            f"  folder {self.folder}"
        )
        if self.renames:
            pairs = ", ".join(f"{old} -> {new}" for old, new in sorted(self.renames.items()))
            text += f"\n  named for plotting ({len(self.renames)}): {pairs}"
        if self.groups:
            parts = ", ".join(
                f"{name} {len(files)}" for name, files in self.groups.items() if files
            )
            text += f"\n  groups {self.groups_folder}: {parts}"
        return text


def safe_filename(name: str) -> str:
    """A channel name that Windows will accept as a file name."""
    cleaned = _UNSAFE.sub("_", name).strip().rstrip(".")
    return cleaned or "channel"


def plot_name(channel: str) -> str | None:
    if _KEYPHASOR.search(channel):
        return "Kph"
    match = _BEARING_TAG.search(channel)
    if not match:
        return None
    number, orientation = match.groups()
    return f"BRG{int(number)}{'X' if orientation.upper() == 'H' else 'Y'}"


def plot_names(channels: Iterable[str]) -> dict[str, str]:
    channels = list(channels)
    proposed: dict[str, str] = {}
    keyphasors = 0
    for channel in channels:
        name = plot_name(channel)
        if name is None:
            continue
        if name == "Kph":
            keyphasors += 1
            name = f"Kph {keyphasors}"
        proposed[channel] = name
    wanted = Counter(proposed.values())
    existing = {c.casefold() for c in channels}
    return {
        original: new
        for original, new in proposed.items()
        if wanted[new] == 1 and new.casefold() not in existing
    }


def channel_group(name: str) -> str:
    lowered = name.casefold()
    if "thrust" in lowered:
        return "thrust"
    if _KEYPHASOR.search(name) or lowered.startswith("kph") or lowered.startswith("brg"):
        return "rotor"
    tagged = plot_name(name)
    if tagged and tagged.startswith("BRG"):
        return "rotor"
    if _CASING_TAG.search(name) or "seis" in lowered or "casing" in lowered:
        return "casing"
    return "other"


def _machine_label(meta_header: list[str], meta_row: list[str]) -> str:
    try:
        return (meta_row[meta_header.index("Machine Name")] or "").strip()
    except (ValueError, IndexError):
        return ""


def write_groups(
    channel_files: Iterable[Path],
    folder: str | Path,
    machines: dict[Path, str] | None = None,
) -> dict[str, list[Path]]:
    """Copy channels into upload folders. Separate rotor trains when Machine Name differs."""
    folder = Path(folder)
    machines = machines or {}
    files = [p for p in channel_files if p.name.casefold() != "channels.csv"]
    rotor_machines = {
        (machines.get(path) or "").strip()
        for path in files
        if channel_group(path.stem) == "rotor"
    }
    rotor_machines.discard("")
    split_rotors = len(rotor_machines) > 1
    grouped: dict[str, list[Path]] = {}
    for source in files:
        kind = channel_group(source.stem)
        machine = (machines.get(source) or "").strip()
        if split_rotors and machine:
            dest_dir = folder / safe_filename(machine) / kind
            key = f"{safe_filename(machine)}/{kind}"
        else:
            dest_dir = folder / kind
            key = kind
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / source.name
        shutil.copy2(source, dest)
        grouped.setdefault(key, []).append(dest)
    manifest = folder / "manifest.csv"
    with manifest.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["Machine", "Group", "File"])
        for key, paths in grouped.items():
            if "/" in key:
                machine, kind = key.split("/", 1)
            else:
                machine, kind = "", key
            for path in paths:
                writer.writerow([machine, kind, path.name])
    return grouped


def _renamed(cells: list[str], index: int, name: str) -> list[str]:
    out = list(cells)
    if index < len(out):
        out[index] = name
    return out


def _strip_trailing_blanks(cells: list[str]) -> list[str]:
    out = list(cells)
    while out and out[-1] == "":
        out.pop()
    return out


def read_tabular_blocks(
    path: str | Path,
) -> tuple[list[str], dict[str, list[str]], list[str], dict[str, list[list[str]]]]:
    """Return ``(meta_header, meta_by_channel, sample_header, rows_by_channel)``.

    Text is left exactly as exported; only the repeated block framing is undone.
    """
    path = Path(path)
    lines = _read_lines(path)
    if not _is_sxp_tabular(lines):
        raise ValueError(f"{path.name}: not an ADRE Sxp Tabular List export")

    meta_header: list[str] = []
    sample_header: list[str] = []
    meta_by_channel: dict[str, list[str]] = {}
    rows_by_channel: dict[str, list[list[str]]] = {}
    seen_samples: dict[str, set[str]] = {}
    mode = ""

    for line in lines:
        if not line.strip():
            mode = ""
            continue
        if line.startswith(META_HEADER_PREFIX):
            mode = "meta"
            meta_header = _strip_trailing_blanks([c.strip() for c in line.split("|")])
            continue
        if line.startswith(SAMPLE_HEADER_PREFIX):
            mode = "sample"
            sample_header = _strip_trailing_blanks([c.strip() for c in line.split("|")])
            continue
        if mode == "meta" and meta_header:
            cells = _split_row(line, len(meta_header), "|")
            name = cells[meta_header.index("Channel Name")].strip()
            if name:
                meta_by_channel.setdefault(name, cells)
            continue
        if mode == "sample" and sample_header:
            cells = _split_row(line, len(sample_header), "|")
            name = cells[sample_header.index("Channel Name")].strip()
            number = cells[sample_header.index("Sample#")].strip()
            if not name or not number.isdigit():
                continue
            # The same sample can appear twice when plot groups overlap.
            if number in seen_samples.setdefault(name, set()):
                continue
            seen_samples[name].add(number)
            rows_by_channel.setdefault(name, []).append(cells)

    if not rows_by_channel:
        raise ValueError(f"{path.name}: no sample rows found")
    return meta_header, meta_by_channel, sample_header, rows_by_channel


def _used_columns(header: list[str], rows: list[list[str]]) -> list[int]:
    """Column indexes that carry a value for this channel, plus the key ones."""
    keep: list[int] = []
    for index, column in enumerate(header):
        if column in _KEEP_ALWAYS or any(row[index].strip() for row in rows):
            keep.append(index)
    return keep


def _render_channel(
    meta_header: list[str],
    meta_row: list[str],
    sample_header: list[str],
    rows: list[list[str]],
    *,
    drop_empty_columns: bool,
) -> str:
    keep = (
        _used_columns(sample_header, rows)
        if drop_empty_columns
        else list(range(len(sample_header)))
    )

    def line(cells: list[str]) -> str:
        return "\t".join(cells) + "\t"

    out = ["", ""]
    out.append(line(meta_header))
    out.append(line(meta_row))
    out.append("")
    out.append(line([sample_header[i] for i in keep]))
    out.extend(line([row[i] for i in keep]) for row in rows)
    return "\r\n".join(out) + "\r\n"


def split_tabular_export(
    source: str | Path,
    folder: str | Path,
    *,
    encoding: str = "utf-16",
    drop_empty_columns: bool = True,
    name_for_plots: bool = True,
    write_upload_groups: bool = True,
) -> SplitResult:
    """Write one file per channel, plus a single ``channels.csv`` configuration table.

    ``encoding`` defaults to UTF-16 so the output matches the ADRE exports the
    rest of the toolchain already reads.
    """
    source = Path(source)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)

    meta_header, meta_by_channel, sample_header, rows_by_channel = read_tabular_blocks(source)
    blank_meta = [""] * len(meta_header)
    renames = plot_names(rows_by_channel) if name_for_plots else {}
    meta_name_at = meta_header.index("Channel Name")
    sample_name_at = sample_header.index("Channel Name")

    written: list[Path] = []
    used_names: set[str] = set()
    for name, rows in rows_by_channel.items():
        display = renames.get(name, name)
        stem = safe_filename(display)
        candidate = stem
        suffix = 2
        while candidate.casefold() in used_names:
            candidate = f"{stem}_{suffix}"
            suffix += 1
        used_names.add(candidate.casefold())
        target = folder / f"{candidate}.csv"
        target.write_text(
            _render_channel(
                meta_header,
                _renamed(meta_by_channel.get(name, blank_meta), meta_name_at, display),
                sample_header,
                [_renamed(row, sample_name_at, display) for row in rows],
                drop_empty_columns=drop_empty_columns,
            ),
            encoding=encoding,
            newline="",
        )
        written.append(target)

    config_file = folder / "channels.csv"
    with config_file.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([*meta_header, "ADRE Name", "Samples", "File"])
        for target, (name, rows) in zip(written, rows_by_channel.items()):
            meta_row = _renamed(
                meta_by_channel.get(name, blank_meta), meta_name_at, renames.get(name, name)
            )
            writer.writerow([*meta_row, name, len(rows), target.name])

    n_samples = sum(len(rows) for rows in rows_by_channel.values())
    n_blocks = sum(1 for line in _read_lines(source) if line.startswith(META_HEADER_PREFIX))
    groups_folder = folder.parent / "groups"
    groups: dict[str, list[Path]] = {}
    if write_upload_groups and written:
        machines = {
            target: _machine_label(meta_header, meta_by_channel.get(name, []))
            for target, (name, _rows) in zip(written, rows_by_channel.items())
        }
        groups = write_groups(written, groups_folder, machines=machines)
    return SplitResult(
        source=source,
        folder=folder,
        channel_files=written,
        config_file=config_file,
        n_channels=len(rows_by_channel),
        n_samples=n_samples,
        config_rows_in=n_blocks * len(meta_by_channel),
        config_rows_out=len(meta_by_channel),
        renames=renames,
        groups=groups,
        groups_folder=groups_folder if groups else None,
    )


def split_export_directory(
    exports: str | Path, folder: str | Path | None = None
) -> list[SplitResult]:
    """Split every Tabular List export in ``exports`` into ``exports/channels``."""
    exports = Path(exports)
    folder = Path(folder) if folder is not None else exports / "channels"
    results: list[SplitResult] = []
    for path in sorted(exports.glob("*.csv")):
        try:
            results.append(split_tabular_export(path, folder))
        except (ValueError, OSError):
            continue
    return results
