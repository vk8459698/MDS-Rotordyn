"""Rotor Buddy harmonics extraction and diagnostic checks."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from rotordyn.buddy import analyse_bearing, analyse_path, iso_zone
from rotordyn.vectors import PROBE_1X_DEG, PROBE_1Y_DEG, to_amp_phase
from rotordyn.waveforms import (
    extract_harmonics,
    is_timebase_export,
    summarise_timebase,
    waveform_metrics,
)


def circular_orbit(radius: float, high_spot_deg: float):
    z_1x = radius * np.exp(-1j * np.deg2rad(high_spot_deg - PROBE_1X_DEG))
    z_1y = radius * np.exp(-1j * np.deg2rad(high_spot_deg - PROBE_1Y_DEG))
    return z_1x, z_1y


def _cosine(amp_pp: float, phase_lag_deg: float, order: float, n_revs: int = 8, spr: int = 32) -> np.ndarray:
    t = np.arange(n_revs * spr) / spr
    return (amp_pp / 2.0) * np.cos(2 * np.pi * order * t - np.deg2rad(phase_lag_deg))


def _pair_frames(zx, zy, rpm):
    ax, px = to_amp_phase(np.asarray(zx, dtype=complex))
    ay, py = to_amp_phase(np.asarray(zy, dtype=complex))
    n = ax.size
    fx = pd.DataFrame(
        {
            "rpm": rpm,
            "amp_1x": ax,
            "phase_1x": px,
            "direct": ax,
            "amp_2x": np.full(n, 0.02),
            "amp_05x": np.full(n, 0.01),
            "amp_033x": np.full(n, 0.005),
            "amp_047x": np.full(n, 0.005),
            "subsync_max": np.full(n, 0.01),
            "supersync_max": np.full(n, 0.01),
            "n_spikes": np.zeros(n),
            "crest": np.full(n, 1.5),
        }
    )
    fy = fx.copy()
    fy["amp_1x"] = ay
    fy["phase_1x"] = py
    fy["direct"] = ay
    return fx, fy


class TestHarmonics:
    def test_recovers_1x_amp_and_phase(self):
        wave = _cosine(1.2, 40.0, 1.0)
        harm = extract_harmonics(wave, n_revs=8)
        assert harm["amp_1x"] == pytest.approx(1.2, rel=1e-3)
        assert harm["phase_1x"] == pytest.approx(40.0, abs=0.5)

    def test_recovers_2x(self):
        wave = _cosine(0.4, 10.0, 1.0) + _cosine(0.8, 80.0, 2.0)
        harm = extract_harmonics(wave, n_revs=8)
        assert harm["amp_2x"] == pytest.approx(0.8, rel=1e-3)
        assert harm["phase_2x"] == pytest.approx(80.0, abs=0.5)

    def test_spike_count(self):
        x = np.zeros(256)
        x[10] = 5.0
        x[20] = -5.0
        metrics = waveform_metrics(x)
        assert metrics["n_spikes"] >= 2
        assert metrics["direct"] == pytest.approx(10.0)


class TestTimebaseFile:
    def test_sniff_and_summarise(self, tmp_path: Path):
        spr, revs = 32, 8
        x = _cosine(0.6, 20.0, 1.0, revs, spr)
        y = _cosine(0.6, 110.0, 1.0, revs, spr)
        path = tmp_path / "dump.csv"
        chunks = []
        for sample, rpm, ch, wave, direction in (
            (1, 400, "BRG1X", x, "Right"),
            (1, 400, "BRG1Y", y, "Left"),
            (2, 3600, "BRG1X", x * 2, "Right"),
            (2, 3600, "BRG1Y", y * 2, "Left"),
        ):
            header = (
                "Plot Name|Waveform Sample Number|Waveform Timestamp|Channel Name|"
                "Machine Name|Probe Angle|Probe Direction|Variable|Plot Status|Speed|"
                "Frequency Span|Sampling Rate/Revs|Variable Type|Slow Roll Amplitude|"
                "Slow Roll Phase|Y axis Units|"
            )
            meta = (
                f"New Timebase Plot|{sample}|01 Jan 2026 00:00:00.000|{ch}|Turbine|"
                f"45|{direction}|Sync Waveform|None|{rpm} rpm|0-16Orders|{spr}/{revs} |"
                f"Direct|None|None|mil|"
            )
            body = "Sample Number|Amplitude|\n" + "\n".join(
                f"{i + 1}|{v}" for i, v in enumerate(wave)
            )
            chunks.append(f"\n{header}\n{meta}\n\n{body}\n")
        path.write_text("".join(chunks), encoding="utf-8")
        assert is_timebase_export(path)
        frame, _raw = summarise_timebase(path, keep_raw=True)
        assert set(frame["channel"]) == {"BRG1X", "BRG1Y"}
        assert len(frame) == 4
        report = analyse_path(path)
        assert "BRG1" in report.bearings
        assert any(f.check_id == "1" for f in report.findings)
        from rotordyn.buddy_pdf import write_pdf

        pdf = tmp_path / "rotor_buddy.pdf"
        write_pdf(report, pdf)
        assert pdf.is_file()
        assert pdf.read_bytes()[:4] == b"%PDF"


class TestChecks:
    def test_iso_zones_match_table_b1(self):
        assert iso_zone(2.0, 3600) == "A"  # 2 mil < 80 µm
        assert iso_zone(10.0, 3600) == "D"  # 10 mil > 220 µm

    def test_swapped_probes_alarm_on_reverse_precession(self):
        n = 40
        rpm = np.concatenate([np.linspace(300, 600, 10), np.full(20, 3600), np.linspace(3600, 300, 10)])
        zx, zy = circular_orbit(0.4, 40.0)
        fx, fy = _pair_frames(np.full(n, np.conj(zx)), np.full(n, np.conj(zy)), rpm)
        findings = {f.check_id: f for f in analyse_bearing("BRG1", fx, fy)}
        assert findings["1"].severity in {"alarm", "watch"}

    def test_scale_mismatch_at_slow_roll(self):
        n = 30
        rpm = np.concatenate([np.full(10, 400.0), np.full(20, 3600.0)])
        zx, zy = circular_orbit(0.3, 10.0)
        fx, fy = _pair_frames(np.full(n, zx), np.full(n, zy), rpm)
        fx = fx.copy()
        fx.loc[fx["rpm"] < 700, "amp_1x"] = 0.8
        fx.loc[fx["rpm"] < 700, "direct"] = 0.8
        fy = fy.copy()
        fy.loc[fy["rpm"] < 700, "amp_1x"] = 0.35
        fy.loc[fy["rpm"] < 700, "direct"] = 0.35
        findings = {f.check_id: f for f in analyse_bearing("BRG1", fx, fy)}
        assert findings["3"].severity == "alarm"

    def test_forward_pair_is_ok(self):
        n = 40
        rpm = np.concatenate([np.linspace(300, 600, 10), np.full(20, 3600), np.linspace(3600, 300, 10)])
        zx, zy = circular_orbit(0.4, 40.0)
        fx, fy = _pair_frames(np.full(n, zx), np.full(n, zy), rpm)
        findings = analyse_bearing("BRG1", fx, fy)
        swapped = next(f for f in findings if f.check_id == "1")
        assert swapped.severity == "ok"
        same = next(f for f in findings if f.check_id == "4")
        assert same.severity in {"ok", "watch"}


def _write_timebase(path: Path, variable: str) -> None:
    spr, revs = 32, 8
    x = _cosine(0.6, 20.0, 1.0, revs, spr)
    y = _cosine(0.6, 110.0, 1.0, revs, spr)
    chunks = []
    for sample, rpm, ch, wave, direction in (
        (1, 400, "BRG1X", x, "Right"),
        (1, 400, "BRG1Y", y, "Left"),
        (2, 3600, "BRG1X", x * 2, "Right"),
        (2, 3600, "BRG1Y", y * 2, "Left"),
    ):
        header = (
            "Plot Name|Waveform Sample Number|Waveform Timestamp|Channel Name|"
            "Machine Name|Probe Angle|Probe Direction|Variable|Plot Status|Speed|"
            "Frequency Span|Sampling Rate/Revs|Variable Type|Slow Roll Amplitude|"
            "Slow Roll Phase|Y axis Units|"
        )
        span = "0-250Hertz" if "Async" in variable else "0-16Orders"
        meta = (
            f"New Timebase Plot|{sample}|01 Jan 2026 00:00:00.000|{ch}|Turbine|"
            f"45|{direction}|{variable}|None|{rpm} rpm|{span}|{spr}/{revs} |"
            f"Direct|None|None|mil|"
        )
        body = "Sample Number|Amplitude|\n" + "\n".join(f"{i + 1}|{v}" for i, v in enumerate(wave))
        chunks.append(f"\n{header}\n{meta}\n\n{body}\n")
    path.write_text("".join(chunks), encoding="utf-8")


def _write_tabular(path: Path) -> None:
    # Rest gap more negative; at speed both gaps rise (shaft toward probes) → CCW lift.
    rows = [
        "CH#|Channel Name|Machine Name|Status|Angle|Direction|Speed Units(P)|Amp Unit|Phase Unit|",
        "1|BRG1X|Turbine|OK|45°|Right|rpm|mil pp|deg|",
        "2|BRG1Y|Turbine|OK|45°|Left|rpm|mil pp|deg|",
        "",
        "CH#|Channel Name|Sample#|Sample Cause|Date|Speed(P)|Direct|Avg Gap|1XAmplitude|1X Phase|2XAmplitude|2X Phase|",
        "1|BRG1X|1|DR-T|01Jan2026 00:00:00.000|80|0.5|-10.0|0.3|20|0.05|10|",
        "2|BRG1Y|1|DR-T|01Jan2026 00:00:00.000|80|0.5|-10.0|0.3|110|0.05|100|",
        "",
        "CH#|Channel Name|Machine Name|Status|Angle|Direction|Speed Units(P)|Amp Unit|Phase Unit|",
        "1|BRG1X|Turbine|OK|45°|Right|rpm|mil pp|deg|",
        "2|BRG1Y|Turbine|OK|45°|Left|rpm|mil pp|deg|",
        "",
        "CH#|Channel Name|Sample#|Sample Cause|Date|Speed(P)|Direct|Avg Gap|1XAmplitude|1X Phase|2XAmplitude|2X Phase|",
        "1|BRG1X|2|DR-T|01Jan2026 00:00:01.000|3600|1.0|-9.4|0.8|40|0.1|20|",
        "2|BRG1Y|2|DR-T|01Jan2026 00:00:01.000|3600|1.0|-9.4|0.8|130|0.1|110|",
    ]
    path.write_text("\n".join(rows) + "\n", encoding="utf-16")


class TestCombinedSources:
    def test_peek_sync_vs_async(self, tmp_path: Path):
        from rotordyn.waveforms import peek_timebase_kind
        from rotordyn.buddy import classify_buddy_input

        sync = tmp_path / "sync.csv"
        async_ = tmp_path / "async.csv"
        _write_timebase(sync, "Sync Waveform")
        _write_timebase(async_, "Async Waveform")
        assert peek_timebase_kind(sync) == "sync"
        assert peek_timebase_kind(async_) == "async"
        assert classify_buddy_input(sync) == "sync"
        assert classify_buddy_input(async_) == "async"

    def test_three_files_score_centreline_and_async_noise(self, tmp_path: Path):
        from rotordyn.buddy import analyse_paths

        sync = tmp_path / "sync.csv"
        async_ = tmp_path / "async.csv"
        static = tmp_path / "static.csv"
        _write_timebase(sync, "Sync Waveform")
        _write_timebase(async_, "Async Waveform")
        _write_tabular(static)
        report = analyse_paths([sync, async_, static])
        assert "sync" in report.source.lower() or "sync.csv" in report.source
        scl = next(f for f in report.findings if f.check_id == "14")
        assert scl.severity != "skip"
        noise = next(f for f in report.findings if f.check_id == "2")
        assert any("async" in d.lower() for d in noise.details)
