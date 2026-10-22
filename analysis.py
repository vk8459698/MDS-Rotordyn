"""Numbers quoted in OBSERVATIONS.md. Run with: python analysis.py"""

import numpy as np
import pandas as pd

from rotordyn import build_run
from rotordyn.vectors import slow_roll_vector, to_amp_phase

run = build_run(".")
f = run.frame
bounds = run.segment_bounds()

pd.set_option("display.width", 200)


def band_peaks(seg_name):
    start, stop = bounds[seg_name]
    s = f.iloc[start:stop]
    s = s[s["valid"]]
    print(f"\n=== {seg_name}: peak 1X amplitude in each 250 rpm band ===")
    bins = pd.cut(s["rpm"], np.arange(0, 3750, 250))
    g = s.groupby(bins, observed=True)
    out = pd.DataFrame({
        "1X max": g["amp_1x"].max().round(3),
        "1X @rpm": g.apply(lambda d: d.loc[d["amp_1x"].idxmax(), "rpm"], include_groups=False),
        "1Y max": g["amp_1y"].max().round(3),
        "1Y @rpm": g.apply(lambda d: d.loc[d["amp_1y"].idxmax(), "rpm"], include_groups=False),
    })
    print(out.to_string())


def phase_sweep(seg_name, lo, hi):
    start, stop = bounds[seg_name]
    s = f.iloc[start:stop]
    s = s[s["valid"] & (s["rpm"] >= lo) & (s["rpm"] <= hi)]
    if s.empty:
        print(f"  {seg_name} {lo}-{hi}: no valid samples")
        return
    for probe in ("1x", "1y"):
        ph = np.unwrap(np.deg2rad(s[f"phase_{probe}"].to_numpy()))
        ph = np.rad2deg(ph)
        print(
            f"  {seg_name:<22} {lo}-{hi} rpm  {probe.upper()}: "
            f"phase {ph[0]:7.1f} -> {ph[-1]:7.1f}  (swing {ph[-1] - ph[0]:+7.1f} deg), "
            f"amp {s[f'amp_{probe}'].iloc[0]:.3f} -> {s[f'amp_{probe}'].iloc[-1]:.3f}, "
            f"peak {s[f'amp_{probe}'].max():.3f}"
        )


band_peaks("Coast-up (startup)")
band_peaks("Coast-down (shutdown)")

print("\n=== phase behaviour through the response regions ===")
for seg in ("Coast-up (startup)", "Coast-down (shutdown)"):
    for lo, hi in ((900, 1500), (2200, 2900), (3000, 3600)):
        phase_sweep(seg, lo, hi)

print("\n=== coast-up vs coast-down amplitude at matched speeds (1X) ===")
up = f.iloc[bounds["Coast-up (startup)"][0]: bounds["Coast-up (startup)"][1]]
down = f.iloc[bounds["Coast-down (shutdown)"][0]: bounds["Coast-down (shutdown)"][1]]
for speed in (500, 1000, 1250, 1500, 2000, 2500, 3000, 3500):
    u = up.iloc[(up["rpm"] - speed).abs().argsort()[:5]]
    d = down.iloc[(down["rpm"] - speed).abs().argsort()[:5]]
    print(
        f"  {speed:>5} rpm   up 1X {u['amp_1x'].mean():.3f} / 1Y {u['amp_1y'].mean():.3f}   "
        f"down 1X {d['amp_1x'].mean():.3f} / 1Y {d['amp_1y'].mean():.3f}   "
        f"ratio 1X {u['amp_1x'].mean() / max(d['amp_1x'].mean(), 1e-9):.2f}x"
    )

print("\n=== slow-roll vectors (300-700 rpm) ===")
for name in ("Coast-up (startup)", "Coast-down (shutdown)"):
    start, stop = bounds[name]
    s = f.iloc[start:stop]
    for probe in ("1x", "1y"):
        v = slow_roll_vector(
            s[f"z_{probe}"].to_numpy(complex), s["rpm"].to_numpy(float), 300, 700,
            s["valid"].to_numpy(bool),
        )
        amp, phase = to_amp_phase(np.array([v]))
        print(f"  {name:<24} {probe.upper()}: {amp[0]:.3f} mil pp @ {phase[0]:.0f} deg")

print("\n=== gap voltage (shaft centreline position) ===")
for probe in ("1x", "1y"):
    g = f[f"gap_{probe}"]
    print(
        f"  {probe.upper()}: start {g.iloc[0]:.3f} V, at peak speed {g.iloc[bounds['At-speed dwell'][0]]:.3f} V, "
        f"end {g.iloc[-1]:.3f} V, total change {g.iloc[-1] - g.iloc[0]:+.3f} V "
        f"({(g.iloc[-1] - g.iloc[0]) / 0.2:+.1f} mil at 200 mV/mil)"
    )

print("\n=== sampling ===")
dt = f["timestamp"].diff().dt.total_seconds()
print(f"  interval: min {dt.min():.2f} s, median {dt.median():.2f} s, max {dt.max():.2f} s")
print(f"  samples closer together than 0.5 s: {(dt < 0.5).sum()}")
print(f"  duplicate rpm+timestamp pairs: {f.duplicated(subset=['timestamp']).sum()}")
print(f"  total record {(f['timestamp'].iloc[-1] - f['timestamp'].iloc[0])}")

print("\n=== flagged samples ===")
for code in ("K25", "BMA", "A1H"):
    m = f["flag_1x"].str.contains(code) | f["flag_1y"].str.contains(code)
    if m.any():
        print(f"  {code}: {m.sum()} samples, rpm {f.loc[m, 'rpm'].min():.1f}-{f.loc[m, 'rpm'].max():.1f}")

print("\n=== speed cross-check: probe channels vs keyphasor channel ===")
from rotordyn.adre import load_channel  # noqa: E402

for name in ("CT2B_BRG1X.csv", "CT2B_BRG1Y.csv"):
    ch = load_channel(name)
    probe_speed = ch.samples["Speed(P)"].to_numpy(float)
    agree = np.allclose(probe_speed, f["rpm"].to_numpy(float), equal_nan=True)
    print(f"  {name}: Speed(P) matches keyphasor Speed -> {agree}; "
          f"Speed(S) unique values {np.unique(ch.samples['Speed(S)'].to_numpy(float))[:5]}")

print("\n=== phase swing after slow-roll compensation ===")
from rotordyn.model import PolarModel, ViewSettings  # noqa: E402

model = PolarModel(run)
for seg in ("Coast-up (startup)", "Coast-down (shutdown)"):
    for lo, hi in ((900, 1500), (2200, 2900)):
        r = model.compute(ViewSettings(segment=seg, slow_roll=True, rpm_min=lo, rpm_max=hi))
        for probe, amp, ph in (("1X", r.amp_1x, r.phase_1x), ("1Y", r.amp_1y, r.phase_1y)):
            if amp.size == 0:
                continue
            unwrapped = np.rad2deg(np.unwrap(np.deg2rad(ph)))
            print(
                f"  {seg:<22} {lo}-{hi} rpm {probe}: swing {unwrapped[-1] - unwrapped[0]:+7.1f} deg, "
                f"peak {amp.max():.3f} mil pp"
            )

print("\n=== the 1X notch on coast-up ===")
n = up[(up["rpm"] > 1050) & (up["rpm"] < 1350) & up["valid"]]
print(n[["rpm", "amp_1x", "phase_1x", "amp_1y", "phase_1y"]].iloc[::2].round(2).to_string())
