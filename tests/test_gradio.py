"""Tests for the Gradio front end used on Hugging Face Spaces."""

from __future__ import annotations

from pathlib import Path

import pytest

gr = pytest.importorskip("gradio", reason="gradio is only needed for the Spaces build")

import app as gradio_app  # noqa: E402
from rotordyn.dataset import build_run  # noqa: E402
from rotordyn.model import PolarModel, ViewSettings  # noqa: E402

DATA_DIR = Path(__file__).resolve().parents[1]
ALL_OPTIONS = list(gradio_app.OPTION_LABELS.values())


@pytest.fixture(scope="module")
def loaded():
    return gradio_app.load_run(None)


class TestLoading:
    def test_sample_load_reports_what_it_found(self, loaded):
        _, banner, *_ = loaded
        assert "1051 samples" in banner
        assert "CT2B_BRG1X.csv" in banner

    def test_sample_load_configures_the_segment_choices(self, loaded):
        _, _, segment, *_ = loaded
        assert "Coast-up (startup)" in segment["choices"]
        assert segment["value"] == "Coast-up (startup)"

    def test_sample_load_sets_the_speed_sliders_to_the_data_range(self, loaded):
        _, _, _, low, high, _ = loaded
        assert low["minimum"] == pytest.approx(high["minimum"])
        assert low["value"] == pytest.approx(low["minimum"])
        assert high["value"] == pytest.approx(high["maximum"])
        assert high["maximum"] > 3600

    def test_uploading_the_real_files_matches_loading_them_from_disk(self):
        paths = [str(p) for p in sorted(DATA_DIR.glob("*.csv"))]
        run, banner, *_ = gradio_app.load_run(paths)
        assert len(run.frame) == len(build_run(DATA_DIR).frame)
        assert "1051 samples" in banner

    def test_unreadable_upload_raises_a_message_the_user_can_read(self, tmp_path):
        junk = tmp_path / "junk.csv"
        junk.write_text("not an ADRE export", encoding="utf-8")
        with pytest.raises(gr.Error, match="No readable ADRE exports"):
            gradio_app.load_run([str(junk)])


class TestRendering:
    def test_every_tab_renders(self, loaded):
        run = loaded[0]
        polar, bode, trend, centreline, status = gradio_app.render(
            run, "Coast-up (startup)", 0, 0, 3614, ALL_OPTIONS,
            300, 700, ["1X amplitude"], 12,
        )
        for figure in (polar, bode, trend, centreline):
            assert figure.data
        assert "points plotted" in status

    def test_nothing_loaded_gives_placeholders_not_an_exception(self):
        figures = gradio_app.render(None, "", 0, 0, 1, [], 300, 700, [], None)
        assert all(not f.data for f in figures[:4])
        assert figures[4] == ""

    def test_rotation_moves_the_reported_probe_positions(self, loaded):
        run = loaded[0]
        *_, status = gradio_app.render(
            run, "Coast-up (startup)", 90, 0, 3614, ALL_OPTIONS, 300, 700, [], None
        )
        assert "126\u00b0 right of TDC" in status
        assert "36\u00b0 right of TDC" in status

    def test_slow_roll_option_reports_what_it_removed(self, loaded):
        run = loaded[0]
        *_, status = gradio_app.render(
            run, "Coast-up (startup)", 0, 0, 3614, ALL_OPTIONS, 300, 700, [], None
        )
        assert "slow-roll removed" in status

    def test_checkbox_labels_map_onto_real_settings(self, loaded):
        """The labels are user-facing text; this catches a rename breaking them."""
        run = loaded[0]
        *_, status = gradio_app.render(
            run, "Coast-up (startup)", 0, 0, 3614, [], 300, 700, [], None
        )
        assert "flagged samples hidden" not in status


class TestSharedCore:
    def test_gradio_and_the_model_agree(self, loaded):
        """The Spaces build must not drift from the desktop and Dash builds."""
        run = loaded[0]
        settings = ViewSettings(segment="Coast-up (startup)", rotation_deg=45.0)
        expected = PolarModel(run).compute(settings)
        polar, *_ = gradio_app.render(
            run, "Coast-up (startup)", 45, 0, 3614,
            [gradio_app.OPTION_LABELS["hide"], gradio_app.OPTION_LABELS["labels"]],
            300, 700, [], None,
        )
        assert f"{expected.theta_1x:.0f}" in " ".join(
            a.text for a in polar.layout.annotations if a.text
        )


class TestInterface:
    def test_the_blocks_interface_builds(self):
        assert gradio_app.build_interface() is not None

    def test_app_py_does_not_import_dash(self):
        """A Space installs one requirements file; the Gradio path must not need Dash."""
        import ast

        tree = ast.parse((DATA_DIR / "app.py").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        assert not any(name.startswith("dash") for name in imported), imported
        assert "rotordyn.webapp" not in imported
