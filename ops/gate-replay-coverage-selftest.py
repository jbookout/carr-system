#!/usr/bin/env python3
"""gate-replay-coverage-selftest.py — proves ops/gate-replay-coverage.py
actually does its job, and that the committed real-replay fixtures are
redacted.

Per Jev's verification_selection call (2026-09-24, choice=
"selftest_synthetic_diffs", confidence 1.0): a known-bad synthetic PR diff
(touches a hook, no replay test) must FAIL the check; a known-good one
(touches a hook, includes a replay test that references a real fixture and
the gate's own module) must PASS; and the committed fixtures must scan clean
for email/hostname patterns.

Runs with no database, no network, no git repository required for the
synthetic-diff cases (they pass an explicit file list to gate-replay-coverage
via `check(...)`, never shelling to git). The fixture-redaction case reads
the actual committed files under ops/fixtures/real-replay/.

    python3 ops/gate-replay-coverage-selftest.py

ops/ci.sh already globs ops/*-selftest.py, so this runs from the commit that
adds it with no CI change.
"""
from __future__ import annotations

import glob
import importlib.util
import os
import re
import sys
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_gate_module():
    path = os.path.join(REPO_ROOT, "ops", "gate-replay-coverage.py")
    spec = importlib.util.spec_from_file_location("gate_replay_coverage", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


grc = _load_gate_module()

cases = []


def record(name, ok):
    cases.append((name, ok))
    print(f"{'PASS' if ok else 'FAIL'}  {name}")


# ---------------------------------------------------------------------------
# Known-bad synthetic PR diff: a hook changes, nothing else does.
# ---------------------------------------------------------------------------
bad_changed_files = ["hooks/bash-write-gate.py"]
bad_file_contents = {
    "hooks/bash-write-gate.py": (
        "#!/usr/bin/env python3\n"
        "import re\n"
        "DENY_RE = re.compile(r'rm -rf /')\n"
        "def check(cmd):\n"
        "    return not DENY_RE.search(cmd)\n"
    ),
}
ok_bad, failures_bad = grc.check(bad_changed_files, bad_file_contents)
record(
    "known-bad diff (hook changed, no replay selftest) is REJECTED",
    (ok_bad is False) and any("bash-write-gate.py" in f for f in failures_bad),
)
record(
    "known-bad diff failure names the specific gate",
    any("hooks/bash-write-gate.py" in f for f in failures_bad),
)

# ---------------------------------------------------------------------------
# Known-good synthetic PR diff: same hook change, PLUS a replay selftest
# that references a real fixture and the gate's own module.
# ---------------------------------------------------------------------------
good_changed_files = ["hooks/bash-write-gate.py", "ops/bash-write-gate-selftest.py"]
good_file_contents = dict(bad_file_contents)
good_file_contents["ops/bash-write-gate-selftest.py"] = (
    "#!/usr/bin/env python3\n"
    "import json, importlib.util\n"
    "spec = importlib.util.spec_from_file_location('bash_write_gate', 'hooks/bash-write-gate.py')\n"
    "bash_write_gate = importlib.util.module_from_spec(spec); spec.loader.exec_module(bash_write_gate)\n"
    "with open('ops/fixtures/real-replay/bash-commands.jsonl') as f:\n"
    "    rows = [json.loads(line) for line in f]\n"
    "for row in rows:\n"
    "    bash_write_gate.check(row['command'])\n"
    "print('bash-write-gate-selftest: replayed', len(rows), 'real commands')\n"
)
ok_good, failures_good = grc.check(good_changed_files, good_file_contents)
record(
    "known-good diff (hook changed + real-fixture replay selftest) PASSES",
    ok_good is True and failures_good == [],
)

# ---------------------------------------------------------------------------
# A selftest that changes but does NOT reference a real-replay fixture still
# fails the check -- proves the fixture-reference requirement is load-bearing,
# not just "some selftest touched".
# ---------------------------------------------------------------------------
weak_changed_files = ["hooks/bash-write-gate.py", "ops/bash-write-gate-selftest.py"]
weak_file_contents = dict(bad_file_contents)
weak_file_contents["ops/bash-write-gate-selftest.py"] = (
    "#!/usr/bin/env python3\n"
    "import bash_write_gate\n"
    "assert bash_write_gate.check('git status')\n"
    "print('ok, invented command only')\n"
)
ok_weak, failures_weak = grc.check(weak_changed_files, weak_file_contents)
record(
    "selftest with NO real-replay fixture reference still FAILS the check",
    ok_weak is False and any("bash-write-gate.py" in f for f in failures_weak),
)

# ---------------------------------------------------------------------------
# A diff that touches no hook/watched file at all is a clean no-op pass.
# ---------------------------------------------------------------------------
ok_noop, failures_noop = grc.check(["README.md"], {"README.md": "docs only\n"})
record("diff touching no watched gate passes with no failures", ok_noop is True and failures_noop == [])

# ---------------------------------------------------------------------------
# Transitive case: a lib file a hook imports changes; still requires replay.
# ---------------------------------------------------------------------------
transitive_changed = ["hooks/cmd_text.py"]
transitive_contents = {
    "hooks/cmd_text.py": "def normalize(cmd):\n    return cmd.strip()\n",
    "hooks/bash-write-gate.py": (
        "import cmd_text\n"
        "def check(cmd):\n"
        "    return cmd_text.normalize(cmd) != 'rm -rf /'\n"
    ),
}
# cmd_text.py is itself under hooks/ so it is directly watched already; this
# mainly proves the direct-watch path still fires for non-gate-suffixed
# hooks/ helper files (cmd_text.py has no "-gate" in its name).
ok_transitive, failures_transitive = grc.check(transitive_changed, transitive_contents)
record(
    "a changed hooks/*.py helper (no '-gate' suffix) is still watched",
    ok_transitive is False and any("cmd_text.py" in f for f in failures_transitive),
)

# ---------------------------------------------------------------------------
# Round-2 coordinator-review gap: a selftest that MENTIONS the fixture
# directory (in a comment/docstring/string) but never actually LOADS a
# fixture file must still FAIL -- this is the specific gap Jev's
# semantic_creation flagged at 0.57/1.0 in round 1: the first version of
# selftest_covers_gate accepted a bare text reference.
# ---------------------------------------------------------------------------
comment_only_changed = ["hooks/bash-write-gate.py", "ops/bash-write-gate-selftest.py"]
comment_only_contents = dict(bad_file_contents)
comment_only_contents["ops/bash-write-gate-selftest.py"] = (
    "#!/usr/bin/env python3\n"
    "# See ops/fixtures/real-replay/bash-commands.jsonl for real examples.\n"
    "import bash_write_gate\n"
    "assert bash_write_gate.check('git status')\n"
)
ok_comment_only, failures_comment_only = grc.check(comment_only_changed, comment_only_contents)
record(
    "selftest that only MENTIONS the fixture dir (no load call) still FAILS",
    ok_comment_only is False and any("bash-write-gate.py" in f for f in failures_comment_only),
)

# ---------------------------------------------------------------------------
# A deleted hook is never watched -- there is no logic left to replay-test.
# ---------------------------------------------------------------------------
ok_deleted, failures_deleted = grc.check(
    ["hooks/bash-write-gate.py"], {}, deleted_files={"hooks/bash-write-gate.py"}
)
record(
    "a DELETED hooks/*.py file is not a watched gate (no replay test required)",
    ok_deleted is True and failures_deleted == [],
)

# A rename (delete old path + add new path): the new path is watched
# normally; the old, deleted path is exempted.
rename_changed = ["hooks/old-name-gate.py", "hooks/new-name-gate.py"]
rename_contents = {"hooks/new-name-gate.py": "def check(cmd):\n    return True\n"}
ok_rename, failures_rename = grc.check(
    rename_changed, rename_contents, deleted_files={"hooks/old-name-gate.py"}
)
record(
    "a RENAMED hook watches only the new path, not the deleted old one",
    ok_rename is False
    and any("new-name-gate.py" in f for f in failures_rename)
    and not any("old-name-gate.py" in f for f in failures_rename),
)

# ---------------------------------------------------------------------------
# A shared lib/ file imported by TWO hooks requires replay coverage for
# BOTH, independently -- Jev's architecture_or_design pick (round 2, item 3).
# ---------------------------------------------------------------------------
shared_lib_changed = ["hooks/cmd_text.py"]
shared_lib_contents = {
    "hooks/cmd_text.py": "def normalize(cmd):\n    return cmd.strip()\n",
    "hooks/bash-write-gate.py": "import cmd_text\ndef check(cmd):\n    return cmd_text.normalize(cmd) != 'x'\n",
    "hooks/lint-gate.py": "import cmd_text\ndef check(cmd):\n    return len(cmd_text.normalize(cmd)) > 0\n",
}
watched_shared = grc.find_watched_gates(shared_lib_changed, shared_lib_contents)
record(
    "hooks/cmd_text.py changing is itself directly watched (it is under hooks/)",
    "hooks/cmd_text.py" in watched_shared,
)

# A lib/ file OUTSIDE hooks/, imported by two different hooks: both hooks
# must independently be watched (transitive import scan, not just the one
# hook that happens to sort first).
outside_lib_changed = ["lib/shared_helper.py"]
outside_lib_contents = {
    "lib/shared_helper.py": "def normalize(cmd):\n    return cmd.strip()\n",
    "hooks/bash-write-gate.py": "import shared_helper\ndef check(cmd):\n    return shared_helper.normalize(cmd) != 'x'\n",
    "hooks/lint-gate.py": "import shared_helper\ndef check(cmd):\n    return len(shared_helper.normalize(cmd)) > 0\n",
}
watched_outside = grc.find_watched_gates(outside_lib_changed, outside_lib_contents)
record(
    "a lib/ file imported by TWO hooks watches BOTH hooks independently",
    "hooks/bash-write-gate.py" in watched_outside and "hooks/lint-gate.py" in watched_outside,
)
ok_outside, failures_outside = grc.check(
    outside_lib_changed + ["ops/bash-write-gate-selftest.py"],
    dict(outside_lib_contents, **{
        "ops/bash-write-gate-selftest.py": (
            "import bash_write_gate\n"
            "with open('ops/fixtures/real-replay/bash-commands.jsonl') as f:\n"
            "    pass\n"
            "bash_write_gate.check('x')\n"
        ),
    }),
)
record(
    "covering only ONE of two importing hooks still fails the OTHER by name",
    ok_outside is False and any("lint-gate.py" in f for f in failures_outside)
    and not any("bash-write-gate.py" in f for f in failures_outside),
)

# ---------------------------------------------------------------------------
# evidence_matching: this whole synthetic-diff block is what Jev's
# evidence_matching call reviewed -- it is recorded in the PR description,
# not re-derived here, since Jev needs the ACTUAL run output as evidence.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Fixture redaction scan: every committed real-replay fixture must contain
# no email address and no bare/URL hostname.
# ---------------------------------------------------------------------------
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
BARE_HOSTNAME_RE = re.compile(
    r"(?<![\w/.-])(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+"
    r"(?:com|net|org|gov|edu)\b"
)
URL_HOST_RE = re.compile(r"\b(?:https?|ssh|git)://[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")

fixture_paths = sorted(glob.glob(os.path.join(REPO_ROOT, "ops", "fixtures", "real-replay", "*.jsonl")))
redaction_ok = len(fixture_paths) > 0
leaks = []
for path in fixture_paths:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for lineno, line in enumerate(fh, start=1):
            if EMAIL_RE.search(line) or BARE_HOSTNAME_RE.search(line) or URL_HOST_RE.search(line):
                leaks.append(f"{os.path.relpath(path, REPO_ROOT)}:{lineno}")
                redaction_ok = False
record(
    f"committed real-replay fixtures ({len(fixture_paths)} files) are redacted "
    f"(no email/hostname pattern found)"
    + (f" -- LEAKS: {leaks[:5]}" if leaks else ""),
    redaction_ok,
)

# Volume sanity: the seed set should be in the ballpark the task asked for.
bash_fixture = os.path.join(REPO_ROOT, "ops", "fixtures", "real-replay", "bash-commands.jsonl")
bash_count = 0
if os.path.exists(bash_fixture):
    with open(bash_fixture) as fh:
        bash_count = sum(1 for _ in fh)
record(f"bash-commands.jsonl has a real seed volume ({bash_count} rows)", bash_count >= 150)

# ---------------------------------------------------------------------------
# CI leak guard (item 2, round-2 coordinator review): a planted street
# address, a planted dollar amount, and a planted bearer-token-shaped string
# must EACH INDEPENDENTLY fail scan_fixtures_for_business_data, against a
# throwaway temp fixture dir -- never the real committed fixtures.
# ---------------------------------------------------------------------------
def _scan_one_planted_line(line: str) -> list:
    with tempfile.TemporaryDirectory(prefix="gate-replay-leak-selftest-") as tmp:
        with open(os.path.join(tmp, "planted.jsonl"), "w", encoding="utf-8") as fh:
            fh.write(line + "\n")
        return grc.scan_fixtures_for_business_data(tmp)


addr_hits = _scan_one_planted_line('{"command": "mail sent to 1204 Oak Street Suite 200"}')
record("a planted STREET ADDRESS independently fails the leak scan", len(addr_hits) == 1)

dollar_hits = _scan_one_planted_line('{"command": "quoted at $18.50 per SF, base rent"}')
record("a planted DOLLAR AMOUNT independently fails the leak scan", len(dollar_hits) == 1)

token_hits = _scan_one_planted_line('{"command": "curl -H \\"Authorization: Bearer notarealtoken0123456789abcdefghij\\""}')
record("a planted BEARER-TOKEN-shaped string independently fails the leak scan", len(token_hits) == 1)

clean_hits = _scan_one_planted_line('{"command": "git status --porcelain"}')
record("a clean, business-data-free planted line passes the leak scan", clean_hits == [])

# The actual committed fixtures must scan clean through the SAME function
# the CI gate calls (not a re-implementation of the pattern set).
real_fixture_hits = grc.scan_fixtures_for_business_data()
record(
    f"committed real-replay fixtures pass scan_fixtures_for_business_data "
    f"({len(real_fixture_hits)} hits)" + (f" -- {real_fixture_hits[:5]}" if real_fixture_hits else ""),
    real_fixture_hits == [],
)

hooks_fixture = os.path.join(REPO_ROOT, "ops", "fixtures", "real-replay", "hook-advisories.jsonl")
hooks_count = 0
if os.path.exists(hooks_fixture):
    with open(hooks_fixture) as fh:
        hooks_count = sum(1 for _ in fh)
record(f"hook-advisories.jsonl has a real seed volume ({hooks_count} rows)", hooks_count >= 15)

print(f"gate-replay-coverage-selftest: {sum(1 for _, ok in cases if ok)}/{len(cases)} passed")
if not all(ok for _, ok in cases):
    sys.exit(1)
