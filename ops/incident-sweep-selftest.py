#!/usr/bin/env python3
"""Selftest for the elapsed-window incident sweep in ops-record.py.

WHY THIS EXISTS. Every incident said "watch until 24h clear, then close with an
outcome", and nothing ever performed that close. `assess` only moves a
recovered incident INTO monitoring — its update targets detected/triaged/
investigating/mitigating, so a row already in monitoring is never touched
again. No scheduled job, launchd agent or service entry invoked the close path.
So the windows expired and the pile sat there, reprinted in full every night.

WHAT IS AND IS NOT AUTOMATED HERE. The judgment stays with the human; only the
clock-watching is automated. A sweep closes an incident ONLY when there is
nothing left to decide: it recovered against real evidence, its window has run
out, and nothing failed again for the whole window. Anything with an open
question — never recovered, still flapping, no evidence — stays open and keeps
the human's outcome requirement, which is the case ops-record.py's `resolve`
subcommand exists for.

That line matters because the database draws it too: carr_jobs holds a
column-scoped grant that cannot write resolved_at or root_cause at all, so this
sweep runs on the owner credential and is the deliberate, audited exception
rather than the collector quietly closing what it just opened.

Run: python3 ops/incident-sweep-selftest.py
"""
import datetime as dt
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL = os.path.join(os.path.dirname(HERE), "tools", "ops-record.py")

spec = importlib.util.spec_from_file_location("ops_record", TOOL)
assert spec is not None and spec.loader is not None, f"cannot load {TOOL}"
opsrec = importlib.util.module_from_spec(spec)
spec.loader.exec_module(opsrec)

CASES: list[tuple] = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn
    return deco


NOW = dt.datetime(2026, 8, 16, 7, 5, tzinfo=dt.timezone.utc)


def inc(**over):
    """The clean case: recovered, window elapsed, nothing failed since."""
    base = {
        "ref": "INC-20260814-01",
        "state": "monitoring",
        "recovery_evidence_ref": "ops.run:abc",
        "monitoring_until": NOW - dt.timedelta(hours=19),
        "title": "nightly.exports failed on nightly-record-layer (production)",
    }
    base.update(over)
    return base


def sweep(incident, clean=True, now=NOW):
    return opsrec.sweep_decision(incident, job_clean=clean, now=now)


@case("a clean elapsed window closes, and says so in words a person can read")
def _(assert_):
    close, reason = sweep(inc())
    assert_(close, f"a clean elapsed window should close: {reason}")
    assert_("monitoring window" in reason.lower(),
            f"the outcome must say what was watched: {reason}")
    assert_("24h" in reason or "clean" in reason.lower(),
            f"the outcome must say what was watched and for how long: {reason}")


@case("a window with time left is left alone")
def _(assert_):
    close, reason = sweep(inc(monitoring_until=NOW + dt.timedelta(hours=5)))
    assert_(not close, "an unelapsed window must not close")
    assert_("window" in reason.lower(), f"say why it was skipped: {reason}")


@case("anything that failed again during the window stays open for a human")
def _(assert_):
    # THE CASE THIS SWEEP EXISTS TO NOT BREAK. A service that recovers, fails
    # again, and recovers again would otherwise be closed on the clock while
    # still flapping — the exact judgment the human close is for.
    close, reason = sweep(inc(), clean=False)
    assert_(not close, "a repeat failure inside the window must block the auto-close")
    assert_("clear" in reason.lower() or "failure" in reason.lower(),
            f"say that it is not yet clear: {reason}")


@case("an incident with no recovery evidence is never swept")
def _(assert_):
    close, reason = sweep(inc(recovery_evidence_ref=None))
    assert_(not close, "no evidence means nothing to stand on")
    assert_("evidence" in reason.lower(), f"say what is missing: {reason}")


@case("an incident that never reached monitoring is never swept")
def _(assert_):
    # The induced-probe shape: it sits in 'detected' forever because no green
    # run is ever coming. A clock sweep must not invent a recovery for it.
    for st in ("detected", "triaged", "investigating", "mitigating"):
        close, reason = sweep(inc(state=st, monitoring_until=None))
        assert_(not close, f"state {st} must not be swept: {reason}")


@case("an already-closed incident is not swept again")
def _(assert_):
    for st in ("resolved", "reviewed"):
        close, _ = sweep(inc(state=st))
        assert_(not close, f"state {st} must not close again")


@case("a null window is not treated as elapsed")
def _(assert_):
    # None must never compare as "in the past" — that would sweep every
    # incident that has no window at all.
    close, reason = sweep(inc(monitoring_until=None))
    assert_(not close, f"a missing window is not an elapsed one: {reason}")


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
