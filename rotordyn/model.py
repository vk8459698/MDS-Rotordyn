"""Turns the UI control state into the vectors that get plotted.

Keeping this separate from the GUI means the same code path is exercised by
the unit tests and by the headless figure export.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .dataset import RunData
from .vectors import (
    describe_position,
    rotate_probe_pair,
    slow_roll_vector,
    to_amp_phase,
)


@dataclass
class ViewSettings:
    """Everything the user can change from the interface."""

    segment: str = "Coast-up (startup)"
    #: Degrees the probe pair is rotated clockwise around the casing, 0-360.
    rotation_deg: float = 0.0
    hide_flagged: bool = True
    slow_roll: bool = False
    slow_roll_rpm: tuple[float, float] = (300.0, 700.0)
    rpm_min: float | None = None
    rpm_max: float | None = None
    machine_view: bool = False
    show_speed_labels: bool = True


@dataclass
class ViewResult:
    """Plot-ready arrays for one control state."""

    rpm: np.ndarray
    amp_1x: np.ndarray
    phase_1x: np.ndarray
    amp_1y: np.ndarray
    phase_1y: np.ndarray
    theta_1x: float
    theta_1y: float
    r_max: float
    n_points: int
    n_dropped: int
    slow_roll_1x: complex
    slow_roll_1y: complex
    notes: list[str] = field(default_factory=list)
    #: The selected samples themselves, so trend and centreline plots can use
    #: the same segment and speed filtering as the polar and Bode plots.
    frame: "pd.DataFrame | None" = None

    def status_line(self) -> str:
        parts = [
            f"1X probe {describe_position(self.theta_1x)}",
            f"1Y probe {describe_position(self.theta_1y)}",
            f"{self.n_points} points plotted",
        ]
        if self.n_dropped:
            parts.append(f"{self.n_dropped} flagged samples hidden")
        return "   |   ".join(parts)


#: Default number of speed labels printed along a polar track.
N_SPEED_LABELS = 12


def speed_label_indices(rpm: np.ndarray, count: int = N_SPEED_LABELS) -> np.ndarray:
    """Indices at roughly evenly spaced *speeds*, not evenly spaced samples.

    Sampling is speed-triggered, so evenly spaced samples bunch the labels up
    wherever the machine dwelt and leave the fast parts of the track bare.

    This lives here rather than beside either plotting layer so that the
    Matplotlib and Plotly figures label their tracks identically, and so the
    web application never has to import Matplotlib.
    """
    if rpm.size == 0:
        return np.empty(0, dtype=int)
    targets = np.linspace(float(np.nanmin(rpm)), float(np.nanmax(rpm)), min(count, rpm.size))
    return np.unique([int(np.nanargmin(np.abs(rpm - t))) for t in targets])


class PolarModel:
    """Applies segment selection, filtering, slow-roll removal and rotation."""

    def __init__(self, run: RunData) -> None:
        self.run = run

    def segment_names(self) -> list[str]:
        return list(self.run.segment_bounds().keys())

    def speed_range(self) -> tuple[float, float]:
        rpm = self.run.rpm
        return float(np.nanmin(rpm)), float(np.nanmax(rpm))

    def compute(self, settings: ViewSettings) -> ViewResult:
        frame = self.run.segment(settings.segment)
        rpm_all = frame["rpm"].to_numpy(dtype=float)

        keep = np.isfinite(rpm_all)
        if settings.rpm_min is not None:
            keep &= rpm_all >= settings.rpm_min
        if settings.rpm_max is not None:
            keep &= rpm_all <= settings.rpm_max
        valid = frame["valid"].to_numpy(dtype=bool)
        n_dropped = int((keep & ~valid).sum())
        if settings.hide_flagged:
            keep &= valid

        z_1x = frame["z_1x"].to_numpy(dtype=complex)
        z_1y = frame["z_1y"].to_numpy(dtype=complex)

        notes: list[str] = []
        sr_x = sr_y = 0j
        if settings.slow_roll:
            low, high = settings.slow_roll_rpm
            sr_x = slow_roll_vector(z_1x, rpm_all, low, high, valid)
            sr_y = slow_roll_vector(z_1y, rpm_all, low, high, valid)
            if sr_x == 0j and sr_y == 0j:
                notes.append(
                    f"No usable samples between {low:.0f} and {high:.0f} rpm; "
                    "slow-roll compensation had no effect."
                )
            # Subtraction and probe projection are both linear, so removing the
            # runout vector here is equivalent to removing it after rotation.
            z_1x = z_1x - sr_x
            z_1y = z_1y - sr_y

        rot_x, rot_y, theta_x, theta_y = rotate_probe_pair(
            z_1x, z_1y, settings.rotation_deg
        )
        amp_x, phase_x = to_amp_phase(rot_x[keep])
        amp_y, phase_y = to_amp_phase(rot_y[keep])
        rpm = rpm_all[keep]

        finite = np.concatenate([amp_x, amp_y])
        finite = finite[np.isfinite(finite)]
        r_max = float(np.max(finite)) * 1.12 if finite.size else 1.0

        return ViewResult(
            rpm=rpm,
            amp_1x=amp_x,
            phase_1x=phase_x,
            amp_1y=amp_y,
            phase_1y=phase_y,
            theta_1x=theta_x,
            theta_1y=theta_y,
            r_max=max(r_max, 1e-3),
            n_points=int(keep.sum()),
            n_dropped=n_dropped if settings.hide_flagged else 0,
            slow_roll_1x=sr_x,
            slow_roll_1y=sr_y,
            notes=notes,
            frame=frame.iloc[np.flatnonzero(keep)],
        )
