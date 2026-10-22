"""MDS Engineer agent: diagnosis, recommendations and report packaging."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from rotordyn.buddy import BuddyReport, Finding, analyse_bearing
from rotordyn.mds import diagnose
from rotordyn.vectors import PROBE_1X_DEG, PROBE_1Y_DEG, to_amp_phase


def _finding(check_id, title, severity, summary, details=None, metrics=None):
    return Finding(
        check_id=check_id,
        title=title,
        severity=severity,
        summary=summary,
        details=details or [],
        metrics=metrics or {},
    )


def _report(findings, **kwargs):
    defaults = dict(
        source="unit.csv",
        n_samples=40,
        rpm_min=300.0,
        rpm_max=3600.0,
        bearings=["BRG1", "BRG2"],
        amp_unit="mil",
        findings=findings,
        notes=[],
    )
    defaults.update(kwargs)
    return BuddyReport(**defaults)


def _ok_quality(name="BRG1"):
    return [
        _finding("1", f"{name}: probe orientation", "ok", "Forward precession."),
        _finding("2", f"{name}: probe noise", "ok", "Timebases look clean."),
        _finding("3", f"{name}: proximity scale", "ok", "X and Y agree at slow roll."),
        _finding("4", f"{name}: same-bearing pairing", "ok", "Phase ~90°."),
        _finding("8", f"{name}: FSNL vs ISO 20816-2", "ok", "FSNL Direct is ISO zone A.", metrics={"zone": "A", "amp": 1.1}),
    ]


class TestDiagnosis:
    def test_all_clear_is_satisfactory(self):
        report = diagnose(_report(_ok_quality() + _ok_quality("BRG2")))
        assert report.condition == "satisfactory"
        assert report.quality_blocks_amplitudes is False
        assert "EXECUTIVE SUMMARY" in report.format_text()
        assert not any(h.malfunction_id == "fluid_instability" for h in report.hypotheses)

    def test_swapped_probes_unqualify_the_machine(self):
        findings = _ok_quality()
        findings[0] = _finding(
            "1",
            "BRG1: swapped probes",
            "alarm",
            "Reverse precession at slow roll AND at speed — X/Y probes are likely swapped.",
            metrics={"bearing": "BRG1"},
        )
        report = diagnose(_report(findings, bearings=["BRG1"]))
        assert report.condition == "unqualified"
        assert report.quality_blocks_amplitudes is True
        assert report.hypotheses[0].malfunction_id == "data_quality"
        assert report.hypotheses[0].confidence == "high"
        assert any(r.priority == 1 and "wiring" in r.action.lower() for r in report.recommendations)

    def test_whirl_alarm_is_primary_fluid_instability(self):
        findings = _ok_quality("BRG3") + [
            _finding(
                "15",
                "BRG3: waterfall / fluid instability",
                "alarm",
                "Persistent subsynchronous component at speed — possible oil whirl/whip.",
                details=["At speed, median 0.30–0.48X / 1X = 0.42"],
            )
        ]
        report = diagnose(_report(findings, bearings=["BRG3"]))
        assert report.condition == "restricted"
        assert report.hypotheses[0].malfunction_id == "fluid_instability"
        primary = report.hypotheses[0]
        assert primary.confidence == "high"
        assert "BRG3" in primary.bearings
        assert any("waterfall" in r.action.lower() for r in report.recommendations)
        assert any("balance" in r.action.lower() for r in report.recommendations)

    def test_whirl_plus_iso_c_is_unsatisfactory(self):
        findings = _ok_quality("BRG3")
        findings[-1] = _finding(
            "8",
            "BRG3: FSNL vs ISO 20816-2",
            "watch",
            "FSNL Direct is ISO zone C — vibration is excessive relative to ISO 20816-2.",
            metrics={"zone": "C", "amp": 7.0},
        )
        findings.append(
            _finding(
                "15",
                "BRG3: waterfall / fluid instability",
                "alarm",
                "Persistent subsynchronous component at speed — possible oil whirl/whip.",
            )
        )
        report = diagnose(_report(findings, bearings=["BRG3"]))
        assert report.condition == "unsatisfactory"
        assert report.hypotheses[0].malfunction_id == "fluid_instability"

    def test_half_x_watch_is_rub_not_whirl(self):
        findings = _ok_quality() + [
            _finding(
                "15",
                "BRG1: waterfall / sub- and supersync",
                "watch",
                "Elevated 0.5X at speed — check rub/looseness on the waterfall.",
            )
        ]
        report = diagnose(_report(findings, bearings=["BRG1"]))
        ids = {h.malfunction_id for h in report.hypotheses}
        assert "rub" in ids
        assert "fluid_instability" not in ids

    def test_circular_slow_roll_and_up_down_split_is_bow(self):
        findings = _ok_quality("BRG2") + [
            _finding(
                "16",
                "BRG2: slow-roll orbit / bowed rotor",
                "watch",
                "Slow-roll orbit is circular with a real 1X component — consistent with rotor bow.",
            ),
            _finding(
                "11",
                "BRG2: startup vs shutdown",
                "watch",
                "Critical speed or amplitude changed between startup and shutdown — inspect for rub or bow.",
            ),
        ]
        report = diagnose(_report(findings, bearings=["BRG2"]))
        bow = next(h for h in report.hypotheses if h.malfunction_id == "bowed_rotor")
        assert bow.confidence == "high"
        assert report.condition == "restricted"
        assert any("do not balance" in r.action.lower() for r in report.recommendations)

    def test_iso_d_is_unsatisfactory_even_without_a_named_malfunction(self):
        findings = _ok_quality("BRG1")
        findings[-1] = _finding(
            "8",
            "BRG1: FSNL vs ISO 20816-2",
            "alarm",
            "FSNL Direct is ISO zone D — vibration is excessive relative to ISO 20816-2.",
            metrics={"zone": "D", "amp": 10.0},
        )
        report = diagnose(_report(findings, bearings=["BRG1"]))
        assert report.condition == "unsatisfactory"
        assert any(h.malfunction_id == "high_relative_vibration" for h in report.hypotheses)
        assert any("zone d" in r.action.lower() for r in report.recommendations)

    def test_slow_roll_only_record_withholds_at_speed_calls(self):
        findings = _ok_quality() + [
            _finding("13", "BRG1: preload / misalignment", "watch", "Flattened 1X orbit and/or high 2X."),
            _finding("16", "BRG1: slow-roll orbit / bowed rotor", "watch", "Slow-roll orbit is circular."),
        ]
        report = diagnose(_report(findings, bearings=["BRG1"], rpm_min=61.0, rpm_max=530.0))
        ids = {h.malfunction_id for h in report.hypotheses}
        assert report.condition == "unqualified"
        assert "preload_misalignment" not in ids
        assert "bowed_rotor" in ids
        assert "530 rpm" in report.headline
        assert report.recommendations[0].action.startswith("Re-export the full run")

    def test_startup_dump_is_labelled_startup_assist(self):
        report = diagnose(_report(_ok_quality(), rpm_min=250.0, rpm_max=3610.0))
        assert report.job_type == "Startup / shutdown assistance"

    def test_pdf_and_text_are_written(self, tmp_path: Path):
        from rotordyn.mds_pdf import write_pdf

        report = diagnose(_report(_ok_quality("BRG1") + [
            _finding(
                "15",
                "BRG1: waterfall / fluid instability",
                "alarm",
                "Persistent subsynchronous component at speed — possible oil whirl/whip.",
            )
        ], bearings=["BRG1"]))
        text = tmp_path / "mds_report.txt"
        text.write_text(report.format_text(), encoding="utf-8")
        pdf = tmp_path / "mds_report.pdf"
        write_pdf(report, pdf)
        assert "MACHINERY DIAGNOSTICS SERVICE" in text.read_text(encoding="utf-8")
        assert pdf.is_file()
        assert pdf.read_bytes()[:4] == b"%PDF"


def circular_orbit(radius: float, high_spot_deg: float):
    z_1x = radius * np.exp(-1j * np.deg2rad(high_spot_deg - PROBE_1X_DEG))
    z_1y = radius * np.exp(-1j * np.deg2rad(high_spot_deg - PROBE_1Y_DEG))
    return z_1x, z_1y


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


def _write_timebase(path: Path, variable: str) -> None:
    spr, revs = 32, 8
    t = np.arange(revs * spr) / spr
    x = 0.3 * np.cos(2 * np.pi * t - np.deg2rad(20.0))
    y = 0.3 * np.cos(2 * np.pi * t - np.deg2rad(110.0))
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
            f"45|{direction}|{variable}|None|{rpm} rpm|0-16Orders|{spr}/{revs} |"
            f"Direct|None|None|mil|"
        )
        body = "Sample Number|Amplitude|\n" + "\n".join(f"{i + 1}|{v}" for i, v in enumerate(wave))
        chunks.append(f"\n{header}\n{meta}\n\n{body}\n")
    path.write_text("".join(chunks), encoding="utf-8")


class TestCli:
    def test_mds_flag_writes_buddy_and_mds(self, tmp_path: Path):
        import main as app

        sync = tmp_path / "sync.csv"
        _write_timebase(sync, "Sync Waveform")
        out = tmp_path / "outbox"
        rc = app.main(["--mds", str(sync), "--outbox", str(out)])
        assert rc == 0
        assert (out / "rotor_buddy.txt").is_file()
        assert (out / "mds_report.txt").is_file()
        body = (out / "mds_report.txt").read_text(encoding="utf-8")
        assert "MACHINERY DIAGNOSTICS SERVICE" in body
        assert (out / "mds_report.pdf").read_bytes()[:4] == b"%PDF"


class TestBuddyToMds:
    def test_reverse_precession_from_buddy_becomes_data_quality(self):
        n = 40
        rpm = np.concatenate([np.linspace(300, 600, 10), np.full(20, 3600), np.linspace(3600, 300, 10)])
        zx, zy = circular_orbit(0.4, 40.0)
        fx, fy = _pair_frames(np.full(n, np.conj(zx)), np.full(n, np.conj(zy)), rpm)
        findings = analyse_bearing("BRG1", fx, fy)
        report = diagnose(
            BuddyReport(
                source="synthetic",
                n_samples=n,
                rpm_min=float(rpm.min()),
                rpm_max=float(rpm.max()),
                bearings=["BRG1"],
                amp_unit="mil",
                findings=findings,
            )
        )
        assert report.quality_blocks_amplitudes or any(
            h.malfunction_id == "data_quality" for h in report.hypotheses
        )
