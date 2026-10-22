# MDS Engineer — how it works (plain language)

**MDS** here means **Machinery Diagnostics Service**: the Bently Nevada field
role that reviews vibration data, names the malfunction, scores how bad it is,
and tells the customer what to do.

Rotor Buddy is a checker. It walks Kiran Toram’s list and writes **ok / watch /
alarm / skipped**. The MDS agent is the next step: it reads that checklist and
writes the report an MDS engineer would send — condition, diagnosis,
confidence, what would confirm it, and recommendations.

It does **not** call a chatbot. The rules are ordinary Python so you can audit
the call. It still does **not** replace looking at polar, Bode, orbit and
waterfall.

---

## How to run it

```
python main.py --mds new_1_1_5.csv
```

Same three-file form as Rotor Buddy (sync timebase, async timebase, static
Tabular List):

```
python main.py --mds new_1_1_5.csv async_data.csv static_1.csv
```

`--mds` also writes the Buddy checklist. You get four files in `outbox/`:

| File | What it is |
| --- | --- |
| `mds_report.txt` | The engineer letter (send this, or the PDF) |
| `mds_report.pdf` | Same letter, paginated |
| `rotor_buddy.txt` | Full per-bearing checklist |
| `rotor_buddy.pdf` | Checklist plus graphs |

Small channel files can be reviewed in the browser: `python main.py --web`,
then open the **MDS Report** tab.

---

## What the agent does (the MDS loop)

1. **Data quality first.** Swapped probes, dirty timebases, 5 m vs 9 m scale,
   wrong XY pairing. If those alarm, the machine is **unqualified** and
   amplitudes are not used for a mechanical call.
2. **Observations.** Speed range, ISO zone at the high-speed dwell (FSNL
   proxy), documented slow-roll vectors, every watch/alarm that is not wiring.
3. **Diagnosis.** A catalog of malfunctions an MDS engineer actually names:

   | Call | What Rotor Buddy evidence it needs |
   | --- | --- |
   | Oil whirl / fluid instability | Check 15 alarm (0.30–0.48X at speed) |
   | Radial rub | Shutdown 0.5X (check 11 alarm) or elevated 0.5X at speed |
   | Bowed / thermally bowed rotor | Circular slow-roll 1X (check 16); higher confidence if startup ≠ shutdown |
   | Thermal sensitivity | 1X walk at constant speed (check 12) |
   | Preload / misalignment | Flat orbit and/or high 2X (check 13), bad centreline lift (check 14) |
   | At-speed change | Soak-to-soak Direct/1X/phase step (check 9) |
   | High relative vibration | ISO 20816-2 zone C or D (check 8) — severity, not a root cause |
   | Residual unbalance | Only if ISO is high **and** nothing else is on the table |

   Each call has a **confidence** (high / moderate / low), the bearings it
   showed up on, how to **confirm** it on the plots, and how to **rule it out**.

4. **Condition.**

   | Rating | Meaning |
   | --- | --- |
   | **Satisfactory** | ISO A/B, no malfunction above threshold |
   | **Restricted** | Something to investigate (whirl/rub screen at ISO A/B, bow, thermal, preload, ISO C, soak step) |
   | **Unsatisfactory** | ISO D, or high-confidence whirl/rub that is already ISO C |
   | **Unqualified** | Fix the probes before diagnosing the rotor |

5. **Recommendations.** Priority 1 = before the next start / before trusting
   the numbers. Priority 2 = confirm on plots or at the next outage.
   Priority 3 = baseline and trend. The agent will not recommend a balance
   shot while bow, rub or whirl is still on the table.

---

## What it will not do

| The agent does this | You still do this |
| --- | --- |
| Names the likely malfunction from the checklist | Confirms it on polar, Bode, orbit, waterfall |
| Treats high-speed dwell as FSNL | Confirms the machine was really no-load |
| Flags a whirl-band number | Opens the waterfall in ADRE |
| Calls circular slow-roll 1X a bow candidate | Decides bow vs runout, and whether it is getting worse |
| Writes a customer-style letter | Signs the report and talks to the plant |

It cannot see MW, valve position, oil temperature, or last month’s dump.
Those stay in the **Limitations** section on every report.

---

## Later (this is a long process)

Still to add, in the same spirit — documented rules, not a chatbot:

- Site/OEM limits instead of only ISO 20816-2
- Seismic and thrust probes
- Multi-run bow/whirl trending
- Optional customer notes / machine type on the command line
- A real waterfall picture in the MDS PDF
- HelpBot / hosted Diagnostics tab driven from this report instead of a fake scorer
