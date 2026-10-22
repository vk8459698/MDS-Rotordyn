"""Shaft centreline: where the journal sits inside the bearing, from gap volts.

The 1X/1Y proximity probes report a DC ``Avg Gap`` voltage alongside the
synchronous vector.  Because the two probes are mounted 90 deg apart, their gap
readings form an orthogonal basis for the picture plane, so the journal's
position can be reconstructed with exactly the same geometry the 1X rotation
maths uses -- only applied to the DC component instead of the 1X phasor.

This is the plot that shows a machine lifting in its bearing as speed comes up,
and it is the one that makes an attitude-angle or alignment problem obvious.
"""

from __future__ import annotations

import numpy as np

from .vectors import PROBE_1X_DEG, PROBE_1Y_DEG, unit_vector

#: Bently Nevada 3300 XL 8 mm proximitor scale factor, mV per mil.
#:
#: The output is negative and grows *more* negative as the gap widens, so a
#: rising voltage means the shaft has moved *towards* the probe.
DEFAULT_SCALE_MV_PER_MIL = 200.0

#: Speed at or below which the machine is treated as at rest for referencing.
REST_RPM = 100.0


def gap_to_mils(
    gap_volts: np.ndarray,
    reference_volts: float,
    scale_mv_per_mil: float = DEFAULT_SCALE_MV_PER_MIL,
) -> np.ndarray:
    """Displacement *towards* the probe, in mils, relative to a reference gap."""
    gap_volts = np.asarray(gap_volts, dtype=float)
    return (gap_volts - reference_volts) / (scale_mv_per_mil / 1000.0)


def rest_reference(
    gap_volts: np.ndarray,
    rpm: np.ndarray,
    rest_rpm: float = REST_RPM,
) -> float:
    """Mean gap voltage while the machine is at rest, used as the plot origin.

    At rest the journal lies at the bottom of its clearance, which is the
    conventional reference point for a centreline plot.  If the record never
    goes that slow, the slowest sample is used instead.
    """
    gap_volts = np.asarray(gap_volts, dtype=float)
    rpm = np.asarray(rpm, dtype=float)
    finite = np.isfinite(gap_volts) & np.isfinite(rpm)
    if not finite.any():
        return float("nan")
    at_rest = finite & (rpm <= rest_rpm)
    if at_rest.any():
        return float(np.mean(gap_volts[at_rest]))
    slowest = np.argmin(np.where(finite, rpm, np.inf))
    return float(gap_volts[slowest])


def centreline_positions(
    gap_1x: np.ndarray,
    gap_1y: np.ndarray,
    reference_1x: float,
    reference_1y: float,
    scale_mv_per_mil: float = DEFAULT_SCALE_MV_PER_MIL,
    probe_1x_deg: float = PROBE_1X_DEG,
    probe_1y_deg: float = PROBE_1Y_DEG,
) -> tuple[np.ndarray, np.ndarray]:
    """Journal position in the (right, up) picture plane, in mils.

    The two probe axes are orthonormal, so the position is just the sum of each
    probe's displacement along its own axis.
    """
    towards_x = gap_to_mils(gap_1x, reference_1x, scale_mv_per_mil)
    towards_y = gap_to_mils(gap_1y, reference_1y, scale_mv_per_mil)
    axis_x = unit_vector(probe_1x_deg)
    axis_y = unit_vector(probe_1y_deg)
    right = towards_x * axis_x[0] + towards_y * axis_y[0]
    up = towards_x * axis_x[1] + towards_y * axis_y[1]
    return right, up


def clearance_circle(
    diametral_clearance_mils: float, points: int = 181
) -> tuple[np.ndarray, np.ndarray]:
    """Bearing clearance circle for a journal referenced at the bottom.

    With the origin at the resting position, the bearing centre sits one radial
    clearance above it, and the journal centre can travel anywhere inside a
    circle of that radius.
    """
    radius = diametral_clearance_mils / 2.0
    angle = np.linspace(0.0, 2.0 * np.pi, points)
    return radius * np.cos(angle), radius + radius * np.sin(angle)
