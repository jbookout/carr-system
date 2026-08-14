#!/usr/bin/env python3
"""bin/record-scheduled-run.py — the manual half of rule a8c55a47 (a manual
path and an automated path that do the same job must be the same code). The
automated half is hooks/scheduled-run-record.py, the Stop hook wired into
settings.json; both call lib/scheduled_run.py and nothing else, so there is
exactly one implementation of "what happened in a scheduled-task session".

Two subcommands:

  from-transcript   Read a Claude Code session transcript, find the scheduled-
                     task launch marker, work out the outcome deterministically
                     (see lib/scheduled_run.py's module docstring), and record
                     one ops.run row. This is what the Stop hook calls; run it
                     by hand to backfill a session the hook missed, or to check
                     what it WOULD record without depending on a live hook:

                       .venv/bin/python bin/record-scheduled-run.py from-transcript \\
                           --transcript /path/to/session.jsonl

  manual             Record a run directly, for a human who already knows the
                     outcome (e.g. re-filing a run the hook could not reach a
                     credential for):

                       .venv/bin/python bin/record-scheduled-run.py manual \\
                           --task loop-drain-weekdays --state failed \\
                           --failure-class gate_denied --detail "..."

Never fails loudly enough to look like a crash of the caller: the DB write
failing (no credential, ops schema unapplied, unregistered service) prints its
reason and exits non-zero, but this script itself never raises past main().
"""
import argparse
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from lib.scheduled_run import (  # noqa: E402
    build_run_args, run_ops_record, record_from_transcript,
)


def cmd_from_transcript(args) -> int:
    if not os.path.exists(args.transcript):
        print(f"record-scheduled-run: no such transcript: {args.transcript}", file=sys.stderr)
        return 2
    result = record_from_transcript(args.transcript, correlation=args.correlation,
                                     source_ref=args.source_ref)
    if not result.get("recorded") and "reason" in result and "task" not in result:
        print(f"record-scheduled-run: {result['reason']}")
        return 0  # not a scheduled-task transcript — nothing to record, not an error
    print(f"record-scheduled-run: task={result['task']} state={result['state']} "
          f"failure_class={result.get('failure_class')} exit={result['exit_code']}")
    if result["stdout"]:
        print(result["stdout"])
    if result["stderr"]:
        print(result["stderr"], file=sys.stderr)
    return 0 if result["recorded"] else 1


def cmd_manual(args) -> int:
    argv = build_run_args(args.task, args.state, args.failure_class,
                           args.started_at, args.ended_at,
                           args.detail or f"manually recorded ({args.state})",
                           args.correlation, args.source_ref)
    rc, out, err = run_ops_record(argv)
    if out:
        print(out.strip())
    if err:
        print(err.strip(), file=sys.stderr)
    return rc


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("from-transcript",
                        help="derive task/outcome from a session transcript and record it")
    t.add_argument("--transcript", required=True)
    t.add_argument("--correlation")
    t.add_argument("--source-ref", default="bin/record-scheduled-run.py")
    t.set_defaults(func=cmd_from_transcript)

    m = sub.add_parser("manual", help="record a run a human already knows the outcome of")
    m.add_argument("--task", required=True, help="taskId — must match a key in "
                    "ops/config/services.json")
    m.add_argument("--state", required=True,
                    choices=["succeeded", "failed", "timed_out", "cancelled", "skipped"])
    m.add_argument("--failure-class")
    m.add_argument("--started-at")
    m.add_argument("--ended-at")
    m.add_argument("--detail")
    m.add_argument("--correlation")
    m.add_argument("--source-ref", default="bin/record-scheduled-run.py")
    m.set_defaults(func=cmd_manual)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
