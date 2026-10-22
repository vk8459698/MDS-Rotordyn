"""Stream reader for ADRE Sxp concatenated timebase (sync waveform) dumps.

A full-machine export is one UTF-16 CSV that repeats this block thousands of
times::

    Plot Name|Waveform Sample Number|...|Speed|Sampling Rate/Revs|...
    New Timebase Plot|47|29 Apr 2026 ...|BRG2X|...|3599 rpm|256/8 |...

    Sample Number|Amplitude|
    1|-0.281
    ...

The 1.5 GB dump in this repo is 16 753 of those plots.  Loading it with
:func:`rotordyn.adre.load_export` would decode the whole file into RAM, so
everything here streams and keeps only harmonic summaries (plus a few raw
orbits at slow-roll and rated speed).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .adre import _parse_angle
from .vectors import to_phasor

_SPEED_RE = re.compile(r"([+-]?(?:\d+\.?\d*|\.\d+))")
_RATE_RE = re.compile(r"(\d+)\s*/\s*(\d+)")

#: Channels used for radial rotor diagnostics.  Seismic and thrust are skipped.
_PROX_RE = re.compile(r"^BRG\d+[XY]$", re.IGNORECASE)


def peek_head_text(path: str | Path, n_bytes: int = 4096) -> str:
    """Decode just the start of an export, enough to recognise the format."""
    path = Path(path)
    with path.open("rb") as handle:
        head = handle.read(n_bytes)
    if head.startswith(b"\xff\xfe"):
        return head.decode("utf-16", errors="replace")
    if head.startswith(b"\xfe\xff"):
        return head.decode("utf-16-be", errors="replace")
    if head.startswith(b"\xef\xbb\xbf"):
        return head.decode("utf-8-sig", errors="replace")
    return head.decode("utf-8", errors="replace")


def timebase_encoding(path: str | Path) -> str:
    path = Path(path)
    with path.open("rb") as handle:
        head = handle.read(4)
    if head.startswith(b"\xff\xfe"):
        return "utf-16"
    if head.startswith(b"\xfe\xff"):
        return "utf-16-be"
    if head.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    return "utf-8"


def is_timebase_export(path: str | Path) -> bool:
    """True for concatenated ``Plot Name|...`` timebase dumps."""
    try:
        text = peek_head_text(path)
    except OSError:
        return False
    stripped = text.lstrip("\ufeff\r\n")
    return stripped.startswith("Plot Name|") or stripped.startswith("Plot Name\t")


def peek_timebase_kind(path: str | Path) -> str:
    """``sync``, ``async``, or ``timebase`` from the first plot's Variable field."""
    try:
        text = peek_head_text(path, 2500)
    except OSError:
        return "timebase"
    lowered = text.lower()
    if "async waveform" in lowered:
        return "async"
    if "sync waveform" in lowered:
        return "sync"
    return "timebase"


def _parse_speed(text: str) -> float:
    match = _SPEED_RE.search(str(text))
    return float(match.group(1)) if match else float("nan")


def _parse_rate(text: str, n_pts: int) -> tuple[int, int]:
    """Return ``(samples_per_rev, n_revs)`` from ``256/8`` or by inference."""
    match = _RATE_RE.search(str(text))
    if match:
        spr, revs = int(match.group(1)), int(match.group(2))
        if spr * revs == n_pts or n_pts == 0:
            return spr, revs
        if revs and n_pts % revs == 0:
            return n_pts // revs, revs
    if n_pts >= 2048 and n_pts % 8 == 0:
        return n_pts // 8, 8
    if n_pts >= 1024 and n_pts % 8 == 0:
        return n_pts // 8, 8
    return max(n_pts, 1), 1


def order_bin(order: float, n_revs: int) -> int:
    """FFT bin of a shaft-order component in an ``n_revs``-long sync record."""
    return int(round(order * n_revs))


def waveform_metrics(samples: np.ndarray) -> dict[str, float]:
    """Direct amplitude and noise clues from one timebase record."""
    x = np.asarray(samples, dtype=float)
    finite = x[np.isfinite(x)]
    if finite.size == 0:
        return {
            "direct": float("nan"),
            "mean": float("nan"),
            "rms": float("nan"),
            "crest": float("nan"),
            "n_spikes": 0.0,
        }
    peak = float(np.max(finite) - np.min(finite))
    rms = float(np.sqrt(np.mean(finite * finite)))
    sigma = float(np.std(finite))
    spikes = int(np.sum(np.abs(finite - np.mean(finite)) > 6.0 * sigma)) if sigma > 0 else 0
    crest = (0.5 * peak / rms) if rms > 1e-12 else 0.0
    return {
        "direct": peak,
        "mean": float(np.mean(finite)),
        "rms": rms,
        "crest": crest,
        "n_spikes": float(spikes),
    }


def extract_harmonics(samples: np.ndarray, n_revs: int) -> dict[str, float]:
    """1X / 2X / subsync amplitudes (peak-to-peak) and 1X/2X phase lag, degrees.

    Sync waveforms are keyphasor-triggered, so bin ``n_revs`` is 1X.  Phase lag
    is the Bently convention used by :func:`rotordyn.vectors.to_phasor`.
    """
    x = np.asarray(samples, dtype=float)
    n = x.size
    if n < 8 or n_revs < 1:
        return _empty_harmonics()
    spec = np.fft.rfft(x)
    scale_pp = 4.0 / n

    def component(order: float) -> tuple[float, float]:
        k = order_bin(order, n_revs)
        if k <= 0 or k >= spec.size:
            return float("nan"), float("nan")
        bin_val = spec[k]
        amp = float(np.abs(bin_val) * scale_pp)
        phase = float((-np.rad2deg(np.angle(bin_val))) % 360.0)
        return amp, phase

    amp_1x, phase_1x = component(1.0)
    amp_2x, phase_2x = component(2.0)
    amp_05, phase_05 = component(0.5)
    amp_033, _ = component(1.0 / 3.0)
    amp_047, _ = component(0.47)

    # Peak in the oil-whirl band (0.30-0.48X) and above 2X, excluding exact 2X.
    orders = np.arange(spec.size) / n_revs
    mag = np.abs(spec) * scale_pp
    whirl = mag[(orders >= 0.30) & (orders <= 0.48)]
    super_mask = (orders >= 2.4) & (orders <= 8.0)
    return {
        "amp_1x": amp_1x,
        "phase_1x": phase_1x,
        "amp_2x": amp_2x,
        "phase_2x": phase_2x,
        "amp_05x": amp_05,
        "phase_05x": phase_05,
        "amp_033x": amp_033,
        "amp_047x": amp_047,
        "subsync_max": float(np.max(whirl)) if whirl.size else 0.0,
        "supersync_max": float(np.max(mag[super_mask])) if super_mask.any() else 0.0,
    }


def _empty_harmonics() -> dict[str, float]:
    keys = (
        "amp_1x",
        "phase_1x",
        "amp_2x",
        "phase_2x",
        "amp_05x",
        "phase_05x",
        "amp_033x",
        "amp_047x",
        "subsync_max",
        "supersync_max",
    )
    return {k: float("nan") for k in keys}


@dataclass
class WaveformPlot:
    """One sync timebase record after streaming out of the dump."""

    channel: str
    machine: str
    sample: int
    timestamp: str
    rpm: float
    angle_deg: float | None
    direction: str
    variable: str
    amp_unit: str
    samples_per_rev: int
    n_revs: int
    samples: np.ndarray = field(repr=False)


def iter_timebase_plots(
    path: str | Path,
    *,
    prox_only: bool = False,
    channels: set[str] | None = None,
):
    """Yield :class:`WaveformPlot` records.

    ``prox_only`` drops seismic, thrust and keyphasor before the sample table
    is parsed.  ``channels`` further restricts to an explicit name set.
    """
    path = Path(path)
    encoding = timebase_encoding(path)
    header: list[str] | None = None
    meta: dict[str, str] | None = None
    buf: list[float] = []
    mode = "idle"

    def _wanted(name: str) -> bool:
        if channels is not None:
            return name in channels
        if prox_only:
            return bool(_PROX_RE.match(name))
        return True

    def emit() -> WaveformPlot | None:
        if meta is None or not buf:
            return None
        name = meta.get("Channel Name", "").strip()
        if not _wanted(name):
            return None
        arr = np.asarray(buf, dtype=np.float64)
        spr, revs = _parse_rate(meta.get("Sampling Rate/Revs", ""), arr.size)
        angle = _parse_angle(meta.get("Probe Angle", ""), meta.get("Probe Direction", ""))
        sample_text = meta.get("Waveform Sample Number", "")
        try:
            sample_no = int(float(sample_text))
        except (TypeError, ValueError):
            sample_no = -1
        return WaveformPlot(
            channel=name,
            machine=meta.get("Machine Name", "").strip(),
            sample=sample_no,
            timestamp=meta.get("Waveform Timestamp", "").strip(),
            rpm=_parse_speed(meta.get("Speed", "")),
            angle_deg=angle,
            direction=meta.get("Probe Direction", "").strip(),
            variable=meta.get("Variable", "").strip(),
            amp_unit=meta.get("Y axis Units", "").strip() or "mil",
            samples_per_rev=spr,
            n_revs=revs,
            samples=arr,
        )

    with path.open("r", encoding=encoding, errors="replace", newline="") as handle:
        for raw in handle:
            line = raw.rstrip("\r\n")
            if not line.strip():
                continue
            if line.startswith("Plot Name|") or line.startswith("Plot Name\t"):
                plotted = emit()
                if plotted is not None:
                    yield plotted
                delim = "|" if "|" in line else "\t"
                header = [c.strip() for c in line.split(delim)]
                while header and header[-1] == "":
                    header.pop()
                meta = None
                buf = []
                mode = "meta"
                continue
            if mode == "meta" and header is not None:
                delim = "|" if "|" in line else "\t"
                cells = [c.strip() for c in line.split(delim)]
                cells += [""] * (len(header) - len(cells))
                meta = dict(zip(header, cells[: len(header)]))
                name = meta.get("Channel Name", "").strip()
                mode = "samples" if _wanted(name) else "skip"
                buf = []
                continue
            if line.startswith("Sample Number|") or line.startswith("Sample Number\t"):
                if mode != "skip":
                    mode = "samples"
                    buf = []
                continue
            if mode == "samples" and line and line[0].isdigit():
                delim = "|" if "|" in line else "\t"
                parts = line.split(delim)
                if len(parts) >= 2:
                    try:
                        buf.append(float(parts[1]))
                    except ValueError:
                        buf.append(float("nan"))
            elif mode == "skip":
                continue
        plotted = emit()
        if plotted is not None:
            yield plotted


def _keep_raw(rpm: float, rpm_low: float = 200.0, rpm_high: float = 800.0, rated: float = 3400.0) -> bool:
    if not np.isfinite(rpm):
        return False
    return rpm <= rpm_high or rpm >= rated


def summarise_timebase(
    path: str | Path,
    *,
    progress: bool = False,
    keep_raw: bool = True,
) -> tuple[pd.DataFrame, dict[tuple[str, int], np.ndarray]]:
    """Stream a timebase dump into a harmonic table plus selected raw waveforms.

    Raw waveforms are kept only at slow-roll and near rated speed so a 1.5 GB
    dump does not stay in RAM.  1X orbits at other speeds are rebuilt from the
    harmonic table.
    """
    path = Path(path)
    rows: list[dict] = []
    raw: dict[tuple[str, int], np.ndarray] = {}
    n_plots = 0
    for plot in iter_timebase_plots(path, prox_only=True):
        n_plots += 1
        if progress and n_plots % 500 == 0:
            print(f"  ... {n_plots} proximity waveforms", flush=True)
        noise = waveform_metrics(plot.samples)
        harm = extract_harmonics(plot.samples, plot.n_revs)
        rows.append(
            {
                "channel": plot.channel,
                "machine": plot.machine,
                "sample": plot.sample,
                "timestamp": plot.timestamp,
                "rpm": plot.rpm,
                "angle_deg": plot.angle_deg if plot.angle_deg is not None else float("nan"),
                "direction": plot.direction,
                "amp_unit": plot.amp_unit,
                "n_pts": int(plot.samples.size),
                "n_revs": plot.n_revs,
                "samples_per_rev": plot.samples_per_rev,
                **noise,
                **harm,
            }
        )
        if keep_raw and _keep_raw(plot.rpm):
            raw[(plot.channel, plot.sample)] = plot.samples
    if not rows:
        raise ValueError(f"{path.name}: no proximity-probe timebase plots found")
    frame = pd.DataFrame(rows).sort_values(["channel", "sample"]).reset_index(drop=True)
    return frame, raw


def harmonics_cache_path(source: str | Path) -> Path:
    return Path(source).with_name(Path(source).stem + ".harmonics.csv")


def save_harmonics(frame: pd.DataFrame, source: str | Path) -> Path:
    path = harmonics_cache_path(source)
    frame.to_csv(path, index=False)
    return path


def load_harmonics(path: str | Path) -> pd.DataFrame:
    return pd.read_csv(path)


def bearing_pairs(frame: pd.DataFrame) -> list[tuple[str, pd.DataFrame, pd.DataFrame]]:
    """Group ``BRGnX`` / ``BRGnY`` tables that share sample numbers."""
    names = sorted({str(c) for c in frame["channel"].unique()})
    pairs: list[tuple[str, pd.DataFrame, pd.DataFrame]] = []
    xs = [n for n in names if n.upper().endswith("X")]
    for x_name in xs:
        y_name = x_name[:-1] + "Y"
        if y_name not in names:
            y_name = x_name[:-1] + "y"
        matches = [n for n in names if n.upper() == y_name.upper()]
        if not matches:
            continue
        y_name = matches[0]
        fx = frame[frame["channel"] == x_name].set_index("sample").sort_index()
        fy = frame[frame["channel"] == y_name].set_index("sample").sort_index()
        common = fx.index.intersection(fy.index)
        if len(common) < 2:
            continue
        pairs.append((x_name[:-1], fx.loc[common], fy.loc[common]))
    return pairs


def aligned_phasors(fx: pd.DataFrame, fy: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """1X phasors and rpm on the shared index of one bearing pair."""
    rpm = fx["rpm"].to_numpy(dtype=float)
    zx = to_phasor(fx["amp_1x"].to_numpy(dtype=float), fx["phase_1x"].to_numpy(dtype=float))
    zy = to_phasor(fy["amp_1x"].to_numpy(dtype=float), fy["phase_1x"].to_numpy(dtype=float))
    return rpm, zx, zy
