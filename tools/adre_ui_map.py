"""Dump the ADRE Sxp window/control tree so the export recipe can be filled in.

Run this after ADRE Sxp is installed and visible (not minimized):

    python tools/adre_ui_map.py
    python tools/adre_ui_map.py --title "ADRE"

It writes ``adre_ui_map.txt`` in the project root.
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _dump_uia(win, lines: list[str]) -> None:
    lines.append("backend=uia  descendants (title / auto_id / class):")
    try:
        kids = win.descendants()
    except Exception as exc:
        lines.append(f"  descendants failed: {exc}")
        return
    lines.append(f"  count={len(kids)}")
    for child in kids[:800]:
        try:
            auto = getattr(child.element_info, "automation_id", "") or ""
            cls = getattr(child.element_info, "class_name", "") or ""
            ctrl = child.friendly_class_name()
            title = child.window_text() or ""
            if not title and not auto:
                continue
            lines.append(f"  {ctrl:<22} title={title!r} auto_id={auto!r} class={cls!r}")
        except Exception:
            continue


def _dump_identifiers(win, lines: list[str]) -> None:
    buf = io.StringIO()
    try:
        old = sys.stdout
        sys.stdout = buf
        try:
            win.print_control_identifiers(depth=8)
        finally:
            sys.stdout = old
    except Exception as exc:
        lines.append(f"print_control_identifiers failed: {exc}")
        return
    text = buf.getvalue().strip()
    if text:
        lines.append("print_control_identifiers:")
        lines.extend("  " + row if row else "" for row in text.splitlines())


def _dump_menu(win, lines: list[str]) -> None:
    try:
        menu = win.menu()
    except Exception as exc:
        lines.append(f"win32 menu() failed: {exc}")
        return
    lines.append("win32 menu items:")

    def walk(items, prefix: str) -> None:
        for item in items:
            try:
                text = item.text()
            except Exception:
                text = "?"
            path = f"{prefix}{text}"
            lines.append(f"  {path}")
            try:
                sub = item.sub_menu()
            except Exception:
                sub = None
            if sub:
                try:
                    walk(sub.items(), path + " -> ")
                except Exception:
                    pass

    try:
        walk(menu.items(), "")
    except Exception as exc:
        lines.append(f"  walk failed: {exc}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--title", default=".*ADRE.*", help="regex for the main window title")
    parser.add_argument(
        "--out",
        default=str(ROOT / "adre_ui_map.txt"),
        help="where to write the control dump",
    )
    args = parser.parse_args(argv)

    try:
        from pywinauto import Application, Desktop
    except ImportError:
        print("Install pywinauto first:  python -m pip install pywinauto", file=sys.stderr)
        return 3

    lines: list[str] = ["ADRE UI map", "===========", ""]

    desktop = Desktop(backend="uia")
    windows = desktop.windows()
    lines.append("Top-level windows (uia):")
    for win in windows:
        text = win.window_text()
        if text:
            lines.append(f"  - {text!r}")

    import re

    pattern = re.compile(args.title, re.I)
    uia_matches = [
        w
        for w in windows
        if w.window_text()
        and "CURSOR" not in w.window_text().upper()
        and pattern.search(w.window_text())
    ]
    if not uia_matches:
        uia_matches = [
            w
            for w in windows
            if w.window_text() and "ADRE" in w.window_text().upper() and "CURSOR" not in w.window_text().upper()
        ]

    if not uia_matches:
        lines.append("")
        lines.append("No ADRE window found. Open ADRE Sxp (not minimized), then re-run.")
        Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n".join(lines))
        return 1

    for win in uia_matches:
        lines.append("")
        lines.append(f"=== UIA window: {win.window_text()!r} ===")
        try:
            win.restore()
            win.set_focus()
        except Exception:
            pass
        lines.append("children:")
        try:
            for child in win.children():
                auto = getattr(child.element_info, "automation_id", "") or ""
                cls = getattr(child.element_info, "class_name", "") or ""
                ctrl = child.friendly_class_name()
                title = child.window_text() or ""
                lines.append(f"  {ctrl:<22} title={title!r} auto_id={auto!r} class={cls!r}")
        except Exception as exc:
            lines.append(f"  children failed: {exc}")
        _dump_uia(win, lines)
        _dump_identifiers(win, lines)

    lines.append("")
    lines.append("=== win32 backend (menus) ===")
    try:
        app = Application(backend="win32").connect(title_re=args.title, timeout=5)
        win32 = app.window(title_re=args.title)
        lines.append(f"connected: {win32.window_text()!r}")
        _dump_menu(win32, lines)
        _dump_identifiers(win32, lines)
    except Exception as exc:
        lines.append(f"win32 connect failed: {exc}")

    out = Path(args.out)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out}  ({len(lines)} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
