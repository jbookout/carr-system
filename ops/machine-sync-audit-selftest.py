#!/usr/bin/env python3
"""Guards for ops/machine-sync-audit.py.

The audit exists to catch a machine that LOOKS in step and is not, so the two
ways it can betray that job are: reporting a gap as fine, and hard-coding a
floor that has quietly drifted from the files that actually enforce it.

The floor check is the load-bearing one. Three places name Python 3.10 — this
audit, requirements.txt's mypy marker, and bin/migrate-dell.sh's PY_MIN_MINOR.
If one moves and the others do not, the audit reports a machine as fine while
the migration script builds something the repo cannot run, which is the exact
failure of 2026-08-19 wearing a different hat.
"""
import importlib.util
import json
import os
import re
import subprocess
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUDIT = os.path.join(REPO, "ops", "machine-sync-audit.py")
# (label, passed, detail)
RESULTS: list[tuple[str, bool, str]] = []


def check(label, ok, detail=""):
    RESULTS.append((label, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  ({detail})" if detail and not ok else ""))


def load():
    spec = importlib.util.spec_from_file_location("msa", AUDIT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    mod = load()

    # ---- the floor must agree everywhere it is enforced
    check("the audit names a 3.10 floor", mod.PY_MIN == (3, 10), f"got {mod.PY_MIN}")

    reqs = open(os.path.join(REPO, "requirements.txt"), encoding="utf-8").read()
    m = re.search(r'python_version\s*>=\s*"3\.(\d+)"', reqs)
    check("requirements.txt pins mypy to the same floor",
          m is not None and int(m.group(1)) == mod.PY_MIN[1],
          f"requirements says 3.{m.group(1) if m else '?'}")

    mig = open(os.path.join(REPO, "bin", "migrate-dell.sh"), encoding="utf-8").read()
    m2 = re.search(r"PY_MIN_MINOR=(\d+)", mig)
    check("migrate-dell.sh enforces the same floor",
          m2 is not None and int(m2.group(1)) == mod.PY_MIN[1],
          f"migrate says 3.{m2.group(1) if m2 else '?'}")

    # ---- a GAP must never be reported as fine
    check("the three states are distinct", len({mod.OK, mod.DESIGN, mod.GAP}) == 3)

    mod.ROWS.clear()
    mod.row("t", "a", mod.GAP, "d")
    mod.row("t", "b", mod.OK, "d")
    mod.row("t", "c", mod.DESIGN, "d")
    gaps = [r for r in mod.ROWS if r[2] == mod.GAP]
    check("only GAP rows count as gaps", len(gaps) == 1, f"counted {len(gaps)}")
    check("a by-design row is not a gap",
          not any(r[2] == mod.GAP for r in mod.ROWS if r[1] == "c"))

    # ---- it must actually run on this machine, read-only, and exit 0 plain
    py = os.path.join(REPO, ".venv", "bin", "python")
    py = py if os.path.exists(py) else sys.executable
    p = subprocess.run([py, AUDIT, "--json"], capture_output=True, text=True,
                       cwd=REPO, timeout=300)
    check("the audit exits 0 without --strict", p.returncode == 0, f"rc={p.returncode}")
    try:
        data = json.loads(p.stdout)
    except ValueError as exc:
        data = None
        check("--json emits valid JSON", False, repr(exc))
    if data:
        check("--json emits valid JSON", True)
        sections = {r["section"] for r in data["rows"]}
        for want in ("identity", "repo", "config", "toolchain", "optional"):
            check(f"the {want} section is reported", want in sections, f"{sorted(sections)}")
        check("every row carries a state",
              all(r["state"] in (mod.OK, mod.DESIGN, mod.GAP) for r in data["rows"]))
        check("the gap count matches the rows",
              data["gaps"] == sum(1 for r in data["rows"] if r["state"] == mod.GAP))

    # ---- read-only: running it must not dirty the tree
    dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                           capture_output=True, text=True, cwd=REPO).stdout
    tracked = [ln for ln in dirty.splitlines() if "machine-sync-audit" not in ln]
    check("the audit changes nothing on the machine", not tracked,
          f"unexpected: {tracked[:3]}")

    failed = sum(1 for _, ok, _ in RESULTS if not ok)
    print(f"\nmachine-sync-audit-selftest: {len(RESULTS) - failed}/{len(RESULTS)} passed")
    if failed:
        print("FAILED:")
        for label, ok, detail in RESULTS:
            if not ok:
                print(f"  - {label}  {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
