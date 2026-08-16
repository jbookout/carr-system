#!/usr/bin/env python3
"""Hermetic fixtures for the 0e22e34a and 99e951b9 commit-time controls."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PATHS = load("path_hygiene", "ops/githooks/path-hygiene-check.py")
INDEX = load("index_upkeep", "ops/githooks/index-upkeep-check.py")


def check(name: str, value: bool, failures: list[str]):
    print(f"  {'ok' if value else 'FAIL'} {name}")
    if not value:
        failures.append(name)


def git(root: Path, *args: str):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def main() -> int:
    failures: list[str] = []
    check("four folder levels pass", not PATHS.violations(["a/b/c/d/file.py"]), failures)
    check("fifth folder level refuses", bool(PATHS.violations(["a/b/c/d/e/file.py"])), failures)
    check("final suffix refuses", bool(PATHS.violations(["ops/report_final.md"])), failures)
    check("version suffix refuses", bool(PATHS.violations(["ops/report-v2.json"])), failures)
    check("dot-versioned machine contract passes",
          not PATHS.violations(["ops/config/policy.v1.json"]), failures)
    check("ordinary descriptive filename passes", not PATHS.violations(["ops/report-2026.json"]), failures)

    with tempfile.TemporaryDirectory(prefix="path-index-hygiene-") as tmp:
        root = Path(tmp)
        git(root, "init", "-q")
        git(root, "config", "user.email", "selftest@example.invalid")
        git(root, "config", "user.name", "Selftest")
        (root / "category").mkdir()
        (root / "category" / "INDEX.md").write_text("# Category\n")
        (root / "category" / "old.md").write_text("old\n")
        git(root, "add", ".")
        git(root, "commit", "-qm", "seed")

        (root / "category" / "new.md").write_text("new\n")
        git(root, "add", "category/new.md")
        old_cwd = os.getcwd()
        try:
            os.chdir(root)
            check("new categorized file requires index",
                  bool(INDEX.violations(INDEX.staged_added(), INDEX.staged_all())), failures)
        finally:
            os.chdir(old_cwd)

        (root / "category" / "INDEX.md").write_text("# Category\n- new\n")
        git(root, "add", "category/INDEX.md")
        try:
            os.chdir(root)
            check("same staged index satisfies category update",
                  not INDEX.violations(INDEX.staged_added(), INDEX.staged_all()), failures)
        finally:
            os.chdir(old_cwd)

    print(f"path-index-hygiene-selftest: {8 - len(failures)}/8 passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
