"""Polar-plot tooling for Bently Nevada ADRE vibration exports.

Plotly and Dash are only imported by ``rotordyn.webplots`` and
``rotordyn.webapp``, which are deliberately left out of this module so the
analysis core stays importable without the web dependencies installed.
"""

from .adre import AdreChannel, load_channel, load_channel_bytes, load_directory
from .buddy import BuddyReport, analyse_path, analyse_paths, analyse_run
from .mds import MdsContext, MdsReport, diagnose, diagnose_path, diagnose_paths, diagnose_run
from .centreline import centreline_positions, gap_to_mils, rest_reference
from .dataset import RunData, build_run, build_run_from_channels
from .model import PolarModel, ViewResult, ViewSettings
from .vectors import (
    PROBE_1X_DEG,
    PROBE_1Y_DEG,
    KEYPHASOR_DEG,
    ROTATION_SENSE,
    to_phasor,
    to_amp_phase,
    probe_phasor,
    rotate_probe_pair,
    slow_roll_vector,
)

__all__ = [
    "AdreChannel",
    "load_channel",
    "load_channel_bytes",
    "load_directory",
    "RunData",
    "build_run",
    "build_run_from_channels",
    "PolarModel",
    "ViewSettings",
    "ViewResult",
    "centreline_positions",
    "gap_to_mils",
    "rest_reference",
    "PROBE_1X_DEG",
    "PROBE_1Y_DEG",
    "KEYPHASOR_DEG",
    "ROTATION_SENSE",
    "to_phasor",
    "to_amp_phase",
    "probe_phasor",
    "rotate_probe_pair",
    "slow_roll_vector",
    "BuddyReport",
    "analyse_run",
    "analyse_path",
    "analyse_paths",
    "MdsContext",
    "MdsReport",
    "diagnose",
    "diagnose_run",
    "diagnose_path",
    "diagnose_paths",
]
