"""Tests for the ADRE-export → analysis watch pipeline."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from rotordyn.pipeline import (
    discover_export_dirs,
    export_fingerprint,
    is_export_ready,
    process_export_directory,
    watch_inbox,
)

DATA_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture()
def export_dir(tmp_path: Path) -> Path:
    folder = tmp_path / "run_a"
    folder.mkdir()
    for name in ("CT2B_BRG1X.csv", "CT2B_BRG1Y.csv", "KPH.csv"):
        shutil.copy2(DATA_DIR / name, folder / name)
    return folder


class TestReadiness:
    def test_complete_export_is_ready(self, export_dir):
        assert is_export_ready(export_dir)

    def test_two_files_are_not_ready(self, export_dir):
        (export_dir / "KPH.csv").unlink()
        assert not is_export_ready(export_dir)

    def test_fingerprint_changes_when_a_file_changes(self, export_dir):
        first = export_fingerprint(export_dir)
        target = export_dir / "KPH.csv"
        target.write_bytes(target.read_bytes() + b"\n")
        assert export_fingerprint(export_dir) != first


class TestProcessing:
    def test_process_writes_summary_and_figures(self, export_dir, tmp_path):
        result = process_export_directory(export_dir, tmp_path / "outbox")
        assert result.summary_path.is_file()
        assert "SOURCE FILES" in result.summary_path.read_text(encoding="utf-8")
        assert result.figure_paths
        assert all(path.is_file() for path in result.figure_paths)
        assert (result.outbox / "run.json").is_file()
        assert (result.outbox / "sources" / "CT2B_BRG1X.csv").is_file()
        assert (export_dir / ".rotordyn_done").read_text(encoding="utf-8").strip() == (
            result.fingerprint
        )

    def test_processed_folder_is_skipped_by_discovery(self, export_dir, tmp_path):
        process_export_directory(export_dir, tmp_path / "outbox")
        assert discover_export_dirs(export_dir.parent) == []

    def test_watch_once_processes_inbox_subfolder(self, export_dir, tmp_path):
        inbox = tmp_path / "inbox"
        outbox = tmp_path / "outbox"
        shutil.copytree(export_dir, inbox / "DukeCitrus")
        results = watch_inbox(inbox, outbox, once=True)
        assert len(results) == 1
        assert results[0].summary_path.is_file()
