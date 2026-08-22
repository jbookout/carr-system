#!/usr/bin/env python3
"""recovery-point.py — how old is the newest backup, across BOTH backup paths.

WHY THIS EXISTS (2026-08-21). bin/nightly.sh computed the recovery point from
one line:

    newest_backup="$(ls -t "$REPO"/backups/*.sql.age 2>/dev/null | head -1)"

That counts the LOCAL dump only. There are two backup paths, and Program 4
built the second one on purpose so the backup of last resort would not depend
on the one machine everything else depends on:

  · LOCAL  — bin/nightly.sh -> bin/backup-dump.sh on Joe's Mac, archived to R2,
             reading CARR_DB_BACKUP_URL from ~/.config/carr/db.env.
  · CLOUD  — .github/workflows/backup-nightly.yml, the same script unmodified,
             same age public key, stored as a 90-day workflow artifact, reading
             BACKUP_DATABASE_URL as a GitHub Actions secret.

THE FALSE ALARM THIS REMOVES, measured 2026-08-21. The local path had no
credential, so the newest file in backups/ was four and a half days old and the
chain reported "newest backup is 104h old, objective is 24h" every run. Mean-
while the cloud workflow had succeeded that morning at 03:31 and every morning
before it. The real recovery point was about twelve hours, comfortably inside
the objective, and every local signal said otherwise.

Worse, the alarm pointed at a fix that would have made the system weaker.
migrations/0119_backup_role.sql states the rule on the role itself: the backup
credential is "held through BACKUP_DATABASE_URL, a GitHub Actions secret, never
on Joe's Mac and never in this repo." Adding CARR_DB_BACKUP_URL locally to
silence the alarm would have put the credential on the exact machine the second
path exists to be independent of. An alarm that is loudest when you are safe,
and whose obvious remedy undoes the isolation, is worse than no alarm.

THREE STATES, NEVER TWO. Each path reports fresh, stale, or UNKNOWN, and
unknown is never quietly folded into either of the others:

  · a path with no backup is a real gap and says so.
  · a path that is unconfigured BY DESIGN (local, on a machine that
    deliberately holds no credential) is not a gap; it is a path that was
    never meant to run here.
  · a path we could not ask — gh missing, gh not authenticated, the API
    unreachable — is UNKNOWN. It is not treated as fresh, because that would
    hide a genuinely dead workflow, and it is not treated as a gap, because
    that recreates the false alarm one layer up.

The overall recovery point is the NEWEST across the paths that answered. If no
path answered, the answer is unknown and the exit code says so rather than
inventing a number.

NO INTERACTIVE CREDENTIAL (rule 847f9995). The cloud read goes through `gh`,
which holds a stored token. If that token is absent or expired the read returns
UNKNOWN and the caller degrades; nothing here ever prompts, and nothing here
ever handles the backup credential itself.

Usage:
  ops/recovery-point.py                 # human-readable report
  ops/recovery-point.py --json          # machine-readable
  ops/recovery-point.py --hours         # just the age in whole hours, or the
                                        # word "unknown"; what nightly.sh reads

Exit codes:
  0  recovery point is within the objective
  1  recovery point is OUT of contract (a real, verified gap)
  2  unknown — no path could be read; do not conclude anything either way
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW = "backup-nightly.yml"
RPO_HOURS = 24  # Joe's accepted objective, 2026-08-13.


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _age_hours(when: datetime) -> float:
    return (_now() - when).total_seconds() / 3600.0


def local_path(repo: str = REPO) -> dict:
    """Newest local encrypted dump, or why there is none.

    An absent CARR_DB_BACKUP_URL is reported as 'unconfigured' rather than as a
    failure: on Joe's Mac that absence is the design (see 0119_backup_role.sql),
    and calling it a gap is what produced the false alarm this module removes.
    """
    files = sorted(glob.glob(os.path.join(repo, "backups", "*.sql.age")),
                   key=os.path.getmtime, reverse=True)
    configured = bool(os.environ.get("CARR_DB_BACKUP_URL"))
    if not files:
        return {"path": "local", "state": "none", "configured": configured,
                "detail": "no encrypted dump in backups/"}
    newest = files[0]
    when = datetime.fromtimestamp(os.path.getmtime(newest), tz=timezone.utc)
    return {"path": "local", "state": "present", "configured": configured,
            "at": when.isoformat().replace("+00:00", "Z"),
            "age_hours": round(_age_hours(when), 1),
            "detail": os.path.basename(newest)}


def cloud_path(workflow: str = WORKFLOW, repo: str = REPO) -> dict:
    """Newest SUCCESSFUL run of the cloud backup workflow, or UNKNOWN.

    Deliberately asks only for successful runs. A workflow that ran and failed
    produced no backup, so counting its timestamp would report a recovery point
    that does not exist — the same class of error as counting a 200-byte corrupt
    dump as a backup, which this system has already been bitten by once.
    """
    if not shutil.which("gh"):
        return {"path": "cloud", "state": "unknown",
                "detail": "gh is not installed; cannot read the workflow"}
    try:
        out = subprocess.run(
            ["gh", "run", "list", "--workflow", workflow, "--status", "success",
             "--limit", "1", "--json", "createdAt,databaseId,conclusion"],
            cwd=repo, capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"path": "cloud", "state": "unknown",
                "detail": f"could not reach the workflow API: {type(exc).__name__}"}
    if out.returncode != 0:
        why = (out.stderr or "").strip().splitlines()
        return {"path": "cloud", "state": "unknown",
                "detail": why[-1] if why else f"gh exited {out.returncode}"}
    try:
        runs = json.loads(out.stdout or "[]")
    except json.JSONDecodeError:
        return {"path": "cloud", "state": "unknown",
                "detail": "gh returned output that is not JSON"}
    if not runs:
        return {"path": "cloud", "state": "none",
                "detail": f"no successful run of {workflow} on record"}
    run = runs[0]
    when = datetime.fromisoformat(run["createdAt"].replace("Z", "+00:00"))
    return {"path": "cloud", "state": "present",
            "at": run["createdAt"],
            "age_hours": round(_age_hours(when), 1),
            "detail": f"workflow run {run['databaseId']}"}


def assess(paths: list[dict], rpo_hours: int = RPO_HOURS) -> dict:
    """Combine the paths into one answer, keeping unknown separate from stale."""
    present = [p for p in paths if p["state"] == "present"]
    unknown = [p for p in paths if p["state"] == "unknown"]
    if not present:
        # Nothing answered with a real backup. If any path merely could not be
        # read, that is unknown; if every path answered "none", that is a gap.
        verdict = "unknown" if unknown else "gap"
        return {"verdict": verdict, "age_hours": None, "newest_path": None,
                "objective_hours": rpo_hours, "paths": paths}
    newest = min(present, key=lambda p: p["age_hours"])
    verdict = "ok" if newest["age_hours"] < rpo_hours else "out_of_contract"
    return {"verdict": verdict, "age_hours": newest["age_hours"],
            "newest_path": newest["path"], "objective_hours": rpo_hours,
            "paths": paths}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--json", action="store_true", dest="as_json")
    ap.add_argument("--hours", action="store_true",
                    help="print only the age in whole hours, or 'unknown'")
    args = ap.parse_args(argv)

    report = assess([local_path(), cloud_path()])

    if args.hours:
        age = report["age_hours"]
        print("unknown" if age is None else str(int(age)))
    elif args.as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for p in report["paths"]:
            if p["state"] == "present":
                print(f"  {p['path']:6s} {p['age_hours']:>6.1f}h  {p['detail']}")
            elif p["path"] == "local" and not p.get("configured", False):
                print(f"  {p['path']:6s}     --  not configured on this machine "
                      f"(by design; see migrations/0119_backup_role.sql)")
            else:
                print(f"  {p['path']:6s}  {p['state'].upper():>7s}  {p['detail']}")
        if report["verdict"] == "unknown":
            print("recovery point: UNKNOWN — no path could be read; "
                  "this is not evidence of a gap and not evidence of a backup")
        elif report["verdict"] == "gap":
            print("recovery point: NO BACKUP on any path — a real gap")
        else:
            print(f"recovery point: {report['age_hours']:.1f}h via "
                  f"{report['newest_path']}, objective {report['objective_hours']}h "
                  f"— {'OK' if report['verdict'] == 'ok' else 'OUT OF CONTRACT'}")

    return {"ok": 0, "out_of_contract": 1, "gap": 1, "unknown": 2}[report["verdict"]]


if __name__ == "__main__":
    raise SystemExit(main())
