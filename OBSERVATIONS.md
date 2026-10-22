# What the data shows

Numbers below come from `python analysis.py`; figures from
`python main.py --export figures`.

## The run

One continuous 1 h 45 min record, 1051 samples, 30 April 2026. The machine
accelerates from rest to 3614 rpm in 24 minutes, holds ~3600 rpm for an hour,
then coasts down to standstill in 20 minutes. Both transients are usable, and
they do not agree with each other - which turns out to be the most interesting
thing in the dataset.

## Two resonances, and only one of them is obvious

The Bode view makes the response regions easy to find: a peak near **1200-1250
rpm** and a broader, larger one spanning **2450-2650 rpm**, with amplitude
climbing again above 3300 rpm towards operating speed.

On the **raw** vectors neither region looks convincing. Phase swings only about
40-50° through each, where a critical speed should sweep roughly 180°. That
changes once the slow-roll vector is removed: through 900-1500 rpm on coast-up,
1Y then sweeps **+186°** while its amplitude peaks - a textbook resonance. So
**the first critical is at approximately 1200-1250 rpm**, and it is only
readable after compensation. The 2200-2900 rpm region sweeps 84-111° after
compensation, so it is a real but more heavily damped second response rather
than a sharp critical.

This is the practical point of the exercise: on this machine the uncompensated
polar plot is dominated by runout and would lead you to conclude there is no
critical speed in the range at all.

## The 1X amplitude notch at 1150 rpm is vector cancellation, not a good rotor

Approaching the first critical on coast-up, raw 1X amplitude *falls* from 0.51
to 0.24 mil pp between 1057 and 1146 rpm while its phase swings 295° → 223°,
then amplitude climbs to 0.92 mil pp by 1250 rpm. Over exactly the same speeds
1Y rises smoothly, 0.76 → 1.33 mil pp, with almost no phase movement.

A resonance cannot make one probe quieter while the orthogonal probe gets three
times louder. What is happening is that the growing response vector at 1X is
rotating into opposition with the 0.40 mil pp runout vector already present
there and partly cancelling it. Slow-roll compensation removes the notch and
leaves a clean loop on both probes. Anyone reading the raw 1X trace alone would
call 1150 rpm a quiet spot; it is the opposite.

## Startup and shutdown disagree by a factor of 3-5 below 2000 rpm

At matched speeds, 1X amplitude on the way up against the way down:

| Speed | Coast-up | Coast-down | Ratio |
| --- | --- | --- | --- |
| 500 rpm | 0.40 | 0.13 | 3.2x |
| 1000 rpm | 0.67 | 0.13 | 5.4x |
| 1250 rpm | 0.92 | 0.24 | 3.9x |
| 2000 rpm | 0.50 | 0.13 | 4.0x |
| 2500 rpm | 1.17 | 0.60 | 1.9x |
| 3500 rpm | 1.06 | 1.05 | 1.0x |

The slow-roll vectors say the same thing: **0.397 mil pp at 279°** on 1X during
startup against **0.134 mil pp at 292°** during shutdown, with 1Y behaving
identically (0.437 @ 10° against 0.155 @ 28°). The low-speed vector is three
times smaller after the run than before it, at nearly the same angle.

That is the signature of a **bowed rotor that straightened out during the run**:
the shaft starts with a thermal bow left over from the previous shutdown, the
bow acts as a large synchronous forcing at every speed, and after an hour at
temperature it has largely gone. The two transients converge above 3000 rpm,
where genuine unbalance response dominates and the bow contributes
proportionally less. A pure mechanical unbalance would have produced matching
coast-up and coast-down plots; this one is a strong candidate for the "start on
turning gear for longer" conversation rather than a balance shot.

## Worth flagging: the 1Y gap voltage drift

The average gap voltage at 1Y moves from -10.30 V at rest, to -8.96 V at
operating speed, to **-12.57 V** at the end of the coast-down - a net change of
-2.27 V, about **11 mil** at the standard 200 mV/mil scale factor. Over the same
record 1X moves only -0.46 V (~2.3 mil).

An 11 mil shift is comparable to the whole diametral clearance of a typical
journal bearing, and the machine is at rest at both ends of the record, so this
is not a running position change. Some of it is casing and pedestal thermal
growth - the machine is hot at the end and cold at the start - but the fact that
the orthogonal probe barely moved is hard to explain that way. Before trusting
any centreline interpretation of this record I would check the 1Y proximitor
gap calibration and the probe mounting.

## Data-quality notes

* **The files are not CSV.** They are UTF-16LE, tab-delimited ADRE exports with
  a metadata table stacked above the sample table. Opening them as UTF-8 CSV
  produces garbage.
* **Numeric cells carry status codes with no separator** - `0.083K25`,
  `280BMA`, `2FNX`. A naive `float()` turns these into NaN and quietly drops
  them, which on this dataset would discard the entire tail of the coast-down.
  The tool splits value from flag and keeps both. Meanings, inferred from where
  they occur, are tabulated in the README; `BMA` appears only on phase cells
  where amplitude is under 0.1 mil pp, and `A1H` only below 60 rpm, so both are
  self-consistent.
* **Sampling is speed-triggered and irregular.** Intervals run from 0.02 s to
  20 s (median 2.7 s), with 245 samples less than half a second apart - two
  triggers, `DR-T` and `DT-T`, firing independently. Anything that assumes a
  uniform time base will be wrong. Sample numbers are shared across all three
  files, so the channels are joined on `Sample#`.
* **Redundant speed columns.** `Speed(P)` in both probe files matches the
  keyphasor `Speed` column on all 1051 samples, so the keyphasor channel is used
  as the brief asks, with the probe columns serving as a cross-check.
  `Speed(S)` is zero throughout.
* **The dwell is a third of the record.** Sixty minutes at constant speed
  contributes one dot to a polar plot but 190 samples. The tool separates it
  from the two transients rather than plotting them together.

## Summary

The rotor passes a lightly damped first critical at about 1200-1250 rpm and a
broader second response around 2450-2650 rpm, neither of which is readable
without slow-roll compensation. Vibration at operating speed is moderate and
steady (1X 1.09 mil pp mean, phase within 300-319° across the hour). The
dominant feature of the record is not a resonance at all - it is a large
low-speed vector present on startup and largely absent on shutdown, consistent
with a thermal bow that ran itself out. The 1Y gap voltage drift is the one
thing I would want an instrument check on before drawing further conclusions.

---

| Figure | Shows |
| --- | --- |
| `figures/polar_coast_up_rot0.png` | Coast-up, both probes, as measured |
| `figures/polar_coast_up_slowroll.png` | Same, slow-roll compensated - the loops appear |
| `figures/polar_coast_up_rot90.png` | Probe pair rotated +90°, demonstrating the control |
| `figures/bode_coast_up.png` | Coast-up amplitude and phase against speed |
| `figures/polar_coast_down_rot0.png` | Coast-down - visibly smaller than coast-up |
| `figures/bode_coast_down.png` | Coast-down amplitude and phase against speed |
