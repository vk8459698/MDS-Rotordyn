"""Browser front end for the polar/Bode/trend/centreline viewer.

The desktop Tk interface and this one share every line of the analysis code:
both drive :class:`~rotordyn.model.PolarModel` and differ only in how controls
are collected and how figures are rendered.  That is what makes the migration
cheap, and it is worth keeping that way.

Run it with ``python main.py --web``.
"""

from __future__ import annotations

import base64
import uuid
from collections import OrderedDict
from pathlib import Path

import numpy as np
from dash import Dash, Input, Output, State, callback_context, dcc, html

from .adre import load_channel_bytes
from .buddy import analyse_run
from .mds import diagnose
from .dataset import RunData, build_run, build_run_from_channels
from .model import PolarModel, ViewSettings
from .summary import describe_run
from .webplots import (
    TREND_CHANNELS,
    bode_figure,
    centreline_figure,
    polar_figure,
    trend_figure,
    empty_figure,
)

#: Parsed runs live on the server keyed by a token held in the browser, because
#: a RunData does not survive a round trip through JSON.  An in-process cache is
#: fine for a single instance; a multi-worker deployment wants Redis here.
_RUNS: "OrderedDict[str, RunData]" = OrderedDict()
_MAX_SESSIONS = 24

_CARD = {
    "background": "white",
    "borderRadius": "8px",
    "padding": "16px 18px",
    "boxShadow": "0 1px 3px rgba(0,0,0,0.12)",
    "marginBottom": "14px",
}
_LABEL = {"fontSize": "12px", "fontWeight": 600, "color": "#444", "marginBottom": "4px"}
_PAGE = {
    "fontFamily": "'Segoe UI', system-ui, sans-serif",
    "background": "#f4f5f7",
    "minHeight": "100vh",
    "padding": "18px 22px",
}


def _remember(run: RunData) -> str:
    token = uuid.uuid4().hex
    _RUNS[token] = run
    while len(_RUNS) > _MAX_SESSIONS:
        _RUNS.popitem(last=False)
    return token


def _recall(token: str | None) -> RunData | None:
    if not token:
        return None
    run = _RUNS.get(token)
    if run is not None:
        _RUNS.move_to_end(token)
    return run


def parse_upload(contents: list[str], filenames: list[str]) -> RunData:
    """Turn Dash upload payloads into a run, or raise with a readable message."""
    channels = []
    problems = []
    for content, name in zip(contents, filenames):
        try:
            _, payload = content.split(",", 1)
            channels.append(load_channel_bytes(base64.b64decode(payload), name))
        except (ValueError, KeyError, IndexError) as exc:
            problems.append(f"{name}: {exc}")
    if not channels:
        raise ValueError("No readable ADRE exports in the upload. " + "; ".join(problems))
    return build_run_from_channels(channels)


def build_figure(
    run: RunData,
    tab: str,
    settings: ViewSettings,
    trend_channels: list[str] | None = None,
    clearance_mils: float | None = None,
):
    """Pick and build the figure for a tab. Pure, so the tests can call it."""
    result = PolarModel(run).compute(settings)
    if tab == "bode":
        return bode_figure(result, run), result
    if tab == "trend":
        return trend_figure(result, run, trend_channels or []), result
    if tab == "centreline":
        return centreline_figure(result, run, clearance_mils), result
    return polar_figure(result, run, settings.machine_view, settings.show_speed_labels), result


def _controls() -> html.Div:
    return html.Div(
        [
            html.Div(
                [
                    html.Div("Segment", style=_LABEL),
                    dcc.Dropdown(id="segment", clearable=False, style={"fontSize": "13px"}),
                ],
                style={"marginBottom": "16px"},
            ),
            html.Div(
                [
                    html.Div("Transducer rotation (clockwise)", style=_LABEL),
                    html.Div(
                        [
                            dcc.Slider(
                                id="rotation",
                                min=0,
                                max=360,
                                step=1,
                                value=0,
                                marks={d: f"{d}\u00b0" for d in range(0, 361, 90)},
                                tooltip={"placement": "bottom", "always_visible": True},
                            )
                        ],
                        style={"padding": "0 4px"},
                    ),
                    html.Div(
                        [
                            dcc.Input(
                                id="rotation-input",
                                type="number",
                                min=0,
                                max=360,
                                step=1,
                                value=0,
                                debounce=True,
                                style={"width": "80px", "fontSize": "13px"},
                            ),
                            html.Span(
                                " degrees",
                                style={"fontSize": "12px", "color": "#666", "marginLeft": "6px"},
                            ),
                        ],
                        style={"marginTop": "10px"},
                    ),
                ],
                style={"marginBottom": "18px"},
            ),
            html.Div(
                [
                    html.Div("Speed range", style=_LABEL),
                    dcc.RangeSlider(id="rpm-range", min=0, max=1, step=1, value=[0, 1], marks=None,
                                    tooltip={"placement": "bottom", "always_visible": True}),
                ],
                style={"marginBottom": "18px"},
            ),
            html.Div(
                [
                    html.Div("Options", style=_LABEL),
                    dcc.Checklist(
                        id="options",
                        options=[
                            {"label": " Hide flagged samples", "value": "hide"},
                            {"label": " Slow-roll compensation", "value": "slowroll"},
                            {"label": " Machine view (0\u00b0 at probe)", "value": "machine"},
                            {"label": " Speed labels", "value": "labels"},
                        ],
                        value=["hide", "labels"],
                        style={"fontSize": "13px"},
                        labelStyle={"display": "block", "marginBottom": "3px"},
                    ),
                ],
                style={"marginBottom": "14px"},
            ),
            html.Div(
                [
                    html.Div("Slow-roll speed window", style=_LABEL),
                    dcc.Input(id="sr-low", type="number", value=300, step=25,
                              style={"width": "72px", "fontSize": "13px"}),
                    html.Span(" to ", style={"fontSize": "12px", "margin": "0 6px"}),
                    dcc.Input(id="sr-high", type="number", value=700, step=25,
                              style={"width": "72px", "fontSize": "13px"}),
                ],
                id="slowroll-controls",
                style={"marginBottom": "14px", "display": "none"},
            ),
            html.Div(
                [
                    html.Div("Trend channels", style=_LABEL),
                    dcc.Checklist(
                        id="trend-channels",
                        options=[{"label": f" {name}", "value": name} for name in TREND_CHANNELS],
                        value=["1X amplitude", "1X phase"],
                        style={"fontSize": "13px"},
                        labelStyle={"display": "block", "marginBottom": "3px"},
                    ),
                ],
                id="trend-controls",
                style={"marginBottom": "14px", "display": "none"},
            ),
            html.Div(
                [
                    html.Div("Diametral bearing clearance", style=_LABEL),
                    dcc.Input(id="clearance", type="number", min=0, step=1, placeholder="mils",
                              style={"width": "90px", "fontSize": "13px"}),
                    html.Div(
                        "Optional \u2014 draws the clearance circle.",
                        style={"fontSize": "11px", "color": "#777", "marginTop": "4px"},
                    ),
                ],
                id="centreline-controls",
                style={"marginBottom": "14px", "display": "none"},
            ),
        ],
        style=_CARD,
    )


def create_app(data_dir: str | Path = ".") -> Dash:
    """Build the Dash application. ``data_dir`` backs the sample-data button."""
    app = Dash(__name__, title="RotorDyn viewer", update_title=None)
    data_dir = Path(data_dir)

    app.layout = html.Div(
        [
            dcc.Store(id="session"),
            html.Div(
                [
                    html.H1(
                        "RotorDyn \u2014 vibration data viewer",
                        style={"fontSize": "22px", "margin": "0 0 4px 0", "color": "#1a1a2e"},
                    ),
                    html.Div(
                        "Upload ADRE channel exports for polar, Bode, trend and centreline plots. "
                        "Rotor Buddy screens the wiring, critical-speed and ISO checks. "
                        "MDS Report turns those checks into a diagnosis. "
                        "A full-machine timebase dump is analysed with python main.py --mds.",
                        style={"fontSize": "13px", "color": "#666"},
                    ),
                ],
                style={"marginBottom": "16px"},
            ),
            html.Div(
                [
                    dcc.Upload(
                        id="upload",
                        multiple=True,
                        children=html.Div(
                            [
                                html.Strong("Drop ADRE exports here"),
                                html.Span(" or click to browse \u2014 one file per channel "
                                          "(1X, 1Y and keyphasor)"),
                            ],
                            style={"fontSize": "13px", "color": "#555"},
                        ),
                        style={
                            "border": "2px dashed #b9c0cc",
                            "borderRadius": "8px",
                            "padding": "22px",
                            "textAlign": "center",
                            "cursor": "pointer",
                            "background": "#fafbfc",
                        },
                    ),
                    html.Div(
                        [
                            html.Button(
                                "Load bundled sample data",
                                id="btn-sample",
                                n_clicks=0,
                                style={
                                    "fontSize": "13px",
                                    "padding": "6px 14px",
                                    "border": "1px solid #b9c0cc",
                                    "borderRadius": "6px",
                                    "background": "white",
                                    "cursor": "pointer",
                                },
                            ),
                            html.Span(id="banner", style={"fontSize": "13px", "marginLeft": "14px"}),
                        ],
                        style={"marginTop": "12px", "display": "flex", "alignItems": "center"},
                    ),
                ],
                style=_CARD,
            ),
            html.Div(
                [
                    html.Div(_controls(), style={"width": "290px", "flexShrink": 0}),
                    html.Div(
                        [
                            html.Div(
                                [
                                    dcc.Tabs(
                                        id="tabs",
                                        value="polar",
                                        children=[
                                            dcc.Tab(label="Polar", value="polar"),
                                            dcc.Tab(label="Bode", value="bode"),
                                            dcc.Tab(label="Trend", value="trend"),
                                            dcc.Tab(label="Shaft centreline", value="centreline"),
                                            dcc.Tab(label="Rotor Buddy", value="buddy"),
                                            dcc.Tab(label="MDS Report", value="mds"),
                                            dcc.Tab(label="Dataset", value="info"),
                                        ],
                                    ),
                                    dcc.Loading(
                                        dcc.Graph(
                                            id="graph",
                                            config={
                                                "displaylogo": False,
                                                "toImageButtonOptions": {
                                                    "format": "png",
                                                    "filename": "rotordyn",
                                                    "scale": 2,
                                                },
                                            },
                                        ),
                                        type="default",
                                    ),
                                    html.Pre(
                                        id="info",
                                        style={
                                            "display": "none",
                                            "fontSize": "12.5px",
                                            "lineHeight": "1.55",
                                            "color": "#222",
                                            "margin": 0,
                                            "padding": "16px 4px",
                                            "whiteSpace": "pre-wrap",
                                        },
                                    ),
                                    html.Div(
                                        id="status",
                                        style={
                                            "fontSize": "12px",
                                            "color": "#555",
                                            "borderTop": "1px solid #eee",
                                            "paddingTop": "10px",
                                            "marginTop": "6px",
                                        },
                                    ),
                                ],
                                style=_CARD,
                            )
                        ],
                        style={"flexGrow": 1, "marginLeft": "16px", "minWidth": 0},
                    ),
                ],
                style={"display": "flex", "alignItems": "flex-start"},
            ),
        ],
        style=_PAGE,
    )

    @app.callback(
        Output("session", "data"),
        Output("banner", "children"),
        Output("banner", "style"),
        Input("upload", "contents"),
        Input("btn-sample", "n_clicks"),
        State("upload", "filename"),
        prevent_initial_call=True,
    )
    def load(contents, _clicks, filenames):
        triggered = callback_context.triggered[0]["prop_id"]
        ok = {"fontSize": "13px", "marginLeft": "14px", "color": "#1e7a3c"}
        bad = {"fontSize": "13px", "marginLeft": "14px", "color": "#c0392b"}
        try:
            if triggered.startswith("btn-sample"):
                run = build_run(data_dir)
                message = f"Loaded sample data from {data_dir.resolve().name}"
            else:
                if not contents:
                    return None, "", ok
                run = parse_upload(contents, filenames or [])
                message = f"Loaded {', '.join(run.source_files.values())}"
        except (ValueError, KeyError, FileNotFoundError, OSError) as exc:
            return None, f"Could not load: {exc}", bad
        return _remember(run), message, ok

    @app.callback(
        Output("segment", "options"),
        Output("segment", "value"),
        Output("rpm-range", "min"),
        Output("rpm-range", "max"),
        Output("rpm-range", "value"),
        Input("session", "data"),
    )
    def configure(token):
        run = _recall(token)
        if run is None:
            return [], None, 0, 1, [0, 1]
        names = list(run.segment_bounds())
        low, high = float(np.nanmin(run.rpm)), float(np.nanmax(run.rpm))
        default = "Coast-up (startup)" if "Coast-up (startup)" in names else names[0]
        return names, default, low, high, [low, high]

    @app.callback(
        Output("info", "children"),
        Input("session", "data"),
        Input("tabs", "value"),
    )
    def report_text(token, tab):
        run = _recall(token)
        if run is None:
            return "Load a dataset to see its summary."
        if tab == "buddy":
            return analyse_run(run).format_text()
        if tab == "mds":
            return diagnose(analyse_run(run)).format_text()
        return describe_run(run)

    @app.callback(
        Output("rotation", "value"),
        Output("rotation-input", "value"),
        Input("rotation", "value"),
        Input("rotation-input", "value"),
    )
    def sync_rotation(slider, box):
        triggered = callback_context.triggered[0]["prop_id"]
        value = box if triggered.startswith("rotation-input") else slider
        value = 0.0 if value is None else float(value) % 360.0
        return value, value

    @app.callback(
        Output("slowroll-controls", "style"),
        Output("trend-controls", "style"),
        Output("centreline-controls", "style"),
        Output("graph", "style"),
        Output("info", "style"),
        Input("tabs", "value"),
        Input("options", "value"),
    )
    def toggle_controls(tab, options):
        shown = {"marginBottom": "14px"}
        hidden = {"display": "none"}
        info_tab = tab in ("info", "buddy", "mds")
        return (
            shown if "slowroll" in (options or []) and not info_tab else hidden,
            shown if tab == "trend" else hidden,
            shown if tab == "centreline" else hidden,
            hidden if info_tab else {},
            {
                "fontSize": "12.5px",
                "lineHeight": "1.55",
                "color": "#222",
                "margin": 0,
                "padding": "16px 4px",
                "whiteSpace": "pre-wrap",
            }
            if info_tab
            else hidden,
        )

    @app.callback(
        Output("graph", "figure"),
        Output("status", "children"),
        Input("session", "data"),
        Input("tabs", "value"),
        Input("segment", "value"),
        Input("rotation", "value"),
        Input("rpm-range", "value"),
        Input("options", "value"),
        Input("sr-low", "value"),
        Input("sr-high", "value"),
        Input("trend-channels", "value"),
        Input("clearance", "value"),
    )
    def render(token, tab, segment, rotation, rpm_range, options, sr_low, sr_high, channels, clearance):
        run = _recall(token)
        if run is None or not segment:
            return empty_figure("Upload ADRE exports, or load the bundled sample data."), ""
        if tab in ("info", "buddy", "mds"):
            return empty_figure(""), ""

        options = options or []
        settings = ViewSettings(
            segment=segment,
            rotation_deg=float(rotation or 0.0),
            hide_flagged="hide" in options,
            slow_roll="slowroll" in options,
            slow_roll_rpm=(float(sr_low or 0), float(sr_high or 0)),
            rpm_min=float(rpm_range[0]) if rpm_range else None,
            rpm_max=float(rpm_range[1]) if rpm_range else None,
            machine_view="machine" in options,
            show_speed_labels="labels" in options,
        )
        figure, result = build_figure(
            run, tab, settings, channels, float(clearance) if clearance else None
        )

        status = [html.Span(result.status_line())]
        if settings.slow_roll and (result.slow_roll_1x or result.slow_roll_1y):
            status.append(
                html.Span(
                    f"   |   slow-roll removed: 1X {2 * abs(result.slow_roll_1x):.3f} {run.amp_unit}, "
                    f"1Y {2 * abs(result.slow_roll_1y):.3f} {run.amp_unit}"
                )
            )
        for note in result.notes:
            status.append(html.Div(note, style={"color": "#c0392b", "marginTop": "4px"}))
        return figure, status

    return app


def run_web(
    data_dir: str | Path = ".",
    host: str = "127.0.0.1",
    port: int = 8050,
    debug: bool = False,
    production: bool = False,
    threads: int = 8,
) -> None:
    """Serve the application.

    ``production`` swaps Flask's development server for waitress, which is a
    real WSGI server and, unlike gunicorn, runs on Windows.  One waitress
    process serves every request from a thread pool, so the in-process cache of
    uploaded runs is shared and no sticky-session handling is needed.
    """
    app = create_app(data_dir)
    if production:
        from waitress import serve

        serve(app.server, host=host, port=port, threads=threads)
        return
    runner = getattr(app, "run", None) or app.run_server
    runner(host=host, port=port, debug=debug)
