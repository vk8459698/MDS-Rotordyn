"""ADRE-export → analysis pipeline.

ADRE Sxp owns the native ``.dat`` database.  This module never reads those
files.  The intended loop is:

1. Open the database folder in ADRE Sxp (on the Windows EC2 box).
2. Export channel CSVs into an inbox directory (1X, 1Y, keyphasor).
3. Call :func:`process_export_directory` (or ``python main.py --watch``).
4. Read the summary and figures written to the outbox.

That is the semi-automatic system: ADRE converts, RotorDyn interprets.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from .dataset import RunData, build_run
from .summary import describe_run


@dataclass(frozen=True)
class ProcessResult:
    source: Path
    outbox: Path
    summary_path: Path
    figure_paths: list[Path]
    fingerprint: str


def export_fingerprint(directory: Path) -> str:
    """Stable id for the CSV set currently in ``directory``."""
    parts: list[str] = []
    for path in sorted(directory.glob("*.csv")):
        stat = path.stat()
        parts.append(f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}")
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def is_export_ready(directory: Path) -> bool:
    """True when the folder looks like a complete ADRE channel export set."""
    if not directory.is_dir():
        return False
    if len(list(directory.glob("*.csv"))) < 3:
        return False
    try:
        build_run(directory)
    except (FileNotFoundError, ValueError, KeyError):
        return False
    return True


def _write_summary(run: RunData, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(describe_run(run) + "\n", encoding="utf-8")
    return path


def process_export_directory(
    source: str | Path,
    outbox: str | Path = "outbox",
    *,
    copy_sources: bool = True,
) -> ProcessResult:
    """Load ADRE CSVs from ``source``, write summary + figures under ``outbox``."""
    import matplotlib

    matplotlib.use("Agg")
    from .export import export_all

    source = Path(source).resolve()
    run = build_run(source)
    fingerprint = export_fingerprint(source)

    stamp = time.strftime("%Y%m%d_%H%M%S")
    label = source.name if source.name not in {".", ""} else "export"
    dest = Path(outbox).resolve() / f"{label}_{stamp}_{fingerprint}"
    figures_dir = dest / "figures"
    dest.mkdir(parents=True, exist_ok=True)

    summary_path = _write_summary(run, dest / "summary.txt")
    figure_paths = export_all(run, figures_dir)

    if copy_sources:
        sources_dir = dest / "sources"
        sources_dir.mkdir(exist_ok=True)
        for path in sorted(source.glob("*.csv")):
            shutil.copy2(path, sources_dir / path.name)

    meta = {
        "source": str(source),
        "fingerprint": fingerprint,
        "files": run.source_files,
        "n_samples": int(len(run.frame)),
        "figures": [str(p.name) for p in figure_paths],
    }
    (dest / "run.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    (source / ".rotordyn_done").write_text(fingerprint + "\n", encoding="utf-8")

    return ProcessResult(
        source=source,
        outbox=dest,
        summary_path=summary_path,
        figure_paths=figure_paths,
        fingerprint=fingerprint,
    )


def _already_processed(directory: Path) -> bool:
    marker = directory / ".rotordyn_done"
    if not marker.is_file():
        return False
    return marker.read_text(encoding="utf-8").strip() == export_fingerprint(directory)


def discover_export_dirs(inbox: Path) -> list[Path]:
    """Inbox itself, plus each immediate subdirectory that holds CSVs."""
    inbox = Path(inbox)
    candidates = [inbox]
    if inbox.is_dir():
        candidates.extend(sorted(p for p in inbox.iterdir() if p.is_dir()))
    return [
        path
        for path in candidates
        if path.is_dir() and list(path.glob("*.csv")) and not _already_processed(path)
    ]


def watch_inbox(
    inbox: str | Path = "inbox",
    outbox: str | Path = "outbox",
    *,
    poll_seconds: float = 5.0,
    once: bool = False,
) -> list[ProcessResult]:
    """Poll ``inbox`` for complete CSV exports and process each one.

    With ``once=True``, scan a single time and return (useful for tests and
    scheduled tasks).  Otherwise block until interrupted.
    """
    inbox = Path(inbox)
    outbox = Path(outbox)
    inbox.mkdir(parents=True, exist_ok=True)
    outbox.mkdir(parents=True, exist_ok=True)

    done: list[ProcessResult] = []
    while True:
        for directory in discover_export_dirs(inbox):
            if not is_export_ready(directory):
                continue
            # Wait until files stop growing so a mid-copy export is not read.
            fingerprint = export_fingerprint(directory)
            time.sleep(min(poll_seconds, 2.0))
            if export_fingerprint(directory) != fingerprint:
                continue
            result = process_export_directory(directory, outbox)
            done.append(result)
        if once:
            return done
        time.sleep(poll_seconds)
