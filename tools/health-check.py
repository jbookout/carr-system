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
import json, os, sys, glob, time, re, subprocess
from datetime import datetime, timedelta

REPO_ROOT = os.path.expanduser("~/carr-system")

VAULT = os.environ.get("CARR_VAULT",
    "/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI")

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
    # RE-POINTED 2026-08-09 (Joe's go). This row used to watch
    # Automation/radar/radar-digest-2*.md — a digest a session TYPED at the end
    # of each run. The record-home gate now refuses hand-authored vault markdown
    # (rule 14181e60), so that file could never be written again and this row
    # could never go green: a detector that is permanently amber is one every
    # reader learns to skip, which is the loops #128/#178/#182 failure a fourth
    # time.
    #
    # The digest itself is now a RENDER (exporters target `radar-digest` ->
    # Automation/radar/radar-digest-latest.md). Do NOT point this row there: that
    # file regenerates nightly with every other export, so its mtime proves only
    # that the exporter ran, never that the weekly radar sweep did.
    #
    # What proves the sweep ran is the MAPPER'S OWN RUN REPORT, written by
    # pipelines/map_radar_lanes.py on every invocation whether or not the run
    # found anything — which is exactly the property a zero-result week needs,
    # and the AL lane has now had four of those in a row. Cadence 8, not 9: the
    # lane is weekly, so 9 let a run slip two days late unnoticed.
    ("Radar digest",      os.path.expanduser("~/carr-system/out/radar-lane-map-*.md"), 8, [],
     "weekly radar sweep — proof of life is the mapper's run report, not the "
     "digest render (radar-digest-sop.md; digest reads at "
     "Automation/radar/radar-digest-latest.md)"),
    ("PECOS pool",        "Automation/radar/upstream/pecos.json", 100, [],
     "quarterly (Jan/Apr/Jul/Oct; next diff vs the Q1 baseline in Oct)"),
    ("Section index",     "Automation/section-index.tsv", 9, [],
     "retrieval-as-code layer; rebuild = run.sh section-index (rides the Monday run; no BEHIND check — doc inputs churn daily by design)"),
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
    # --- the Wave 2 job reports (added 2026-07-31 with ORDER 19a) ---------------
    # These five live in the REPO's out/, not the vault, so their patterns are
    # absolute — os.path.join returns an absolute second argument unchanged.
    # 26h each, per the ORDER 19 ruling: the two digests ride the nightly chain
    # (7 days), and the brief pack plus the queue are rebuilt by the heartbeat.
    # WHAT A FAILURE HERE MEANS, so nobody debugs the wrong end: cadence and
    # matcher going stale means the nightly chain SKIPPED them, which until Joe's
    # role tap lands is the DESIGNED state (exit 78, not a fault).
    #
    # CORRECTED TWICE ON 2026-08-04, and the second correction is the one to read.
    #
    # This comment first said these rows going stale "is the weekends-off rule
    # showing through, since the heartbeat stands down Sat/Sun". That was never
    # the mechanism: Monday 2026-08-03 was a full business day, npi-sweep-weekly
    # fired at 12:31Z and the vault took writes that morning, while brief-pack
    # and review-queue did not. Their last write was an ad-hoc session run, Sun
    # 2026-08-02 15:18, which is not the heartbeat's ~08:00 CT slot.
    #
    # The replacement text then claimed THE HEARTBEAT HAS NO SCHEDULE AT ALL,
    # having checked the Claude scheduler, launchd and cron. Joe corrected that
    # the same day: the heartbeat is scheduled in COWORK, which a local Claude
    # Code session cannot enumerate. Both the subagent and the main session named
    # Cowork as unreachable and then wrote the negative anyway — rule 2b889e80
    # says an unreachable collection makes a finding partial BY DEFINITION.
    #
    # SO THE STATE IS: the trigger fires every morning and these two jobs do not
    # run. Leading hypothesis, UNTESTED — a Cowork session cannot reach
    # ~/carr-system, so it cannot execute `run.sh brief-pack` at all. Do not
    # re-explain these rows as a weekend artifact, and do not assert a cause
    # until someone reads the Cowork task's own run history. Tracked as loop 181.
    #
    # The Monday brief (monday-brief-task.md) is unscheduled by the same gap and
    # has NO row here at all, so it fails silently — the one failure mode with no
    # detector. Tracked as an open loop; do not re-explain these rows as a
    # weekend artifact until a schedule actually exists.
    ("JOB brief-pack",    os.path.expanduser("~/carr-system/out/brief-pack/brief-pack-latest.md"), 26/24, [],
     "run.sh brief-pack (heartbeat JOB 4b)"),
    ("JOB monday-agenda", os.path.expanduser("~/carr-system/out/brief-pack/monday-agenda.md"), 26/24, [],
     "run.sh brief-pack — the Monday brief's own input, watched separately because "
     "it has its own consumer"),
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


# --- weekday-only outputs (loop #275, fixed 2026-08-09) ----------------------
# These three are written by bin/local-briefs.sh under launchd
# com.carr.local-briefs, which fires WEEKDAYS at 06:45 to match Joe's weekend
# stand-down. Measured against a flat 26-hour cadence they went STALE every
# Saturday and Sunday on a job that was working perfectly — Friday's run is
# 2.0 days old by Sunday. That is an alarm firing on healthy state, which this
# same file's scheduler register already warns is how a dashboard loses its
# readers.
#
# The register got this right by evaluating the real cron. Do the same here:
# compare against the most recent weekday firing that has actually passed,
# rather than against wall-clock age. No separate weekend heuristic is added —
# the schedule itself carries the rule, exactly as the register argues.
WEEKDAY_ONLY = {
    "JOB brief-pack": (6, 45),
    "JOB monday-agenda": (6, 45),
    "JOB review-queue": (6, 45),
}


def last_weekday_window(hour, minute):
    """Most recent Mon-Fri hour:minute that has already passed, as epoch."""
    now = datetime.now()
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate > now:
        candidate -= timedelta(days=1)
    while candidate.weekday() >= 5:          # 5=Sat, 6=Sun
        candidate -= timedelta(days=1)
    return candidate.timestamp()

rc = 0
print(f"Façade check (rule 28) — {time.strftime('%Y-%m-%d %H:%M')} — outputs, not schedules")
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
    if name in WEEKDAY_ONLY:
        # Weekday-only job: did it run at its last ACTUAL firing window?
        window = last_weekday_window(*WEEKDAY_ONLY[name])
        if os.path.getmtime(out) < window:
            missed = (time.time() - window) / 86400
            problems.append(
                f"MISSED its {time.strftime('%a %H:%M', time.localtime(window))} "
                f"window ({missed:.1f}d ago), output {a:.1f}d old")
    elif max_age is not None and a > max_age:
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
# signal that is local: the TIME OF DAY the output was written. A nightly whose files
# carry an 08:49 mtime did not run at 02:05, whatever the schedule claims.
#
# name, output glob, expected local hour, tolerance hours, note
# The watched file must be one ONLY THE SCHEDULED JOB writes. The first version of this
# check watched an exported .xlsx and immediately produced a false positive: a manual
# `CARR_EXPORT_LIVE=1 ./run.sh export` rewrote it at 11:38 and the check reported "9.6h
# drift" from that, not from the scheduler. It was measuring when a FILE was written, not
# when the JOB ran. out/nightly.log is appended by bin/nightly.sh and by nothing else.
SCHEDULE = [
    ("nightly-record-layer", "~/carr-system/out/nightly.log", 2, 2.5,
     "cron 0 2 * * * (local CT). Landing hours late means the Mac slept through it and "
     "the task fired on wake — the encrypted backup is the seventh step of that chain "
     "(it was step 3 when this note was written), so it is "
     "skipped for as long as no session opens. Fix: sudo pmset repeat wakeorpoweron "
     "MTWRFSU 01:55:00"),
]

print("Schedule drift — did the job run WHEN scheduled, not merely recently")
for name, out_pat, want_hour, tol_h, note in SCHEDULE:
    # SCHEDULE watches job artefacts, which may sit in the repo rather than the vault.
    out = (newest(out_pat) if not out_pat.startswith(("~", "/"))
           else (lambda h: h if os.path.exists(h) else None)(os.path.expanduser(out_pat)))
    if not out:
        print(f"  MISSING {name:<22} no file matches {out_pat}")
        rc = 1
        continue
    lt = time.localtime(os.path.getmtime(out))
    ran_h = lt.tm_hour + lt.tm_min / 60.0
    # circular distance on a 24h clock: 23:50 against 00:10 is 20 minutes, not 23.7h
    d = abs(ran_h - want_hour)
    drift = min(d, 24 - d)
    if drift > tol_h:
        print(f"  ⚠︎ {name:<22} ran {time.strftime('%H:%M', lt)}, scheduled ~{want_hour:02d}:00 "
              f"— {drift:.1f}h drift  · {note}")
        rc = 1
    else:
        print(f"  OK {name:<22} ran {time.strftime('%H:%M', lt)}, within {tol_h}h of ~{want_hour:02d}:00")
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
    def _live_refs(_n):
        _h = subprocess.run(["grep", "-rlw", _n, REPO_ROOT, "--include=*.py",
                             "--include=*.js", "--include=*.sh"],
                            capture_output=True, text=True)
        _out = []
        for _f in _h.stdout.splitlines():
            # A GIT WORKTREE IS THE SAME CODE, NOT ANOTHER CALLER. .claude/worktrees/
            # holds transient checkouts of this repo, so every reference was being
            # counted once per worktree and the register reported roughly 3x the
            # real number — entity-formation-leads.json read "24 file(s)" against 6
            # actual callers on 2026-08-09. An inflated count is not a harmless
            # cosmetic: this register exists to answer "is it safe to drop yet",
            # and a number nobody trusts cannot answer it. _to_delete/ is excluded
            # for the same reason — a file staged for deletion is not a live caller.
            # Match "/worktrees/" ANYWHERE, not just under .claude/: worktrees live
            # in at least three places (.claude/worktrees/ for agent isolation and
            # out/review-council/worktrees/ for panel runs), and excluding only the
            # first still left the count at 2x.
            if ("/migrations/" in _f or "node_modules" in _f or "/corpus/" in _f
                    or "/worktrees/" in _f or "/_to_delete/" in _f
                    or f"import_{_n}" in _f):
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
            print(f"  \u26a0\ufe0e {_name:<22} {_kind}, still referenced by {len(_files)} file(s): "
                  + ", ".join(os.path.basename(f) for f in _files[:4]))
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
print("REG\t" + "\t".join(sorted(TARGETS)))
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
    _registered, _seen, _bad = set(), {}, []
    for _line in _ep.stdout.splitlines():
        _c = _line.split("\t")
        if _c[0] == "REG":
            _registered = {x for x in _c[1:] if x}
        elif _c[0] == "ROW" and len(_c) >= 4:
            _seen[_c[1]] = (_c[2], _c[3])
    _unreg = sorted(k for k in _seen if k not in _registered)
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

try:
    _fal = os.path.join(REPO_ROOT, "ops", "fetch-allowlist.py")
    if os.path.exists(_fal):
        _p = subprocess.run([sys.executable, _fal, "--check"],
                            capture_output=True, text=True, timeout=60)
        _line = (_p.stdout or "").strip().splitlines()
        _line = _line[0].split(": ", 1)[-1] if _line else "(no output)"
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
# out/exports/ STAGING by default; only CARR_EXPORT_LIVE=1 reaches the vault
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

sys.exit(rc)
