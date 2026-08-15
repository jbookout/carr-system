#!/usr/bin/env python3
"""Selftest for ops-record.py's incident resolution preconditions.

WHY THIS EXISTS. Until 2026-08-14 nothing in this repo could close an
incident. Collectors opened them (mcp-server/src/trace.js, ops-record assess)
and `assess` moved a recovered one to `monitoring` while deliberately leaving
resolved_at null "for a human" — but no verb, no subcommand and no script gave
that human a way to act. All 106 verbs were checked; not one mentions
incidents. So every incident ever opened stayed open, and the nightly
assessment reprinted the whole pile each night, including one that was a
DELIBERATE acceptance probe and could never clear on its own.

That is the alarm-that-fires-every-night problem the chain has been burned by
before, arriving from the other direction: not a check that goes red on normal
work, but a list that can only grow.

The guards below are the point. A close path that will rubber-stamp anything
is worse than none, because the pile would then look handled.

Run: python3 ops/incident-resolve-selftest.py
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


NOW = dt.datetime(2026, 8, 15, 1, 0, tzinfo=dt.timezone.utc)


def inc(**over):
    """A recovered incident, past its monitoring window — the resolvable case."""
    base = {
        "ref": "INC-20260814-08",
        "state": "monitoring",
        "recovery_evidence_ref": "ops.run:abc",
        "monitoring_until": NOW - dt.timedelta(hours=1),
    }
    base.update(over)
    return base


def check(incident, root_cause="cause", evidence=None, early=False):
    return opsrec.resolve_preconditions(
        incident, root_cause=root_cause, evidence=evidence,
        allow_early=early, now=NOW)


@case("a recovered incident past its window resolves, and carries its evidence")
def _(assert_):
    ok, err, fields = check(inc())
    assert_(ok, f"should resolve, got {err}")
    assert_(fields["recovery_evidence_ref"] == "ops.run:abc",
            f"must keep the recovery evidence assess recorded: {fields}")
    assert_(fields["resolved_at"] == NOW, f"resolved_at must be stamped: {fields}")


@case("an incident still inside its monitoring window is refused")
def _(assert_):
    ok, err, _ = check(inc(monitoring_until=NOW + dt.timedelta(hours=6)))
    assert_(not ok, "a window with 6h left must not close")
    assert_("monitoring" in err.lower() or "window" in err.lower(),
            f"the refusal should name the window: {err}")


@case("--allow-early opens that window, because a probe has nothing to watch")
def _(assert_):
    ok, err, _ = check(inc(monitoring_until=NOW + dt.timedelta(hours=6)), early=True)
    assert_(ok, f"an explicit early close should be allowed: {err}")


@case("a root cause is required, because 'close with an outcome' means one is recorded")
def _(assert_):
    for bad in ("", "   ", None):
        ok, err, _ = check(inc(), root_cause=bad)
        assert_(not ok, f"root_cause {bad!r} must be refused")
        assert_("root" in err.lower() or "outcome" in err.lower(),
                f"the refusal should name the missing outcome: {err}")


@case("an incident with no recovery evidence needs evidence supplied")
def _(assert_):
    # The induced-probe shape: state 'detected', no green run behind it, and
    # none will ever arrive, so assess can never move it on.
    detected = inc(state="detected", recovery_evidence_ref=None, monitoring_until=None)
    ok, err, _ = check(detected, early=True)
    assert_(not ok, "resolving with no evidence at all must be refused")
    assert_("evidence" in err.lower(), f"the refusal should name evidence: {err}")

    ok2, err2, fields = check(detected, evidence="incident_fact:deliberate-probe", early=True)
    assert_(ok2, f"supplied evidence should satisfy it: {err2}")
    assert_(fields["recovery_evidence_ref"] == "incident_fact:deliberate-probe",
            f"the supplied evidence must be what gets written: {fields}")
    assert_(fields["monitoring_until"] is not None,
            "monitoring_until is NOT NULL under the resolved constraint, so it must be set")


@case("running without owner privileges names the break-glass command, not a psycopg error")
def _(assert_):
    ok, err = opsrec.resolve_authority({})
    assert_(not ok, "no DATABASE_URL means no owner credential, so it must refuse")
    assert_("db-tap.py" in err and "--reason" in err,
            f"the refusal must carry the command that works: {err}")
    assert_("carr_jobs" in err and "resolved_at" in err,
            f"it must say WHY the job role cannot, or the reader retries as themselves: {err}")
    ok2, _ = opsrec.resolve_authority({"DATABASE_URL": "postgres://x"})
    assert_(ok2, "with the owner credential present it proceeds")


@case("it works off the real clock when no time is injected")
def _(assert_):
    # Every other case passes NOW, so the default-clock branch never runs in
    # them. It was wrong when first written — datetime.datetime.now() against a
    # `from datetime import datetime` import — and would have raised on the
    # first real call while the suite stayed green.
    ok, err, fields = opsrec.resolve_preconditions(
        inc(monitoring_until=None), root_cause="cause", evidence="ops.run:x")
    assert_(ok, f"should resolve against the real clock: {err}")
    assert_(fields["resolved_at"].tzinfo is not None,
            "the stamp must be timezone-aware, since the column is timestamptz")


@case("an already-resolved incident is refused rather than re-stamped")
def _(assert_):
    for st in ("resolved", "reviewed"):
        ok, err, _ = check(inc(state=st))
        assert_(not ok, f"state {st} must not resolve again")
        assert_(st in err.lower() or "already" in err.lower(),
                f"the refusal should say it is already closed: {err}")


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
