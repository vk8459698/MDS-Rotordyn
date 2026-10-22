"""Rotor Buddy: automated rotor-dynamics checks on an ADRE run.

The checks follow the scope written by Kiran Toram (29 Aug 2026, v001).
Each check returns a :class:`Finding` so the desktop, web and CLI front ends
can print the same report.  Waveform-only clues (noise spikes, slow-roll
orbit shape, waterfall orders) need a timebase dump; vector-only clues
(Bode criticals, ISO zones, gap centreline) also run on a tabular :class:`RunData`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .centreline import centreline_positions, rest_reference
from .dataset import RunData
from .vectors import precession_split, to_phasor
from .waveforms import (
    aligned_phasors,
    bearing_pairs,
    harmonics_cache_path,
    is_timebase_export,
    load_harmonics,
    peek_timebase_kind,
    save_harmonics,
    summarise_timebase,
)

# ISO 20816-2:2017 Table B.1 — shaft relative vibration, peak-to-peak, µm.
# Rows are (speed_rpm, machine_kind) → (A/B, B/C, C/D).
_ISO_20816_2_REL_UM = {
    ("steam", 1500): (100, 200, 320),
    ("steam", 1800): (95, 185, 290),
    ("steam", 3000): (90, 165, 240),
    ("steam", 3600): (80, 150, 220),
    ("gas", 3000): (90, 165, 240),
    ("gas", 3600): (80, 150, 220),
}
_UM_PER_MIL = 25.4

SLOW_ROLL = (200.0, 700.0)
RATED_FRACTION = 0.97
PHASE_ORTH_TOL_DEG = 35.0
SCALE_RATIO_ALARM = 1.6
CIRCULAR_ASPECT = 0.80


@dataclass
class Finding:
    check_id: str
    title: str
    severity: str  # ok | info | watch | alarm | skip
    summary: str
    details: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


@dataclass
class BuddyReport:
    source: str
    n_samples: int
    rpm_min: float
    rpm_max: float
    bearings: list[str]
    amp_unit: str
    findings: list[Finding]
    notes: list[str] = field(default_factory=list)
    frame: pd.DataFrame | None = field(default=None, repr=False, compare=False)

    def format_text(self) -> str:
        lines = [
            "ROTOR BUDDY  —  automated rotor-dynamics review",
            "Agent: rotor buddy    Scope: Kiran Toram, 29 Aug 2026, v001",
            f"Source: {self.source}",
            f"Record: {self.n_samples} aligned samples, "
            f"{self.rpm_min:.0f}–{self.rpm_max:.0f} rpm,  bearings {', '.join(self.bearings)}",
            "",
        ]
        if self.notes:
            lines += ["NOTES", *[f"  {n}" for n in self.notes], ""]
        order = {"alarm": 0, "watch": 1, "info": 2, "skip": 3, "ok": 4}
        ranked = sorted(self.findings, key=lambda f: (order.get(f.severity, 9), f.check_id))
        counts = {k: sum(1 for f in self.findings if f.severity == k) for k in order}
        lines.append(
            "SUMMARY  "
            f"{counts['alarm']} alarm  {counts['watch']} watch  "
            f"{counts['info']} info  {counts['ok']} ok  {counts['skip']} skipped"
        )
        lines.append("")
        for finding in ranked:
            tag = finding.severity.upper()
            lines.append(f"[{tag:<5}]  {finding.check_id}  {finding.title}")
            lines.append(f"         {finding.summary}")
            for detail in finding.details:
                lines.append(f"         - {detail}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"


def _wrap180(deg: np.ndarray | float) -> np.ndarray:
    return (np.asarray(deg, dtype=float) + 180.0) % 360.0 - 180.0


def _circ_median(deg: np.ndarray) -> float:
    ang = np.deg2rad(np.asarray(deg, dtype=float))
    ang = ang[np.isfinite(ang)]
    if ang.size == 0:
        return float("nan")
    return float(np.rad2deg(np.angle(np.mean(np.exp(1j * ang))))) % 360.0


def _iso_limits_mil(rpm: float, kind: str = "gas") -> tuple[float, float, float]:
    """A/B, B/C, C/D in mil pp for the nearest ISO 20816-2 Table B.1 row."""
    target = 3600 if rpm >= 3300 else 3000 if rpm >= 2400 else 1800 if rpm >= 1650 else 1500
    kind = "gas" if kind == "gas" and target in (3000, 3600) else "steam"
    ab, bc, cd = _ISO_20816_2_REL_UM[(kind, target)]
    return ab / _UM_PER_MIL, bc / _UM_PER_MIL, cd / _UM_PER_MIL


def iso_zone(amp_mil: float, rpm: float, kind: str = "gas") -> str:
    ab, bc, cd = _iso_limits_mil(rpm, kind)
    if not np.isfinite(amp_mil):
        return "?"
    if amp_mil <= ab:
        return "A"
    if amp_mil <= bc:
        return "B"
    if amp_mil <= cd:
        return "C"
    return "D"


def _segments(rpm: np.ndarray) -> dict[str, np.ndarray]:
    """Boolean masks for coast-up, rated-speed dwell and coast-down.

    Dwell is the samples that *are* at rated speed, not the index span between
    the first and last such sample.  A two-cycle dump would otherwise treat the
    shutdown and second startup as 'steady state'.
    """
    n = rpm.size
    full = np.ones(n, dtype=bool)
    finite = np.isfinite(rpm)
    peak = float(np.nanmax(rpm)) if n else 0.0
    slow = finite & (rpm >= SLOW_ROLL[0]) & (rpm <= SLOW_ROLL[1])
    if not np.isfinite(peak) or peak <= 0:
        return {"full": full, "up": full, "dwell": full, "down": full, "slow": slow}
    dwell = finite & (rpm >= RATED_FRACTION * peak)
    work = np.where(finite, rpm, peak)
    if n >= 5:
        work = np.convolve(work, np.ones(5) / 5.0, mode="same")
    delta = np.diff(work, prepend=work[0] if n else 0.0)
    up = finite & (delta > 3.0) & (rpm < RATED_FRACTION * peak)
    down = finite & (delta < -3.0) & (rpm < RATED_FRACTION * peak)
    if not up.any():
        first = int(np.argmax(dwell)) if dwell.any() else n
        up = finite & (np.arange(n) <= first)
    if not down.any():
        last = int(n - 1 - np.argmax(dwell[::-1])) if dwell.any() else 0
        down = finite & (np.arange(n) >= last)
    return {"full": full, "up": up, "dwell": dwell, "down": down, "slow": slow}


def _mask_runs(mask: np.ndarray) -> list[np.ndarray]:
    """Split a boolean mask into contiguous True islands."""
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return []
    cuts = np.where(np.diff(idx) > 1)[0] + 1
    islands: list[np.ndarray] = []
    for group in np.split(idx, cuts):
        island = np.zeros_like(mask)
        island[group] = True
        islands.append(island)
    return islands


def _precession_stats(zx: np.ndarray, zy: np.ndarray, mask: np.ndarray) -> dict[str, float]:
    use = mask & np.isfinite(zx) & np.isfinite(zy) & (np.abs(zx) > 0) & (np.abs(zy) > 0)
    if use.sum() < 3:
        return {}
    ccw, cw = precession_split(zx[use], zy[use])
    mag_f, mag_r = np.abs(ccw), np.abs(cw)
    major = mag_f + mag_r
    minor = np.abs(mag_f - mag_r)
    aspect = np.where(major > 1e-12, minor / major, np.nan)
    return {
        "n": float(use.sum()),
        "mean_ccw": float(np.mean(mag_f)),
        "mean_cw": float(np.mean(mag_r)),
        "frac_reverse": float(np.mean(mag_r > mag_f)),
        "median_aspect": float(np.nanmedian(aspect)),
    }


def _phase_lead(fx: pd.DataFrame, fy: pd.DataFrame, mask: np.ndarray) -> dict[str, float]:
    lead = (fy["phase_1x"].to_numpy(dtype=float) - fx["phase_1x"].to_numpy(dtype=float)) % 360.0
    amp_ok = (fx["amp_1x"].to_numpy(dtype=float) > 0.05) & (fy["amp_1x"].to_numpy(dtype=float) > 0.05)
    use = mask & amp_ok & np.isfinite(lead)
    if use.sum() < 3:
        return {}
    med = _circ_median(lead[use])
    err90 = np.abs(_wrap180(lead[use] - 90.0))
    err270 = np.abs(_wrap180(lead[use] - 270.0))
    nearest = np.minimum(err90, err270)
    return {
        "n": float(use.sum()),
        "median_lead": med,
        "median_err_from_90_or_270": float(np.median(nearest)),
        "frac_within_tol": float(np.mean(nearest <= PHASE_ORTH_TOL_DEG)),
    }


def _find_criticals(rpm: np.ndarray, amp: np.ndarray, phase: np.ndarray) -> list[dict]:
    """Amplitude peaks on a transient that also show a clear phase roll."""
    order = np.argsort(rpm)
    rpm_s, amp_s, phase_s = rpm[order], amp[order], phase[order]
    finite = np.isfinite(rpm_s) & np.isfinite(amp_s) & np.isfinite(phase_s)
    rpm_s, amp_s, phase_s = rpm_s[finite], amp_s[finite], phase_s[finite]
    if rpm_s.size < 12:
        return []
    # Speed-bin the transient so dwells do not dominate peak picking.
    bins = np.linspace(float(rpm_s.min()), float(rpm_s.max()), min(80, rpm_s.size // 2 + 2))
    centres, amps, phases = [], [], []
    for lo, hi in zip(bins[:-1], bins[1:]):
        sel = (rpm_s >= lo) & (rpm_s < hi)
        if sel.sum() < 1:
            continue
        centres.append(0.5 * (lo + hi))
        amps.append(float(np.nanmedian(amp_s[sel])))
        phases.append(_circ_median(phase_s[sel]))
    if len(centres) < 8:
        return []
    centres = np.asarray(centres)
    amps = np.asarray(amps)
    unwrapped = np.rad2deg(np.unwrap(np.deg2rad(np.asarray(phases))))
    peaks: list[dict] = []
    floor = max(0.05, 0.25 * float(np.nanmax(amps)))
    for i in range(2, len(amps) - 2):
        if not (amps[i] >= amps[i - 1] and amps[i] >= amps[i + 1] and amps[i] >= floor):
            continue
        lo, hi = max(0, i - 4), min(len(amps) - 1, i + 4)
        dphi = abs(unwrapped[hi] - unwrapped[lo])
        if dphi < 70.0:
            continue
        peaks.append(
            {
                "rpm": float(centres[i]),
                "amp": float(amps[i]),
                "phase_roll_deg": float(dphi),
            }
        )
    # Keep distinct peaks (at least 250 rpm apart), lowest speed first.
    peaks.sort(key=lambda p: p["rpm"])
    distinct: list[dict] = []
    for peak in peaks:
        if distinct and abs(peak["rpm"] - distinct[-1]["rpm"]) < 250:
            if peak["amp"] > distinct[-1]["amp"]:
                distinct[-1] = peak
            continue
        distinct.append(peak)
    return distinct[:3]


def _count_speed_cycles(rpm: np.ndarray) -> int:
    """How many times the machine climbed to rated speed and came back down."""
    if rpm.size < 10:
        return 0
    peak = np.nanmax(rpm)
    high = rpm >= RATED_FRACTION * peak
    low = rpm <= max(SLOW_ROLL[1], 0.25 * peak)
    cycles = 0
    state = "low"
    for h, l in zip(high, low):
        if state == "low" and h:
            state = "high"
        elif state == "high" and l:
            cycles += 1
            state = "low"
    return cycles


def _finding(check_id: str, title: str, severity: str, summary: str, **kwargs) -> Finding:
    return Finding(check_id=check_id, title=title, severity=severity, summary=summary, **kwargs)


def _check_swapped(name: str, fx: pd.DataFrame, fy: pd.DataFrame, segs: dict) -> Finding:
    rpm, zx, zy = aligned_phasors(fx, fy)
    slow = _precession_stats(zx, zy, segs["slow"])
    fast = _precession_stats(zx, zy, segs["dwell"])
    lead = _phase_lead(fx, fy, segs["up"] | segs["dwell"])
    details = []
    metrics = {"bearing": name, "slow": slow, "fast": fast, "lead": lead}
    reverse_slow = slow.get("frac_reverse", 0) > 0.6
    reverse_fast = fast.get("frac_reverse", 0) > 0.6
    if slow:
        details.append(
            f"Slow roll ({SLOW_ROLL[0]:.0f}–{SLOW_ROLL[1]:.0f} rpm): "
            f"forward {slow['mean_ccw']:.3f} vs reverse {slow['mean_cw']:.3f}, "
            f"reverse-dominant in {100 * slow['frac_reverse']:.0f}% of samples"
        )
    if fast:
        details.append(
            f"At speed: forward {fast['mean_ccw']:.3f} vs reverse {fast['mean_cw']:.3f}, "
            f"reverse-dominant in {100 * fast['frac_reverse']:.0f}% of samples"
        )
    if lead:
        q_x = int(fx["phase_1x"].median() // 90) + 1
        q_y = int(fy["phase_1x"].median() // 90) + 1
        details.append(
            f"Median 1Y−1X phase = {lead['median_lead']:.0f}° "
            f"(X polar quadrant Q{q_x}, Y Q{q_y})"
        )
        details.append(
            "For a CCW machine the journal should lift right and up; "
            "a mirrored shaft-centreline needs gap data (not in a timebase dump)."
        )
    if reverse_slow and reverse_fast:
        return _finding(
            "1",
            f"{name}: swapped probes",
            "alarm",
            "Reverse precession at slow roll AND at speed — X/Y probes are likely swapped.",
            details=details,
            metrics=metrics,
        )
    if reverse_slow or reverse_fast:
        where = "slow roll" if reverse_slow else "running speed"
        return _finding(
            "1",
            f"{name}: possible swapped probes",
            "watch",
            f"Reverse precession at {where} only. Confirm wiring and polar quadrant.",
            details=details,
            metrics=metrics,
        )
    return _finding(
        "1",
        f"{name}: probe orientation",
        "ok",
        "1X orbit is forward-precessing at slow roll and at speed; probes look correctly assigned.",
        details=details,
        metrics=metrics,
    )


def _check_noise(name: str, fx: pd.DataFrame, fy: pd.DataFrame) -> Finding:
    if "n_spikes" not in fx.columns:
        return _finding(
            "2",
            f"{name}: probe noise",
            "skip",
            "No timebase waveforms — cannot inspect spikes. Re-run on a Plot Name dump.",
        )
    parts = []
    alarm = False
    watch = False
    for label, frame in (("X", fx), ("Y", fy)):
        spikes = frame["n_spikes"].to_numpy(dtype=float)
        crest = frame["crest"].to_numpy(dtype=float)
        n_bad = int(np.nansum(spikes >= 3))
        med_crest = float(np.nanmedian(crest))
        max_crest = float(np.nanmax(crest)) if np.isfinite(crest).any() else 0.0
        parts.append(
            f"{label}: {n_bad} records with ≥3 samples beyond 6σ, "
            f"median crest {med_crest:.1f}, peak crest {max_crest:.1f}"
        )
        if n_bad > max(3, 0.05 * len(frame)) or max_crest > 8:
            alarm = True
        elif n_bad > 0 or max_crest > 5:
            watch = True
    severity = "alarm" if alarm else "watch" if watch else "ok"
    summary = {
        "ok": "Timebase records look clean; no unusual spike population.",
        "watch": "Some spike/crest outliers — inspect those timebases.",
        "alarm": "Frequent spikes or very high crest factor; treat amplitudes as suspect.",
    }[severity]
    return _finding("2", f"{name}: probe noise", severity, summary, details=parts)


def _check_scale(name: str, fx: pd.DataFrame, fy: pd.DataFrame, segs: dict) -> Finding:
    slow = segs["slow"]
    if slow.sum() < 3:
        return _finding(
            "3",
            f"{name}: proximity scale / cable length",
            "skip",
            "Not enough slow-roll samples to compare X and Y scale factors.",
        )
    ax = fx["amp_1x"].to_numpy(dtype=float)[slow]
    ay = fy["amp_1x"].to_numpy(dtype=float)[slow]
    dx = fx["direct"].to_numpy(dtype=float)[slow] if "direct" in fx.columns else ax
    dy = fy["direct"].to_numpy(dtype=float)[slow] if "direct" in fy.columns else ay
    ratio_1x = float(np.nanmedian(ax) / np.nanmedian(ay)) if np.nanmedian(ay) > 1e-6 else float("nan")
    ratio_d = float(np.nanmedian(dx) / np.nanmedian(dy)) if np.nanmedian(dy) > 1e-6 else float("nan")
    details = [
        f"Slow-roll median 1X: X={np.nanmedian(ax):.3f}, Y={np.nanmedian(ay):.3f}, ratio X/Y={ratio_1x:.2f}",
        f"Slow-roll median Direct: X={np.nanmedian(dx):.3f}, Y={np.nanmedian(dy):.3f}, ratio X/Y={ratio_d:.2f}",
        "A 9 m Proximitor on a 5 m cable (or the reverse) changes the scale factor. "
        "At slow roll the mis-scaled probe typically reads about twice its orthogonal partner.",
    ]
    bad = (np.isfinite(ratio_1x) and (ratio_1x >= SCALE_RATIO_ALARM or ratio_1x <= 1 / SCALE_RATIO_ALARM)) or (
        np.isfinite(ratio_d) and (ratio_d >= SCALE_RATIO_ALARM or ratio_d <= 1 / SCALE_RATIO_ALARM)
    )
    if bad:
        return _finding(
            "3",
            f"{name}: scale-factor / cable-length mismatch",
            "alarm",
            "X and Y slow-roll amplitudes differ by ~2× — check 5 m vs 9 m probe/extension/Proximitor matching.",
            details=details,
            metrics={"ratio_1x": ratio_1x, "ratio_direct": ratio_d},
        )
    return _finding(
        "3",
        f"{name}: proximity scale / cable length",
        "ok",
        "Slow-roll X and Y amplitudes agree; no evidence of a 5 m/9 m scale-factor mismatch.",
        details=details,
        metrics={"ratio_1x": ratio_1x, "ratio_direct": ratio_d},
    )


def _check_same_bearing(name: str, fx: pd.DataFrame, fy: pd.DataFrame, segs: dict) -> Finding:
    transient = segs["up"] | (segs["dwell"] & (np.arange(len(fx)) < (np.where(segs["dwell"])[0][0] + 40 if segs["dwell"].any() else 0)))
    lead = _phase_lead(fx, fy, transient if transient.any() else segs["full"])
    details = []
    if lead:
        details.append(
            f"Orthogonal phase check on transient + short steady state: "
            f"median 1Y−1X = {lead['median_lead']:.0f}°, "
            f"{100 * lead['frac_within_tol']:.0f}% of samples within {PHASE_ORTH_TOL_DEG:.0f}° of 90°/270°"
        )
    # Spectral family: if X is 1X-dominant, Y should be too.
    def family(frame: pd.DataFrame) -> str:
        cols = {"1X": "amp_1x", "0.5X": "amp_05x", "0.47X": "amp_047x", "0.33X": "amp_033x"}
        present = {k: frame[c].to_numpy(dtype=float) for k, c in cols.items() if c in frame.columns}
        if "1X" not in present:
            return "1X"
        dwell = segs["dwell"] if segs["dwell"].any() else segs["full"]
        means = {k: float(np.nanmedian(v[dwell])) for k, v in present.items()}
        return max(means, key=means.get)

    fam_x, fam_y = family(fx), family(fy)
    details.append(f"Dominant order at speed: X={fam_x}, Y={fam_y}")
    phase_bad = bool(lead) and lead["frac_within_tol"] < 0.5
    family_bad = fam_x != fam_y and {fam_x, fam_y} != {"1X"}
    if phase_bad or family_bad:
        return _finding(
            "4",
            f"{name}: XY pair may not be the same bearing",
            "alarm",
            "Phase is not ~90° and/or the two probes are dominated by different orders — likely mis-paired channels.",
            details=details,
        )
    if lead and lead["frac_within_tol"] < 0.8:
        return _finding(
            "4",
            f"{name}: same-bearing pairing",
            "watch",
            "Phase scatter around 90° is larger than ideal; pairing is probably right but noisy.",
            details=details,
        )
    return _finding(
        "4",
        f"{name}: same-bearing pairing",
        "ok",
        "X and Y stay ~90° apart and share the same spectral family.",
        details=details,
    )


def _check_criticals(name: str, fx: pd.DataFrame, fy: pd.DataFrame, segs: dict) -> list[Finding]:
    rpm = fx["rpm"].to_numpy(dtype=float)
    amp = 0.5 * (fx["amp_1x"].to_numpy(dtype=float) + fy["amp_1x"].to_numpy(dtype=float))
    phase = fx["phase_1x"].to_numpy(dtype=float)
    findings = []
    for label, mask in (("startup", segs["up"]), ("shutdown", segs["down"])):
        crits = _find_criticals(rpm[mask], amp[mask], phase[mask])
        if not crits:
            findings.append(
                _finding(
                    "5" if label == "startup" else "11",
                    f"{name}: critical speeds ({label})",
                    "info",
                    f"No clear 1X amplitude peak with ≥70° phase roll on {label}.",
                )
            )
            continue
        details = [
            f"{label} peak at {c['rpm']:.0f} rpm, 1X {c['amp']:.3f}, phase roll {c['phase_roll_deg']:.0f}°"
            for c in crits
        ]
        first = crits[0]
        second = crits[1] if len(crits) > 1 else None
        summary = f"First critical ≈ {first['rpm']:.0f} rpm"
        if second:
            summary += f", second ≈ {second['rpm']:.0f} rpm"
        summary += f" on {label} (1X Bode / polar)."
        findings.append(
            _finding(
                "5" if label == "startup" else "6",
                f"{name}: critical speeds ({label})",
                "ok",
                summary,
                details=details,
                metrics={"criticals": crits, "segment": label},
            )
        )
    # First vs second labelled on startup if we have them.
    up = [f for f in findings if f.metrics.get("segment") == "startup"]
    if up and up[0].metrics.get("criticals"):
        crits = up[0].metrics["criticals"]
        if len(crits) >= 2:
            findings.append(
                _finding(
                    "6",
                    f"{name}: first and second critical",
                    "ok",
                    f"First critical {crits[0]['rpm']:.0f} rpm, second {crits[1]['rpm']:.0f} rpm.",
                    details=[
                        "Picked from 1X amplitude peaks that also roll phase on the Bode. "
                        "Confirm on the polar (vector slows and turns ~180°) and 1X orbit."
                    ],
                )
            )
    return findings


def _check_fsnl(name: str, fx: pd.DataFrame, fy: pd.DataFrame, segs: dict, unit: str) -> Finding:
    dwell = segs["dwell"]
    rpm = fx["rpm"].to_numpy(dtype=float)
    if not dwell.any():
        return _finding("8", f"{name}: FSNL vs ISO 20816-2", "skip", "No at-speed dwell to treat as FSNL.")
    direct = np.maximum(
        fx["direct"].to_numpy(dtype=float) if "direct" in fx.columns else fx["amp_1x"].to_numpy(dtype=float),
        fy["direct"].to_numpy(dtype=float) if "direct" in fy.columns else fy["amp_1x"].to_numpy(dtype=float),
    )
    rated = float(np.nanmax(rpm))
    amp = float(np.nanmedian(direct[dwell]))
    ab, bc, cd = _iso_limits_mil(rated, "gas")
    zone = iso_zone(amp, rated, "gas")
    details = [
        f"At-speed dwell median Direct (max of X,Y) = {amp:.3f} {unit} "
        f"(table selected for {rated:.0f} rpm class)",
        f"ISO 20816-2:2017 Table B.1 gas turbine {3600 if rated >= 3300 else 3000} r/min, "
        f"relative shaft pp: A/B={ab:.2f}, B/C={bc:.2f}, C/D={cd:.2f} mil "
        f"({ab * _UM_PER_MIL:.0f}/{bc * _UM_PER_MIL:.0f}/{cd * _UM_PER_MIL:.0f} µm)",
        "Load is not in this export, so the high-speed dwell is treated as FSNL. "
        "Zone A = newly commissioned, B = acceptable, C = limited operation, D = damage likely.",
    ]
    severity = {"A": "ok", "B": "ok", "C": "watch", "D": "alarm"}.get(zone, "info")
    if zone in {"C", "D"}:
        summary = f"FSNL Direct is ISO zone {zone} — vibration is excessive relative to ISO 20816-2."
    elif zone == "B":
        summary = f"FSNL Direct is ISO zone B (acceptable for long-term operation)."
    else:
        summary = f"FSNL Direct is ISO zone {zone}."
    return _finding("8", f"{name}: FSNL vs ISO 20816-2", severity, summary, details=details, metrics={"zone": zone, "amp": amp})


def _check_steady(name: str, fx: pd.DataFrame, fy: pd.DataFrame, segs: dict) -> Finding:
    islands = [m for m in _mask_runs(segs["dwell"]) if m.sum() >= 8]
    if not islands:
        return _finding("9", f"{name}: steady-state change", "skip", "Dwell is too short to trend Direct/1X/2X.")
    details = [f"{len(islands)} high-speed dwell island(s) in this record."]
    watch = False
    alarm = False
    medians_1x: list[float] = []
    for i, island in enumerate(islands, 1):
        ax = fx["amp_1x"].to_numpy(dtype=float)[island]
        dx = fx["direct"].to_numpy(dtype=float)[island] if "direct" in fx.columns else ax
        px = fx["phase_1x"].to_numpy(dtype=float)[island]
        med = float(np.nanmedian(ax))
        medians_1x.append(med)
        dspan = float(np.nanmax(dx) - np.nanmin(dx))
        pspan = float(np.ptp(np.rad2deg(np.unwrap(np.deg2rad(px[np.isfinite(px)]))))) if np.isfinite(px).sum() > 2 else 0.0
        details.append(
            f"Dwell {i}: median 1X {med:.3f}, Direct range {dspan:.3f}, 1X phase span {pspan:.0f}° "
            f"({int(island.sum())} samples)"
        )
        if med > 1e-6 and dspan / max(float(np.nanmedian(dx)), 1e-6) > 0.35:
            watch = True
        if pspan > 50:
            watch = True
        if pspan > 90:
            alarm = True
    if len(medians_1x) >= 2 and medians_1x[0] > 1e-6:
        rel = abs(medians_1x[-1] - medians_1x[0]) / medians_1x[0]
        details.append(f"1X change between first and last dwell: {100 * rel:.0f}%")
        if rel > 0.2:
            watch = True
        if rel > 0.4:
            alarm = True
    severity = "alarm" if alarm else "watch" if watch else "ok"
    summary = {
        "ok": "Steady-state Direct, 1X, 2X and phase are stable through the dwell.",
        "watch": "Steady-state vibration walks during a dwell or changed between high-speed soaks.",
        "alarm": "Large Direct/1X/2X or phase change at constant speed.",
    }[severity]
    return _finding("9", f"{name}: steady-state change", severity, summary, details=details)


def _check_slowroll_comp(name: str, fx: pd.DataFrame, fy: pd.DataFrame, segs: dict) -> Finding:
    slow = segs["slow"]
    down = segs["down"]
    has_shutdown = down.any() and (fx["rpm"].to_numpy(dtype=float)[down].min() <= SLOW_ROLL[1])
    if not slow.any():
        return _finding("10", f"{name}: slow-roll compensation", "skip", "No slow-roll window in the record.")
    ax = float(np.nanmedian(fx["amp_1x"].to_numpy(dtype=float)[slow]))
    ay = float(np.nanmedian(fy["amp_1x"].to_numpy(dtype=float)[slow]))
    px = _circ_median(fx["phase_1x"].to_numpy(dtype=float)[slow])
    py = _circ_median(fy["phase_1x"].to_numpy(dtype=float)[slow])
    details = [
        f"Slow-roll 1X vector: X {ax:.3f} ∠ {px:.0f}°, Y {ay:.3f} ∠ {py:.0f}°",
        "Subtract this vector from the Bode/polar before reading unbalance response. "
        f"Shutdown data {'is' if has_shutdown else 'is not'} present in this record.",
    ]
    return _finding(
        "10",
        f"{name}: slow-roll compensation",
        "info",
        f"Documented slow-roll 1X: X {ax:.3f} ∠ {px:.0f}°, Y {ay:.3f} ∠ {py:.0f}°. Apply before polar/Bode reads.",
        details=details,
        metrics={"amp_x": ax, "phase_x": px, "amp_y": ay, "phase_y": py},
    )


def _check_up_vs_down(name: str, fx: pd.DataFrame, fy: pd.DataFrame, segs: dict) -> Finding:
    rpm = fx["rpm"].to_numpy(dtype=float)
    amp = 0.5 * (fx["amp_1x"].to_numpy(dtype=float) + fy["amp_1x"].to_numpy(dtype=float))
    phase = fx["phase_1x"].to_numpy(dtype=float)
    if not segs["down"].any() or rpm[segs["down"]].max() - rpm[segs["down"]].min() < 500:
        return _finding("11", f"{name}: startup vs shutdown", "skip", "No usable shutdown transient.")
    up = _find_criticals(rpm[segs["up"]], amp[segs["up"]], phase[segs["up"]])
    down = _find_criticals(rpm[segs["down"]], amp[segs["down"]], phase[segs["down"]])
    details = []
    watch = False
    alarm = False
    if up:
        details.append(f"Startup first critical {up[0]['rpm']:.0f} rpm at {up[0]['amp']:.3f}")
    if down:
        details.append(f"Shutdown first critical {down[0]['rpm']:.0f} rpm at {down[0]['amp']:.3f}")
    if up and down:
        d_rpm = down[0]["rpm"] - up[0]["rpm"]
        d_amp = down[0]["amp"] - up[0]["amp"]
        details.append(f"Δ critical = {d_rpm:+.0f} rpm, Δ amplitude = {d_amp:+.3f}")
        if abs(d_rpm) > 150 or (up[0]["amp"] > 0.05 and abs(d_amp) / up[0]["amp"] > 0.35):
            watch = True
        # Rub often drops the apparent critical on shutdown and adds 1/2X.
        if "amp_05x" in fx.columns:
            half_up = float(np.nanmedian(fx["amp_05x"].to_numpy(dtype=float)[segs["up"]]))
            half_down = float(np.nanmedian(fx["amp_05x"].to_numpy(dtype=float)[segs["down"]]))
            details.append(f"0.5X median: startup {half_up:.3f}, shutdown {half_down:.3f}")
            if half_down > max(0.08, 1.8 * half_up):
                alarm = True
                details.append("0.5X grows on shutdown — rub is a candidate.")
    slow = segs["slow"]
    if slow.any():
        details.append(
            "Circular slow-roll 1X orbit that matches the adjacent bearing is the bowed-rotor signature "
            "(see check 16)."
        )
    severity = "alarm" if alarm else "watch" if watch else "ok"
    summary = {
        "ok": "Startup and shutdown criticals agree; no strong rub/bow signature in the 1X Bode.",
        "watch": "Critical speed or amplitude changed between startup and shutdown — inspect for rub or bow.",
        "alarm": "Shutdown 0.5X and/or a large critical shift — treat as possible rub.",
    }[severity]
    return _finding("11", f"{name}: startup vs shutdown", severity, summary, details=details)


def _check_thermal(name: str, fx: pd.DataFrame, segs: dict) -> Finding:
    dwell = segs["dwell"]
    if dwell.sum() < 12:
        return _finding("12", f"{name}: thermal sensitivity", "skip", "Dwell is too short for a thermal walk.")
    amp = fx["amp_1x"].to_numpy(dtype=float)[dwell]
    phase = fx["phase_1x"].to_numpy(dtype=float)[dwell]
    t = np.arange(amp.size)
    finite = np.isfinite(amp)
    if finite.sum() < 12:
        return _finding("12", f"{name}: thermal sensitivity", "skip", "Not enough finite 1X points in the dwell.")
    slope = float(np.polyfit(t[finite], amp[finite], 1)[0])
    span = float(np.nanmax(amp) - np.nanmin(amp))
    dphi = float(np.ptp(np.rad2deg(np.unwrap(np.deg2rad(phase[np.isfinite(phase)]))))) if np.isfinite(phase).any() else 0.0
    details = [
        f"1X amplitude walk over dwell: {span:.3f} (slope {slope:+.4f} per sample)",
        f"1X phase walk over dwell: {dphi:.0f}°",
        "Thermal sensitivity shows up as a slow 1X vector walk at constant speed while the machine soaks.",
    ]
    med = float(np.nanmedian(amp))
    rel = span / med if med > 1e-6 else 0.0
    if rel > 0.35 or dphi > 60:
        return _finding("12", f"{name}: thermal sensitivity", "watch", "1X vector walks during the speed dwell — thermal / alignment soak.", details=details)
    return _finding("12", f"{name}: thermal sensitivity", "ok", "No significant thermal walk of the 1X vector at speed.", details=details)


def _orbit_aspect(zx, zy, mask) -> float:
    stats = _precession_stats(zx, zy, mask)
    return stats.get("median_aspect", float("nan"))


def _check_preload(name: str, fx: pd.DataFrame, fy: pd.DataFrame, segs: dict) -> Finding:
    rpm, zx, zy = aligned_phasors(fx, fy)
    aspect_fast = _orbit_aspect(zx, zy, segs["dwell"])
    amp2_x = float(np.nanmedian(fx["amp_2x"].to_numpy(dtype=float)[segs["dwell"]])) if "amp_2x" in fx.columns and segs["dwell"].any() else float("nan")
    amp1_x = float(np.nanmedian(fx["amp_1x"].to_numpy(dtype=float)[segs["dwell"]])) if segs["dwell"].any() else float("nan")
    ratio_2x = amp2_x / amp1_x if amp1_x and amp1_x > 1e-6 else float("nan")
    details = [
        f"At-speed 1X orbit aspect (minor/major) = {aspect_fast:.2f}  (1.00 = circular, low = flattened / preloaded)",
        f"2X/1X amplitude ratio (X) = {ratio_2x:.2f}" if np.isfinite(ratio_2x) else "2X not available",
        "Preload / misalignment: flattened or banana orbits, elevated 2X, and a shaft centreline that does not lift as expected.",
    ]
    watch = (np.isfinite(aspect_fast) and aspect_fast < 0.45) or (np.isfinite(ratio_2x) and ratio_2x > 0.5)
    if watch:
        return _finding("13", f"{name}: preload / misalignment", "watch", "Flattened 1X orbit and/or high 2X — check preload and alignment.", details=details)
    return _finding("13", f"{name}: preload / misalignment", "ok", "Orbit aspect and 2X do not show a strong preload/misalignment signature.", details=details)


def _check_waterfall(name: str, fx: pd.DataFrame, fy: pd.DataFrame, segs: dict) -> Finding:
    if "subsync_max" not in fx.columns:
        return _finding("15", f"{name}: waterfall / instability", "skip", "Need timebase FFTs for subsynchronous orders.")
    dwell = segs["dwell"] if segs["dwell"].any() else segs["full"]
    sub = np.maximum(fx["subsync_max"].to_numpy(dtype=float), fy["subsync_max"].to_numpy(dtype=float))
    half = np.maximum(fx["amp_05x"].to_numpy(dtype=float), fy["amp_05x"].to_numpy(dtype=float))
    one = np.maximum(fx["amp_1x"].to_numpy(dtype=float), fy["amp_1x"].to_numpy(dtype=float))
    sup = np.maximum(fx["supersync_max"].to_numpy(dtype=float), fy["supersync_max"].to_numpy(dtype=float))
    sub_d, half_d, one_d, sup_d = sub[dwell], half[dwell], one[dwell], sup[dwell]
    sub_rel = float(np.nanmedian(sub_d) / np.nanmedian(one_d)) if np.nanmedian(one_d) > 1e-6 else 0.0
    half_rel = float(np.nanmedian(half_d) / np.nanmedian(one_d)) if np.nanmedian(one_d) > 1e-6 else 0.0
    details = [
        f"At speed, median 0.30–0.48X / 1X = {sub_rel:.2f}, 0.5X / 1X = {half_rel:.2f}",
        f"Peak subsync {float(np.nanmax(sub_d)):.3f}, peak supersync (2.4–8X) {float(np.nanmax(sup_d)):.3f}",
        "Oil whirl/whip sits near 0.38–0.48X and can lock and grow (fluid-induced instability). "
        "A persistent 0.5X is more often rub or looseness than whip.",
    ]
    if sub_rel > 0.35 or (sub_rel > 0.15 and float(np.nanmedian(sub_d)) > 0.12):
        return _finding("15", f"{name}: waterfall / fluid instability", "alarm", "Persistent subsynchronous component at speed — possible oil whirl/whip.", details=details)
    if half_rel > 0.35:
        return _finding("15", f"{name}: waterfall / sub- and supersync", "watch", "Elevated 0.5X at speed — check rub/looseness on the waterfall.", details=details)
    return _finding("15", f"{name}: waterfall / sub- and supersync", "ok", "No significant subsynchronous or unusual supersynchronous family at speed.", details=details)


def _raw_orbit_circularity(
    raw: dict[tuple[str, int], np.ndarray],
    x_name: str,
    y_name: str,
    samples: np.ndarray,
) -> dict[str, float]:
    aspects = []
    for sample in samples:
        x = raw.get((x_name, int(sample)))
        y = raw.get((y_name, int(sample)))
        if x is None or y is None:
            continue
        n = min(x.size, y.size)
        if n < 32:
            continue
        x, y = x[:n] - np.mean(x[:n]), y[:n] - np.mean(y[:n])
        # Covariance ellipse aspect.
        cov = np.cov(x, y)
        eig = np.linalg.eigvalsh(cov)
        if eig[-1] <= 1e-18:
            continue
        aspects.append(float(np.sqrt(max(eig[0], 0.0) / eig[-1])))
    if not aspects:
        return {}
    return {"n": float(len(aspects)), "median_aspect": float(np.median(aspects))}


def _check_bow(
    name: str,
    fx: pd.DataFrame,
    fy: pd.DataFrame,
    segs: dict,
    raw: dict[tuple[str, int], np.ndarray],
    x_name: str,
    y_name: str,
    neighbor: tuple[pd.DataFrame, pd.DataFrame] | None,
    n_cycles: int,
) -> Finding:
    rpm, zx, zy = aligned_phasors(fx, fy)
    stats = _precession_stats(zx, zy, segs["slow"])
    sample_ids = fx.index.to_numpy()[segs["slow"]]
    raw_stats = _raw_orbit_circularity(raw, x_name, y_name, sample_ids) if raw else {}
    aspect = raw_stats.get("median_aspect", stats.get("median_aspect", float("nan")))
    amp = float(np.nanmedian(0.5 * (fx["amp_1x"].to_numpy()[segs["slow"]] + fy["amp_1x"].to_numpy()[segs["slow"]]))) if segs["slow"].any() else float("nan")
    details = [
        f"Slow-roll 1X orbit aspect {stats.get('median_aspect', float('nan')):.2f}" if stats else "No slow-roll 1X orbit",
        f"Slow-roll timebase orbit aspect {aspect:.2f} from {raw_stats.get('n', 0):.0f} records" if raw_stats else "No paired slow-roll timebases kept",
        f"Slow-roll 1X amplitude {amp:.3f}",
        f"Speed cycles (startup+shutdown pairs) in this dump: {n_cycles}",
        "A circular slow-roll orbit with a stable 1X amplitude is the classic bowed-rotor signature. "
        "Compare with the adjacent bearing at the same speed; a bow is seen at both.",
    ]
    neighbor_note = ""
    if neighbor is not None and segs["slow"].any():
        nfx, nfy = neighbor
        n_rpm = nfx["rpm"].to_numpy(dtype=float)
        # Use this bearing's slow-roll speed band on the neighbour.
        nmask = (n_rpm >= SLOW_ROLL[0]) & (n_rpm <= SLOW_ROLL[1])
        n_amp = float(np.nanmedian(0.5 * (nfx["amp_1x"].to_numpy()[nmask] + nfy["amp_1x"].to_numpy()[nmask]))) if nmask.any() else float("nan")
        neighbor_note = f"Adjacent bearing slow-roll 1X ≈ {n_amp:.3f}"
        details.append(neighbor_note)
    circular = np.isfinite(aspect) and aspect >= CIRCULAR_ASPECT and np.isfinite(amp) and amp > 0.25
    noisy = raw and "crest" in fx.columns and float(np.nanmedian(fx["crest"].to_numpy()[segs["slow"]])) > 4.5
    if noisy and not circular:
        return _finding("16", f"{name}: slow-roll orbit / bow", "watch", "Slow-roll orbit is noisy rather than circular — inspect timebases before calling bow.", details=details)
    if circular:
        return _finding(
            "16",
            f"{name}: slow-roll orbit / bowed rotor",
            "watch",
            "Slow-roll orbit is circular with a real 1X component — consistent with rotor bow. Compare adjacent bearing and later shutdowns.",
            details=details,
        )
    return _finding("16", f"{name}: slow-roll orbit / bow", "ok", "Slow-roll orbit is not a clean circle; no strong bowed-rotor call.", details=details)


def _check_scl_placeholder(name: str, has_gap: bool) -> Finding:
    if not has_gap:
        return _finding(
            "14",
            f"{name}: shaft centreline across bearings",
            "skip",
            "Timebase dumps do not include Avg Gap. Export a Tabular List (or channel CSVs) for SCL comparison. "
            "For a CCW machine the journal should move right and up as speed rises; CW is left and up. "
            "A mirrored SCL is another swapped-probe clue.",
        )
    return _finding(
        "14",
        f"{name}: shaft centreline",
        "info",
        "Gap voltages are present — use the Shaft centreline tab to compare lift direction across bearings.",
        details=[
            "CCW machine: rotor moves right and up with speed. CW: left and up. "
            "A mirror image versus a correctly wired bearing is a swapped-probe indicator (check 1c)."
        ],
    )


def _check_scl(name: str, fx: pd.DataFrame, fy: pd.DataFrame, segs: dict) -> Finding:
    """Journal lift from Avg Gap. CCW convention: right and up as speed rises."""
    if "gap" not in fx.columns or "gap" not in fy.columns:
        return _check_scl_placeholder(name, False)
    rpm = fx["rpm"].to_numpy(dtype=float)
    gx = fx["gap"].to_numpy(dtype=float)
    gy = fy["gap"].to_numpy(dtype=float)
    if not (np.isfinite(gx).any() and np.isfinite(gy).any()):
        return _check_scl_placeholder(name, False)
    ref_x = rest_reference(gx, rpm)
    ref_y = rest_reference(gy, rpm)
    right, up = centreline_positions(gx, gy, ref_x, ref_y)
    dwell = segs["dwell"] if segs["dwell"].any() else rpm >= 0.9 * float(np.nanmax(rpm))
    r = float(np.nanmedian(right[dwell])) if dwell.any() else float("nan")
    u = float(np.nanmedian(up[dwell])) if dwell.any() else float("nan")
    details = [
        f"Journal lift rest→FSNL: {r:+.2f} mil right, {u:+.2f} mil up "
        "(CCW machine: expect right and up).",
        f"Rest gap X={ref_x:.3f} V, Y={ref_y:.3f} V. "
        f"FSNL median gap X={float(np.nanmedian(gx[dwell])):.3f} V, "
        f"Y={float(np.nanmedian(gy[dwell])):.3f} V.",
        "From the static Tabular List (Avg Gap). A mirror image vs the other bearings "
        "is a swapped-probe clue (check 1c).",
    ]
    if not (np.isfinite(r) and np.isfinite(u)):
        return _finding("14", f"{name}: shaft centreline", "skip", "Avg Gap present but lift could not be computed.", details=details)
    if r < -0.15 and u < -0.15:
        return _finding(
            "14",
            f"{name}: shaft centreline mirrored",
            "alarm",
            "Journal moved left and down — opposite of CCW lift. Check swapped probes / SCL plot.",
            details=details,
            metrics={"right_mil": r, "up_mil": u},
        )
    if r < -0.15 or u < -0.15:
        return _finding(
            "14",
            f"{name}: shaft centreline",
            "watch",
            f"Lift is not cleanly right-and-up (Δright {r:+.2f} mil, Δup {u:+.2f} mil). Confirm on the SCL plot.",
            details=details,
            metrics={"right_mil": r, "up_mil": u},
        )
    return _finding(
        "14",
        f"{name}: shaft centreline",
        "ok",
        f"Journal lifts right and up at speed (Δright {r:+.2f} mil, Δup {u:+.2f} mil), as expected for CCW.",
        details=details,
        metrics={"right_mil": r, "up_mil": u},
    )


def analyse_bearing(
    name: str,
    fx: pd.DataFrame,
    fy: pd.DataFrame,
    *,
    unit: str = "mil",
    raw: dict | None = None,
    x_name: str = "",
    y_name: str = "",
    neighbor: tuple[pd.DataFrame, pd.DataFrame] | None = None,
    has_gap: bool = False,
) -> list[Finding]:
    rpm = fx["rpm"].to_numpy(dtype=float)
    segs = _segments(rpm)
    n_cycles = _count_speed_cycles(rpm)
    raw = raw or {}
    findings = [
        _check_swapped(name, fx, fy, segs),
        _check_noise(name, fx, fy),
        _check_scale(name, fx, fy, segs),
        _check_same_bearing(name, fx, fy, segs),
    ]
    findings.extend(_check_criticals(name, fx, fy, segs))
    findings.extend(
        [
            _check_fsnl(name, fx, fy, segs, unit),
            _check_steady(name, fx, fy, segs),
            _check_slowroll_comp(name, fx, fy, segs),
            _check_up_vs_down(name, fx, fy, segs),
            _check_thermal(name, fx, segs),
            _check_preload(name, fx, fy, segs),
            _check_scl(name, fx, fy, segs) if has_gap else _check_scl_placeholder(name, False),
            _check_waterfall(name, fx, fy, segs),
            _check_bow(name, fx, fy, segs, raw, x_name, y_name, neighbor, n_cycles),
        ]
    )
    return findings


def _run_data_to_pair(run: RunData) -> tuple[str, pd.DataFrame, pd.DataFrame]:
    frame = run.frame
    fx = pd.DataFrame(
        {
            "rpm": frame["rpm"],
            "amp_1x": frame["amp_1x"],
            "phase_1x": frame["phase_1x"],
            "direct": frame["direct_1x"] if "direct_1x" in frame.columns else frame["amp_1x"],
            "amp_2x": frame["amp_2x"] if "amp_2x" in frame.columns else np.nan,
            "phase_2x": frame["phase_2x"] if "phase_2x" in frame.columns else np.nan,
            "gap": frame["gap_1x"] if "gap_1x" in frame.columns else np.nan,
        }
    )
    fy = pd.DataFrame(
        {
            "rpm": frame["rpm"],
            "amp_1x": frame["amp_1y"],
            "phase_1x": frame["phase_1y"],
            "direct": frame["direct_1y"] if "direct_1y" in frame.columns else frame["amp_1y"],
            "amp_2x": frame["amp_2y"] if "amp_2y" in frame.columns else np.nan,
            "phase_2x": frame["phase_2y"] if "phase_2y" in frame.columns else np.nan,
            "gap": frame["gap_1y"] if "gap_1y" in frame.columns else np.nan,
        }
    )
    name = run.probe_1x_name[:-1] if run.probe_1x_name.upper().endswith("X") else "BRG"
    return name, fx, fy


def analyse_run(run: RunData) -> BuddyReport:
    """Vector-based Rotor Buddy review of one XY pair (tabular ADRE export)."""
    name, fx, fy = _run_data_to_pair(run)
    has_gap = "gap_1x" in run.frame.columns
    findings = analyse_bearing(name, fx, fy, unit=run.amp_unit, has_gap=has_gap, x_name=run.probe_1x_name, y_name=run.probe_1y_name)
    return BuddyReport(
        source=", ".join(run.source_files.values()),
        n_samples=len(run.frame),
        rpm_min=float(np.nanmin(run.rpm)),
        rpm_max=float(np.nanmax(run.rpm)),
        bearings=[name],
        amp_unit=run.amp_unit,
        findings=findings,
        notes=[
            "Loaded from a tabular / channel export. Checks that need timebase waveforms "
            "(noise spikes, waterfall orders, slow-roll orbit shape) are skipped or weakened. "
            "Drop the concatenated Plot Name dump on `--buddy` for the full set."
        ],
        frame=None,
    )


def analyse_timebase(
    path: str | Path,
    *,
    progress: bool = False,
    use_cache: bool = True,
) -> BuddyReport:
    """Full Rotor Buddy review of a concatenated timebase dump."""
    path = Path(path)
    cache = harmonics_cache_path(path)
    raw: dict[tuple[str, int], np.ndarray] = {}
    if use_cache and cache.is_file() and cache.stat().st_mtime >= path.stat().st_mtime:
        if progress:
            print(f"Loading cached harmonics {cache.name}", flush=True)
        frame = load_harmonics(cache)
    else:
        if progress:
            print(f"Streaming {path.name} ({path.stat().st_size / 1e6:.0f} MB) ...", flush=True)
        frame, raw = summarise_timebase(path, progress=progress, keep_raw=True)
        save_harmonics(frame, path)
        if progress:
            print(f"Wrote {cache.name}", flush=True)
    pairs = bearing_pairs(frame)
    if not pairs:
        raise ValueError(f"{path.name}: no XY bearing pairs in the timebase dump")
    findings: list[Finding] = []
    unit = str(frame["amp_unit"].dropna().iloc[0]) if "amp_unit" in frame.columns and not frame["amp_unit"].dropna().empty else "mil"
    for i, (name, fx, fy) in enumerate(pairs):
        neighbor = None
        if i + 1 < len(pairs):
            neighbor = (pairs[i + 1][1], pairs[i + 1][2])
        elif i > 0:
            neighbor = (pairs[i - 1][1], pairs[i - 1][2])
        x_name = str(fx["channel"].iloc[0]) if "channel" in fx.columns else f"{name}X"
        y_name = str(fy["channel"].iloc[0]) if "channel" in fy.columns else f"{name}Y"
        # After set_index("sample"), channel may still be a column.
        if "channel" not in fx.columns:
            x_name, y_name = f"{name}X", f"{name}Y"
        else:
            x_name = str(fx["channel"].iloc[0])
            y_name = str(fy["channel"].iloc[0])
        findings.extend(
            analyse_bearing(
                name,
                fx,
                fy,
                unit=unit,
                raw=raw,
                x_name=x_name,
                y_name=y_name,
                neighbor=neighbor,
            )
        )
    rpm = pairs[0][1]["rpm"].to_numpy(dtype=float)
    return BuddyReport(
        source=path.name,
        n_samples=int(pairs[0][1].shape[0]),
        rpm_min=float(np.nanmin(rpm)),
        rpm_max=float(np.nanmax(rpm)),
        bearings=[p[0] for p in pairs],
        amp_unit=unit,
        findings=findings,
        notes=[
            "Proximity XY timebases only (seismic and thrust ignored).",
            "Shaft-centreline (check 14) needs Avg Gap from a Tabular List export.",
            "ISO 20816-2 FSNL uses the high-speed dwell as a stand-in for load (none in the dump).",
        ],
        frame=frame,
    )


def analyse_path(path: str | Path, *, progress: bool = False) -> BuddyReport:
    """Dispatch on file kind: timebase dump, harmonics cache, or tabular folder."""
    path = Path(path)
    if path.is_dir():
        from .dataset import build_run

        return analyse_run(build_run(path))
    if path.name.endswith(".harmonics.csv"):
        return _report_from_frame(load_harmonics(path), source=path.name)
    if is_timebase_export(path):
        kind = peek_timebase_kind(path)
        report = analyse_timebase(path, progress=progress)
        if kind == "async":
            report.notes = [
                "This file is an Async Waveform timebase. 1X/whirl/criticals need a Sync Waveform dump; "
                "noise (check 2) is valid here.",
                *report.notes,
            ]
        return report
    from .adre import load_export

    return _report_from_tabular(load_export(path), source=path.name)


def classify_buddy_input(path: str | Path) -> str:
    """``sync``, ``async``, ``tabular``, ``dir``, or ``harmonics``."""
    path = Path(path)
    if path.is_dir():
        return "dir"
    if path.name.endswith(".harmonics.csv"):
        origin = path.with_name(path.name[: -len(".harmonics.csv")] + ".csv")
        if origin.is_file() and is_timebase_export(origin):
            return peek_timebase_kind(origin)
        return "sync"
    if is_timebase_export(path):
        return peek_timebase_kind(path)
    return "tabular"


def _load_timebase_frame(path: Path, *, progress: bool = False) -> tuple[pd.DataFrame, dict]:
    cache = harmonics_cache_path(path)
    raw: dict[tuple[str, int], np.ndarray] = {}
    if cache.is_file() and cache.stat().st_mtime >= path.stat().st_mtime:
        if progress:
            print(f"Loading cached harmonics {cache.name}", flush=True)
        return load_harmonics(cache), raw
    if progress:
        print(f"Streaming {path.name} ({path.stat().st_size / 1e6:.0f} MB) ...", flush=True)
    frame, raw = summarise_timebase(path, progress=progress, keep_raw=True)
    save_harmonics(frame, path)
    if progress:
        print(f"Wrote {cache.name}", flush=True)
    return frame, raw


def _frame_from_tabular_channel(channel) -> pd.DataFrame:
    samples = channel.samples
    rpm = samples["Speed(P)"] if "Speed(P)" in samples.columns else samples["Speed"]
    frame = pd.DataFrame(
        {
            "channel": channel.name,
            "sample": samples["Sample#"],
            "rpm": rpm,
            "direct": samples["Direct"] if "Direct" in samples.columns else np.nan,
            "amp_1x": samples["1XAmplitude"] if "1XAmplitude" in samples.columns else np.nan,
            "phase_1x": samples["1X Phase"] if "1X Phase" in samples.columns else np.nan,
            "amp_2x": samples["2XAmplitude"] if "2XAmplitude" in samples.columns else np.nan,
            "gap": samples["Avg Gap"] if "Avg Gap" in samples.columns else np.nan,
            "amp_unit": channel.amp_unit or "mil",
        }
    )
    return frame


def _prox_tabular_frame(channels) -> pd.DataFrame:
    parts = []
    for channel in channels:
        name = channel.name.strip()
        if channel.is_keyphasor or not (name.upper().startswith("BRG") and name[-1].upper() in "XY" and "_" not in name):
            continue
        parts.append(_frame_from_tabular_channel(channel))
    if not parts:
        raise ValueError("Tabular export has no BRGnX / BRGnY proximity channels")
    return pd.concat(parts, ignore_index=True)


def _report_from_tabular(channels, source: str) -> BuddyReport:
    frame = _prox_tabular_frame(channels)
    pairs = bearing_pairs(frame)
    findings: list[Finding] = []
    unit = str(frame["amp_unit"].dropna().iloc[0]) if "amp_unit" in frame.columns else "mil"
    for i, (name, fx, fy) in enumerate(pairs):
        neighbor = (pairs[i + 1][1], pairs[i + 1][2]) if i + 1 < len(pairs) else None
        has_gap = "gap" in fx.columns and np.isfinite(fx["gap"].to_numpy(dtype=float)).any()
        findings.extend(
            analyse_bearing(
                name,
                fx,
                fy,
                unit=unit,
                x_name=f"{name}X",
                y_name=f"{name}Y",
                neighbor=neighbor,
                has_gap=has_gap,
            )
        )
    rpm = pairs[0][1]["rpm"].to_numpy(dtype=float)
    return BuddyReport(
        source=source,
        n_samples=int(pairs[0][1].shape[0]),
        rpm_min=float(np.nanmin(rpm)),
        rpm_max=float(np.nanmax(rpm)),
        bearings=[p[0] for p in pairs],
        amp_unit=unit,
        findings=findings,
        notes=[
            "Loaded from a static Tabular List. Whirl / orbit-shape checks need a Sync Waveform dump.",
            "Avg Gap is present — shaft centreline (check 14) is scored from journal lift.",
        ],
        frame=frame,
    )


def _swap_check(findings: list[Finding], new: Finding) -> list[Finding]:
    return [new if f.check_id == new.check_id else f for f in findings]


def analyse_paths(paths: list[str | Path], *, progress: bool = False) -> BuddyReport:
    """One Rotor Buddy report from sync timebase, async timebase and/or static tabular."""
    files = [Path(p) for p in paths]
    if len(files) == 1:
        return analyse_path(files[0], progress=progress)

    buckets: dict[str, list[Path]] = {"sync": [], "async": [], "tabular": [], "dir": []}
    for path in files:
        kind = classify_buddy_input(path)
        if kind == "harmonics":
            kind = "sync"
        if kind not in buckets:
            kind = "tabular"
        buckets[kind].append(path)

    if buckets["dir"]:
        from .dataset import build_run

        return analyse_run(build_run(buckets["dir"][0]))

    sync_path = buckets["sync"][0] if buckets["sync"] else None
    async_path = buckets["async"][0] if buckets["async"] else None
    static_path = buckets["tabular"][0] if buckets["tabular"] else None
    if sync_path is None and async_path is None and static_path is None:
        raise ValueError("No readable Rotor Buddy inputs")

    sync_frame = async_frame = static_frame = None
    sync_raw: dict = {}
    if sync_path is not None:
        if sync_path.name.endswith(".harmonics.csv"):
            sync_frame = load_harmonics(sync_path)
        else:
            sync_frame, sync_raw = _load_timebase_frame(sync_path, progress=progress)
    if async_path is not None:
        async_frame, _async_raw = _load_timebase_frame(async_path, progress=progress)
    if static_path is not None:
        from .adre import load_export

        static_frame = _prox_tabular_frame(load_export(static_path))

    primary = sync_frame if sync_frame is not None else (async_frame if async_frame is not None else static_frame)
    assert primary is not None
    primary_pairs = {name: (fx, fy) for name, fx, fy in bearing_pairs(primary)}
    async_pairs = {name: (fx, fy) for name, fx, fy in bearing_pairs(async_frame)} if async_frame is not None else {}
    static_pairs = {name: (fx, fy) for name, fx, fy in bearing_pairs(static_frame)} if static_frame is not None else {}
    if not primary_pairs:
        channels = sorted(set(primary["channel"])) if "channel" in primary.columns else []
        raise ValueError(
            "no XY bearing pairs in the primary export "
            f"(channels found: {', '.join(channels) or 'none'}). "
            "Re-export the timebase with every proximity channel selected."
        )

    names = list(primary_pairs)
    findings: list[Finding] = []
    unit = str(primary["amp_unit"].dropna().iloc[0]) if "amp_unit" in primary.columns and not primary["amp_unit"].dropna().empty else "mil"
    for i, name in enumerate(names):
        fx, fy = primary_pairs[name]
        neighbor = None
        if i + 1 < len(names):
            neighbor = primary_pairs[names[i + 1]]
        has_gap = name in static_pairs and "gap" in static_pairs[name][0].columns
        if has_gap:
            fx = fx.copy()
            fy = fy.copy()
            sfx, sfy = static_pairs[name]
            common = fx.index.intersection(sfx.index)
            if len(common):
                fx.loc[common, "gap"] = sfx.loc[common, "gap"]
                fy.loc[common, "gap"] = sfy.loc[common, "gap"]
        x_name = str(fx["channel"].iloc[0]) if "channel" in fx.columns else f"{name}X"
        y_name = str(fy["channel"].iloc[0]) if "channel" in fy.columns else f"{name}Y"
        bearing_findings = analyse_bearing(
            name,
            fx,
            fy,
            unit=unit,
            raw=sync_raw,
            x_name=x_name,
            y_name=y_name,
            neighbor=neighbor,
            has_gap=has_gap,
        )
        if name in async_pairs:
            noise = _check_noise(name, async_pairs[name][0], async_pairs[name][1])
            noise.details = [f"Noise from async timebase ({async_path.name}).", *noise.details]
            bearing_findings = _swap_check(bearing_findings, noise)
        if name in static_pairs:
            sfx, sfy = static_pairs[name]
            scl = _check_scl(name, sfx, sfy, _segments(sfx["rpm"].to_numpy(dtype=float)))
            bearing_findings = _swap_check(bearing_findings, scl)
        findings.extend(bearing_findings)

    labels = []
    if sync_path:
        labels.append(f"sync {sync_path.name}")
    if async_path:
        labels.append(f"async {async_path.name}")
    if static_path:
        labels.append(f"static {static_path.name}")
    rpm = next(iter(primary_pairs.values()))[0]["rpm"].to_numpy(dtype=float)
    notes = [
        "Combined report: " + "; ".join(labels) + ".",
        "1X / criticals / whirl / bow from the sync timebase.",
    ]
    if async_path:
        notes.append("Probe noise (check 2) from the async timebase (unfiltered waveform).")
    if static_path:
        notes.append("Shaft centreline (check 14) from static Avg Gap.")
    else:
        notes.append("Shaft-centreline still needs a Tabular List with Avg Gap.")
    return BuddyReport(
        source=" + ".join(p.name for p in files),
        n_samples=int(next(iter(primary_pairs.values()))[0].shape[0]),
        rpm_min=float(np.nanmin(rpm)),
        rpm_max=float(np.nanmax(rpm)),
        bearings=names,
        amp_unit=unit,
        findings=findings,
        notes=notes,
        frame=primary,
    )


def _report_from_frame(frame: pd.DataFrame, source: str) -> BuddyReport:
    pairs = bearing_pairs(frame)
    findings: list[Finding] = []
    unit = str(frame["amp_unit"].dropna().iloc[0]) if "amp_unit" in frame.columns else "mil"
    for i, (name, fx, fy) in enumerate(pairs):
        neighbor = (pairs[i + 1][1], pairs[i + 1][2]) if i + 1 < len(pairs) else None
        x_name = str(fx["channel"].iloc[0]) if "channel" in fx.columns else f"{name}X"
        y_name = str(fy["channel"].iloc[0]) if "channel" in fy.columns else f"{name}Y"
        findings.extend(
            analyse_bearing(name, fx, fy, unit=unit, x_name=x_name, y_name=y_name, neighbor=neighbor)
        )
    rpm = pairs[0][1]["rpm"].to_numpy(dtype=float)
    return BuddyReport(
        source=source,
        n_samples=int(pairs[0][1].shape[0]),
        rpm_min=float(np.nanmin(rpm)),
        rpm_max=float(np.nanmax(rpm)),
        bearings=[p[0] for p in pairs],
        amp_unit=unit,
        findings=findings,
        frame=frame,
    )
