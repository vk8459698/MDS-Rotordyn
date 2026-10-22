# Everything, from scratch

This document assumes you know **nothing** about rotating machinery, vibration
analysis, or this codebase. It explains the physical problem first, then the
maths, then every file of code and why it exists.

Read it top to bottom. Each section only uses ideas from the sections above it.

**Contents**

1. [What problem are we solving?](#1-what-problem-are-we-solving)
2. [The machine and its sensors](#2-the-machine-and-its-sensors)
3. [What "amplitude" and "phase" actually mean](#3-what-amplitude-and-phase-actually-mean)
4. [Why two probes, and why 90° apart](#4-why-two-probes-and-why-90-apart)
5. [What a polar plot is](#5-what-a-polar-plot-is)
6. [What the assignment asked for](#6-what-the-assignment-asked-for)
7. [The data files](#7-the-data-files)
8. [The maths, built up from nothing](#8-the-maths-built-up-from-nothing)
9. [How we worked out which way the shaft turns](#9-how-we-worked-out-which-way-the-shaft-turns)
10. [Slow-roll compensation](#10-slow-roll-compensation)
11. [The code, file by file](#11-the-code-file-by-file)
12. [What happens when you drag the slider](#12-what-happens-when-you-drag-the-slider)
13. [The tests](#13-the-tests)
14. [How to run everything](#14-how-to-run-everything)
15. [Glossary](#15-glossary)

---

## 1. What problem are we solving?

A gas turbine has a **rotor**: a long steel shaft with blades on it, spinning at
thousands of revolutions per minute. It is held up by bearings at each end.

No rotor is perfectly balanced. There is always slightly more mass on one side
than the other. When it spins, that extra mass throws the shaft outwards, so
instead of spinning perfectly on its axis, the shaft **wobbles** in a small
circle inside its bearing. That wobble is what we call vibration.

A small wobble is normal. A big wobble destroys machines worth millions of
pounds. So every large turbine has sensors watching the shaft, and engineers
look at the data to answer questions like:

- Is the wobble growing?
- At which speeds does it get worse?
- Is the cause unbalance, or misalignment, or the shaft rubbing on something?

The single most useful picture for answering those questions during a startup or
shutdown is called a **polar plot**. Building an interactive polar plot tool is
what this project is.

---

## 2. The machine and its sensors

### The proximity probe

A **proximity probe** (also called a displacement probe or "prox probe") is a
small sensor screwed through the machine casing so its tip points at the shaft,
with a gap of a millimetre or so. It does not touch the shaft. It emits a
high-frequency electrical field and measures how much of it the steel shaft
absorbs, which tells it **how far away the shaft surface is**.

Output is a voltage. The standard scaling is **200 millivolts per mil**, where
a "mil" is one thousandth of an inch (~0.0254 mm). So:

- shaft sits still → steady voltage
- shaft wobbles → voltage wiggles up and down

Crucially, a probe only measures distance **along its own line of sight**. It is
blind to motion sideways across its face. Think of it as measuring the *shadow*
of the shaft's movement on the probe's axis.

### The two probes on this machine

There are two probes on the bearing we are looking at, named **1X** and **1Y**:

```
                    TDC (Top Dead Centre = 12 o'clock)
                             |
                  1Y  \      |      /  1X
                    45°\     |     /45°
                        \    |    /
                         \   |   /
                          \  |  /
                        ( the shaft )
```

- **1X** is mounted 45° to the **right** of the top.
- **1Y** is mounted 45° to the **left** of the top.

45 + 45 = 90, so the two probes are at right angles to each other. That is
deliberate and it matters enormously — see section 4.

### The keyphasor

The third sensor is the **keyphasor**, abbreviated **KPH**. It is the same kind
of probe, but it does not point at the smooth part of the shaft. It points at a
**notch cut into the shaft** (or a piece of reflective tape).

Every time the shaft completes one revolution, the notch passes the keyphasor
and the sensor produces one sharp pulse. So the keyphasor gives us two things:

1. **Speed** — count pulses per minute and you have RPM.
2. **A timing reference** — one moment per revolution that we can call "the
   start of the lap".

That second one is the important one, and section 3 explains why.

Think of a running track. The keyphasor is the start/finish line. Without it you
can say "the runner is fast". With it you can say "the runner passes the water
station 4 seconds after crossing the start line" — a *position within the lap*,
not just a speed.

---

## 3. What "amplitude" and "phase" actually mean

### The raw signal

Suppose the shaft centre is moving in a small circle. Probe 1X sees the distance
to the shaft grow and shrink once per revolution, so its voltage traces out a
sine wave:

```
voltage
   ^
   |     .--.              .--.              .--.
   |    /    \            /    \            /    \
   |---/------\----------/------\----------/------\------->  time
   |  /        \        /        \        /        \
   | '          '--.--'          '--.--'          '--.
   |
   |  ^                 ^                 ^
   |  keyphasor pulse   keyphasor pulse   keyphasor pulse
```

The instrument (a Bently Nevada ADRE system) does the hard work for us. It
filters this signal to keep **only the component that repeats exactly once per
revolution**, throws away everything else, and reports two numbers.

### Amplitude

**Amplitude** is how big the wiggle is: the distance from the lowest point of
the wave to the highest point. That is why the units in our data are
`mil pp` — *mils, peak to peak*. A value of 1.0 mil pp means the shaft is
sweeping across a total of one thousandth of an inch.

### Phase

**Phase** is *when in the revolution* the biggest wiggle happens.

This is the concept people find slippery, so here it is concretely. The signal
repeats every revolution, so "when" is measured in **degrees of shaft rotation**
starting from the keyphasor pulse:

```
        keyphasor                          peak of the vibration signal
        pulse                              (shaft closest to the probe)
          |                                        |
          v                                        v
  --------+----------------------------------------+------------->
          |<--------------- 120 degrees ---------->|
          |                                        |
          |<-------------- one full revolution = 360 degrees ---->|
```

Here the phase is **120°**. It means: every revolution, 120° of rotation after
the notch passes the keyphasor, the shaft reaches its closest point to this
probe.

Two things to be clear about:

- Phase is measured as a **lag**. Bigger phase number = the peak happens *later*
  in the revolution. This is the Bently Nevada convention and it is what our
  data uses.
- Phase is only meaningful **because** the keyphasor exists. Without a
  once-per-revolution marker, there is no "start of lap" to measure from.

### Why the pair of numbers is called a vector

Amplitude and phase together are exactly like **a length and a direction**. So
instead of two separate numbers, engineers think of it as one arrow:

```
            amplitude = how long the arrow is
            phase     = which way the arrow points
```

This single arrow is called the **1X vector** (the "1X" means once-per-
revolution, i.e. synchronous with the shaft). Everything in this project is
about manipulating these arrows.

---

## 4. Why two probes, and why 90° apart

One probe gives you a shadow. Shadows lose information:

```
   Shaft moving in a circle           What probe 1X sees
   (viewed down the shaft)            (its shadow on the probe axis)

          .-''-.
        .'      '.                     <-------->
       :          :         ---->      just a back-and-forth
       :          :                    line segment
        '.      .'
          '-..-'
```

From that one shadow you cannot tell whether the shaft was moving in a circle, a
squashed ellipse, or straight back and forth along the probe axis. All three
produce the same shadow.

Two probes at right angles fix that completely. If you know the shadow on the
horizontal axis **and** the shadow on the vertical axis, you know the full
two-dimensional position at every instant. Same idea as saying "I know the
x-coordinate and the y-coordinate, therefore I know the point".

That is the key fact this whole project rests on:

> **1X and 1Y being exactly 90° apart means their two readings completely
> describe the shaft's motion. Nothing is missing.**

And because nothing is missing, we can *calculate* what a probe mounted anywhere
else would have read. That is the whole "transducer rotation" feature.

---

## 5. What a polar plot is

### The idea

During a startup, the machine passes through hundreds of different speeds. At
each speed, the instrument gives us one 1X vector per probe — one arrow.

A **polar plot** draws all those arrows on one circular chart, but only their
tips, joined up in speed order:

- **distance from the centre** = amplitude at that speed
- **angle around the circle** = phase at that speed

```
                     0°
                      |
                  ....|....
              ..'     |     '..
           .'         |         '.
     270°--+----------+----------+--90°
           '.         |         .'      <- each dot is ONE SPEED
              '..     |     ..'            joined to the next
                  ''''|''''
                     180°
```

So one dot = one speed. Drag your eye along the curve and you are watching the
vibration change as the machine accelerates.

### What you look for

When a rotor passes through a **critical speed** (a resonance — a speed at which
the rotor naturally likes to vibrate, like pushing a swing at just the right
rhythm), two things happen together:

1. amplitude rises to a peak then falls again, and
2. phase rotates through roughly **180°**.

On a polar plot that combination draws an unmistakable **loop**. Spotting loops
is the main reason the plot exists.

### How it differs from the other two plots

| Plot | What it shows |
| --- | --- |
| **Orbit plot** | The actual path the shaft traces in one or two revolutions, at *one fixed speed*. A snapshot. |
| **Bode plot** | Amplitude vs speed on one graph, phase vs speed on another. Two separate line charts. |
| **Polar plot** | Amplitude *and* phase together, one dot per speed, over the whole speed range. |

The tool builds the polar plot (required) and the Bode plot (added, because
resonances are easier to *find* on a Bode plot and easier to *interpret* on a
polar plot).

---

## 6. What the assignment asked for

Five things:

1. Load the data and work out how amplitude, phase and speed are represented.
2. Use the keyphasor channel for speed and the phase reference.
3. Draw a polar plot for 1X and one for 1Y.
4. Add a control where the user types an angle from 0° to 360°, and both plots
   update to show **what the probes would read if they were mounted at that
   different position instead**.
5. Make it a real interactive application, not saved images.

Plus a README, a note on assumptions, and a written observation of what the data
shows.

Point 4 is the interesting one and it is where the maths lives.

---

## 7. The data files

Three files were supplied: `CT2B_BRG1X.csv`, `CT2B_BRG1Y.csv`, `KPH.csv`.

### Surprise 1: they are not CSV files

Despite the `.csv` extension they are **UTF-16LE encoded** and **tab-separated**.
UTF-16 stores every character in two bytes, so if you open one as ordinary text
you see a NUL byte between every letter:

```
b'C\x00H\x00#\x00\t\x00C\x00h\x00a\x00n\x00n\x00e\x00l\x00'
   C     H     #    tab    C     h     a     n     n     e     l
```

The reader tries UTF-16, then UTF-8, then cp1252, and rejects any attempt that
leaves NUL bytes behind.

### Surprise 2: there are two tables in each file

```
(blank lines)
CH#  Channel Name  Machine Name  Status  Angle  Direction  ...   <- table 1 header
1    BRG1X         Turbine       OK      45°    Right      ...   <- table 1: ONE row
(blank line)
CH#  Channel Name  Sample#  Sample Cause  Date  Speed(P) ...     <- table 2 header
1    BRG1X         3296     DR-T          ...                    <- table 2: 1051 rows
1    BRG1X         3297     DR-T          ...
...
```

The first table is **metadata about the sensor** — this is where we learn that
BRG1X is at "45° Right" and its amplitude unit is "mil pp". The second table is
the actual measurements.

The reader finds both by looking for the two lines beginning `CH#`, rather than
hard-coding line numbers.

### Surprise 3: numbers have letters stuck to them

Real cells from the file:

```
0.083K25        280BMA        2FNX        -9.847
```

Those trailing letters are **status codes** from the instrument, glued straight
onto the number with no separator. If you call Python's `float()` on
`"0.083K25"` it throws an error, and if you use `pandas.to_numeric(errors="coerce")`
you get `NaN` — the value is silently thrown away.

On this dataset that would have deleted the entire tail of the shutdown.

So every numeric cell is split into a value and a flag:

```python
split_value("0.083K25")   ->  (0.083, "K25")
split_value("280BMA")     ->  (280.0, "BMA")
split_value("-9.847")     ->  (-9.847, "")
```

The flag goes into a companion column (e.g. `1XAmplitude__flag`) so nothing is
lost.

### What the codes mean

Nothing in the assignment explains them. They were worked out by looking at
**where** each one appears:

| Code | Appears | Conclusion |
| --- | --- | --- |
| `K25` | only on the first 2 samples, where speed jumps 0 → 62 rpm | Keyphasor speed changed >25% between samples. Confirmed by the reference image in the assignment, which prints "KEYPHASOR CHANGE BY MORE THAN 25%". |
| `BMA` | only on **phase** cells, only where amplitude is 0.036–0.097 mil | Below Minimum Amplitude — too little signal for the phase to mean anything. |
| `A1H` | only below 60 rpm at the very end of the shutdown | Below the minimum speed for valid 1X processing. |
| `FNX` | only on a channel we do not use | nX filter not tracking. |

Each conclusion is falsifiable — if `BMA` appeared on a high-amplitude sample,
the explanation would be wrong. It never does.

### The columns we actually use

From the probe files:

| Column | Meaning |
| --- | --- |
| `Sample#` | Sequential ID. Identical across all three files, so we join on it. |
| `Date` | Timestamp. |
| `1XAmplitude` | The amplitude of the once-per-rev vibration, in mil pp. |
| `1X Phase` | The phase lag of it, in degrees. |
| `Direct` | Total unfiltered vibration (all frequencies). Not used for polar plots. |
| `Avg Gap` | The DC voltage — average shaft position. Used in the observations. |

From the keyphasor file the `Speed` column, which is the RPM.

---

## 8. The maths, built up from nothing

This is the core of the project. Everything here builds on the previous step.

### Step 1: an arrow is a complex number

A complex number `a + bi` is just an arrow on a flat plane: `a` across, `b` up.
Every arrow has a **length** and an **angle**, and there is a shorthand for
writing it that way:

```
   z = r · e^(iθ)         means:  an arrow of length r pointing at angle θ
```

You do not need to understand `e^(iθ)` deeply. Just remember two rules:

- **Multiplying** two complex numbers multiplies their lengths and **adds** their
  angles. So multiplying by `e^(iθ)` = "rotate by θ".
- **Adding** two complex numbers adds them tip-to-tail like arrows.

That second rule is why we use complex numbers here: it means vibration vectors
can be added and subtracted like ordinary arrows, which is exactly the physics.

### Step 2: turning a measurement into an arrow

Our measurement is amplitude (peak-to-peak) and phase lag. We convert:

```python
Z = (amplitude_pp / 2) * exp(-1j * radians(phase_lag))
```

Two details:

- **Divide by 2** because peak-to-peak is the *full* swing, but the arrow length
  should be the radius, i.e. half of it.
- **Minus sign** on the phase because phase is a *lag*. A bigger lag means later
  in the revolution, which corresponds to the arrow being rotated backwards.

Going back the other way:

```python
amplitude_pp = 2 * abs(Z)
phase_lag    = (-angle(Z)) mod 360
```

That is `to_phasor` and `to_amp_phase` in `rotordyn/vectors.py`. They are exact
inverses of each other, and there is a test proving it.

The formal statement of what this arrow means: the shaft's displacement towards
the probe at time τ after the keyphasor pulse is

```
displacement(τ) = Re{ Z · e^(i·Ω·τ) }
```

where Ω is the rotation rate. Multiplying by `e^(i·Ω·τ)` spins the arrow round
as time passes, and `Re{...}` takes its shadow on the horizontal axis. The peak
occurs exactly `phase_lag` degrees of rotation after the pulse, which is what we
wanted.

### Step 3: setting up a coordinate system

We need to give every probe position a number. The export describes them as
"45° Right" and "45° Left", so we match that:

> **θ = degrees from Top Dead Centre, counted positive going clockwise
> (i.e. positive = towards the right), as seen in a fixed view of the machine.**

That gives:

```
   1X        θ = +45°
   1Y        θ = -45°   (same as 315°)
   Keyphasor θ = +100°
```

The unit vector pointing outwards at angle θ, in a normal (right, up) picture
frame, is:

```python
u(θ) = ( sin θ , cos θ )
```

Check it: `u(0°) = (0, 1)` points straight up = TDC. Correct.
`u(90°) = (1, 0)` points right. Correct — 90° clockwise from up is right.

And the two probe directions really are perpendicular:

```
u(45°)  · u(-45°)  =  sin45·sin(-45) + cos45·cos(-45)
                   =  -0.5 + 0.5  =  0        ✓
```

### Step 4: the projection formula (the important bit)

Here is the reasoning in full.

**(a)** The shaft centre moves around in a 2D plane. Call its synchronous motion
`r(τ)`.

**(b)** A probe at angle θ measures the component of that motion along its own
direction — the dot product `r(τ) · u(θ)`.

**(c)** The 1X reading is therefore `r(τ) · u(45°)`, and the 1Y reading is
`r(τ) · u(-45°)`.

**(d)** Since `u(45°)` and `u(-45°)` are perpendicular unit vectors, they form a
complete coordinate system for the plane. Knowing a vector's components along
both **is** knowing the vector. So `Z_1X` and `Z_1Y` are the complete
description of the motion.

**(e)** Therefore a probe at *any* angle θ reads the component of that same
motion along `u(θ)`, and we can write `u(θ)` in terms of the two probe
directions:

```
u(θ) = cos(θ − 45°) · u(45°)  +  cos(θ + 45°) · u(-45°)
```

(The coefficients are just the cosines of the angles between the directions —
standard vector decomposition on an orthonormal basis.)

**(f)** Taking components along `u(θ)` and using the fact that everything here is
linear, the arrow a probe at angle θ would read is:

```
   Z(θ) = cos(θ − 45°) · Z_1X  +  cos(θ + 45°) · Z_1Y
```

**That one line is the entire transducer-rotation feature.**

### Step 5: sanity-checking the formula

Always check a formula at values where you already know the answer.

| Put in | Formula gives | Should be | ✓ |
| --- | --- | --- | --- |
| θ = 45° (the 1X probe itself) | `cos(0)·Z_1X + cos(90)·Z_1Y` = `Z_1X` | the measured 1X reading | ✓ |
| θ = −45° (the 1Y probe itself) | `cos(−90)·Z_1X + cos(0)·Z_1Y` = `Z_1Y` | the measured 1Y reading | ✓ |
| θ = 225° (directly opposite 1X) | `cos(180)·Z_1X + cos(270)·Z_1Y` = `−Z_1X` | the same signal inverted, because a probe on the far side sees the shaft move away when the near probe sees it move closer | ✓ |
| shaft moving purely up and down | probe at 90° (horizontal) reads exactly 0 | a horizontal probe cannot see vertical motion | ✓ |

Every one of those is a test in `tests/test_vectors.py`.

### Step 6: why not just add the angle to the phase?

There is a well-known rule of thumb: *"moving a probe by 30° just shifts its
phase reading by 30°."*

That rule is only true if the shaft is moving in a perfect **circle**. If the
orbit is an **ellipse** (which real ones are, because bearings are stiffer
vertically than horizontally), the amplitude genuinely changes depending on
where you stand, and the simple rule gives the wrong answer.

The projection formula handles both. When the orbit happens to be circular it
automatically reduces to the simple phase shift — there is a test for that too:

```python
def test_circular_orbit_is_a_pure_phase_shift(self, theta):
    ...
    assert amp == pytest.approx(2 * radius)                    # amplitude unchanged
    assert phase == pytest.approx((high_spot - theta) % 360.0) # phase shifts by the angle moved
```

and a companion test proving an *elliptical* orbit does **not** behave that way,
so we know the formula is not secretly just doing a phase shift.

### Step 7: rotating the pair

The user's control rotates **both** probes together by `r` degrees clockwise:

```python
theta_x = 45  + r        # where the 1X probe ends up
theta_y = -45 + r        # where the 1Y probe ends up
rotated_x = probe_phasor(z_1x, z_1y, theta_x)
rotated_y = probe_phasor(z_1x, z_1y, theta_y)
```

Why rotate both rather than move one probe to an absolute angle? Because if you
put both probes at the same absolute angle they would be in the same place and
the two plots would be identical, which is useless. Rotating the pair keeps them
90° apart — still a valid probe pair — and `r = 0` reproduces the measured data
exactly. (The underlying function takes an absolute angle, so the other reading
is one call away if it was what the brief meant.)

A nice consequence you can see in the app: set the control to **90°** and the 1Y
plot becomes exactly what the 1X plot was, because the 1Y probe has been moved
into the 1X probe's old position.

---

## 9. How we worked out which way the shaft turns

The assignment deliberately does not say whether the shaft turns clockwise or
counter-clockwise. Rather than guess, we measured it from the data — twice.

### Method 1: which probe sees the bump first

Picture the heavy spot on the shaft going past the probes. The probe it reaches
*later* reports a *bigger* phase lag, because more degrees of rotation elapse
between the keyphasor pulse and that probe's peak.

Across 734 good samples, **1Y reads a median of 97° more lag than 1X**. Close to
the 90° physical separation, and positive. So the heavy spot reaches 1X first
and 1Y second: rotation goes *from* 1X *towards* 1Y.

1X is at 45° right of top; 1Y is at 45° left of top. Going from right-of-top to
left-of-top the short way passes over the top — that is **counter-clockwise**.

### Method 2: splitting the orbit into two spinning circles

Any elliptical orbit can be written as the sum of two circles: one spinning
counter-clockwise ("forward") and one spinning clockwise ("reverse"). This is
what `precession_split` does.

A rotor being pushed round by its own unbalance whirls in the **same** direction
it rotates. So whichever circular component is bigger tells you the rotation
direction.

Result: the counter-clockwise component averages **0.396** against **0.046** for
clockwise, and it is the bigger one in **100%** of samples.

Both methods agree, and both agree with the `CCW ROTATION` marking printed on
the example polar plot included in the assignment. `python main.py --summary`
reprints these numbers.

---

## 10. Slow-roll compensation

### The problem

At very low speed — say 400 rpm — there is essentially no real vibration.
Centrifugal force scales with speed squared, so at 400 rpm it is negligible.

And yet the probe still reads about **0.4 mil pp**. Why?

Because the probe is not really measuring vibration; it is measuring *distance to
the shaft surface*. And a real shaft is not a perfect cylinder:

- it may be very slightly bent (**mechanical runout**)
- its magnetic properties vary around the circumference from machining or
  handling, which fools the probe (**electrical runout**)

So even a shaft turning slowly and perfectly steadily produces a once-per-
revolution signal. This is called the **slow-roll vector**, and it is a fixed
arrow that gets added to every genuine reading at every speed.

### The fix

Measure the arrow at low speed, then subtract it from every reading:

```python
true_vibration = measured_vector − slow_roll_vector
```

That is `slow_roll_vector()` in `vectors.py` — it just averages the arrows
inside a chosen speed window (default 300–700 rpm).

### Why it mattered enormously here

On the raw data, the phase in this dataset only swings 40–50° through the
suspected resonances. A real critical speed should swing about 180°. So on the
raw data it looks like there is no critical speed at all.

After subtracting the slow-roll vector, the 1Y probe sweeps **+186°** through
900–1500 rpm. The resonance was there all along; the runout arrow was masking it.

There is a second, more striking symptom. Between 1057 and 1146 rpm the raw 1X
amplitude *falls* from 0.51 to 0.24 mil while 1Y *rises* from 0.76 to 0.88. A
resonance cannot make one probe quieter and the perpendicular probe louder. What
was happening is that the growing response arrow at 1X rotated into direct
opposition with the runout arrow and cancelled it:

```
   runout arrow  ---------->
   response arrow          <----------
   what the probe reads:   -->              (nearly nothing)
```

Anyone reading the raw 1X trace alone would call 1150 rpm a quiet spot. It is
the opposite. This is exactly why the slow-roll toggle is in the interface.

---

## 11. The code, file by file

### Design principle

The code is split so that **nothing that knows about maths knows about windows,
and nothing that knows about windows knows about file formats**:

```
   files on disk
        |
        v
   adre.py      "read the weird file format"
        |
        v
   dataset.py   "combine 3 channels into 1 aligned table"
        |
        v
   vectors.py   "pure maths on arrows"           <-- no pandas, no GUI
        |
        v
   model.py     "settings in -> plot-ready arrays out"   <-- no GUI
        |
        +--------------------+
        v                    v
   gui.py               export.py
   (interactive)        (saves PNG files)
```

The reason for the split is testability. Because `model.py` has no GUI
dependency, the test suite and the headless PNG export run **the exact same code
path** the interactive app uses. If the maths breaks, the tests catch it, not the
user.

---

### `rotordyn/adre.py` — reading the files

Handles all three surprises from section 7.

| Function | Job |
| --- | --- |
| `_read_lines` | Try UTF-16, UTF-8, cp1252 in turn; reject any result containing NUL bytes. |
| `split_value` | `"0.083K25"` → `(0.083, "K25")`. The regex is a number followed by an optional letter-group. |
| `_parse_angle` | `("45°", "Right")` → `+45.0`; `("45°", "Left")` → `−45.0`. |
| `load_channel` | Finds both `CH#` headers, parses both tables, splits every numeric column into value + flag, parses timestamps, returns an `AdreChannel`. |
| `load_directory` | Loads every `.csv` in a folder, skipping ones that do not parse. |

An `AdreChannel` holds the metadata (name, mounting angle, units) plus the
sample table.

---

### `rotordyn/dataset.py` — assembling one run

**Channel classification.** It works out which file is which from the *metadata*,
not the filenames:

- the keyphasor is the channel that has a `Speed` column and no `1XAmplitude`
- the two probes are told apart by the sign of their mounting angle

Rename the files however you like; it still works. It also refuses to continue if
the two probes are not 90° apart, because the projection maths would be invalid.

**Alignment.** All three files share the same `Sample#` values, so the three
tables are joined on that column. The result is one table with one row per
sample:

```
sample | timestamp | rpm | amp_1x | phase_1x | amp_1y | phase_1y | z_1x | z_1y | valid
```

`z_1x` and `z_1y` are the complex arrows, precomputed once. `valid` is `False`
when the instrument flagged that sample.

**Segmentation.** The record is one hour and 45 minutes long: 24 minutes of
startup, 61 minutes sitting at full speed, 20 minutes of shutdown.

Plotting the middle hour is pointless — at constant speed all the dots land on
top of each other. So `segment_bounds()` cuts the record at the first and last
time the machine reaches 99% of its top speed, giving:

```
Coast-up (startup)    samples    0-448
At-speed dwell        samples  448-637
Coast-down (shutdown) samples  637-1050
Full record           samples    0-1050
```

**`rotation_evidence()`** computes the numbers from section 9.

---

### `rotordyn/vectors.py` — the maths

Pure functions, numpy only, no pandas and no GUI. Everything from section 8
lives here:

| Function | What it does |
| --- | --- |
| `to_phasor` | amplitude + phase → complex arrow |
| `to_amp_phase` | complex arrow → amplitude + phase |
| `unit_vector` | angle → direction vector `(sin θ, cos θ)` |
| `probe_phasor` | **the projection formula** — what a probe at any angle reads |
| `rotate_probe_pair` | moves both probes by `r` degrees, returns both readings and both new positions |
| `precession_split` | splits an orbit into forward and reverse circles (used for the rotation-direction proof) |
| `slow_roll_vector` | averages the arrows in a low-speed window |
| `describe_position` | `120.0` → `"120° right of TDC"`, for the status bar and titles |

The file's docstring is the single place where all the conventions are written
down, so there is one authority rather than comments scattered around.

---

### `rotordyn/model.py` — settings in, arrays out

Two small data holders and one class:

- **`ViewSettings`** — a plain record of everything the user can change: which
  segment, the rotation angle, whether to hide flagged samples, whether to apply
  slow-roll compensation and over what window, the speed filter, and two display
  toggles.
- **`ViewResult`** — the arrays the plots need, plus the two probe positions and
  a `status_line()` for the bottom of the window.
- **`PolarModel.compute()`** — does the work, in this order:

```
1. take the rows for the chosen segment
2. build a keep/drop mask from the speed filter and the flagged-sample toggle
3. pull out the complex arrays z_1x, z_1y
4. if slow-roll is on: measure the runout arrows and subtract them
5. rotate the probe pair by the requested angle  (the projection formula)
6. convert back to amplitude + phase
7. work out a shared radial scale so both plots are comparable
```

Step 4 happens before step 5, and that is deliberate: subtraction and projection
are both linear operations, so the order does not change the answer — but doing
it first is simpler. That reasoning is written in a comment at the point where it
happens.

---

### `rotordyn/plots.py` — drawing

`PolarPlot` wraps one polar axes and updates it **in place**. That matters for
responsiveness: when you drag the slider, we do not rebuild the figure, we swap
the coordinates inside existing objects.

Choices worth explaining:

- **`set_theta_direction(-1)`** — makes phase increase *clockwise*, i.e. against
  the direction of rotation. That is the standard convention, and it means the
  drawn arrow points at where the heavy spot physically is.
- **`LineCollection` coloured by speed** — the track is drawn as many small
  segments, each coloured by the RPM at that point, so you can read speed off the
  colour rather than hunting for text labels.
- **Speed labels at evenly spaced *speeds*, not evenly spaced samples** — the
  instrument samples faster when speed changes quickly, so evenly spaced samples
  would bunch all the labels into the slow parts.
- **Shared radial scale** across the two plots so 1X and 1Y are directly
  comparable.
- **Fixed margins instead of `tight_layout`** — polar axes push their title above
  the ring of tick labels, and automatic layout fought with the figure-level
  captions.

`BodePlot` draws amplitude vs speed and phase vs speed. `_break_wraps` inserts a
`NaN` wherever phase jumps between 359° and 1°, so matplotlib lifts the pen
instead of drawing a vertical line across the whole chart.

---

### `rotordyn/gui.py` — the window

Plain Tkinter, which ships with Python, plus matplotlib's Tk canvas.

Layout:

```
+-------------------------------------------------------------+
| Transducer rotation:  [======O=================] [ 90 ] deg |
| Jump to: [0°][45°][90°][135°][180°][225°][270°][315°] [Reset]|
+-------------------------------------------------------------+
| Data selection    | Slow-roll compensation | Display         |
| segment, speed    | on/off, rpm window     | labels, machine |
| range, hide flags |                        | view            |
+-------------------------------------------------------------+
| [Polar plots] [Bode plot] [Dataset & conventions]            |
|                                                             |
|            (the matplotlib figure)                          |
|                                                             |
+-------------------------------------------------------------+
| 1X probe 135° right of TDC | 1Y probe 45° right | 447 points |
+-------------------------------------------------------------+
```

Three implementation details that are not obvious:

**Debouncing.** Dragging a slider fires dozens of events per second. Redrawing on
every one would make the app stutter. So each event cancels any pending redraw and
schedules a new one 20 ms later — only the last one in a burst actually runs:

```python
def _schedule_refresh(self) -> None:
    if self._redraw_job is not None:
        self.after_cancel(self._redraw_job)
    self._redraw_job = self.after(20, self.refresh)
```

**Packing order.** Tk gives space to widgets in the order they are packed, so the
status bar is packed *before* the expanding notebook, otherwise the notebook eats
the whole window and pushes the status bar off the bottom. Same reason the
matplotlib toolbar is created before the canvas inside each tab.

**Window size.** The default size is computed from the actual screen rather than
hard-coded, because two side-by-side polar plots want more height than a typical
laptop panel has.

---

### `rotordyn/export.py` and `main.py`

`export.py` renders the same figures without a screen, using matplotlib's `Agg`
backend. It exists so the images in `README.md` and `OBSERVATIONS.md` are
generated by the same code as the app, not drawn separately.

`main.py` is the entry point:

```bash
python main.py                     # interactive app
python main.py --data some/folder  # read the exports from elsewhere
python main.py --summary           # print what was found, then exit
python main.py --export figures    # write the PNGs, then exit
```

`analysis.py` at the top level is separate: it prints the numbers quoted in
`OBSERVATIONS.md` so every claim in the write-up can be re-checked.

---

## 12. What happens when you drag the slider

End to end, for one slider movement:

```
1. Tk fires the Scale's command callback with the new value.

2. _on_scale() copies the value into the text box and calls
   _schedule_refresh(), which cancels any pending redraw and books a new
   one for 20 ms later.

3. 20 ms passes with no further movement, so refresh() runs.

4. refresh() collects every control's current value into a ViewSettings.

5. PolarModel.compute() runs the seven steps from section 11:
   pick segment -> filter -> (optional slow-roll subtraction) ->
   apply the projection formula at 45+r and -45+r -> back to amp/phase.

6. Each PolarPlot gets the new arrays and updates its existing artists
   in place. The Bode plot gets the same arrays sorted by speed.

7. draw_idle() asks matplotlib to repaint when it next gets a chance.

8. The status bar text is replaced with the new probe positions.
```

The whole cycle takes a few milliseconds for ~450 points, which is why it feels
instant.

---

## 13. The tests

104 tests. The role this was written for is a *test* engineer role, so the test
suite is part of the deliverable rather than an afterthought.

### `tests/test_vectors.py` — the maths

The important ones are the properties that must hold no matter what the data is:

| Test | Why it matters |
| --- | --- |
| round trip amplitude/phase → arrow → amplitude/phase | the conversion is lossless |
| rotation by 0° and by 360° change nothing | catches sign and wrap-around errors |
| the probes stay exactly 90° apart at every rotation | the projection formula is only valid if they do |
| rotating 90° swaps the two probes | an easy-to-verify physical fact |
| a probe 180° away reads the negated signal | ditto |
| a circular orbit gives a pure phase shift | agrees with the textbook rule of thumb |
| an elliptical orbit does **not** | proves the code is not secretly just shifting phase |
| straight-line motion projects to zero on its perpendicular | a probe cannot see motion across its face |
| reversing both readings flips the precession direction | validates the rotation-direction proof |

### `tests/test_pipeline.py` — parsing and assembly

Parsing tests build their **own** synthetic export file in a temp folder, so they
run on a machine that does not have the dataset. They cover value/flag splitting,
UTF-16 *and* UTF-8 files, Right/Left angle signs, and malformed files being
rejected rather than silently mis-parsed.

The dataset tests check channel identification, alignment, segment bounds, and
most importantly:

```python
def test_zero_rotation_reproduces_the_raw_columns(self, run):
    # rotation = 0 must give back exactly what the instrument exported
    assert np.allclose(result.amp_1x, run.frame["amp_1x"], atol=1e-9)
```

If the maths ever drifts, that test fails immediately.

### `tests/test_gui_smoke.py` — the interface

Builds the real window (hidden), then checks that typed angles are accepted,
nonsense input is ignored, out-of-range angles are clamped, every segment
renders, and moving the control actually changes both plots.

It uses one Tk window for the whole session because creating and destroying many
Tk roots in one process is unreliable — that was found by running the suite
repeatedly and seeing intermittent failures.

---

## 14. How to run everything

```bash
# once
python -m pip install -r requirements.txt

# the app
python main.py

# see what is in the data without opening a window
python main.py --summary

# regenerate all the figures
python main.py --export figures

# run the tests
python -m pytest tests -q

# reproduce the numbers quoted in OBSERVATIONS.md
python analysis.py
```

### What to try in the app first

1. Leave the rotation at 0°, segment on **Coast-up**. This is the measured data.
2. Tick **Subtract slow-roll vector**. Watch the loops appear — that is section 10
   happening in front of you.
3. Click the **90°** preset. The 1Y plot becomes what the 1X plot was, because
   the 1Y probe has been moved into the 1X probe's old position.
4. Switch to the **Bode plot** tab to see the same data as amplitude and phase
   against speed. The resonance peaks are easier to find here.
5. Switch the segment to **Coast-down** and compare. Below 2000 rpm it is three
   to five times smaller than the startup — the finding described in
   `OBSERVATIONS.md`.

---

## 15. Glossary

| Term | Meaning |
| --- | --- |
| **1X** | Once per revolution. "The 1X vector" = the part of the vibration that repeats exactly once per shaft turn. Also the name of one of the probes, confusingly. |
| **Amplitude (mil pp)** | Size of the vibration, peak to peak, in thousandths of an inch. |
| **Bode plot** | Amplitude vs speed and phase vs speed, as two line charts. |
| **Critical speed** | A speed at which the rotor resonates. Amplitude peaks and phase swings ~180°. |
| **Gap voltage** | The steady DC part of a probe's output — the average distance to the shaft. Shows where the shaft is sitting, not how it vibrates. |
| **Keyphasor (KPH)** | Sensor giving one pulse per revolution. Provides speed and the phase reference. |
| **mil** | One thousandth of an inch, ~0.0254 mm. |
| **Orbit plot** | The shaft's actual path over one or two revolutions at a fixed speed. |
| **Phase (lag)** | How many degrees of rotation after the keyphasor pulse the vibration peaks. |
| **Phasor** | A vibration vector written as a complex number. |
| **Polar plot** | Amplitude as radius, phase as angle, one point per speed. |
| **Precession** | The direction the shaft centre whirls around its orbit. Forward = same as rotation. |
| **Proximity probe** | Non-contact sensor measuring distance to the shaft. |
| **Runout** | The once-per-rev signal a probe reads that is not vibration — from the shaft being slightly bent or magnetically uneven. |
| **Slow roll** | Very low speed, where the reading is essentially all runout. |
| **Synchronous** | At exactly the rotation frequency. Same as 1X. |
| **TDC / BDC** | Top / Bottom Dead Centre — 12 o'clock and 6 o'clock on the machine. |
| **Transducer** | Generic word for a sensor. Here, one of the probes. |
