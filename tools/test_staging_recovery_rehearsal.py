#!/usr/bin/env python3
"""Hermetic safety cases for the staging recovery controller.

This deliberately replaces the provider runner: a controller unit test must
prove its ordering and recovery semantics without touching a live Worker.
"""
import importlib.util
from pathlib import Path

path = Path(__file__).with_name("staging-recovery-rehearsal.py")
spec = importlib.util.spec_from_file_location("rehearsal", path)
assert spec is not None and spec.loader is not None
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)


def argv(**extra):
    base = ["--release-key", "current", "--prior-release-key", "prior",
            "--current-sha", "a" * 40, "--prior-sha", "b" * 40, "--execute"]
    for key, value in extra.items():
        base.extend([key, value])
    return base


def run_with(outcomes):
    calls, records = [], []
    old_execute, old_record = mod.execute_step, mod.record_restore_unknown
    iterator = iter(outcomes)
    def fake_execute(step, sha, args, attempt, idem):
        calls.append((step, sha, attempt, idem))
        return {"step": step, "sha": sha, "idempotency_key": idem,
                "exit_code": next(iterator), "command": ["safe", step]}
    def fake_record(args, attempt, idem):
        records.append((attempt, idem))
        return {"status": "unknown", "recorded": True, "record_exit_code": 0}
    mod.execute_step, mod.record_restore_unknown = fake_execute, fake_record
    try:
        rc = mod.run(argv())
    finally:
        mod.execute_step, mod.record_restore_unknown = old_execute, old_record
    return rc, calls, records


# The legitimate chain remains exactly three typed bundle steps.
rc, calls, records = run_with([0, 0, 0])
assert rc == 0 and [row[0] for row in calls] == ["current_before", "prior", "current_after"]
assert not records
assert len({row[2] for row in calls}) == 1
assert len({row[3] for row in calls}) == 3

# A failed deploy after a possible staging mutation invokes an isolated repair,
# never a second current_after observation; no bundle can be claimed by output.
rc, calls, records = run_with([0, 1, 0])
assert rc == 1 and [row[0] for row in calls] == ["current_before", "prior", "restore_only"]
assert not records

# Failed repair remains ineligible and is auditable as unknown, not success.
rc, calls, records = run_with([0, 1, 1])
assert rc == 1 and calls[-1][0] == "restore_only" and len(records) == 1
assert records[0][1] == calls[-1][3]

# A deploy timeout is ambiguous after prepare/claim and follows the same
# isolated recovery path; it never becomes a time-window exemption.
rc, calls, records = run_with([0, 124, 0])
assert rc == 1 and [row[0] for row in calls] == ["current_before", "prior", "restore_only"]

# current_after failure also gets the isolated repair and never appends a fourth
# approval step; same correlation and a distinct idempotency key are preserved.
rc, calls, records = run_with([0, 0, 1, 0])
assert rc == 1 and [row[0] for row in calls] == ["current_before", "prior", "current_after", "restore_only"]
assert calls[-1][2] == calls[0][2] and calls[-1][3] not in {row[3] for row in calls[:-1]}

# Wrong environment and malformed SHA fail before any executor could be reached.
for invalid in (
    ["--release-key", "current", "--prior-release-key", "prior", "--current-sha", "a" * 40,
     "--prior-sha", "b" * 40, "--environment", "production"],
    ["--release-key", "current", "--prior-release-key", "prior", "--current-sha", "a;touch-pwn",
     "--prior-sha", "b" * 40],
):
    try:
        mod.run(invalid)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid controller input must refuse")

# The result writer command is argument-vector only; the reason is a fixed
# token and no release key/SHA can become shell syntax.
seen: list[list[str]] = []
old_run = mod.subprocess.run
def fake_run(command, **kwargs):
    seen.append(command)
    class R:
        returncode = 0
        stdout = ""
        stderr = ""
    return R()
mod.subprocess.run = fake_run
try:
    outcome = mod.record_restore_unknown(None, "x;not-a-shell", "11111111-2222-4333-8444-555555555555")
finally:
    mod.subprocess.run = old_run
assert outcome["recorded"] and seen[0][0].endswith("tools/ops-record.py") and ";" not in " ".join(seen[0][1:])

print("staging-recovery-rehearsal: isolated restore-only recovery cases passed")
