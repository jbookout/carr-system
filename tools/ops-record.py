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

CREDENTIALS. run and deployment prefer CARR_DB_JOBS_URL — the carr_jobs role,
which holds INSERT and no UPDATE on ops.run, because a ledger whose writer can
rewrite history is not a ledger. Reads prefer the exporter credential. Registry
sync needs the owner and is meant to be run through tools/db-tap.py.

  bin/nightly.sh                                   (records every step)
  .venv/bin/python tools/db-tap.py run tools/ops-record.py sync-registry
  .venv/bin/python tools/ops-record.py trace <correlation-id>
  .venv/bin/python tools/ops-record.py health
"""

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REGISTRY = REPO / "ops" / "config" / "services.json"

# Ordered credential preference per mode. The first name that is set wins, so a
# db-tap invocation (which sets DATABASE_URL) always overrides the ambient
# job credential — that is what makes `db-tap run` a deliberate escalation
# rather than an accident.
DSN_FOR = {
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
            return value
    raise SystemExit(
        f"ops-record: no credential — set one of {', '.join(DSN_FOR[kind])} "
        f"(they live in ~/.config/carr/db.env)")


def connect(kind: str):
    try:
        import psycopg
    except ImportError:
        raise SystemExit("ops-record: psycopg not installed (pip install 'psycopg[binary]')")
    return psycopg.connect(dsn(kind), autocommit=True)


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
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
        with connect("write") as conn, conn.cursor() as cur:
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


def cmd_assess(args) -> int:
    with connect("write") as conn, conn.cursor() as cur:
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
def cmd_deployment(args) -> int:
    if args.state == "complete" and not args.read_back_at:
        print("ops-record: complete requires --read-back-at. A successful deploy "
              "command without live verification is Verifying, never Complete.",
              file=sys.stderr)
        return 2
    corr = correlation_of(args.correlation)
    try:
        with connect("write") as conn, conn.cursor() as cur:
            sid = service_id(cur, args.service)
            cur.execute(
                """insert into ops.deployment
                       (correlation_id, service_id, environment, state, git_sha,
                        release_ref, deployed_by_actor, verb_count,
                        schema_highest_migration, doctrine_generation,
                        started_at, ended_at, read_back_at, verification_evidence_ref,
                        failure_class, source_kind, source_ref, observed_at, detail)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now(), %s)
                   returning id""",
                (corr, sid, args.environment, args.state, args.git_sha,
                 args.release_ref, args.actor, args.verb_count,
                 args.schema_migration, args.doctrine_generation,
                 parse_ts(args.started_at), parse_ts(args.ended_at),
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
    d.add_argument("--release-ref")
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
        "trace": cmd_trace,
        "health": cmd_health,
        "assess": cmd_assess,
        "resolve": cmd_resolve,
        "settings-change": cmd_settings_change,
    }[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
