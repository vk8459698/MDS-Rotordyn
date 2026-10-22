"""Tests for the ADRE reader, the run assembly and the view model.

Everything below the class ``TestRealDataset`` runs against the supplied
exports; the parsing tests build their own file so they run anywhere.
"""

import numpy as np
import pytest

from rotordyn.adre import load_channel, split_value
from rotordyn.model import PolarModel, ViewSettings
from rotordyn.vectors import to_amp_phase, to_phasor

SYNTHETIC = "\n".join(
    [
        "",
        "CH#\tChannel Name\tMachine Name\tStatus\tAngle\tDirection\tSpeed Units(P)\tAmp Unit\tPhase Unit\t",
        "1\tBRG9X\tRig\tOK\t45\u00b0\tRight\trpm\tmil pp\tdeg\t",
        "",
        "CH#\tChannel Name\tSample#\tSample Cause\tDate\tSpeed(P)\tDirect\tAvg Gap\t1XAmplitude\t1X Phase\t",
        "1\tBRG9X\t10\tDR-T\t30Apr2026 19:28:50.563\t1200\t1.5\t-9.8\t0.500\t120\t",
        "1\tBRG9X\t11\tDR-T\t30Apr2026 19:28:52.563\t1300\t1.6\t-9.7\t0.610K25\t130K25\t",
        "",
    ]
)


@pytest.mark.parametrize(
    "cell,expected",
    [
        ("0.083K25", (0.083, "K25")),
        ("280BMA", (280.0, "BMA")),
        ("2FNX", (2.0, "FNX")),
        ("-9.847", (-9.847, "")),
        ("3614", (3614.0, "")),
        (".5", (0.5, "")),
    ],
)
def test_split_value(cell, expected):
    value, flag = split_value(cell)
    assert (value, flag) == expected


@pytest.mark.parametrize("cell", ["", "   ", "n/a"])
def test_split_value_rejects_non_numeric(cell):
    value, flag = split_value(cell)
    assert np.isnan(value)


class TestChannelParsing:
    @pytest.fixture
    def channel(self, tmp_path):
        path = tmp_path / "rig.csv"
        path.write_text(SYNTHETIC, encoding="utf-16")
        return load_channel(path)

    def test_metadata(self, channel):
        assert channel.name == "BRG9X"
        assert channel.machine == "Rig"
        assert channel.angle_deg == pytest.approx(45.0)
        assert channel.amp_unit == "mil pp"

    def test_left_mounted_probe_gets_a_negative_angle(self, tmp_path):
        path = tmp_path / "left.csv"
        path.write_text(SYNTHETIC.replace("\tRight\t", "\tLeft\t"), encoding="utf-16")
        assert load_channel(path).angle_deg == pytest.approx(-45.0)

    def test_values_and_flags_are_separated(self, channel):
        samples = channel.samples
        assert samples["1XAmplitude"].tolist() == pytest.approx([0.5, 0.61])
        assert samples["1X Phase"].tolist() == pytest.approx([120.0, 130.0])
        assert samples["1XAmplitude__flag"].tolist() == ["", "K25"]

    def test_timestamps_are_parsed(self, channel):
        assert channel.samples["timestamp"].iloc[0].year == 2026

    def test_flag_summary(self, channel):
        assert channel.flag_summary()["K25"] == 2

    def test_utf8_export_also_loads(self, tmp_path):
        path = tmp_path / "utf8.csv"
        path.write_text(SYNTHETIC, encoding="utf-8")
        assert load_channel(path).name == "BRG9X"

    def test_missing_sample_header_is_rejected(self, tmp_path):
        path = tmp_path / "broken.csv"
        path.write_text("\n".join(SYNTHETIC.splitlines()[:3]), encoding="utf-16")
        with pytest.raises(ValueError):
            load_channel(path)


class TestRealDataset:
    def test_channels_identified_from_metadata(self, run):
        assert run.probe_1x_name == "BRG1X"
        assert run.probe_1y_name == "BRG1Y"
        assert run.keyphasor_deg == pytest.approx(100.0)
        assert run.amp_unit == "mil pp"

    def test_channels_are_aligned_on_sample_number(self, run):
        assert len(run.frame) == 1051
        assert run.frame["sample"].is_monotonic_increasing

    def test_speed_comes_from_the_keyphasor(self, run):
        assert run.rpm.min() == pytest.approx(0.0)
        assert run.rpm.max() == pytest.approx(3614.0)

    def test_segments_cover_the_record_without_gaps(self, run):
        bounds = run.segment_bounds()
        assert bounds["Coast-up (startup)"][0] == 0
        assert bounds["Coast-down (shutdown)"][1] == len(run.frame)
        assert bounds["Coast-up (startup)"][1] <= bounds["At-speed dwell"][1]

    def test_flags_found_are_the_documented_ones(self, run):
        assert set(run.flag_counts) <= {"K25", "BMA", "A1H", "FNX"}

    def test_rotation_direction_is_counter_clockwise(self, run):
        evidence = run.rotation_evidence()
        assert evidence["median_1y_minus_1x_deg"] == pytest.approx(90.0, abs=15.0)
        assert evidence["fraction_ccw_dominant"] > 0.95


class TestViewModel:
    def test_zero_rotation_reproduces_the_raw_columns(self, run):
        model = PolarModel(run)
        result = model.compute(
            ViewSettings(segment="Full record", rotation_deg=0.0, hide_flagged=False)
        )
        assert np.allclose(result.amp_1x, run.frame["amp_1x"], atol=1e-9)
        assert np.allclose(result.amp_1y, run.frame["amp_1y"], atol=1e-9)
        # Phase is only meaningful where there is amplitude to carry it.
        strong = run.frame["amp_1x"].to_numpy() > 0.05
        assert np.allclose(result.phase_1x[strong], run.frame["phase_1x"][strong], atol=1e-6)

    def test_hiding_flagged_samples_removes_rows(self, run):
        model = PolarModel(run)
        shown = model.compute(ViewSettings(segment="Full record", hide_flagged=True))
        all_rows = model.compute(ViewSettings(segment="Full record", hide_flagged=False))
        assert shown.n_points < all_rows.n_points
        assert shown.n_dropped == all_rows.n_points - shown.n_points

    def test_speed_filter_is_applied(self, run):
        model = PolarModel(run)
        result = model.compute(
            ViewSettings(segment="Full record", rpm_min=1000, rpm_max=2000, hide_flagged=False)
        )
        assert result.rpm.min() >= 1000
        assert result.rpm.max() <= 2000

    def test_rotation_updates_the_reported_probe_positions(self, run):
        model = PolarModel(run)
        result = model.compute(ViewSettings(rotation_deg=75.0))
        assert result.theta_1x == pytest.approx(111.0)
        assert result.theta_1y == pytest.approx(21.0)

    def test_full_turn_returns_the_original_vectors(self, run):
        model = PolarModel(run)
        base = model.compute(ViewSettings(segment="Full record", rotation_deg=0.0))
        turned = model.compute(ViewSettings(segment="Full record", rotation_deg=360.0))
        assert np.allclose(base.amp_1x, turned.amp_1x)
        assert np.allclose(base.phase_1x, turned.phase_1x)

    def test_slow_roll_removes_the_low_speed_vector(self, run):
        model = PolarModel(run)
        settings = ViewSettings(segment="Coast-up (startup)", slow_roll=True)
        result = model.compute(settings)
        assert abs(result.slow_roll_1x) > 0
        low_speed = result.rpm <= settings.slow_roll_rpm[1]
        uncompensated = model.compute(ViewSettings(segment="Coast-up (startup)"))
        assert result.amp_1x[low_speed].mean() < uncompensated.amp_1x[low_speed].mean()

    def test_status_line_mentions_both_probes(self, run):
        result = PolarModel(run).compute(ViewSettings(rotation_deg=90.0))
        line = result.status_line()
        assert "1X probe" in line and "1Y probe" in line


def test_rotation_is_reversible_on_real_data(run):
    """Rotating by +r then -r must return the measured vectors."""
    model = PolarModel(run)
    base = model.compute(ViewSettings(segment="Full record", rotation_deg=0.0))
    there = model.compute(ViewSettings(segment="Full record", rotation_deg=123.0))
    back = model.compute(ViewSettings(segment="Full record", rotation_deg=123.0 - 123.0))
    assert not np.allclose(base.phase_1x, there.phase_1x)
    assert np.allclose(base.phase_1x, back.phase_1x)


def test_round_trip_through_phasors_preserves_the_export(run):
    amp = run.frame["amp_1x"].to_numpy()
    phase = run.frame["phase_1x"].to_numpy()
    amp_out, phase_out = to_amp_phase(to_phasor(amp, phase))
    assert np.allclose(amp_out, amp, atol=1e-12)
    strong = amp > 0.05
    assert np.allclose(phase_out[strong], phase[strong], atol=1e-6)
