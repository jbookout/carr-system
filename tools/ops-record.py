#!/usr/bin/env python3
"""
ops-record.py — THE ONE WRITER for the operational ledger, and the read path
until the Control Room exists.

WHY ONE FILE. Rule a8c55a47: a manual path and an automated path that do the same
job must be the same code. bin/nightly.sh records a step through this; a human
recording a deploy by hand records it through this; the golden-workflow runner
records a check through this. There is no second way to put a row in ops.run, so
the two paths cannot drift apart the way the export path and the manual export
once did.

WHAT IT DOES

  sync-registry   Apply ops/config/services.json into ops.service,
                  ops.service_environment and ops.service_dependency. The repo
                  file is the source; the tables are its render. A service that
                  disappears from the file is RETIRED, never deleted — nothing
                  silently rots (rule def3e84e).

  run             Append one row to the job-run / check-run ledger.

  deployment      Append one deployment marker. /release answers what is serving
                  now; this answers what was serving then.

  release         Record a release candidate from a manifest, approve one, or
                  read one back. The release is the P0-1 object that JOINS code,
                  schema, config, tests, approval, deploy and verification;
                  before it existed, ops.deployment.release_ref pointed at
                  nothing and a deploy could name its SHA and nothing else.

  trace           Read one correlation id back as a chain. This is the terminal
                  form of the Program 3 gate view, and it is the honest interim
                  answer to "without terminal archaeology": one query against the
                  record, not a hunt through a text log on one Mac.

  health          Read the derived health of every registered service and
                  environment, with the freshness that produced it.

WHAT IT DELIBERATELY DOES NOT DO

  It never invents a service. An unknown --service is refused with the registry
  named in the message, because a catalog that mints a row for every typo stops
  being a catalog. Register it in ops/config/services.json and sync.

  It never fails the job it is recording. `run` exits non-zero and says why when
  it cannot write, and callers are expected to ignore that exit code — but the
  failure is NOT hidden, because a missing run row makes that service's health
  read `unknown` on the next look rather than staying green. That property is
  the whole design of ops.v_service_environment_health: silence is visible.

  It writes no business payload. detail is one redacted line and evidence_ref is
  a pointer. Client content, secrets and raw transcripts are absent from
  ordinary telemetry by the observability contract.

CREDENTIALS. unattended run and assess require CARR_DB_JOBS_URL — the
carr_jobs role, which holds narrow operational grants because a ledger whose
routine writer can rewrite history is not a ledger. Explicit release,
deployment and settings operations preserve the deliberate DATABASE_URL path;
reads prefer the exporter credential. Registry sync needs the owner and is
meant to be run through tools/db-tap.py.

  bin/nightly.sh                                   (records every step)
  .venv/bin/python tools/db-tap.py run tools/ops-record.py sync-registry
  .venv/bin/python tools/ops-record.py trace <correlation-id>
  .venv/bin/python tools/ops-record.py health
"""

import argparse
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlsplit

REPO = Path(__file__).resolve().parent.parent
REGISTRY = REPO / "ops" / "config" / "services.json"

# Routine ledger writes are jobs-only.  The broader write mode remains the
# explicit operator/release path used by db-tap and disposable DB tests.
DSN_FOR = {
    "routine": ("CARR_DB_JOBS_URL",),
    "write": ("DATABASE_URL", "CARR_DB_JOBS_URL"),
    "owner": ("DATABASE_URL", "CARR_DB_EXPORTER_URL"),
    "read":  ("DATABASE_URL", "CARR_DB_EXPORTER_URL", "CARR_DB_JOBS_URL"),
}

TERMINAL_RUN_STATES = {"succeeded", "failed", "timed_out", "cancelled", "skipped"}


def _load_db_env() -> None:
    """Read ~/.config/carr/db.env the same way every other job does. Values are
    shell-quoted there so `set -a; . db.env` survives an & in a DSN."""
    path = Path.home() / ".config" / "carr" / "db.env"
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("\"'"))
    except OSError:
        pass


def dsn(kind: str) -> str:
    _load_db_env()
    for name in DSN_FOR[kind]:
        value = os.environ.get(name)
        if value:
            if kind == "routine" and not _is_jobs_dsn(value):
                raise SystemExit("ops-record: CARR_DB_JOBS_URL must authenticate as carr_jobs")
            return value
    raise SystemExit(
        f"ops-record: no credential — set one of {', '.join(DSN_FOR[kind])} "
        f"(they live in ~/.config/carr/db.env)")


def _is_jobs_dsn(value: str) -> bool:
    """Reject a misleading jobs variable before it reaches psycopg."""
    parsed = urlsplit(value)
    if parsed.scheme:
        return unquote(parsed.username or "") == "carr_jobs"
    return any(part == "user=carr_jobs" for part in value.split())


def connect(kind: str):
    try:
        import psycopg
    except ImportError:
        raise SystemExit("ops-record: psycopg not installed (pip install 'psycopg[binary]')")
    conn = psycopg.connect(dsn(kind), autocommit=True)
    if kind == "routine":
        with conn.cursor() as cur:
            cur.execute("select session_user,current_user")
            row = cur.fetchone()
        if row != ("carr_jobs", "carr_jobs"):
            conn.close()
            raise SystemExit("ops-record: routine ledger connection is not carr_jobs")
    return conn


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    # `now` is accepted because the callers are SHELL scripts, and portable
    # ISO-8601 out of `date` differs between BSD and GNU. Making every wrapper
    # get that right is how a read-back timestamp ends up missing on the one
    # machine whose date(1) took the other flag.
    if value == "now":
        return datetime.now(timezone.utc)
    try:
        # Accept the ISO-8601 the shell produces, Z suffix included.
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise SystemExit(f"ops-record: not a timestamp: {value!r}")


def correlation_of(explicit: str | None) -> str:
    """One id threads a whole journey. The chain's own id wins; then the ambient
    one the caller exported; then a fresh one, because a run that cannot be
    correlated is still a chain of one and must never be a chain of none."""
    for candidate in (explicit, os.environ.get("CARR_CORRELATION_ID")):
        if candidate:
            try:
                return str(uuid.UUID(candidate))
            except ValueError:
                raise SystemExit(f"ops-record: correlation id is not a uuid: {candidate!r}")
    return str(uuid.uuid4())


def service_id(cur, key: str) -> str:
    cur.execute("select id from ops.service where key = %s", (key,))
    row = cur.fetchone()
    if not row:
        raise SystemExit(
            f"ops-record: no service registered with key {key!r}. Add it to "
            f"ops/config/services.json and run sync-registry — this tool does not "
            f"invent services, because a catalog that mints a row per typo is not "
            f"a catalog.")
    return row[0]


# ── sync-registry ────────────────────────────────────────────────────────────
def cmd_sync_registry(args) -> int:
    spec = json.loads(REGISTRY.read_text(encoding="utf-8"))
    services = spec.get("services", [])
    deps = spec.get("dependencies", [])
    declared = {s["key"] for s in services}

    changes: list[str] = []
    with connect("owner") as conn, conn.cursor() as cur:
        for s in services:
            cur.execute(
                """insert into ops.service
                       (key, name, purpose, family, criticality, owner_actor,
                        repo_path, runtime, runbook_ref, retired_at, updated_at)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s, null, now())
                   on conflict (key) do update set
                       name = excluded.name, purpose = excluded.purpose,
                       family = excluded.family, criticality = excluded.criticality,
                       owner_actor = excluded.owner_actor, repo_path = excluded.repo_path,
                       runtime = excluded.runtime, runbook_ref = excluded.runbook_ref,
                       retired_at = null, updated_at = now()
                   returning (xmax = 0) as inserted""",
                (s["key"], s["name"], s.get("purpose"), s.get("family"),
                 s.get("criticality", "medium"), s["owner_actor"],
                 s.get("repo_path"), s.get("runtime"), s.get("runbook_ref")))
            if cur.fetchone()[0]:
                changes.append(f"registered service {s['key']}")

            sid = service_id(cur, s["key"])
            for e in s.get("environments", []):
                cur.execute(
                    """insert into ops.service_environment
                           (service_id, environment, endpoint, deploy_mechanism,
                            expected_cadence_seconds, cadence_grace_seconds, notes, updated_at)
                       values (%s,%s,%s,%s,%s,%s,%s, now())
                       on conflict (service_id, environment) do update set
                           endpoint = excluded.endpoint,
                           deploy_mechanism = excluded.deploy_mechanism,
                           expected_cadence_seconds = excluded.expected_cadence_seconds,
                           cadence_grace_seconds = excluded.cadence_grace_seconds,
                           notes = excluded.notes, updated_at = now()
                       returning (xmax = 0) as inserted""",
                    (sid, e["environment"], e.get("endpoint"), e.get("deploy_mechanism"),
                     e.get("expected_cadence_seconds"), e.get("cadence_grace_seconds", 0),
                     e.get("notes")))
                if cur.fetchone()[0]:
                    changes.append(f"registered {s['key']} in {e['environment']}")

        for d in deps:
            cur.execute(
                """insert into ops.service_dependency (service_id, depends_on_id, note)
                   select a.id, b.id, %s from ops.service a, ops.service b
                    where a.key = %s and b.key = %s
                   on conflict do nothing""",
                (d.get("note"), d["service"], d["depends_on"]))

        # A service that left the file is RETIRED, not deleted. Its runs stay
        # readable, and the retirement is visible rather than a silent absence.
        cur.execute("select key from ops.service where retired_at is null")
        for (key,) in cur.fetchall():
            if key not in declared:
                cur.execute(
                    "update ops.service set retired_at = now(), updated_at = now() where key = %s",
                    (key,))
                changes.append(f"RETIRED {key} — no longer declared in ops/config/services.json")

    print(f"ops-record: registry synced — {len(services)} service(s) declared")
    for c in changes:
        print(f"  {c}")
    if not changes:
        print("  (no change)")
    return 0


# ── run ──────────────────────────────────────────────────────────────────────
def cmd_run(args) -> int:
    if args.state in ("failed", "timed_out") and not args.failure_class:
        # The database refuses this too. Failing here first gives the caller a
        # sentence instead of a constraint name.
        print("ops-record: a failed run must name its failure class (--failure-class)",
              file=sys.stderr)
        return 2

    started = parse_ts(args.started_at)
    ended = parse_ts(args.ended_at)
    if args.state in TERMINAL_RUN_STATES and ended is None:
        ended = datetime.now(timezone.utc)
    if ended is not None and started is None:
        started = ended

    corr = correlation_of(args.correlation)
    try:
        with connect("routine") as conn, conn.cursor() as cur:
            try:
                sid = service_id(cur, args.service)
            except SystemExit as e:
                # AN UNREGISTERED SERVICE IS A CONFIGURATION STATE, NOT A FAILED
                # STEP — on the COLLECTOR path only. service_id() refuses loudly
                # because that is right for an operator typing a command: they
                # want the registry named and the run rejected. A wrapper calling
                # this once per step wants the opposite. Without this, a database
                # that has the ops schema but an unseeded catalog makes
                # bin/nightly.sh print the same refusal ten times a night, which
                # is strictly worse than the missing-schema case it already
                # handles — and worse than silence, because a log that cries
                # every night is a log nobody reads. 78 is the same EX_CONFIG
                # code the missing schema returns, so the wrapper's existing
                # "say it once and stop" path covers both without new logic.
                print(str(e), file=sys.stderr)   # already carries the prefix
                return 78
            cur.execute(
                """insert into ops.run
                       (kind, correlation_id, service_id, environment, run_key, state,
                        failure_class, exit_code, attempt, started_at, ended_at,
                        source_kind, source_ref, observed_at, expires_at,
                        evidence_ref, detail)
                   select %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now(),
                          -- THE OBSERVATION DECLARES ITS OWN EXPIRY, taken from
                          -- the registry at write time when the caller does not
                          -- name one. Without this the row reads `unknown`
                          -- forever in ops.v_trace while ops.v_service_environment_
                          -- health calls the same row stale — two views over one
                          -- row disagreeing, which is how a reader stops trusting
                          -- both. A service with no registered cadence still gets
                          -- null, and null still means unknown, honestly.
                          case
                            when %s::int is not null
                              then now() + make_interval(secs => %s::int)
                            when se.expected_cadence_seconds is not null
                              then now() + make_interval(secs =>
                                     se.expected_cadence_seconds + se.cadence_grace_seconds)
                          end,
                          %s,%s
                     from (select 1) _
                     left join ops.service_environment se
                       on se.service_id = %s and se.environment = %s
                   returning id""",
                (args.kind, corr, sid, args.environment, args.key, args.state,
                 args.failure_class, args.exit_code, args.attempt, started, ended,
                 args.source_kind, args.source_ref,
                 args.expires_in, args.expires_in,
                 args.evidence_ref, (args.detail or None),
                 sid, args.environment))
            run_id = cur.fetchone()[0]
    except SystemExit:
        raise
    except Exception as e:                                   # noqa: BLE001
        # 78 = EX_CONFIG, this codebase's own "it ran, found a thing it needs
        # absent, wrote nothing and said so" convention (bin/nightly.sh treats it
        # as SKIP rather than FAIL). The ops schema being absent means migration
        # 0115 has not been applied to THIS database — a configuration state, not
        # a failed night, and callers use the distinct code to say it once rather
        # than once per step.
        # Any ops.* relation being absent means the same thing, and the first one
        # touched is ops.service (the registry lookup), not ops.run — matching on
        # ops.run alone would have missed the case this exists for.
        if 'relation "ops.' in str(e) and "does not exist" in str(e):
            print("ops-record: the ops schema is not on this database — "
                  "migration 0115 is unapplied here. Nothing recorded.",
                  file=sys.stderr)
            return 78
        # Otherwise the caller ignores this exit code on purpose. The failure is
        # still not hidden: an absent run row makes this service read unknown on
        # the next health look, which is the honest outcome and the reason
        # nothing here needs a spool to stay truthful.
        print(f"ops-record: could not record {args.service}/{args.key}: "
              f"{str(e).splitlines()[0][:200]}", file=sys.stderr)
        return 1

    print(f"{corr} {run_id}")
    return 0


# ── assess: failures become incidents ────────────────────────────────────────
# THE LAST UNBUILT ELEMENT OF PROGRAM 3. ops.incident shipped in 0115 with a
# lifecycle, separated facts and hypotheses, and constraints that refuse a
# dishonest close — and nothing ever wrote to it. On the night of 2026-08-14 the
# nightly chain failed five steps; the ledger caught all five and not one became
# an incident, because a table with no writer is a schema.
#
# IT JUDGES THE LATEST OBSERVATION, NOT THE HISTORY. For each job the question is
# "what did this thing do most recently?" — not "has it ever failed in the
# window". Alarming on history means an incident that can never clear while a
# week-old failure is still inside the window, and a service that recovered an
# hour ago keeps paging. The latest terminal run is the state of the world.
#
# IT NEVER WRITES A HYPOTHESIS. A machine reading an exit code has no theory
# about the cause, and putting a guess where a human looks for evidence is the
# thing the facts/hypotheses split exists to prevent. Facts only, each with the
# run that produced it as its source.

SEVERITY_BY_CRITICALITY = {
    "critical": "SEV-1",   # service unavailable
    "high":     "SEV-2",   # major workflow degraded
    "medium":   "SEV-3",   # contained, with a workaround
    "low":      "SEV-3",
}

# How long a recovered service is watched before a human may call it resolved.
# The doctrine requires a monitoring interval on resolution; this is the interval
# the machine proposes, never the resolution itself.
MONITORING_HOURS = 24


# THE CLOSE PATH, added 2026-08-14. Until this existed, nothing in the repo
# could resolve an incident: collectors opened them, `assess` moved a recovered
# one to monitoring and deliberately left resolved_at "for a human", and that
# human had no tool to act with — no verb (all 106 checked), no subcommand, no
# script. So the pile only ever grew, and the nightly assessment reprinted it
# whole every night. One of the entries was a DELIBERATE acceptance probe that
# could never clear on its own, because no green run for an induced failure is
# ever coming.
#
# Kept PURE and separate from the write so the guards can be tested without a
# database, the same reason mcp-server/src/trace.js exports its classifiers.
# The guards are the substance: a close path that rubber-stamps anything is
# worse than none, because then the pile LOOKS handled.
def resolve_preconditions(incident, root_cause, evidence=None,
                          allow_early=False, now=None):
    """(ok, error, fields_to_write). Decides whether one incident may close.

    `incident` is a mapping with ref/state/recovery_evidence_ref/monitoring_until.
    """
    now = now or datetime.now(timezone.utc)
    state = (incident.get("state") or "").strip()
    if state in ("resolved", "reviewed"):
        return False, f"{incident.get('ref')} is already {state}", {}

    if not (root_cause or "").strip():
        return False, ("a root cause is required — 'close with an outcome' means the "
                       "outcome is recorded, not that the row is cleared"), {}

    # Evidence: prefer what assess already recorded off a real green run; fall
    # back to what the caller supplies, which is the only option for an incident
    # that never recovered because nothing was ever broken.
    ref = incident.get("recovery_evidence_ref") or (evidence or "").strip() or None
    if not ref:
        return False, ("no recovery evidence on the incident and none supplied — pass "
                       "--evidence naming what shows it is safe to close"), {}

    until = incident.get("monitoring_until")
    if until is not None and until > now and not allow_early:
        return False, (f"still inside its monitoring window until {until:%Y-%m-%d %H:%M}Z — "
                       f"a green run says the symptom stopped, not that the cause is "
                       f"understood. Pass --allow-early with a reason if the window "
                       f"cannot apply."), {}

    # monitoring_until is NOT NULL under the resolved constraint. An incident
    # closed early, or one that never had a window, still needs a value: stamp
    # now, so the row says the watching ended here rather than implying a wait
    # that never happened.
    return True, None, {
        "recovery_evidence_ref": ref,
        "monitoring_until": until or now,
        "resolved_at": now,
        "root_cause": root_cause.strip(),
    }


def _next_incident_ref(cur) -> str:
    cur.execute(
        """select coalesce(max(substring(ref from '[0-9]+$')::int), 0) + 1
             from ops.incident
            where ref like 'INC-' || to_char(now(), 'YYYYMMDD') || '-%'""")
    return f"INC-{datetime.now(timezone.utc):%Y%m%d}-{cur.fetchone()[0]:02d}"


def assess(cur, environment: str | None = None, window_hours: int = 24) -> int:
    """Turn the latest run of every job into incident state. Returns how many
    incidents were OPENED (recoveries and appends are not openings)."""
    opened = 0

    cur.execute(
        """select distinct on (r.service_id, r.environment, r.run_key)
                  r.id, r.service_id, s.key, s.criticality, r.environment,
                  r.run_key, r.state, r.failure_class, r.correlation_id, r.detail
             from ops.run r
             join ops.service s on s.id = r.service_id
            where r.state in ('succeeded','failed','timed_out','cancelled','skipped')
              and r.observed_at > now() - make_interval(hours => %s)
              and (%s::text is null or r.environment = %s)
              and s.retired_at is null
         order by r.service_id, r.environment, r.run_key, r.observed_at desc""",
        (window_hours, environment, environment))
    latest = cur.fetchall()

    for (run_id, service_id_, service_key, criticality, env, run_key,
         state, failure_class, correlation_id, detail) in latest:

        if state in ("failed", "timed_out"):
            signature = f"{service_key}|{env}|{run_key}|{failure_class or ''}"
            cur.execute(
                """select id from ops.incident
                    where signature = %s and state not in ('resolved','reviewed')""",
                (signature,))
            row = cur.fetchone()

            if row is None:
                # A NEW incident. The unique index over open incidents is what
                # actually guarantees there is only ever one; this lookup is the
                # polite path to the same answer.
                cur.execute(
                    """insert into ops.incident
                           (ref, correlation_id, title, severity, state, environment,
                            owner_actor, next_action, detected_source, detected_at,
                            source_kind, source_ref, signature, observed_at, expires_at)
                       values (%s,%s,%s,%s,'detected',%s,'joe',%s,%s, now(),
                               'collector','tools/ops-record.py assess',%s, now(),
                               now() + make_interval(hours => %s))
                       returning id""",
                    (_next_incident_ref(cur), correlation_id,
                     f"{run_key} failed on {service_key} ({env})",
                     SEVERITY_BY_CRITICALITY.get(criticality, "SEV-3"), env,
                     f"read the trace: ops-record trace {correlation_id}",
                     f"job-run ledger: {run_key}",
                     signature, MONITORING_HOURS))
                incident_id = cur.fetchone()[0]
                opened += 1
            else:
                incident_id = row[0]

            # Link the run and record it as a FACT — but only once per run, or a
            # repeated assess would grow the fact list without new information.
            cur.execute(
                """insert into ops.incident_link (incident_id, kind, ref, note)
                   values (%s, 'run', %s, %s)
                   on conflict do nothing
                   returning incident_id""",
                (incident_id, str(run_id), run_key))
            if cur.fetchone() is not None:
                cur.execute(
                    """insert into ops.incident_fact (incident_id, text, source_ref)
                       values (%s, %s, %s)""",
                    (incident_id,
                     f"{run_key} on {service_key} ({env}) ended {state}"
                     + (f", failure class {failure_class}" if failure_class else "")
                     + (f" — {detail}" if detail else ""),
                     f"ops.run:{run_id}"))

        elif state == "succeeded":
            # RECOVERY IS NOT RESOLUTION. One green run says the symptom stopped,
            # not that the cause is understood — so this moves the incident to
            # monitoring with evidence and an interval, and leaves resolved_at
            # null for a human. The database would refuse the dishonest version
            # anyway; this does not try it.
            cur.execute(
                """update ops.incident
                      set state = 'monitoring',
                          recovery_evidence_ref = %s,
                          monitoring_until = now() + make_interval(hours => %s),
                          next_action = %s
                    where signature like %s
                      and state in ('detected','triaged','investigating','mitigating')""",
                (f"ops.run:{run_id}", MONITORING_HOURS,
                 f"watch until {MONITORING_HOURS}h clear, then close with an outcome",
                 f"{service_key}|{env}|{run_key}|%"))

        # skipped and cancelled raise nothing. exit 78 means a step ran, found
        # something it needs absent, wrote nothing and said so — alarming on that
        # fires every night until a credential lands, which is exactly how a
        # system teaches people to stop reading its alarms.

    return opened


# THE ELAPSED-WINDOW SWEEP, added 2026-08-14. Every incident carried the line
# "watch until 24h clear, then close with an outcome" and nothing ever performed
# that close: assess only moves a recovered incident INTO monitoring (its update
# targets detected/triaged/investigating/mitigating), so a row already there was
# never touched again, and no job, agent or service entry called the close path.
# The windows expired and the pile stayed, reprinted whole every night.
#
# ONLY THE CLOCK-WATCHING IS AUTOMATED — the judgment is not. This closes an
# incident only when nothing is left to decide: it recovered against real
# evidence, the window ran out, and nothing failed again for the whole window.
# Everything else — never recovered, still flapping, no evidence — stays open
# and keeps its human outcome, which is what the `resolve` subcommand is for.
# A failure that recurs mid-window is the case this must never close, because
# that is precisely the judgment the human close exists to make.
def sweep_decision(incident, job_clean, now=None):
    """(close, reason). Whether one monitoring incident may close on the clock.

    `job_clean` answers the question every incident's own next_action asks —
    "watch until 24h clear" — of the LAST 24 HOURS UP TO NOW: latest run for
    that signature succeeded, and no failed or timed-out run in the window.

    IT IS DELIBERATELY ANCHORED ON NOW, not on the recovery pointer. assess
    writes recovery_evidence_ref when it moves an incident detected -> monitoring
    and never updates it again, so a job that recovers, fails again and recovers
    once more still points at the FIRST recovery. Anchoring there counts long-
    healed failures forever and the incident never closes — which a read-only
    proof against the live ledger showed for three of the eight open rows before
    this shipped. Anchored on now, the test is self-correcting: whatever went
    wrong, 24 clean hours is 24 clean hours.
    """
    now = now or datetime.now(timezone.utc)
    if (incident.get("state") or "") != "monitoring":
        return False, f"state is {incident.get('state')!r}, not monitoring"

    if not incident.get("recovery_evidence_ref"):
        return False, "no recovery evidence recorded, so there is nothing to stand on"

    until = incident.get("monitoring_until")
    if until is None:
        # Never treat a missing window as an elapsed one — that would sweep
        # every incident that has no window at all.
        return False, "no monitoring window recorded"
    if until > now:
        return False, f"monitoring window still open until {until:%Y-%m-%d %H:%M}Z"

    if not job_clean:
        return False, (f"not yet {MONITORING_HOURS}h clear — a failure is still recorded "
                       f"inside the window, or the latest run is not green")

    return True, (f"its monitoring window ended {until:%Y-%m-%d %H:%M}Z and the job has run "
                  f"clean for a full {MONITORING_HOURS}h since — no failure recorded, latest "
                  f"run green. That is the watch every incident asks for. Closed by the "
                  f"elapsed-window sweep.")


def resolve_authority(env):
    """(ok, error). Closing needs owner privileges, and says so before connecting.

    THE DATABASE IS THE GATE, not this function. carr_jobs — the role every
    scheduled job runs as — holds a COLUMN-SCOPED update on ops.incident
    (state, next_action, monitoring_until, recovery_evidence_ref, observed_at,
    expires_at) and no grant at all on resolved_at or root_cause. So a machine
    can move an incident to monitoring and can never mark it closed, which is
    "closing an incident is a human's call" enforced in grants rather than in
    prose. Running this under the job role earns a bare `permission denied for
    table incident` with nothing saying why or what to do instead.
    """
    if not env.get("DATABASE_URL"):
        return False, (
            "closing an incident needs owner privileges, which the job role does not "
            "have: carr_jobs may write state and monitoring_until but has no grant on "
            "resolved_at or root_cause. Run it through the receipted break-glass path, "
            "which supplies the owner credential and logs why:\n\n"
            "  CARR_BREAK_GLASS=1 .venv/bin/python tools/db-tap.py --reason \"why\" \\\n"
            "    run tools/ops-record.py resolve --ref INC-... --root-cause \"...\"")
    return True, None


def cmd_resolve(args) -> int:
    ok, err = resolve_authority(os.environ)
    if not ok:
        print(err, file=sys.stderr)
        return 1
    with connect("owner") as conn, conn.cursor() as cur:
        cur.execute(
            """select ref, state, recovery_evidence_ref, monitoring_until
                 from ops.incident where ref = %s""", (args.ref,))
        row = cur.fetchone()
        if not row:
            print(f"no incident {args.ref}", file=sys.stderr)
            return 1
        incident = dict(zip(("ref", "state", "recovery_evidence_ref", "monitoring_until"), row))

        ok, err, fields = resolve_preconditions(
            incident, root_cause=args.root_cause, evidence=args.evidence,
            allow_early=bool(args.allow_early))
        if not ok:
            print(f"REFUSED — {err}", file=sys.stderr)
            return 1

        cur.execute(
            """update ops.incident
                  set state = 'resolved', resolved_at = %s, monitoring_until = %s,
                      recovery_evidence_ref = %s, root_cause = %s,
                      next_action = 'review and record a followup disposition'
                where ref = %s""",
            (fields["resolved_at"], fields["monitoring_until"],
             fields["recovery_evidence_ref"], fields["root_cause"], args.ref))
        # The reason an early close was allowed belongs ON the incident, not in
        # a shell history nobody reads back.
        if args.allow_early:
            cur.execute(
                """insert into ops.incident_fact (incident_id, text, source_ref)
                   select id, %s, %s from ops.incident where ref = %s""",
                (f"closed before its monitoring window elapsed: {args.allow_early}",
                 "ops-record.py resolve --allow-early", args.ref))
        conn.commit()
    print(f"{args.ref} resolved — {fields['root_cause']}")
    return 0


def cmd_sweep(args) -> int:
    ok, err = resolve_authority(os.environ)
    if not ok:
        print(err, file=sys.stderr)
        return 1
    closed = skipped = 0
    with connect("owner") as conn, conn.cursor() as cur:
        cur.execute(
            """select ref, state, recovery_evidence_ref, monitoring_until, title, signature
                 from ops.incident
                where state = 'monitoring'
                  and (%s::text is null or environment = %s)
             order by ref""",
            (args.environment, args.environment))
        cols = ("ref", "state", "recovery_evidence_ref", "monitoring_until", "title", "signature")
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]

        for inc in rows:
            # "Failed again during the window" is asked of the RUN LEDGER, not
            # of the incident: the incident is not re-opened by a repeat
            # failure under the same signature (0116's partial unique index
            # collapses it), so the incident alone cannot answer this.
            # "Watch until 24h clear", asked of the run ledger over the last 24
            # hours UP TO NOW — not of the incident, which is not re-opened by a
            # repeat failure under the same signature (0116's partial unique
            # index collapses it), and not of recovery_evidence_ref, which assess
            # never updates after the move into monitoring.
            sig = (inc.get("signature") or "").split("|")
            job_clean = False
            if len(sig) >= 3:
                cur.execute(
                    """select (select r.state from ops.run r
                                 join ops.service s on s.id = r.service_id
                                where s.key=%s and r.environment=%s and r.run_key=%s
                             order by r.observed_at desc limit 1) = 'succeeded'
                          and not exists (
                              select 1 from ops.run r
                                join ops.service s on s.id = r.service_id
                               where s.key=%s and r.environment=%s and r.run_key=%s
                                 and r.state in ('failed','timed_out')
                                 and r.observed_at > now() - make_interval(hours => %s))""",
                    (sig[0], sig[1], sig[2], sig[0], sig[1], sig[2], MONITORING_HOURS))
                job_clean = bool(cur.fetchone()[0])

            close, reason = sweep_decision(inc, job_clean=job_clean)
            if not close:
                skipped += 1
                if args.verbose:
                    print(f"  keep  {inc['ref']}  {reason}")
                continue

            ok2, err2, fields = resolve_preconditions(
                inc, root_cause=reason, evidence=inc["recovery_evidence_ref"])
            if not ok2:
                skipped += 1
                print(f"  keep  {inc['ref']}  {err2}")
                continue

            cur.execute(
                """update ops.incident
                      set state = 'resolved', resolved_at = %s, monitoring_until = %s,
                          recovery_evidence_ref = %s, root_cause = %s,
                          next_action = 'review and record a followup disposition'
                    where ref = %s and state = 'monitoring'""",
                (fields["resolved_at"], fields["monitoring_until"],
                 fields["recovery_evidence_ref"], fields["root_cause"], inc["ref"]))
            closed += 1
            print(f"  close {inc['ref']}  {inc['title']}")
        conn.commit()
    print(f"incident sweep: {closed} closed, {skipped} left open for a human")
    return 0


def cmd_assess(args) -> int:
    with connect("routine") as conn, conn.cursor() as cur:
        opened = assess(cur, environment=args.environment, window_hours=args.window_hours)
        conn.commit()
        cur.execute(
            """select ref, severity, state, title, next_action
                 from ops.incident
                where state not in ('resolved','reviewed')
                  and (%s::text is null or environment = %s)
             order by severity, detected_at""",
            (args.environment, args.environment))
        live = cur.fetchall()

    print(f"assess: {opened} incident(s) opened · {len(live)} live incident(s)")
    for ref, severity, state, title, next_action in live:
        print(f"  {severity}  {state:<12} {ref}  {title}")
        if next_action:
            print(f"        next: {next_action}")
    if not live:
        print("  nothing is broken that the ledger can see")
    return 0


# ── deployment ───────────────────────────────────────────────────────────────
def _validate_provider_identity(args, subject: str) -> bool:
    """Keep provider-version evidence exclusive to Production promotion.

    Staging is a source rehearsal.  Giving it the same immutable provider
    version identity as Production would collapse two different facts into one
    label, so non-Production writers refuse the fields instead of ignoring them.
    """
    provider = (getattr(args, "provider", None) or "").strip()
    version_id = (getattr(args, "provider_version_id", None) or "").strip()
    args.provider = provider or None
    args.provider_version_id = version_id or None
    if args.environment == "production":
        if not provider or not version_id:
            print(f"ops-record: Production {subject} requires --provider and "
                  "--provider-version-id", file=sys.stderr)
            return False
        if any(ch.isspace() for ch in provider + version_id):
            print("ops-record: provider identity may not contain whitespace",
                  file=sys.stderr)
            return False
        if provider == "cloudflare-workers":
            try:
                parsed_version = uuid.UUID(version_id)
            except ValueError:
                parsed_version = None
            if parsed_version is None or str(parsed_version) != version_id.lower():
                print("ops-record: cloudflare-workers provider version must be an "
                      "exact UUID", file=sys.stderr)
                return False
            args.provider_version_id = version_id.lower()
    elif provider or version_id:
        print("ops-record: --provider and --provider-version-id are only valid for "
              "Production; staging/rehearsal are source rehearsals, not the same "
              "provider version", file=sys.stderr)
        return False
    return True


def cmd_deployment(args) -> int:
    if not _validate_provider_identity(args, "deployment"):
        return 2
    if args.environment == "production" and not args.release_key:
        print("ops-record: Production deployment requires --release-key", file=sys.stderr)
        return 2
    if args.state == "complete" and not args.read_back_at:
        print("ops-record: complete requires --read-back-at. A successful deploy "
              "command without live verification is Verifying, never Complete.",
              file=sys.stderr)
        return 2
    corr = correlation_of(args.correlation)
    # A TERMINAL DEPLOYMENT HAS ENDED — 0115 refuses one that has not, and the
    # wrapper calling this knows the answer is "just now". Defaulting it here
    # keeps every caller from having to produce a portable timestamp, and the
    # explicit --ended-at still wins when a caller has a truer one.
    ended_at = args.ended_at
    if not ended_at and args.state in ("complete", "failed", "aborted",
                                       "rolled_back", "superseded"):
        ended_at = "now"
    try:
        with connect("write") as conn, conn.cursor() as cur:
            sid = service_id(cur, args.service)
            release_id = None
            if getattr(args, "release_key", None):
                cur.execute("select to_regclass('ops.release')")
                if cur.fetchone()[0] is not None:
                    cur.execute(
                        """select id, environment, git_sha, provider, provider_version_id
                             from ops.release where release_key = %s""",
                                (args.release_key,))
                    row = cur.fetchone()
                    if not row:
                        print(f"ops-record: no release {args.release_key!r}", file=sys.stderr)
                        if args.environment == "production":
                            return 2
                    else:
                        release_id = row[0]
                        if args.environment == "production":
                            _, release_env, release_sha, release_provider, release_version = row
                            if (release_env != args.environment
                                    or release_sha != args.git_sha
                                    or release_provider != args.provider
                                    or release_version != args.provider_version_id):
                                print("ops-record: Production deployment identity does not "
                                      "exactly match its release (environment, git SHA, "
                                      "provider, and provider version must all agree)",
                                      file=sys.stderr)
                                return 2
            cur.execute(
                """insert into ops.deployment
                       (correlation_id, service_id, environment, state, git_sha,
                        provider, provider_version_id, release_ref, release_id,
                        deployed_by_actor, verb_count,
                        schema_highest_migration, doctrine_generation,
                        started_at, ended_at, read_back_at, verification_evidence_ref,
                        failure_class, source_kind, source_ref, observed_at, detail)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now(), %s)
                   returning id""",
                (corr, sid, args.environment, args.state, args.git_sha,
                 args.provider, args.provider_version_id, args.release_ref, release_id,
                 args.actor, args.verb_count,
                 args.schema_migration, args.doctrine_generation,
                 parse_ts(args.started_at), parse_ts(ended_at),
                 parse_ts(args.read_back_at), args.verification_evidence_ref,
                 args.failure_class, args.source_kind, args.source_ref,
                 (args.detail or None)))
            dep_id = cur.fetchone()[0]
    except SystemExit:
        raise
    except Exception as e:                                   # noqa: BLE001
        print(f"ops-record: could not record deployment: "
              f"{str(e).splitlines()[0][:200]}", file=sys.stderr)
        return 1
    print(f"{corr} {dep_id}")
    return 0


# ── release ──────────────────────────────────────────────────────────────────
def cmd_release(args) -> int:
    """Record one release candidate from a manifest built by
    tools/release-manifest.py, or approve / read one back.

    ONE WRITER, still (rule a8c55a47). The manifest tool computes evidence and
    this puts it in the record; neither does the other's job, and there is no
    second path that writes ops.release.

    THE APPROVAL IS NOT HERE BY ACCIDENT. `approve` takes the plan hash the
    approver actually read and refuses if the manifest has moved since — the
    database would void it anyway (migration 0131's trigger), and catching it
    here means the human is told which field changed rather than watching an
    approval silently evaporate.
    """
    if args.action in ("candidate", "require"):
        if not _validate_provider_identity(args, f"release {args.action}"):
            return 2
    if args.action == "require":
        if args.environment != "production" and not args.sha:
            print("ops-record: release require needs --sha", file=sys.stderr)
            return 2
    elif not args.key:
        print(f"ops-record: release {args.action} needs --key", file=sys.stderr)
        return 2

    manifest = {}
    if getattr(args, "manifest", None):
        try:
            manifest = json.loads(Path(args.manifest).read_text())
        except Exception as e:                                   # noqa: BLE001
            print(f"ops-record: could not read the manifest: {e}", file=sys.stderr)
            return 2
    if args.action == "candidate" and args.environment == "production":
        manifest_target = (manifest.get("service"), manifest.get("environment"))
        requested_target = (args.service, args.environment)
        if manifest_target != requested_target:
            print("ops-record: Production candidate manifest service/environment "
                  "must exactly match the requested release target", file=sys.stderr)
            return 2
        manifest_identity = (manifest.get("provider"),
                             manifest.get("provider_version_id"))
        requested_identity = (args.provider, args.provider_version_id)
        if manifest_identity != requested_identity:
            print("ops-record: Production candidate provider/version must exactly "
                  "match the bound release manifest so the approval plan hash "
                  "covers the version that can be promoted", file=sys.stderr)
            return 2
        verified = subprocess.run(
            [sys.executable, str(REPO / "tools" / "release-manifest.py"),
             "verify", "--manifest", args.manifest],
            cwd=REPO, capture_output=True, text=True, check=False)
        if verified.returncode != 0:
            detail = (verified.stdout + verified.stderr).strip().splitlines()
            summary = detail[-1][:240] if detail else "verification returned no evidence"
            print("ops-record: Production candidate manifest verification failed "
                  f"before ledger intake: {summary}", file=sys.stderr)
            return 2

    try:
        with connect("write") as conn, conn.cursor() as cur:
            if args.action == "candidate":
                sid = service_id(cur, args.service)
                corr = correlation_of(getattr(args, "correlation", None))
                cur.execute(
                    """insert into ops.release
                           (correlation_id, release_key, service_id, environment,
                            state, git_sha, provider, provider_version_id,
                            artifact_digest, dependency_lock_digest,
                            sbom_ref, migration_set, schema_highest_migration,
                            config_fingerprint, declared_env_differences,
                            asset_versions, maker_actor, maker_verification_ref,
                            test_evidence_ref, security_evidence_ref,
                            rollback_ready, rollback_plan_ref, work_request_ref,
                            plan_hash, source_kind, source_ref, expires_at)
                       values (%s,%s,%s,%s,'candidate',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                               %s,%s,%s,%s,%s,%s,%s,%s,'wrapper',
                               'tools/release-manifest.py', %s)
                       returning id, release_key""",
                    (corr, args.key, sid, args.environment,
                     manifest.get("git_sha"), args.provider, args.provider_version_id,
                     manifest.get("artifact_digest"),
                     manifest.get("dependency_lock_digest"), manifest.get("sbom_ref"),
                     manifest.get("migration_set"),
                     manifest.get("schema_highest_migration"),
                     manifest.get("config_fingerprint"),
                     manifest.get("declared_env_differences"),
                     json.dumps(manifest.get("asset_versions")) if manifest.get("asset_versions") else None,
                     args.maker, args.maker_verification,
                     args.test_evidence, args.security_evidence,
                     args.rollback_ready, args.rollback_plan,
                     args.work_request, manifest.get("plan_hash"),
                     parse_ts(args.expires_at) if args.expires_at else None))
                row = cur.fetchone()
                print(f"{row[0]} {row[1]}")
                return 0

            if args.action == "require":
                # THE QUESTION A DEPLOY ASKS, one query: may this SHA ship?
                #
                # WHY THIS RETURNS 0 WHEN THE TABLE IS ABSENT. Migration 0131
                # carries the enforcement — the trigger that refuses a
                # production deployment naming an unapproved release. Where the
                # table does not exist, that control is not installed, and a
                # wrapper refusing on its behalf would be theatre: it would
                # claim a protection the database is not providing and could be
                # bypassed by any other deploy path. So it says so, loudly, on
                # every run, and the enforcement begins the moment 0131 applies.
                cur.execute("select to_regclass('ops.release')")
                if cur.fetchone()[0] is None:
                    if args.environment == "production":
                        print("RELEASE TRUTH IS NOT ENFORCED ON THIS DATABASE.\n"
                              "  Production provider-version promotion is refused "
                              "until ops.release exists.", file=sys.stderr)
                        return 3
                    print("RELEASE TRUTH IS NOT ENFORCED ON THIS DATABASE.\n"
                          "  ops.release does not exist, so migration 0131 has not "
                          "been applied here.\n"
                          "  This deploy will ship WITHOUT a release record, and "
                          "nothing will refuse it.\n"
                          "  Close it with: bin/migrate-prod.sh",
                          file=sys.stderr)
                    return 0

                if args.environment == "production":
                    cur.execute(
                        """select release_key, state, approval_expires_at, plan_hash,
                                  git_sha
                             from ops.release
                            where environment = %s
                              and provider = %s
                              and provider_version_id = %s
                              and (%s::text is null or git_sha = %s)
                              and state in ('approved','deploying','verifying')
                              and approval_expires_at > now()
                            order by approved_at desc
                            limit 1""",
                        (args.environment, args.provider, args.provider_version_id,
                         args.sha, args.sha))
                else:
                    cur.execute(
                        """select release_key, state, approval_expires_at, plan_hash,
                                  git_sha
                             from ops.release
                            where git_sha = %s and environment = %s
                              and state in ('approved','deploying','verifying')
                              and approval_expires_at > now()
                            order by approved_at desc
                            limit 1""",
                        (args.sha, args.environment))
                row = cur.fetchone()
                if not row:
                    release_identity = (args.sha[:12] if args.sha else
                                        f"{args.provider}:{args.provider_version_id}")
                    if args.environment == "production":
                        print(f"NO LIVE APPROVAL for {release_identity} in production.\n"
                              "  Record a candidate from the manifest bound to this "
                              "exact provider version, then have Joe approve that "
                              "bound plan hash:\n"
                              "    tools/ops-record.py release candidate --key <key> "
                              "--environment production "
                              f"--provider {args.provider} --provider-version-id "
                              f"{args.provider_version_id} --manifest out/bound.json\n"
                              "    tools/ops-record.py release approve --key <key> "
                              "--plan-hash <bound-hash> --actor joe",
                              file=sys.stderr)
                    else:
                        print(f"NO LIVE APPROVAL for {release_identity} in {args.environment}.\n"
                              "  Build the manifest, record the candidate, and have Joe "
                              "approve the plan hash it prints:\n"
                              f"    tools/release-manifest.py build --sha {args.sha} "
                              "> out/release.json\n"
                              "    tools/ops-record.py release candidate --key <key> "
                              "--manifest out/release.json\n"
                              "    tools/ops-record.py release approve --key <key> "
                              "--plan-hash <hash> --actor joe",
                              file=sys.stderr)
                    return 3
                key, state, expires, stored_plan, release_sha = row
                if args.plan_hash and args.plan_hash != stored_plan:
                    print(f"THE PLAN MOVED SINCE APPROVAL. Release {key} was approved "
                          f"against {stored_plan}; this tree builds {args.plan_hash}. "
                          f"Re-approve before shipping.", file=sys.stderr)
                    return 3
                if args.environment == "production":
                    # The promotion wrapper must get provenance from the approved
                    # immutable version, not from whichever checkout invokes it.
                    print(f"{key} {release_sha}")
                else:
                    print(key)
                return 0

            if args.action == "abandon":
                # A RELEASE THAT ENDS WITHOUT SHIPPING STILL HAS TO SAY WHY, and
                # the two ways it can end are genuinely different facts that 0131
                # already models separately:
                #   superseded  a NAMED later release replaced it, and
                #               a_superseded_release_names_its_successor forces
                #               the pointer, which is the useful half — collapsing
                #               it into `abandoned` loses that a successor exists.
                #   abandoned   nothing replaced it; it will simply never ship,
                #               and 0134 forces a reason so the row is not a
                #               terminal state nobody can explain.
                #
                # THIS IS A WAY TO END A RELEASE, NEVER A WAY TO ERASE ONE. The
                # state filter below refuses anything that reached deployment or
                # completion: a release that shipped is history, and letting it be
                # marked abandoned afterwards would write a real deploy out of the
                # ledger, which is the opposite of what P0-1 exists to do.
                # SUPERSEDED IS NOT AN OPTION HERE, and that is the schema's
                # ruling rather than a simplification. 0131's
                # an_approved_release_can_be_rebuilt and
                # an_approved_release_names_its_approval both exempt only
                # draft/candidate/abandoned, so reaching `superseded` requires a
                # full artifact digest, dependency lock, plan hash, approver and
                # expiry. That is a release which was APPROVED — and usually one
                # that shipped and was replaced by a later deploy. An unapproved
                # candidate overtaken before signing has none of that evidence and
                # is not superseded in the sense this table means; it is abandoned,
                # and its reason can name the successor in words.
                if not args.reason or len(args.reason.strip()) < 12:
                    print("ops-record: release abandon needs --reason (at least a "
                          "dozen characters). A terminal row nobody can explain is "
                          "the thing this action exists to prevent.", file=sys.stderr)
                    return 2
                cur.execute(
                    """update ops.release
                          set state = 'abandoned', abandoned_reason = %s,
                              ended_at = now()
                        where release_key = %s
                          and state in ('draft','candidate','approved')
                    returning state""",
                    (args.reason.strip(), args.key))
                row = cur.fetchone()
                if not row:
                    cur.execute("select state from ops.release where release_key = %s",
                                (args.key,))
                    existing = cur.fetchone()
                    if not existing:
                        print(f"ops-record: no release {args.key!r}", file=sys.stderr)
                    else:
                        print(f"ops-record: {args.key!r} is {existing[0]} and cannot be "
                              f"abandoned. Only a release that never shipped can be "
                              f"ended this way; one that deployed is history.",
                              file=sys.stderr)
                    return 2
                print(f"{args.key} {row[0]}")
                return 0

            if args.action == "complete":
                # THE LIFECYCLE HAS TO CLOSE, or a release sits at `approved`
                # forever while its deployment reads `complete`. Observed on the
                # first real release, 2026-08-16: bin/deploy-worker.sh recorded
                # the DEPLOYMENT complete and nothing advanced the RELEASE, so
                # the manifest view showed a deploy that had landed against a
                # release still waiting to ship. Two states of one fact
                # disagreeing is the fragmentation P0-1 exists to end.
                #
                # The read-back is NOT re-checked here on purpose: migration
                # 0131's trigger already refuses completion unless a deployment
                # attached to this release recorded one, and duplicating that
                # test in the wrapper would let the two drift apart. Failing
                # here means the trigger refused, and its message is the answer.
                cur.execute(
                    """update ops.release
                          set state = 'complete', ended_at = now(),
                              verifier_actor = coalesce(%s, verifier_actor),
                              verifier_evidence_ref = coalesce(%s, verifier_evidence_ref)
                        where release_key = %s
                          and state in ('approved','deploying','verifying')
                    returning state""",
                    (args.verifier, args.verifier_evidence, args.key))
                row = cur.fetchone()
                if not row:
                    print(f"ops-record: {args.key!r} is not in a state that can complete "
                          f"(already complete, or never approved)", file=sys.stderr)
                    return 2
                print(f"{args.key} {row[0]}")
                return 0

            if args.action == "approve":
                cur.execute(
                    "select plan_hash, state from ops.release where release_key = %s",
                    (args.key,))
                row = cur.fetchone()
                if not row:
                    print(f"ops-record: no release {args.key!r}", file=sys.stderr)
                    return 2
                stored_hash, state = row
                if args.plan_hash != stored_hash:
                    print(f"ops-record: the plan moved. You are approving "
                          f"{args.plan_hash}, the release carries {stored_hash}. "
                          f"Rebuild the manifest and re-read it before approving.",
                          file=sys.stderr)
                    return 2
                cur.execute(
                    """update ops.release
                          set state = 'approved', approved_by_actor = %s,
                              approved_at = now(),
                              approval_expires_at = now() + make_interval(hours => %s)
                        where release_key = %s
                    returning approval_expires_at""",
                    (args.actor, args.expires_hours, args.key))
                print(f"approved until {cur.fetchone()[0].isoformat()}")
                return 0

            # read one back — the manifest, in one query, as the gate asserts
            cur.execute(
                "select * from ops.v_release_manifest where release_key = %s",
                (args.key,))
            row = cur.fetchone()
            if not row:
                print(f"ops-record: no release {args.key!r}", file=sys.stderr)
                return 2
            cols = [d.name for d in cur.description]
            print(json.dumps(dict(zip(cols, row)), indent=2, default=str))
            return 0
    except SystemExit:
        raise
    except Exception as e:                                       # noqa: BLE001
        print(f"ops-record: could not record the release: "
              f"{str(e).splitlines()[0][:300]}", file=sys.stderr)
        return 1


# ── settings-change ──────────────────────────────────────────────────────────
def cmd_settings_change(args) -> int:
    """Record one control-plane change. Called by hooks/settings-change-gate.py at
    the moment of the change, never afterwards — the 2026-08-14 ruleset incident
    was an authorised change whose only account died with an interrupted
    session."""
    try:
        with connect("write") as conn, conn.cursor() as cur:
            cur.execute(
                """insert into ops.settings_change
                       (kind, target, reason, outcome, session_id, actor, command, environment)
                   values (%s,%s,%s,%s,%s,%s,%s,%s)
                   returning id""",
                (args.kind, args.target, args.reason, args.outcome, args.session,
                 args.actor, args.command, args.environment))
            row = cur.fetchone()
    except SystemExit:
        raise
    except Exception as e:                                   # noqa: BLE001
        # The change has ALREADY HAPPENED by the time this runs. Failing loudly
        # is right; failing in a way the caller treats as "so it did not happen"
        # is not. The gate spools locally on any non-zero exit.
        print(f"ops-record: could not record the settings change: "
              f"{str(e).splitlines()[0][:200]}", file=sys.stderr)
        return 1
    print(row[0] if row else "")
    return 0


# ── trace ────────────────────────────────────────────────────────────────────
def cmd_trace(args) -> int:
    try:
        corr = str(uuid.UUID(args.correlation))
    except ValueError:
        raise SystemExit(f"ops-record: not a uuid: {args.correlation!r}")

    with connect("read") as conn, conn.cursor() as cur:
        cur.execute(
            """select kind, ref, state, occurred_at, environment, service_key,
                      failure_class, detail, source_kind, source_ref, freshness_state
                 from ops.v_trace
                where correlation_id = %s
             order by occurred_at""",
            (corr,))
        rows = cur.fetchall()

    if not rows:
        print(f"no trace for {corr}")
        return 1

    print(f"trace {corr}   ({len(rows)} link(s))\n")
    for kind, ref, state, at, env, svc, fclass, detail, skind, sref, fresh in rows:
        stamp = at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ") if at else "no time"
        head = f"  {stamp}  {kind:<12} {state:<12} {ref}"
        print(head)
        line2 = f"      {env or 'no environment'}"
        if svc:
            line2 += f" · {svc}"
        line2 += f" · via {skind}:{sref} · {fresh}"
        print(line2)
        if fclass:
            print(f"      failure class: {fclass}")
        if detail:
            print(f"      {detail}")
    return 0


# ── health ───────────────────────────────────────────────────────────────────
def cmd_health(args) -> int:
    with connect("read") as conn, conn.cursor() as cur:
        cur.execute(
            """select service_key, environment, health, freshness_state,
                      last_run_state, last_failure_class, observed_at, criticality
                 from ops.v_service_environment_health
             order by case health when 'unavailable' then 0 when 'degraded' then 1
                                  when 'unknown' then 2 else 3 end,
                      case criticality when 'critical' then 0 when 'high' then 1
                                       when 'medium' then 2 else 3 end,
                      service_key""")
        rows = cur.fetchall()

    if not rows:
        print("no registered service/environment rows — run sync-registry first")
        return 1

    worst = 0
    for key, env, health, fresh, last_state, fclass, observed, crit in rows:
        mark = {"healthy": "ok  ", "degraded": "WARN", "unavailable": "DOWN",
                "unknown": "?   "}.get(health, "?   ")
        if health in ("unavailable",):
            worst = max(worst, 2)
        elif health in ("degraded", "unknown"):
            worst = max(worst, 1)
        seen = observed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ") if observed else "never"
        line = f"{mark} {key:<24} {env:<11} {health:<12} {fresh:<8} last seen {seen}"
        if last_state and last_state != "succeeded":
            line += f"  [{last_state}{'/' + fclass if fclass else ''}]"
        print(line)
    print(f"\n{len(rows)} registered service/environment row(s). "
          f"`unknown` means nobody has observed it inside its cadence — not that it is well.")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("sync-registry", help="apply ops/config/services.json into the catalog")

    r = sub.add_parser("run", help="append one job or check run")
    r.add_argument("--service", required=True)
    r.add_argument("--key", required=True, help="run_key, e.g. nightly.exports")
    r.add_argument("--state", required=True,
                   choices=["scheduled", "queued", "running", "succeeded", "failed",
                            "timed_out", "cancelled", "skipped", "stale", "unknown"])
    r.add_argument("--kind", default="job", choices=["job", "check"])
    r.add_argument("--environment", default="production",
                   choices=["local", "rehearsal", "staging", "production"])
    r.add_argument("--failure-class")
    r.add_argument("--exit-code", type=int)
    r.add_argument("--attempt", type=int, default=1)
    r.add_argument("--started-at")
    r.add_argument("--ended-at")
    r.add_argument("--correlation")
    r.add_argument("--source-kind", default="wrapper",
                   choices=["collector", "registry", "wrapper", "operator"])
    r.add_argument("--source-ref", required=True, help="e.g. bin/nightly.sh")
    r.add_argument("--expires-in", type=int,
                   help="seconds this observation stays believable; omit to fall back "
                        "to the environment's registered cadence")
    r.add_argument("--evidence-ref")
    r.add_argument("--detail", help="ONE redacted line — no secrets, no client content")

    d = sub.add_parser("deployment", help="append one deployment marker")
    d.add_argument("--service", required=True)
    d.add_argument("--environment", required=True,
                   choices=["local", "rehearsal", "staging", "production"])
    d.add_argument("--state", required=True,
                   choices=["planned", "rehearsing", "ready", "awaiting_approval",
                            "deploying", "verifying", "complete", "failed", "aborted",
                            "rolled_back", "superseded"])
    d.add_argument("--git-sha")
    d.add_argument("--provider", help="Production provider, e.g. cloudflare-workers")
    d.add_argument("--provider-version-id", dest="provider_version_id",
                   help="immutable Production provider version actually promoted")
    d.add_argument("--release-ref", help="SUPERSEDED by --release-key (0131); kept "
                                         "so nothing that wrote it breaks")
    d.add_argument("--release-key", help="the release this deploy is shipping, by key. "
                                         "Resolved to ops.release.id, which is the "
                                         "edge that makes a deploy traceable to an "
                                         "approved plan.")
    d.add_argument("--actor")
    d.add_argument("--verb-count", type=int)
    d.add_argument("--schema-migration")
    d.add_argument("--doctrine-generation", type=int)
    d.add_argument("--started-at")
    d.add_argument("--ended-at")
    d.add_argument("--read-back-at", help="when production was read back; required for complete")
    d.add_argument("--verification-evidence-ref")
    d.add_argument("--failure-class")
    d.add_argument("--correlation")
    d.add_argument("--source-kind", default="wrapper",
                   choices=["collector", "registry", "wrapper", "operator"])
    d.add_argument("--source-ref", default="bin/deploy-worker.sh")
    d.add_argument("--detail")

    sc = sub.add_parser("settings-change", help="record one control-plane change")
    sc.add_argument("--kind", required=True)
    sc.add_argument("--target", required=True)
    sc.add_argument("--reason", required=True)
    sc.add_argument("--outcome", required=True, choices=["applied", "failed"])
    sc.add_argument("--session", required=True)
    sc.add_argument("--actor")
    sc.add_argument("--command")
    sc.add_argument("--environment",
                    choices=["local", "rehearsal", "staging", "production"],
                    default="production")

    rel = sub.add_parser("release", help="record, approve or read one release (P0-1)")
    rel.add_argument("action", choices=["candidate", "approve", "require", "complete",
                                        "abandon", "show"])
    rel.add_argument("--sha", help="require only: the SHA about to ship")
    rel.add_argument("--provider", help="Production provider, e.g. cloudflare-workers")
    rel.add_argument("--provider-version-id", dest="provider_version_id",
                     help="immutable Production provider version bound to this release")
    rel.add_argument("--key", help="the release key, e.g. r-2026-08-15-01. Required "
                                   "for every action except `require`, which asks "
                                   "about a SHA rather than a named release.")
    rel.add_argument("--manifest", help="JSON from tools/release-manifest.py build")
    rel.add_argument("--service", default="carr-mcp")
    rel.add_argument("--environment",
                     choices=["local", "rehearsal", "staging", "production"],
                     default="production")
    rel.add_argument("--correlation")
    rel.add_argument("--maker", default=os.environ.get("CARR_ACTOR", "claude"))
    rel.add_argument("--maker-verification", help="ref to the maker's own evidence")
    rel.add_argument("--test-evidence", help="ref to the test run, e.g. ops/ci.sh#<run>")
    rel.add_argument("--security-evidence", help="ref to the security/scan run")
    rel.add_argument("--rollback-ready", action="store_true")
    rel.add_argument("--rollback-plan", help="ref to the rollback runbook")
    rel.add_argument("--work-request", help="the Work Request this release delivers")
    rel.add_argument("--expires-at", help="when this candidate's evidence goes stale")
    rel.add_argument("--plan-hash", help="approve only: the hash the approver read")
    rel.add_argument("--actor", help="approve only: who is approving")
    rel.add_argument("--verifier", help="complete only: who verified, and it may not be the maker")
    rel.add_argument("--verifier-evidence", dest="verifier_evidence",
                     help="complete only: ref to the verification that closed it")
    rel.add_argument("--reason", help="abandon only: why this release will never "
                                      "ship. Required unless --superseded-by names "
                                      "the release that replaced it.")
    rel.add_argument("--expires-hours", type=int, default=24,
                     help="approve only: how long the approval stays live. An "
                          "approval that never expires is how a plan-hash check "
                          "gets bypassed by time.")

    t = sub.add_parser("trace", help="read one correlation id back as a chain")
    t.add_argument("correlation")

    sub.add_parser("health", help="derived health of every registered service")

    rs = sub.add_parser("resolve", help="close one incident, with its outcome recorded")
    rs.add_argument("--ref", required=True, help="e.g. INC-20260814-09")
    rs.add_argument("--root-cause", required=True,
                    help="what actually happened — recorded on the incident")
    rs.add_argument("--evidence",
                    help="what shows it is safe to close; required only when the "
                         "incident carries no recovery evidence of its own")
    rs.add_argument("--allow-early", metavar="REASON",
                    help="close before the monitoring window elapses, stating why "
                         "the window cannot apply (e.g. an induced failure that will "
                         "never produce a green run). Recorded as an incident fact.")

    sw = sub.add_parser("sweep", help="close monitoring incidents whose window elapsed clean")
    sw.add_argument("--environment",
                    choices=["local", "rehearsal", "staging", "production"])
    sw.add_argument("--verbose", action="store_true",
                    help="also print why each incident was left open")

    a = sub.add_parser("assess", help="turn the latest run of every job into incident state")
    a.add_argument("--environment",
                   choices=["local", "rehearsal", "staging", "production"])
    a.add_argument("--window-hours", type=int, default=24,
                   help="how far back to look for each job's latest run")

    args = p.parse_args()
    return {
        "sync-registry": cmd_sync_registry,
        "run": cmd_run,
        "deployment": cmd_deployment,
        "release": cmd_release,
        "trace": cmd_trace,
        "health": cmd_health,
        "assess": cmd_assess,
        "resolve": cmd_resolve,
        "sweep": cmd_sweep,
        "settings-change": cmd_settings_change,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
