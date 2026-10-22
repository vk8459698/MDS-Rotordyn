# Rotor Buddy — how it works (plain language)

Rotor Buddy is a checker, not a chatbot. You give it vibration data from ADRE.
It walks through the same list an analyst would (Kiran Toram, 29 Aug 2026) and
writes a report: **ok**, **watch**, **alarm**, or **skipped**.

It does **not** replace an engineer looking at polar, Bode, orbit and waterfall
plots. It flags what to look at first.

---

## How to run it

In a terminal, from the RotorDyn folder:

```
python main.py --buddy new_1_1_5.csv
```

Or all three exports from the same run (sync timebase, async timebase, static
Tabular List) in one report:

```
python main.py --buddy new_1_1_5.csv async_data.csv static_1.csv
```

Order does not matter. Sync drives 1X / whirl / criticals; async drives probe
noise; static Avg Gap drives shaft centreline.

The report appears on screen and is saved as `outbox/rotor_buddy.txt`
plus a graphed PDF `outbox/rotor_buddy.pdf` (findings table, Bode, polar,
whirl band, orbits). The PDF is the one to send; the text file is the
full checklist with every bearing.

The big file (`new_1_1_5.csv`, about 1.5 GB) must be run this way. Do not upload
it in the website. Smaller channel files can also be checked in the browser:
`python main.py --web`, then open the **Rotor Buddy** tab.

The first run on the big file takes about a minute. After that the program
saves a small summary file, so the next run is much faster.

---

## What the computer does with the file

The dump is thousands of **timebase** snapshots: for each bearing probe, at
each speed, a short picture of how the shaft wobbled for a few turns.

Rotor Buddy:

1. Reads those snapshots one at a time (so a 1.5 GB file does not fill memory).
2. For each snapshot, measures overall vibration and the usual orders: **1X**
   (once per turn), **2X**, **0.5X**, and the band where oil whirl lives
   (~0.3–0.48X).
3. Pairs the probes that belong together (BRG1X with BRG1Y, and so on).
4. Splits the run into **slow roll** (about 200–700 rpm), **startup**,
   **full speed**, and **shutdown**.
5. Runs the checks below on each bearing.

Seismic probes and thrust probes are ignored. Only the XY proximity probes on
the bearings are used.

---

## The checks, in the same order as the scope

### 1. Swapped probes

**Ask:** Did someone mix up X and Y?

**What the code does:** A healthy shaft circles the same way it rotates
(forward). If X and Y are swapped, that circle looks like it is going
backwards. The code checks that at slow speed and again at running speed. It
also notes which “clock quadrant” the 1X arrows sit in.

**Alarm** if the orbit looks backwards at *both* slow roll and full speed.

**Not done automatically:** the shaft-centreline “mirror image” test. That plot
needs gap voltage, which is not in the timebase dump. An engineer still looks
at the centreline tab. For a machine turning counter-clockwise the journal
should move **right and up** as speed rises; clockwise is **left and up**.

### 2. Noise on the probes

**Ask:** Is the signal spiky or dirty?

**What the code does:** On each timebase it looks for sudden spikes (points far
outside the normal wiggle) and a “peakiness” number (crest factor).

**Watch / alarm** if many snapshots are spiky. Those amplitudes should not be
trusted until someone inspects the timebase.

### 3. Wrong cable length (5 m vs 9 m)

**Ask:** Is one probe on a 5-metre system and the other on a 9-metre system?

**What the code does:** At slow roll, X and Y should read about the same size.
If the scale is wrong, one probe often reads about **twice** the other.

**Alarm** if X is more than about 1.6 times Y (or the other way around). The
code cannot see the cable labels; it only sees this 2× symptom.

### 4. Are X and Y from the same bearing?

**Ask:** Did we pair the wrong two channels?

**What the code does:** Two probes 90° apart should differ by about **90° in
phase**. It checks that on the startup and a short stretch at speed — not on
the whole file. It also checks that if X is mostly 1X vibration, Y is too. If
one probe is 1X and the other is 0.5X or 0.47X, the pairing is suspect.

**Alarm** if phase is not near 90°, or the two probes are dominated by
different frequencies.

### 5 and 6. Critical speeds (first and second)

**Ask:** At which speeds does the rotor go through resonance?

**What the code does:** On the startup (and shutdown) it looks for a **hump in
1X amplitude** that also has a **large phase change** (the Bode “roll”). Those
are the same clues you use on polar, Bode and 1X orbit plots. The lowest such
speed is called first critical; the next is second.

The code does not draw the plots. It applies the same rules to the numbers.
You still confirm on the Bode and polar screens. A very well-damped hump can
be missed.

### 8. Is FSNL vibration too high? (ISO 20816-2)

**Ask:** At full speed, no load, is vibration in a bad ISO zone?

**What the code does:** There is no “MW” or “load” in the file, so it treats
**near maximum speed** as FSNL. It takes overall vibration (Direct) and
compares it to ISO 20816-2 for a 3600 rpm gas turbine:

- **Zone A** — typical of a new machine  
- **Zone B** — acceptable long term  
- **Zone C** — limited operation  
- **Zone D** — damage likely  

On the dump we ran, every bearing was **zone A** (well below the limit).

### 9. Did vibration change while sitting at speed?

**Ask:** Did Direct, 1X, 2X or phase walk during a soak, or between two soaks?

**What the code does:** The file has two trips to full speed. The code looks
inside each soak, then compares the first soak to the second.

**Alarm** if 1X changes a lot between soaks (on this machine BRG2 went from
about 0.5 mil to 1.5 mil). That is a real at-speed change, not a startup peak.

### 10. Slow roll (and shutdown)

**Ask:** What is the slow-roll vector, and can we subtract it?

**What the code does:** It averages 1X at 200–700 rpm and **writes the numbers
down** (amplitude and phase for X and Y). That is the runout you subtract
before reading unbalance on a polar/Bode.

The code documents it. You still switch on **Slow-roll compensation** in the
plot tool. It also notes whether a shutdown actually reached slow roll.

### 11. Startup vs shutdown — rub or bow?

**Ask:** Did the critical speed or vibration change coming down? Is there rub?

**What the code does:** It compares the startup critical to the shutdown
critical (speed and size). Rub often shows extra **0.5X** on the way down.

**Alarm** if 0.5X grows clearly on shutdown. A circular slow-roll orbit (bow)
is covered in check 16.

### 12. Thermal sensitivity

**Ask:** While the machine sits at speed and warms up, does the 1X vector walk?

**What the code does:** It watches 1X size and phase during the long full-speed
stretch.

**Watch** if amplitude or phase moves a lot at constant speed. That is the
usual thermal / alignment soak signature.

### 13. Preload and misalignment

**Ask:** Does the orbit look squashed? Is 2X high?

**What the code does:** A round orbit is normal unbalance. A flat orbit and a
strong **2X** are clues for preload or misalignment.

**Watch** if the orbit is very flat, or 2X is more than about half of 1X.

### 14. Shaft centreline across bearings

**Ask:** Do all journals lift the same way?

**What the code does:** **It cannot do this on the big timebase file.** That
plot needs the DC gap voltage, which is in a Tabular List export, not in the
waveform dump. The report is marked **skipped** and tells you to use the Shaft
centreline screen once gap data is available.

### 15. Waterfall — oil whirl / extra frequencies

**Ask:** Is there vibration below running speed (subsync) or well above it?

**What the code does:** It does not draw a waterfall. It measures the same
ingredients: energy around **0.38X** (oil whirl/whip), **0.5X**, and higher
orders, **at full speed** so a brief blip during startup does not set off an
alarm.

**Alarm** if that whirl band stays large compared with 1X at speed (BRG3 and
BRG4 on the dump we ran). An engineer should then open the real waterfall.

### 16. Slow-roll orbit shape — bowed rotor

**Ask:** At slow roll, is the orbit a clean circle? Does the next bearing show
the same thing? Does it get worse from run to run?

**What the code does:** A bent rotor still looks like a circle at slow speed,
with a real 1X. The code measures how round that orbit is and how big the 1X
is, then prints the **neighbouring bearing** at the same speed so you can see
if the bow is along the whole shaft.

**Watch** if the slow-roll orbit is round and 1X is big enough to matter (on
this dump, BRG2 was the standout). It counts how many startup/shutdown cycles
are in the file. It does **not** yet trend “getting worse over months” — that
needs several dumps compared by a person.

---

## What the report is not

| Rotor Buddy does this | An engineer still does this |
| --- | --- |
| Flags backwards orbits, 2× scale, wrong pairing | Looks at the actual polar and orbit |
| Picks likely critical speeds | Confirms on Bode / polar |
| Scores FSNL against ISO | Confirms the machine was really no-load |
| Flags a strong whirl-band number | Opens the waterfall in ADRE |
| Flags a circular slow-roll 1X | Decides bow vs runout, and whether it is getting worse |
| Skips centreline on this dump | Reads SCL once gap data is exported |

Item 7 on the original list is blank and is not used.

---

## What we already saw on this machine (`new_1_1_5.csv`)

- Probes look **correctly wired** (not swapped, not 5 m vs 9 m).
- FSNL vibration is **ISO zone A** (not excessive).
- **BRG3 and BRG4** have a lasting below-running-speed family — check for oil
  whirl on the waterfall.
- **BRG2** 1X jumped a lot between the two times it sat at full speed.
- **BRG2** slow-roll orbit is circular and larger than the bearing next to it
  — bow is possible; confirm on the orbit plot.

---

## Next step: the MDS Engineer report

Rotor Buddy is the checklist. To turn it into a diagnosis with recommendations
(the job an MDS engineer actually sends the customer), run:

```
python main.py --mds new_1_1_5.csv async_data.csv static_1.csv
```

That writes `outbox/mds_report.pdf` as well as the Buddy files. See [`MDS.md`](MDS.md).
