#!/usr/bin/env python3
"""
run-ledger.py — the Program 3 COLLECTOR: turn real work into ops.run rows.

WHAT THIS IS FOR. Migration 0115 built the job-run ledger, the service catalog,
the deployment and incident tables and the ops.v_trace view, and
ops/program3-trace-gate.py proves one query explains a failed journey. But that
gate seeds its own journey and rolls it back, so it passes against a database in
which nothing real has ever been recorded. Nothing writes to ops.run. This is
the thing that writes to ops.run.

Its acceptance test is ops/run-ledger-selftest.py and was written first
(rule e65efc68). Read that file for WHY each rule below exists; this one says
what they are.

THE SHAPE: SPOOL, THEN FLUSH.

    record  — append one row to a local JSONL spool. No network, ever.
    flush   — drain the spool into ops.run in one connection.
    status  — say how many rows are waiting and how old the oldest is.

Recording is a local append because the collector must never fail the thing it
observes. bin/nightly.sh's step() wrapper calls `record` after every step; a
Neon round trip per step would put the network on the critical path of a chain
whose whole job is to be reliable, and a database outage would turn one failure
into twenty. Delivery is at-least-once: the spool is cleared only after the rows
are committed, so a flush that dies retries on the next run, and a chain that
dies before its own flush leaves its rows on disk for the next chain to deliver.
That is why the chain flushes at the START as well as the end.

TWO REFUSALS, BOTH DELIBERATE.

  ENVIRONMENT IS NEVER INFERRED. The frozen read contract says "Environment is
  required on each operational object and never inferred", and release.js
  already set the idiom for the unlabelled case: "an unlabelled deployment is
  never assumed to be production". With no CARR_ENV this writes NOTHING and says
  so. There is no honest fallback row — ops.run.environment is NOT NULL under a
  four-value check, so 'unknown' is not available and guessing 'production' is
  exactly how a local rehearsal ends up in production's health view.

  A FAILURE ALWAYS NAMES A CLASS. ops.run refuses a failed run with no
  failure_class. If this collector leaned on that constraint, a nightly failure
  would be rejected at flush time and the failure it was reporting would vanish —
  the observability layer would silently drop precisely the events it exists to
  capture. So a failure with no class given is recorded as 'unclassified' rather
  than dropped, which is visible and countable and can be improved later.

EVERY EXIT IS 0. This tool has no opinion strong enough to be worth failing a
caller over. Problems go to stderr where the caller's own log will carry them.

USAGE

  ops/run-ledger.py record --service nightly-record-layer --run-key nightly.exports \
      --state succeeded --exit-code 0 \
      --started 2026-08-14T01:00:00Z --ended 2026-08-14T01:00:05Z \
      --source-ref bin/nightly.sh
  ops/run-ledger.py flush
  ops/run-ledger.py status

ENVIRONMENT
  CARR_ENV               required: local | rehearsal | staging | production
  CARR_CORRELATION_ID    the chain's id; one is minted per row when absent
  CARR_RUN_LEDGER_SPOOL  spool path (default: out/run-ledger-spool.jsonl)
  DATABASE_URL           needed by `flush` only
"""

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# The contract's own vocabularies. Kept as literals here rather than read from
# the JSON so this tool has no import-time dependency on the contract tree, and
# so a drift between the two is a test failure rather than a silent widening.
ENVIRONMENTS = ("local", "rehearsal", "staging", "production")
STATES = ("scheduled", "queued", "running", "succeeded", "failed",
          "timed_out", "cancelled", "skipped", "stale", "unknown")
KINDS = ("job", "check")
SOURCE_KINDS = ("collector", "registry", "wrapper", "operator")
NEEDS_CLASS = ("failed", "timed_out")


def warn(msg):
    print(f"run-ledger: {msg}", file=sys.stderr)


def spool_path():
    p = os.environ.get("CARR_RUN_LEDGER_SPOOL")
    return Path(p) if p else REPO / "out" / "run-ledger-spool.jsonl"


def resolve_environment():
    """The environment, or None. NEVER a guess — see the module docstring."""
    env = (os.environ.get("CARR_ENV") or "").strip()
    if not env:
        warn("no CARR_ENV — refusing to record. An unlabelled run is never assumed "
             "to be production, and ops.run has no 'unknown' environment to fall "
             "back to. Set CARR_ENV to one of: " + ", ".join(ENVIRONMENTS))
        return None
    if env not in ENVIRONMENTS:
        warn(f"CARR_ENV={env!r} is not one of the contract's four environments "
             f"({', '.join(ENVIRONMENTS)}) — refusing to record rather than "
             f"inventing a fifth.")
        return None
    return env


def cmd_record(args):
    env = resolve_environment()
    if env is None:
        return 0

    if args.state not in STATES:
        warn(f"state {args.state!r} is not one of the contract's ten run states "
             f"({', '.join(STATES)}) — refusing to record rather than coercing it "
             f"into a state it might not be.")
        return 0
    if args.kind not in KINDS:
        warn(f"kind {args.kind!r} is not 'job' or 'check' — refusing.")
        return 0
    if args.source_kind not in SOURCE_KINDS:
        warn(f"source-kind {args.source_kind!r} is not one of "
             f"{', '.join(SOURCE_KINDS)} — refusing.")
        return 0

    failure_class = args.failure_class
    if args.state in NEEDS_CLASS and not failure_class:
        # Recorded, not dropped. See the module docstring: leaning on the check
        # constraint here would make the ledger swallow the failures it exists
        # to report.
        failure_class = "unclassified"
    if args.state not in NEEDS_CLASS:
        # A skip or a success has no failure to classify, and hanging one on it
        # would put noise in the column whose whole value is that the tenth
        # occurrence of a class is visible as a pattern.
        failure_class = None

    row = {
        "correlation_id": (os.environ.get("CARR_CORRELATION_ID") or "").strip()
                          or str(uuid.uuid4()),
        "kind": args.kind,
        "service_key": args.service,
        "environment": env,
        "run_key": args.run_key,
        "state": args.state,
        "failure_class": failure_class,
        "exit_code": args.exit_code,
        "attempt": args.attempt,
        "started_at": args.started,
        "ended_at": args.ended,
        "source_kind": args.source_kind,
        "source_ref": args.source_ref,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "evidence_ref": args.evidence_ref,
        "detail": args.detail,
    }

    try:
        p = spool_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    except Exception as exc:
        # The last thing that could take the chain down. It does not.
        warn(f"could not write the spool ({exc}) — this run is unrecorded, but the "
             f"job it observed is unaffected.")
    return 0


def cmd_flush(args):
    p = spool_path()
    if not p.exists():
        print("run-ledger: nothing spooled.")
        return 0
    try:
        lines = [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    except Exception as exc:
        warn(f"could not read the spool ({exc}); leaving it in place.")
        return 0
    if not lines:
        print("run-ledger: nothing spooled.")
        return 0

    dsn = os.environ.get("DATABASE_URL") or ""
    if not dsn:
        warn(f"no DATABASE_URL — {len(lines)} row(s) stay spooled for the next flush.")
        return 0

    try:
        import psycopg
    except ImportError:
        warn(f"psycopg not installed — {len(lines)} row(s) stay spooled.")
        return 0

    rows, malformed = [], 0
    for line in lines:
        try:
            rows.append(json.loads(line))
        except Exception:
            malformed += 1
    if malformed:
        warn(f"{malformed} malformed spool line(s) skipped.")

    written, rejected = 0, []
    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                # Resolve the service catalog once. A run_key whose service is not
                # in the catalog cannot be stored (service_id is a FK and that is
                # correct — an unnamed service is not an observation), so those
                # rows go to a rejects file rather than blocking the spool for ever.
                cur.execute("select key, id from ops.service")
                catalog = {k: i for k, i in cur.fetchall()}
                for r in rows:
                    sid = catalog.get(r.get("service_key"))
                    if sid is None:
                        rejected.append(r)
                        continue
                    cur.execute(
                        """insert into ops.run
                             (correlation_id, kind, service_id, environment, run_key,
                              state, failure_class, exit_code, attempt,
                              started_at, ended_at, source_kind, source_ref,
                              observed_at, evidence_ref, detail)
                           values (%s,%s,%s,%s,%s,%s,%s,%s,%s,
                                   %s::timestamptz,%s::timestamptz,%s,%s,
                                   %s::timestamptz,%s,%s)""",
                        (r["correlation_id"], r.get("kind", "job"), sid,
                         r["environment"], r["run_key"], r["state"],
                         r.get("failure_class"), r.get("exit_code"),
                         r.get("attempt") or 1, r.get("started_at"), r.get("ended_at"),
                         r.get("source_kind", "wrapper"), r.get("source_ref", "unknown"),
                         r.get("observed_at"), r.get("evidence_ref"), r.get("detail")))
                    written += 1
            conn.commit()
    except Exception as exc:
        # THE SPOOL IS NOT TOUCHED. Delivery is at-least-once: whatever did not
        # commit is retried by the next flush.
        warn(f"flush failed ({exc}) — {len(rows)} row(s) stay spooled and will be "
             f"retried. Nothing was lost.")
        return 0

    # Only now — after the commit — is the spool cleared.
    try:
        if rejected:
            rp = p.with_suffix(p.suffix + ".rejected")
            with rp.open("a", encoding="utf-8") as fh:
                for r in rejected:
                    fh.write(json.dumps(r, separators=(",", ":")) + "\n")
            warn(f"{len(rejected)} row(s) name a service that is not in ops.service; "
                 f"they were moved to {rp} rather than dropped. Add the service to "
                 f"the catalog and replay that file if they matter.")
        p.write_text("", encoding="utf-8")
    except Exception as exc:
        warn(f"rows were committed but the spool could not be cleared ({exc}). The "
             f"next flush will deliver them again — at-least-once, by design.")

    print(f"run-ledger: {written} run(s) delivered"
          + (f", {len(rejected)} rejected" if rejected else "") + ".")
    return 0


def cmd_status(args):
    p = spool_path()
    if not p.exists():
        print(f"run-ledger: no spool at {p}")
        return 0
    try:
        lines = [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    except Exception as exc:
        warn(f"could not read the spool ({exc})")
        return 0
    if not lines:
        print(f"run-ledger: spool empty ({p})")
        return 0
    oldest = None
    for line in lines:
        try:
            o = json.loads(line).get("observed_at")
            if o and (oldest is None or o < oldest):
                oldest = o
        except Exception:
            pass
    print(f"run-ledger: {len(lines)} run(s) waiting in {p}"
          + (f", oldest observed {oldest}" if oldest else ""))
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="Program 3 collector: spool run outcomes, flush them to ops.run.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("record", help="append one run to the spool (no network)")
    r.add_argument("--service", required=True, help="ops.service key, e.g. nightly-record-layer")
    r.add_argument("--run-key", required=True, help="e.g. nightly.exports")
    r.add_argument("--state", required=True, help=" | ".join(STATES))
    r.add_argument("--kind", default="job", help="job | check (default: job)")
    r.add_argument("--exit-code", type=int, default=None)
    r.add_argument("--attempt", type=int, default=1)
    r.add_argument("--started", default=None, help="RFC3339")
    r.add_argument("--ended", default=None, help="RFC3339")
    r.add_argument("--source-kind", default="wrapper", help=" | ".join(SOURCE_KINDS))
    r.add_argument("--source-ref", required=True, help="e.g. bin/nightly.sh — never a payload")
    r.add_argument("--failure-class", default=None)
    r.add_argument("--evidence-ref", default=None, help="a POINTER to evidence, never its content")
    r.add_argument("--detail", default=None, help="one redacted line; no secrets, no client content")
    r.set_defaults(fn=cmd_record)

    f = sub.add_parser("flush", help="deliver the spool to ops.run")
    f.set_defaults(fn=cmd_flush)

    s = sub.add_parser("status", help="how many runs are waiting")
    s.set_defaults(fn=cmd_status)

    args = ap.parse_args()
    try:
        return args.fn(args)
    except Exception as exc:
        # Belt and braces: this tool never fails its caller.
        warn(f"unexpected error ({exc}); exiting 0 so the observed job is unaffected.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
