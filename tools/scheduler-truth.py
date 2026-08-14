#!/usr/bin/env python3
"""scheduler-truth.py — one answer to "what is actually scheduled on this Mac,
and does anything know about it". Program 4's "consolidate scheduler truth".

THE PROBLEM, counted 2026-08-14. Five places claim to know what runs on this
machine and no two of them agreed:

    launchctl list                 16 com.carr.* jobs loaded
    ~/Library/LaunchAgents         16 com.carr.* plists installed
    ops/launchd/                   17 plists in the repo
    ops/config/services.json       23 services declared
    ops.service                    23 rows (25 service/environment rows)

Every one of those numbers was reachable in about a minute, and nobody had put
them side by side, so the gaps were invisible: NINE launchd jobs registered
nowhere, and one plist in the repo that is not installed. A job registered
nowhere is worse off than a job that looks unhealthy — it cannot appear in
`ops-record health` at all, so its failure is not `unknown`, it is
unrepresentable, and to a human reading the table that is indistinguishable
from nothing being wrong.

WHAT THIS FILE DOES NOT CLAIM, because an earlier draft of it did and was wrong.
The registry IS applied: services.json's 23 declarations and ops.service's 23
rows agree exactly. The first version of this docstring said eleven services had
never been synced, on the strength of a `health` listing that had been truncated
to its last 60 lines by the command that produced it. Reading a summary and not
the artifact is rule fa217e48, and the number that mattered was hiding in the
part scrolled off the top: 21 of the 25 service/environment rows have never been
observed AT ALL, which is a far worse finding than the one the truncated view
suggested.

WHAT THIS IS NOT. It is not a second registry. ops/config/services.json remains
the declaration and ops.service remains its render; this file reads all five
sources and reports where they disagree. Adding a sixth source of truth to fix
five that disagree is how the disease spreads.

WHY IT RUNS WITHOUT A DATABASE. A cold Mac, a CI container and a session whose
credential has not loaded all still need the first four sources reconciled — and
the fifth being unreachable is itself a finding worth printing rather than a
reason to refuse. The database section is additive.

EXIT CODES: 0 clean, 1 drift found, 2 could not read the machine at all.

    tools/scheduler-truth.py              # the report
    tools/scheduler-truth.py --quiet      # only the drift lines
"""
import glob
import json
import os
import plistlib

import subprocess
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REPO_PLISTS = os.path.join(REPO, "ops", "launchd")
INSTALLED = os.path.expanduser("~/Library/LaunchAgents")
SERVICES = os.path.join(REPO, "ops", "config", "services.json")

# nightly-record-layer records every one of its STEPS itself, under one
# correlation id per night (bin/nightly.sh's record_run). Wrapping the chain as
# a whole would add a second, coarser row saying only "the chain exited 1" next
# to the precise row naming which step did — worse, not better. This is an
# exemption with a reason, which is the only kind that should exist: a bare
# allow-list is how a gap becomes permanent.
WRAPPER_EXEMPT = {
    "nightly-record-layer": "records each of its own steps under one correlation "
                            "id; a whole-chain row would be coarser than what it "
                            "already writes",
}

DRIFT = []


def drift(line: str) -> None:
    DRIFT.append(line)


def label_of(path: str) -> str:
    return os.path.basename(path).replace(".plist", "")


def read_plist(path: str) -> dict:
    """Repo copies store {{REPO}}/{{HOME}}/{{VAULT}} placeholders (see
    ops/config-as-code.py) — plistlib does not care, and neither does anything
    below, which compares structure rather than resolved paths."""
    try:
        with open(path, "rb") as fh:
            return plistlib.load(fh)
    except Exception as exc:  # noqa: BLE001 — a malformed plist IS the finding
        return {"__error__": str(exc)}


def loaded_labels() -> set:
    """What launchd currently holds. `launchctl list` prints pid/status/label."""
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True,
                             text=True, timeout=30).stdout
    except Exception:
        return None
    return {ln.split("\t")[-1].strip() for ln in out.splitlines()
            if "com.carr." in ln}


def wrapped(pl: dict) -> bool:
    return any("run-scheduled.sh" in str(a) for a in pl.get("ProgramArguments", []))


def wrapper_service(pl: dict) -> str:
    """The service key a wrapped plist reports under: the argument right after
    the wrapper path. Reading it back is what catches a copy-paste that wrapped
    a job under a neighbour's key — the failure mode of rewiring seven files."""
    args = [str(a) for a in pl.get("ProgramArguments", [])]
    for i, a in enumerate(args):
        if "run-scheduled.sh" in a and i + 1 < len(args):
            return args[i + 1]
    return ""


def main() -> int:
    quiet = "--quiet" in sys.argv

    if not os.path.isdir(REPO_PLISTS):
        print(f"scheduler-truth: {REPO_PLISTS} is missing", file=sys.stderr)
        return 2

    repo = {label_of(p): read_plist(p) for p in glob.glob(f"{REPO_PLISTS}/*.plist")}
    inst = {label_of(p): read_plist(p)
            for p in glob.glob(f"{INSTALLED}/com.carr.*.plist")}
    live = loaded_labels()

    declared = {}
    try:
        for s in json.load(open(SERVICES))["services"]:
            declared[s["key"]] = s
    except Exception as exc:  # noqa: BLE001
        print(f"scheduler-truth: cannot read {SERVICES}: {exc}", file=sys.stderr)
        return 2

    launchd_declared = {k: v for k, v in declared.items()
                        if v.get("runtime") == "launchd"}

    if not quiet:
        print("Scheduler truth — five sources, one table\n")
        print(f"  ops/launchd/            {len(repo):>3} plist(s) in the repo")
        print(f"  ~/Library/LaunchAgents  {len(inst):>3} plist(s) installed")
        print(f"  launchctl list          {'  ?' if live is None else f'{len(live):>3}'}"
              f" com.carr.* job(s) loaded")
        print(f"  services.json           {len(declared):>3} service(s) declared"
              f" ({len(launchd_declared)} of them launchd)")

    # ── repo vs machine ──────────────────────────────────────────────────────
    for lbl in sorted(set(repo) - set(inst)):
        drift(f"IN REPO, NOT INSTALLED   {lbl} — ops/config-as-code.py install --apply")
    for lbl in sorted(set(inst) - set(repo)):
        drift(f"INSTALLED, NOT IN REPO   {lbl} — ops/config-as-code.py pull "
              f"(config that exists only on one Mac is config nobody can restore)")

    # ── installed vs loaded ──────────────────────────────────────────────────
    if live is not None:
        for lbl in sorted(set(inst) - live):
            drift(f"INSTALLED, NOT LOADED    {lbl} — launchctl bootstrap "
                  f"gui/$UID {INSTALLED}/{lbl}.plist")
        for lbl in sorted(live - set(inst)):
            drift(f"LOADED, NO PLIST ON DISK {lbl} — loaded from something that is "
                  f"no longer there; it will not survive a reboot")

    # ── malformed ────────────────────────────────────────────────────────────
    for src, where in ((repo, "ops/launchd"), (inst, "~/Library/LaunchAgents")):
        for lbl, pl in sorted(src.items()):
            if "__error__" in pl:
                drift(f"UNREADABLE PLIST         {where}/{lbl}.plist — {pl['__error__']}")

    # ── scheduled but registered nowhere ─────────────────────────────────────
    # This is the one that matters most. A launchd job with no service row
    # cannot appear in `ops-record health` at all, so its failure is not
    # "unknown" — it is unrepresentable, which reads to a human exactly like
    # nothing being wrong.
    for lbl in sorted(repo):
        short = lbl.replace("com.carr.", "")
        if short not in declared:
            drift(f"SCHEDULED, UNREGISTERED  {short} — runs on this Mac and no "
                  f"service declares it, so no health row can ever mention it. "
                  f"Add it to ops/config/services.json")

    for key, svc in sorted(launchd_declared.items()):
        if f"com.carr.{key}" not in repo:
            drift(f"REGISTERED, NO PLIST     {key} — services.json declares a "
                  f"launchd service with no plist in ops/launchd/")

    # ── recording coverage, the Program 4 gate ───────────────────────────────
    if not quiet:
        print("\nDurable run results — does a failure in this job survive it?\n")
    covered = uncovered = 0
    for key in sorted(launchd_declared):
        lbl = f"com.carr.{key}"
        pl = repo.get(lbl)
        if pl is None:
            continue
        if key in WRAPPER_EXEMPT:
            if not quiet:
                print(f"  n/a  {key:<24} {WRAPPER_EXEMPT[key]}")
            covered += 1
            continue
        if "__error__" in pl:
            # Already reported as UNREADABLE above. Saying "no durable run
            # result" here too would be a second finding for one cause, and the
            # remedy it prints (wrap the job) would be wrong — the job may
            # already be wrapped, as this one was.
            if not quiet:
                print(f"  ?    {key:<24} cannot tell — its plist does not parse")
            continue
        if wrapped(pl):
            got = wrapper_service(pl)
            if got != key:
                drift(f"WRAPPED UNDER THE WRONG KEY {key} — its plist records as "
                      f"'{got}', so its runs land on another service's history")
            elif not quiet:
                print(f"  ok   {key:<24} records through bin/run-scheduled.sh")
            covered += 1
        else:
            uncovered += 1
            drift(f"NO DURABLE RUN RESULT    {key} — invokes its script directly, "
                  f"so a failure lives only in a local log. Wrap it: "
                  f"/bin/zsh {{{{REPO}}}}/bin/run-scheduled.sh {key} launchd.run <its "
                  f"current ProgramArguments>")

    if not quiet:
        total = covered + uncovered
        print(f"\n  {covered}/{total} registered launchd service(s) write a durable "
              f"run result.")

    # ── the registry vs its render ───────────────────────────────────────────
    # Additive: absent credentials are a finding, not a refusal.
    # The credential is loaded by ops-record.py's OWN loader, imported rather
    # than reimplemented. The first cut of this file re-parsed
    # ~/.config/carr/db.env with a regex, stripped double quotes and not single
    # ones, and handed psycopg a DSN still wrapped in apostrophes — which failed
    # with "invalid connection option" and would have reported the registry
    # unreachable on a Mac where it was perfectly reachable. Values in that file
    # are shell-quoted so `set -a; . db.env` survives an & in a DSN, and
    # ops-record.py already knows that. Rule a8c55a47: one job, one code path.
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "ops_record", os.path.join(REPO, "tools", "ops-record.py"))
        ops_record = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ops_record)
        ops_record._load_db_env()
        dsn = next((os.environ[v] for v in ("DATABASE_URL", "CARR_DB_JOBS_URL")
                    if os.environ.get(v)), "")
        if dsn:
            import psycopg as pg
            with pg.connect(dsn, connect_timeout=10) as conn:
                rows = conn.execute("select key from ops.service").fetchall()
            in_db = {r[0] for r in rows}
            if not quiet:
                print(f"\n  ops.service             {len(in_db):>3} row(s) in the "
                      f"database")
            missing = set(declared) - in_db
            if missing:
                drift(f"DECLARED, NOT APPLIED    {len(missing)} service(s) in "
                      f"services.json have no ops.service row "
                      f"({', '.join(sorted(missing)[:4])}"
                      f"{', …' if len(missing) > 4 else ''}) — "
                      f"tools/ops-record.py sync-registry")
            for k in sorted(in_db - set(declared)):
                drift(f"IN DATABASE, UNDECLARED  {k} — an ops.service row no "
                      f"declaration produces; sync-registry will never remove it")
        elif not quiet:
            print("\n  ops.service               ? no database credential in this "
                  "environment — the registry's render could not be compared")
    except ImportError:
        if not quiet:
            print("\n  ops.service               ? psycopg unavailable — the "
                  "registry's render could not be compared")
    except Exception as exc:  # noqa: BLE001
        if not quiet:
            print(f"\n  ops.service               ? unreachable ({exc}) — the "
                  f"registry's render could not be compared")

    # ── verdict ──────────────────────────────────────────────────────────────
    print()
    if not DRIFT:
        print("scheduler-truth: no drift — every source agrees.")
        return 0
    print(f"scheduler-truth: {len(DRIFT)} drift finding(s)\n")
    for d in DRIFT:
        print(f"  ⚠︎ {d}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
