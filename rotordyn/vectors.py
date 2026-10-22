"""Synchronous (1X) vector maths: phasors, probe rotation, slow-roll removal.

Conventions used everywhere in this package
-------------------------------------------
Viewing direction
    The machine is viewed from the end that makes the export's ``Right`` /
    ``Left`` labels true, i.e. the view in which 1X sits 45 deg to the right of
    Top Dead Centre and 1Y 54 deg to the left (machine drawings; the ADRE export
    metadata still lists 45 deg / 45 deg).

Probe angular coordinate ``theta``
    Degrees from Top Dead Centre, **positive clockwise** (positive = toward the
    right) in that view.
    1X = +36, 1Y = -54 (= 306), Keyphasor = +100.

Phase
    Bently Nevada convention: phase *lag*, in degrees of shaft rotation from
    the Keyphasor pulse to the positive peak of the filtered vibration signal.
    Larger phase = more lag.

Shaft rotation
    Counter-clockwise in the view above.  This is not assumed - it is measured
    from the data (see ``precession_split``): the 1X synchronous orbits are
    almost purely forward-precessing counter-clockwise, and 1Y reads ~90 deg
    more lag than 1X, which places 1Y downstream of 1X in the direction of
    rotation.

Phasor definition
    A probe reading ``(A_pp, phi)`` becomes the complex phasor

        Z = (A_pp / 2) * exp(-i * phi)

    so that the instantaneous displacement toward the probe is
    ``Re{Z * exp(i * Omega * tau)}`` with ``tau`` measured from the Keyphasor
    pulse.  The peak then occurs ``phi`` degrees of rotation after the pulse,
    which is exactly the lag convention above.
"""

from __future__ import annotations

import numpy as np

#: Probe mounting angles, degrees clockwise-positive from Top Dead Centre.
PROBE_1X_DEG = 36.0
PROBE_1Y_DEG = -54.0
KEYPHASOR_DEG = 100.0

#: Shaft rotation sense in the stated view, inferred from the data.
ROTATION_SENSE = "CCW"


def to_phasor(amp_pp: np.ndarray, phase_lag_deg: np.ndarray) -> np.ndarray:
    """Convert peak-to-peak amplitude and phase lag to a complex phasor."""
    amp_pp = np.asarray(amp_pp, dtype=float)
    phase_lag_deg = np.asarray(phase_lag_deg, dtype=float)
    return 0.5 * amp_pp * np.exp(-1j * np.deg2rad(phase_lag_deg))


def to_amp_phase(phasor: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Inverse of :func:`to_phasor`; phase is wrapped into ``[0, 360)``."""
    phasor = np.asarray(phasor, dtype=complex)
    amp_pp = 2.0 * np.abs(phasor)
    phase = np.rad2deg(-np.angle(phasor)) % 360.0
    return amp_pp, phase


def unit_vector(theta_deg: np.ndarray | float) -> np.ndarray:
    """Unit vector of a probe at ``theta`` in the (right, up) picture plane."""
    theta = np.deg2rad(np.asarray(theta_deg, dtype=float))
    return np.stack([np.sin(theta), np.cos(theta)], axis=-1)


def probe_phasor(
    phasor_1x: np.ndarray,
    phasor_1y: np.ndarray,
    theta_deg: float,
    probe_1x_deg: float = PROBE_1X_DEG,
    probe_1y_deg: float = PROBE_1Y_DEG,
) -> np.ndarray:
    """1X vector a probe mounted at ``theta_deg`` would measure.

    Because 1X and 1Y are physically orthogonal, their phasors are the
    components of the shaft's synchronous motion on an orthonormal basis of the
    transverse plane.  A probe at any other angle simply reads the projection
    of that motion onto its own axis::

        Z(theta) = cos(theta - theta_1X) * Z_1X + cos(theta - theta_1Y) * Z_1Y

    This is exact for an elliptical orbit - no circular-orbit assumption is
    made.  For a purely circular forward orbit it collapses to a pure phase
    shift equal to the angle the probe was moved, which is the textbook rule of
    thumb.
    """
    if abs(abs(_wrap180(probe_1x_deg - probe_1y_deg)) - 90.0) > 1e-6:
        raise ValueError("probe_phasor requires two orthogonal probes")
    wx = np.cos(np.deg2rad(theta_deg - probe_1x_deg))
    wy = np.cos(np.deg2rad(theta_deg - probe_1y_deg))
    return wx * np.asarray(phasor_1x) + wy * np.asarray(phasor_1y)


def rotate_probe_pair(
    phasor_1x: np.ndarray,
    phasor_1y: np.ndarray,
    rotation_deg: float,
) -> tuple[np.ndarray, np.ndarray, float, float]:
    """Move both probes ``rotation_deg`` clockwise around the casing.

    Returns the phasors the relocated 1X and 1Y probes would read, together
    with their new mounting angles.  The pair stays 90 deg apart, so the result
    is still a valid orthogonal probe pair.  ``rotation_deg = 0`` reproduces the
    measured data exactly.
    """
    theta_x = PROBE_1X_DEG + rotation_deg
    theta_y = PROBE_1Y_DEG + rotation_deg
    rotated_x = probe_phasor(phasor_1x, phasor_1y, theta_x)
    rotated_y = probe_phasor(phasor_1x, phasor_1y, theta_y)
    return rotated_x, rotated_y, theta_x % 360.0, theta_y % 360.0


def precession_split(
    phasor_1x: np.ndarray, phasor_1y: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Split the synchronous orbit into counter-clockwise and clockwise parts.

    Any elliptical orbit is the sum of a forward and a reverse circular orbit.
    Comparing their magnitudes is what tells us which way the shaft turns:
    a healthy rotor precesses predominantly in the direction of rotation.
    """
    ux = unit_vector(PROBE_1X_DEG)
    uy = unit_vector(PROBE_1Y_DEG)
    phasor_1x = np.asarray(phasor_1x)
    phasor_1y = np.asarray(phasor_1y)
    horizontal = phasor_1x * ux[0] + phasor_1y * uy[0]
    vertical = phasor_1x * ux[1] + phasor_1y * uy[1]
    ccw = 0.5 * (horizontal + 1j * vertical)
    cw = 0.5 * (np.conj(horizontal) + 1j * np.conj(vertical))
    return ccw, cw


def slow_roll_vector(
    phasor: np.ndarray,
    rpm: np.ndarray,
    rpm_low: float,
    rpm_high: float,
    valid: np.ndarray | None = None,
) -> complex:
    """Average 1X vector inside a slow-roll speed window.

    At speeds well below the first critical the 1X response to unbalance is
    negligible, so whatever the probe still reads is mechanical and electrical
    runout.  Subtracting this vector is standard practice before reading a
    polar plot.  Returns ``0`` when the window contains no usable samples.
    """
    phasor = np.asarray(phasor, dtype=complex)
    rpm = np.asarray(rpm, dtype=float)
    mask = (rpm >= rpm_low) & (rpm <= rpm_high) & np.isfinite(phasor)
    if valid is not None:
        mask &= np.asarray(valid, dtype=bool)
    if not mask.any():
        return 0j
    return complex(np.mean(phasor[mask]))


def _wrap180(angle_deg: float) -> float:
    return (angle_deg + 180.0) % 360.0 - 180.0


def describe_position(theta_deg: float) -> str:
    """Human-readable clock position, e.g. ``'120° right of TDC'``."""
    theta = _wrap180(theta_deg)
    if abs(theta) < 0.5:
        return "at TDC"
    if abs(abs(theta) - 180.0) < 0.5:
        return "at BDC"
    side = "right" if theta > 0 else "left"
    return f"{abs(theta):.0f}\u00b0 {side} of TDC"
