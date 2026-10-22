"""Plain-text description of a loaded run.

Kept free of any interface dependency so the desktop, Dash and Gradio front
ends can all show the same text.
"""

from __future__ import annotations

import numpy as np

from .adre import STATUS_CODES
from .dataset import RunData
from .vectors import KEYPHASOR_DEG, PROBE_1X_DEG, PROBE_1Y_DEG


def describe_run(run: RunData) -> str:
    """What was loaded, how it was classified, and what the data says."""
    frame = run.frame
    lines = [
        "SOURCE FILES",
        *(f"  {role:<10} {name}" for role, name in run.source_files.items()),
        "",
        "PROBES  (degrees clockwise from Top Dead Centre)",
        f"  1X         {run.probe_1x_name} at {PROBE_1X_DEG:+.0f}\u00b0",
        f"  1Y         {run.probe_1y_name} at {PROBE_1Y_DEG:+.0f}\u00b0",
        f"  Keyphasor  {run.keyphasor_name} at {KEYPHASOR_DEG:+.0f}\u00b0",
        "",
        "RECORD",
        f"  {len(frame)} samples, {np.nanmin(run.rpm):.0f}-{np.nanmax(run.rpm):.0f} {run.speed_unit}",
        f"  {frame['timestamp'].iloc[0]} to {frame['timestamp'].iloc[-1]}",
        f"  amplitude in {run.amp_unit}",
        "",
        "SEGMENTS",
    ]
    for name, (start, stop) in run.segment_bounds().items():
        speeds = run.rpm[start:stop]
        lines.append(
            f"  {name:<24} samples {start:>4}-{stop - 1:<4} "
            f"{np.nanmin(speeds):.0f}-{np.nanmax(speeds):.0f} {run.speed_unit}"
        )
    if run.flag_counts:
        lines += ["", "INSTRUMENT STATUS CODES"]
        for code, count in run.flag_counts.items():
            lines.append(f"  {code:<6} {count:<5} {STATUS_CODES.get(code, 'unknown code')}")

    evidence = run.rotation_evidence()
    if evidence:
        lines += [
            "",
            "ROTATION DIRECTION EVIDENCE",
            f"  median (1Y phase - 1X phase) = {evidence['median_1y_minus_1x_deg']:.0f}\u00b0",
            f"  mean forward (CCW) orbit {evidence['mean_ccw_magnitude']:.3f} "
            f"vs reverse (CW) {evidence['mean_cw_magnitude']:.3f}",
            f"  CCW dominates in {evidence['fraction_ccw_dominant'] * 100:.0f}% of "
            f"{evidence['n_samples']:.0f} strong-signal samples",
        ]
    return "\n".join(lines)
