#!/usr/bin/env python3
"""
health-check.py — the rule-28 façade check as code (phase 3 pilot, 2026-07-24).

Protocol rule 28 (DNA/Team/dna-protocol.md): an automation is verified by OUTPUT
freshness, never by its schedule existing. This script checks every watched
pipeline output two ways:
  STALE  — the output is older than its cadence allows
  BEHIND — a derived view is older than one of its inputs (the picture lies)
Read-only. Exit 0 = all fresh; exit 1 = at least one STALE/BEHIND (so the
heartbeat can surface findings). Run: python3 tools/health-check.py  (or run.sh health)
Cadences are calendar days, padded so weekends (off days, both humans) never
false-alarm a weekly pipeline.

Second mode, added 2026-08-02:
  python3 tools/health-check.py --tasks <list_scheduled_tasks.json>
classifies scheduled tasks by whether a firing window has actually PASSED, so a
brand-new task is never mistaken for a broken one. See the scheduler section below.
"""
import json, os, sys, glob, time, re, subprocess, calendar
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import health_submodule as _health_sub

# Script-relative, NOT expanduser("~/carr-system") — same fix as commit fad87a4
# (tests) and c4d040d (gates). This is the ONLY caller of ops/renders-verify.py,
# so fixing that script while its caller still resolved REPO_ROOT through $HOME
# would have left the render-tamper check dead on any clone outside $HOME.
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

DEFAULT_RECOVERY_VAULT = (
    "/Users/booko/Library/CloudStorage/"
    "GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI"
)

# ── the active-rule-gap acceptance ──────────────────────────────────────────
# A PERMANENTLY CHOSEN STATE MUST NOT READ AS A PERMANENT FAILURE — rule
# bd4a6d22, which this check was breaking. It counted every active rule whose
# enforcement_status was not exactly 'hard_enforced', so it flagged two rules
# that are working as designed and left a ⚠︎ nobody could ever clear.
#
# Two separate corrections, and they are different mistakes:
#
# 1. 'authority_enforced' IS enforced. migrations/0194_atomic_rule_approval.sql
#    allows exactly three values — hard_enforced, authority_enforced, blocked —
#    and treating the second as a gap miscounts a rule that has real enforcement
#    behind it, just not a local one.
#
# 2. A judgment rule cannot be hard-enforced, and saying so is a DECISION, not a
#    gap. ops/config/rule-enforcement-map.json records these with a written
#    why_unenforceable — for bd4a6d22 itself: "No control can judge whether a
#    human chose a state on purpose." 'blocked' is also simply the column
#    default from 0194, so an unset row and a deliberate one looked identical.
#
# THE ACCEPTANCE IS NAMED, NARROW, AND STILL PRINTS. Only this one class is
# accepted; every other class with an unenforced status still counts and still
# fails. The accepted count is printed on the passing line rather than dropped,
# so the state stays visible instead of becoming silence. Widening either
# constant is what ops/health-rule-gap-selftest.py exists to catch.
RULE_ENFORCED_STATES = ("hard_enforced", "authority_enforced")
RULE_UNENFORCEABLE_CLASS = "judgment_advisory"


def _reader_args(argv):
    """Return the Drive boundary without letting normal mode inherit a vault."""
    recovery = False
    reason = ""
    vault = None
    section = "all"
    fixture = None
    rest = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--recovery":
            recovery = True
        elif arg in ("--reason", "--vault", "--section", "--fixture"):
            if i + 1 >= len(argv):
                raise SystemExit(f"health-check: {arg} requires a value")
            value = argv[i + 1]
            i += 1
            if arg == "--reason":
                reason = value
            elif arg == "--vault":
                vault = value
            elif arg == "--section":
                section = value
            else:
                fixture = value
        else:
            rest.append(arg)
        i += 1
    if vault and not recovery:
        raise SystemExit("health-check: --vault is recovery-only; pass --recovery")
    if recovery:
        reason = (reason or os.environ.get("CARR_RECOVERY_REASON", "")).strip()
        if not reason:
            raise SystemExit("health-check: --recovery requires a nonblank --reason")
        vault = vault or os.environ.get("CARR_VAULT") or DEFAULT_RECOVERY_VAULT
    else:
        # A parent shell may carry this old ambient variable.  Normal health must
        # not pass it to any child or let a child silently choose a Drive reader.
        os.environ.pop("CARR_VAULT", None)
    if section not in ("all", "exports", "jobs", "registry"):
        raise SystemExit("health-check: --section must be all|exports|jobs|registry")
    if fixture and recovery:
        raise SystemExit("health-check: --fixture is for hermetic canonical tests only")
    return recovery, reason, vault, section, fixture, rest


RECOVERY_MODE, RECOVERY_REASON, VAULT, CANONICAL_SECTION, CANONICAL_FIXTURE, _READER_REST = \
    _reader_args(sys.argv[1:])
sys.argv[1:] = _READER_REST

# ── scheduler register (added 2026-08-02) ────────────────────────────────────
# A TASK THAT HAS NEVER REACHED ITS FIRST WINDOW LOOKS EXACTLY LIKE A TASK THAT IS
# FAILING. Both show an absent `lastRunAt`. On 2026-08-02 that cost two readers in a
# row: an audit reported "health-audit-monthly has never run" and "contact-enrichment-
# weekly has never run" as defects, and neither was one — all four never-run tasks were
# created AFTER their most recent firing window, so their first window is still ahead.
# Same principle as GATED-vs-MISSING above: an alarm that fires on healthy state gets
# ignored by the time it means something.
#
# WHAT THIS CAN AND CANNOT SEE, stated rather than assumed. The scheduler's cron
# expressions and `lastRunAt` live in the app's own store, NOT on disk — the task
# directories under ~/.claude/scheduled-tasks hold only a SKILL.md. So this cannot run
# unattended. It is a classifier you feed: paste the output of the `list_scheduled_tasks`
# MCP tool into a file and run
#
#     python3 tools/health-check.py --tasks /tmp/tasks.json
#
# and every task comes back as one of
#   OK             a window passed and lastRunAt is at or after it
#   AWAITING FIRST no window has passed since the task was created — expected, not a fault
#   MISSED         a window passed with no run since; the count of missed windows is shown
#   DISABLED       enabled:false, reported and never counted as a fault
#
# NO SPECIAL WEEKEND RULE, on purpose. Joe's weekends-are-off rule is already encoded in
# the crons themselves (`* * 1-5`), so evaluating the real cron gets Saturday and Sunday
# right for free. A separate weekend heuristic layered on top would be a second, weaker
# copy of a rule the data already carries, and the two would eventually disagree.

TASK_DIRS = os.path.expanduser("~/.claude/scheduled-tasks")


def _cron_field(spec, lo, hi):
    """One cron field -> the set of values it matches. Handles * , - and /."""
    out = set()
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, _, s = part.partition("/")
            step = int(s)
        if part in ("*", "?"):
            start, end = lo, hi
        elif "-" in part.lstrip("-"):
            a, _, b = part.partition("-")
            start, end = int(a), int(b)
        else:
            start = end = int(part)
        out.update(range(start, end + 1, step))
    return out


def cron_windows(expr, start, end):
    """Every firing time of a 5-field cron in (start, end]. Minute-exact, day-stepped."""
    m, h, dom, mon, dow = expr.split()
    mins = sorted(_cron_field(m, 0, 59))
    hrs = sorted(_cron_field(h, 0, 23))
    doms = _cron_field(dom, 1, 31)
    mons = _cron_field(mon, 1, 12)
    dows = {d % 7 for d in _cron_field(dow, 0, 7)}
    dom_restricted = dom not in ("*", "?")
    dow_restricted = dow not in ("*", "?")
    hits = []
    day = start.replace(hour=0, minute=0, second=0, microsecond=0)
    while day <= end:
        if day.month in mons:
            # Vixie cron: with BOTH day-of-month and day-of-week restricted, either
            # matching fires. Getting this backwards silently drops or invents windows.
            d_ok = day.day in doms
            w_ok = ((day.weekday() + 1) % 7) in dows
            fires = (d_ok or w_ok) if (dom_restricted and dow_restricted) \
                else (d_ok if dom_restricted else (w_ok if dow_restricted else True))
            if fires:
                for hh in hrs:
                    for mm in mins:
                        t = day.replace(hour=hh, minute=mm)
                        if start < t <= end:
                            hits.append(t)
        day += timedelta(days=1)
    return hits


def _task_created(task_id, path=None):
    """Birth time of the task's own directory. The only creation signal on disk."""
    d = os.path.dirname(path) if path else os.path.join(TASK_DIRS, task_id)
    try:
        st = os.stat(d)
        return datetime.fromtimestamp(getattr(st, "st_birthtime", st.st_mtime))
    except OSError:
        return None


def classify_tasks(path):
    """Read list_scheduled_tasks JSON; print a per-task verdict. Returns an exit code."""
    with open(path) as fh:
        tasks = json.load(fh)
    if isinstance(tasks, dict):
        tasks = tasks.get("tasks", [])
    now = datetime.now()
    bad = 0
    print(f"Scheduler register — {now:%Y-%m-%d %H:%M} — has a window actually PASSED?")
    for t in sorted(tasks, key=lambda x: x.get("taskId", "")):
        tid = t.get("taskId", "?")
        expr = t.get("cronExpression")
        born = _task_created(tid, t.get("path"))
        last = t.get("lastRunAt")
        if not t.get("enabled", True):
            print(f"  -- DISABLED       {tid}")
            continue
        if not expr:
            # A one-time fireAt task, or a shape this classifier does not model. Say so
            # rather than guessing — a wrong verdict here is worse than no verdict.
            print(f"  ?? NO CRON        {tid} (fireAt={t.get('fireAt') or 'none'}) — not classified")
            continue
        if born is None:
            print(f"  ?? NO DIR         {tid} — no task directory on disk, cannot date it")
            continue
        lastdt = None
        if last:
            try:
                lastdt = datetime.fromisoformat(last.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
            except ValueError:
                lastdt = None
        # Windows since the task existed. A window before its creation could never
        # have fired it and must not be counted against it.
        wins = cron_windows(expr, born, now)
        if not wins:
            nxt = cron_windows(expr, now, now + timedelta(days=400))
            when = f"{nxt[0]:%Y-%m-%d %H:%M}" if nxt else "none within a year"
            print(f"  -- AWAITING FIRST {tid:<30} created {born:%Y-%m-%d}, cron `{expr}` — "
                  f"no window has passed yet; first is {when}. Absent lastRunAt is CORRECT here")
            continue
        missed = [w for w in wins if not (lastdt and lastdt >= w - timedelta(hours=1))]
        if not missed:
            print(f"  OK {tid:<33} last run {lastdt:%Y-%m-%d %H:%M}, most recent window "
                  f"{wins[-1]:%Y-%m-%d %H:%M}")
        else:
            _tail = (f"last run {lastdt:%Y-%m-%d %H:%M}" if lastdt
                     else f"it has NEVER run and was created {born:%Y-%m-%d}")
            print(f"  ⚠︎ MISSED         {tid:<30} {len(missed)} window(s) passed with no run; "
                  f"latest missed {missed[-1]:%Y-%m-%d %H:%M}; {_tail}")
            bad = 1
    return bad


if "--tasks" in sys.argv:
    _i = sys.argv.index("--tasks")
    if _i + 1 >= len(sys.argv):
        sys.exit("usage: health-check.py --tasks <list_scheduled_tasks.json>")
    sys.exit(classify_tasks(sys.argv[_i + 1]))


def _canonical_snapshot():
    """Read canonical database/control-plane evidence, never a Drive render."""
    if CANONICAL_FIXTURE:
        with open(CANONICAL_FIXTURE, encoding="utf-8") as fh:
            value = json.load(fh)
        if not isinstance(value, dict):
            raise ValueError("canonical health fixture must be an object")
        return value

    snapshot = {"exports": None, "job_definitions": None, "jobs": None,
                "controls": None, "errors": []}
    if CANONICAL_SECTION in ("all", "exports"):
        # THE CUTOFF (fired 2026-08-19) IS CARRIED HERE TOO. A retired .md target
        # stops producing export_run rows at all, so 26 hours later every one of
        # them trips the STALE branch below — 88 amber lines on this machine,
        # prescribing a re-export of targets the exporter now refuses to write.
        # A state chosen on purpose was reading as a permanent failure, which is
        # the thing rule bd4a6d22 forbids and the same defect this file already
        # carried for active rule gaps.
        #
        # The register further down this file (the "a target nobody registered is
        # not a target that failed" block) already had this right. It is a
        # different section and does not run in a normal `run.sh health`, so its
        # correctness never reached the line Joe and Dell actually read. Same
        # flag function the exporter itself gates on — one contract, no second
        # opinion, and no second place to decide what "retired" means.
        probe = r'''
import json
from exporters.targets import TARGETS
from exporters.common import connect
from exporters.run_exports import md_renders_retired
retired = sorted(k for k, (rel, _fn) in TARGETS.items()
                 if rel.lower().endswith(".md")) if md_renders_retired() else []
with connect() as c, c.cursor() as cur:
    cur.execute("""select target,
                          max(ran_at) filter (where status='ok'),
                          (array_agg(status order by ran_at desc))[1]
                     from export_run group by target""")
    rows = [{"target": t, "last_ok": x.isoformat() if x else None,
             "latest_status": s} for t, x, s in cur.fetchall()]
print(json.dumps({"registered": sorted(TARGETS), "rows": rows, "retired": retired}))
'''
        _venv_python = os.path.join(REPO_ROOT, ".venv/bin/python")
        _query_python = _venv_python if os.path.exists(_venv_python) else sys.executable
        p = subprocess.run(
            [_query_python, "-c", probe],
            cwd=REPO_ROOT, text=True, capture_output=True, timeout=120,
            env={k: v for k, v in os.environ.items() if k != "CARR_VAULT"},
        )
        if p.returncode:
            snapshot["errors"].append("exports: " + ((p.stderr or "").strip() or "query failed"))
        else:
            try:
                snapshot["exports"] = json.loads(p.stdout.strip().splitlines()[-1])
            except (ValueError, IndexError) as exc:
                snapshot["errors"].append(f"exports: unparseable result ({exc})")

    if CANONICAL_SECTION in ("all", "jobs"):
        # v_job_control is the durable provider-independent job ledger.  The
        # SQL is intentionally here (rather than a projection parser) so a test
        # can prove exactly which authority surface health reads.
        sql = """select 'DEF',d.key,d.version,d.recurrence::text,d.registered_at,
                        coalesce(d.legacy_disabled_at::text,'')
                   from ops.job_definition d where d.enabled order by d.key;
                 select 'JOB',v.id,v.definition_key,v.definition_version,v.state,v.mode,
                        v.attempt,v.max_attempts,v.scheduled_for,v.created_at,
                        v.started_at,v.ended_at,v.next_attempt_at,v.leased_until,
                        j.timeout_seconds,
                        (select count(*) from ops.job_receipt r
                          where r.job_id=v.id and r.attempt=v.attempt
                            and r.kind='completion') as completion_receipt_count
                   from ops.v_job_control v join ops.job j on j.id=v.id
                   where v.created_at > now() - interval '40 days' and v.mode='live'
                  order by v.created_at desc"""
        _venv_python = os.path.join(REPO_ROOT, ".venv/bin/python")
        _query_python = _venv_python if os.path.exists(_venv_python) else sys.executable
        p = subprocess.run(
            [_query_python,
             os.path.join(REPO_ROOT, "tools/db-tap.py"), "sql", "/dev/stdin"],
            input=sql, cwd=REPO_ROOT, text=True, capture_output=True, timeout=120,
            env={k: v for k, v in os.environ.items() if k != "CARR_VAULT"},
        )
        if p.returncode:
            snapshot["errors"].append("jobs: " + ((p.stderr or "").strip() or "query failed"))
        else:
            definitions = []
            rows = []
            for line in p.stdout.splitlines():
                cols = [c.strip() for c in line.split("|")]
                if len(cols) == 6 and cols[0] == "DEF":
                    try:
                        recurrence = json.loads(cols[3])
                    except ValueError:
                        recurrence = None
                    definitions.append({"key": cols[1], "version": int(cols[2]),
                                        "recurrence": recurrence,
                                        "registered_at": cols[4],
                                        # The control plane is the executor of
                                        # record only once the legacy schedule
                                        # is disabled; until then its ledger is
                                        # SILENT for this key by design, and
                                        # afterwards it is the authority only
                                        # from that instant forward.
                                        "legacy_disabled_at": cols[5] or None,
                                        "legacy_live": not cols[5]})
                elif len(cols) == 16 and cols[0] == "JOB" and cols[4] in {
                    "queued", "running", "retry_wait", "waiting_approval", "succeeded",
                    "failed", "timed_out", "dead_lettered", "cancelled", "skipped",
                }:
                    rows.append({
                        "id": cols[1], "definition_key": cols[2],
                        "definition_version": int(cols[3]), "state": cols[4],
                        "mode": cols[5], "attempt": int(cols[6]),
                        "max_attempts": int(cols[7]), "scheduled_for": cols[8],
                        "created_at": cols[9], "started_at": cols[10],
                        "ended_at": cols[11], "next_attempt_at": cols[12],
                        "leased_until": cols[13], "timeout_seconds": int(cols[14]),
                        "completion_receipt_count": int(cols[15]),
                    })
            snapshot["job_definitions"] = definitions
            snapshot["jobs"] = rows
    if CANONICAL_SECTION == "all":
        # Built from the named constants rather than spelled inline, so the
        # acceptance and the query can never drift apart. Both values are fixed
        # identifiers defined in this file, never caller input.
        _enforced = "', '".join(RULE_ENFORCED_STATES)
        sql = f"""select
          (select count(*) from doctrine_gate_run
            where result='fail' and dry_run=false
              and started_at > now() - interval '24 hours'),
          (select count(*) from doctrine_section s join doctrine_document d
             on d.id=s.document_id join doctrine_review_policy p
             on p.id=d.review_policy_id
            where s.status='active' and p.max_age_days is not null
              and s.review_after is null),
          (select count(*) from doctrine_section
            where status='active' and review_after < now()),
          (select count(*) from ops.v_rule_enforcement_status
            where policy_status='active'
              and enforcement_status not in ('{_enforced}')
              and enforcement_class <> '{RULE_UNENFORCEABLE_CLASS}'),
          (select count(*) from ops.v_rule_enforcement_status
            where policy_status='active'
              and enforcement_status not in ('{_enforced}')
              and enforcement_class = '{RULE_UNENFORCEABLE_CLASS}')"""
        _venv_python = os.path.join(REPO_ROOT, ".venv/bin/python")
        _query_python = _venv_python if os.path.exists(_venv_python) else sys.executable
        p = subprocess.run(
            [_query_python, os.path.join(REPO_ROOT, "tools/db-tap.py"),
             "sql", "/dev/stdin"],
            input=sql, cwd=REPO_ROOT, text=True, capture_output=True, timeout=120,
            env={k: v for k, v in os.environ.items() if k != "CARR_VAULT"},
        )
        if p.returncode:
            snapshot["errors"].append("controls: " +
                                      ((p.stderr or "").strip() or "query failed"))
        else:
            for line in p.stdout.splitlines():
                cols = [c.strip() for c in line.split("|")]
                if len(cols) == 5 and all(c.isdigit() for c in cols):
                    snapshot["controls"] = {
                        "doctrine_gate_failures_24h": int(cols[0]),
                        "doctrine_never_reviewed": int(cols[1]),
                        "doctrine_stale_sections": int(cols[2]),
                        "active_rules_not_hard_enforced": int(cols[3]),
                        "active_rules_accepted_unenforceable": int(cols[4]),
                    }
                    break
    return snapshot


def _iso(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _canonical_now(snap):
    fixed = _iso(snap.get("observed_at"))
    return fixed or datetime.now(timezone.utc)


def _live_jobs(snap):
    """Fixture and database paths both enforce the live-only health contract."""
    return [j for j in (snap.get("jobs") or [])
            if isinstance(j, dict) and j.get("mode") == "live"]


def _legacy_scheduled_definitions(snap):
    """Enabled definitions whose LEGACY scheduler is still the executor.

    Until ``disable-legacy-schedule`` fires for a key, launchd or the Claude
    scheduler runs it and the Control Plane ledger holds nothing — so a due
    window with no ledger row is the cutover working, not a missed run.  The
    default is fail-closed: a definition that does not declare itself legacy
    is still held to every due window.
    """
    return sorted(str(d.get("key")) for d in (snap.get("job_definitions") or [])
                  if isinstance(d, dict) and d.get("legacy_live") is True)


def _missing_due_executions(snap):
    """Due cron windows without a successful, exact ledger completion.

    A ledger row is evidence only when its ``scheduled_for`` is one of the
    cron's eligible instants (not merely in the same calendar month), it is
    already due, and that exact attempt succeeded with its completion receipt.
    This matters most for collapsed monthly windows: a cancelled run, an old
    failure, or a future/off-window row must not make the month look healthy.
    """
    now_utc = _canonical_now(snap)
    jobs = _live_jobs(snap)
    findings = []
    for definition in snap.get("job_definitions") or []:
        if not isinstance(definition, dict):
            continue
        if definition.get("legacy_live") is True:
            # Loop #536: asserting a ledger row for a key the control plane does
            # not dispatch produced a MISSING DUE line every day for eleven
            # definitions, which is how a genuine miss became unreadable.
            continue
        recurrence = definition.get("recurrence")
        if not isinstance(recurrence, dict) or not recurrence.get("cron"):
            continue
        zone_name = str(recurrence.get("timezone") or "America/Chicago")
        try:
            zone = ZoneInfo(zone_name)
        except Exception:
            findings.append((str(definition.get("key")), f"invalid timezone {zone_name}"))
            continue
        now_local = now_utc.astimezone(zone).replace(tzinfo=None)
        registered = _iso(definition.get("registered_at"))
        registered_local = (registered.astimezone(zone).replace(tzinfo=None)
                            if registered else now_local - timedelta(days=40))
        # A CUT-OVER DEFINITION IS THE CONTROL PLANE'S ONLY FROM THE INSTANT ITS
        # LEGACY SCHEDULE WAS DISABLED. Windows before that stamp were the
        # launchd or Claude scheduler's to run, so the ledger is rightly silent
        # for them; counting them made calendar-fetch-daily read as three missed
        # weekdays it never owned (loop #536).
        cutover = _iso(definition.get("legacy_disabled_at"))
        if cutover:
            registered_local = max(registered_local,
                                   cutover.astimezone(zone).replace(tzinfo=None))
        collapsed = "collapses window" in str(recurrence.get("source") or "").lower()
        start = max(registered_local,
                    now_local - timedelta(days=40 if collapsed else 8))
        try:
            windows = [w for w in cron_windows(str(recurrence["cron"]), start, now_local)
                       if w <= now_local - timedelta(minutes=5)]
        except (ValueError, IndexError) as exc:
            findings.append((str(definition.get("key")), f"invalid cron: {exc}"))
            continue
        eligible = set(windows)
        scheduled = []
        for job in jobs:
            try:
                version_matches = (int(job.get("definition_version", -1)) ==
                                   int(definition.get("version", -2)))
            except (TypeError, ValueError):
                version_matches = False
            if job.get("definition_key") != definition.get("key") or not version_matches:
                continue
            stamp = _iso(job.get("scheduled_for"))
            if stamp:
                local_stamp = stamp.astimezone(zone).replace(tzinfo=None)
                # An exact scheduled instant is the ledger identity.  Do not
                # give a row a five-minute grace here: the scheduler itself
                # creates the exact instant, and a nearby/manual row is not a
                # receipt for this due window.
                if local_stamp in eligible:
                    scheduled.append((local_stamp, job))

        def is_successful(job):
            return (job.get("state") == "succeeded" and
                    int(job.get("completion_receipt_count", 0)) >= 1)

        def non_success_detail(scope, candidates):
            states = sorted({str(job.get("state") or "unknown") for _, job in candidates})
            if any(job.get("state") == "succeeded" for _, job in candidates):
                states.append("succeeded without exact completion receipt")
            return f"{scope} NON-SUCCESS execution ({', '.join(states)})"
        if collapsed:
            # A monthly window cron intentionally exposes several eligible days;
            # its ledger contract admits one *successful exact eligible* job in
            # that month, not one arbitrary row or one per eligible day.
            months = sorted({(w.year, w.month) for w in windows})
            for year, month in months:
                candidates = [(s, job) for s, job in scheduled
                              if s.year == year and s.month == month]
                if any(is_successful(job) for _, job in candidates):
                    continue
                scope = f"monthly window {year:04d}-{month:02d}"
                if candidates:
                    findings.append((str(definition.get("key")),
                                     non_success_detail(scope, candidates)))
                else:
                    findings.append((str(definition.get("key")), scope))
        else:
            for window in windows:
                candidates = [(s, job) for s, job in scheduled if s == window]
                if any(is_successful(job) for _, job in candidates):
                    continue
                scope = window.strftime("%Y-%m-%d %H:%M") + f" {zone_name}"
                if candidates:
                    findings.append((str(definition.get("key")),
                                     non_success_detail(scope, candidates)))
                else:
                    findings.append((str(definition.get("key")), scope))
    return findings


def _stuck_live_jobs(snap):
    now = _canonical_now(snap)
    findings = []
    for job in _live_jobs(snap):
        state = job.get("state")
        reason = None
        if state == "queued":
            due = _iso(job.get("scheduled_for"))
            if due and due < now - timedelta(minutes=15):
                reason = "queued more than 15m after scheduled_for"
        elif state == "retry_wait":
            due = _iso(job.get("next_attempt_at"))
            if due and due < now - timedelta(minutes=5):
                reason = "retry_wait more than 5m past next_attempt_at"
        elif state == "running":
            lease = _iso(job.get("leased_until"))
            started = _iso(job.get("started_at"))
            timeout = int(job.get("timeout_seconds") or 0)
            if lease and lease < now:
                reason = "running with expired lease"
            elif started and timeout and started + timedelta(seconds=timeout) < now:
                reason = "running past registered timeout"
        elif state == "waiting_approval":
            created = _iso(job.get("created_at"))
            if created and created < now - timedelta(hours=24):
                reason = "waiting_approval more than 24h"
        if reason:
            findings.append((job, reason))
    return findings


def _canonical_finding(key, detail):
    print(f"  CANONICAL_FINDING {key} — {detail}")


def _canonical_health():
    """The normal health surface: record/control-plane/local truth only."""
    rc = 0
    try:
        snap = _canonical_snapshot()
    except Exception as exc:
        print(f"canonical health: REFUSED ({type(exc).__name__}: {exc})")
        return 1

    print(f"Façade check (rule 28) — {time.strftime('%Y-%m-%d %H:%M')} — canonical receipts, not Drive renders")
    for error in snap.get("errors", []):
        print(f"  ⚠︎ canonical source UNREADABLE — {error}")
        _canonical_finding("source_unreadable", str(error))
        rc = 1

    if CANONICAL_SECTION in ("all", "exports"):
        exports = snap.get("exports")
        print("Export register — canonical export_run receipts")
        if not isinstance(exports, dict):
            print("  ⚠︎ export receipts UNREADABLE")
            rc = 1
        else:
            registered = set(exports.get("registered") or [])
            # A RETIRED TARGET IS NOT A MISSED CHAIN. Subtracted before the loop
            # rather than filtered out of `bad` afterwards, so a retired target
            # cannot reach any of the four failure branches by another route.
            retired = set(exports.get("retired") or [])
            registered -= retired
            rows = {r.get("target"): r for r in exports.get("rows") or [] if isinstance(r, dict)}
            bad = []
            for target in sorted(registered):
                row = rows.get(target)
                if not row:
                    bad.append(f"NEVER RAN {target}")
                    continue
                if row.get("latest_status") != "ok":
                    bad.append(f"LATEST FAILED {target} (latest status {row.get('latest_status')})")
                    continue
                last_ok = row.get("last_ok")
                if not last_ok:
                    bad.append(f"NEVER OK {target}")
                    continue
                try:
                    stamp = datetime.fromisoformat(str(last_ok).replace("Z", "+00:00"))
                    now = datetime.now(stamp.tzinfo) if stamp.tzinfo else datetime.now()
                    if now - stamp > timedelta(hours=26):
                        bad.append(f"STALE {target} (last ok {str(last_ok)[:16]})")
                except ValueError:
                    bad.append(f"UNPARSEABLE {target} last_ok={last_ok}")
            for finding in bad:
                print(f"  ⚠︎ {finding}")
                _canonical_finding("export_receipt", finding)
            # THE RETIRED COUNT RIDES ON THE LINE EITHER WAY. Rule bd4a6d22 asks
            # for a chosen state to stay visible rather than become silence, so
            # the reader is told how many targets are carried and why they have
            # no receipt — never left to infer it from a number that shrank.
            _carried = (f", {len(retired)} retired at the 2026-08-19 cutoff and "
                        f"correctly unreceipted" if retired else "")
            if bad:
                rc = 1
                if retired:
                    print(f"  -- RETIRED {len(retired)} md render target(s) not counted above")
            else:
                print(f"  OK {len(registered)} registered target(s), "
                      f"all receipted inside 26h{_carried}")

    if CANONICAL_SECTION in ("all", "jobs"):
        print("Schedule drift — durable Control Plane job state")
        jobs = snap.get("jobs")
        definitions = snap.get("job_definitions")
        if not isinstance(jobs, list) or not isinstance(definitions, list):
            print("  ⚠︎ job ledger UNREADABLE")
            _canonical_finding("job_ledger", "jobs or enabled definitions missing")
            rc = 1
        else:
            live_jobs = _live_jobs(snap)
            cutoff = _canonical_now(snap) - timedelta(days=8)
            bad = [j for j in live_jobs if j.get("state") in
                   ("failed", "timed_out", "dead_lettered") and
                   (_iso(j.get("created_at")) or cutoff) >= cutoff]
            unreceipted = [j for j in live_jobs if j.get("state") == "succeeded" and
                           int(j.get("completion_receipt_count", 0)) < 1]
            missing = _missing_due_executions(snap)
            stuck = _stuck_live_jobs(snap)
            legacy = _legacy_scheduled_definitions(snap)
            for job in bad:
                print(f"  ⚠︎ {job.get('definition_key')} {job.get('state')} "
                      f"attempt {job.get('attempt')}/{job.get('max_attempts')}")
                _canonical_finding("job_terminal_failure",
                                   f"{job.get('definition_key')} {job.get('state')}")
            for job in unreceipted:
                detail = (f"{job.get('definition_key')} job={job.get('id')} "
                          f"attempt={job.get('attempt')} succeeded without exact completion receipt")
                print(f"  ⚠︎ {detail}")
                _canonical_finding("job_completion_receipt", detail)
            for key, window in missing:
                if " NON-SUCCESS execution" in window:
                    detail = f"{key} {window}"
                    finding = "job_due_non_success"
                else:
                    detail = f"{key} MISSING DUE execution for {window}"
                    finding = "job_missing_due"
                print(f"  ⚠︎ {detail}")
                _canonical_finding(finding, detail)
            for job, why in stuck:
                detail = f"{job.get('definition_key')} job={job.get('id')} {why}"
                print(f"  ⚠︎ {detail}")
                _canonical_finding("job_stuck", detail)
            # THE CARRIED COUNT RIDES ON THE LINE EITHER WAY, same contract the
            # exports section uses for retired targets: a chosen state stays
            # visible rather than becoming silence (rule bd4a6d22).
            if legacy:
                print(f"  -- CARRIED {len(legacy)} definition(s) still on a legacy "
                      f"scheduler, no Control Plane ledger row expected: "
                      f"{', '.join(legacy)}")
            if bad or unreceipted or missing or stuck:
                rc = 1
            else:
                print(f"  OK {len(live_jobs)} live job(s), every due window present; "
                      "no terminal failure, stuck state, or unreceipted success")

    if CANONICAL_SECTION in ("all", "registry"):
        print("Registry integrity — canonical v_export_leads")
        p = subprocess.run(
            [sys.executable, os.path.join(REPO_ROOT, "tools/registry-audit.py")],
            cwd=REPO_ROOT, text=True, capture_output=True, timeout=120,
            env={k: v for k, v in os.environ.items() if k != "CARR_VAULT"},
        )
        if p.stdout:
            print(p.stdout.rstrip())
        if p.returncode:
            print("  ⚠︎ canonical registry audit failed")
            _canonical_finding("registry_integrity", "canonical registry audit failed")
            rc = 1
    if CANONICAL_SECTION == "all":
        print("Doctrine and rule controls — canonical database state")
        controls = snap.get("controls")
        if not isinstance(controls, dict):
            print("  ⚠︎ doctrine/rule controls UNREADABLE")
            _canonical_finding("control_state", "doctrine/rule controls unreadable")
            rc = 1
        else:
            gate_failures = int(controls.get("doctrine_gate_failures_24h", 0))
            never_reviewed = int(controls.get("doctrine_never_reviewed", 0))
            stale = int(controls.get("doctrine_stale_sections", 0))
            rule_gaps = int(controls.get("active_rules_not_hard_enforced", 0))
            accepted = int(controls.get("active_rules_accepted_unenforceable", 0))
            print(f"  {'OK' if not gate_failures else '⚠︎'} gate-blocks-24h      "
                  f"{gate_failures}")
            print(f"  {'OK' if not never_reviewed else '⚠︎'} never-reviewed       "
                  f"{never_reviewed} policy-bearing sections")
            print(f"  {'OK' if not stale else '⚠︎'} stale-sections       "
                  f"{stale} past review_after")
            # The accepted count RIDES ON THE PASSING LINE. Rule bd4a6d22 asks
            # for the chosen state to stay visible rather than become silence,
            # so a green line still says how many rules are carried as
            # unenforceable by design and how many would have to change for that
            # to be wrong.
            _carried = (f", {accepted} carried as unenforceable by design"
                        if accepted else "")
            print(f"  {'OK' if not rule_gaps else '⚠︎'} active-rule-gaps      "
                  f"{rule_gaps} active admitted rules unenforced{_carried}")
            if gate_failures or never_reviewed or stale or rule_gaps:
                if gate_failures:
                    _canonical_finding("doctrine_gate", f"{gate_failures} failures in 24h")
                if never_reviewed:
                    _canonical_finding("doctrine_review", f"{never_reviewed} never reviewed")
                if stale:
                    _canonical_finding("doctrine_stale", f"{stale} stale sections")
                if rule_gaps:
                    _canonical_finding("rule_enforcement", f"{rule_gaps} active rule gaps")
                rc = 1

        p = subprocess.run(["git", "status", "--porcelain"], cwd=REPO_ROOT,
                           text=True, capture_output=True, timeout=30)
        if p.returncode:
            print("  ⚠︎ repository worktree status UNREADABLE")
            _canonical_finding("repo_status", "git status unreadable")
            rc = 1
        else:
            _loose = _health_sub.classify_loose_status(REPO_ROOT, p.stdout.splitlines())
            _actionable = _loose["actionable_tracked"] + _loose["actionable_untracked"]
            _needs_attention = _health_sub.loose_work_requires_attention(_loose)
            print(f"  {'OK' if not _needs_attention else '⚠︎'} loose-work            "
                  f"{len(_actionable)} actionable path(s) "
                  f"({len(_loose['actionable_tracked'])} tracked, "
                  f"{len(_loose['actionable_untracked'])} untracked)"
                  f" · {len(_loose['expected_patched_submodules'])} expected patched submodule(s)"
                  f" · {len(_loose['managed_artifacts'])} managed artifact(s)")
            if _needs_attention:
                _canonical_finding("repo_loose_work", f"{len(_actionable)} actionable path(s)")
                rc = 1
    print("Projection freshness/tamper checks are recovery evidence; use --recovery --reason <why>.")
    return rc


if not RECOVERY_MODE:
    sys.exit(_canonical_health())

print(f"HEALTH RECOVERY MODE — NONCANONICAL Drive projections — reason: {RECOVERY_REASON}", file=sys.stderr)
print(f"HEALTH RECOVERY MODE — vault: {VAULT}", file=sys.stderr)

# name, output (glob ok — newest match wins), max_age_days (None = no cadence),
# input globs (any newer than output => BEHIND), note shown on failure
WATCH = [
    ("Lead Board",        "Automation/lead-board.html", 9,
     ["DNA/Leads/lead-registry.xlsx", "DNA/Leads/lead-router-*.xlsx",
      "Automation/renewal-radar.json", "Automation/entity-formation-leads.json",
      "Automation/pre-entity-watch.json", "Automation/lead-board-decisions.json",
      "Automation/lead-board-hot.json"],
     "weekly Monday run (lead-system-weekly.md)"),
    ("Deal Room",         "DNA/Team/live-boards/deal-room-panhandle.html", None,
     ["DNA/Deal Management/panhandle-team-deals.json"],
     "refresh-on-change law (deal-enrichment-sop.md)"),
    ("Renewal feed",      "Automation/renewal-radar.json", 9,
     ["DNA/Leads/renewal-radar-*.xlsx"],
     "weekly with the Monday run"),
    ("Entity formation",  "Automation/entity-formation-leads.json", 9, [],
     "weekly Sunbiz/AL sweep (corp-filings-sop.md; fires on Mac wake)"),
    ("Pre-entity watch",  "Automation/pre-entity-watch.json", 9, [],
     "corroborate.py rides the Monday radar run"),
    ("DSO matches",       "Automation/dso-matches.json", 100, [],
     "dso-match.py, event-driven vs quarterly source refresh. CORRECTED 2026-07-27: the "
     "note here used to claim 'board ingests it' — it does NOT. Nothing reads this file. "
     "build-lead-board.py derives its DSO Associate count from the router's SEGMENT string, "
     "not from these matches. Wiring it in is open work; until then this row only proves the "
     "file is fresh, never that it reaches a surface."),
    ("Relocating owners", "Automation/relocating-owner-leads.json", 100, [],
     "FL DOR x out-of-state DOH join, quarterly-ish; board ingests it (corrective 2026-07-25)"),
    ("National accounts", "DNA/Team/national-accounts.json", None,  [],
     "curated shared feed, human-review cadence — existence/readability watch only (corrective 2026-07-25)"),
    # The weekly run retains dated history when it has one, while the current
    # scheduler writes/refreshes the canonical `radar-digest-latest.md`.
    # Watch both names and let the normal newest-match logic judge freshness.
    ("Radar digest",      "Automation/radar/radar-digest-*.md", 9, [],
     "Monday radar run (radar-digest-sop.md)"),
    ("PECOS pool",        "Automation/radar/upstream/pecos.json", 100, [],
     "quarterly (Jan/Apr/Jul/Oct; next diff vs the Q1 baseline in Oct)"),
    ("Section index",     "Automation/section-index.tsv", 26/24, [],
     "retrieval-as-code layer; rebuild = run.sh section-index. CORRECTED 2026-08-13 (Phase 1): "
     "this used to claim it 'rides the Monday run', wiring that never existed — build-section-"
     "index.py was invoked by nothing until today. It now rides the nightly chain (bin/nightly.sh, "
     "after exports + corpus push), same 26h cadence as the other nightly-chain GEN rows below. "
     "No BEHIND check — doc inputs churn daily by design. Graph-System/ (pipelines/build-system-"
     "graph.py, also newly wired into the same nightly step) deliberately gets no row of its own: "
     "it is a many-file derived folder like Graph/ (build-graph-notes.py, wired long before today), "
     "which has never had a row here either — a single representative mtime would not mean much for "
     "a folder, and the nightly chain's own step log already proves it ran. Adding one would be "
     "noise, not signal."),
    # --- the record layer's generated files (added 2026-07-31 with ORDER 2) ------
    # 26h, matching the export digest's dead-man: the chain runs nightly 7 days a
    # week (unlike the weekday pipelines above), so anything past ~a day means the
    # chain did not run or its export step failed. Rule 28 in its purest form —
    # these files existing proves nothing; their FRESHNESS proves the chain ran.
    # No BEHIND inputs: their input is the database, which has no mtime.
    ("GEN lead-registry",   "DNA/Leads/lead-registry.xlsx",              26/24, [], "nightly chain (bin/nightly.sh)"),
    ("GEN client-roster",   "DNA/Clients/client-roster.xlsx",            26/24, [], "nightly chain (bin/nightly.sh)"),
    ("GEN vendors",         "DNA/Network/vendors.xlsx",                  26/24, [], "nightly chain (bin/nightly.sh)"),
    ("GEN deals json",      "DNA/Deal Management/panhandle-team-deals.json", 26/24, [], "nightly chain (bin/nightly.sh)"),
    ("GEN clients-active",  "DNA/Clients/clients-active.md",             26/24, [], "nightly chain (bin/nightly.sh)"),
    ("GEN rules shared",    "DNA/compiled-rules-shared.md",              26/24, [], "nightly chain (bin/nightly.sh)"),
    ("GEN rules joe",       "00_Context/compiled-rules-joe.md",          26/24, [], "nightly chain (bin/nightly.sh)"),
    # ORDER 37: the vendor-politics compile target. Its key prefix-matches
    # `--only compiled-rules`, so the hourly refresh reaches it as well.
    ("GEN rules intro",     "DNA/Network/introduction-rules.md",         26/24, [],
     "nightly chain (bin/nightly.sh) + hourly bin/refresh-rules.sh"),
    # --- the remaining repo-local job reports ----------------------------------
    # The review queue is still refreshed by the weekday local schedule. Legacy
    # brief-pack and Monday-agenda files are intentionally absent from this
    # dead-man list: normal delivery is the on-demand record-native
    # `morning-brief` composite, whose sections expose ready/empty/unavailable
    # source state instead of borrowing a local file mtime.
    # Cadence and matcher remain credential-gated nightly diagnostics.
    ("JOB review-queue",  os.path.expanduser("~/carr-system/out/review-queue/review-queue.html"), 26/24, [],
     "run.sh review-queue (heartbeat JOB 4b)"),
    ("JOB matcher",       os.path.expanduser("~/carr-system/out/availability-matches.md"), 26/24, [],
     "availability_matcher.py, step 2 of the nightly chain (exits 78 without a jobs credential)"),
    ("JOB cadence",       os.path.expanduser("~/carr-system/out/cadence-latest.md"), 26/24, [],
     "cadence_engine.py, step 1 of the nightly chain (exits 78 without a jobs credential)"),
    ("Joe calendar feed", "DNA/Team/calendar-latest.ics", 4, [],
     "fetch-calendar.sh; business days only"),
    ("Dell calendar feed","DNA/Team/calendar-latest-dell.ics", 4, [],
     "KNOWN BLOCKED on Dell's OS update (memory: dell-calendar-fetch-blocked) — expected stale until he updates"),
]

def _retired_watch_entries(watch, retired_paths):
    """Drop only dead-man rows whose exact output is a retired export target."""
    retired = [entry for entry in watch if entry[1] in retired_paths]
    active = [entry for entry in watch if entry[1] not in retired_paths]
    return active, retired


# THE CUTOFF REACHES THIS LIST TOO, and until 2026-08-23 it did not.
#
# The markdown-render cutoff fired 2026-08-19: exporters/run_exports.py skips
# every .md target and prints RETIRED instead of writing it. Both export-freshness
# checks in this file already ask the same flag function and drop the retired
# targets. This dead-man list did not, so four rows watched files the exporter now
# refuses to write and reported a CHOSEN state as four failures every single day —
# the compiled shared rules, Joe's personal rules, the introduction rules, and the
# active-client roster.
#
# Worse than noise: the remedy those rows printed was bin/refresh-rules.sh, which
# exits 0 and writes nothing after the cutoff. A red row whose fix silently does
# nothing costs more than no row, because somebody runs it and believes it.
#
# MATCHED AGAINST THE EXPORT REGISTRY, NOT AGAINST ".md". The first version of
# this block dropped any row whose path ended in .md, which took the radar digest
# and the two repo-local job reports with it — three files still written nightly
# by tools the cutoff never touched. The cutoff is about EXPORT TARGETS, so this
# asks the registry which paths it has stopped writing and drops exactly those.
#
# Gated on the same flag rather than deleted, so flipping doctrine.md_renders_retired
# back to false restores the rows with no code change — one contract, no second
# place to decide what "retired" means.
try:
    from exporters.run_exports import md_renders_retired as _md_retired
    from exporters.targets import TARGETS as _EXPORT_TARGETS
    if _md_retired():
        _retired_paths = {rel for rel, _fn in _EXPORT_TARGETS.values()
                          if rel.lower().endswith(".md")}
        WATCH, _retired_watch = _retired_watch_entries(WATCH, _retired_paths)
        for _w in _retired_watch:
            print(f"  -- {_w[0]:<18} RETIRED at the markdown-render cutoff; the store "
                  f"serves this (standing-context / read-doctrine / catch-me-up)")
except Exception as _exc:
    # Fails OPEN, the same direction run_exports.py chose: a dead config lookup
    # keeps every row rather than silently hiding a watch that still matters.
    print(f"  -- {'md cutoff':<18} could not read the cutoff registry "
          f"({type(_exc).__name__}); watching every row")

# --- credential gates (added 2026-08-02) -------------------------------------
# A JOB THAT CANNOT RUN IS NOT A JOB THAT FAILED, and reporting both as MISSING
# is how a dashboard loses its readers. The matcher and the cadence engine exit
# 78 (EX_CONFIG — ran, found no credential, wrote nothing, said so) and the
# nightly chain already counts that as SKIP rather than FAIL. This check now
# agrees with the chain instead of contradicting it: a gated job reports GATED
# and does NOT set rc, so a REAL absence somewhere else in this list still turns
# the run red and still gets read.
#
# THREE STATES, not two, and the third is the reason this is not a one-liner.
# The credential may be PRESENT IN THE FILE and still never reach the job. That
# is not hypothetical: on 2026-08-02 this check found CARR_DB_JOBS_URL sitting in
# ~/.config/carr/db.env since 07-31 with its value UNQUOTED and containing an `&`,
# so `set -a; . db.env` — the exact line bin/nightly.sh uses — died on a parse
# error at that line and the variable was never set. Every nightly run since
# printed "NOT CONFIGURED", which read like "Joe has not set it yet" and was
# false. A gate check that only asked "is the key in the file" would have flipped
# these two rows to a permanent, wrong GATED; a gate check that only sourced the
# file would have called it a permanent, uninformative CLOSED. It asks both and
# reports the disagreement, because the disagreement IS the bug.
#
# Values are never read into this process and never printed — the shell probe
# reports set/not-set as an exit code and nothing else.
DB_ENV = os.path.expanduser("~/.config/carr/db.env")

# watch name -> the credential names the job itself accepts, in its own order
GATES = {
    "JOB matcher": ("CARR_DB_JOBS_URL", "CARR_DB_MATCHER_URL", "DATABASE_URL"),
    "JOB cadence": ("CARR_DB_JOBS_URL", "CARR_DB_CADENCE_URL", "DATABASE_URL"),
}


def _keys_in_env_file():
    """Key names declared in db.env, parsed as text. Never sources, never stores values."""
    try:
        with open(DB_ENV) as fh:
            return {ln.split("=", 1)[0].strip()
                    for ln in fh
                    if "=" in ln and not ln.lstrip().startswith("#") and ln.split("=", 1)[1].strip()}
    except OSError:
        return set()


def _shell_can_load(key):
    """Would bin/nightly.sh's own `set -a; . db.env` leave this key set?"""
    if not os.path.exists(DB_ENV):
        return False
    probe = subprocess.run(
        ["/bin/zsh", "-c", 'set -a; . "$1" >/dev/null 2>&1; set +a; [ -n "${'+key+':-}" ]',
         "_", DB_ENV],
        capture_output=True, text=True)
    return probe.returncode == 0


def gate_state(names):
    """('open'|'gated'|'broken', detail). 'broken' = declared but unreachable."""
    for k in names:
        if os.environ.get(k):
            return "open", f"{k} set in this environment"
    declared = _keys_in_env_file() & set(names)
    for k in names:
        if k in declared and _shell_can_load(k):
            return "open", f"{k} loads from {DB_ENV}"
    if declared:
        k = next(k for k in names if k in declared)
        return "broken", (
            f"{k} IS present in {DB_ENV} but `set -a; . db.env` cannot load it — the value is "
            f"unquoted and carries a shell metacharacter, so the source line dies on a parse "
            f"error and the job sees nothing. Fix: wrap the value in single quotes. This is why "
            f"the nightly log says NOT CONFIGURED for a credential that exists")
    return "gated", f"none of {', '.join(names)} is set — the job exits 78 and writes nothing"


def newest(pattern):
    hits = glob.glob(os.path.join(VAULT, pattern))
    return max(hits, key=os.path.getmtime) if hits else None

def age_days(path):
    return (time.time() - os.path.getmtime(path)) / 86400


def db_env_parse_failure():
    """Line number db.env dies on when SOURCED, or None if the whole file parses.

    gate_state() below asks "can THIS KEY be loaded", per watch, and only when a
    watch has no output. That question cannot be asked at all for a key no watch
    names, and it is never asked on a healthy night. The failure it was built for
    is not per-key: zsh aborts the whole `. db.env` at the first bad line, so ONE
    unquoted DSN silently empties the file for every job on this Mac.

    That is not hypothetical twice over. 2026-08-02 was CARR_DB_JOBS_URL. Then on
    2026-08-20 CARR_DB_PROGRAM5_FORWARD_FIX_VERIFIER_URL — a key no watch here
    names, so nothing in this file could see it — landed unquoted and closed
    bin/migrate-prod.sh, the one sanctioned door to production migrations, for
    six days, for every caller that did not already have NEON_API_KEY exported.
    This check is file-level and unconditional for exactly that reason: it does
    not need to know which keys matter.

    `zsh -n` parses and runs nothing, so no value is read, expanded or printed.
    Only the line number is kept — zsh's message can quote a token from the
    offending line, and every value in that file is a credential.
    """
    if not os.path.exists(DB_ENV):
        return None
    probe = subprocess.run(["/bin/zsh", "-n", DB_ENV], capture_output=True, text=True)
    if probe.returncode == 0:
        return None
    found = re.search(r":(\d+):", probe.stderr)
    return found.group(1) if found else "unknown"


rc = 0
print(f"Façade check (rule 28) — {time.strftime('%Y-%m-%d %H:%M')} — outputs, not schedules")
_db_env_line = db_env_parse_failure()
if _db_env_line is not None:
    print(f"  ⚠︎ {'db.env':<18} UNSOURCEABLE — {DB_ENV} does not parse as shell at line "
          f"{_db_env_line}. Every job that does `set -a; . db.env` (bin/migrate-prod.sh, "
          f"bin/nightly.sh, bin/refresh-rules.sh, bin/control-plane-tick.sh and the rest) "
          f"loses ALL of its keys, not just the bad one, and reports its credential missing. "
          f"Fix: single-quote that value  · one bad line disables every credential here")
    rc = 1
for name, out_pat, max_age, inputs, note in WATCH:
    out = newest(out_pat)
    if not out:
        if name in GATES:
            state, detail = gate_state(GATES[name])
            if state == "gated":
                print(f"  -- GATED {name:<16} not runnable, so no output is expected: {detail}  · {note}")
                continue
            if state == "broken":
                print(f"  ⚠︎ {name:<18} CREDENTIAL PRESENT BUT UNREACHABLE — {detail}  · {note}")
                rc = 1
                continue
        print(f"  MISSING {name:<18} no file matches {out_pat}  · {note}")
        rc = 1
        continue
    a = age_days(out)
    problems = []
    if max_age is not None and a > max_age:
        problems.append(f"STALE {a:.1f}d old (cadence {max_age}d)")
    behind = [os.path.basename(i) for pat in inputs
              if (i := newest(pat)) and os.path.getmtime(i) > os.path.getmtime(out) + 60]
    if behind:
        problems.append(f"BEHIND inputs: {', '.join(behind)}")
    if problems:
        print(f"  ⚠︎ {name:<18} {'; '.join(problems)}  · {note}")
        rc = 1
    else:
        print(f"  OK {name:<18} {a:.1f}d old")

# --- schedule drift (added 2026-08-02) ---------------------------------------
# WHY THIS EXISTS. Every check above measures output AGE, so a job scheduled for
# 2:05am that actually runs at 8:49am still looks perfectly fresh that day. Joe
# found this by noticing it himself: "nightly record layer does not run unless my
# computer is on. its scheduled for 230am but it only runs when i open a session
# the next morning." He was right — `pmset -g sched` carried no wake event, so the
# Mac slept through 2am and the task fired on wake. Nothing here could see it.
#
# The scheduler's own lastRunAt is NOT readable from a script (it lives in the app's
# store, reachable only through the list_scheduled_tasks MCP tool), so this uses the
# signal that is local: WHEN THE JOB LAST STARTED, read from the marker the job itself
# writes on entry.
#
# MEASURE THE LAST ATTEMPT, NEVER THE LAST SUCCESS (corrected 2026-08-13, loop #327).
# Until this change the drift was computed from the watched file's MTIME, which is the
# last time anything appended to it — i.e. the last run to get far enough to write. So a
# chain that FIRED EXACTLY ON TIME and then failed, and was re-run by hand at midday,
# reported the midday time and was indistinguishable from a chain that never fired at
# all. That happened on 2026-08-11 and the row it printed — "ran 12:33, scheduled ~02:00
# — 10.6h drift · Fix: sudo pmset repeat wakeorpoweron" — was wrong in every part: the
# job had begun at 07:05:04Z (02:05 CT, on schedule), the failure was the mypy tripwire,
# and the pmset wake event it recommended was ALREADY SET and could not have helped. A
# session read that row and went chasing a sleep problem that did not exist instead of
# the failing step. THE TWO FACTS ARE SEPARATE SENTENCES: "fired on time, exited 1" is a
# different report from "never fired", they have different remedies, and only the second
# is ever a wake-schedule problem — which is why the pmset hint now lives ONLY on that
# branch. Whether the run WORKED is the next block's question, not this one's.
#
# name, watched file, expected local hour, tolerance hours, start marker, stale-after
# hours, wake-remedy note.
# The watched file must be one ONLY THE SCHEDULED JOB writes. The first version of this
# check watched an exported .xlsx and immediately produced a false positive: a manual
# `CARR_EXPORT_LIVE=1 ./run.sh export` rewrote it at 11:38 and the check reported "9.6h
# drift" from that, not from the scheduler. It was measuring when a FILE was written, not
# when the JOB ran. out/nightly.log is appended by bin/nightly.sh and by nothing else.
#
# ── THE LOG IS NOW THE FALLBACK, NOT THE SOURCE (2026-08-14) ─────────────────
# "Appended by bin/nightly.sh and by nothing else" is true and was never the
# problem. The problem is that the file gets TRIMMED. On 2026-08-14 this row
# read "FIRED LATE — nearest attempt to the 08-14 02:00 window began 08:00, 6.0h
# drift" and recommended `sudo pmset repeat wakeorpoweron`. Every part of that
# was wrong: the job ledger shows the chain's first step started 02:05:05 local,
# dead on schedule, and `pmset -g sched` already carried "wakepoweron at 1:55AM
# every day". out/nightly.log held only two "chain begin" markers, both from
# midday hand re-runs, and its first line was a mid-sentence fragment — the head
# of the file, 02:05 marker included, had been cut off.
#
# THIS IS THE SECOND TIME THE SAME ROW SENT SOMEONE THE SAME WRONG WAY. The
# block below already carries the 2026-08-11 post-mortem: "A session read that
# row and went chasing a sleep problem that did not exist instead of the failing
# step." It happened again on 08-14, to a session that had READ that warning.
# A comment describing the trap does not disarm it; changing what the row reads
# does.
#
# ops.run is what should have been read all along, and only exists to be: every
# step of every chain records started_at durably, nothing trims it, and it
# survives the machine. The log stays as the fallback for a cold Mac or an
# absent credential — and when the fallback is what answered, the row SAYS SO,
# because a row that silently changes its basis is how a wrong reading becomes
# invisible.
#
# name, watched file, expected local hour, tolerance hours, start marker,
# stale-after hours, wake-remedy note, LEDGER SERVICE KEY.
SCHEDULE = [
    ("nightly-record-layer", "~/carr-system/out/nightly.log", 2, 2.5,
     "chain begin", 26,
     "the Mac slept through the window and no session opened to fire it on wake — the "
     "encrypted backup is the seventh step of that chain (it was step 3 when this note "
     "was written), so it is skipped for as long as no session opens. "
     "Check `pmset -g sched` BEFORE recommending a wake schedule: on 2026-08-11 and "
     "again on 2026-08-14 this remedy was printed at a Mac that already had one",
     "nightly-record-layer"),
]


def attempt_starts_from_ledger(service_key):
    """Every ATTEMPT start for one service, as local epochs, oldest-first.

    ops.run.started_at is written by the job itself as it begins each step, so
    the earliest step of a chain IS that chain's firing time — the same question
    the log marker was standing in for, answered by a record that cannot be
    trimmed.

    Returns None when the ledger cannot be reached at all, which is a different
    answer from "it ran and there is nothing there" and must stay
    distinguishable: the caller falls back to the log only on None.
    """
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "ops_record_hc",
            os.path.expanduser("~/carr-system/tools/ops-record.py"))
        if spec is None or spec.loader is None:
            return None
        ops_record = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ops_record)
        with ops_record.connect("read") as conn, conn.cursor() as cur:
            # Chain steps only. A whole-chain wrapper row, were one ever added,
            # would double-count the same firing.
            cur.execute(
                """select extract(epoch from r.started_at)
                     from ops.run r join ops.service s on s.id = r.service_id
                    where s.key = %s and r.started_at is not null
                      and r.started_at > now() - interval '14 days'
                 order by r.started_at""",
                (service_key,))
            rows = [float(x[0]) for x in cur.fetchall()]
        if not rows:
            return None
        # COLLAPSE STEPS INTO FIRINGS. The ledger holds one row per STEP — 249 of
        # them for 2026-08-14 — and the caller's question is "when did the chain
        # start", asked once per firing. Left uncollapsed, every step after the
        # first reads as a separate re-run and the row would report 248 of them.
        # A chain's steps run minutes apart and its firings hours apart, so an
        # hour of silence is an unambiguous boundary; the first start of each
        # cluster is that firing's attempt time.
        firings = [rows[0]]
        prev = rows[0]
        for t in rows[1:]:
            if t - prev > 3600:      # an hour of silence ends the previous chain
                firings.append(t)
            prev = t
        return firings
    except Exception:
        # Absent credential, unapplied migration, unreachable Neon — all mean
        # "ask the log", never "the job did not run".
        return None


def attempt_starts(path, marker):
    """Every ATTEMPT start in the log, as local epochs, oldest-first. The marker is the
    line the job writes on ENTRY, so these are firings, not completions. Marker lines
    lead with an ISO-8601 UTC stamp: `2026-08-13T07:05:05Z  ===== nightly chain begin
    =====`. Returns None when the marker is unreadable — the caller must say so rather
    than fall back to mtime silently, because mtime answers a different question."""
    found = []
    try:
        with open(path, errors="replace") as fh:
            for ln in fh:
                if marker not in ln:
                    continue
                parts = ln.split()
                if not parts:
                    continue
                try:
                    parsed = time.strptime(parts[0], "%Y-%m-%dT%H:%M:%SZ")
                except ValueError:
                    continue
                found.append(calendar.timegm(parsed))   # stamp is UTC
    except OSError:
        return None
    return sorted(found) or None


def scheduled_window(want_hour, now=None):
    """Epoch of the most recent time the job was DUE — today at want_hour, or yesterday
    if today's has not come round yet."""
    now = time.time() if now is None else now
    lt = time.localtime(now)
    due = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, want_hour, 0, 0, 0, 0, -1))
    return due if due <= now else due - 86400


print("Schedule drift — did the job START when scheduled (not: did it succeed)")
for name, out_pat, want_hour, tol_h, marker, stale_h, wake_note, svc_key in SCHEDULE:
    # THE LEDGER FIRST. It is the only source here that cannot be trimmed, and
    # the log's trimming is what produced two wrong readings of this row.
    starts = attempt_starts_from_ledger(svc_key)
    basis = "job ledger"
    if starts is None:
        # SCHEDULE watches job artefacts, which may sit in the repo rather than the vault.
        out = (newest(out_pat) if not out_pat.startswith(("~", "/"))
               else (lambda h: h if os.path.exists(h) else None)(os.path.expanduser(out_pat)))
        if not out:
            print(f"  MISSING {name:<22} the job ledger is unreachable and no file "
                  f"matches {out_pat} — nothing here can say when this last ran")
            rc = 1
            continue
        starts = attempt_starts(out, marker)
        basis = f"{os.path.basename(out)}, which gets trimmed — treat with suspicion"
        if not starts:
            print(f"  -- {name:<22} the job ledger is unreachable and {os.path.basename(out)} "
                  f"holds no '{marker}' marker — the last ATTEMPT cannot be read, and this "
                  f"row refuses to substitute the file's mtime, which is its last WRITE and "
                  f"a different question")
            rc = 1
            continue
    # MEASURE AGAINST THE WINDOW, NOT AGAINST THE NEWEST LINE. A hand re-run writes its
    # own marker, so "newest attempt" would hand a midday rescue the same false positive
    # the mtime reading produced. The question is whether SOME attempt landed near the
    # last scheduled firing; later re-runs are recovery and belong to the result row.
    due = scheduled_window(want_hour)
    closest = min(starts, key=lambda e: abs(e - due))
    drift = abs(closest - due) / 3600.0
    lt = time.localtime(closest)
    reruns = sum(1 for e in starts if e > closest)
    tail = f" · {reruns} later re-run(s), which this row ignores by design" if reruns else ""
    # NEVER FIRED is its own verdict, and the only one the wake schedule explains.
    if (time.time() - starts[-1]) / 3600.0 > stale_h:
        print(f"  ⚠︎ {name:<22} NEVER FIRED — newest attempt began "
              f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(starts[-1]))}, "
              f"{(time.time() - starts[-1]) / 3600.0:.1f}h ago against a ~{stale_h}h "
              f"cadence  · read from the {basis}  · {wake_note}")
        rc = 1
    elif drift > tol_h:
        print(f"  ⚠︎ {name:<22} FIRED LATE — nearest attempt to the "
              f"{time.strftime('%m-%d %H:%M', time.localtime(due))} window began "
              f"{time.strftime('%H:%M', lt)}, {drift:.1f}h drift  · read from the "
              f"{basis}  · {wake_note}{tail}")
        rc = 1
    else:
        print(f"  OK {name:<22} fired {time.strftime('%H:%M', lt)}, within {tol_h}h of "
              f"~{want_hour:02d}:00 per the {basis} (whether it SUCCEEDED is the next "
              f"row){tail}")
# --- did the chain WORK, not merely run (added 2026-08-10) -------------------
# The drift row above answers "did it run on time" and nothing else, which is the
# same defect the backup guard had: a success signal that never looks at the
# thing it reports on. On 2026-08-10 the chain had been exiting non-zero for
# three nights — the mypy tripwire since 08-08 — and every surface Joe had said
# fine. The three Healthchecks dead-man pings each report on ONE named step
# (exports, backup, worker), so a failure anywhere else pings nothing at all.
#
# CONSECUTIVE COUNT, not a bare red. A check that is chronically red detects
# nothing, which is exactly how the settings wipe hid behind an already-red
# config row on 2026-08-08. Naming the streak separates "this broke last night"
# from "this has been broken all week and nobody looked".
_nightly_log = os.path.expanduser("~/carr-system/out/nightly.log")
# PARSED ON COMPLETION LINES, NOT ON "chain begin", and the difference is not
# cosmetic. Two chains can overlap in this log — it happened on 2026-08-10, two
# runs 44 seconds apart — and anchoring each FAIL to the most recent begin then
# hands every failure to the second run and leaves the first with no verdict at
# all, which under-counts the streak and reports a chronic red as a first
# failure. Attributing the FAILs seen since the previous verdict to the run that
# just ended is the honest reading under interleaving: it can over-attribute
# between two overlapping runs, never lose one.
try:
    # BLOCKED is tracked beside FAIL, never folded into it (2026-08-21). A step
    # that refused for want of a canonical seam did not run, so reporting the
    # chain clean and saying nothing else would be the false-healthy reading the
    # refusal script warned about. It is not a failure either — see the argument
    # at the 69 branch in bin/nightly.sh. So it gets its own count, printed
    # beside the verdict on every line below, and it never sets rc.
    # TOMBSTONE is tracked beside BLOCKED for the reason BLOCKED is tracked
    # beside FAIL, one step further along (2026-08-23). A tombstoned step was
    # gated out before the chain ran it, so blocked-count going to zero must not
    # be readable as "the seams got built" — the debt moved from one column to
    # another and this row has to show both or it reports a fix that did not
    # happen. Neither count ever sets rc: a known, named, filed debt is not
    # tonight's alarm. What IS tonight's alarm is a step failing, which still is.
    _done: list[tuple[bool, list[str], list[str], list[str]]] = []
    _pending: list[str] = []
    _blocked: list[str] = []
    _tombs: list[str] = []
    _begins = _overlaps = 0
    _open = 0
    for _ln in open(_nightly_log, errors="replace"):
        if "chain begin" in _ln:
            _begins += 1
            _open += 1
            if _open > 1:
                _overlaps += 1
        elif "  FAIL  " in _ln:
            _pending.append(_ln.split("  FAIL  ", 1)[1].strip())
        elif "  BLOCKED  " in _ln:
            _blocked.append(_ln.split("  BLOCKED  ", 1)[1].strip())
        elif "  TOMBSTONE  " in _ln:
            _tombs.append(_ln.split("  TOMBSTONE  ", 1)[1].strip())
        else:
            _outcome = _health_sub.nightly_completion(_ln)
            if _outcome is None:
                continue
            _done.append((_outcome, _pending, _blocked, _tombs))
            _pending = []
            _blocked = []
            _tombs = []
            _open = max(0, _open - 1)
    if not _done:
        print(f"  -- {'nightly chain result':<22} no completed run in the log — the chain "
              f"has not finished since the log was last trimmed")
    else:
        _last_ok, _last_fails, _last_blocked, _last_tombs = _done[-1]
        _bl_names = sorted({s.split(" (exit")[0] for s in _last_blocked})
        _bl = (f"  · {len(_bl_names)} step(s) BLOCKED on a missing canonical seam, "
               f"not counted as failures: {', '.join(_bl_names)}" if _bl_names else "")
        # The label is everything before the " — gated out" the chain writes, so
        # the row names the steps rather than reprinting every reopen condition.
        # The conditions themselves are one grep of out/nightly.log away and this
        # row has to stay one line.
        _tb_names = sorted({s.split(" — gated out")[0] for s in _last_tombs})
        _bl += (f"  · {len(_tb_names)} step(s) TOMBSTONED, gated out rather than run: "
                f"{', '.join(_tb_names)} — each names what it is missing in "
                f"out/nightly.log" if _tb_names else "")
        if _last_ok:
            print(f"  OK {'nightly chain result':<22} last run exited clean, all steps OK{_bl}")
        else:
            # how many consecutive completed runs, newest-first, ended red
            _streak = 0
            for _ok, _, _, _ in reversed(_done):
                if _ok:
                    break
                _streak += 1
            _labels = sorted({s.split(" (exit")[0] for s in _last_fails})
            _age = "FIRST FAILURE" if _streak == 1 else f"{_streak} runs in a row"
            _note = (f" · {_overlaps} overlapping run(s) in this log, so step names may be "
                     f"attributed to the wrong one" if _overlaps else "")
            print(f"  ⚠︎ {'nightly chain result':<22} last run FAILED ({_age}) — "
                  f"{', '.join(_labels) or 'step name not in the log'}  · read "
                  f"out/nightly.log; a chain red for several runs is one nobody is reading, "
                  f"which is the failure this row exists to catch{_note}{_bl}")
            rc = 1
except OSError as _exc:
    print(f"  -- {'nightly chain result':<22} cannot read {_nightly_log} ({_exc})")

# --- work sitting outside git (added 2026-08-10) -----------------------------
# Joe asked whether a routine should force every session to commit on a timer.
# The answer was no: `git commit -a` on a schedule is the operation
# git-writer-gate.py was built to block on 2026-08-09, after a sweep took another
# session's in-flight files and cost an hour of rebuilding. A timer cannot tell
# whose file is whose, cannot tell finished code from a half-applied edit, and
# replaces the commit message — which on that very incident was the most
# valuable part of the change — with "auto-commit".
#
# REPORT, NEVER ENFORCE, which is the house posture for exactly this shape
# (v_drip_conflict, v_loop_bell_cap and the deprecation register all detect and
# prompt rather than act). The real fix for concurrent writers is worktree
# isolation, already accepted in loop #195; this row is the cheap half that makes
# loose work visible in the meantime.
#
# THE CLOCK RUNS ON TRACKED-MODIFIED FILES ONLY. Managed generated artifacts
# and verified worktrees stay informational, while an UNKNOWN untracked path is
# actionable immediately: it could be source awaiting a named disposition and
# must not hide behind a tracked-edit grace period.
#
# 12 HOURS, because it crosses a night. A session running four or six hours is
# ordinary here and must not nag; work still loose the next morning is the thing
# actually worth seeing.
_STALE_H = 12
# Hours since the NEWEST loose file changed, past which nothing here can be
# called in-flight. Two, not one: a session can legitimately think, research or
# sit in a browser for a while between writes.
_IDLE_H = 2
try:
    _gs = subprocess.run(["git", "status", "--porcelain"], cwd=REPO_ROOT,
                         capture_output=True, text=True, timeout=20)
    if _gs.returncode != 0:
        print(f"  -- {'uncommitted work':<22} git status failed — cannot tell what is loose")
    else:
        _loose = _health_sub.classify_loose_status(REPO_ROOT, _gs.stdout.splitlines())
        _tracked: list[tuple[float, str]] = []
        for _gpath in _loose["actionable_tracked"]:
            _full = os.path.join(REPO_ROOT, _gpath)
            try:
                _tracked.append((os.path.getmtime(_full), _gpath))
            except OSError:
                continue                  # deleted or renamed away; not loose work
        _extra = (f" · {len(_loose['actionable_untracked'])} actionable untracked"
                  if _loose["actionable_untracked"] else "")
        if _loose["expected_patched_submodules"]:
            _extra += (f" · {len(_loose['expected_patched_submodules'])} vendored submodule(s) "
                       f"dirty from their own tracked patches, which is expected")
        if _loose["managed_artifacts"]:
            _extra += f" · {len(_loose['managed_artifacts'])} managed artifact(s)"
        if not _tracked:
            _needs_attention = _health_sub.loose_work_requires_attention(_loose, False)
            if _needs_attention:
                print(f"  ⚠︎ {'uncommitted work':<22} 0 tracked file(s) loose{_extra}"
                      " · actionable untracked source needs a named disposition")
                rc = 1
            else:
                print(f"  OK {'uncommitted work':<22} nothing tracked is loose{_extra}")
        else:
            _tracked.sort()
            _oldest_h = (time.time() - _tracked[0][0]) / 3600.0
            # THE NEWEST file's age is the liveness signal, and the first version
            # of this row did not have one. It reported anything under 12h as
            # reading "like a live session rather than abandoned work" — an
            # inference about a cause it could not observe, stated as fact. Joe
            # caught it: the sessions were all idle, so a 7.6h file was abandoned
            # exactly like the 13h ones. If even the NEWEST loose file has not
            # been touched in hours, nobody is mid-edit on any of them, whatever
            # the process list says.
            _newest_h = (time.time() - _tracked[-1][0]) / 3600.0
            _names = ", ".join(p for _, p in _tracked[:3])
            _more = f" (+{len(_tracked) - 3} more)" if len(_tracked) > 3 else ""
            _tracked_attention = _oldest_h >= _STALE_H or _newest_h >= _IDLE_H
            _needs_attention = _health_sub.loose_work_requires_attention(
                _loose, _tracked_attention)
            _why = ("actionable untracked source needs a named disposition"
                    if _loose["actionable_untracked"]
                    else "past a night outside git" if _oldest_h >= _STALE_H
                    else f"nothing here has been touched in {_newest_h:.1f}h, so none of it "
                         f"is mid-edit")
            if _needs_attention:
                print(f"  ⚠︎ {'uncommitted work':<22} {len(_tracked)} tracked file(s) loose, "
                      f"oldest {_oldest_h:.0f}h / newest {_newest_h:.1f}h — {_names}{_more}"
                      f"{_extra}  · {_why}. Commit by NAMING PATHS (never a sweep, which takes "
                      f"another writer's files), or say why it is deliberately held")
                rc = 1
            else:
                print(f"  -- {'uncommitted work':<22} {len(_tracked)} tracked file(s) loose, "
                      f"oldest {_oldest_h:.1f}h / newest {_newest_h:.1f}h — {_names}{_more}"
                      f"{_extra}  · under {_STALE_H}h and touched within {_IDLE_H}h, which is "
                      f"consistent with work in progress")
except Exception as _exc:
    print(f"  -- {'uncommitted work':<22} not checked ({_exc})")

# The scheduler's own store is not readable from a script (see the scheduler register at
# the top of this file), so this run cannot judge the other 14 tasks. It says so instead
# of staying quiet, because silence here reads as "all clear" for tasks nobody checked.
print(f"  -- {'scheduled tasks':<22} not checked here — cron and lastRunAt live in the app "
      f"store. Paste `list_scheduled_tasks` output to a file and run "
      f"`python3 tools/health-check.py --tasks <file>`; a task whose first window has not "
      f"arrived reports AWAITING FIRST, not a fault")

# --- deprecation register (added 2026-08-02) ---------------------------------
# Joe, on the 0048 compatibility shim: "i dont want bloat in the system but if it makes
# sense to create a 'self-healing' component to this so that it slowly erases the old way".
# Self-healing here means DETECT AND PROMPT, never auto-drop: dropping is irreversible and
# cannot be verified beforehand, detecting is free. Same posture as v_drip_conflict and
# v_loop_bell_cap — report, never enforce.
#
# Answers the only question that matters about a shim: is anything still using it, and can
# I delete it yet? Greps the repo rather than guessing. KNOWN GAP, stated rather than
# assumed away: this sees THIS REPO only. A Cowork session or a script on Dell's Mac would
# not show up; pg_stat_statements would catch those and is available but not installed.
print("Deprecation register — what is kept alive only for compatibility")
import subprocess
_q = ("select object_name, object_kind, coalesce(replaced_by,''), "
      "coalesce(safe_to_drop_after::text,'') from deprecation where dropped_at is null;")
_p = subprocess.run([os.path.join(REPO_ROOT, ".venv/bin/python"),
                     os.path.join(REPO_ROOT, "tools/db-tap.py"), "sql", "/dev/stdin"],
                    input=_q, capture_output=True, text=True, timeout=90)
if _p.returncode != 0:
    # A FAILED READ IS NOT AN EMPTY REGISTER. The first cut of this check passed "-" as the
    # filename, db-tap rejected it, and the empty output printed as "none outstanding" — a
    # detector reporting all-clear because it could not see, which is the exact defect 0034
    # was written to name. It is now loud.
    print(f"  \u26a0\ufe0e register UNREADABLE — cannot say whether anything is deprecated "
          f"({(_p.stderr or '').strip().splitlines()[-1] if _p.stderr.strip() else 'no stderr'})")
    rc = 1
else:
    _rows = []
    for _line in _p.stdout.splitlines():
        if "|" not in _line or "object_name" in _line or "---" in _line or "row" in _line:
            continue
        _c = [c.strip() for c in _line.split("|")]
        if len(_c) >= 4 and _c[0]:
            _rows.append(_c)
    if not _rows:
        print("  OK none outstanding (register read successfully)")
    # PROSE IS NOT A DEPENDENCY, and the first version of this check did not know it.
    # On 2026-08-02 it held `prospect_pool` open on the strength of ONE line: an awk
    # comment in bin/restore-rehearse.sh narrating the 0048 rename as the clue that
    # proved the restore drill's eight "failures" were schema evolution and not data
    # loss. Deleting that sentence to satisfy a grep trades a true piece of history for
    # a green line; leaving it holds the shim alive forever. Neither is right, because
    # the CHECK was wrong: a name inside a comment executes nothing and breaks nothing
    # when the object is dropped. Comment-only lines are now excluded and the match is
    # word-bounded (`prospect_pool` no longer matches `v_prospect_pool_x`). A file that
    # cannot be read counts as a live reference — same posture as the UNREADABLE branch
    # above, because a check that cannot see must never report all-clear.
    # PERFORMANCE, added 2026-08-09 by the system-design council. This grep ran
    # once per deprecated object with no --exclude-dir, over a repo that is 8.0 GB
    # (5.7 GB of it ML virtualenvs under tools/doc-convo). Seven full walks took
    # `run.sh health` to ~8 minutes, and a daily check that takes eight minutes
    # stops being run daily — which is exactly what happened: nothing automated
    # calls it, and the one cloud task that does skipped two days this week.
    # Excluding the venvs, node_modules, .git, out/ and the in-tree worktrees is
    # correct on the merits too: none of them is executable source this check is
    # meant to judge, and the worktrees hold two diverged FULL COPIES of the repo,
    # so every count this produced was inflated up to 3x on any file that exists
    # in them.
    _SKIP_DIRS = ["--exclude-dir=.venv", "--exclude-dir=.venv-*",
                  "--exclude-dir=node_modules", "--exclude-dir=.git",
                  "--exclude-dir=out", "--exclude-dir=worktrees",
                  "--exclude-dir=.claude", "--exclude-dir=_to_delete"]

    def _live_refs(_n):
        _h = subprocess.run(["grep", "-rlw", _n, REPO_ROOT, "--include=*.py",
                             "--include=*.js", "--include=*.sh", *_SKIP_DIRS],
                            capture_output=True, text=True)
        _out = []
        for _f in _h.stdout.splitlines():
            if ("/migrations/" in _f or "node_modules" in _f or "/corpus/" in _f
                    or f"import_{_n}" in _f):
                continue
            # A WATCHER NAMING A FILE IS NOT A CONSUMER OF IT. Added 2026-08-09,
            # same council pass. Five of the six deprecation rows warned solely
            # because THIS file's own WATCH list holds those filenames, and
            # parity-lead-board.py is the test harness that dies with them. The
            # check was its own dependency, so the register could never go green
            # and had printed the identical six warnings since 2026-08-02. That
            # is not a harmless cosmetic: a row that is chronically red detects
            # nothing, and this system has already been bitten by it once — on
            # 2026-08-08 a plugin install deleted the entire hooks block and the
            # catastrophic wipe printed the same headline as a benign stale row,
            # so all five gates were off for a day and it was found by accident.
            if os.path.basename(_f) in ("health-check.py", "parity-lead-board.py"):
                continue
            try:
                _lines = open(_f, errors="replace").read().splitlines()
            except OSError:
                _out.append(_f)
                continue
            if any(re.search(rf"\b{re.escape(_n)}\b", _l)
                   and not _l.lstrip().startswith(("#", "//", "--", "*"))
                   for _l in _lines):
                _out.append(_f)
        return _out

    for _name, _kind, _repl, _after in _rows:
        _files = _live_refs(_name)
        _due = bool(_after) and _after <= time.strftime("%Y-%m-%d")
        if not _files:
            _flag = "SAFE TO DROP" if _due else f"unused; scheduled {_after or 'no date'}"
            print(f"  OK {_name:<22} {_kind}, 0 executable refs (comments ignored) — {_flag}"
                  + (f"  (replaced by {_repl})" if _repl else ""))
        else:
            # SELF-HEAL MESSAGE (loop #240 item 1). Every other non-OK row on this
            # surface already tells the reader what to do about it; this one named a
            # problem and stopped, which is the shape that trains a reader to skim
            # past a warning. A row an agent cannot act on unaided is a row that
            # stays amber forever.
            #
            # It names the REPLACEMENT when one is registered, because that is the
            # whole content of the remedy \u2014 repoint these callers at it \u2014 and says
            # plainly when there is none, since "deprecated with nothing to move to"
            # is a different and more expensive problem than a pending repoint.
            _paths = ", ".join(os.path.basename(f) for f in _files[:4])
            _more = f" (+{len(_files) - 4} more)" if len(_files) > 4 else ""
            print(f"  \u26a0\ufe0e {_name:<22} {_kind}, still referenced by {len(_files)} file(s): "
                  + _paths + _more)
            if _repl:
                print(f"       FIX: repoint those callers at {_repl}, then re-run "
                      f"`./run.sh health` \u2014 this row clears itself once the last "
                      f"executable reference is gone (comments do not count).")
            else:
                print(f"       FIX: no replacement is registered, so this cannot be "
                      f"repointed yet. Either register the replacement in the "
                      f"deprecation register, or drop the deprecation if "
                      f"{_name} is in fact still the live path."
                      + (f" Scheduled removal: {_after}." if _after else
                         " No removal date is set, which is why it has no deadline to hit."))
            rc = 1

# --- the export register (added 2026-08-02) ----------------------------------
# WHY THIS EXISTS: v_integrity_digest's `export_freshness` is built by grouping
# export_run BY TARGET, so every target that ever wrote a row stays in it forever.
# Two targets — decision-history.md and loop-idea-bank.md — were registered, failed
# every run because the files they render are STILL HAND-MAINTAINED, and were then
# deliberately unregistered. Their failed rows remain, so the digest reports
# {"stale": null, "last_ok": null} for both. A null there reads as "no data yet" or
# "something is broken", and it is neither: those two are not export targets, on
# purpose, and nothing is wrong.
#
# The view cannot be repaired from here (changing it needs a migration, which this
# session is not writing; the proposed SQL is filed at specs/v_integrity_digest-
# unregistered-targets.sql as a handoff). What CAN be fixed is the reading surface:
# this section joins the same export_run data against the LIVE exporter registry and
# names each state instead of leaving a null to be interpreted.
#
#   NOT A TARGET   rows exist, the key is not in exporters.targets.TARGETS. Expected
#                  null in the digest. Informational, never red.
#   NEVER OK       the key IS registered and has no successful run. A real defect.
#   STALE          registered, last ok older than 26h — the nightly chain missed it.
#   NEVER RAN      registered with no export_run row at all.
print("Export register — a target nobody registered is not a target that failed")
# DELIBERATELY NOT THROUGH db-tap. The deprecation register above reads production via
# neonctl, and on 2026-08-02 the neonctl OAuth token expired mid-session and took every
# db-tap call down with it. That is precisely the moment a health check needs to keep
# working. export_run is inside the exporter credential's own grant, so this section
# reads it the same way the exporters do: a local DSN from ~/.config/carr/db.env, no
# browser token, no interactive auth. One subprocess does both halves (import the live
# registry, query the table) so the registry and the rows are read by the same process.
_probe = r'''
import sys
from exporters.targets import TARGETS
from exporters.common import connect
from exporters.run_exports import md_renders_retired
print("REG\t" + "\t".join(sorted(TARGETS)))
# THE CUTOFF (fired 2026-08-19). A retired .md target stops producing export_run
# rows entirely, so 26 hours later every one of them reads STALE — 38 amber lines
# prescribing a re-export of targets the exporter now refuses to write. Emitted as
# its own class so the row keeps meaning "the nightly chain missed something".
# Same flag function the exporter gates on: one contract, no second opinion.
if md_renders_retired():
    print("RETIRED\t" + "\t".join(sorted(
        k for k, (rel, _fn) in TARGETS.items() if rel.lower().endswith(".md"))))
with connect() as c, c.cursor() as cur:
    cur.execute("""select target,
                          coalesce(max(ran_at) filter (where status='ok')::text,''),
                          coalesce((array_agg(status order by ran_at desc))[1],'')
                     from export_run group by target""")
    for t, lastok, st in cur.fetchall():
        print("ROW\t%s\t%s\t%s" % (t, lastok, st))
'''
_ep = subprocess.run([os.path.join(REPO_ROOT, ".venv/bin/python"), "-c", _probe],
                     cwd=REPO_ROOT, capture_output=True, text=True, timeout=120)
if _ep.returncode != 0:
    # Same rule as the deprecation register: a read that failed is not a clean register.
    _tail = (_ep.stderr or "").strip().splitlines()
    print(f"  ⚠︎ register UNREADABLE — cannot classify any export target "
          f"({_tail[-1] if _tail else 'no stderr'})")
    rc = 1
else:
    _registered, _seen, _bad, _retired = set(), {}, [], set()
    for _line in _ep.stdout.splitlines():
        _c = _line.split("\t")
        if _c[0] == "REG":
            _registered = {x for x in _c[1:] if x}
        elif _c[0] == "RETIRED":
            _retired = {x for x in _c[1:] if x}
        elif _c[0] == "ROW" and len(_c) >= 4:
            _seen[_c[1]] = (_c[2], _c[3])
    _registered -= _retired
    _unreg = sorted(k for k in _seen if k not in _registered and k not in _retired)
    if _retired:
        print(f"  -- RETIRED       {len(_retired)} md render target(s) ended at the "
              f"2026-08-19 cutoff — the exporter prints RETIRED instead of writing "
              f"them, so no run row is the correct state rather than a missed chain")
    for _k in _unreg:
        _lastok, _status = _seen[_k]
        print(f"  -- NOT A TARGET  {_k:<26} no such key in exporters.targets.TARGETS "
              f"(last attempt: {_status or 'none'}"
              + (f", last ok {_lastok[:16]}Z" if _lastok else ", never ok")
              + ") — nothing exports it, so the digest's null for this key is "
                "correct rather than a fault")
    for _k in sorted(_registered):
        if _k not in _seen:
            _bad.append(f"NEVER RAN {_k}")
            continue
        _lastok, _status = _seen[_k]
        if not _lastok:
            _bad.append(f"NEVER OK {_k} (latest run: {_status or 'unknown'})")
        elif (time.time() - time.mktime(time.strptime(_lastok[:19], "%Y-%m-%d %H:%M:%S"))
              + time.timezone) > 26 * 3600:
            _bad.append(f"STALE {_k} (last ok {_lastok[:16]}Z)")
    for _b in _bad:
        print(f"  ⚠︎ {_b}")
        rc = 1
    if not _bad:
        print(f"  OK {len(_registered)} registered target(s), all with a successful run "
              f"inside 26h; {len(_unreg)} unregistered key(s) held in history only")

# --- the R2 archive quota (added 2026-07-31, ORDER 20c) ----------------------
# NOT a freshness check, and it is here rather than in WATCH for that reason.
# The ledger only moves when a document is archived, so an old ledger is normal
# and proves nothing. What matters is the NUMBER creeping toward the cap: Joe's
# requirement is a hard self-enforced quota, and a hard cap that nobody watches
# turns into a refusal on the day it matters. This row is how the heartbeat sees
# it coming years out. Read-only, file-only: no network call and no credential,
# so the heartbeat never fails because Cloudflare was slow.
GB = 1024 ** 3
DEFAULT_QUOTA_GB = 8          # mirrors lib/r2_archive.py when system_config is unset
WARN_AT = 0.80
R2_LEDGER = os.path.expanduser("~/carr-system/out/r2-usage.json")
try:
    import json
    with open(R2_LEDGER) as fh:
        _led = json.load(fh)
    used = sum(int(o.get("bytes", 0)) for o in _led.get("objects", {}).values())
    cap = float(_led.get("quota_gb_last_seen") or DEFAULT_QUOTA_GB) * GB
    pct = used / cap if cap else 0
    n = len(_led.get("objects", {}))

    def _h(b):                       # the unit that keeps the number legible
        for unit, size in (("GB", GB), ("MB", 1024 ** 2), ("KB", 1024)):
            if abs(b) >= size:
                return f"{b / size:,.2f} {unit}"
        return f"{int(b):,} bytes"

    line = f"{_h(used)} of {_h(cap)} ({pct * 100:.1f}%), {n} objects"
    if pct >= 1:
        print(f"  ⚠︎ {'R2 archive':<18} OVER QUOTA: {line}  · uploads are being REFUSED; "
              f"raise system_config r2.quota_gb or purge archived documents")
        rc = 1
    elif pct >= WARN_AT:
        print(f"  ⚠︎ {'R2 archive':<18} {line}  · past {WARN_AT:.0%} of the self-enforced cap; "
              f"decide before the pipeline has to refuse")
        rc = 1
    else:
        print(f"  OK {'R2 archive':<18} {line}")
except FileNotFoundError:
    # Not a failure. Before the first document is archived there is nothing to
    # count, and inventing an alarm for that would train everyone to ignore this
    # line by the time it means something.
    print(f"  -- {'R2 archive':<18} no ledger yet ({R2_LEDGER}); nothing archived so far")
except (ValueError, OSError, KeyError) as e:
    print(f"  ⚠︎ {'R2 archive':<18} ledger unreadable ({type(e).__name__}); the quota guard "
          f"rebuilds it from the bucket on the next archive run")
    rc = 1

# --- the doctrine corpus mirror (added 2026-07-31, ORDER 30c) ----------------
# Also not a freshness check, and file-only by design: it hashes 34 small text
# files on local disk plus their Drive originals. No network, no credential, no
# database, so the heartbeat can never fail here for being offline.
# TWO FINDINGS, and they mean opposite things:
#   the Drive moved on  — normal. Doctrine changed and the mirror has not caught
#     up. It warns (rc=1) rather than staying quiet because an un-synced mirror
#     is a mirror with a hole in its history, and closing it is one command. If
#     this proves noisy in practice, downgrading it to informational is a
#     one-line change — flagged in ORDER 30's log rather than decided here.
#   the mirror was edited — a defect. Nothing reads corpus/, so that edit reaches
#     no session, and the next --sync would erase it. The Drive is canonical.
CORPUS_SYNC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "corpus-sync.py")
try:
    import importlib.util
    _spec = importlib.util.spec_from_file_location("corpus_sync", CORPUS_SYNC)
    if _spec is None:
        raise RuntimeError(f"cannot create import spec for {CORPUS_SYNC}")
    _cs = importlib.util.module_from_spec(_spec)
    if _spec.loader is None:
        raise RuntimeError(f"import spec for {CORPUS_SYNC} has no loader")
    _spec.loader.exec_module(_cs)
    _st = _cs.status()
    if not _st["manifest"]:
        print(f"  -- {'corpus mirror':<18} no manifest yet; run python3 tools/corpus-sync.py --sync")
    else:
        _c = _st["counts"]
        _loud = sum(_c.get(k, 0) for k in ("MIRROR-EDITED", "BOTH-CHANGED"))
        _behind = _c.get("DRIFT", 0) + _c.get("NEW", 0) + _c.get("MISSING-MIRROR", 0)
        _gone = _c.get("SOURCE-GONE", 0) + _c.get("UNREADABLE", 0)
        _n = _st["manifest_count"]
        if _loud:
            print(f"  ⚠︎ {'corpus mirror':<18} {_loud} file(s) EDITED IN THE MIRROR  · the Drive is "
                  f"canonical and nothing reads corpus/; move the edit to the Drive "
                  f"(tools/corpus-sync.py names them)")
            rc = 1
        elif _behind or _gone:
            _bits = []
            if _behind:
                _bits.append(f"{_behind} newer on the Drive")
            if _gone:
                _bits.append(f"{_gone} missing/unreadable at the source")
            print(f"  ⚠︎ {'corpus mirror':<18} {', '.join(_bits)} of {_n}  · "
                  f"run python3 tools/corpus-sync.py --sync")
            rc = 1
        else:
            print(f"  OK {'corpus mirror':<18} {_n} doctrine files, 0 drift")
except FileNotFoundError:
    print(f"  -- {'corpus mirror':<18} tools/corpus-sync.py not present; corpus check skipped")
except Exception as e:
    print(f"  ⚠︎ {'corpus mirror':<18} check failed ({type(e).__name__}: {e})")
    rc = 1

# ── machine config vs the repo (added 2026-08-03) ────────────────────────────
# Joe: "shouldnt all code be in the repo? .json is code". He was right, and the
# exposure was wider than the file he named: the five hook SCRIPTS were version
# controlled while the settings.json block that makes them RUN was not, along
# with both launchd plists and 11 of 15 scheduled tasks.
#
# The reason this is a CHECK and not just a one-time commit: hooks/SETTINGS-BLOCK.md
# already existed as the written record of that config, and it had silently
# drifted — it documented two hooks while four were live, and nothing noticed
# because nothing compared them. A document DESCRIBING config drifts. A check
# COMPARING config cannot. Same rule-28 logic as everything above: verify by
# output, never by the artifact existing.
try:
    _cac = os.path.join(REPO_ROOT, "ops", "config-as-code.py")
    if not os.path.exists(_cac):
        print(f"  -- {'machine config':<18} ops/config-as-code.py not present; skipped")
    else:
        _p = subprocess.run([sys.executable, _cac, "check"],
                            capture_output=True, text=True, timeout=30)
        _lines = (_p.stdout or "").strip().splitlines()
        _first = _lines[0] if _lines else "(no output)"
        if _p.returncode == 0:
            print(f"  OK {'machine config':<18} {_first.split('— ', 1)[-1]}")
        elif "PREREQUISITES MISSING" in _first:
            print(f"  ✗✗ {'machine setup':<18} {_first.split(': ', 1)[-1]}")
            print(f"     {'':<18} config-as-code lists each missing dependency and its fix")
            rc = 1
        elif "MISSING FROM MACHINE" in _first:
            # The 2026-08-08 case: a plugin install deleted the hooks block from
            # ~/.claude/settings.json and all five gates stopped running. The old
            # row printed the same "DRIFT — N of 28" it had been printing since a
            # benign matcher change two days earlier, so nothing distinguished
            # "baseline lagged" from "every protection is off". Missing gets its
            # own louder row, and the bound action prints INLINE (rule 590b11e1)
            # rather than sending the reader to another command to find out.
            print(f"  ✗✗ {'machine config':<18} {_first.split(': ', 1)[-1]}")
            print(f"     {'':<18} on breach: config-as-code.py install --apply, "
                  f"then prove a denial fires")
            rc = 1
        else:
            print(f"  ⚠︎ {'machine config':<18} {_first.split(': ', 1)[-1]}  · "
                  f"on breach: config-as-code.py pull --apply (baseline lagged a live change)")
            rc = 1
except Exception as e:
    print(f"  ⚠︎ {'machine config':<18} check failed ({type(e).__name__}: {e})")
    rc = 1

# ── the egress guard: is its LOGIC right, and is its DATA fresh (2026-08-09) ──
# Two rows, because they fail independently and the 2026-08-08 incident turned on
# exactly that distinction. `machine config` above answers "is the guard
# REGISTERED". These answer "does it still deny what it claims to" and "does it
# know about the clients added this week". A green on any one of the three says
# nothing about the other two.
try:
    _gst = os.path.join(REPO_ROOT, "ops", "guard-selftest.py")
    if os.path.exists(_gst):
        _p = subprocess.run([sys.executable, _gst], capture_output=True, text=True, timeout=120)
        _sum = next((l for l in (_p.stdout or "").splitlines() if "guard-selftest:" in l), "")
        _sum = _sum.split(": ", 1)[-1] if _sum else "(no output)"
        if _p.returncode == 0:
            print(f"  OK {'egress guard':<18} {_sum} · on breach: a denial the guard "
                  f"promises is not firing; read the FAILED list before any research run")
        else:
            print(f"  ✗✗ {'egress guard':<18} {_sum} · on breach: a denial the guard "
                  f"promises is NOT firing; python3 ops/guard-selftest.py -v")
            rc = 1
except Exception as e:
    print(f"  ⚠︎ {'egress guard':<18} selftest failed ({type(e).__name__}: {e})")
    rc = 1

# A THIRD INDEPENDENT ROW (2026-08-10, loop #231). The egress row above proves
# the guard still denies network and render writes. It says nothing about whether
# the GATES THEMSELVES are still protected, which is a separate claim that was
# false for three days: gate-edit-gate.py guarded Write/Edit while the shell path
# was wide open, and its own docstring asserted otherwise. The fixtures for that
# suite had also gone chronically red — 15/27 — after the gate was downgraded to
# announce and nobody moved them, so nothing would have reported the regression.
# A suite nobody runs is not a check, which is why it gets a row here.
try:
    _ggt = os.path.join(REPO_ROOT, "ops", "gate-edit-gate-selftest.py")
    if os.path.exists(_ggt):
        _p = subprocess.run([sys.executable, _ggt], capture_output=True, text=True, timeout=180)
        _sum = next((l for l in (_p.stdout or "").splitlines()
                     if "gate-edit-gate-selftest:" in l), "")
        _sum = _sum.split(": ", 1)[-1] if _sum else "(no output)"
        if _p.returncode == 0:
            print(f"  OK {'gate protection':<18} {_sum} · on breach: a gate can be "
                  f"changed SILENTLY; python3 ops/gate-edit-gate-selftest.py")
        else:
            print(f"  ✗✗ {'gate protection':<18} {_sum} · on breach: a gate can be "
                  f"changed SILENTLY — the announcement both doors promise is not "
                  f"firing; python3 ops/gate-edit-gate-selftest.py")
            rc = 1
except Exception as e:
    print(f"  ⚠︎ {'gate protection':<18} selftest failed ({type(e).__name__}: {e})")
    rc = 1

# Rule coverage is deliberately a category map, not a gate count.  A green
# result means every active rule is classified once and every non-advisory rule
# names a concrete control/test; it does NOT claim all rules are hard-blocked.
try:
    _rem = os.path.join(REPO_ROOT, "ops", "rule-enforcement-map-check.py")
    if os.path.exists(_rem):
        _p = subprocess.run([sys.executable, _rem], capture_output=True, text=True, timeout=45)
        _line = next((l for l in (_p.stdout or "").splitlines()
                      if l.startswith("rule-enforcement-map:")), "(no output)")
        _summary = _line.split(" — ", 1)[-1]
        if _p.returncode == 0:
            print(f"  OK {'rule coverage':<18} {_summary}")
        else:
            print(f"  ✗✗ {'rule coverage':<18} {_summary} · an active rule is unmapped or a claimed control lacks evidence")
            rc = 1
except Exception as e:
    print(f"  ⚠︎ {'rule coverage':<18} check failed ({type(e).__name__}: {e})")
    rc = 1

# The Hermes runtime writes its own memory files and injects them into every one
# of its sessions. They are a second, ungated rule store with no provenance, and
# they go stale silently when the record layer moves. This is a predicate rather
# than a scheduled model run on Joe's ruling of 2026-08-16 ("that drift check
# needs to be code, not a scheduled routine"), which is rule 5e89c211 applied.
# A machine with no Hermes install reads OK — there is nothing to drift.
try:
    _hmc = os.path.join(REPO_ROOT, "ops", "hermes-memory-check.py")
    if os.path.exists(_hmc):
        _p = subprocess.run([sys.executable, _hmc], capture_output=True, text=True, timeout=20)
        _line = next((l for l in (_p.stdout or "").splitlines()
                      if l.startswith("hermes-memory:")), "(no output)")
        _summary = _line.split(" — ", 1)[-1] if " — " in _line else _line.split(": ", 1)[-1]
        if _p.returncode == 0:
            print(f"  OK {'hermes memory':<18} {_summary}")
        else:
            print(f"  ✗✗ {'hermes memory':<18} {_summary} · python3 ops/hermes-memory-check.py for the entries")
            rc = 1
except Exception as e:
    print(f"  ⚠︎ {'hermes memory':<18} check failed ({type(e).__name__}: {e})")
    rc = 1

# The condition on the 2026-08-16 write grant. Hermes holds nine additive write
# verbs against the record; that grant assumed a human in the session. Its own
# scheduler and messaging gateway answer to none of CARR's scheduled-run rules,
# so this row is what notices the moment it can act unattended.
try:
    _hac = os.path.join(REPO_ROOT, "ops", "hermes-autonomy-check.py")
    if os.path.exists(_hac):
        _p = subprocess.run([sys.executable, _hac], capture_output=True, text=True, timeout=30)
        _line = next((l for l in (_p.stdout or "").splitlines()
                      if l.startswith("hermes-autonomy:")), "(no output)")
        _summary = _line.split(" — ", 1)[-1] if " — " in _line else _line.split(": ", 1)[-1]
        if _p.returncode == 0:
            print(f"  OK {'hermes autonomy':<18} {_summary}")
        else:
            print(f"  ✗✗ {'hermes autonomy':<18} {_summary} · python3 ops/hermes-autonomy-check.py")
            rc = 1
except Exception as e:
    print(f"  ⚠︎ {'hermes autonomy':<18} check failed ({type(e).__name__}: {e})")
    rc = 1

# Equality of ~/.codex/hooks.json with the tracked contract is useful, but it
# cannot establish that Codex has trusted that hook or actually invokes it.
# ops/codex-hook-smoke.sh is the live negative smoke that closes that gap: it
# sends Codex a probe matching guard-unattended.py's private-key pattern
# through the SAME invocation helper (bin/council-lib.sh's run_precheck) the
# automation actually uses, and records whether the guard's denial text came
# back. This row reads that result (out/codex-hook-smoke.json) rather than
# re-running the smoke — a live Codex call on every `run.sh health` would be a
# heavier side effect than a freshness check should carry, same reasoning as
# the cutover-readiness row below. PASS within 30 days reads OK; anything
# else — FAIL, or no recent record at all — reads as a fault, never silently
# green, because an unverified hook runtime is exactly the gap this row used
# to paper over with a permanent gray "unverified" line.
_chs_path = os.path.join(REPO_ROOT, "out", "codex-hook-smoke.json")
_chs_max_age_days = 30
if not os.path.exists(_chs_path):
    print(f"  -- {'Codex hook runtime':<18} no smoke run yet — run ops/codex-hook-smoke.sh")
else:
    try:
        with open(_chs_path) as _f:
            _chs = json.load(_f)
        _chs_ts = datetime.strptime(_chs["timestamp"], "%Y-%m-%dT%H:%M:%SZ")
        _chs_age_days = (datetime.utcnow() - _chs_ts).total_seconds() / 86400
        _chs_ver = _chs.get("codex_version", "unknown")
        if _chs_age_days > _chs_max_age_days:
            print(f"  ⚠︎ {'Codex hook runtime':<18} last smoke is {_chs_age_days:.0f}d old "
                  f"(>{_chs_max_age_days}d) — {_chs_ts.date()} · run ops/codex-hook-smoke.sh")
            rc = 1
        elif _chs.get("pass"):
            print(f"  OK {'Codex hook runtime':<18} PASS on {_chs_ts.date()} ({_chs_ver}) — "
                  f"live negative smoke confirms PreToolUse hooks fire under "
                  f"--dangerously-bypass-hook-trust")
        else:
            print(f"  ✗✗ {'Codex hook runtime':<18} FAIL on {_chs_ts.date()} ({_chs_ver}) — "
                  f"hooks may be silently skipped; run ops/codex-hook-smoke.sh and read its output")
            rc = 1
    except Exception as e:
        print(f"  ⚠︎ {'Codex hook runtime':<18} could not read {_chs_path} "
              f"({type(e).__name__}: {e}) · run ops/codex-hook-smoke.sh")
        rc = 1

try:
    _fal = os.path.join(REPO_ROOT, "ops", "fetch-allowlist.py")
    if os.path.exists(_fal):
        _p = subprocess.run([sys.executable, _fal, "--check"],
                            capture_output=True, text=True, timeout=60)
        _lines = (_p.stdout or "").strip().splitlines()
        _line = _lines[0].split(": ", 1)[-1] if _lines else "(no output)"
        # The tool's own line reads "fetch-allowlist: OK — 30 hosts…" because it
        # is also run by hand. The row already carries the glyph, so strip the
        # repeated status token rather than printing "OK fetch allowlist OK —".
        for _tok in ("OK — ", "STALE — ", "MISSING — "):
            if _line.startswith(_tok):
                _line = _line[len(_tok):]
                break
        print(f"  {'OK' if _p.returncode == 0 else '⚠︎'} {'fetch allowlist':<18} {_line}")
        if _p.returncode != 0:
            rc = 1
except Exception as e:
    print(f"  ⚠︎ {'fetch allowlist':<18} check failed ({type(e).__name__}: {e})")
    rc = 1

# ── taught rules: store vs the file that actually binds (added 2026-08-04) ────
# Every row above this one asks "is the output FRESH". This one asks "is the
# output TRUE", because freshness could not have caught the defect it exists for.
#
# 2026-08-04: teach returned ok, activate-rule returned ok, and
# `run.sh export --only compiled-rules` returned ok reporting 56 rows. The rule
# was still absent from the file sessions read. `run.sh export` writes to
# out/exports/ DRAFT by default; only CARR_EXPORT_LIVE=1 reaches the vault
# (exporters/common.py:32), and CLAUDE.md documented the command without the
# flag. The vault file was fresh the whole time — the hourly refresh keeps it
# that way — so an mtime check would have shown green while a rule taught after
# 20:00 bound nobody until morning.
#
# The count is the signal because both halves already carry it: the store knows
# how many rules are active, and each file declares its own total in its header.
# Nothing compared them. The session-start recitation reads that header and
# states it to the partner as fact, so a stale file makes the session misreport
# what is binding it — which is the audit signal rule 4f7c348f exists to provide.
try:
    _rlc = os.path.join(REPO_ROOT, "ops", "rules-live-check.py")
    if not os.path.exists(_rlc):
        print(f"  -- {'rules live':<18} ops/rules-live-check.py not present; skipped")
    else:
        # NOT sys.executable. `run.sh health` invokes this file with bare
        # `python3` (run.sh:80) while every other entry point uses "$PY", the
        # repo venv — health-check.py is deliberately stdlib-only so it runs
        # anywhere. The child is not: it needs psycopg. Handing it sys.executable
        # gave it system python3, which failed the import, exited non-zero and
        # printed nothing, so the row read "(no output)" and looked like a broken
        # check rather than a missing dependency. Resolve the venv explicitly and
        # leave health-check's own stdlib-only property intact.
        _venv = os.path.join(REPO_ROOT, ".venv", "bin", "python")
        _py = _venv if os.path.exists(_venv) else sys.executable
        _p = subprocess.run([_py, _rlc], capture_output=True, text=True, timeout=60)
        _lines = (_p.stdout or "").strip().splitlines()
        # Carry stderr into the message on a silent failure. The first version of
        # this block swallowed it, which is the defect this whole row exists for.
        _first = _lines[0] if _lines else (
            f"(no output; stderr: {(_p.stderr or '').strip().splitlines()[-1]})"
            if (_p.stderr or "").strip() else "(no output, no stderr)")
        if _first.startswith("SKIP"):
            print(f"  -- {'rules live':<18} {_first.split(': ', 1)[-1]}")
        elif _p.returncode == 0:
            print(f"  OK {'rules live':<18} {_first.split('— ', 1)[-1]}")
        else:
            print(f"  ⚠︎ {'rules live':<18} {_first.split('— ', 1)[-1]}  · "
                  f"run ~/carr-system/bin/refresh-rules.sh")
            rc = 1
except Exception as e:
    print(f"  ⚠︎ {'rules live':<18} check failed ({type(e).__name__}: {e})")
    rc = 1

# ── cutover readiness: LAST result per partner (Phase 1, 2026-08-13) ─────────
# Unlike the "rules live" row above, this does NOT re-run the check — it reads
# the artifact ops/cutover-readiness.py wrote the last time the nightly chain
# ran it (out/cutover-readiness.json), same read-the-artifact pattern as the
# vault-drift-watch row below. Re-running here would mean this row silently
# fires a LIVE standing-context/doctrine-index call and a full store scan every
# time someone runs `run.sh health` by hand, which is a heavier side effect
# than a freshness check should carry. What matters for the cutover is whether
# last night's proof was clean and recent, not whether it is clean RIGHT NOW.
print("\ncutover readiness (store-first boot, per partner)")
try:
    _crc_path = os.path.join(REPO_ROOT, "out", "cutover-readiness.json")
    if not os.path.exists(_crc_path):
        print(f"  -- cutover-readiness    no run yet — ops/cutover-readiness.py has not been "
              f"run (rides the nightly chain now; expected before the first night, a fault after)")
    else:
        with open(_crc_path) as _f:
            _crj = json.load(_f)
        if _crj.get("skip"):
            print(f"  -- cutover-readiness    last run SKIPPED — {_crj['skip']}")
        else:
            _crc_age_h = (time.time() -
                          datetime.strptime(_crj["generated_at"][:19], "%Y-%m-%dT%H:%M:%S").timestamp())
            # generated_at is UTC (isoformat of a timezone.utc datetime); the strptime
            # above parses it naive, so localtime-vs-UTC offsets this by up to a few
            # hours depending on the machine's zone. Good enough for a freshness gate
            # whose tolerance is measured in hours, same slack the vault-drift row below
            # accepts from the same isoformat-vs-epoch approach.
            _crc_age_h = abs(_crc_age_h) / 3600
            _this = (_crj.get("this_machine") or {}).get("resolved_identity") or "(unresolved)"
            _parts = _crj.get("partners") or {}
            _bits = []
            for _partner in ("joe", "dell"):
                _cr_st = (_parts.get(_partner) or {}).get("status", "MISSING")
                _short = "READY" if _cr_st == "READY" else ("PARTIAL" if _cr_st.startswith("PARTIAL") else "NOT READY")
                _bits.append(f"{_partner}={_short}")
            _summary = f"this machine={_this}; {', '.join(_bits)}"
            if not _crj.get("overall_ready", False):
                print(f"  ⚠︎ cutover-readiness    {_summary} ({_crc_age_h:.1f}h old) · on breach: "
                      f"./.venv/bin/python ops/cutover-readiness.py and read the DISAGREES/FAILED lines")
                rc = 1
            elif _crc_age_h > 30:
                print(f"  ⚠︎ cutover-readiness    STALE {_crc_age_h:.0f}h — last result was clean "
                      f"({_summary}) · on breach: check the nightly chain log for the "
                      f"'cutover readiness' step")
                rc = 1
            else:
                print(f"  OK cutover-readiness    {_summary} ({_crc_age_h:.1f}h old)")
except Exception as e:
    print(f"  ⚠︎ cutover-readiness check failed ({type(e).__name__}: {e})")
    rc = 1

# ── forgetting (loop #212, migration 0071) ──────────────────────────────────
# The store's forgetting policy, surfaced daily: re-verify queue depth (expired
# + unstamped-volatile verifications), ingest_inbox backlog, and growth SLOPE
# per accumulating table. The child also writes today's growth snapshot — the
# health check is the one daily process guaranteed to run, so the snapshot
# rides it. Same delegate pattern as rules-live: stdlib parent, venv child.
try:
    _fgc = os.path.join(REPO_ROOT, "ops", "forgetting-check.py")
    if not os.path.exists(_fgc):
        print(f"  -- {'forgetting':<18} ops/forgetting-check.py not present; skipped")
    else:
        _venv = os.path.join(REPO_ROOT, ".venv", "bin", "python")
        _py = _venv if os.path.exists(_venv) else sys.executable
        _p = subprocess.run([_py, _fgc], capture_output=True, text=True, timeout=60)
        _lines = (_p.stdout or "").strip().splitlines()
        _first = _lines[0] if _lines else (
            f"(no output; stderr: {(_p.stderr or '').strip().splitlines()[-1]})"
            if (_p.stderr or "").strip() else "(no output, no stderr)")
        if _first.startswith("SKIP"):
            print(f"  -- {'forgetting':<18} {_first.split(': ', 1)[-1]}")
        elif _p.returncode == 0:
            print(f"  OK {'forgetting':<18} {_first.split('— ', 1)[-1]}")
        else:
            print(f"  ⚠︎ {'forgetting':<18} {_first.split('— ', 1)[-1]}  · "
                  f"re-verify queue: v_expired_verification; intake: v_ingest_backlog")
            rc = 1
except Exception as e:
    print(f"  ⚠︎ {'forgetting':<18} check failed ({type(e).__name__}: {e})")
    rc = 1

# ── renders-verify (wave 1 C1, decision a317439f) ───────────────────────────
# Live render bytes vs the file_sha stored at export: a mismatch means
# something other than the exporter touched a generated file. Bound action is
# printed by the child per rule 590b11e1.
try:
    _rvc = os.path.join(REPO_ROOT, "ops", "renders-verify.py")
    if not os.path.exists(_rvc):
        print(f"  -- {'renders-verify':<18} ops/renders-verify.py not present; skipped")
    else:
        _venv = os.path.join(REPO_ROOT, ".venv", "bin", "python")
        _py = _venv if os.path.exists(_venv) else sys.executable
        _p = subprocess.run([_py, _rvc], capture_output=True, text=True, timeout=120)
        _lines = (_p.stdout or "").strip().splitlines()
        _first = _lines[0] if _lines else (
            f"(no output; stderr: {(_p.stderr or '').strip().splitlines()[-1]})"
            if (_p.stderr or "").strip() else "(no output, no stderr)")
        if _first.startswith("SKIP"):
            print(f"  -- {'renders-verify':<18} {_first.split(': ', 1)[-1]}")
        elif _p.returncode == 0:
            print(f"  OK {'renders-verify':<18} {_first.split('— ', 1)[-1]}")
        else:
            print(f"  ⚠︎ {'renders-verify':<18} {_first.split('— ', 1)[-1]}")
            rc = 1
except Exception as e:
    print(f"  ⚠︎ {'renders-verify':<18} check failed ({type(e).__name__}: {e})")
    rc = 1

# ── ledger-vs-live ───────────────────────────────────────────────────────────
# Does the deployment ledger still describe the Worker that is answering? The
# two disagreed THREE times on 2026-08-19 and 2026-08-20 — a good deploy booked
# failed on a stale identity read-back, another booked failed on a performance
# budget that clocked the whole golden suite, and a third booked complete with
# no verb count at all — and every one was found by a human curling /release,
# never by a check. The ledger is not a diary: bin/deploy-worker.sh's verb-loss
# guard reads it to decide whether a deploy is about to remove verbs, so a
# baseline that has drifted below reality re-opens the 2026-08-09 hole where
# production went from 75 verbs to 66 with nothing objecting. Bound action is
# printed by the child per rule 590b11e1.
try:
    _dlv = os.path.join(REPO_ROOT, "ops", "deploy-ledger-vs-live.py")
    if not os.path.exists(_dlv):
        print(f"  -- {'ledger-vs-live':<18} ops/deploy-ledger-vs-live.py not present; skipped")
    else:
        _venv = os.path.join(REPO_ROOT, ".venv", "bin", "python")
        _py = _venv if os.path.exists(_venv) else sys.executable
        _p = subprocess.run([_py, _dlv], capture_output=True, text=True, timeout=120)
        _lines = (_p.stdout or "").strip().splitlines()
        _first = _lines[0] if _lines else (
            f"(no output; stderr: {(_p.stderr or '').strip().splitlines()[-1]})"
            if (_p.stderr or "").strip() else "(no output, no stderr)")
        if _first.startswith("SKIP"):
            print(f"  -- {'ledger-vs-live':<18} {_first.split(': ', 1)[-1]}")
        elif _p.returncode == 0:
            print(f"  OK {'ledger-vs-live':<18} {_first.split('— ', 1)[-1]}")
        else:
            print(f"  ⚠︎ {'ledger-vs-live':<18} {_first.split('— ', 1)[-1]}")
            rc = 1
except Exception as e:
    print(f"  ⚠︎ {'ledger-vs-live':<18} check failed ({type(e).__name__}: {e})")
    rc = 1

# --- the doctrine store (P4/P5, 2026-08-08; decisions 82a2fb62 + import door) -
# Every row prints its bound action inline (rule 590b11e1: no metric without a
# bound action, visible in the render itself). A failed read is never all-clear.
print("\ndoctrine store")
try:
    _q = ("select "
          " (select count(*) from doctrine_gate_run where result='fail' "
          "   and dry_run=false and started_at > now() - interval '24 hours'),"
          " (select count(*) from doctrine_section s join doctrine_document d "
          "   on d.id=s.document_id join doctrine_review_policy p on p.id=d.review_policy_id "
          "   where s.status='active' and p.max_age_days is not null "
          "   and s.review_after is null),"
          " (select count(*) from doctrine_section where review_after < now()),"
          " (select generation from doctrine_meta where id=1),"
          " (select count(*) from doctrine_document);")
    _p = subprocess.run([os.path.join(REPO_ROOT, ".venv/bin/python"),
                         os.path.join(REPO_ROOT, "tools/db-tap.py"), "sql", "/dev/stdin"],
                        input=_q, capture_output=True, text=True, timeout=90)
    if _p.returncode != 0:
        print("  ⚠︎ store UNREADABLE — cannot say whether doctrine is healthy · "
              "on breach: check db.env / Neon, then rerun")
        rc = 1
    else:
        _vals = None
        for _line in _p.stdout.splitlines():
            _c = [x.strip() for x in _line.split("|")]
            if len(_c) == 5 and all(x.lstrip("-").isdigit() for x in _c):
                _vals = [int(x) for x in _c]
        if _vals is None:
            print("  ⚠︎ store row unparseable · on breach: run the query by hand via db-tap")
            rc = 1
        else:
            _blocks, _norev, _stale, _gen, _docs = _vals
            print(f"  {'OK' if not _blocks else '⚠︎'} gate-blocks-24h      {_blocks} · "
                  f"on breach: read doctrine_gate_finding for the failing runs; fix the content "
                  f"or amend the gate row")
            if _blocks:
                rc = 1
            # never_reviewed is the EMPTY-SIGNAL guard (2b889e80): imported
            # sections carry no review_after, so a bare stale-count of 0 would
            # read healthy while the staleness machinery is actually inert.
            print(f"  {'OK' if not _norev else '⚠︎'} never-reviewed       {_norev} "
                  f"policy-bearing sections with no review clock · on breach: backfill "
                  f"review_after from each doc's policy (P5 item, import follow-up)")
            print(f"  {'OK' if not _stale else '⚠︎'} stale-sections       {_stale} past "
                  f"review_after · on breach: review or re-confirm via write-doctrine-section")
            _inv = subprocess.run([os.path.join(REPO_ROOT, ".venv/bin/python"),
                                   os.path.join(REPO_ROOT, "pipelines/doctrine_inventory.py"),
                                   "--count"], capture_output=True, text=True, timeout=120)
            try:
                _j = json.loads((_inv.stdout or "").strip().splitlines()[-1])
            except Exception:
                _j = {"remaining": -1}
            if _j.get("remaining", -1) < 0:
                print("  ⚠︎ migration-coverage   UNKNOWN (inventory failed) · on breach: "
                      "run pipelines/doctrine_inventory.py by hand")
                rc = 1
            else:
                print(f"  {'OK' if _j['remaining'] == 0 else '→︎'} migration-coverage   "
                      f"{_j['remaining']} corpus files not yet in a verified batch "
                      f"({_docs} docs, generation {_gen}) · on breach: run the next bounded "
                      f"batch through bin/import-doctrine.sh")
except Exception as e:
    print(f"  ⚠︎ doctrine store check failed ({type(e).__name__}: {e})")
    rc = 1

# --- the portability mirror (Joe's ruling 2026-08-08) ------------------------
# Freshness only: a mirror is insurance, and stale insurance that looks valid
# is worse than none. Bound action inline per rule 590b11e1.
try:
    _mp = ("/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us"
           "@gmail.com/My Drive/CARR AI/Backups/portability-mirror/MANIFEST.md")
    if not os.path.exists(_mp):
        print("  ⚠︎ portability-mirror  MISSING · on breach: run tools/db-tap.py run "
              "pipelines/doctrine_mirror.py (see nightly.sh for args)")
        rc = 1
    else:
        _age_h = (time.time() - os.path.getmtime(_mp)) / 3600
        if _age_h > 30:
            print(f"  ⚠︎ portability-mirror  STALE {int(_age_h)}h · on breach: the nightly "
                  f"mirror step failed — check out/nightly logs, rerun the db-tap command")
            rc = 1
        else:
            print(f"  OK portability-mirror  fresh ({int(_age_h)}h old, Drive + local)")
except Exception as e:
    print(f"  ⚠︎ portability-mirror check failed ({type(e).__name__}: {e})")
    rc = 1

# --- vault drift watch (Phase 1 v2, 2026-08-13) --------------------------------
# Detection control for the proven-open Codex vault-write door
# (ops/vault-drift-watch.py). v2 added a second, independent baseline
# (--rebaseline, run LAST in the nightly chain after all exports) that survives
# the nightly rewrite, so a generated file tampered with and then re-exported
# the same night is still caught the NEXT time --check runs (--check runs
# FIRST in the chain, before exports) — the exact gap a plain manifest-to-
# manifest diff could not close. This row reads --check's own summary
# (out/vault-drift-check-summary.json) plus the --rebaseline baseline's age;
# neither existing yet before the first-ever run of its mode is not a fault, a
# stale or non-clean one is. Bound action inline per rule 590b11e1.
print("\nvault drift watch")
try:
    _vdw_summary_path = os.path.join(REPO_ROOT, "out", "vault-drift-check-summary.json")
    _vdw_baseline_path = os.path.join(REPO_ROOT, "out", "vault-drift-baseline", "manifest.json")

    _vdw_baseline_age_h = None
    if os.path.exists(_vdw_baseline_path):
        with open(_vdw_baseline_path) as _f:
            _vdw_bl = json.load(_f)
        _vdw_baseline_age_h = (time.time() -
                                datetime.fromisoformat(_vdw_bl["generated_at"]).timestamp()) / 3600

    if not os.path.exists(_vdw_summary_path):
        _bl_note = (f"; baseline is {_vdw_baseline_age_h:.1f}h old" if _vdw_baseline_age_h is not None
                    else ", and no --rebaseline baseline exists yet either")
        print(f"  -- vault-drift-watch     no --check run yet — run ops/vault-drift-watch.py "
              f"--check once (nightly step 1){_bl_note} (expected before the first run, a "
              f"fault after)")
    else:
        with open(_vdw_summary_path) as _f:
            _vdw = json.load(_f)
        _vdw_age_h = (time.time() -
                      datetime.fromisoformat(_vdw["generated_at"]).timestamp()) / 3600
        _vdw_tamper = _vdw.get("tamper_count", 0)
        _vdw_unexpected = _vdw.get("unexpected_count", 0)
        _vdw_mirror_verified = _vdw.get("mirror_verified_count", 0)
        _bl_str = f"{_vdw_baseline_age_h:.1f}h" if _vdw_baseline_age_h is not None else "NO BASELINE"
        if _vdw_tamper or _vdw_unexpected:
            print(f"  ⚠︎ vault-drift-watch     {_vdw_tamper} TAMPER + {_vdw_unexpected} "
                  f"UNEXPECTED last check ({_vdw_age_h:.1f}h old, baseline {_bl_str} old) · "
                  f"on breach: read out/vault-drift-salvage-manifest.jsonl and the newest "
                  f"folder under out/vault-drift-quarantine/ for the quarantined files + "
                  f"diffs, then confirm with Joe whether each edit is legitimate")
            rc = 1
        elif _vdw_age_h > 30:
            print(f"  ⚠︎ vault-drift-watch     STALE {_vdw_age_h:.0f}h — last check reported "
                  f"clean (baseline {_bl_str} old) · on breach: check the nightly chain log "
                  f"for the 'vault drift watch (check)' step")
            rc = 1
        elif _vdw_baseline_age_h is None:
            print(f"  ⚠︎ vault-drift-watch     check clean ({_vdw_age_h:.1f}h old) but NO "
                  f"--rebaseline BASELINE exists yet · on breach: run "
                  f"ops/vault-drift-watch.py --rebaseline once (nightly's last step, after "
                  f"exports) — until then the tamper check has nothing to compare against")
            rc = 1
        elif _vdw_baseline_age_h > 30:
            print(f"  ⚠︎ vault-drift-watch     check clean ({_vdw_age_h:.1f}h old) but "
                  f"baseline STALE {_vdw_baseline_age_h:.0f}h · on breach: check the nightly "
                  f"chain log for the 'vault drift watch (rebaseline)' step")
            rc = 1
        else:
            _mirror_note = (f", {_vdw_mirror_verified} mirror-verified" if _vdw_mirror_verified
                             else "")
            print(f"  OK vault-drift-watch     0 tamper, 0 unexpected{_mirror_note} "
                  f"({_vdw_age_h:.1f}h old check, baseline {_vdw_baseline_age_h:.1f}h old)")
except Exception as e:
    print(f"  ⚠︎ vault-drift-watch check failed ({type(e).__name__}: {e})")
    rc = 1

# --- deploy provenance: does production run code this repo has a record of? (Phase 1, 2026-08-13) ---
# THE GAP THIS CLOSES. Before mcp-server/src/release.js existed, the deployed
# Worker could not say what it was running AT ALL — no Git SHA, no schema
# range, no policy generation, anywhere in its responses or its deploy
# metadata. The only local signal was mcp-server/.last-deployed-verb-count, a
# bookkeeping file the deploy script writes, and on 2026-08-13 it sat
# un-bumped for roughly two hours after a real deploy (a Worker deploy
# happened ~15:21 while the marker's last write was ~13:23) — a verification
# pass very nearly concluded the code was unshipped, and only a live
# database query settled it. A marker that can go silently stale is worse
# than none.
#
# THIS ROW READS THE WORKER ITSELF, not the local marker — that is the whole
# point (see bin/deploy-worker.sh's DEPLOY PROVENANCE note: the marker only
# ever protects a deploy that goes through the script, and `wrangler deploy`
# is also called directly elsewhere in this repo's own history). The Git SHA
# is stamped into the Worker at deploy time (`wrangler deploy --var
# GIT_SHA:<sha>`) and comes back null with a stated reason when a deploy
# bypassed that script — reported here honestly, not treated as "probably
# fine".
#
# A PRODUCTION SHA THAT IS NOT AN ANCESTOR OF LOCAL HEAD IS THE LOUD FINDING
# THIS ROW EXISTS FOR: it means production is running code this checkout has
# no record of — this checkout is behind, or someone deployed from a branch
# or a force-pushed-away commit. That is loop #276's failure mode (a verb
# silently vanishing from production because the wrong tree shipped),
# checked independently of whether bin/deploy-worker.sh's own preflight ran.
print("\ndeploy provenance (/release)")
RELEASE_URL = os.environ.get("CARR_RELEASE_URL", "https://api.doctorcre.com/release")
try:
    import urllib.request
    import urllib.error

    try:
        _req = urllib.request.Request(RELEASE_URL, headers={"user-agent": "carr-health-check"})
        with urllib.request.urlopen(_req, timeout=15) as _resp:
            _rel = json.loads(_resp.read().decode())
    except Exception as e:
        print(f"  ⚠︎ release UNREACHABLE — cannot verify what production is running "
              f"({type(e).__name__}: {e}) · on breach: curl -fsS {RELEASE_URL} by hand")
        rc = 1
        _rel = None

    if _rel is not None:
        _sha_obj = _rel.get("git_sha") or {}
        _sha = _sha_obj.get("value")
        _rel_verbs = _rel.get("verb_count")
        _rel_gen = (_rel.get("doctrine_generation") or {}).get("value")
        _schema = _rel.get("schema") or {}

        if not _sha:
            _reason = _sha_obj.get("reason") or "no reason given"
            print(f"  ⚠︎ release       git_sha NOT STAMPED — {_reason} · production's "
                  f"provenance cannot be checked against this checkout until the next "
                  f"deploy goes through bin/deploy-worker.sh ({_rel_verbs} verbs reported)")
            rc = 1
        else:
            # The commit may not exist in this checkout's object database yet (a
            # teammate's deploy, or this clone simply has not fetched). Try once
            # locally, fetch once if that fails, then judge — never report "not an
            # ancestor" for a SHA this checkout has just never heard of.
            _known = subprocess.run(["git", "cat-file", "-e", _sha + "^{commit}"],
                                     cwd=REPO_ROOT, capture_output=True, text=True, timeout=15)
            _fetched = False
            if _known.returncode != 0:
                subprocess.run(["git", "fetch", "origin", "--quiet"],
                                cwd=REPO_ROOT, capture_output=True, text=True, timeout=30)
                _fetched = True
                _known = subprocess.run(["git", "cat-file", "-e", _sha + "^{commit}"],
                                         cwd=REPO_ROOT, capture_output=True, text=True, timeout=15)
            if _known.returncode != 0:
                print(f"  ✗✗ release       production SHA {_sha[:12]} is UNKNOWN to this "
                      f"checkout even after git fetch — this repo has NO record of the "
                      f"commit production is running ({_rel_verbs} verbs reported). Check "
                      f"whether it exists on another remote/branch or was force-pushed away")
                rc = 1
            else:
                _anc = subprocess.run(["git", "merge-base", "--is-ancestor", _sha, "HEAD"],
                                       cwd=REPO_ROOT, capture_output=True, text=True, timeout=15)
                _fetch_note = " (needed a git fetch to find it)" if _fetched else ""
                if _anc.returncode == 0:
                    print(f"  OK release       production is running {_sha[:12]}{_fetch_note} — "
                          f"an ancestor of local HEAD ({_rel_verbs} verbs, schema "
                          f"{_schema.get('highest_applied_migration') or '?'}, doctrine gen {_rel_gen})")
                else:
                    print(f"  ✗✗ release       production SHA {_sha[:12]} EXISTS in this repo's "
                          f"history but is NOT an ancestor of local HEAD{_fetch_note} — "
                          f"production is off main, or main has been rewritten since it "
                          f"shipped ({_rel_verbs} verbs reported)")
                    rc = 1

        if _schema.get("reason"):
            print(f"  ⚠︎ release       schema unreadable from production: {_schema['reason']}")
            rc = 1
except Exception as e:
    print(f"  ⚠︎ deploy provenance check failed ({type(e).__name__}: {e})")
    rc = 1

# --- worktrees (2026-08-14) ---------------------------------------------------
# A worktree that never gets reaped is not a fault by itself, but the count was
# the FIRST symptom noticed of the underlying gap: 22 existed live the day this
# was written, 10 with branches already merged into origin/main, one 443 commits
# behind. This row is not a metric without a bound action (rule 590b11e1) — it
# runs the real sweep in --dry-run (read-only, nothing removed) and reports
# exactly what ./run.sh worktree --sweep would do, computed live every time
# rather than a count anyone could let go stale.
print("\nworktrees")
try:
    _wt_sh = os.path.join(REPO_ROOT, "bin", "worktree.sh")
    if not os.path.exists(_wt_sh):
        print("  -- worktrees          bin/worktree.sh not present; skipped")
    else:
        _wtp = subprocess.run(["zsh", _wt_sh, "--sweep", "--dry-run"],
                               cwd=REPO_ROOT, capture_output=True, text=True, timeout=60)
        _wtm = re.search(r"(\d+) total, (\d+) reaped, (\d+) kept", _wtp.stdout)
        if _wtp.returncode != 0 or not _wtm:
            print(f"  ⚠︎ worktrees          sweep --dry-run failed or unparseable · on breach: "
                  f"run ./run.sh worktree --sweep --dry-run by hand and read the output")
            rc = 1
        else:
            _wt_total, _wt_reaped, _wt_kept = _wtm.groups()
            if int(_wt_reaped) > 0:
                print(f"  → worktrees          {_wt_total} total, {_wt_reaped} reapable, "
                      f"{_wt_kept} kept · ./run.sh worktree --sweep")
            else:
                print(f"  OK worktrees          {_wt_total} total, 0 reapable")
except Exception as e:
    print(f"  ⚠︎ worktrees check failed ({type(e).__name__}: {e})")
    rc = 1

# --- stranded pull requests (Joe's question, 2026-08-14) ---------------------
# "if i don't have the emails and things sit until they are noticed how will the
# system fix them consistently and timely". Until this row existed, GitHub's
# failure emails to Joe personally were the ONLY thing watching the repo, and
# they missed the case that mattered anyway: PR #79 sat 8.5h with no CI run
# against it at all and a conflict against main, found only because a session
# went looking by hand. The nightly chain runs this file, so a stranded pull
# request now surfaces within a day with no inbox involved. Thresholds and the
# reasoning behind their width live in ops/pr-hygiene-check.py.
print("\nrepo hygiene")
try:
    _prh = os.path.join(REPO_ROOT, "ops", "pr-hygiene-check.py")
    if not os.path.exists(_prh):
        print("  -- stranded PRs        ops/pr-hygiene-check.py not present; skipped")
    else:
        _prp = subprocess.run([os.path.join(REPO_ROOT, ".venv/bin/python"), _prh,
                               "--health-row"],
                              cwd=REPO_ROOT, capture_output=True, text=True, timeout=120)
        print((_prp.stdout or "").rstrip() or
              "  ⚠︎ stranded PRs        check produced no output · on breach: run "
              "ops/pr-hygiene-check.py by hand")
        if _prp.returncode != 0:
            rc = 1
except Exception as e:
    print(f"  ⚠︎ stranded PRs check failed ({type(e).__name__}: {e})")
    rc = 1

# --- the sign against origin (rule 173119a8) --------------------------------
# The `uncommitted work` row above reads the WORKING TREE only. It cannot see a
# commit that exists on this machine and nowhere else, and it cannot see work
# another session already landed — which is the pair the rule is about. Its own
# incident was a session naming a peer as the blocker off a HEAD-based diff:
# accurate diff, backwards conclusion, because a HEAD that is BEHIND origin
# makes somebody else's landed work look like your missing work.
#
# bin/whose-work.py is the ONE implementation of that measurement, so this row
# and a session asking by hand cannot drift apart (rule a8c55a47).
print("\nlanded against origin")
try:
    _ww = os.path.join(REPO_ROOT, "bin", "whose-work.py")
    if not os.path.exists(_ww):
        print("  -- landed              bin/whose-work.py not present; skipped")
    else:
        _wwp = subprocess.run([os.path.join(REPO_ROOT, ".venv/bin/python"), _ww,
                               "--repo", REPO_ROOT, "--json"],
                              cwd=REPO_ROOT, capture_output=True, text=True,
                              timeout=240)
        _w = json.loads(_wwp.stdout or "{}")
        _ahead, _behind = _w.get("ahead"), _w.get("behind")
        _unl = _w.get("unlanded_commits", 0)
        if _ahead is None:
            print(f"  -- landed              {_w.get('note', 'no upstream to measure against')}")
        else:
            print(f"  {'⚠︎' if _unl else 'OK'} landed              "
                  f"ahead {_ahead}, behind {_behind}"
                  + (f" · {_unl} commit(s) on no remote — push them"
                     if _unl else " · nothing unlanded"))
            if _unl:
                rc = 1
            if _behind and not _ahead:
                print("     behind-only means another session landed work; pull it. "
                      "Reading this as your own missing work is the mistake this row exists for.")
except Exception as e:
    print(f"  ⚠︎ landed check failed ({type(e).__name__}: {e})")
    rc = 1

sys.exit(rc)
