"""Tkinter front end: two live polar plots plus the transducer-angle control."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

from .dataset import RunData
from .model import PolarModel, ViewSettings
from .plots import BodePlot, annotate_conventions, build_polar_figure
from .vectors import KEYPHASOR_DEG, PROBE_1X_DEG, PROBE_1Y_DEG, ROTATION_SENSE, describe_position

_CONVENTION_NOTE = (
    f"Viewed from the end where 1X is {abs(PROBE_1X_DEG):.0f}\u00b0 right of TDC  |  "
    f"shaft rotation {ROTATION_SENSE} in this view  |  "
    "0\u00b0 = Keyphasor pulse, phase lag increases clockwise (against rotation)"
)

_ROTATION_PRESETS = (0, 45, 90, 135, 180, 225, 270, 315)


class PolarApp(tk.Tk):
    """Main window."""

    def __init__(self, run: RunData) -> None:
        super().__init__()
        self.run = run
        self.model = PolarModel(run)
        self.title("RotorDyn - 1X Vector Polar Plot Tool")
        # Fit the screen rather than assuming one; laptop panels are often
        # shorter than the natural size of two side-by-side polar plots.
        width = min(1320, int(self.winfo_screenwidth() * 0.86))
        height = min(920, int(self.winfo_screenheight() * 0.80))
        self.geometry(f"{width}x{height}+20+20")
        self.minsize(980, 600)

        self._redraw_job: str | None = None
        self._build_variables()
        self._build_controls()
        # The status bar is packed before the notebook so that the expanding
        # notebook cannot squeeze it off the bottom of the window.
        self._build_status_bar()
        self._build_figures()
        self.refresh()

    # ------------------------------------------------------------------ state

    def _build_variables(self) -> None:
        segments = self.model.segment_names()
        rpm_lo, rpm_hi = self.model.speed_range()

        self.var_segment = tk.StringVar(value=segments[0])
        self.var_rotation = tk.DoubleVar(value=0.0)
        self.var_rotation_text = tk.StringVar(value="0")
        self.var_hide_flagged = tk.BooleanVar(value=True)
        self.var_slow_roll = tk.BooleanVar(value=False)
        self.var_sr_low = tk.StringVar(value="300")
        self.var_sr_high = tk.StringVar(value="700")
        self.var_rpm_min = tk.StringVar(value=f"{rpm_lo:.0f}")
        self.var_rpm_max = tk.StringVar(value=f"{rpm_hi:.0f}")
        self.var_machine_view = tk.BooleanVar(value=False)
        self.var_labels = tk.BooleanVar(value=True)
        self.var_status = tk.StringVar(value="")

    # --------------------------------------------------------------- controls

    def _build_controls(self) -> None:
        outer = ttk.Frame(self, padding=(10, 8, 10, 4))
        outer.pack(side=tk.TOP, fill=tk.X)

        rotation = ttk.LabelFrame(
            outer, text="Transducer rotation (probe pair moved clockwise around the casing)"
        )
        rotation.pack(side=tk.TOP, fill=tk.X)

        scale_row = ttk.Frame(rotation, padding=(8, 6))
        scale_row.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(scale_row, text="0\u00b0").pack(side=tk.LEFT)
        self.scale = ttk.Scale(
            scale_row,
            from_=0.0,
            to=360.0,
            orient=tk.HORIZONTAL,
            variable=self.var_rotation,
            command=self._on_scale,
        )
        self.scale.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)
        ttk.Label(scale_row, text="360\u00b0").pack(side=tk.LEFT)

        entry = ttk.Entry(scale_row, textvariable=self.var_rotation_text, width=7, justify="right")
        entry.pack(side=tk.LEFT, padx=(12, 2))
        entry.bind("<Return>", self._on_entry)
        entry.bind("<FocusOut>", self._on_entry)
        ttk.Label(scale_row, text="deg").pack(side=tk.LEFT)

        preset_row = ttk.Frame(rotation, padding=(8, 0, 8, 8))
        preset_row.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(preset_row, text="Jump to:").pack(side=tk.LEFT, padx=(0, 6))
        for angle in _ROTATION_PRESETS:
            ttk.Button(
                preset_row,
                text=f"{angle}\u00b0",
                width=5,
                command=lambda a=angle: self.set_rotation(a),
            ).pack(side=tk.LEFT, padx=1)
        ttk.Button(preset_row, text="Reset all", command=self.reset).pack(side=tk.RIGHT)

        options = ttk.Frame(outer, padding=(0, 8, 0, 0))
        options.pack(side=tk.TOP, fill=tk.X)

        data_box = ttk.LabelFrame(options, text="Data selection", padding=(8, 6))
        data_box.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 8))
        ttk.Label(data_box, text="Run segment").grid(row=0, column=0, sticky="w")
        combo = ttk.Combobox(
            data_box,
            textvariable=self.var_segment,
            values=self.model.segment_names(),
            state="readonly",
            width=22,
        )
        combo.grid(row=0, column=1, columnspan=3, sticky="w", padx=(6, 0))
        combo.bind("<<ComboboxSelected>>", lambda _event: self.refresh())

        ttk.Label(data_box, text="Speed range").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self._spin(data_box, self.var_rpm_min).grid(row=1, column=1, sticky="w", padx=(6, 2), pady=(6, 0))
        ttk.Label(data_box, text="to").grid(row=1, column=2, pady=(6, 0))
        self._spin(data_box, self.var_rpm_max).grid(row=1, column=3, sticky="w", padx=(2, 0), pady=(6, 0))

        ttk.Checkbutton(
            data_box,
            text="Hide samples flagged by the instrument",
            variable=self.var_hide_flagged,
            command=self.refresh,
        ).grid(row=2, column=0, columnspan=4, sticky="w", pady=(6, 0))

        sr_box = ttk.LabelFrame(options, text="Slow-roll (runout) compensation", padding=(8, 6))
        sr_box.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 8))
        ttk.Checkbutton(
            sr_box,
            text="Subtract slow-roll vector",
            variable=self.var_slow_roll,
            command=self.refresh,
        ).grid(row=0, column=0, columnspan=4, sticky="w")
        ttk.Label(sr_box, text="Averaged over").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self._spin(sr_box, self.var_sr_low).grid(row=1, column=1, sticky="w", padx=(6, 2), pady=(6, 0))
        ttk.Label(sr_box, text="to").grid(row=1, column=2, pady=(6, 0))
        self._spin(sr_box, self.var_sr_high).grid(row=1, column=3, sticky="w", padx=(2, 0), pady=(6, 0))
        ttk.Label(sr_box, text="rpm").grid(row=1, column=4, sticky="w", padx=(4, 0), pady=(6, 0))

        view_box = ttk.LabelFrame(options, text="Display", padding=(8, 6))
        view_box.pack(side=tk.LEFT, fill=tk.Y)
        ttk.Checkbutton(
            view_box, text="Label speeds along the track", variable=self.var_labels,
            command=self.refresh,
        ).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(
            view_box,
            text="Machine view (0\u00b0 phase at the probe)",
            variable=self.var_machine_view,
            command=self.refresh,
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Label(
            view_box,
            text=(
                f"Installed: 1X {describe_position(PROBE_1X_DEG)}, "
                f"1Y {describe_position(PROBE_1Y_DEG)}, "
                f"Keyphasor {describe_position(KEYPHASOR_DEG)}"
            ),
            foreground="#555",
        ).grid(row=2, column=0, sticky="w", pady=(6, 0))

    def _spin(self, parent: tk.Misc, variable: tk.StringVar) -> ttk.Spinbox:
        box = ttk.Spinbox(
            parent,
            from_=0,
            to=100000,
            increment=50,
            width=7,
            textvariable=variable,
            command=self.refresh,
        )
        box.bind("<Return>", lambda _event: self.refresh())
        box.bind("<FocusOut>", lambda _event: self.refresh())
        return box

    # ---------------------------------------------------------------- figures

    def _build_figures(self) -> None:
        notebook = ttk.Notebook(self)
        notebook.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=(8, 0))

        polar_tab = ttk.Frame(notebook)
        notebook.add(polar_tab, text="Polar plots")
        self.polar_fig, self.plot_1x, self.plot_1y, self.colourbar = build_polar_figure(
            self.run.amp_unit
        )
        annotate_conventions(self.polar_fig, _CONVENTION_NOTE)
        self.polar_canvas = self._mount(self.polar_fig, polar_tab)

        bode_tab = ttk.Frame(notebook)
        notebook.add(bode_tab, text="Bode plot")
        self.bode_fig = Figure(figsize=(10.6, 5.2), dpi=100)
        self.bode_fig.patch.set_facecolor("white")
        self.bode = BodePlot(self.bode_fig, self.run.amp_unit)
        self.bode_canvas = self._mount(self.bode_fig, bode_tab)

        info_tab = ttk.Frame(notebook)
        notebook.add(info_tab, text="Dataset & conventions")
        text = tk.Text(info_tab, wrap="word", padx=12, pady=10, relief=tk.FLAT)
        text.pack(fill=tk.BOTH, expand=True)
        text.insert("1.0", self._dataset_summary())
        text.configure(state=tk.DISABLED)

    def _mount(self, figure: Figure, parent: ttk.Frame) -> FigureCanvasTkAgg:
        """Put a figure and its toolbar in a tab, toolbar first so it survives."""
        canvas = FigureCanvasTkAgg(figure, master=parent)
        toolbar_frame = ttk.Frame(parent)
        toolbar_frame.pack(side=tk.BOTTOM, fill=tk.X)
        NavigationToolbar2Tk(canvas, toolbar_frame).update()
        canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        return canvas

    def _build_status_bar(self) -> None:
        bar = ttk.Frame(self, padding=(12, 6))
        bar.pack(side=tk.BOTTOM, fill=tk.X)
        ttk.Label(bar, textvariable=self.var_status, foreground="#1f4e9c").pack(side=tk.LEFT)

    def _dataset_summary(self) -> str:
        run = self.run
        evidence = run.rotation_evidence()
        frame = run.frame
        lines = [
            "SOURCE FILES",
            *(f"  {role:<10} {name}" for role, name in run.source_files.items()),
            "",
            "CHANNELS",
            f"  1X probe      {run.probe_1x_name} at {PROBE_1X_DEG:+.0f}\u00b0 from TDC (right)",
            f"  1Y probe      {run.probe_1y_name} at {PROBE_1Y_DEG:+.0f}\u00b0 from TDC (left)",
            f"  Keyphasor     {run.keyphasor_name} at {run.keyphasor_deg:+.0f}\u00b0 from TDC (right)",
            f"  Units         amplitude {run.amp_unit}, phase {run.phase_unit}, speed {run.speed_unit}",
            "",
            "RECORD",
            f"  Samples       {len(frame)}",
            f"  Time span     {frame['timestamp'].iloc[0]}  ->  {frame['timestamp'].iloc[-1]}",
            f"  Speed range   {frame['rpm'].min():.0f} to {frame['rpm'].max():.0f} rpm",
            *(
                f"  {name:<13} samples {start}-{stop - 1}"
                for name, (start, stop) in run.segment_bounds().items()
            ),
            "",
            "INSTRUMENT STATUS CODES FOUND (counts across both probes)",
            *(f"  {code:<6} {count}" for code, count in run.flag_counts.items()),
            "",
            "ROTATION DIRECTION - MEASURED, NOT ASSUMED",
            f"  1Y phase lag exceeds 1X by a median of "
            f"{evidence.get('median_1y_minus_1x_deg', float('nan')):.0f}\u00b0, matching the 90\u00b0",
            "  physical separation, which places 1Y downstream of 1X in the direction of rotation.",
            f"  Forward/reverse orbit split: mean counter-clockwise component "
            f"{evidence.get('mean_ccw_magnitude', float('nan')):.3f} vs clockwise "
            f"{evidence.get('mean_cw_magnitude', float('nan')):.3f};",
            f"  counter-clockwise dominates in "
            f"{100 * evidence.get('fraction_ccw_dominant', float('nan')):.0f}% of "
            f"{evidence.get('n_samples', 0):.0f} strong-signal samples.",
            "  => shaft turns counter-clockwise as viewed from the end described above.",
            "",
            "ROTATION MATHS",
            "  A probe at angle t reads the projection of the shaft's synchronous motion:",
            f"      Z(t) = cos(t - {PROBE_1X_DEG:.0f}\u00b0) * Z_1X + cos(t - {PROBE_1Y_DEG:.0f}\u00b0) * Z_1Y",
            "  with Z = (amplitude_pp / 2) * exp(-i * phase_lag). Moving the pair by r degrees",
            f"  evaluates this at t = {PROBE_1X_DEG:.0f}\u00b0 + r for the 1X plot "
            f"and t = {PROBE_1Y_DEG:.0f}\u00b0 + r for the 1Y plot.",
        ]
        return "\n".join(lines)

    # --------------------------------------------------------------- handlers

    def _on_scale(self, _value: str) -> None:
        self.var_rotation_text.set(f"{self.var_rotation.get():.0f}")
        self._schedule_refresh()

    def _on_entry(self, _event: tk.Event) -> None:
        raw = self.var_rotation_text.get().strip().rstrip("\u00b0")
        try:
            angle = float(raw)
        except ValueError:
            self.var_rotation_text.set(f"{self.var_rotation.get():.0f}")
            return
        self.set_rotation(angle)

    def set_rotation(self, angle: float) -> None:
        angle = float(np.clip(angle, 0.0, 360.0))
        self.var_rotation.set(angle)
        self.var_rotation_text.set(f"{angle:.0f}")
        self.refresh()

    def reset(self) -> None:
        rpm_lo, rpm_hi = self.model.speed_range()
        self.var_segment.set(self.model.segment_names()[0])
        self.var_hide_flagged.set(True)
        self.var_slow_roll.set(False)
        self.var_sr_low.set("300")
        self.var_sr_high.set("700")
        self.var_rpm_min.set(f"{rpm_lo:.0f}")
        self.var_rpm_max.set(f"{rpm_hi:.0f}")
        self.var_machine_view.set(False)
        self.var_labels.set(True)
        self.set_rotation(0.0)

    def _schedule_refresh(self) -> None:
        """Coalesce the flood of events a slider drag produces."""
        if self._redraw_job is not None:
            self.after_cancel(self._redraw_job)
        self._redraw_job = self.after(20, self.refresh)

    def _float(self, variable: tk.StringVar, fallback: float) -> float:
        try:
            return float(variable.get())
        except ValueError:
            variable.set(f"{fallback:.0f}")
            return fallback

    def _settings(self) -> ViewSettings:
        rpm_lo, rpm_hi = self.model.speed_range()
        return ViewSettings(
            segment=self.var_segment.get(),
            rotation_deg=self.var_rotation.get(),
            hide_flagged=self.var_hide_flagged.get(),
            slow_roll=self.var_slow_roll.get(),
            slow_roll_rpm=(
                self._float(self.var_sr_low, 300.0),
                self._float(self.var_sr_high, 700.0),
            ),
            rpm_min=self._float(self.var_rpm_min, rpm_lo),
            rpm_max=self._float(self.var_rpm_max, rpm_hi),
            machine_view=self.var_machine_view.get(),
            show_speed_labels=self.var_labels.get(),
        )

    def refresh(self) -> None:
        self._redraw_job = None
        settings = self._settings()
        try:
            result = self.model.compute(settings)
        except (KeyError, ValueError) as exc:  # pragma: no cover - defensive
            messagebox.showerror("Could not update plots", str(exc))
            return

        self.plot_1x.set_orientation(result.theta_1x, settings.machine_view)
        self.plot_1y.set_orientation(result.theta_1y, settings.machine_view)
        self.plot_1x.update(
            result.amp_1x, result.phase_1x, result.rpm, result.theta_1x,
            result.r_max, settings.show_speed_labels,
        )
        self.plot_1y.update(
            result.amp_1y, result.phase_1y, result.rpm, result.theta_1y,
            result.r_max, settings.show_speed_labels,
        )
        if result.rpm.size:
            self.colourbar.update_normal(self.plot_1x._points)
        self.polar_canvas.draw_idle()

        order = np.argsort(result.rpm)
        self.bode.update(
            result.rpm[order],
            result.amp_1x[order],
            result.phase_1x[order],
            result.amp_1y[order],
            result.phase_1y[order],
        )
        self.bode_canvas.draw_idle()

        status = result.status_line()
        if result.notes:
            status += "   |   " + "; ".join(result.notes)
        self.var_status.set(status)


def launch(run: RunData) -> None:
    app = PolarApp(run)
    app.mainloop()
