"""Entry point for the RotorDyn 1X vector polar plot tool.

    python main.py                     launch the desktop application
    python main.py --web               serve the browser application
    python main.py --buddy FILE [FILE ...]  Rotor Buddy report (sync, async and/or static)
    python main.py --mds FILE [FILE ...]    MDS Engineer report (diagnosis + recommendations)
    python main.py --data path/to/csvs use a different data directory
    python main.py --summary           print what was found in the data
    python main.py --export figures    render the static figure set
    python main.py --watch             watch inbox/ for ADRE CSV exports
    python main.py --process inbox/run process one export folder once
    python main.py --ingest PATH       zip/folder → inventory (add --adre to export CSV)
    python main.py 3_GE_7HA.zip --adre  unzip, drive ADRE Sxp, split, group
    python main.py --split-static CSV  split a Tabular List export into one file per channel
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from rotordyn import build_run
from rotordyn.vectors import KEYPHASOR_DEG, PROBE_1X_DEG, PROBE_1Y_DEG


def _print(text: str) -> None:
    # Flushed because the server messages are printed just before a call that
    # blocks forever; buffered, they would not appear until the server stopped.
    try:
        print(text, flush=True)
    except UnicodeEncodeError:  # older Windows consoles
        print(text.encode("ascii", "replace").decode("ascii"), flush=True)


def _browsable_urls(host: str, port: int) -> list[tuple[str, str]]:
    """URLs a browser can actually open, given what the server bound to.

    ``0.0.0.0`` means "listen on every interface".  It is a valid bind address
    but not a valid destination, so printing it as a link sends people to
    ERR_ADDRESS_INVALID.
    """
    if host not in ("0.0.0.0", "::", ""):
        return [("this machine", f"http://{host}:{port}")]

    urls = [("this machine", f"http://localhost:{port}")]
    try:
        import socket

        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        with probe:
            # No packets are sent; this just asks the OS which interface it
            # would use to reach the outside, which is the address other
            # machines should target.
            probe.connect(("8.8.8.8", 80))
            urls.append(("other machines", f"http://{probe.getsockname()[0]}:{port}"))
    except OSError:
        pass
    return urls


def summarise(run) -> None:
    evidence = run.rotation_evidence()
    frame = run.frame
    _print("Source files:")
    for role, name in run.source_files.items():
        _print(f"  {role:<10} {name}")
    _print("")
    _print(
        f"1X probe {run.probe_1x_name} at {PROBE_1X_DEG:+.0f}\u00b0, "
        f"1Y probe {run.probe_1y_name} at {PROBE_1Y_DEG:+.0f}\u00b0, "
        f"keyphasor {run.keyphasor_name} at {KEYPHASOR_DEG:+.0f}\u00b0 (clockwise from TDC)"
    )
    _print(
        f"{len(frame)} samples, {frame['timestamp'].iloc[0]} to {frame['timestamp'].iloc[-1]}, "
        f"{frame['rpm'].min():.0f}-{frame['rpm'].max():.0f} {run.speed_unit}, "
        f"amplitude in {run.amp_unit}"
    )
    _print("")
    _print("Segments:")
    for name, (start, stop) in run.segment_bounds().items():
        rpm = frame["rpm"].iloc[start:stop]
        _print(f"  {name:<24} samples {start:>4}-{stop - 1:<4} {rpm.min():.0f}-{rpm.max():.0f} rpm")
    _print("")
    _print("Instrument status codes:")
    for code, count in run.flag_counts.items():
        _print(f"  {code:<6} {count}")
    _print("")
    _print("Rotation direction evidence:")
    _print(f"  median (1Y phase - 1X phase) = {evidence['median_1y_minus_1x_deg']:.0f}\u00b0")
    _print(
        f"  mean forward (CCW) orbit component {evidence['mean_ccw_magnitude']:.3f} vs "
        f"reverse (CW) {evidence['mean_cw_magnitude']:.3f}"
    )
    _print(
        f"  CCW dominates in {100 * evidence['fraction_ccw_dominant']:.0f}% of "
        f"{evidence['n_samples']:.0f} strong-signal samples -> shaft turns CCW in this view"
    )


def main(argv: list[str] | None = None) -> int:
    # cp1252 Windows consoles cannot print the arrows in ADRE automation logs.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "inputs",
        nargs="*",
        metavar="PATH",
        help="ADRE zip/folder or Tabular List CSV; with --adre, export then split and group",
    )
    parser.add_argument(
        "--data", default=".", help="directory holding the ADRE channel exports (default: .)"
    )
    parser.add_argument("--summary", action="store_true", help="print a data summary and exit")
    parser.add_argument(
        "--export",
        nargs="?",
        const="figures",
        metavar="DIR",
        help="render the static figure set to DIR and exit",
    )
    # Hosting platforms (Hugging Face Spaces, Render, Fly, Heroku) tell the
    # application which port to listen on through $PORT.
    default_port = int(os.environ.get("PORT", 8050))
    parser.add_argument(
        "--web",
        nargs="?",
        const=default_port,
        type=int,
        metavar="PORT",
        help=f"serve the browser application on PORT (default: {default_port})",
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="interface --web binds to (default: 127.0.0.1)"
    )
    parser.add_argument(
        "--production",
        action="store_true",
        help="serve --web with waitress instead of the development server",
    )
    parser.add_argument(
        "--watch",
        nargs="?",
        const="inbox",
        metavar="DIR",
        help="watch DIR for ADRE CSV exports and analyse them into --outbox",
    )
    parser.add_argument(
        "--process",
        metavar="DIR",
        help="process one ADRE CSV export folder once and exit",
    )
    parser.add_argument(
        "--outbox",
        default="outbox",
        help="where --watch / --process write summaries and figures (default: outbox)",
    )
    parser.add_argument(
        "--buddy",
        nargs="+",
        metavar="PATH",
        help="Rotor Buddy report. One file, or sync + async + static together",
    )
    parser.add_argument(
        "--mds",
        nargs="+",
        metavar="PATH",
        help="MDS Engineer report (diagnosis + recommendations). Implies Rotor Buddy",
    )
    parser.add_argument(
        "--ingest",
        nargs="+",
        metavar="PATH",
        help="ingest ADRE Sxp zip(s) or database folder(s): preserve, scan, inventory",
    )
    parser.add_argument(
        "--ingest-status",
        nargs="?",
        const="*",
        metavar="ID",
        help="list ingest imports, or print one import report",
    )
    parser.add_argument(
        "--ingest-verify",
        metavar="ID",
        help="re-check CSV exports for an existing import ID",
    )
    parser.add_argument(
        "--split-static",
        nargs="+",
        metavar="CSV",
        help="rewrite an ADRE Sxp Tabular List export as one file per channel",
    )
    parser.add_argument(
        "--split-out",
        metavar="DIR",
        help="with --split-static, where to write the per-channel files",
    )
    parser.add_argument(
        "--vault",
        default="imports",
        help="where --ingest stores originals and reports (default: imports)",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="with --ingest, fingerprint a folder without copying it into the vault",
    )
    parser.add_argument(
        "--try-export",
        action="store_true",
        help="with --ingest, drive ADRE Sxp CSV export when the UI recipe is complete",
    )
    parser.add_argument(
        "--adre",
        action="store_true",
        help="drive ADRE Sxp to export CSV, then split and group (same as --ingest PATH --try-export)",
    )
    parser.add_argument(
        "--skip-scan",
        action="store_true",
        help="with --ingest, skip Windows Defender (still recorded as skipped, not clean)",
    )
    args = parser.parse_args(argv)

    from rotordyn.ingest import resolve_user_path

    if args.adre:
        args.try_export = True
    if args.inputs:
        csvs = [p for p in args.inputs if Path(p).suffix.lower() in {".csv", ".txt"}]
        rest = [p for p in args.inputs if p not in csvs]
        if csvs and not args.split_static:
            args.split_static = csvs
        if rest:
            args.ingest = list(args.ingest or []) + rest
    if args.ingest:
        args.ingest = [str(resolve_user_path(p)) for p in args.ingest]
    if args.split_static:
        args.split_static = [str(resolve_user_path(p)) for p in args.split_static]
    if args.adre and not args.ingest and not args.split_static:
        parser.error("--adre needs a zip, database folder, or CSV")

    if args.split_static:
        from rotordyn.tabular import split_tabular_export

        exit_code = 0
        for raw in args.split_static:
            path = Path(raw)
            folder = Path(args.split_out) if args.split_out else path.parent / "channels"
            try:
                split = split_tabular_export(path, folder)
            except (ValueError, OSError) as exc:
                _print(f"{path.name}: {exc}")
                exit_code = 2
                continue
            _print(split.format_text())
        return exit_code

    if args.ingest_status:
        from rotordyn.ingest import list_imports, load_manifest

        if args.ingest_status == "*":
            rows = list_imports(args.vault)
            if not rows:
                _print(f"No imports in {Path(args.vault).resolve()}")
                return 0
            for row in rows:
                _print(f"{row['import_id']}  {row['status']:<8}  {row['source']}")
            return 0
        try:
            result = load_manifest(args.ingest_status, args.vault)
        except FileNotFoundError as exc:
            _print(str(exc))
            return 2
        _print(result.format_text())
        return 0

    if args.ingest_verify:
        from rotordyn.ingest import verify_import

        try:
            result = verify_import(args.ingest_verify, args.vault)
        except FileNotFoundError as exc:
            _print(str(exc))
            return 2
        _print(result.format_text())
        _print(f"wrote {Path(result.folder) / 'report.txt'}")
        return 0 if result.status != "failed" else 2

    if args.ingest:
        from rotordyn.ingest import ingest_paths

        results = ingest_paths(
            args.ingest,
            vault=args.vault,
            in_place=args.in_place,
            try_export=args.try_export,
            skip_scan=args.skip_scan,
        )
        exit_code = 0
        for result in results:
            _print(result.format_text())
            _print(f"wrote {Path(result.folder) / 'report.txt'}")
            if result.status == "failed":
                exit_code = 2
        return exit_code

    if args.buddy or args.mds:
        from rotordyn.buddy import analyse_paths

        paths = args.mds or args.buddy
        try:
            report = analyse_paths(paths, progress=True)
        except (FileNotFoundError, ValueError, KeyError, OSError) as exc:
            _print(f"Could not analyse: {exc}")
            return 2
        text = report.format_text()
        if not args.mds:
            _print(text)
        out = Path(args.outbox)
        out.mkdir(parents=True, exist_ok=True)
        dest = out / "rotor_buddy.txt"
        dest.write_text(text, encoding="utf-8")
        _print(f"wrote {dest}")
        pdf_path = out / "rotor_buddy.pdf"
        try:
            from rotordyn.buddy_pdf import write_pdf

            write_pdf(report, pdf_path)
        except Exception as exc:  # txt already saved; do not hide the analysis
            _print(f"Buddy PDF not written: {exc}")
        else:
            _print(f"wrote {pdf_path}")
        if args.mds:
            from rotordyn.mds import diagnose

            mds = diagnose(report)
            mds_text = mds.format_text()
            _print(mds_text)
            mds_dest = out / "mds_report.txt"
            mds_dest.write_text(mds_text, encoding="utf-8")
            _print(f"wrote {mds_dest}")
            mds_pdf = out / "mds_report.pdf"
            try:
                from rotordyn.mds_pdf import write_pdf as write_mds_pdf

                write_mds_pdf(mds, mds_pdf)
            except Exception as exc:
                _print(f"MDS PDF not written: {exc}")
            else:
                _print(f"wrote {mds_pdf}")
        return 0

    if args.process:
        from rotordyn.pipeline import process_export_directory

        try:
            result = process_export_directory(args.process, args.outbox)
        except (FileNotFoundError, ValueError, KeyError) as exc:
            _print(f"Could not process export: {exc}")
            return 2
        _print(f"wrote {result.summary_path}")
        for path in result.figure_paths:
            _print(f"wrote {path}")
        _print(f"outbox {result.outbox}")
        return 0

    if args.watch:
        from rotordyn.pipeline import watch_inbox

        _print(f"Watching {Path(args.watch).resolve()}  →  {Path(args.outbox).resolve()}")
        _print("Export CSVs from ADRE Sxp into that folder (see inbox/HOW_TO_EXPORT.txt).")
        _print("Ctrl+C to stop.")
        try:
            watch_inbox(args.watch, args.outbox)
        except KeyboardInterrupt:
            _print("Stopped.")
        return 0

    # The web app takes its data from uploads, so it must start without one.
    if args.web:
        try:
            from rotordyn.webapp import run_web
        except ImportError as exc:
            _print(f"The web interface needs dash and plotly installed: {exc}")
            return 3
        server = "waitress" if args.production else "development server"
        _print(f"Serving on port {args.web} via {server}  (Ctrl+C to stop)")
        for label, url in _browsable_urls(args.host, args.web):
            _print(f"  {label:<16} {url}")
        try:
            run_web(
                data_dir=args.data,
                host=args.host,
                port=args.web,
                production=args.production,
            )
        except ImportError as exc:
            _print(f"--production needs waitress installed: {exc}")
            return 3
        return 0

    try:
        run = build_run(args.data)
    except (FileNotFoundError, ValueError) as exc:
        _print(f"Could not load the dataset: {exc}")
        return 2

    if args.summary:
        summarise(run)
        return 0

    if args.export:
        import matplotlib

        matplotlib.use("Agg")
        from rotordyn.export import export_all

        for path in export_all(run, args.export):
            _print(f"wrote {path}")
        return 0

    try:
        from rotordyn.gui import launch
    except ImportError as exc:
        _print(f"Tkinter is required for the interactive tool: {exc}")
        _print("Use --export to render figures without a display.")
        return 3
    launch(run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
