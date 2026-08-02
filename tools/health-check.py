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
"""
import os, sys, glob, time

REPO_ROOT = os.path.expanduser("~/carr-system")

VAULT = os.environ.get("CARR_VAULT",
    "/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI")

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
    ("Radar digest",      "Automation/radar/radar-digest-2*.md", 9, [],
     "Monday radar run (radar-digest-sop.md)"),
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
    # role tap lands is the DESIGNED state (exit 78, not a fault). Brief pack and
    # review queue going stale on a Monday is the weekends-off rule showing
    # through, since the heartbeat stands down Sat/Sun and JOB 4a runs before
    # JOB 4b rebuilds them — flagged to Fable rather than padded on my own
    # judgment, because the order set the cadence explicitly at 26h.
    ("JOB brief-pack",    os.path.expanduser("~/carr-system/out/brief-pack/brief-pack-latest.md"), 26/24, [],
     "run.sh brief-pack (heartbeat JOB 4b)"),
    ("JOB monday-agenda", os.path.expanduser("~/carr-system/out/brief-pack/monday-agenda.md"), 26/24, [],
     "run.sh brief-pack — the Monday brief's own input, watched separately because "
     "it has its own consumer"),
    ("JOB review-queue",  os.path.expanduser("~/carr-system/out/review-queue/review-queue.html"), 26/24, [],
     "run.sh review-queue (heartbeat JOB 4b)"),
    ("JOB matcher",       os.path.expanduser("~/carr-system/out/availability-matches.md"), 26/24, [],
     "availability_matcher.py, step 2 of the nightly chain (SKIPs until CARR_DB_JOBS_URL exists)"),
    ("JOB cadence",       os.path.expanduser("~/carr-system/out/cadence-latest.md"), 26/24, [],
     "cadence_engine.py, step 1 of the nightly chain (SKIPs until CARR_DB_JOBS_URL exists)"),
    ("Joe calendar feed", "DNA/Team/calendar-latest.ics", 4, [],
     "fetch-calendar.sh; business days only"),
    ("Dell calendar feed","DNA/Team/calendar-latest-dell.ics", 4, [],
     "KNOWN BLOCKED on Dell's OS update (memory: dell-calendar-fetch-blocked) — expected stale until he updates"),
]

def newest(pattern):
    hits = glob.glob(os.path.join(VAULT, pattern))
    return max(hits, key=os.path.getmtime) if hits else None

def age_days(path):
    return (time.time() - os.path.getmtime(path)) / 86400

rc = 0
print(f"Façade check (rule 28) — {time.strftime('%Y-%m-%d %H:%M')} — outputs, not schedules")
for name, out_pat, max_age, inputs, note in WATCH:
    out = newest(out_pat)
    if not out:
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
     "the task fired on wake — step 3 of that chain is the encrypted backup, so it is "
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
    for _name, _kind, _repl, _after in _rows:
        _hits = subprocess.run(["grep", "-rl", _name, REPO_ROOT, "--include=*.py",
                                "--include=*.js", "--include=*.sh"],
                               capture_output=True, text=True)
        _files = [f for f in _hits.stdout.splitlines()
                  if "/migrations/" not in f and "node_modules" not in f
                  and "/corpus/" not in f and f"import_{_name}" not in f]
        _due = bool(_after) and _after <= time.strftime("%Y-%m-%d")
        if not _files:
            _flag = "SAFE TO DROP" if _due else f"unused; scheduled {_after or 'no date'}"
            print(f"  OK {_name:<22} {_kind}, 0 code refs — {_flag}"
                  + (f"  (replaced by {_repl})" if _repl else ""))
        else:
            print(f"  \u26a0\ufe0e {_name:<22} {_kind}, still referenced by {len(_files)} file(s): "
                  + ", ".join(os.path.basename(f) for f in _files[:4]))
            rc = 1

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
    _cs = importlib.util.module_from_spec(_spec)
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

sys.exit(rc)
