"""CI guard (V5-F08 review H2): only backup-nightly.yml may write Checks.

The restore verifier (tools/restore-watermark.py) binds a nightly backup copy to
its run through the "Backup artifact" Check, whose external_id is free text. A
workflow on main allowed `checks: write` could write an identical envelope, so
that permission is confined to the one workflow that produces the Check:

  * `checks: write` anywhere but .github/workflows/backup-nightly.yml fails;
  * `write-all` anywhere fails (it includes checks: write);
  * every job must run under a declared `permissions:` block (top level or its
    own), because an undeclared block inherits the repository default, which
    could be changed to read-write without touching this tree.

Read as text: the venv carries no YAML parser, and the shapes checked here are
the plain block-mapping forms these workflows use (a flow mapping such as
`permissions: {checks: write}` is caught by the same pattern).

  .venv/bin/python -m unittest tools/test_workflow_checks_write_grant.py
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO / ".github" / "workflows"
ALLOWED = "backup-nightly.yml"
CHECKS_WRITE = re.compile(r"(?<![\w-])checks\s*:\s*['\"]?write\b")
WRITE_ALL = re.compile(r"permissions\s*:\s*['\"]?write-all\b")


def _code(text: str) -> list[str]:
    """Lines with YAML comments dropped (a '#' preceded by whitespace or at column 0)."""
    return [re.sub(r"(^|\s)#.*$", "", line) for line in text.splitlines()]


def violations(name: str, text: str) -> list[str]:
    lines = _code(text)
    found = []
    for n, line in enumerate(lines, 1):
        if CHECKS_WRITE.search(line) and name != ALLOWED:
            found.append(f"{name}:{n}: checks: write outside {ALLOWED}")
        if WRITE_ALL.search(line):
            found.append(f"{name}:{n}: permissions: write-all")
    if not any(re.match(r"permissions\s*:", line) for line in lines):
        # no top-level block: every job needs its own
        in_jobs = False
        job: str | None = None
        declared: dict[str, bool] = {}
        for line in lines:
            if re.match(r"\S", line):
                in_jobs = line.startswith("jobs:")
                continue
            if not in_jobs:
                continue
            m = re.match(r"  ([A-Za-z0-9_-]+)\s*:\s*$", line)
            if m:
                job = m.group(1)
                declared[job] = False
            elif job and re.match(r"    permissions\s*:", line):
                declared[job] = True
        if not declared:
            found.append(f"{name}: no top-level permissions block and no jobs found")
        found += [f"{name}: job {j} runs with the repository default permissions" for j, ok in declared.items() if not ok]
    return found


class ChecksWriteGrant(unittest.TestCase):
    def test_only_the_backup_workflow_may_write_checks_and_every_job_declares_permissions(self):
        files = sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")])
        self.assertTrue(files, "no workflows found")
        found = [v for f in files for v in violations(f.name, f.read_text())]
        self.assertEqual(found, [])
        self.assertTrue(CHECKS_WRITE.search((WORKFLOWS / ALLOWED).read_text()),
                        "backup-nightly.yml no longer writes its Check; the restore verifier cannot bind a run")

    def test_the_guard_catches_each_shape(self):
        top = "on: push\npermissions:\n  contents: read\njobs:\n  a:\n    runs-on: x\n"
        self.assertEqual(violations("other.yml", top), [])
        self.assertTrue(violations("other.yml", top.replace("contents: read", "checks: write")))
        self.assertTrue(violations("other.yml", top.replace("contents: read", "checks: 'write'")))
        self.assertTrue(violations("other.yml", "on: push\npermissions: {checks: write}\njobs:\n  a:\n    x: 1\n"))
        self.assertTrue(violations("other.yml", "on: push\npermissions: write-all\njobs:\n  a:\n    x: 1\n"))
        self.assertEqual(violations("other.yml", top + "# checks: write (a comment)\n"), [])
        self.assertEqual(violations(ALLOWED, top.replace("contents: read", "checks: write")), [])
        self.assertTrue(violations(ALLOWED, "on: push\npermissions: write-all\njobs:\n  a:\n    x: 1\n"))
        per_job = ("on: push\njobs:\n  a:\n    permissions:\n      contents: read\n    runs-on: x\n"
                   "  b:\n    runs-on: x\n")
        self.assertEqual(violations("other.yml", per_job), ["other.yml: job b runs with the repository default permissions"])
        self.assertEqual(violations("other.yml", per_job.replace("  b:\n    runs-on: x\n",
                                                                 "  b:\n    permissions:\n      contents: read\n")), [])
        job_level = ("on: push\njobs:\n  a:\n    permissions:\n      checks: write\n")
        self.assertTrue(violations("other.yml", job_level))


if __name__ == "__main__":
    unittest.main()
