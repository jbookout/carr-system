#!/usr/bin/env python3
"""Alarm on backup STALENESS, for both paths, into a channel a human reads.

WHY THIS EXISTS, and it is not because the backups were broken. Both backup
paths were emitting exactly the signal they were designed to emit and NOBODY
AND NOTHING CONSUMED IT. The local nightly skipped with exit 78 every night for
roughly two weeks from 2026-08-17 for want of the carr_backup credential; the
cloud workflow then failed six nights running, 08-27 to 09-01. Every one of
those was visible to anyone who went looking, and for weeks nobody did. An
alarm nobody answers is worse than no alarm, because its existence gets counted
as coverage.

THE THREE PROPERTIES THIS HAS TO HAVE, each one taken from a way the last
outage actually hid:

1. IT MEASURES THE AGE OF THE LAST SUCCESS, never failure events. A job that
   stops running altogether emits no failure at all -- that is precisely how the
   local path hid -- so anything triggered BY a failure cannot see the worst
   case. Age is the only signal that grows while everything is silent.

2. IT READS BOTH PATHS AND REPORTS THEM SEPARATELY. On 2026-09-02 a session
   checked one path, found it dead, and announced production had gone seventeen
   days without a backup. That was wrong: the other path had succeeded the day
   before. One path healthy is not "backed up" and one path dead is not "no
   backups" -- the honest unit is per path.

3. IT ESCALATES BY CONSECUTIVE COUNT rather than repeating identically. Six
   identical failure lines read as one line and get skimmed as one line.

WHERE THE ALARM GOES, and why not the obvious place. Not a GitHub issue:
jbookout/carr-system is a PUBLIC repository, so a failure issue would publish
which backups are broken and for how long, to anyone. Not email: no credential
here sends mail without a human gate. The model room is internal, is already
the coordination surface both partners read, needs no new credential, and is
reachable from Joe's phone -- so it is where this writes.

Read-only against the world: it inspects a directory listing and asks the
GitHub API for run conclusions. The only thing it writes is the alarm itself.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# The local chain runs nightly, so two clear nights is already abnormal; the
# cloud workflow is also nightly. These are the thresholds at which a human
# should be told, NOT the thresholds at which data is lost -- the point is to be
# woken while the problem is one night old rather than three weeks old.
LOCAL_STALE_DAYS = int(os.environ.get("BACKUP_WATCH_LOCAL_STALE_DAYS", "2"))
CLOUD_STALE_DAYS = int(os.environ.get("BACKUP_WATCH_CLOUD_STALE_DAYS", "2"))

DUMP_NAME = re.compile(r"^carr-(\d{8})\.sql\.age$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def local_last_success(backups_dir: Path) -> datetime | None:
    """Newest local dump, by the STAMP IN ITS NAME rather than its mtime.

    mtime would be wrong here: a git checkout, a clone or a worktree rewrites
    mtimes to the moment of checkout, which would make every dump look like it
    was taken today and turn this whole check green on a machine that has never
    produced one.
    """
    newest: datetime | None = None
    if not backups_dir.is_dir():
        return None
    for entry in backups_dir.iterdir():
        match = DUMP_NAME.match(entry.name)
        if not match:
            continue
        try:
            stamp = datetime.strptime(match.group(1), "%Y%m%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if newest is None or stamp > newest:
            newest = stamp
    return newest


def cloud_runs(limit: int = 20) -> list[dict]:
    """Recent runs of the nightly backup workflow, newest first."""
    proc = subprocess.run(
        ["gh", "run", "list", "--workflow", "backup-nightly.yml",
         "--limit", str(limit), "--json", "conclusion,status,createdAt,url"],
        capture_output=True, text=True, timeout=60, cwd=REPO,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"gh run list failed: {proc.stderr.strip()[:300]}")
    return json.loads(proc.stdout or "[]")


def cloud_state(runs: list[dict]) -> tuple[datetime | None, int]:
    """Last successful run, and how many failures have piled up since it.

    The consecutive count is what turns a repeating line into an escalating
    one. Runs still in flight are skipped rather than counted either way: an
    in-progress run is not yet evidence of anything.
    """
    last_success: datetime | None = None
    consecutive_failures = 0
    for run in runs:
        if run.get("status") != "completed":
            continue
        when = datetime.fromisoformat(run["createdAt"].replace("Z", "+00:00"))
        if run.get("conclusion") == "success":
            last_success = when
            break
        consecutive_failures += 1
    return last_success, consecutive_failures


def age_days(when: datetime | None, now: datetime) -> float | None:
    return None if when is None else (now - when).total_seconds() / 86400.0


def assess(local_success: datetime | None, cloud_success: datetime | None,
           cloud_failures: int, now: datetime) -> dict:
    """Decide the alarm. Kept pure so the selftest can seed every case."""
    local_age = age_days(local_success, now)
    cloud_age = age_days(cloud_success, now)

    # A path that has NEVER succeeded is stale, not healthy. Treating "no
    # record" as "nothing wrong" is the same mistake as reading an absent
    # failure as a success.
    local_stale = local_age is None or local_age > LOCAL_STALE_DAYS
    cloud_stale = cloud_age is None or cloud_age > CLOUD_STALE_DAYS

    if local_stale and cloud_stale:
        severity = "BOTH BACKUP PATHS ARE STALE"
    elif local_stale or cloud_stale:
        severity = "ONE BACKUP PATH IS STALE, the other is current"
    else:
        severity = "ok"

    return {
        "ok": not (local_stale or cloud_stale),
        "severity": severity,
        "local": {"last_success": local_success.date().isoformat() if local_success else None,
                  "age_days": None if local_age is None else round(local_age, 1),
                  "stale": local_stale, "threshold_days": LOCAL_STALE_DAYS},
        "cloud": {"last_success": cloud_success.date().isoformat() if cloud_success else None,
                  "age_days": None if cloud_age is None else round(cloud_age, 1),
                  "stale": cloud_stale, "threshold_days": CLOUD_STALE_DAYS,
                  "consecutive_failures_since": cloud_failures},
    }


def alarm_text(verdict: dict) -> str:
    local, cloud = verdict["local"], verdict["cloud"]

    def describe(name: str, path: dict, extra: str = "") -> str:
        if path["last_success"] is None:
            return f"{name}: NO SUCCESSFUL BACKUP ON RECORD AT ALL.{extra}"
        state = "STALE" if path["stale"] else "current"
        return (f"{name}: {state} — last success {path['last_success']}, "
                f"{path['age_days']} days ago, alarms past {path['threshold_days']}.{extra}")

    escalation = ""
    if cloud["consecutive_failures_since"] > 1:
        escalation = (f" {cloud['consecutive_failures_since']} consecutive failed runs since "
                      f"then — this is a run of failures, not a single bad night.")

    return (
        f"BACKUP FRESHNESS ALARM — {verdict['severity']}.\n\n"
        f"{describe('LOCAL (nightly chain, backups/carr-*.sql.age)', local)}\n"
        f"{describe('CLOUD (backup-nightly.yml workflow)', cloud, escalation)}\n\n"
        "This alarm measures the AGE OF THE LAST SUCCESS, not failure events, because a "
        "job that stops running emits no failure at all — which is how the 2026-08-17 "
        "local outage stayed invisible for about two weeks. Both paths are reported "
        "separately on purpose: on 2026-09-02 checking only one produced a confident and "
        "wrong claim that production had gone seventeen days unbacked."
    )


def post_to_room(body: str) -> None:
    payload = json.dumps({"room": "model-room", "seat": "claude", "kind": "system",
                          "body": body, "idempotency_key": os.urandom(16).hex()})
    proc = subprocess.run(["./run.sh", "call", "add-room-turn", payload],
                          capture_output=True, text=True, timeout=120, cwd=REPO)
    if proc.returncode != 0:
        raise RuntimeError(f"could not post the alarm: {proc.stderr.strip()[:300]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--post", action="store_true",
                        help="post the alarm to the model room when a path is stale")
    parser.add_argument("--json", action="store_true", help="print the verdict as JSON")
    args = parser.parse_args()

    now = _now()
    local_success = local_last_success(Path(os.environ.get("BACKUP_OUTPUT_DIR",
                                                           str(REPO / "backups"))))
    try:
        last_cloud, failures = cloud_state(cloud_runs())
    except Exception as exc:                                  # noqa: BLE001
        # NOT SILENTLY GREEN. Being unable to read the cloud path is itself a
        # thing a human must hear about, because "the checker is broken" and
        # "the backups are fine" look identical from the outside otherwise.
        print(f"backup-freshness-watch: cannot read the cloud path: {exc}", file=sys.stderr)
        last_cloud, failures = None, 0

    verdict = assess(local_success, last_cloud, failures, now)
    if args.json:
        print(json.dumps(verdict, indent=2))
    else:
        print(alarm_text(verdict) if not verdict["ok"] else
              f"backup-freshness-watch: both paths current "
              f"(local {verdict['local']['age_days']}d, cloud {verdict['cloud']['age_days']}d)")

    if not verdict["ok"] and args.post:
        post_to_room(alarm_text(verdict))
        print("backup-freshness-watch: alarm posted to the model room", file=sys.stderr)

    # Exit 1 on a stale path so a scheduler treats it as the failure it is.
    return 0 if verdict["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
