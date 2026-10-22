"""Rewrite any UTF-16 source file in the repo as UTF-8.

Some editors on this machine save new Python files as UTF-16LE, which the
interpreter rejects with "source code string cannot contain null bytes".  Run
this after bulk edits if imports start failing for that reason.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = ("*.py", "*.md", "*.txt")
SKIP = {".git", "__pycache__", ".pytest_cache", "figures"}


def main() -> int:
    changed = []
    for pattern in PATTERNS:
        for path in ROOT.rglob(pattern):
            if SKIP & set(path.parts):
                continue
            raw = path.read_bytes()
            if not raw.count(0):
                continue
            for encoding in ("utf-16", "utf-16-le", "utf-16-be"):
                try:
                    text = raw.decode(encoding)
                except (UnicodeDecodeError, UnicodeError):
                    continue
                if "\x00" in text:
                    continue
                path.write_text(text, encoding="utf-8", newline="\n")
                changed.append(path.relative_to(ROOT))
                break
            else:
                print(f"could not decode {path.relative_to(ROOT)}", file=sys.stderr)
                return 1
    for path in changed:
        print(f"rewrote {path} as utf-8")
    print(f"{len(changed)} file(s) changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
