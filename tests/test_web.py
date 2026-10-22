"""Tests for the shaft centreline maths and the browser interface."""

from __future__ import annotations

import base64
from pathlib import Path

import numpy as np
import pytest

from rotordyn.adre import load_channel_bytes, load_directory
from rotordyn.centreline import (
    centreline_positions,
    clearance_circle,
    gap_to_mils,
    rest_reference,
)
from rotordyn.dataset import build_run, build_run_from_channels
from rotordyn.model import PolarModel, ViewSettings
from rotordyn.vectors import PROBE_1X_DEG, PROBE_1Y_DEG
from rotordyn.webapp import build_figure, create_app, parse_upload
from rotordyn.webplots import TREND_CHANNELS, empty_figure
from rotordyn.waveforms import is_timebase_export

DATA_DIR = Path(__file__).resolve().parents[1]


def _tabular_csvs() -> list[Path]:
    return [p for p in sorted(DATA_DIR.glob("*.csv")) if not is_timebase_export(p)]


@pytest.fixture(scope="module")
def run():
    return build_run(DATA_DIR)


@pytest.fixture(scope="module")
def client():
    return create_app(DATA_DIR).server.test_client()


class TestGapConversion:
    def test_reference_gap_reads_as_zero_displacement(self):
        assert gap_to_mils(np.array([-10.0]), -10.0) == pytest.approx([0.0])

    def test_a_rising_voltage_means_the_shaft_moved_towards_the_probe(self):
        # The proximitor grows more negative as the gap widens, so a less
        # negative reading is a smaller gap.
        assert gap_to_mils(np.array([-9.8]), -10.0)[0] > 0

    def test_scale_factor_is_two_hundred_millivolts_per_mil(self):
        assert gap_to_mils(np.array([-9.8]), -10.0) == pytest.approx([1.0])

    def test_rest_reference_uses_the_slow_samples(self):
        gap = np.array([-10.0, -10.2, -12.0, -12.0])
        rpm = np.array([0.0, 50.0, 3000.0, 3600.0])
        assert rest_reference(gap, rpm) == pytest.approx(-10.1)

    def test_rest_reference_falls_back_to_the_slowest_sample(self):
        gap = np.array([-12.0, -11.0])
        rpm = np.array([3000.0, 2000.0])
        assert rest_reference(gap, rpm) == pytest.approx(-11.0)


class TestCentrelineGeometry:
    def test_no_gap_change_leaves_the_journal_at_the_origin(self):
        right, up = centreline_positions(
            np.array([-10.0]), np.array([-12.0]), -10.0, -12.0
        )
        assert right == pytest.approx([0.0])
        assert up == pytest.approx([0.0])

    def test_equal_movement_towards_both_probes_lies_on_their_bisector(self):
        # Both probes seeing the same approach puts the shaft on the line that
        # bisects them, which for 36 deg and -54 deg is -9 deg from TDC.
        right, up = centreline_positions(
            np.array([-9.8]), np.array([-11.8]), -10.0, -12.0
        )
        bisector = np.rad2deg(np.arctan2(right[0], up[0]))
        assert bisector == pytest.approx(0.5 * (PROBE_1X_DEG + PROBE_1Y_DEG))

    def test_motion_along_one_probe_axis_only_moves_that_probe(self):
        # A pure 1 mil move straight at the 1X probe must land 1 mil away in
        # the direction the 1X probe points.
        right, up = centreline_positions(np.array([-9.8]), np.array([-12.0]), -10.0, -12.0)
        assert np.hypot(right[0], up[0]) == pytest.approx(1.0)
        assert np.rad2deg(np.arctan2(right[0], up[0])) == pytest.approx(PROBE_1X_DEG)

    def test_clearance_circle_sits_on_the_resting_position(self):
        x, y = clearance_circle(12.0)
        assert np.hypot(x, y - 6.0) == pytest.approx(np.full(x.shape, 6.0))
        assert np.min(y) == pytest.approx(0.0)

    def test_real_shaft_lifts_and_leans_against_rotation(self, run):
        frame = run.frame
        gap_x = frame["gap_1x"].to_numpy(float)
        gap_y = frame["gap_1y"].to_numpy(float)
        right, up = centreline_positions(
            gap_x, gap_y, rest_reference(gap_x, run.rpm), rest_reference(gap_y, run.rpm)
        )
        at_speed = run.rpm > 3500
        # A fluid-film bearing lifts its journal as the oil wedge forms, and
        # the attitude angle leans it the way the shaft turns (CCW = left).
        assert np.nanmean(up[at_speed]) > 5.0
        assert np.nanmean(right[at_speed]) < 0.0


class TestUploads:
    def test_bytes_and_path_loaders_agree(self):
        from rotordyn.adre import load_channel

        for path in _tabular_csvs():
            try:
                from_disk = load_channel(path)
            except ValueError:
                continue
            from_bytes = load_channel_bytes(path.read_bytes(), path.name)
            assert from_disk.name
            assert from_bytes.n_samples == from_disk.n_samples
            return
        pytest.skip("no single-channel ADRE export in the repo root")

    def test_parse_upload_rebuilds_the_run(self, run):
        payloads, names = [], []
        for path in _tabular_csvs():
            encoded = base64.b64encode(path.read_bytes()).decode()
            payloads.append(f"data:text/csv;base64,{encoded}")
            names.append(path.name)
        if not payloads:
            pytest.skip("no tabular ADRE exports in the repo root")
        uploaded = parse_upload(payloads, names)
        assert len(uploaded.frame) == len(run.frame)
        assert uploaded.probe_1x_name == run.probe_1x_name
        assert uploaded.source_files == run.source_files

    def test_unreadable_upload_reports_rather_than_crashing(self):
        junk = base64.b64encode(b"not an ADRE export").decode()
        with pytest.raises(ValueError, match="No readable ADRE exports"):
            parse_upload([f"data:text/csv;base64,{junk}"], ["junk.csv"])

    def test_build_run_from_channels_matches_build_run(self, run):
        rebuilt = build_run_from_channels(load_directory(DATA_DIR))
        assert len(rebuilt.frame) == len(run.frame)


class TestFigures:
    @pytest.mark.parametrize("tab", ["polar", "bode", "trend", "centreline"])
    def test_every_tab_produces_a_figure_with_data(self, run, tab):
        figure, result = build_figure(
            run, tab, ViewSettings(), list(TREND_CHANNELS), 12.0
        )
        assert figure.data
        assert result.n_points > 0

    def test_rotation_reaches_the_polar_subplot_titles(self, run):
        figure, _ = build_figure(run, "polar", ViewSettings(rotation_deg=90.0))
        titles = " ".join(a.text for a in figure.layout.annotations if a.text)
        assert "126" in titles
        assert "36" in titles

    def test_machine_view_rotates_the_angular_axis_to_the_probe(self, run):
        default, _ = build_figure(run, "polar", ViewSettings(machine_view=False))
        machine, _ = build_figure(run, "polar", ViewSettings(machine_view=True))
        assert default.layout.polar.angularaxis.rotation == pytest.approx(90.0)
        assert machine.layout.polar.angularaxis.rotation == pytest.approx(90.0 - PROBE_1X_DEG)
        assert machine.layout.polar2.angularaxis.rotation == pytest.approx(90.0 - PROBE_1Y_DEG)

    def test_phase_axis_on_the_bode_plot_runs_downwards(self, run):
        figure, _ = build_figure(run, "bode", ViewSettings())
        assert tuple(figure.layout.yaxis2.range) == (360, 0)

    def test_trend_adds_one_row_per_selected_channel(self, run):
        one, _ = build_figure(run, "trend", ViewSettings(), ["1X amplitude"])
        two, _ = build_figure(run, "trend", ViewSettings(), ["1X amplitude", "Gap voltage"])
        assert len(two.data) > len(one.data)
        assert two.layout.height > one.layout.height

    def test_clearance_circle_only_drawn_when_asked(self, run):
        without, _ = build_figure(run, "centreline", ViewSettings())
        with_circle, _ = build_figure(run, "centreline", ViewSettings(), None, 12.0)
        assert len(with_circle.data) == len(without.data) + 1

    def test_an_impossible_filter_gives_a_placeholder_not_a_crash(self, run):
        figure, result = build_figure(
            run, "polar", ViewSettings(rpm_min=9000.0, rpm_max=9999.0)
        )
        assert result.n_points == 0
        assert not figure.data

    def test_empty_figure_carries_its_message(self):
        assert empty_figure("nothing here").layout.annotations[0].text == "nothing here"


class TestApplication:
    def test_index_page_serves(self, client):
        page = client.get("/")
        assert page.status_code == 200
        assert b"react-entry-point" in page.data

    def test_layout_contains_every_control(self, client):
        layout = client.get("/_dash-layout").get_data(as_text=True)
        for component in (
            "upload", "btn-sample", "session", "segment", "rotation",
            "rpm-range", "options", "trend-channels", "clearance", "tabs", "graph",
        ):
            assert f'"{component}"' in layout
        assert "MDS Report" in layout
        assert "Rotor Buddy" in layout

    def test_callbacks_are_registered(self, client):
        import json

        dependencies = json.loads(client.get("/_dash-dependencies").get_data(as_text=True))
        outputs = {d["output"] for d in dependencies}
        assert any("graph.figure" in o for o in outputs)
        assert any("session.data" in o for o in outputs)


class TestSharedCore:
    def test_the_web_and_desktop_paths_compute_identical_numbers(self, run):
        """The migration is only cheap while both front ends share the model."""
        settings = ViewSettings(rotation_deg=137.0, slow_roll=True)
        _, from_web = build_figure(run, "polar", settings)
        from_model = PolarModel(run).compute(settings)
        assert from_web.amp_1x == pytest.approx(from_model.amp_1x, nan_ok=True)
        assert from_web.theta_1x == pytest.approx(from_model.theta_1x)

    def test_result_carries_the_filtered_samples(self, run):
        result = PolarModel(run).compute(ViewSettings(rpm_min=1000.0, rpm_max=2000.0))
        assert result.frame is not None
        assert len(result.frame) == result.n_points
        assert result.frame["rpm"].between(1000.0, 2000.0).all()
