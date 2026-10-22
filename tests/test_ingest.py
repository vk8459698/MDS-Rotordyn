"""ADRE Sxp ingest: preserve, validate, inventory (no ADRE install required)."""

from __future__ import annotations

import struct
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

import pytest

from rotordyn.ingest import (
    fingerprint_path,
    ingest,
    inventory_database,
    verify_import,
)

FILETIME_EPOCH = 11_644_473_600

CONFIG_XML = """<?xml version="1.0" encoding="unicode"?>
<ADRE_ROOT xmlns="xsdUserDBADRE" adre_db_version="3.10" revision="4" DBGuid="test-guid">
  <hierarchy_root>
    <segment SID="1" Type="2" Level="0">
      <name>{db}</name>
      <properties>
        <property PropID="1100" DataType="3" NumValues="1"><value1>City Plant</value1></property>
        <property PropID="1101" DataType="3" NumValues="1"><value1>Test Site</value1></property>
        <property PropID="1102" DataType="3" NumValues="1"><value1>Unit 1 GT</value1></property>
      </properties>
      <children>
        <segment SID="2" Type="3" Level="1">
          <name>ADRE 408</name>
          <properties />
          <children>
            <segment SID="53" Type="6" Level="2">
              <name>Dynamic Sampling Card</name>
              <properties />
              <children>
                <segment SID="182" Type="10" Level="3">
                  <name>BRG1X</name>
                  <properties />
                  <children>
                    <segment SID="183" Type="255" Level="4"><name>Direct</name><properties /><children /></segment>
                    <segment SID="184" Type="257" Level="4"><name>Avg Gap</name><properties /><children /></segment>
                    <segment SID="198" Type="265" Level="4"><name>Async Waveform</name><properties /><children /></segment>
                    <segment SID="199" Type="264" Level="4"><name>Sync Waveform</name><properties /><children /></segment>
                  </children>
                </segment>
              </children>
            </segment>
            <segment SID="54" Type="6" Level="2">
              <name>Keyphasor Card</name>
              <properties />
              <children>
                <segment SID="300" Type="10" Level="3">
                  <name>Kph 1</name>
                  <properties />
                  <children>
                    <segment SID="301" Type="255" Level="4"><name>Direct</name><properties /><children /></segment>
                    <segment SID="302" Type="264" Level="4"><name>Sync Waveform</name><properties /><children /></segment>
                  </children>
                </segment>
              </children>
            </segment>
          </children>
        </segment>
      </children>
    </segment>
  </hierarchy_root>
</ADRE_ROOT>
"""


def _filetime(dt: datetime) -> int:
    return int((dt.timestamp() + FILETIME_EPOCH) * 10_000_000)


def _payload_header(start: datetime, end: datetime) -> bytes:
    raw = bytearray(140)
    struct.pack_into("<Q", raw, 52, _filetime(start))
    struct.pack_into("<Q", raw, 60, _filetime(end))
    struct.pack_into("<I", raw, 68, 140)
    struct.pack_into("<I", raw, 72, 1000)
    return bytes(raw)


def make_database(folder: Path, name: str = "MiniADRE") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "ADRE SXP.adb").write_bytes(b"ADRE")
    xml = CONFIG_XML.format(db=escape(name))
    (folder / "ConfigDBFile.xml").write_text(xml, encoding="utf-16")
    data = folder / "Data"
    data.mkdir()
    data.joinpath("CG0MaxSample.dat").write_bytes(struct.pack("<I", 7264) + b"\x00" * 12)
    data.joinpath("WaveformCG0MaxSample.dat").write_bytes(struct.pack("<I", 729) + b"\x00" * 16)
    start = datetime(2026, 4, 29, 15, 13, 20, tzinfo=timezone.utc)
    end = datetime(2026, 5, 1, 11, 48, 39, tzinfo=timezone.utc)
    data.joinpath("Box0Slot0Chan0_0.dat").write_bytes(_payload_header(start, end))
    return folder


def test_inventory_reads_config_and_bookkeeping(tmp_path):
    root = make_database(tmp_path / "db")
    info = inventory_database(root)
    assert info["database_name"] == "MiniADRE"
    assert info["plant"] == "Test Site"
    assert info["unit"] == "Unit 1 GT"
    assert info["static_samples"] == 7264
    assert info["waveform_samples"] == 729
    names = [ch["name"] for ch in info["channels"]]
    assert names == ["BRG1X", "Kph 1"]
    brg = info["channels"][0]
    assert brg["has_sync"] and brg["has_async"] and brg["has_static"]
    assert info["period_start"].startswith("2026-04-29")


def test_ingest_zip_is_partial_without_csvs(tmp_path):
    db = make_database(tmp_path / "db")
    archive = tmp_path / "job.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for path in db.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(db.parent).as_posix())
    result = ingest(archive, vault=tmp_path / "imports", skip_scan=True)
    assert result.status == "partial"
    assert result.import_id.startswith("imp_")
    assert len(result.fingerprint) == 64
    assert (Path(result.folder) / "original" / "job.zip").is_file()
    assert (Path(result.folder) / "EXPORT_PLAN.txt").is_file()
    assert result.inventory["n_channels"] == 2
    assert result.scan["status"] == "skipped"


def test_ingest_rejects_random_zip(tmp_path):
    archive = tmp_path / "notes.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("readme.txt", "not a database")
    result = ingest(archive, vault=tmp_path / "imports", skip_scan=True)
    assert result.status == "failed"
    assert any("did not contain" in err for err in result.errors)


def test_ingest_rejects_path_traversal_zip(tmp_path):
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../outside.adb", "nope")
    result = ingest(archive, vault=tmp_path / "imports", skip_scan=True)
    assert result.status == "failed"


def test_in_place_does_not_copy(tmp_path):
    db = make_database(tmp_path / "live")
    result = ingest(db, vault=tmp_path / "imports", in_place=True, skip_scan=True)
    assert result.status == "partial"
    original = Path(result.folder) / "original"
    assert not original.exists() or not any(original.rglob("*.adb"))
    pointer = Path(result.folder) / "original_pointer.txt"
    assert pointer.is_file()
    assert Path(pointer.read_text(encoding="utf-8").strip()) == db.resolve()


def test_verify_marks_complete_when_all_three_kinds_present(tmp_path):
    db = make_database(tmp_path / "db")
    result = ingest(db, vault=tmp_path / "imports", in_place=True, skip_scan=True)
    exports = Path(result.folder) / "exports"
    exports.joinpath("static.csv").write_text(
        "\n".join(
            [
                "",
                "CH#\tChannel Name\tMachine Name\tStatus\tAngle\tDirection\tSpeed Units(P)\tAmp Unit\tPhase Unit\t",
                "1\tBRG1X\tRig\tOK\t45°\tRight\trpm\tmil pp\tdeg\t",
                "",
                "CH#\tChannel Name\tSample#\tSample Cause\tDate\tSpeed(P)\tDirect\tAvg Gap\t1XAmplitude\t1X Phase\t",
                "1\tBRG1X\t10\tDR-T\t30Apr2026 19:28:50.563\t1200\t1.5\t-9.8\t0.500\t120\t",
                "",
            ]
        ),
        encoding="utf-16",
    )
    exports.joinpath("sync.csv").write_text(
        "Plot Name|Waveform Sample Number|Date|Channel|Variable|Speed|\n"
        "New Timebase Plot|1|29 Apr 2026|BRG1X|Sync Waveform|3600 rpm|\n",
        encoding="utf-16",
    )
    exports.joinpath("async.csv").write_text(
        "Plot Name|Waveform Sample Number|Date|Channel|Variable|Speed|\n"
        "New Timebase Plot|1|29 Apr 2026|BRG1X|Async Waveform|3600 rpm|\n",
        encoding="utf-16",
    )
    checked = verify_import(result.import_id, tmp_path / "imports")
    assert checked.status == "complete"
    assert checked.exports["has_static"]
    assert checked.exports["has_sync"]
    assert checked.exports["has_async"]


def test_fingerprint_changes_when_bytes_change(tmp_path):
    path = tmp_path / "a.bin"
    path.write_bytes(b"abc")
    first = fingerprint_path(path)
    path.write_bytes(b"abd")
    assert fingerprint_path(path) != first


def test_inventory_real_dukecitrus():
    root = Path("CT2B_DukeCitrus_04292026")
    if not (root / "ConfigDBFile.xml").is_file():
        pytest.skip("DukeCitrus database not in the workspace")
    info = inventory_database(root)
    names = {ch["name"] for ch in info["channels"]}
    assert "BRG1X" in names
    assert "BRG1Y" in names
    assert "Kph 1" in names
    assert info["static_samples"] == 7264
    assert info["waveform_samples"] == 729
    assert info["period_start"].startswith("2026-04-29")


def test_split_static_skips_until_tabular_csv_exists(tmp_path):
    from rotordyn.ingest import split_static_exports

    folder = tmp_path / "exports"
    folder.mkdir()
    (folder / "export_sync.csv").write_text(
        "Plot Name|Waveform Sample Number|Date|Channel|Variable|Speed|\n"
        "New Timebase Plot|1|29 Apr 2026|BRG1X|Sync Waveform|3600 rpm|\n",
        encoding="utf-16",
    )
    assert split_static_exports(folder) == {}


def test_resolve_user_path_uses_rotordyn_folder(tmp_path, monkeypatch):
    from rotordyn import ingest as ing

    archive = tmp_path / "job_from_project.zip"
    archive.write_bytes(b"PK\x03\x04")
    monkeypatch.setattr(ing, "_PROJECT_ROOT", tmp_path)
    found = ing.resolve_user_path("job_from_project.zip")
    assert found == archive
