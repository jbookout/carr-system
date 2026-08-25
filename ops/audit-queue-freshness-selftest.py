#!/usr/bin/env python3
"""Fixture for ops/audit-queue-freshness-check.py, pinning BOTH directions.

A guard tested only on the side that permits is how a fail-closed check quietly
stops closing: the permitting path keeps passing, nobody notices the refusing
path went inert, and the check reads as coverage while enforcing nothing. So
every case here is paired — one tree the check MUST accept, one it MUST refuse,
for each behaviour the check claims.

The trees are built in a tempdir rather than run against the real repo, because a
check whose fixture depends on the live tree changes its verdict whenever anyone
edits a gate, and then the fixture is measuring the repo instead of the check.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
CHECK = os.path.join(HERE, "audit-queue-freshness-check.py")

spec = importlib.util.spec_from_file_location("aqf_check", CHECK)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

HEADER = "id\tplain_name\tbucket\tenforcement_or_sketch\tevidence\n"

failures: list[str] = []


def build(root: str, rows: list[tuple[str, str, str, str]], artifacts: dict[str, str],
          active_ids: list[str] | None = None) -> None:
    """Write a fake repo: an audit table plus whatever enforcement files."""
    os.makedirs(os.path.join(root, "audits"), exist_ok=True)
    path = os.path.join(root, "audits", "rule-enforceability-audit-2026-08-14.tsv")
    with open(path, "w") as fh:
        fh.write(HEADER)
        for rid, name, bucket, evidence in rows:
            fh.write(f"{rid}\t{name}\t{bucket}\tsketch\t{evidence}\n")
    for rel, text in artifacts.items():
        full = os.path.join(root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as fh:
            fh.write(text)
    os.makedirs(os.path.join(root, "ops", "config"), exist_ok=True)
    ids = active_ids if active_ids is not None else [row[0] for row in rows]
    with open(os.path.join(root, "ops", "config", "rule-enforcement-map.json"), "w") as fh:
        json.dump({"active_rule_ids": {"shared": ids}}, fh)


def case(label: str, rows, artifacts, expect_ids: set[str]) -> None:
    """Run the check over a synthetic tree; compare flagged ids to expect_ids."""
    with tempfile.TemporaryDirectory() as root:
        build(root, rows, artifacts)
        findings, audit = mod.stale_rows(root)
        got = {f[0] for f in findings}
        if audit is None and expect_ids:
            failures.append(f"{label}: no audit table found at all")
            return
        if got != expect_ids:
            failures.append(f"{label}: expected {sorted(expect_ids)}, flagged {sorted(got)}")


CLAIM = "No existing check confirmed this session."
NO_CLAIM = "rule_controls: session_task_rail/session_boot."

# ---------------------------------------------------------------- direction 1
# REFUSES: a U row claiming nothing exists, whose rule id sits in a live hook.
case(
    "refuses U row contradicted by a hook",
    [("aaaaaaa1", "some rule", "U", CLAIM)],
    {"hooks/some-gate.py": "# enforces aaaaaaa1\n"},
    {"aaaaaaa1"},
)

# PERMITS: the same row when no artifact names it. This is an honestly-open row
# and must stay in the queue undisturbed.
case(
    "permits U row nothing names",
    [("aaaaaaa1", "some rule", "U", CLAIM)],
    {"hooks/some-gate.py": "# enforces something else\n"},
    set(),
)

# ---------------------------------------------------------------- direction 2
# REFUSES: a P row is a claim too — partial coverage that says "no existing
# check" for its uncovered half is contradicted the same way.
case(
    "refuses P row contradicted by a selftest",
    [("aaaaaaa2", "partial rule", "P", "Verified NOT present: grepped, zero hits.")],
    {"ops/thing-selftest.py": "# pins aaaaaaa2\n"},
    {"aaaaaaa2"},
)

# PERMITS: an E row makes no open-work claim, so it is out of scope even when
# a hook names it. Flagging it would put every correctly-closed row in the
# failure list, which is how a check gets turned off.
case(
    "permits E row named by a hook",
    [("aaaaaaa2", "enforced rule", "E", CLAIM)],
    {"hooks/some-gate.py": "# enforces aaaaaaa2\n"},
    set(),
)

# ---------------------------------------------------------------- direction 3
# PERMITS: a U row with no negative claim in its evidence. The check reads the
# row's own words and refuses to invent a claim the auditor did not make.
case(
    "permits U row that never claimed nothing exists",
    [("aaaaaaa3", "unclaimed rule", "U", NO_CLAIM)],
    {"hooks/some-gate.py": "# mentions aaaaaaa3 in passing\n"},
    set(),
)

# REFUSES: same row, same artifact, once the evidence does carry the claim.
# This isolates the evidence text as the only difference between the two.
case(
    "refuses once the same row carries the claim",
    [("aaaaaaa3", "unclaimed rule", "U", CLAIM)],
    {"hooks/some-gate.py": "# mentions aaaaaaa3 in passing\n"},
    {"aaaaaaa3"},
)

# ---------------------------------------------------------------- direction 4
# PERMITS: a file that is NOT an enforcement artifact. A rule id quoted in a
# doc, a migration or an ordinary tool is not evidence of a gate, and treating
# it as such is how this check would start over-reporting and get deleted.
case(
    "permits a mention in a non-artifact file",
    [("aaaaaaa4", "doc-quoted rule", "U", CLAIM)],
    {"tools/some-helper.py": "# aaaaaaa4 discussed here\n"},
    set(),
)

# REFUSES: the same id in a githook, which does deny.
case(
    "refuses a mention in a githook",
    [("aaaaaaa4", "doc-quoted rule", "U", CLAIM)],
    {"ops/githooks/some-check.py": "# aaaaaaa4 enforced pre-commit\n"},
    {"aaaaaaa4"},
)

# ---------------------------------------------------------------- direction 5
# REFUSES: no audit table means no current assessment at all. A no-op here once
# let CI report fresh while the only evidence file was absent.
with tempfile.TemporaryDirectory() as _root:
    _findings, _audit = mod.stale_rows(_root)
    if _audit is not None or _findings:
        failures.append("stale_rows should report no findings when no table exists")
    os.makedirs(os.path.join(_root, "ops", "config"), exist_ok=True)
    with open(os.path.join(_root, "ops", "config", "rule-enforcement-map.json"), "w") as fh:
        json.dump({"active_rule_ids": {"shared": ["aaaaaaa5"]}}, fh)
    _saved = sys.argv
    sys.argv = ["check", _root]
    try:
        if mod.main() != 1:
            failures.append("missing audit table must fail closed")
    finally:
        sys.argv = _saved

# REFUSES: a malformed id column is skipped, not crashed on.
case(
    "skips rows without a well-formed rule id",
    [("not-an-id", "junk row", "U", CLAIM)],
    {"hooks/some-gate.py": "# not-an-id\n"},
    set(),
)

# ---------------------------------------------------------------- direction 6
# PERMITS: a row that has explicitly marked the naming artifact as an incidental
# mention. This is the escape hatch that keeps honest U rows in the queue.
case(
    "permits a row that marked the artifact mention-only",
    [(
        "aaaaaaa6", "cited-in-prose rule", "U",
        CLAIM + " [mention-only: hooks/some-gate.py]",
    )],
    {"hooks/some-gate.py": "# built under aaaaaaa6, does not enforce it\n"},
    set(),
)

# REFUSES: the hatch is per-path, not a blanket amnesty. A second artifact the
# row did not name still counts — otherwise one annotation would silence every
# future gate for that rule, which is the failure mode the hatch itself risks.
case(
    "refuses an artifact the mention-only list does not cover",
    [(
        "aaaaaaa6", "cited-in-prose rule", "U",
        CLAIM + " [mention-only: hooks/some-gate.py]",
    )],
    {
        "hooks/some-gate.py": "# built under aaaaaaa6, does not enforce it\n",
        "ops/real-selftest.py": "# pins aaaaaaa6 for real\n",
    },
    {"aaaaaaa6"},
)

# ---------------------------------------------------------------- exit codes
# The check's exit code is what CI reads; a correct findings list behind a
# always-zero exit would be invisible.
with tempfile.TemporaryDirectory() as _root:
    build(_root, [("aaaaaaa5", "x", "U", CLAIM)], {"hooks/g.py": "aaaaaaa5"})
    _saved = sys.argv
    sys.argv = ["check", _root]
    try:
        if mod.main() != 1:
            failures.append("exit code should be 1 when a stale row is found")
    finally:
        sys.argv = _saved

# ---------------------------------------------------------------- direction 7
# The current audit is a join against the reviewed map, not a historical
# sample. Pin both directions: an added rule needs a row, and an old/retired
# row must be removed. Also pin the structural fail-closed paths, because a
# permissive CSV reader would otherwise turn malformed evidence into a green.
with tempfile.TemporaryDirectory() as _root:
    build(_root, [("aaaaaaa7", "current", "J", "map says ambient")], {},
          active_ids=["aaaaaaa7", "bbbbbbb7"])
    _saved = sys.argv
    sys.argv = ["check", _root]
    try:
        if mod.main() != 1:
            failures.append("audit missing a current map id must fail")
    finally:
        sys.argv = _saved

with tempfile.TemporaryDirectory() as _root:
    build(_root, [("aaaaaaa7", "current", "J", "map says ambient"),
                  ("bbbbbbb7", "retired", "J", "old row")], {},
          active_ids=["aaaaaaa7"])
    _saved = sys.argv
    sys.argv = ["check", _root]
    try:
        if mod.main() != 1:
            failures.append("inactive audit extra must fail")
    finally:
        sys.argv = _saved

with tempfile.TemporaryDirectory() as _root:
    build(_root, [("aaaaaaa7", "current", "J", "map says ambient"),
                  ("aaaaaaa7", "duplicate", "J", "same id")], {},
          active_ids=["aaaaaaa7"])
    _saved = sys.argv
    sys.argv = ["check", _root]
    try:
        if mod.main() != 1:
            failures.append("duplicate audit id must fail")
    finally:
        sys.argv = _saved

with tempfile.TemporaryDirectory() as _root:
    build(_root, [("aaaaaaa7", "current", "J", "map says ambient")], {},
          active_ids=["aaaaaaa7"])
    with open(os.path.join(_root, "audits", "rule-enforceability-audit-2026-08-14.tsv"), "w") as fh:
        fh.write("id\tplain_name\tbucket\tevidence\n")
        fh.write("aaaaaaa7\tcurrent\tJ\tmissing column\n")
    _saved = sys.argv
    sys.argv = ["check", _root]
    try:
        if mod.main() != 1:
            failures.append("malformed audit row/header must fail")
    finally:
        sys.argv = _saved

with tempfile.TemporaryDirectory() as _root:
    build(_root, [("not-an-id", "malformed", "J", "bad id")], {},
          active_ids=["aaaaaaa7"])
    _saved = sys.argv
    sys.argv = ["check", _root]
    try:
        if mod.main() != 1:
            failures.append("malformed audit id must fail")
    finally:
        sys.argv = _saved

with tempfile.TemporaryDirectory() as _root:
    build(_root, [("aaaaaaa5", "x", "U", CLAIM)], {"hooks/g.py": "unrelated"})
    _saved = sys.argv
    sys.argv = ["check", _root]
    try:
        if mod.main() != 0:
            failures.append("exit code should be 0 when the queue is fresh")
    finally:
        sys.argv = _saved

if failures:
    print("audit-queue-freshness selftest FAILED")
    for f in failures:
        print("  " + f)
    sys.exit(1)
print("audit-queue-freshness selftest passed")
