#!/usr/bin/env python3
"""Guard the deliberate Lead Board runtime fork from a stale equality gate.

The primary generator is record-native; the shared helper is the cloud-only
file-mode runtime.  ``tools/check.sh`` must report that manifest-governed fork
honestly, rather than failing because their source bytes differ.
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHECK = (ROOT / "tools" / "check.sh").read_text(encoding="utf-8")
MANIFEST = (ROOT / "manifest.tsv").read_text(encoding="utf-8")
TWIN_DIFF = ('diff -q "$REPO/generators/build-lead-board.py" '
             '"$REPO/shared/build-lead-board-template.py"')
FORK_DECLARATION = "# FORKED BY DESIGN 2026-08-11:"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    require(FORK_DECLARATION in MANIFEST,
            "manifest.tsv must declare the Lead Board runtime fork")
    require(TWIN_DIFF not in CHECK,
            "tools/check.sh still treats the deliberate runtime fork as byte identity")
    require(CHECK.count("intentional runtime fork") == 2,
            "both check.sh paths must report the manifest-governed runtime fork")
    require(CHECK.count(FORK_DECLARATION) == 2,
            "both check.sh paths must tie fork status to the manifest declaration")
    print("lead-board runtime fork selftest: passed")


if __name__ == "__main__":
    main()
