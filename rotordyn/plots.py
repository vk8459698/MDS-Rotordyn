"""Matplotlib layer: polar (vector) plots and a supporting Bode plot."""

from __future__ import annotations

import numpy as np
from matplotlib.collections import LineCollection
from matplotlib.figure import Figure

from .model import speed_label_indices as _speed_label_indices
from .vectors import ROTATION_SENSE, describe_position

#: Phase lag grows against the direction of shaft rotation, so with a
#: counter-clockwise shaft the phase scale on the polar plot runs clockwise.
_THETA_DIRECTION = -1 if ROTATION_SENSE == "CCW" else 1

_TRACK_CMAP = "viridis"


class PolarPlot:
    """A single probe's polar plot, updated in place as controls change."""

    def __init__(self, ax, probe_label: str, amp_unit: str, colour: str) -> None:
        self.ax = ax
        self.probe_label = probe_label
        self.amp_unit = amp_unit
        self.colour = colour
        self._labels: list = []

        ax.set_theta_direction(_THETA_DIRECTION)
        ax.set_theta_offset(np.pi / 2)
        ax.set_thetagrids(np.arange(0, 360, 30))
        ax.set_rlabel_position(135)
        ax.tick_params(axis="y", labelsize=7, colors="#666")
        ax.grid(True, alpha=0.35, linestyle=":")
        ax.set_facecolor("#fbfbfd")

        self._track = LineCollection([], cmap=_TRACK_CMAP, linewidths=1.6, zorder=2)
        ax.add_collection(self._track)
        self._points = ax.scatter(
            [], [], s=9, c=[], cmap=_TRACK_CMAP, zorder=3, edgecolors="none"
        )
        self._points.set_clim(0, 1)
        (self._start,) = ax.plot(
            [], [], "o", ms=9, mfc="none", mec="#1b7f3b", mew=2, zorder=4, label="first sample"
        )
        (self._end,) = ax.plot(
            [], [], "s", ms=9, mfc="none", mec="#b02418", mew=2, zorder=4, label="last sample"
        )
        self._probe_marker = ax.annotate(
            "", xy=(0, 0), xytext=(0, 0), ha="center", va="center", fontsize=8, color="#444"
        )

    def set_orientation(self, probe_angle_deg: float, machine_view: bool) -> None:
        """Place 0 deg of phase at the top, or at the probe's clock position.

        The machine view is how a Bently polar plot is normally read: the plot
        is oriented like the machine as you look at it, so the plotted vector
        points at the physical high spot.
        """
        offset = np.pi / 2
        if machine_view:
            # Screen angle of a plotted phase theta is (offset - theta) because
            # the phase scale runs clockwise, so putting phase 0 at the probe
            # means shifting the offset by the probe's clock angle.
            offset -= np.deg2rad(probe_angle_deg)
        self.ax.set_theta_offset(offset)

    def update(
        self,
        amp: np.ndarray,
        phase_deg: np.ndarray,
        rpm: np.ndarray,
        probe_angle_deg: float,
        r_max: float,
        show_labels: bool = True,
    ) -> None:
        amp = np.asarray(amp, dtype=float)
        theta = np.deg2rad(np.asarray(phase_deg, dtype=float))
        rpm = np.asarray(rpm, dtype=float)

        for label in self._labels:
            label.remove()
        self._labels = []

        if amp.size == 0:
            self._track.set_segments([])
            self._points.set_offsets(np.empty((0, 2)))
            self._start.set_data([], [])
            self._end.set_data([], [])
        else:
            points = np.column_stack([theta, amp])
            segments = np.stack([points[:-1], points[1:]], axis=1)
            self._track.set_segments(list(segments))
            if segments.size:
                self._track.set_array(0.5 * (rpm[:-1] + rpm[1:]))
                self._track.set_clim(float(np.nanmin(rpm)), float(np.nanmax(rpm)))
            self._points.set_offsets(points)
            self._points.set_array(rpm)
            self._points.set_clim(float(np.nanmin(rpm)), float(np.nanmax(rpm)))
            self._start.set_data([theta[0]], [amp[0]])
            self._end.set_data([theta[-1]], [amp[-1]])

            if show_labels:
                for i in _speed_label_indices(rpm):
                    self._labels.append(
                        self.ax.annotate(
                            f"{rpm[i]:.0f}",
                            xy=(theta[i], amp[i]),
                            xytext=(4, 4),
                            textcoords="offset points",
                            fontsize=7,
                            color="#333",
                            zorder=5,
                            bbox={
                                "boxstyle": "round,pad=0.12",
                                "facecolor": "white",
                                "edgecolor": "none",
                                "alpha": 0.7,
                            },
                        )
                    )

        self.ax.set_ylim(0, r_max)
        self.ax.set_title(
            f"{self.probe_label}  -  {describe_position(probe_angle_deg)}",
            fontsize=11,
            pad=14,
            color=self.colour,
            fontweight="bold",
        )
        self.ax.set_xlabel(
            f"radius = 1X amplitude [{self.amp_unit}]   |   angle = 1X phase lag [deg]",
            fontsize=8,
            labelpad=12,
        )


def build_polar_figure(amp_unit: str) -> tuple[Figure, PolarPlot, PolarPlot, object]:
    """Two polar axes side by side plus a shared speed colour bar.

    Margins are set explicitly rather than by ``tight_layout`` so the layout
    stays stable while the user resizes the window.
    """
    fig = Figure(figsize=(10.6, 5.2), dpi=100)
    fig.patch.set_facecolor("white")
    ax_x = fig.add_subplot(1, 2, 1, projection="polar")
    ax_y = fig.add_subplot(1, 2, 2, projection="polar")
    # Polar axes push their title above the ring of theta tick labels, so the
    # top margin has to leave room for the title *and* those labels.
    fig.subplots_adjust(left=0.04, right=0.87, top=0.78, bottom=0.16, wspace=0.22)
    plot_x = PolarPlot(ax_x, "1X probe", amp_unit, "#1f4e9c")
    plot_y = PolarPlot(ax_y, "1Y probe", amp_unit, "#a1420a")
    cax = fig.add_axes([0.905, 0.20, 0.016, 0.56])
    colourbar = fig.colorbar(plot_x._points, cax=cax, label="shaft speed [rpm]")
    ax_x.legend(
        loc="lower left", bbox_to_anchor=(-0.14, -0.1), fontsize=8, frameon=False, handletextpad=0.4
    )
    fig.suptitle("Synchronous (1X) vector polar plots", fontsize=13, fontweight="bold", y=0.985)
    return fig, plot_x, plot_y, colourbar


def annotate_conventions(fig: Figure, text: str) -> object:
    """Persistent convention note along the bottom of a figure."""
    return fig.text(0.5, 0.025, text, ha="center", fontsize=8, color="#333")


class BodePlot:
    """Amplitude and phase against speed for both probes."""

    def __init__(self, fig: Figure, amp_unit: str) -> None:
        self.amp_axis = fig.add_subplot(2, 1, 1)
        self.phase_axis = fig.add_subplot(2, 1, 2, sharex=self.amp_axis)
        self.amp_unit = amp_unit
        self._lines = {}
        for key, label, colour in (
            ("1x", "1X probe", "#1f4e9c"),
            ("1y", "1Y probe", "#a1420a"),
        ):
            (self._lines[f"amp_{key}"],) = self.amp_axis.plot(
                [], [], lw=1.4, color=colour, label=label
            )
            (self._lines[f"phase_{key}"],) = self.phase_axis.plot(
                [], [], lw=1.4, color=colour, label=label
            )
        self.amp_axis.set_ylabel(f"1X amplitude [{amp_unit}]")
        self.amp_axis.legend(loc="upper left", fontsize=9)
        self.amp_axis.grid(alpha=0.3, linestyle=":")
        self.phase_axis.set_ylabel("1X phase lag [deg]")
        self.phase_axis.set_xlabel("shaft speed [rpm]")
        self.phase_axis.set_ylim(0, 360)
        self.phase_axis.set_yticks(np.arange(0, 361, 90))
        self.phase_axis.grid(alpha=0.3, linestyle=":")
        fig.suptitle("Bode plot - 1X amplitude and phase against speed", fontsize=12)

    def update(
        self,
        rpm: np.ndarray,
        amp_1x: np.ndarray,
        phase_1x: np.ndarray,
        amp_1y: np.ndarray,
        phase_1y: np.ndarray,
    ) -> None:
        self._lines["amp_1x"].set_data(rpm, amp_1x)
        self._lines["amp_1y"].set_data(rpm, amp_1y)
        # Break the line where phase wraps so the 0/360 jump is not drawn.
        self._lines["phase_1x"].set_data(*_break_wraps(rpm, phase_1x))
        self._lines["phase_1y"].set_data(*_break_wraps(rpm, phase_1y))
        if rpm.size:
            self.amp_axis.set_xlim(float(np.nanmin(rpm)), float(np.nanmax(rpm)))
            top = float(np.nanmax([np.nanmax(amp_1x), np.nanmax(amp_1y)])) * 1.1
            self.amp_axis.set_ylim(0, max(top, 1e-3))


def _break_wraps(x: np.ndarray, phase: np.ndarray, threshold: float = 180.0):
    """Insert NaNs where a phase trace wraps through 0/360."""
    x = np.asarray(x, dtype=float)
    phase = np.asarray(phase, dtype=float)
    if x.size < 2:
        return x, phase
    jumps = np.where(np.abs(np.diff(phase)) > threshold)[0] + 1
    return np.insert(x, jumps, np.nan), np.insert(phase, jumps, np.nan)
