"""Unit tests for the synchronous-vector and probe-rotation maths."""

import numpy as np
import pytest

from rotordyn.vectors import (
    PROBE_1X_DEG,
    PROBE_1Y_DEG,
    describe_position,
    precession_split,
    probe_phasor,
    rotate_probe_pair,
    slow_roll_vector,
    to_amp_phase,
    to_phasor,
)


def circular_orbit(radius: float, high_spot_deg: float):
    """1X/1Y phasors for a forward circular orbit.

    The high spot sits at ``high_spot_deg`` (clockwise from TDC) at the moment
    of the Keyphasor pulse and then travels counter-clockwise, so a probe at
    angle ``theta`` sees its peak ``high_spot_deg - theta`` degrees later.
    """
    z_1x = radius * np.exp(-1j * np.deg2rad(high_spot_deg - PROBE_1X_DEG))
    z_1y = radius * np.exp(-1j * np.deg2rad(high_spot_deg - PROBE_1Y_DEG))
    return z_1x, z_1y


class TestPhasorRoundTrip:
    @pytest.mark.parametrize("amp", [0.0, 0.083, 1.473, 12.5])
    @pytest.mark.parametrize("phase", [0.0, 37.0, 180.0, 259.0, 359.9])
    def test_round_trip(self, amp, phase):
        amp_out, phase_out = to_amp_phase(to_phasor(amp, phase))
        assert amp_out == pytest.approx(amp, abs=1e-12)
        if amp > 0:
            assert phase_out == pytest.approx(phase, abs=1e-9)

    def test_phase_wraps_into_0_360(self):
        _, phase = to_amp_phase(to_phasor(1.0, -30.0))
        assert phase == pytest.approx(330.0)

    def test_vectorised(self):
        amp = np.array([0.5, 1.0, 2.0])
        phase = np.array([10.0, 120.0, 300.0])
        amp_out, phase_out = to_amp_phase(to_phasor(amp, phase))
        assert np.allclose(amp_out, amp)
        assert np.allclose(phase_out, phase)


class TestProbeProjection:
    def test_returns_measured_vector_at_probe_locations(self):
        z_1x, z_1y = circular_orbit(0.6, 210.0)
        assert probe_phasor(z_1x, z_1y, PROBE_1X_DEG) == pytest.approx(z_1x)
        assert probe_phasor(z_1x, z_1y, PROBE_1Y_DEG) == pytest.approx(z_1y)

    @pytest.mark.parametrize("theta", np.arange(0.0, 360.0, 15.0))
    def test_circular_orbit_is_a_pure_phase_shift(self, theta):
        """Moving a probe around a circular orbit only changes phase."""
        radius, high_spot = 0.75, 130.0
        z_1x, z_1y = circular_orbit(radius, high_spot)
        amp, phase = to_amp_phase(probe_phasor(z_1x, z_1y, theta))
        assert amp == pytest.approx(2 * radius)
        assert phase == pytest.approx((high_spot - theta) % 360.0, abs=1e-9)

    def test_opposite_probe_inverts_the_signal(self):
        z_1x, z_1y = circular_orbit(0.4, 15.0)
        opposite = probe_phasor(z_1x, z_1y, PROBE_1X_DEG + 180.0)
        assert opposite == pytest.approx(-z_1x)

    def test_purely_vertical_motion_is_invisible_to_a_horizontal_probe(self):
        """Straight-line motion along TDC is invisible to a probe at 90° (horizontal)."""
        vertical = to_phasor(1.0, 40.0)
        z_1x = vertical * np.cos(np.deg2rad(PROBE_1X_DEG))
        z_1y = vertical * np.cos(np.deg2rad(PROBE_1Y_DEG))
        assert abs(probe_phasor(z_1x, z_1y, 90.0)) == pytest.approx(0.0, abs=1e-12)
        assert probe_phasor(z_1x, z_1y, 0.0) == pytest.approx(vertical)

    def test_elliptical_orbit_amplitude_varies_with_angle(self):
        """An ellipse must not behave like a circle under rotation."""
        z_1x = to_phasor(1.0, 0.0)
        z_1y = to_phasor(0.2, 90.0)
        amplitudes = [
            to_amp_phase(probe_phasor(z_1x, z_1y, t))[0] for t in np.arange(0, 360, 10)
        ]
        assert max(amplitudes) - min(amplitudes) > 0.5

    def test_rejects_non_orthogonal_probes(self):
        with pytest.raises(ValueError):
            probe_phasor(1 + 0j, 1 + 0j, 0.0, probe_1x_deg=0.0, probe_1y_deg=60.0)


class TestRotateProbePair:
    def test_zero_rotation_is_the_identity(self):
        z_1x, z_1y = circular_orbit(0.5, 77.0)
        rot_x, rot_y, theta_x, theta_y = rotate_probe_pair(z_1x, z_1y, 0.0)
        assert rot_x == pytest.approx(z_1x)
        assert rot_y == pytest.approx(z_1y)
        assert theta_x == pytest.approx(PROBE_1X_DEG)
        assert theta_y == pytest.approx(PROBE_1Y_DEG % 360.0)

    def test_full_turn_is_the_identity(self):
        z_1x, z_1y = circular_orbit(0.5, 77.0)
        rot_x, rot_y, _, _ = rotate_probe_pair(z_1x, z_1y, 360.0)
        assert rot_x == pytest.approx(z_1x)
        assert rot_y == pytest.approx(z_1y)

    def test_ninety_degrees_swaps_the_probes(self):
        """Rotating 90 deg clockwise puts 1X where 1Y was, and 1Y opposite 1X."""
        z_1x, z_1y = circular_orbit(0.5, 33.0)
        rot_x, rot_y, theta_x, theta_y = rotate_probe_pair(z_1x, z_1y, 90.0)
        assert rot_y == pytest.approx(z_1x)
        assert rot_x == pytest.approx(-z_1y)
        assert theta_x == pytest.approx(126.0)
        assert theta_y == pytest.approx(36.0)

    def test_probes_stay_orthogonal(self):
        z_1x, z_1y = circular_orbit(0.5, 33.0)
        for rotation in np.arange(0, 361, 17):
            _, _, theta_x, theta_y = rotate_probe_pair(z_1x, z_1y, rotation)
            separation = abs((theta_x - theta_y + 180) % 360 - 180)
            assert separation == pytest.approx(90.0)

    def test_amplitude_is_preserved_for_a_circular_orbit(self):
        z_1x, z_1y = circular_orbit(0.9, 0.0)
        for rotation in np.arange(0, 360, 7):
            rot_x, _, _, _ = rotate_probe_pair(z_1x, z_1y, rotation)
            assert to_amp_phase(rot_x)[0] == pytest.approx(1.8)


class TestPrecessionSplit:
    def test_forward_orbit_is_all_counter_clockwise(self):
        z_1x, z_1y = circular_orbit(1.0, 0.0)
        ccw, cw = precession_split(np.array([z_1x]), np.array([z_1y]))
        assert abs(ccw[0]) == pytest.approx(1.0)
        assert abs(cw[0]) == pytest.approx(0.0, abs=1e-12)

    def test_reversed_orbit_is_all_clockwise(self):
        """Swapping the two probe readings reverses the precession direction."""
        z_1x, z_1y = circular_orbit(1.0, 0.0)
        ccw, cw = precession_split(np.array([np.conj(z_1x)]), np.array([np.conj(z_1y)]))
        assert abs(cw[0]) == pytest.approx(1.0)
        assert abs(ccw[0]) == pytest.approx(0.0, abs=1e-12)


class TestSlowRoll:
    def test_averages_inside_the_window(self):
        phasor = np.array([1 + 0j, 1 + 0j, 5 + 5j])
        rpm = np.array([300.0, 400.0, 3000.0])
        assert slow_roll_vector(phasor, rpm, 250, 500) == pytest.approx(1 + 0j)

    def test_empty_window_returns_zero(self):
        phasor = np.array([1 + 0j])
        rpm = np.array([3000.0])
        assert slow_roll_vector(phasor, rpm, 250, 500) == 0j

    def test_respects_the_validity_mask(self):
        phasor = np.array([1 + 0j, 99 + 0j])
        rpm = np.array([300.0, 310.0])
        valid = np.array([True, False])
        assert slow_roll_vector(phasor, rpm, 250, 500, valid) == pytest.approx(1 + 0j)


@pytest.mark.parametrize(
    "angle,expected",
    [(0, "at TDC"), (45, "45\u00b0 right of TDC"), (315, "45\u00b0 left of TDC"), (180, "at BDC")],
)
def test_describe_position(angle, expected):
    assert describe_position(angle) == expected
