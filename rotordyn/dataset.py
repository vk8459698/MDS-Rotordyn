"""Assemble the three exported channels into one speed-referenced run."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .adre import (
    AMPLITUDE_COL,
    PHASE_COL,
    SPEED_COL,
    AdreChannel,
    load_directory,
)
from .vectors import (
    PROBE_1X_DEG,
    PROBE_1Y_DEG,
    precession_split,
    to_phasor,
)

#: Fraction of maximum speed above which the machine counts as "at speed".
_AT_SPEED_FRACTION = 0.99


@dataclass
class RunData:
    """One startup/dwell/shutdown record with both probes on a common index."""

    frame: pd.DataFrame
    amp_unit: str
    phase_unit: str
    speed_unit: str
    probe_1x_name: str
    probe_1y_name: str
    keyphasor_name: str
    keyphasor_deg: float | None
    source_files: dict[str, str]
    flag_counts: dict[str, int]

    @property
    def rpm(self) -> np.ndarray:
        return self.frame["rpm"].to_numpy(dtype=float)

    @property
    def phasor_1x(self) -> np.ndarray:
        return self.frame["z_1x"].to_numpy(dtype=complex)

    @property
    def phasor_1y(self) -> np.ndarray:
        return self.frame["z_1y"].to_numpy(dtype=complex)

    def segment_bounds(self) -> dict[str, tuple[int, int]]:
        """Index ranges (half-open) for coast-up, at-speed dwell and coast-down.

        The record is split on the first and last time the machine reaches
        99% of its maximum speed, which keeps the constant-speed dwell out of
        both transients.  A polar plot of a dwell is meaningless - all the
        points land on top of each other.
        """
        rpm = self.rpm
        n = len(rpm)
        peak = np.nanmax(rpm)
        at_speed = np.where(rpm >= _AT_SPEED_FRACTION * peak)[0]
        if at_speed.size == 0:
            return {"Full record": (0, n)}
        first, last = int(at_speed[0]), int(at_speed[-1])
        return {
            "Coast-up (startup)": (0, first + 1),
            "At-speed dwell": (first, last + 1),
            "Coast-down (shutdown)": (last, n),
            "Full record": (0, n),
        }

    def segment(self, name: str) -> pd.DataFrame:
        start, stop = self.segment_bounds()[name]
        return self.frame.iloc[start:stop]

    def rotation_evidence(self) -> dict[str, float]:
        """Numbers backing the counter-clockwise rotation call."""
        frame = self.frame
        strong = (
            frame["valid"]
            & (frame["amp_1x"] > 0.3)
            & (frame["amp_1y"] > 0.3)
            & (frame["rpm"] > 200)
        ).to_numpy()
        if not strong.any():
            return {}
        zx = frame["z_1x"].to_numpy(dtype=complex)[strong]
        zy = frame["z_1y"].to_numpy(dtype=complex)[strong]
        lead = (frame["phase_1y"].to_numpy()[strong] - frame["phase_1x"].to_numpy()[strong]) % 360.0
        ccw, cw = precession_split(zx, zy)
        return {
            "n_samples": float(strong.sum()),
            "median_1y_minus_1x_deg": float(np.median(lead)),
            "mean_ccw_magnitude": float(np.mean(np.abs(ccw))),
            "mean_cw_magnitude": float(np.mean(np.abs(cw))),
            "fraction_ccw_dominant": float(np.mean(np.abs(ccw) > np.abs(cw))),
        }


def _pick_probe_pair(probes: list[AdreChannel]) -> tuple[AdreChannel, AdreChannel]:
    """Choose the XY pair to plot when a Tabular List export contains many probes.

    Prefer the named bearing-1 pair when present; otherwise the first two probes
    that sit 90 deg apart.
    """
    by_name = {c.name: c for c in probes}
    if "BRG1X" in by_name and "BRG1Y" in by_name:
        return by_name["BRG1X"], by_name["BRG1Y"]

    for i, probe_a in enumerate(probes):
        angle_a = probe_a.angle_deg if probe_a.angle_deg is not None else None
        if angle_a is None:
            continue
        for probe_b in probes[i + 1 :]:
            angle_b = probe_b.angle_deg if probe_b.angle_deg is not None else None
            if angle_b is None:
                continue
            separation = abs(((angle_a - angle_b) + 180.0) % 360.0 - 180.0)
            if abs(separation - 90.0) <= 1.0:
                return probe_a, probe_b
    raise ValueError(
        f"Expected an orthogonal XY probe pair, found {len(probes)} vibration channels"
    )


def _classify(channels: list[AdreChannel]) -> tuple[AdreChannel, AdreChannel, AdreChannel]:
    """Pick out the keyphasor and the two orthogonal vibration probes.

    Classification uses the metadata block, not the file name: the keyphasor is
    the channel that reports speed and carries no 1X vector, and the two
    vibration probes are told apart by the sign of their mounting angle
    (``Right`` is clockwise-positive, ``Left`` negative).
    """
    keyphasors = [c for c in channels if c.is_keyphasor]
    probes = [
        c
        for c in channels
        if not c.is_keyphasor
        and AMPLITUDE_COL in c.samples.columns
        and c.name  # skip blank Unconfigured rows
        and "_SEIS" not in c.name.upper()
        and not c.name.upper().startswith("THRUST")
    ]
    if not keyphasors:
        raise ValueError("No keyphasor channel found (need a channel with a Speed column)")
    keyphasors = sorted(keyphasors, key=lambda c: c.n_samples, reverse=True)
    if len(probes) < 2:
        raise ValueError(f"Expected at least 2 vibration probes, found {len(probes)}")

    probe_a, probe_b = _pick_probe_pair(probes)
    angle_a = probe_a.angle_deg if probe_a.angle_deg is not None else PROBE_1X_DEG
    angle_b = probe_b.angle_deg if probe_b.angle_deg is not None else PROBE_1Y_DEG
    if angle_a < angle_b:
        probe_a, probe_b = probe_b, probe_a
        angle_a, angle_b = angle_b, angle_a
    separation = abs(((angle_a - angle_b) + 180.0) % 360.0 - 180.0)
    if abs(separation - 90.0) > 1.0:
        raise ValueError(
            f"Probes are {separation:.1f} deg apart; the projection maths needs 90 deg"
        )
    return probe_a, probe_b, keyphasors[0]


def _keep_longest_by_name(channels: list[AdreChannel]) -> list[AdreChannel]:
    """When a folder mixes two exports, keep the longer copy of each channel."""
    best: dict[tuple[str, bool], AdreChannel] = {}
    for channel in channels:
        key = (channel.name, channel.is_keyphasor)
        previous = best.get(key)
        if previous is None or channel.n_samples > previous.n_samples:
            best[key] = channel
    return list(best.values())


def build_run_from_channels(channels: list[AdreChannel]) -> RunData:
    """Align already-parsed channels on sample number.

    Split out from :func:`build_run` so that channels arriving from a browser
    upload take the same path as channels read from disk.
    """
    channels = _keep_longest_by_name(channels)
    probe_x, probe_y, keyphasor = _classify(channels)

    key = keyphasor.samples.set_index("Sample#")
    frames = {"1x": probe_x.samples.set_index("Sample#"), "1y": probe_y.samples.set_index("Sample#")}
    common = key.index
    for f in frames.values():
        common = common.intersection(f.index)
    if len(common) == 0:
        raise ValueError("Channels share no sample numbers; cannot align them")
    common = common.sort_values()

    key = key.loc[common]
    fx = frames["1x"].loc[common]
    fy = frames["1y"].loc[common]

    flags_x = fx[f"{AMPLITUDE_COL}__flag"].to_numpy()
    flags_p_x = fx[f"{PHASE_COL}__flag"].to_numpy()
    flags_y = fy[f"{AMPLITUDE_COL}__flag"].to_numpy()
    flags_p_y = fy[f"{PHASE_COL}__flag"].to_numpy()

    amp_x = fx[AMPLITUDE_COL].to_numpy(dtype=float)
    phase_x = fx[PHASE_COL].to_numpy(dtype=float)
    amp_y = fy[AMPLITUDE_COL].to_numpy(dtype=float)
    phase_y = fy[PHASE_COL].to_numpy(dtype=float)

    def joined(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        return np.array([" ".join(sorted({p for p in (i, j) if p})) for i, j in zip(a, b)])

    flag_1x = joined(flags_x, flags_p_x)
    flag_1y = joined(flags_y, flags_p_y)

    frame = pd.DataFrame(
        {
            "sample": common.to_numpy(),
            "timestamp": key["timestamp"].to_numpy(),
            "rpm": key[SPEED_COL].to_numpy(dtype=float),
            "amp_1x": amp_x,
            "phase_1x": phase_x,
            "amp_1y": amp_y,
            "phase_1y": phase_y,
            "direct_1x": fx["Direct"].to_numpy(dtype=float),
            "direct_1y": fy["Direct"].to_numpy(dtype=float),
            "gap_1x": fx["Avg Gap"].to_numpy(dtype=float),
            "gap_1y": fy["Avg Gap"].to_numpy(dtype=float),
            "amp_2x": (
                fx["2XAmplitude"].to_numpy(dtype=float)
                if "2XAmplitude" in fx.columns
                else np.full(len(common), np.nan)
            ),
            "phase_2x": (
                fx["2X Phase"].to_numpy(dtype=float)
                if "2X Phase" in fx.columns
                else np.full(len(common), np.nan)
            ),
            "amp_2y": (
                fy["2XAmplitude"].to_numpy(dtype=float)
                if "2XAmplitude" in fy.columns
                else np.full(len(common), np.nan)
            ),
            "phase_2y": (
                fy["2X Phase"].to_numpy(dtype=float)
                if "2X Phase" in fy.columns
                else np.full(len(common), np.nan)
            ),
            "flag_1x": flag_1x,
            "flag_1y": flag_1y,
        }
    )
    frame["z_1x"] = to_phasor(amp_x, phase_x)
    frame["z_1y"] = to_phasor(amp_y, phase_y)
    frame["valid"] = (
        (flag_1x == "")
        & (flag_1y == "")
        & np.isfinite(amp_x)
        & np.isfinite(phase_x)
        & np.isfinite(amp_y)
        & np.isfinite(phase_y)
    )

    flag_counts: dict[str, int] = {}
    for column in (flag_1x, flag_1y):
        for entry in column:
            for code in entry.split():
                flag_counts[code] = flag_counts.get(code, 0) + 1

    return RunData(
        frame=frame.reset_index(drop=True),
        amp_unit=probe_x.amp_unit or "mil pp",
        phase_unit=probe_x.phase_unit or "deg",
        speed_unit=keyphasor.speed_unit or "rpm",
        probe_1x_name=probe_x.name,
        probe_1y_name=probe_y.name,
        keyphasor_name=keyphasor.name,
        keyphasor_deg=keyphasor.angle_deg,
        source_files={
            "1X": probe_x.path.name,
            "1Y": probe_y.path.name,
            "Keyphasor": keyphasor.path.name,
        },
        flag_counts=dict(sorted(flag_counts.items())),
    )


def build_run(directory: str | Path = ".") -> RunData:
    """Load a directory of ADRE exports and align them on sample number."""
    return build_run_from_channels(load_directory(directory))
