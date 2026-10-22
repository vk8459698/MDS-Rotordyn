"""Plotly figures for the web interface.

These consume exactly the same :class:`~rotordyn.model.ViewResult` that the
Matplotlib figures do, so the maths has one home and only the rendering
differs.  Plotly is used here rather than Matplotlib because hover readout,
zoom and pan are the features the firm's brief calls out as missing from the
incumbent tools.
"""

from __future__ import annotations

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .centreline import centreline_positions, clearance_circle, rest_reference
from .dataset import RunData
from .model import ViewResult, speed_label_indices
from .vectors import ROTATION_SENSE, describe_position

COLOUR_1X = "#1f4e9c"
COLOUR_1Y = "#a1420a"
_SPEED_SCALE = "Viridis"

CONVENTION_NOTE = (
    f"0\u00b0 = keyphasor pulse \u00b7 phase lag increases clockwise, against the "
    f"{ROTATION_SENSE} shaft rotation \u00b7 probe angles measured clockwise from TDC"
)

_LAYOUT = dict(
    template="plotly_white",
    margin=dict(l=60, r=30, t=70, b=70),
    hovermode="closest",
    font=dict(size=12),
)


def empty_figure(message: str) -> go.Figure:
    """Placeholder shown before a dataset is loaded, or when a filter is empty."""
    fig = go.Figure()
    fig.add_annotation(
        text=message, showarrow=False, font=dict(size=15, color="#666"), xref="paper", yref="paper", x=0.5, y=0.5
    )
    fig.update_layout(
        **_LAYOUT,
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
    )
    return fig


def break_phase_wraps(x: np.ndarray, phase: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Insert a gap wherever the phase wraps through 0/360.

    Without this the line is drawn straight down the page at every wrap, which
    reads as a real 360 deg swing and is one of the things that makes the
    incumbent plots hard to trust.
    """
    if phase.size < 2:
        return x, phase
    wraps = np.flatnonzero(np.abs(np.diff(phase)) > 180.0) + 1
    if wraps.size == 0:
        return x, phase
    return np.insert(x, wraps, np.nan), np.insert(phase, wraps, np.nan)


def _footnote(fig: go.Figure, text: str) -> None:
    fig.add_annotation(
        text=text,
        showarrow=False,
        xref="paper",
        yref="paper",
        x=0.5,
        y=-0.13,
        font=dict(size=10, color="#555"),
    )


def polar_figure(
    result: ViewResult, run: RunData, machine_view: bool = False, show_labels: bool = True
) -> go.Figure:
    """Side-by-side 1X and 1Y polar plots, coloured by shaft speed."""
    if result.n_points == 0:
        return empty_figure("No samples match the current filters.")

    fig = make_subplots(
        rows=1,
        cols=2,
        specs=[[{"type": "polar"}, {"type": "polar"}]],
        subplot_titles=(
            f"1X probe \u2014 {describe_position(result.theta_1x)}",
            f"1Y probe \u2014 {describe_position(result.theta_1y)}",
        ),
        horizontal_spacing=0.16,
    )

    probes = (
        (result.amp_1x, result.phase_1x, result.theta_1x, COLOUR_1X, run.probe_1x_name, "polar"),
        (result.amp_1y, result.phase_1y, result.theta_1y, COLOUR_1Y, run.probe_1y_name, "polar2"),
    )

    for col, (amp, phase, theta_probe, colour, name, axis) in enumerate(probes, start=1):
        fig.add_trace(
            go.Scatterpolar(
                r=amp,
                theta=phase,
                mode="lines",
                line=dict(color="#b8b8b8", width=1),
                hoverinfo="skip",
                showlegend=False,
            ),
            row=1,
            col=col,
        )
        fig.add_trace(
            go.Scatterpolar(
                r=amp,
                theta=phase,
                mode="markers",
                name=name,
                customdata=result.rpm,
                marker=dict(
                    size=6,
                    color=result.rpm,
                    colorscale=_SPEED_SCALE,
                    showscale=col == 1,
                    colorbar=dict(title=f"speed<br>[{run.speed_unit}]", x=1.02, len=0.75),
                    line=dict(color=colour, width=0.6),
                ),
                hovertemplate=(
                    "%{customdata:.0f} " + run.speed_unit + "<br>"
                    "%{r:.3f} " + run.amp_unit + " @ %{theta:.0f}\u00b0<extra></extra>"
                ),
                showlegend=False,
            ),
            row=1,
            col=col,
        )

        if show_labels:
            # Hover already gives an exact readout, so the printed labels only
            # need to orient the eye along the track.  Fewer of them collide less.
            picks = speed_label_indices(result.rpm, count=6)
            if picks.size:
                fig.add_trace(
                    go.Scatterpolar(
                        r=amp[picks],
                        theta=phase[picks],
                        mode="text",
                        text=[f"{result.rpm[i]:.0f}" for i in picks],
                        textposition="top right",
                        textfont=dict(size=9, color="#333"),
                        hoverinfo="skip",
                        showlegend=False,
                    ),
                    row=1,
                    col=col,
                )

        # Placing 0 deg at the probe reproduces what an engineer sees standing
        # at the machine; otherwise 0 deg sits at the top of the page.
        rotation = 90.0 - theta_probe if machine_view else 90.0
        fig.update_layout(
            **{
                axis: dict(
                    hole=0.04,
                    radialaxis=dict(
                        range=[0, result.r_max],
                        angle=90,
                        tickangle=90,
                        nticks=4,
                        tickfont=dict(size=9, color="#777"),
                        gridcolor="#e2e2e2",
                    ),
                    angularaxis=dict(
                        direction="clockwise",
                        rotation=rotation,
                        dtick=30,
                        ticksuffix="\u00b0",
                        tickfont=dict(size=9),
                        gridcolor="#e2e2e2",
                    ),
                )
            }
        )

    view = "machine view (0\u00b0 drawn at the probe)" if machine_view else "0\u00b0 at top of page"
    fig.update_layout(
        **_LAYOUT,
        title=dict(
            text=f"Synchronous (1X) vector polar plots \u2014 amplitude in {run.amp_unit}",
            font=dict(size=17),
        ),
        height=560,
    )
    _footnote(fig, f"{CONVENTION_NOTE} \u00b7 {view}")
    return fig


def bode_figure(result: ViewResult, run: RunData) -> go.Figure:
    """Amplitude and phase against shaft speed for both probes."""
    if result.n_points == 0:
        return empty_figure("No samples match the current filters.")

    order = np.argsort(result.rpm)
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.07)

    for amp, phase, colour, name in (
        (result.amp_1x, result.phase_1x, COLOUR_1X, run.probe_1x_name),
        (result.amp_1y, result.phase_1y, COLOUR_1Y, run.probe_1y_name),
    ):
        fig.add_trace(
            go.Scatter(
                x=result.rpm[order],
                y=amp[order],
                mode="lines",
                name=name,
                line=dict(color=colour, width=1.6),
                legendgroup=name,
                hovertemplate="%{x:.0f} " + run.speed_unit + "<br>%{y:.3f} " + run.amp_unit + "<extra></extra>",
            ),
            row=1,
            col=1,
        )
        speed, unwrapped = break_phase_wraps(result.rpm[order], phase[order])
        fig.add_trace(
            go.Scatter(
                x=speed,
                y=unwrapped,
                mode="lines",
                name=name,
                line=dict(color=colour, width=1.6),
                legendgroup=name,
                showlegend=False,
                connectgaps=False,
                hovertemplate="%{x:.0f} " + run.speed_unit + "<br>%{y:.0f}\u00b0 lag<extra></extra>",
            ),
            row=2,
            col=1,
        )

    fig.update_yaxes(title_text=f"1X amplitude [{run.amp_unit}]", row=1, col=1)
    fig.update_yaxes(
        title_text="1X phase lag [deg]", range=[360, 0], dtick=45, row=2, col=1
    )
    fig.update_xaxes(title_text=f"shaft speed [{run.speed_unit}]", row=2, col=1)
    fig.update_layout(
        **_LAYOUT,
        title=dict(text="Bode plot \u2014 1X response against speed", font=dict(size=17)),
        height=620,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
    )
    _footnote(fig, CONVENTION_NOTE)
    return fig


#: Trend channels the user can select, mapped to (frame columns, axis label).
TREND_CHANNELS: dict[str, tuple[tuple[str, str], str]] = {
    "1X amplitude": (("amp_1x", "amp_1y"), "1X amplitude"),
    "1X phase": (("phase_1x", "phase_1y"), "1X phase lag [deg]"),
    "Direct (overall)": (("direct_1x", "direct_1y"), "direct amplitude"),
    "Gap voltage": (("gap_1x", "gap_1y"), "average gap [V]"),
}


def trend_figure(result: ViewResult, run: RunData, channels: list[str]) -> go.Figure:
    """Selected channels against time, with shaft speed overlaid."""
    frame = result.frame
    if frame is None or frame.empty:
        return empty_figure("No samples match the current filters.")
    channels = [c for c in channels if c in TREND_CHANNELS] or ["1X amplitude"]

    time = frame["timestamp"]
    rows = len(channels)
    fig = make_subplots(
        rows=rows,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.06,
        specs=[[{"secondary_y": True}] for _ in range(rows)],
    )

    for row, channel in enumerate(channels, start=1):
        (col_x, col_y), label = TREND_CHANNELS[channel]
        unit = run.amp_unit if "amplitude" in channel.lower() else ""
        for column, colour, name in (
            (col_x, COLOUR_1X, run.probe_1x_name),
            (col_y, COLOUR_1Y, run.probe_1y_name),
        ):
            fig.add_trace(
                go.Scatter(
                    x=time,
                    y=frame[column],
                    mode="lines",
                    name=name,
                    line=dict(color=colour, width=1.4),
                    legendgroup=name,
                    showlegend=row == 1,
                    hovertemplate="%{x|%H:%M:%S}<br>%{y:.3f} " + unit + "<extra></extra>",
                ),
                row=row,
                col=1,
                secondary_y=False,
            )
        fig.add_trace(
            go.Scatter(
                x=time,
                y=frame["rpm"],
                mode="lines",
                name="speed",
                line=dict(color="#9aa0a6", width=1, dash="dot"),
                legendgroup="speed",
                showlegend=row == 1,
                hovertemplate="%{y:.0f} " + run.speed_unit + "<extra></extra>",
            ),
            row=row,
            col=1,
            secondary_y=True,
        )
        title = f"{label} [{unit}]" if unit else label
        fig.update_yaxes(title_text=title, row=row, col=1, secondary_y=False)
        fig.update_yaxes(
            title_text=f"speed [{run.speed_unit}]",
            row=row,
            col=1,
            secondary_y=True,
            showgrid=False,
            color="#9aa0a6",
        )

    fig.update_xaxes(title_text="time", row=rows, col=1)
    fig.update_layout(
        **_LAYOUT,
        title=dict(text="Trend \u2014 channels against time", font=dict(size=17)),
        height=260 * rows + 120,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
    )
    return fig


def centreline_figure(
    result: ViewResult, run: RunData, clearance_mils: float | None = None
) -> go.Figure:
    """Journal position inside the bearing, referenced to the resting position."""
    frame = result.frame
    if frame is None or frame.empty:
        return empty_figure("No samples match the current filters.")

    # Reference against the whole record, not the selected segment, so the
    # origin stays put when the user switches between coast-up and coast-down.
    ref_x = rest_reference(run.frame["gap_1x"].to_numpy(float), run.rpm)
    ref_y = rest_reference(run.frame["gap_1y"].to_numpy(float), run.rpm)
    right, up = centreline_positions(
        frame["gap_1x"].to_numpy(float), frame["gap_1y"].to_numpy(float), ref_x, ref_y
    )
    rpm = frame["rpm"].to_numpy(float)

    fig = go.Figure()

    if clearance_mils:
        cx, cy = clearance_circle(clearance_mils)
        fig.add_trace(
            go.Scatter(
                x=cx,
                y=cy,
                mode="lines",
                name=f"{clearance_mils:.0f} mil clearance",
                line=dict(color="#cccccc", width=1.5, dash="dash"),
                hoverinfo="skip",
            )
        )

    fig.add_trace(
        go.Scatter(
            x=right,
            y=up,
            mode="lines",
            line=dict(color="#c8c8c8", width=1),
            hoverinfo="skip",
            showlegend=False,
        )
    )
    fig.add_trace(
        go.Scatter(
            x=right,
            y=up,
            mode="markers",
            name="journal centre",
            customdata=rpm,
            marker=dict(
                size=6,
                color=rpm,
                colorscale=_SPEED_SCALE,
                showscale=True,
                colorbar=dict(title=f"speed<br>[{run.speed_unit}]"),
                line=dict(color="#333", width=0.4),
            ),
            hovertemplate=(
                "%{customdata:.0f} " + run.speed_unit + "<br>"
                "%{x:+.2f} mil right, %{y:+.2f} mil up<extra></extra>"
            ),
        )
    )
    fig.add_trace(
        go.Scatter(
            x=[0.0],
            y=[0.0],
            mode="markers",
            name="at rest",
            marker=dict(size=12, symbol="x", color="#c0392b"),
            hovertemplate="resting position<extra></extra>",
        )
    )

    fig.update_layout(
        **_LAYOUT,
        title=dict(text="Shaft centreline \u2014 journal position in the bearing", font=dict(size=17)),
        height=620,
        xaxis=dict(title="right of resting position [mil]", zeroline=True, zerolinecolor="#ddd"),
        yaxis=dict(
            title="above resting position [mil]",
            scaleanchor="x",
            scaleratio=1,
            zeroline=True,
            zerolinecolor="#ddd",
        ),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
    )
    _footnote(
        fig,
        "Derived from the DC gap voltages at 200 mV/mil, projected onto the probe axes \u00b7 "
        "origin is the mean position below 100 rpm",
    )
    return fig
