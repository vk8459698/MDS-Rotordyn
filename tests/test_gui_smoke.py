"""Smoke test: the window builds, refreshes and reacts to the angle control.

Skipped automatically when no display / Tk is available (CI, SSH sessions).
"""

import numpy as np
import pytest

tk = pytest.importorskip("tkinter")


@pytest.fixture(scope="session")
def _window(run):
    """One Tk root for the whole session; repeated roots are unreliable."""
    from rotordyn.gui import PolarApp

    try:
        window = PolarApp(run)
    except tk.TclError:  # pragma: no cover - headless machine
        pytest.skip("no display available")
    window.withdraw()
    yield window
    window.destroy()


@pytest.fixture
def app(_window):
    _window.reset()
    return _window


def test_window_builds_and_reports_probe_positions(app):
    assert "1X probe" in app.var_status.get()
    assert "36\u00b0 right of TDC" in app.var_status.get()


def test_setting_the_angle_updates_both_plots(app):
    before = app.plot_1x._points.get_offsets().copy()
    app.set_rotation(90.0)
    after = app.plot_1x._points.get_offsets()
    assert not np.allclose(before, after)
    assert "126\u00b0 right of TDC" in app.var_status.get()
    assert "36\u00b0 right of TDC" in app.var_status.get()


def test_typed_angle_is_accepted(app):
    app.var_rotation_text.set("210")
    app._on_entry(None)
    assert app.var_rotation.get() == pytest.approx(210.0)


def test_invalid_typed_angle_is_ignored(app):
    app.set_rotation(30.0)
    app.var_rotation_text.set("not an angle")
    app._on_entry(None)
    assert app.var_rotation.get() == pytest.approx(30.0)


def test_angle_is_clamped_to_the_supported_range(app):
    app.set_rotation(999.0)
    assert app.var_rotation.get() == pytest.approx(360.0)
    app.set_rotation(-50.0)
    assert app.var_rotation.get() == pytest.approx(0.0)


def test_reset_restores_defaults(app):
    app.set_rotation(180.0)
    app.var_slow_roll.set(True)
    app.reset()
    assert app.var_rotation.get() == pytest.approx(0.0)
    assert app.var_slow_roll.get() is False


def test_every_segment_renders(app):
    for segment in app.model.segment_names():
        app.var_segment.set(segment)
        app.refresh()
        assert app.var_status.get()


def test_machine_view_rotates_the_axes(app):
    app.var_machine_view.set(True)
    app.refresh()
    assert app.plot_1x.ax.get_theta_offset() != pytest.approx(np.pi / 2)
