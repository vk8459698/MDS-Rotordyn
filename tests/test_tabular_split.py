"""Splitting an Sxp Tabular List export into per-channel files."""

from __future__ import annotations

from pathlib import Path

import pytest

from rotordyn.adre import load_channel
from rotordyn.tabular import read_tabular_blocks, safe_filename, split_tabular_export, write_groups

META = "CH#|Channel Name|Machine Name|Status|Angle|Direction|Speed Units(P)|Amp Unit|Phase Unit|"
SAMPLE = (
    "CH#|Channel Name|Sample#|Sample Cause|Date|Speed(P)|Speed(S)|Direct|Avg Gap|"
    "Inst Gap|1XAmplitude|1X Phase|2XAmplitude|2X Phase|Bandpass|Process Variable|"
)


def _block(sample_no: int) -> list[str]:
    """One repeated configuration + sample block, as ADRE Sxp writes it."""
    stamp = f"03Mar1953 03:03:{sample_no:02d}.468"
    return [
        "",
        META,
        "1|BRG1X|TURBINE|OK|45\u00b0|Right|rpm|mil pp|deg|",
        "2|KPH|TURBINE|OK|90\u00b0|Right|rpm|V pp|deg|",
        "",
        SAMPLE,
        f"1|BRG1X|{sample_no}|DT-T|{stamp}|3599|0|0.994|-10.4|-10.4|0.454|138BMA|0.221|25|0.965||",
        f"2|KPH|{sample_no}|DT-T|{stamp}|3599|0|10.66|-10.0|-10.0|||||10.62||",
    ]


@pytest.fixture()
def export(tmp_path: Path) -> Path:
    lines = ["", ""]
    for sample_no in (1, 2, 3):
        lines.extend(_block(sample_no))
    path = tmp_path / "export_static.csv"
    path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-16", newline="")
    return path


class TestReadBlocks:
    def test_repeated_config_collapses_to_one_row_per_channel(self, export: Path):
        meta_header, meta, sample_header, rows = read_tabular_blocks(export)
        assert meta_header[:2] == ["CH#", "Channel Name"]
        assert sorted(meta) == ["BRG1X", "KPH"]
        assert sample_header[2] == "Sample#"
        assert [len(v) for v in rows.values()] == [3, 3]

    def test_duplicate_sample_numbers_are_dropped(self, tmp_path: Path):
        lines = ["", ""]
        for sample_no in (1, 1, 2):
            lines.extend(_block(sample_no))
        path = tmp_path / "dupes.csv"
        path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-16", newline="")
        _, _, _, rows = read_tabular_blocks(path)
        assert [r[2] for r in rows["BRG1X"]] == ["1", "2"]

    def test_rejects_a_file_that_is_not_a_tabular_export(self, tmp_path: Path):
        path = tmp_path / "plain.csv"
        path.write_text("a,b\n1,2\n", encoding="utf-8")
        with pytest.raises(ValueError, match="not an ADRE Sxp Tabular List"):
            read_tabular_blocks(path)


class TestSplit:
    def test_writes_one_file_per_channel_plus_a_single_config_table(self, export, tmp_path):
        result = split_tabular_export(export, tmp_path / "channels")
        assert result.n_channels == 2
        assert result.n_samples == 6
        assert result.config_rows_in == 6
        assert result.config_rows_out == 2
        assert result.duplicate_config_rows == 4
        names = {p.name for p in result.channel_files}
        assert names == {"BRG1X.csv", "Kph 1.csv"}
        assert result.config_file.is_file()
        config = result.config_file.read_text(encoding="utf-8-sig").splitlines()
        assert len(config) == 3  # header plus one row per channel

    def test_split_files_reload_with_the_normal_channel_reader(self, export, tmp_path):
        result = split_tabular_export(export, tmp_path / "channels")
        brg = load_channel(tmp_path / "channels" / "BRG1X.csv")
        assert brg.name == "BRG1X"
        assert brg.machine == "TURBINE"
        assert brg.angle_deg == 45
        assert brg.amp_unit == "mil pp"
        assert brg.n_samples == 3
        assert brg.samples["1XAmplitude"].tolist() == [0.454, 0.454, 0.454]
        # Vendor status codes survive the round trip.
        assert brg.samples["1X Phase__flag"].tolist() == ["BMA"] * 3
        assert result.channel_files

    def test_columns_empty_for_a_channel_are_dropped(self, export, tmp_path):
        split_tabular_export(export, tmp_path / "channels")
        text = (tmp_path / "channels" / "BRG1X.csv").read_text(encoding="utf-16")
        assert "Process Variable" not in text
        assert "1XAmplitude" in text

    def test_keeping_every_column_is_opt_in(self, export, tmp_path):
        split_tabular_export(export, tmp_path / "all", drop_empty_columns=False)
        text = (tmp_path / "all" / "BRG1X.csv").read_text(encoding="utf-16")
        assert "Process Variable" in text


class TestWebsiteContract:
    """Dashboard.jsx ``preprocessCSV`` only reads the transducer metadata when
    exactly two non-empty lines precede the sample header."""

    def test_exactly_two_non_empty_lines_precede_the_sample_header(self, export, tmp_path):
        result = split_tabular_export(export, tmp_path / "channels")
        for path in result.channel_files:
            lines = path.read_text(encoding="utf-16").splitlines()
            header = next(i for i, l in enumerate(lines) if l.startswith("CH#\tChannel Name\tSample#"))
            assert len([l for l in lines[:header] if l.strip()]) == 2, path.name

    def test_the_raw_export_breaks_that_rule(self, export):
        lines = export.read_text(encoding="utf-16").splitlines()
        header = next(i for i, l in enumerate(lines) if l.startswith("CH#|Channel Name|Sample#"))
        assert len([l for l in lines[:header] if l.strip()]) > 2


class TestSafeFilename:
    def test_strips_characters_windows_rejects(self):
        assert safe_filename("KEYPH.-LP13") == "KEYPH.-LP13"
        assert safe_filename("A/B:C") == "A_B_C"
        assert safe_filename("trailing.") == "trailing"
        assert safe_filename("") == "channel"


class TestWriteGroups:
    def test_one_machine_stays_in_rotor_folder(self, tmp_path):
        brg = tmp_path / "BRG1X.csv"
        brg.write_text("x", encoding="utf-8")
        groups = write_groups([brg], tmp_path / "groups", machines={brg: "TURBINE"})
        assert (tmp_path / "groups" / "rotor" / "BRG1X.csv").is_file()
        assert "rotor" in groups

    def test_separate_rotors_when_machine_names_differ(self, tmp_path):
        gt = tmp_path / "BRG1X.csv"
        st = tmp_path / "BRG3X.csv"
        gt.write_text("x", encoding="utf-8")
        st.write_text("x", encoding="utf-8")
        groups = write_groups(
            [gt, st],
            tmp_path / "groups",
            machines={gt: "7HA GT", st: "D3 STG"},
        )
        assert (tmp_path / "groups" / "7HA GT" / "rotor" / "BRG1X.csv").is_file()
        assert (tmp_path / "groups" / "D3 STG" / "rotor" / "BRG3X.csv").is_file()
        assert "7HA GT/rotor" in groups
        assert "D3 STG/rotor" in groups
