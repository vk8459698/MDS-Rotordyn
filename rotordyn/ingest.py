"""Ingest native ADRE Sxp databases: preserve, validate, inventory, export.

Kiran Toram's workflow (receive → validate → open → inventory → CSV export →
verify) is the public contract.  Native ``.dat`` payloads stay proprietary, so
CSV extraction still needs ADRE Sxp on Windows.  Everything before that step
runs anywhere: fingerprint the upload, unzip safely, read ``ConfigDBFile.xml``,
and write a per-import report.

Typical use::

    python main.py --ingest incoming\\job.zip
    python main.py --ingest CT2B_DukeCitrus_04292026 --in-place
    python main.py --ingest-status
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import struct
import subprocess
import time
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

FILETIME_EPOCH = 11_644_473_600
_FILETIME_LOW = int((946_684_800 + FILETIME_EPOCH) * 1e7)
_FILETIME_HIGH = int((2_524_608_000 + FILETIME_EPOCH) * 1e7)
CHUNK_T_FIRST = 52
CHUNK_T_LAST = 60
MAX_ZIP_UNCOMPRESSED = 80 * 1024 * 1024 * 1024  # 80 GiB

_STATIC_NAMES = {
    "direct",
    "avg gap",
    "inst gap",
    "1x",
    "2x",
    "bandpass",
    "speed",
}

_DEFENDER_CANDIDATES = (
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Windows Defender" / "MpCmdRun.exe",
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Windows Defender" / "MpCmdRun.exe",
)


class IngestError(RuntimeError):
    """The upload is not a usable ADRE Sxp database."""


@dataclass
class ScanResult:
    status: str  # clean | threat | skipped | error
    tool: str = ""
    detail: str = ""


@dataclass
class IngestResult:
    import_id: str
    status: str  # complete | partial | failed
    source: str
    folder: str
    fingerprint: str
    fingerprint_alg: str = "sha256"
    received_at: str = ""
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    scan: dict[str, Any] = field(default_factory=dict)
    inventory: dict[str, Any] = field(default_factory=dict)
    exports: dict[str, Any] = field(default_factory=dict)
    adre: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    def format_text(self) -> str:
        lines = [
            "ADRE Sxp ingest",
            f"Import ID    {self.import_id}",
            f"Status       {self.status}",
            f"Source       {self.source}",
            f"Fingerprint  {self.fingerprint_alg}:{self.fingerprint}",
            f"Vault        {self.folder}",
            "",
        ]
        if self.notes:
            lines.append("NOTES")
            lines.extend(f"  {note}" for note in self.notes)
            lines.append("")
        if self.errors:
            lines.append("ERRORS")
            lines.extend(f"  {err}" for err in self.errors)
            lines.append("")
        scan = self.scan or {}
        lines.append(
            f"MALWARE SCAN  {scan.get('status', 'n/a')}"
            + (f"  ({scan.get('tool')})" if scan.get("tool") else "")
        )
        if scan.get("detail"):
            lines.append(f"  {scan['detail']}")
        lines.append("")
        inv = self.inventory or {}
        if inv:
            lines.append("INVENTORY")
            for key in (
                "database_name",
                "plant",
                "unit",
                "instrument",
                "adre_db_version",
                "db_guid",
                "static_samples",
                "waveform_samples",
                "period_start",
                "period_end",
            ):
                if inv.get(key) not in (None, "", []):
                    lines.append(f"  {key:<20} {inv[key]}")
            channels = inv.get("channels") or []
            lines.append(f"  channels             {len(channels)}")
            for ch in channels:
                kinds = ", ".join(ch.get("kinds") or []) or "unknown"
                lines.append(
                    f"    {ch.get('sid', '?'):>6}  {ch.get('name', '?'):<24} "
                    f"{ch.get('role', ''):<18} {ch.get('card', ''):<24} {kinds}"
                )
            lines.append("")
        exports = self.exports or {}
        if exports:
            lines.append("EXPORTS")
            lines.append(f"  folder               {exports.get('folder', '')}")
            lines.append(f"  status               {exports.get('status', '')}")
            for item in exports.get("files") or []:
                lines.append(
                    f"    {item.get('kind', '?'):<8}  "
                    f"{item.get('name', '')}  "
                    f"{item.get('detail', '')}"
                )
            split = exports.get("channels") or {}
            if split:
                lines.append(
                    f"  per-channel          {len(split.get('files') or [])} files "
                    f"in {split.get('folder', '')}"
                )
                lines.append(
                    f"  config de-duplicated {split.get('duplicate_config_rows_removed', 0)} "
                    "repeated transducer rows removed"
                )
                groups = split.get("groups") or {}
                if groups:
                    lines.append(
                        "  upload groups         "
                        + ", ".join(f"{name} {len(files)}" for name, files in groups.items())
                    )
                upload_at = split.get("upload_folder")
                if upload_at:
                    lines.append(f"  website upload        {upload_at}")
            if exports.get("plan"):
                lines.append(f"  plan                 {exports['plan']}")
            lines.append("")
        adre = self.adre or {}
        if adre:
            lines.append("ADRE Sxp")
            for key, value in adre.items():
                lines.append(f"  {key:<20} {value}")
        return "\n".join(lines).rstrip() + "\n"


def vault_root(explicit: str | Path | None = None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    env = os.environ.get("ROTORDYN_IMPORTS")
    if env:
        return Path(env).resolve()
    return Path("imports").resolve()


_PROJECT_ROOT = Path(__file__).resolve().parents[1]


def resolve_user_path(raw: str | Path) -> Path:
    """Find a zip/folder the operator named on the command line.

    Bare names like ``3_GE_7HA.zip`` live in the RotorDyn folder. Prefer that
    copy so ingest uploads from the project tree, not a random cwd.
    """
    path = Path(raw).expanduser()
    if not path.is_absolute() and len(path.parts) == 1:
        project = _PROJECT_ROOT / path.name
        if project.exists():
            return project
    if path.exists():
        return path
    for candidate in (_PROJECT_ROOT / path, _PROJECT_ROOT / path.name):
        if candidate.exists():
            return candidate
    return path


def publish_upload_groups(source: Path, channels_split: dict[str, Any]) -> Path | None:
    """Copy static rotor groups next to the project so the website can upload them."""
    src = channels_split.get("groups_folder")
    if not src:
        return None
    src_path = Path(src)
    if not src_path.is_dir():
        return None
    dest = _PROJECT_ROOT / "outbox" / Path(source).stem / "groups"
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src_path, dest)
    channels_split["upload_folder"] = str(dest)
    return dest


def import_folder(import_id: str, vault: str | Path | None = None) -> Path:
    return vault_root(vault) / import_id


def list_imports(vault: str | Path | None = None) -> list[dict[str, str]]:
    root = vault_root(vault)
    if not root.is_dir():
        return []
    rows = []
    for folder in sorted(root.iterdir()):
        manifest = folder / "manifest.json"
        if not manifest.is_file():
            continue
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        rows.append(
            {
                "import_id": data.get("import_id", folder.name),
                "status": data.get("status", ""),
                "source": data.get("source", ""),
                "fingerprint": data.get("fingerprint", ""),
            }
        )
    return rows


def load_manifest(import_id: str, vault: str | Path | None = None) -> IngestResult:
    path = import_folder(import_id, vault) / "manifest.json"
    if not path.is_file():
        raise FileNotFoundError(f"No ingest manifest for {import_id}")
    data = json.loads(path.read_text(encoding="utf-8"))
    known = {k: data[k] for k in IngestResult.__dataclass_fields__ if k in data}
    return IngestResult(**known)


def _write_manifest(result: IngestResult) -> Path:
    folder = Path(result.folder)
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / "manifest.json"
    dest.write_text(result.to_json() + "\n", encoding="utf-8")
    (folder / "report.txt").write_text(result.format_text(), encoding="utf-8")
    return dest


def _sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def fingerprint_path(path: str | Path) -> str:
    """SHA-256 of a file, or of a sorted path+hash listing for a directory."""
    path = Path(path)
    if path.is_file():
        return _sha256_file(path)
    if not path.is_dir():
        raise FileNotFoundError(path)
    digest = hashlib.sha256()
    for child in sorted(p for p in path.rglob("*") if p.is_file()):
        relative = child.relative_to(path).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(_sha256_file(child).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _new_import_id(fingerprint: str) -> str:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    return f"imp_{stamp}_{fingerprint[:8]}"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def find_defender() -> Path | None:
    extra = os.environ.get("ROTORDYN_MPCMDRUN")
    candidates = [Path(extra)] if extra else []
    candidates.extend(_DEFENDER_CANDIDATES)
    for path in candidates:
        if path and path.is_file():
            return path
    return None


def malware_scan(path: str | Path) -> ScanResult:
    """Scan with Windows Defender when present; never claim clean if skipped."""
    path = Path(path)
    exe = find_defender()
    if exe is None:
        return ScanResult(
            status="skipped",
            tool="",
            detail="Windows Defender MpCmdRun.exe not found. Scan the file in Defender or VirusTotal before opening in ADRE.",
        )
    try:
        completed = subprocess.run(
            [str(exe), "-Scan", "-ScanType", "3", "-File", str(path.resolve())],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return ScanResult(status="error", tool=str(exe), detail=str(exc))
    output = (completed.stdout or "") + (completed.stderr or "")
    if completed.returncode == 0:
        return ScanResult(status="clean", tool=str(exe), detail=output.strip() or "no threats")
    if completed.returncode == 2:
        return ScanResult(status="threat", tool=str(exe), detail=output.strip() or "threat detected")
    return ScanResult(
        status="error",
        tool=str(exe),
        detail=f"exit {completed.returncode}: {output.strip()}",
    )


def _looks_like_zip(path: Path) -> bool:
    if path.suffix.lower() in {".zip", ".adbzip"}:
        return True
    if not path.is_file():
        return False
    with path.open("rb") as handle:
        return handle.read(4)[:2] == b"PK"


def _safe_zip_members(zf: zipfile.ZipFile, dest: Path) -> list[zipfile.ZipInfo]:
    dest = dest.resolve()
    members = []
    total = 0
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        if name.startswith("/") or name.startswith("\\") or ".." in Path(name).parts:
            raise IngestError(f"Zip path escapes the extract folder: {info.filename}")
        target = (dest / name).resolve()
        if dest not in target.parents and target != dest:
            raise IngestError(f"Zip path escapes the extract folder: {info.filename}")
        total += max(info.file_size, 0)
        if total > MAX_ZIP_UNCOMPRESSED:
            raise IngestError("Zip uncompressed size exceeds the safety limit (80 GiB)")
        members.append(info)
    return members


def _extract_zip(archive: Path, dest: Path) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        bad = zf.testzip()
        if bad:
            raise IngestError(f"Zip CRC failed on {bad}")
        members = _safe_zip_members(zf, dest)
        zf.extractall(dest, members=members)
    return dest


def _find_database_root(folder: Path) -> Path | None:
    """Folder that contains ConfigDBFile.xml and/or an .adb file."""
    if _is_database_root(folder):
        return folder
    hits = sorted({p.parent for p in folder.rglob("ConfigDBFile.xml")})
    if len(hits) == 1:
        return hits[0]
    adb = sorted(folder.rglob("*.adb"))
    if len(adb) == 1:
        return adb[0].parent
    if hits:
        # Prefer a hit that also has Data\ or an .adb.
        for hit in hits:
            if (hit / "Data").is_dir() or list(hit.glob("*.adb")):
                return hit
        return hits[0]
    if adb:
        return adb[0].parent
    return None


def _is_database_root(folder: Path) -> bool:
    if not folder.is_dir():
        return False
    return (folder / "ConfigDBFile.xml").is_file() or any(folder.glob("*.adb"))


def _copy_preserve(source: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if source.is_file():
        shutil.copy2(source, dest)
        return
    shutil.copytree(source, dest, dirs_exist_ok=True)


def _decode_xml_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-16", "utf-8-sig", "cp1252", "utf-8"):
        try:
            text = raw.decode(encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue
        if "\x00" in text:
            continue
        if text.lstrip().startswith("<?xml"):
            text = text.split("?>", 1)[1]
        return text
    raise IngestError(f"Could not decode {path.name}")


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _cdata_name(node: ET.Element) -> str:
    found = node.find("{*}name")
    if found is None:
        return ""
    return (found.text or "").strip()


def _prop_value(node: ET.Element, prop_id: str) -> str:
    holder = node.find("{*}properties")
    if holder is None:
        return ""
    for prop in holder:
        if prop.get("PropID") != prop_id:
            continue
        for child in prop:
            if _strip_ns(child.tag).startswith("value"):
                return (child.text or "").strip()
    return ""


def _classify_variable(name: str) -> str:
    lowered = name.strip().lower()
    if "async" in lowered and "waveform" in lowered:
        return "async"
    if "sync waveform" in lowered:
        return "sync"
    if lowered in _STATIC_NAMES or lowered.endswith("-direct") or lowered.endswith("-avg gap"):
        return "static"
    if "waveform" in lowered:
        return "waveform"
    return "other"


_CARD_TYPES = {"6", "28"}  # Dynamic Sampling Card, Keyphasor Card
_CHANNEL_TYPES = {
    "10": "proximity",
    "11": "seismic",
    "17": "position",
    "29": "keyphasor",
    "35": "derived_keyphasor",
}


def _walk_segments(node: ET.Element, card: str = ""):
    for child in list(node):
        if _strip_ns(child.tag) == "segment":
            name = _cdata_name(child)
            stype = child.get("Type", "")
            this_card = name if stype in _CARD_TYPES else card
            yield child, this_card
            kids = child.find("{*}children")
            if kids is not None:
                yield from _walk_segments(kids, this_card)
        else:
            yield from _walk_segments(child, card)


def _filetime_iso(value: int) -> str:
    if not (_FILETIME_LOW < value < _FILETIME_HIGH):
        return ""
    seconds = value / 1e7 - FILETIME_EPOCH
    return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_u32(path: Path) -> int | None:
    if not path.is_file() or path.stat().st_size < 4:
        return None
    return struct.unpack_from("<I", path.read_bytes(), 0)[0]


def _payload_period(data_dir: Path) -> tuple[str, str]:
    starts: list[int] = []
    ends: list[int] = []
    if not data_dir.is_dir():
        return "", ""
    for path in sorted(data_dir.glob("Box*_0.dat")):
        try:
            raw = path.read_bytes()[:80]
        except OSError:
            continue
        if len(raw) < 68:
            continue
        first = struct.unpack_from("<Q", raw, CHUNK_T_FIRST)[0]
        last = struct.unpack_from("<Q", raw, CHUNK_T_LAST)[0]
        if _FILETIME_LOW < first < _FILETIME_HIGH:
            starts.append(first)
        if _FILETIME_LOW < last < _FILETIME_HIGH:
            ends.append(last)
    if not starts:
        return "", ""
    return _filetime_iso(min(starts)), _filetime_iso(max(ends) if ends else min(starts))


def inventory_database(root: Path) -> dict[str, Any]:
    """Describe machines, channels, and sample counts from the native folder."""
    config = root / "ConfigDBFile.xml"
    info: dict[str, Any] = {
        "database_name": root.name,
        "adb_files": [p.name for p in sorted(root.glob("*.adb"))],
        "has_config": config.is_file(),
        "has_data": (root / "Data").is_dir(),
        "channels": [],
        "n_payload_files": 0,
    }
    data_dir = root / "Data"
    if data_dir.is_dir():
        info["n_payload_files"] = len(list(data_dir.glob("Box*.dat")))
        info["static_samples"] = _read_u32(data_dir / "CG0MaxSample.dat")
        info["waveform_samples"] = _read_u32(data_dir / "WaveformCG0MaxSample.dat")
        start, end = _payload_period(data_dir)
        info["period_start"] = start
        info["period_end"] = end
        version_path = data_dir / "DatabaseVersion.dat"
        if version_path.is_file() and version_path.stat().st_size >= 4:
            info["database_version_float"] = round(
                struct.unpack_from("<f", version_path.read_bytes(), 0)[0], 2
            )

    if not config.is_file():
        info["warning"] = "ConfigDBFile.xml missing — channel list unknown until ADRE opens the database"
        return info

    root_el = ET.fromstring(_decode_xml_text(config))
    info["adre_db_version"] = root_el.get("adre_db_version", "")
    info["db_guid"] = root_el.get("DBGuid", "")
    info["revision"] = root_el.get("revision", "")

    cards: dict[str, str] = {}
    channels: list[dict[str, Any]] = []
    plant = unit = instrument = database_name = ""
    for segment, parent_card in _walk_segments(root_el):
        stype = segment.get("Type", "")
        level = segment.get("Level", "")
        name = _cdata_name(segment)
        sid = segment.get("SID", "")
        if stype == "2" and not database_name:
            database_name = name
            plant = _prop_value(segment, "1101") or _prop_value(segment, "1100")
            unit = _prop_value(segment, "1102")
        if stype == "3" and not instrument:
            instrument = name
        if stype in _CARD_TYPES:
            cards[sid] = name
        if stype in _CHANNEL_TYPES and level == "3":
            kinds = set()
            kids = segment.find("{*}children")
            variables = []
            if kids is not None:
                for var in kids:
                    if _strip_ns(var.tag) != "segment":
                        continue
                    vname = _cdata_name(var)
                    kind = _classify_variable(vname)
                    if kind != "other":
                        kinds.add(kind)
                    variables.append({"sid": var.get("SID", ""), "name": vname, "kind": kind})
            channels.append(
                {
                    "sid": sid,
                    "name": name,
                    "card": parent_card,
                    "role": _CHANNEL_TYPES[stype],
                    "kinds": sorted(kinds) if kinds else ["channel"],
                    "variables": variables,
                    "has_sync": "sync" in kinds,
                    "has_async": "async" in kinds,
                    "has_static": "static" in kinds,
                }
            )

    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for ch in channels:
        key = ch["name"].casefold()
        if key in seen:
            continue
        seen.add(key)
        unique.append(ch)
    channels = unique

    info["database_name"] = database_name or info["database_name"]
    info["plant"] = plant
    info["unit"] = unit
    info["instrument"] = instrument
    info["cards"] = sorted(set(cards.values()))
    info["channels"] = channels
    info["n_channels"] = len(channels)
    info["n_sync_channels"] = sum(1 for ch in channels if ch.get("has_sync"))
    info["n_async_channels"] = sum(1 for ch in channels if ch.get("has_async"))
    info["n_static_channels"] = sum(1 for ch in channels if ch.get("has_static"))
    return info


def _adre_status() -> dict[str, str]:
    from .adre_automate import AdreNotInstalled, find_adre_executable, load_recipe

    info = {"exe": "", "recipe": "", "export_ready": "no"}
    try:
        info["exe"] = str(find_adre_executable())
    except AdreNotInstalled as exc:
        info["exe"] = str(exc)
        return info
    recipe_path = Path("adre_export_recipe.json")
    example = Path("adre_export_recipe.example.json")
    if not recipe_path.is_file():
        info["recipe"] = f"missing {recipe_path.name}"
        if example.is_file():
            info["recipe"] += "; copy the example and map menus with tools/adre_ui_map.py"
        return info
    recipe = load_recipe(recipe_path)
    placeholders = sum(
        1 for step in recipe.get("steps", []) if step.get("action") in {"placeholder", "todo"}
    )
    info["recipe"] = f"{recipe_path} ({placeholders} placeholder steps)"
    if placeholders == 0 and recipe.get("steps"):
        info["export_ready"] = "yes"
    return info


def _write_export_plan(folder: Path, working: Path, inventory: dict[str, Any]) -> Path:
    dest = folder / "EXPORT_PLAN.txt"
    channels = inventory.get("channels") or []
    prox = [ch["name"] for ch in channels if "brg" in ch["name"].lower()]
    kph = [ch["name"] for ch in channels if "kph" in ch["name"].lower()]
    lines = [
        "ADRE Sxp CSV export plan for this import",
        f"Working copy: {working}",
        f"Save CSVs into: {folder / 'exports'}",
        "",
        "CSV extraction is owned by ADRE Sxp. RotorDyn does not decode Data\\*.dat.",
        "",
        "1. Start ADRE Sxp. Connect / Open database → this working folder",
        "   (the folder that contains ADRE SXP.adb and ConfigDBFile.xml).",
        "2. Static (Tabular List): in Configuration Hierarchy, Ctrl-click the",
        "   proximity probes and the keyphasor, then Plots → Tabular List.",
        "   Include Direct, Avg Gap, 1X Amplitude, 1X Phase. Export → CSV.",
        "3. Dynamic sync: open a Timebase of Sync Waveform for those probes,",
        "   Export → CSV (concatenated timebase dump).",
        "4. Dynamic async: same Timebase plot, right-click the plot → Configure,",
        "   set Variable from Sync Waveform to Async Waveform on every plot, OK,",
        "   then Export → CSV.",
        "",
        "Suggested channel set from ConfigDBFile.xml:",
        "  Proximity: " + (", ".join(prox) or "(none listed)"),
        "  Keyphasor: " + (", ".join(kph) or "(none listed)"),
        "",
        f"Bookkeeping counts: static samples={inventory.get('static_samples')}, "
        f"waveform samples={inventory.get('waveform_samples')}.",
        "",
        "When the CSVs are in the exports folder:",
        f"  python main.py --ingest-verify {folder.name}",
        "",
        "Do not rewrite native .dat timestamps in this folder. If dates must be",
        "shifted for sharing, do that in ADRE Sxp on a copy, then re-ingest.",
        "",
    ]
    dest.write_text("\n".join(lines), encoding="utf-8")
    return dest


def classify_export_csv(path: Path) -> dict[str, str]:
    from .buddy import classify_buddy_input
    from .waveforms import is_timebase_export

    kind = classify_buddy_input(path)
    detail = f"{path.stat().st_size} bytes"
    if kind == "tabular":
        try:
            from .adre import load_export

            channels = load_export(path)
            n = sum(ch.n_samples for ch in channels)
            names = ", ".join(ch.name for ch in channels[:8])
            detail = f"{n} samples; {names}"
        except (ValueError, OSError, KeyError) as exc:
            detail = f"unreadable: {exc}"
    elif is_timebase_export(path):
        detail = f"{kind} timebase, {path.stat().st_size / 1e6:.1f} MB"
    return {"name": path.name, "kind": kind, "detail": detail}


def verify_exports(exports_dir: Path, inventory: dict[str, Any] | None = None) -> dict[str, Any]:
    exports_dir.mkdir(parents=True, exist_ok=True)
    files = sorted(p for p in exports_dir.glob("*.csv") if p.is_file())
    classified = [classify_export_csv(path) for path in files]
    kinds = {item["kind"] for item in classified}
    status = "missing"
    if classified:
        has_static = "tabular" in kinds
        has_sync = "sync" in kinds
        has_async = "async" in kinds
        if has_static and has_sync and has_async:
            status = "complete"
        else:
            status = "partial"
    out = {
        "folder": str(exports_dir),
        "status": status,
        "n_files": len(classified),
        "files": classified,
        "has_static": any(item["kind"] == "tabular" for item in classified),
        "has_sync": any(item["kind"] == "sync" for item in classified),
        "has_async": any(item["kind"] == "async" for item in classified),
    }
    if inventory:
        out["expected_static_samples"] = inventory.get("static_samples")
        out["expected_waveform_samples"] = inventory.get("waveform_samples")
    return out


def _static_tabular_csvs(exports_dir: Path) -> list[Path]:
    """Tabular List extracts only — not sync/async timebase dumps."""
    from .buddy import classify_buddy_input

    found: list[Path] = []
    for path in sorted(Path(exports_dir).glob("*.csv")):
        if not path.is_file() or path.stat().st_size < 80:
            continue
        try:
            if classify_buddy_input(path) == "tabular":
                found.append(path)
        except (ValueError, OSError, KeyError):
            continue
    return found


def split_static_exports(exports_dir: Path) -> dict[str, Any]:
    """Split static Tabular List CSVs only, after they have been extracted."""
    from .tabular import split_tabular_export

    exports_dir = Path(exports_dir)
    static_files = _static_tabular_csvs(exports_dir)
    if not static_files:
        return {}
    folder = exports_dir / "channels"
    written: list[str] = []
    info: dict[str, Any] = {"folder": str(folder), "files": written}
    for path in static_files:
        print(f"[adre] split static {path.name}", flush=True)
        try:
            split = split_tabular_export(path, folder)
        except (ValueError, OSError, KeyError) as exc:
            print(f"[adre] skip {path.name}: {exc}", flush=True)
            continue
        written.extend(p.name for p in split.channel_files)
        info["source"] = path.name
        info["n_channels"] = split.n_channels
        info["n_samples"] = split.n_samples
        info["duplicate_config_rows_removed"] = split.duplicate_config_rows
        info["config"] = split.config_file.name
        if split.groups:
            info["groups"] = {
                name: [p.name for p in files] for name, files in split.groups.items()
            }
            info["groups_folder"] = str(split.groups_folder)
    if not written:
        return {}
    return info


def _try_adre_export(working: Path, exports: Path, result: IngestResult) -> None:
    from .adre_automate import RecipeIncomplete, export_database_via_adre

    exports.mkdir(parents=True, exist_ok=True)
    log_path = Path(result.folder) / "export_log.txt"
    try:
        export_database_via_adre(
            working,
            exports,
            then_analyse=False,
        )
    except (RecipeIncomplete, FileNotFoundError, TimeoutError, OSError, RuntimeError) as exc:
        result.notes.append(f"ADRE export did not run to completion: {exc}")
        log_path.write_text(str(exc) + "\n", encoding="utf-8")
        return
    except Exception as exc:  # pywinauto ElementNotEnabled / ElementNotFound
        result.notes.append(f"ADRE export did not run to completion: {type(exc).__name__}: {exc}")
        log_path.write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        return
    log_path.write_text("ADRE export finished\n", encoding="utf-8")


def ingest(
    source: str | Path,
    *,
    vault: str | Path | None = None,
    in_place: bool = False,
    try_export: bool = False,
    skip_scan: bool = False,
) -> IngestResult:
    """Run receive → validate → inventory → (optional ADRE export) → verify."""
    source = resolve_user_path(source)
    if not source.exists():
        raise FileNotFoundError(source)

    notes: list[str] = []
    errors: list[str] = []
    scan_info = ScanResult(status="skipped", detail="not scanned yet")

    try:
        if not skip_scan:
            scan_info = malware_scan(source)
            if scan_info.status == "threat":
                raise IngestError(f"Malware scan reported a threat: {scan_info.detail}")
            if scan_info.status == "skipped":
                notes.append(scan_info.detail)
        else:
            scan_info = ScanResult(status="skipped", detail="scan skipped by operator")
            notes.append(scan_info.detail)

        fingerprint = fingerprint_path(source)
        import_id = _new_import_id(fingerprint)
        folder = import_folder(import_id, vault)
        original = folder / "original"
        working_parent = folder / "working"
        exports = folder / "exports"
        folder.mkdir(parents=True, exist_ok=True)
        exports.mkdir(exist_ok=True)

        if in_place and source.is_dir():
            working = source.resolve()
            notes.append("In-place ingest: original was not copied; working copy is the source folder.")
            (folder / "original_pointer.txt").write_text(str(working) + "\n", encoding="utf-8")
        else:
            if source.is_file():
                original.mkdir(parents=True, exist_ok=True)
                preserved = original / source.name
                _copy_preserve(source, preserved)
                if _looks_like_zip(source):
                    try:
                        _extract_zip(preserved, working_parent)
                    except zipfile.BadZipFile as exc:
                        raise IngestError(f"Not a valid zip archive: {exc}") from exc
                    found = _find_database_root(working_parent)
                    if found is None:
                        raise IngestError("Zip did not contain an ADRE Sxp database (.adb or ConfigDBFile.xml)")
                    working = found
                else:
                    raise IngestError(
                        f"{source.name} is not a zip archive. Pass the unzipped database folder, or a .zip of it."
                    )
            else:
                _copy_preserve(source, original / source.name)
                found = _find_database_root(original / source.name)
                if found is None:
                    raise IngestError("Folder is not an ADRE Sxp database (need .adb or ConfigDBFile.xml)")
                working = found

        if not list(working.glob("*.adb")) and not (working / "ConfigDBFile.xml").is_file():
            raise IngestError("Working copy is missing both .adb and ConfigDBFile.xml")

        inventory = inventory_database(working)
        plan = _write_export_plan(folder, working, inventory)
        adre = _adre_status()
        result = IngestResult(
            import_id=import_id,
            status="partial",
            source=str(source.resolve()),
            folder=str(folder),
            fingerprint=fingerprint,
            received_at=_utc_now(),
            notes=notes,
            errors=errors,
            scan={"status": scan_info.status, "tool": scan_info.tool, "detail": scan_info.detail},
            inventory=inventory,
            exports={"folder": str(exports), "status": "pending", "plan": str(plan), "files": []},
            adre=adre,
        )
        (folder / "working_path.txt").write_text(str(working) + "\n", encoding="utf-8")
        (folder / "inventory.json").write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")

        if try_export and adre.get("export_ready") == "yes":
            _try_adre_export(working, exports, result)
        elif try_export:
            result.notes.append(
                "ADRE CSV export not started: install ADRE Sxp and fill adre_export_recipe.json, "
                "or follow EXPORT_PLAN.txt and drop CSVs into the exports folder."
            )

        result.exports = verify_exports(exports, inventory)
        result.exports["plan"] = str(plan)
        channels_split = {}
        if result.exports.get("has_static"):
            channels_split = split_static_exports(exports)
        else:
            result.notes.append(
                "Static Tabular List CSV not extracted yet — per-channel split skipped."
            )
        if channels_split:
            publish_upload_groups(source, channels_split)
            result.exports["channels"] = channels_split
        if result.exports.get("status") == "complete":
            result.status = "complete"
        else:
            result.status = "partial"
            result.notes.append(
                "Marked partial until static + sync + async CSVs are in the exports folder. "
                f"See {plan.name}."
            )
        _write_manifest(result)
        return result
    except IngestError as exc:
        # Still persist a failed import when we already have a folder.
        failed_id = locals().get("import_id") or _new_import_id("failed000")
        folder = locals().get("folder") or import_folder(failed_id, vault)
        Path(folder).mkdir(parents=True, exist_ok=True)
        result = IngestResult(
            import_id=failed_id,
            status="failed",
            source=str(source.resolve()) if source.exists() else str(source),
            folder=str(folder),
            fingerprint=locals().get("fingerprint") or "",
            received_at=_utc_now(),
            notes=notes,
            errors=[str(exc)],
            scan={"status": scan_info.status, "tool": scan_info.tool, "detail": scan_info.detail},
        )
        _write_manifest(result)
        return result


def ingest_paths(
    paths: list[str | Path],
    **kwargs: Any,
) -> list[IngestResult]:
    return [ingest(path, **kwargs) for path in paths]


def verify_import(import_id: str, vault: str | Path | None = None) -> IngestResult:
    result = load_manifest(import_id, vault)
    exports = Path(result.folder) / "exports"
    result.exports = verify_exports(exports, result.inventory)
    plan = Path(result.folder) / "EXPORT_PLAN.txt"
    if plan.is_file():
        result.exports["plan"] = str(plan)
    if result.exports.get("has_static"):
        channels_split = split_static_exports(exports)
        if channels_split:
            publish_upload_groups(Path(result.source), channels_split)
            result.exports["channels"] = channels_split
    if result.exports.get("status") == "complete" and result.status != "failed":
        result.status = "complete"
        result.notes = [n for n in result.notes if "Marked partial" not in n]
    elif result.status != "failed":
        result.status = "partial"
    _write_manifest(result)
    return result
