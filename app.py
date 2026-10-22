"""Gradio front end, used to host the viewer on Hugging Face Spaces.

Hugging Face runs ``app.py`` for a Gradio Space, which is why this sits at the
repository root rather than under ``rotordyn/``.

This is the third interface over the same analysis core. It renders the exact
Plotly figures the Dash application does, from the same ``ViewSettings`` and
the same ``PolarModel``; only the widgets differ. Nothing in ``rotordyn/``
needed changing to add it.

Run it locally with ``python app.py``.
"""

from __future__ import annotations

from pathlib import Path

import gradio as gr
import numpy as np

from rotordyn.adre import load_channel
from rotordyn.dataset import RunData, build_run_from_channels
from rotordyn.model import PolarModel, ViewSettings
from rotordyn.summary import describe_run
from rotordyn.webplots import (
    TREND_CHANNELS,
    bode_figure,
    centreline_figure,
    empty_figure,
    polar_figure,
    trend_figure,
)

try:
    import spaces
except ImportError:  # local runs and the test suite; Spaces always has this
    class spaces:  # type: ignore[no-redef]
        @staticmethod
        def GPU(function=None, **_kwargs):
            if function is None:
                return lambda fn: fn
            return function

ROOT = Path(__file__).resolve().parent
BLANK = empty_figure("Upload ADRE exports to begin.")

OPTION_LABELS = {
    "hide": "Hide flagged samples",
    "slowroll": "Slow-roll compensation",
    "machine": "Machine view (0\u00b0 at probe)",
    "labels": "Speed labels",
}


def _sample_files() -> list[Path]:
    """The exports bundled with the repository, if any were shipped."""
    return sorted(ROOT.glob("*.csv"))


@spaces.GPU(duration=30)
def load_run(files: list[str] | None) -> tuple:
    """Parse uploaded exports and reconfigure the controls around them."""
    paths = [Path(f) for f in files] if files else _sample_files()
    if not paths:
        raise gr.Error(
            "No files given, and no sample data is bundled with this deployment. "
            "Upload one export per channel: 1X, 1Y and the keyphasor."
        )

    channels, problems = [], []
    for path in paths:
        try:
            channels.append(load_channel(path))
        except (ValueError, KeyError) as exc:
            problems.append(f"{path.name}: {exc}")
    if not channels:
        raise gr.Error("No readable ADRE exports. " + "; ".join(problems))

    try:
        run = build_run_from_channels(channels)
    except (ValueError, KeyError) as exc:
        raise gr.Error(str(exc)) from exc

    names = list(run.segment_bounds())
    default = "Coast-up (startup)" if "Coast-up (startup)" in names else names[0]
    low, high = float(np.nanmin(run.rpm)), float(np.nanmax(run.rpm))
    loaded = ", ".join(run.source_files.values())
    return (
        run,
        f"**Loaded** {loaded} \u2014 {len(run.frame)} samples, "
        f"{low:.0f}\u2013{high:.0f} {run.speed_unit}",
        gr.update(choices=names, value=default),
        gr.update(minimum=low, maximum=high, value=low),
        gr.update(minimum=low, maximum=high, value=high),
        describe_run(run),
    )


@spaces.GPU(duration=60)
def render(
    run: RunData | None,
    segment: str,
    rotation: float,
    rpm_min: float,
    rpm_max: float,
    options: list[str],
    sr_low: float,
    sr_high: float,
    channels: list[str],
    clearance: float | None,
) -> tuple:
    """Build all four figures for the current control state."""
    if run is None or not segment:
        return BLANK, BLANK, BLANK, BLANK, ""

    options = options or []
    settings = ViewSettings(
        segment=segment,
        rotation_deg=float(rotation or 0.0),
        hide_flagged=OPTION_LABELS["hide"] in options,
        slow_roll=OPTION_LABELS["slowroll"] in options,
        slow_roll_rpm=(float(sr_low or 0), float(sr_high or 0)),
        rpm_min=float(rpm_min),
        rpm_max=float(rpm_max),
        machine_view=OPTION_LABELS["machine"] in options,
        show_speed_labels=OPTION_LABELS["labels"] in options,
    )
    result = PolarModel(run).compute(settings)

    status = result.status_line()
    if settings.slow_roll and (result.slow_roll_1x or result.slow_roll_1y):
        status += (
            f"   |   slow-roll removed: 1X {2 * abs(result.slow_roll_1x):.3f} {run.amp_unit}, "
            f"1Y {2 * abs(result.slow_roll_1y):.3f} {run.amp_unit}"
        )
    for note in result.notes:
        status += f"\n\n{note}"

    return (
        polar_figure(result, run, settings.machine_view, settings.show_speed_labels),
        bode_figure(result, run),
        trend_figure(result, run, channels or []),
        centreline_figure(result, run, float(clearance) if clearance else None),
        status,
    )


def build_interface() -> gr.Blocks:
    with gr.Blocks(title="RotorDyn viewer") as demo:
        # Gradio State is per-session, so two people using the Space at once
        # never see each other's data.
        run_state = gr.State(None)

        gr.Markdown(
            "# RotorDyn \u2014 vibration data viewer\n"
            "Polar, Bode, trend and shaft centreline plots from Bently Nevada ADRE "
            "channel exports, with a transducer-rotation control that shows what each "
            "probe would read from any other clock position."
        )

        with gr.Row():
            with gr.Column(scale=1, min_width=280):
                upload = gr.File(
                    label="ADRE exports (1X, 1Y and keyphasor)",
                    file_count="multiple",
                    file_types=[".csv", ".txt"],
                    type="filepath",
                )
                sample_button = gr.Button(
                    "Load bundled sample data", variant="secondary", size="sm"
                )
                banner = gr.Markdown("")

                segment = gr.Dropdown(label="Segment", choices=[], interactive=True)
                rotation = gr.Slider(
                    label="Transducer rotation, clockwise [deg]",
                    minimum=0, maximum=360, step=1, value=0,
                )
                with gr.Row():
                    rpm_min = gr.Slider(label="Speed from", minimum=0, maximum=1, value=0, step=1)
                    rpm_max = gr.Slider(label="Speed to", minimum=0, maximum=1, value=1, step=1)

                options = gr.CheckboxGroup(
                    label="Options",
                    choices=list(OPTION_LABELS.values()),
                    value=[OPTION_LABELS["hide"], OPTION_LABELS["labels"]],
                )
                with gr.Row():
                    sr_low = gr.Number(label="Slow-roll from [rpm]", value=300)
                    sr_high = gr.Number(label="Slow-roll to [rpm]", value=700)

                trend_channels = gr.CheckboxGroup(
                    label="Trend channels",
                    choices=list(TREND_CHANNELS),
                    value=["1X amplitude", "1X phase"],
                )
                clearance = gr.Number(
                    label="Diametral bearing clearance [mil], optional",
                    value=None,
                )

            with gr.Column(scale=3):
                with gr.Tabs():
                    with gr.Tab("Polar"):
                        polar = gr.Plot(value=BLANK, label="")
                    with gr.Tab("Bode"):
                        bode = gr.Plot(value=BLANK, label="")
                    with gr.Tab("Trend"):
                        trend = gr.Plot(value=BLANK, label="")
                    with gr.Tab("Shaft centreline"):
                        centreline = gr.Plot(value=BLANK, label="")
                    with gr.Tab("Dataset"):
                        info = gr.Code(value="Load a dataset to see its summary.", label="")
                status = gr.Markdown("")

        controls = [
            run_state, segment, rotation, rpm_min, rpm_max,
            options, sr_low, sr_high, trend_channels, clearance,
        ]
        figures = [polar, bode, trend, centreline, status]
        load_outputs = [run_state, banner, segment, rpm_min, rpm_max, info]

        upload.change(load_run, inputs=[upload], outputs=load_outputs).then(
            render, inputs=controls, outputs=figures
        )
        sample_button.click(
            lambda: load_run(None), inputs=None, outputs=load_outputs
        ).then(render, inputs=controls, outputs=figures)

        for control in controls[1:]:
            # Sliders fire on release so dragging does not queue a render per pixel.
            event = control.release if isinstance(control, gr.Slider) else control.change
            event(render, inputs=controls, outputs=figures)

    return demo


# Hugging Face Spaces looks for a module-level ``demo`` (see GRADIO_HOT_RELOAD
# in the Space logs).  ``launch`` stays behind the name guard so ``import app``
# in the tests does not start a server.
demo = build_interface()


if __name__ == "__main__":
    # SSR's Node proxy is what exits on Spaces with "Stopping Node.js server..."
    # immediately after a successful bind.  Disable it.
    try:
        demo.launch(ssr_mode=False, server_name="0.0.0.0")
    except TypeError:
        demo.launch(server_name="0.0.0.0")
