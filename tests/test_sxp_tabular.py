"""Loader for ADRE Sxp multi-channel Tabular List CSV exports."""

from __future__ import annotations

from pathlib import Path

import pytest

from rotordyn.adre import load_export
from rotordyn.dataset import build_run

DATA = Path(__file__).resolve().parents[1] / "new_1.csv"


@pytest.mark.skipif(not DATA.is_file(), reason="new_1.csv not present")
class TestSxpTabular:
    def test_expands_to_many_channels_and_a_speed_keyphasor(self):
        channels = load_export(DATA)
        names = {c.name for c in channels}
        assert "BRG1X" in names and "BRG1Y" in names
        assert any(c.is_keyphasor for c in channels)
        brg1x = next(c for c in channels if c.name == "BRG1X")
        assert brg1x.n_samples == 7264

    def test_build_run_uses_bearing_1_and_full_speed_range(self, tmp_path):
        target = tmp_path / "export"
        target.mkdir()
        (target / "new_1.csv").write_bytes(DATA.read_bytes())
        run = build_run(target)
        assert run.probe_1x_name == "BRG1X"
        assert run.probe_1y_name == "BRG1Y"
        assert len(run.frame) == 7264
        assert float(run.rpm.max()) == pytest.approx(3614.0)
