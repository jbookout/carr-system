#!/usr/bin/env python3
"""Selftest for hooks/session-brief.py's nightly_verdict().

WHY THIS EXISTS. On 2026-08-14 the brief opened the session with five named
nightly failures — two golden-workflow assertions, the golden workflow suite,
schema snapshot drift, and the vault drift watch. Only the LAST of those was
still failing; the other four had been fixed hours earlier and their runs had
already gone green on the parts that mattered. The accumulator was deduped at
each run boundary but never reset, so the line reported the union of every
failure since the last fully green chain and read as one catastrophic run.

That is the same disease this function was written to cure. Its own docstring
says a line that prints every session is a line nobody reads; a line that names
four already-fixed failures is worse, because the one real failure hides among
them, and the session pays to rediscover which.

Run: python3 ops/session-brief-selftest.py
"""
import importlib.util
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BRIEF = os.path.join(os.path.dirname(HERE), "hooks", "session-brief.py")

spec = importlib.util.spec_from_file_location("session_brief", BRIEF)
assert spec is not None and spec.loader is not None, f"cannot load {BRIEF}"
sb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sb)

CASES: list[tuple] = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


def write_log(text):
    fd, path = tempfile.mkstemp(suffix=".log")
    with os.fdopen(fd, "w") as fh:
        fh.write(text)
    return path


# One failing run, then a second run that failed on ONE different step. This is
# the real 2026-08-14 shape, reduced.
TWO_RUNS = """\
2026-08-14T13:08:00Z  START golden workflow suite
2026-08-14T13:08:17Z  FAIL  golden workflow suite (read verbs) (exit 1)
2026-08-14T13:08:23Z  ===== nightly chain FINISHED WITH FAILURES — see above =====
2026-08-14T13:36:34Z  FAIL  vault drift watch (check, first) (exit 2)
2026-08-14T13:39:27Z  START golden workflow suite
2026-08-14T13:43:27Z  OK    golden workflow suite (read verbs)
2026-08-14T13:43:34Z  ===== nightly chain FINISHED WITH FAILURES — see above =====
"""


@case("the verdict names only the LAST run's failures, not every failure since green")
def _(assert_):
    path = write_log(TWO_RUNS)
    out = sb.nightly_verdict(log=path)
    assert_("vault drift watch" in out,
            f"the last run's real failure must be named: {out!r}")
    assert_("golden workflow suite" not in out,
            f"a step that failed in an EARLIER run and passed in the last one must "
            f"not be reported as current: {out!r}")


@case("a clean final run says nothing at all")
def _(assert_):
    path = write_log(TWO_RUNS + "2026-08-14T14:10:00Z  ===== nightly chain OK =====\n")
    out = sb.nightly_verdict(log=path)
    assert_(out == "", f"a green chain must print nothing, got {out!r}")


@case("repeats within one run are still deduped")
def _(assert_):
    path = write_log(
        "2026-08-14T01:00:00Z  FAIL  vault drift watch (check, first) (exit 2)\n"
        "2026-08-14T01:05:00Z  FAIL  vault drift watch (check, first) (exit 2)\n"
        "2026-08-14T01:09:00Z  ===== nightly chain FINISHED WITH FAILURES =====\n")
    out = sb.nightly_verdict(log=path)
    assert_(out.count("vault drift watch") == 1,
            f"one step failing twice in a run should be named once: {out!r}")


@case("failures from a run still in flight are not attributed to the last finished one")
def _(assert_):
    # A chain that is running RIGHT NOW has emitted FAIL lines past the last
    # boundary marker. Those belong to a run that has not finished, so the
    # verdict still describes the last COMPLETED run.
    path = write_log(TWO_RUNS + "2026-08-14T14:20:00Z  FAIL  exports (7 targets) (exit 1)\n")
    out = sb.nightly_verdict(log=path)
    assert_("exports" not in out,
            f"an unfinished run's failure must not join the last verdict: {out!r}")


@case("a missing log is silent rather than fatal — the brief must never fail on this")
def _(assert_):
    out = sb.nightly_verdict(log="/nonexistent/nightly.log")
    assert_(out == "", f"a missing log should produce no line, got {out!r}")


def main():
    failures = []
    for name, fn in CASES:
        errors = []

        def assert_(cond, msg):
            if not cond:
                errors.append(msg)

        try:
            fn(assert_)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"raised {type(exc).__name__}: {exc}")
        if errors:
            print(f"[FAIL] {name}")
            for e in errors:
                print(f"    - {e}")
            failures.append(name)
        else:
            print(f"[PASS] {name}")

    print(f"\n{len(CASES) - len(failures)}/{len(CASES)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
