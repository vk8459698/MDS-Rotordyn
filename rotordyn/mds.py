"""MDS Engineer agent: diagnosis and recommendations from a Rotor Buddy report.

Rotor Buddy is a checker. This module is the engineer: it reads the checklist,
applies Bently Nevada Machinery Diagnostics Service methodology, and writes a
customer-facing report (condition, hypotheses, what would confirm them, and
what to do next).

It does not call an LLM. Rules are documented so a field engineer can audit
the call. It does not replace polar, Bode, orbit or waterfall review.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .buddy import BuddyReport, Finding, analyse_path, analyse_paths, analyse_run
from .dataset import RunData

_CONF_RANK = {"high": 0, "moderate": 1, "low": 2}
_FLAG = {"alarm", "watch"}
_QUALITY_CHECKS = {"1", "2", "3", "4"}
# Below this peak speed the "full-speed dwell" is really slow roll, so every
# at-speed check would be scored on the wrong part of the run.
MIN_RUNNING_RPM = 1500.0
_AT_SPEED_CHECKS = {"5", "6", "8", "9", "11", "12", "13", "14", "15"}
# When confidence ties, lead with the call that changes what the plant does next.
_LEAD = {
    "data_quality": 0,
    "fluid_instability": 1,
    "rub": 2,
    "high_relative_vibration": 3,
    "bowed_rotor": 4,
    "preload_misalignment": 5,
    "thermal_sensitivity": 6,
    "at_speed_change": 7,
    "residual_unbalance": 8,
}

# Bently-style malfunction catalog. Each rule maps Buddy check ids to a call
# an MDS engineer would actually write down.
_CATALOG = {
    "data_quality": {
        "name": "Instrumentation / data quality",
        "confirm": "Verify probe assignment, Proximitor/cable matching and pairing in ADRE before trusting amplitudes.",
        "rule_out": "Clean timebases, forward precession, ~90° XY phase and matched slow-roll scale.",
    },
    "fluid_instability": {
        "name": "Oil whirl / fluid-induced instability",
        "confirm": "Open the waterfall at speed: a locked 0.38–0.48X that grows with speed or load drop is whirl/whip.",
        "rule_out": "A brief startup blip, or energy that is 0.5X (rub/looseness) rather than the whirl band.",
    },
    "rub": {
        "name": "Radial rub",
        "confirm": "Waterfall 0.5X on shutdown, flattened/irregular orbits, and a shaft centreline that walks into a wall.",
        "rule_out": "Matching startup/shutdown 1X with no 0.5X family.",
    },
    "bowed_rotor": {
        "name": "Bowed / thermally bowed rotor",
        "confirm": "Circular slow-roll 1X orbit on adjacent bearings; shutdown slow-roll smaller than startup; bow shrinks after a hot soak.",
        "rule_out": "A mechanical unbalance would match coast-up and coast-down after slow-roll compensation.",
    },
    "thermal_sensitivity": {
        "name": "Thermal sensitivity / hot alignment",
        "confirm": "1X vector walks at constant speed while metal temperatures change; compare hot vs cold alignment.",
        "rule_out": "A step change with no soak (load swing, valve move) is not a thermal walk.",
    },
    "preload_misalignment": {
        "name": "Preload / misalignment",
        "confirm": "Flattened or banana orbit, elevated 2X, and a shaft centreline that does not lift as expected.",
        "rule_out": "A round 1X orbit with low 2X is not preload.",
    },
    "at_speed_change": {
        "name": "At-speed change (developing or process-driven)",
        "confirm": "Compare the two soaks on polar/Bode and check whether load, temperature or valve position moved.",
        "rule_out": "A startup peak that is gone at speed is not an at-speed change.",
    },
    "high_relative_vibration": {
        "name": "Elevated relative shaft vibration (ISO)",
        "confirm": "Confirm the dwell was really FSNL/full speed and that Direct is shaft-relative, not seismic.",
        "rule_out": "Zone A/B at a verified no-load dwell.",
    },
    "residual_unbalance": {
        "name": "Residual unbalance (candidate only)",
        "confirm": "Slow-roll-compensated polar: 1X loop, circular orbit, matching up/down. Then a balance shot.",
        "rule_out": "Bow, rub, whirl or preload evidence — do not balance those away.",
    },
}


@dataclass
class MdsContext:
    """Optional site facts the export does not contain."""

    machine: str = ""
    site: str = ""
    unit: str = ""
    job_type: str = ""
    customer_notes: str = ""
    machine_kind: str = "gas"


@dataclass
class Hypothesis:
    malfunction_id: str
    name: str
    confidence: str  # high | moderate | low
    bearings: list[str]
    summary: str
    evidence: list[str] = field(default_factory=list)
    confirm: str = ""
    rule_out: str = ""

    @property
    def rank(self) -> int:
        return _CONF_RANK.get(self.confidence, 9)


@dataclass
class Recommendation:
    priority: int  # 1 = before next start / before trusting data
    action: str
    why: str
    bearings: list[str] = field(default_factory=list)


@dataclass
class MdsReport:
    source: str
    job_type: str
    condition: str  # satisfactory | restricted | unsatisfactory | unqualified
    headline: str
    executive_summary: str
    data_quality: str
    quality_blocks_amplitudes: bool
    observations: list[str]
    hypotheses: list[Hypothesis]
    recommendations: list[Recommendation]
    limitations: list[str]
    notes: list[str] = field(default_factory=list)
    buddy: BuddyReport | None = field(default=None, repr=False, compare=False)
    context: MdsContext = field(default_factory=MdsContext)

    def format_text(self) -> str:
        lines = [
            "MACHINERY DIAGNOSTICS SERVICE REPORT",
            "Agent: MDS Engineer (RotorDyn)    Methodology: Bently Nevada MDS screening",
            f"Source: {self.source}",
            f"Job: {self.job_type}",
            f"Condition: {self.condition.upper()}",
            "",
            "1. EXECUTIVE SUMMARY",
            f"  {self.headline}",
            "",
        ]
        for para in _paragraphs(self.executive_summary):
            lines.append(f"  {para}")
            lines.append("")
        lines += ["2. DATA QUALITY", f"  {self.data_quality}", ""]
        if self.observations:
            lines.append("3. OBSERVATIONS")
            for item in self.observations:
                lines.append(f"  • {item}")
            lines.append("")
        lines.append("4. DIAGNOSIS")
        if not self.hypotheses:
            lines.append("  No malfunction signature rose above screening thresholds.")
            lines.append("")
        else:
            for i, hyp in enumerate(self.hypotheses, 1):
                brg = f" ({', '.join(hyp.bearings)})" if hyp.bearings else ""
                lines.append(f"  {i}. {hyp.name}{brg}  —  confidence {hyp.confidence}")
                lines.append(f"     {hyp.summary}")
                for ev in hyp.evidence:
                    lines.append(f"     - {ev}")
                if hyp.confirm:
                    lines.append(f"     Confirm: {hyp.confirm}")
                if hyp.rule_out:
                    lines.append(f"     Rule out: {hyp.rule_out}")
                lines.append("")
        lines.append("5. RECOMMENDATIONS")
        for rec in self.recommendations:
            brg = f" [{', '.join(rec.bearings)}]" if rec.bearings else ""
            lines.append(f"  P{rec.priority}{brg}  {rec.action}")
            lines.append(f"      {rec.why}")
        lines.append("")
        lines.append("6. LIMITATIONS")
        for item in self.limitations:
            lines.append(f"  • {item}")
        if self.notes:
            lines += ["", "NOTES", *[f"  {n}" for n in self.notes]]
        lines.append("")
        lines.append(
            "This is a screening diagnosis from ADRE exports. It is not a field sign-off. "
            "Confirm alarms on polar, Bode, orbit and waterfall before acting on the machine."
        )
        return "\n".join(lines).rstrip() + "\n"


def diagnose(buddy: BuddyReport, *, context: MdsContext | None = None) -> MdsReport:
    """Turn a Rotor Buddy checklist into an MDS engineer report."""
    ctx = context or MdsContext()
    findings = list(buddy.findings)
    low_speed = _never_reached_speed(buddy)
    if low_speed:
        findings = [f for f in findings if f.check_id not in _AT_SPEED_CHECKS]
    quality_flags = _of(findings, checks=_QUALITY_CHECKS, severities=_FLAG)
    quality_alarms = [f for f in quality_flags if f.severity == "alarm"]
    blocks = bool(quality_alarms)
    hypotheses = _hypotheses(findings, blocks)
    condition = "unqualified" if low_speed else _condition(findings, hypotheses, blocks)
    recs = _recommendations(findings, hypotheses, blocks, buddy)
    job = ctx.job_type or _job_type(buddy)
    machine = ctx.machine or _machine_label(buddy)
    headline, executive = _executive(buddy, machine, condition, hypotheses, blocks, quality_flags)
    data_quality = _quality_narrative(quality_flags, buddy)
    limitations = _limitations(buddy)
    if low_speed:
        coverage = (
            f"The record never reached running speed (peak {buddy.rpm_max:.0f} rpm, "
            f"{buddy.n_samples} aligned samples). At-speed checks — ISO, criticals, "
            "soak change, thermal walk, preload, centreline lift, waterfall — were withheld."
        )
        headline = (
            f"Unqualified — record stops at {buddy.rpm_max:.0f} rpm. "
            "Only slow-roll calls are valid; re-export the full run."
        )
        executive = coverage + " " + executive
        data_quality = coverage + " " + data_quality
        limitations.insert(0, "Slow-roll-only record: re-export the full time range from ADRE before an at-speed diagnosis.")
        recs.insert(
            0,
            Recommendation(
                priority=1,
                action="Re-export the full run from ADRE (whole time range, every proximity channel) and re-run --mds.",
                why="This export only covers slow roll, so it cannot show ISO severity, criticals, whirl or soak behaviour.",
            ),
        )
    return MdsReport(
        source=buddy.source,
        job_type=job,
        condition=condition,
        headline=headline,
        executive_summary=executive,
        data_quality=data_quality,
        quality_blocks_amplitudes=blocks,
        observations=_observations(buddy, findings),
        hypotheses=hypotheses,
        recommendations=recs,
        limitations=limitations,
        notes=list(buddy.notes),
        buddy=buddy,
        context=ctx,
    )


def _never_reached_speed(buddy: BuddyReport) -> bool:
    return buddy.rpm_max < MIN_RUNNING_RPM


def diagnose_run(run: RunData, *, context: MdsContext | None = None) -> MdsReport:
    """MDS review of one XY pair from a tabular / channel export."""
    return diagnose(analyse_run(run), context=context)


def diagnose_path(path: str | Path, *, progress: bool = False, context: MdsContext | None = None) -> MdsReport:
    return diagnose(analyse_path(path, progress=progress), context=context)


def diagnose_paths(
    paths: list[str | Path],
    *,
    progress: bool = False,
    context: MdsContext | None = None,
) -> MdsReport:
    """One MDS report from sync, async and/or static tabular exports."""
    return diagnose(analyse_paths(paths, progress=progress), context=context)


def _of(
    findings: list[Finding],
    *,
    checks: set[str] | None = None,
    severities: set[str] | None = None,
    bearings: set[str] | None = None,
) -> list[Finding]:
    out = []
    for finding in findings:
        if checks is not None and finding.check_id not in checks:
            continue
        if severities is not None and finding.severity not in severities:
            continue
        if bearings is not None and _bearing(finding) not in bearings:
            continue
        out.append(finding)
    return out


def _bearing(finding: Finding) -> str:
    named = finding.metrics.get("bearing") if finding.metrics else None
    if named:
        return str(named)
    if ":" in finding.title:
        return finding.title.split(":", 1)[0].strip()
    return ""


def _bearings(findings: list[Finding]) -> list[str]:
    seen: list[str] = []
    for finding in findings:
        name = _bearing(finding)
        if name and name not in seen:
            seen.append(name)
    return seen


def _job_type(buddy: BuddyReport) -> str:
    if buddy.rpm_min < 800 and buddy.rpm_max >= 2400:
        return "Startup / shutdown assistance"
    if buddy.rpm_min >= 2000:
        return "Steady-state / exception review"
    return "Machinery diagnostics review"


def _machine_label(buddy: BuddyReport) -> str:
    brg = ", ".join(buddy.bearings) if buddy.bearings else "unknown bearings"
    return f"{buddy.source} ({brg})"


def _worst_iso(findings: list[Finding]) -> str:
    order = {"A": 0, "B": 1, "C": 2, "D": 3, "?": -1}
    worst = "A"
    for finding in _of(findings, checks={"8"}):
        zone = str(finding.metrics.get("zone", "")).upper() if finding.metrics else ""
        if order.get(zone, -1) > order.get(worst, -1):
            worst = zone
    return worst


def _condition(findings: list[Finding], hypotheses: list[Hypothesis], blocks: bool) -> str:
    if blocks:
        return "unqualified"
    iso = _worst_iso(findings)
    rotor = [
        h
        for h in hypotheses
        if h.malfunction_id not in {"data_quality", "residual_unbalance", "high_relative_vibration"}
    ]
    severe = [
        h
        for h in rotor
        if h.malfunction_id in {"fluid_instability", "rub"} and h.confidence == "high"
    ]
    # ISO zone is severity. A whirl/rub screen at ISO A/B is investigate-now,
    # not a damage-likely machine. Unsatisfactory is reserved for zone D, or
    # whirl/rub that is already in the restricted ISO bands.
    if iso == "D" or (severe and iso in {"C", "D"}):
        return "unsatisfactory"
    if iso == "C" or rotor or severe:
        return "restricted"
    return "satisfactory"


def _quality_narrative(flags: list[Finding], buddy: BuddyReport) -> str:
    if not flags:
        n = sum(1 for f in buddy.findings if f.check_id in _QUALITY_CHECKS)
        return (
            f"Wiring, scale, pairing and probe-noise screens are clear "
            f"({n} checks across {len(buddy.bearings) or 1} bearing(s)). "
            "Amplitudes may be used for diagnosis."
        )
    alarms = [f for f in flags if f.severity == "alarm"]
    watches = [f for f in flags if f.severity == "watch"]
    parts = []
    if alarms:
        parts.append(
            "Do not trust amplitudes until instrumentation is verified: "
            + "; ".join(f.summary for f in alarms)
        )
    if watches:
        parts.append("Watch items: " + "; ".join(f.summary for f in watches))
    return " ".join(parts)


def _observations(buddy: BuddyReport, findings: list[Finding]) -> list[str]:
    items: list[str] = []
    items.append(
        f"Record: {buddy.n_samples} aligned samples, "
        f"{buddy.rpm_min:.0f}–{buddy.rpm_max:.0f} rpm, amplitude in {buddy.amp_unit}."
    )
    iso_bits = []
    for finding in _of(findings, checks={"8"}):
        zone = finding.metrics.get("zone") if finding.metrics else None
        amp = finding.metrics.get("amp") if finding.metrics else None
        if zone:
            bit = f"{_bearing(finding)} ISO {zone}"
            if amp is not None:
                bit += f" ({amp:.2f} {buddy.amp_unit} Direct)"
            iso_bits.append(bit)
    if iso_bits:
        items.append("Full-speed dwell vs ISO 20816-2 (FSNL proxy): " + "; ".join(iso_bits) + ".")
    crit_by_bearing: dict[str, str] = {}
    for finding in _of(findings, checks={"5", "6"}):
        if "critical" not in finding.summary.lower():
            continue
        brg = _bearing(finding)
        if brg not in crit_by_bearing or "second" in finding.summary.lower():
            crit_by_bearing[brg] = finding.summary
    for brg, summary in crit_by_bearing.items():
        items.append(f"{brg}: {summary}")
    for finding in _of(findings, checks={"10"}, severities={"info", "ok"}):
        items.append(finding.summary)
    for finding in findings:
        if finding.check_id in _QUALITY_CHECKS:
            continue
        if finding.severity in _FLAG:
            items.append(f"{finding.title}: {finding.summary}")
    skipped = [f for f in findings if f.severity == "skip"]
    if skipped:
        topics = sorted({f.title.split(":", 1)[-1].strip() for f in skipped})
        items.append("Skipped (missing data): " + "; ".join(topics[:6]) + ".")
    return items


def _hypotheses(findings: list[Finding], blocks: bool) -> list[Hypothesis]:
    hyps: list[Hypothesis] = []
    quality = _of(findings, checks=_QUALITY_CHECKS, severities=_FLAG)
    if quality:
        alarms = [f for f in quality if f.severity == "alarm"]
        hyps.append(
            _hyp(
                "data_quality",
                "high" if alarms else "moderate",
                quality,
                "Instrumentation problems can mimic or hide rotor malfunctions. "
                "Fix the data path before a balance shot or a mechanical call.",
            )
        )

    whirl = _of(findings, checks={"15"}, severities={"alarm"})
    if not whirl:
        whirl = [
            f
            for f in _of(findings, checks={"15"}, severities={"watch"})
            if "fluid" in f.title.lower() or "subsync" in f.summary.lower() or "whirl" in f.summary.lower()
        ]
    if whirl:
        conf = "high" if any(f.severity == "alarm" for f in whirl) else "moderate"
        if blocks:
            conf = "moderate" if conf == "high" else "low"
        hyps.append(
            _hyp(
                "fluid_instability",
                conf,
                whirl,
                "Persistent below-running-speed energy at speed is the oil-whirl / whip screen. "
                "It is not unbalance and will not trim out with a balance weight.",
            )
        )

    half_watch = [
        f
        for f in _of(findings, checks={"15"}, severities={"watch"})
        if "0.5" in f.summary or "0.5X" in f.summary or "rub" in f.summary.lower()
    ]
    rub = _of(findings, checks={"11"}, severities={"alarm"}) + half_watch
    if rub:
        conf = "high" if any(f.severity == "alarm" for f in rub) else "moderate"
        if blocks:
            conf = "moderate" if conf == "high" else "low"
        hyps.append(
            _hyp(
                "rub",
                conf,
                rub,
                "A 0.5X family that grows on shutdown, or a large critical shift coming down, "
                "is the classic radial-rub screen.",
            )
        )

    bow = _of(findings, checks={"16"}, severities=_FLAG)
    updown = _of(findings, checks={"11"}, severities={"watch"})
    if bow:
        same = set(_bearings(bow)) & set(_bearings(updown))
        conf = "high" if same else "moderate"
        if any("noisy" in f.summary.lower() for f in bow) and not same:
            conf = "low"
        if blocks:
            conf = "low"
        extra = updown if same else []
        hyps.append(
            _hyp(
                "bowed_rotor",
                conf,
                bow + extra,
                "A circular slow-roll 1X with a real amplitude is runout or bow. "
                "If startup and shutdown disagree, a thermal bow that relaxed during the soak is favoured over a fixed mechanical unbalance.",
            )
        )

    thermal = _of(findings, checks={"12"}, severities=_FLAG)
    if thermal:
        hyps.append(
            _hyp(
                "thermal_sensitivity",
                "moderate",
                thermal,
                "A 1X vector that walks at constant speed while the machine soaks is thermal / alignment sensitivity, not a sudden unbalance.",
            )
        )

    preload = _of(findings, checks={"13"}, severities=_FLAG)
    scl = _of(findings, checks={"14"}, severities=_FLAG)
    if preload:
        # Centreline is supporting evidence only when the orbit/2X screen also fired.
        # Buddy's "right-and-up" CCW rule flags the usual oil-film attitude (left
        # and up) as a watch — that alone is not a misalignment call.
        hyps.append(
            _hyp(
                "preload_misalignment",
                "moderate",
                preload + [f for f in scl if _bearing(f) in set(_bearings(preload))],
                "Flattened 1X orbits and high 2X are preload or misalignment. "
                "A journal that does not lift as expected on the centreline plot supports the same call.",
            )
        )

    soak = _of(findings, checks={"9"}, severities=_FLAG)
    if soak:
        hyps.append(
            _hyp(
                "at_speed_change",
                "high" if any(f.severity == "alarm" for f in soak) else "moderate",
                soak,
                "Direct/1X/phase changed at constant speed or between soaks. "
                "That is an at-speed event — thermal, process, or a developing fault — not a startup peak.",
            )
        )

    iso_bad = _of(findings, checks={"8"}, severities=_FLAG)
    if iso_bad:
        hyps.append(
            _hyp(
                "high_relative_vibration",
                "high" if any(f.severity == "alarm" for f in iso_bad) else "moderate",
                iso_bad,
                "Full-speed relative vibration is in ISO 20816-2 zone C or D. "
                "This is severity, not a root cause — pair it with the order that dominates.",
            )
        )

    rotor_ids = {
        "fluid_instability",
        "rub",
        "bowed_rotor",
        "preload_misalignment",
        "thermal_sensitivity",
    }
    has_rotor = any(h.malfunction_id in rotor_ids for h in hyps)
    if iso_bad and not has_rotor and not blocks:
        hyps.append(
            _hyp(
                "residual_unbalance",
                "low",
                iso_bad,
                "High 1X with a round orbit and no competing malfunction is a residual-unbalance candidate. "
                "Compensate slow-roll and read the polar before placing a weight.",
            )
        )

    hyps.sort(
        key=lambda h: (
            0 if blocks and h.malfunction_id == "data_quality" else 1,
            h.rank,
            _LEAD.get(h.malfunction_id, 9),
        )
    )
    return hyps


def _hyp(malfunction_id: str, confidence: str, evidence: list[Finding], summary: str) -> Hypothesis:
    spec = _CATALOG[malfunction_id]
    quotes = []
    for finding in evidence:
        bit = finding.summary
        if finding.details:
            bit = f"{finding.summary} ({finding.details[0]})"
        quotes.append(f"{finding.title}: {bit}")
    return Hypothesis(
        malfunction_id=malfunction_id,
        name=spec["name"],
        confidence=confidence,
        bearings=_bearings(evidence),
        summary=summary,
        evidence=quotes[:8],
        confirm=spec["confirm"],
        rule_out=spec["rule_out"],
    )


def _recommendations(
    findings: list[Finding],
    hypotheses: list[Hypothesis],
    blocks: bool,
    buddy: BuddyReport,
) -> list[Recommendation]:
    recs: list[Recommendation] = []
    ids = {h.malfunction_id: h for h in hypotheses}

    def add(priority: int, action: str, why: str, bearings: list[str] | None = None) -> None:
        recs.append(Recommendation(priority=priority, action=action, why=why, bearings=bearings or []))

    if blocks:
        add(
            1,
            "Stop mechanical calls until probe wiring, scale and pairing are verified in ADRE.",
            "Data-quality alarms (swapped XY, 5 m/9 m scale, mis-pairing or dirty timebases) "
            "can invert precession and double amplitudes.",
            ids.get("data_quality").bearings if "data_quality" in ids else [],
        )
    if "fluid_instability" in ids:
        h = ids["fluid_instability"]
        add(
            1,
            "Open the waterfall at speed on the flagged bearings; do not schedule a balance shot for this.",
            "Oil whirl/whip is fluid-induced. Check oil supply temperature and pressure, bearing clearance and load. "
            "A locked 0.38–0.48X that grows is whip.",
            h.bearings,
        )
    if "rub" in ids:
        h = ids["rub"]
        add(
            1,
            "Inspect for radial rub: 0.5X waterfall, orbit, seals/blade-tip clearance, shaft centreline into a wall.",
            "Rub is a contact problem. Balancing will not clear 0.5X.",
            h.bearings,
        )
    if "bowed_rotor" in ids:
        h = ids["bowed_rotor"]
        named = ", ".join(h.bearings) or "the flagged bearing"
        add(
            1,
            f"Treat {named} slow-roll 1X as bow until shutdown slow-roll and adjacent bearings say otherwise. Do not balance yet.",
            "A thermal bow often shrinks after a hot soak. A balance weight for a bow that then straightens over-corrects.",
            h.bearings,
        )
        add(
            2,
            "If the bow is thermal, extend turning-gear time before the next start and compare the next slow-roll vector.",
            "Repeatable startup-only 1X that collapses at temperature is the turning-gear conversation, not a trim shot.",
            h.bearings,
        )
    if "high_relative_vibration" in ids:
        h = ids["high_relative_vibration"]
        iso = _worst_iso(findings)
        if iso == "D":
            add(
                1,
                "ISO zone D — treat as damage-likely. Restrict operation and identify the dominant order before a return-to-service.",
                "ISO 20816-2 zone D is not a long-term running condition.",
                h.bearings,
            )
        else:
            add(
                2,
                "ISO zone C — limited operation. Plan an outage diagnosis; do not ignore a rising trend.",
                "Zone C is acceptable only for a limited time.",
                h.bearings,
            )
    if "at_speed_change" in ids:
        h = ids["at_speed_change"]
        add(
            2,
            "Explain the soak-to-soak 1X step: process/load, temperature, or a developing rotor fault.",
            "An at-speed change is not a startup peak. Overlay the two dwells on polar.",
            h.bearings,
        )
    if "thermal_sensitivity" in ids:
        h = ids["thermal_sensitivity"]
        add(
            2,
            "Hot vs cold alignment and casing growth: the 1X vector walked at constant speed.",
            "Thermal sensitivity is an alignment/support problem until proven otherwise.",
            h.bearings,
        )
    if "preload_misalignment" in ids:
        h = ids["preload_misalignment"]
        add(
            2,
            "Check alignment, piping strain and bearing preload. Confirm on orbit (flat/banana) and shaft centreline.",
            "High 2X and a flattened orbit are not unbalance.",
            h.bearings,
        )
    if "residual_unbalance" in ids:
        h = ids["residual_unbalance"]
        add(
            3,
            "If plots confirm a clean 1X unbalance after slow-roll compensation, plan a balance shot from the polar high spot.",
            "Only after bow, rub, whirl and preload are off the table.",
            h.bearings,
        )

    add(
        2,
        "Confirm every alarm and watch on polar, Bode, orbit and waterfall — the agent screens numbers, it does not look at the pictures.",
        "Rotor Buddy and this report apply documented thresholds to the FFT table. An MDS sign-off still reads the plots.",
        list(buddy.bearings),
    )
    add(
        3,
        "File this run as the baseline. The next dump is what shows a bow or whirl getting worse.",
        "Single-run screening cannot trend over months.",
        [],
    )
    recs.sort(key=lambda r: (r.priority, r.action))
    return recs


def _executive(
    buddy: BuddyReport,
    machine: str,
    condition: str,
    hypotheses: list[Hypothesis],
    blocks: bool,
    quality_flags: list[Finding],
) -> tuple[str, str]:
    iso = _worst_iso(buddy.findings)
    n_brg = len(buddy.bearings) or 1
    headline_bits = {
        "satisfactory": f"Satisfactory screen — {n_brg} bearing(s), ISO {iso}, no malfunction above threshold.",
        "restricted": f"Restricted — investigate before treating this as a routine run.",
        "unsatisfactory": f"Unsatisfactory — do not treat this as a clean return-to-service.",
        "unqualified": "Unqualified — fix instrumentation before any mechanical diagnosis.",
    }
    headline = headline_bits.get(condition, condition)
    if hypotheses and not blocks:
        primary = next((h for h in hypotheses if h.malfunction_id != "data_quality"), hypotheses[0])
        headline = f"{headline.split('—')[0].strip()} — primary screen: {primary.name.lower()} ({', '.join(primary.bearings) or 'machine'})."

    paras = [
        f"{machine} was reviewed over {buddy.rpm_min:.0f}–{buddy.rpm_max:.0f} rpm "
        f"({buddy.n_samples} aligned samples, {n_brg} proximity XY bearing(s)). "
        f"Overall condition from this screen is {condition.upper()}."
    ]
    if blocks:
        paras.append(
            "Data-quality alarms are in force. "
            + "; ".join(f.summary for f in quality_flags if f.severity == "alarm")
            + " Rotor-malfunction comments below are provisional until the probes are verified."
        )
    rotor = [h for h in hypotheses if h.malfunction_id != "data_quality"]
    if rotor:
        lead = rotor[0]
        brg = ", ".join(lead.bearings) if lead.bearings else "the machine"
        paras.append(
            f"Primary diagnosis: {lead.name} on {brg} (confidence {lead.confidence}). {lead.summary}"
        )
        others = rotor[1:3]
        if others:
            paras.append(
                "Also on the table: "
                + "; ".join(f"{h.name} ({', '.join(h.bearings) or 'machine'}, {h.confidence})" for h in others)
                + "."
            )
    else:
        paras.append(
            f"No rotor-malfunction signature rose above the screening thresholds. "
            f"Full-speed relative vibration is ISO 20816-2 zone {iso} "
            "(FSNL proxy = high-speed dwell; load is not in the export)."
        )
    paras.append(
        "This report is a pointer for an MDS engineer, not a customer sign-off. "
        "Confirm on polar, Bode, orbit and waterfall before acting."
    )
    return headline, " ".join(paras)


def _limitations(buddy: BuddyReport) -> list[str]:
    items = [
        "Load (MW), valve position and metal temperatures are not in the ADRE export. Full-speed dwell is used as an FSNL proxy.",
        "No historical baseline: bow or whirl getting worse over months still needs a person comparing dumps.",
        "Seismic, thrust and valve probes are not in this review — proximity XY bearings only.",
        "Waterfall / orbit conclusions are from FFT and orbit-aspect numbers, not from looking at the ADRE plots.",
        "ISO 20816-2 Table B.1 gas-turbine row is used unless the speed class says otherwise. Site/OEM limits may be tighter.",
    ]
    skipped = [f for f in buddy.findings if f.severity == "skip"]
    if any(f.check_id == "14" for f in skipped):
        items.append("Shaft centreline was skipped — export a Tabular List with Avg Gap.")
    if any(f.check_id in {"2", "15", "16"} for f in skipped):
        items.append("Some waveform checks were skipped — drop the Sync (and Async) timebase dump for the full set.")
    return items


def _paragraphs(text: str) -> list[str]:
    text = " ".join(text.split())
    # Split on sentence boundaries enough to keep the text report readable.
    parts = []
    buf = []
    for sentence in text.replace("  ", " ").split(". "):
        sentence = sentence.strip()
        if not sentence:
            continue
        if not sentence.endswith("."):
            sentence += "."
        buf.append(sentence)
        if len(" ".join(buf)) > 280:
            parts.append(" ".join(buf))
            buf = []
    if buf:
        parts.append(" ".join(buf))
    return parts or [text]
