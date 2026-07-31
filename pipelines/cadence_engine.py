#!/usr/bin/env python3
"""
cadence_engine.py — ORDER 14(a)/(b), wave2-design §2e. The pipeline keeps moving
without anyone remembering it.

WHAT IT DOES
  Reads `cadence_rule` (rules are ROWS, never code — Joe grows the table by
  sentence, not by deploy) and spawns `next_action` rows under the automation
  actor for:
    * on_complete — a next_action on a matching subject reached status 'done',
      so its successor is due interval_days later.
    * on_date     — a dated trigger arrived. Today the only wired source is the
      lease-event countdown off `lead.est_lease_event` (lane 'lease_event');
      any other on_date lane is REPORTED as unwired, never silently ignored.

THE HARD GUARD, AND IT IS NOT A RULE ROW (ORDER 14a, verbatim: "HARD-CODED
GUARD regardless of rules data")
  A subject whose client is `cold` or `paused` is SKIPPED and counted, no matter
  what the rules table says. Joe's shared rule, in his words:

    "a cold client is usually one where they are probably ghosting us. we dont
     want to pester them. we have plenty of business and psychologically we find
     that operating with an abundance mindset gives us more credibility and
     leverage when a client ghosts us."

  A cold or paused subject re-enters cadence only by a HUMAN act — a status
  change. There is deliberately no rule row, no flag and no override argument
  that turns this off: a guard you can switch off in data is a guard that gets
  switched off at 2am by a job nobody is watching. The skip count is printed on
  every run so the exclusion is visible rather than silent.
  Scope: `client` subjects by their own status; `deal` and `lead` subjects by
  the status of the client they belong to; `vendor` subjects are never in scope
  (a vendor has no client status — the abundance rule is about clients).

THE RULE'S OWN SCOPE (ORDER 19c, column added by 0021)
  `cadence_rule.status_filter` is a nullable text[] of client_status slugs. NULL
  keeps the pre-0021 behaviour (every subject the hard guard allows). A list
  NARROWS the rule to subjects whose governing client status is in it — the
  seeded nurture45 row carries {engaged}, so "engaged-client nurture" finally
  means what its name says (ORDER 14 reported that gap; this closes it). It can
  only ever narrow: the hard guard above runs first, so a row saying
  status_filter={cold} still spawns nothing.

IDEMPOTENCE (ORDER 14a: "key on (rule_id, completed_action_id)")
  Every spawn writes an `event` row, verb 'cadence-spawn', carrying
  idempotency_key 'cadence:<rule_id>:<completed_action_id>' (on_complete) or
  'cadence:<rule_id>:date:<subject_id>:<milestone_date>' (on_date). The engine
  reads those keys before it spawns, so a rule firing twice for the same
  completion spawns once, forever, across any number of reruns. No new table:
  `event` is the ledger this system already keeps, and a spawn IS a recorded
  fact, not bookkeeping beside one.

WHAT IT NEVER DOES
  * Never drops or overwrites a human's ball. `set-next-action` drops YOUR own
    previous open ball on purpose; this engine has no such licence. If the owner
    already holds an open action on the subject, the spawn is SKIPPED and
    counted — a person holds one ball per subject and the automation does not
    get to take it off them.
  * Never notifies, sends, or pushes anything. That is ORDER 16's layer, and
    weekends-off and quiet hours bind THERE, not here — the clock does not stop.
  * Never invents an owner. A ball with no holder is not a ball; unownable
    subjects are skipped and counted.

Usage:
  DATABASE_URL=... .venv/bin/python pipelines/cadence_engine.py           # dry run (default)
  DATABASE_URL=... .venv/bin/python pipelines/cadence_engine.py --apply   # spawn
Writes out/cadence-latest.md either way — one STABLE filename, overwritten every
run, so this reporter can never become an accumulator that needs a lifecycle
rule. The one-line summary goes to stdout, which the nightly chain appends to
out/nightly.log (self-trimming).

EXIT CODES
  0  ran (including "nothing to do" and "no rules seeded", both of which are
     honest, reportable states rather than failures)
  78 EX_CONFIG — no database URL. The nightly chain treats 78 as NOT CONFIGURED
     and does not fail the night over it.
"""

import argparse
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import psycopg

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "out"
REPORT = OUT / "cadence-latest.md"

SPAWN_VERB = "cadence-spawn"
AUTOMATION_SLUG = "automation"          # actor.slug; kind='automation'
COLD_CLASS = ("cold", "paused")         # the abundance rule's two statuses

# on_complete lookback. A rule added today should not spawn a ball for something
# somebody finished last year — but the cutoff is stated, and completions older
# than it are COUNTED in the report rather than vanishing. Overridable by a
# system_config row (`cadence.lookback_days`) so tuning stays a sentence.
DEFAULT_LOOKBACK_DAYS = 365

# on_date dispatch. cadence_rule has no source-field column, so the LANE is the
# dispatch key: lane 'lease_event' + subject_type 'lead' reads lead.est_lease_event.
# A new dated source is a small change here PLUS a row; on_complete rules stay
# pure data. Flagged in the ORDER 14 report as a shape observation, not invented
# schema.
ON_DATE_SOURCES = {
    ("lease_event", "lead"): ("lead", "est_lease_event"),
}


# ── helpers ──────────────────────────────────────────────────────────────────

def fmt(template, **kw):
    """Fill an action_template. A missing token is left visible, never blanked —
    a next_action reading 'Nurture touch — {subject}' is obviously wrong, while
    one reading 'Nurture touch — ' looks finished and is not."""
    out = template or ""
    for k, v in kw.items():
        out = out.replace("{" + k + "}", str(v) if v is not None else "{" + k + "}")
    return out.strip()


def load_context(conn):
    """Everything the run needs about subjects, read once."""
    ctx = {}

    ctx["automation"] = conn.execute(
        "select id from actor where slug=%s", (AUTOMATION_SLUG,)).fetchone()
    if ctx["automation"] is None:
        raise SystemExit("actor '%s' does not exist — stop and report" % AUTOMATION_SLUG)
    ctx["automation"] = ctx["automation"][0]

    ctx["lookback_days"] = DEFAULT_LOOKBACK_DAYS
    row = conn.execute(
        "select value from system_config where key='cadence.lookback_days'").fetchone()
    if row and isinstance(row[0], int):
        ctx["lookback_days"] = row[0]

    # subject display + owner + the client whose status governs the guard.
    # client: its own status. deal: its client's. lead: its client's, when the
    # lead has been converted (lead.client_id is set on conversion). vendor: no
    # client, so never guarded.
    subj = {}       # (subject_type, subject_id) -> dict(name, ref, owner, status)
    for sid, ref, name, status, owner in conn.execute("""
            select c.id, c.roster_ref, p.name, c.status, c.owner_id
              from client c join party p on p.id = c.party_id""").fetchall():
        subj[("client", sid)] = dict(name=name, ref=ref, owner=owner, status=status)
    for sid, ref, name, status, owner in conn.execute("""
            select l.id, l.registry_ref, p.name, c.status, l.owner_id
              from lead l join party p on p.id = l.party_id
              left join client c on c.id = l.client_id""").fetchall():
        subj[("lead", sid)] = dict(name=name, ref=ref, owner=owner, status=status)
    for sid, ref, name, owner in conn.execute("""
            select v.id, v.vendor_ref, p.name, v.owner_id
              from vendor v join party p on p.id = v.party_id""").fetchall():
        subj[("vendor", sid)] = dict(name=name, ref=ref, owner=owner, status=None)
    # deal has no owner column; the CURRENT lead participant is the owner.
    for sid, name, status, owner in conn.execute("""
            select d.id, d.name, c.status,
                   (select dp.actor_id from deal_participant dp
                     where dp.deal_id = d.id and dp.role='lead' and dp.to_at is null
                       and dp.actor_id is not null limit 1)
              from deal d join client c on c.id = d.client_id""").fetchall():
        subj[("deal", sid)] = dict(name=name, ref=None, owner=owner, status=status)
    ctx["subjects"] = subj

    # already-open balls, so the engine can refuse to touch one
    ctx["open_balls"] = {
        (t, s, o) for t, s, o in conn.execute(
            "select subject_type, subject_id, owner_id from next_action where status='open'"
        ).fetchall()}

    # every key this engine has ever written — idempotence, read once
    ctx["spawned"] = {
        r[0] for r in conn.execute(
            "select idempotency_key from event where verb=%s and idempotency_key is not null",
            (SPAWN_VERB,)).fetchall()}

    return ctx


def guard_reason(ctx, stype, sid, status_filter=None):
    """The hard guard + the two honest refusals + the rule's own scope.

    ORDER OF EVALUATION IS THE WHOLE POINT. The hard cold/paused guard runs
    FIRST and is not expressible in rules data; `status_filter` (0021, ORDER
    19c) runs after it and can only NARROW. A rule row carrying
    status_filter={cold} therefore still spawns nothing — data cannot re-admit
    a subject the abundance rule excluded.
    """
    s = ctx["subjects"].get((stype, sid))
    if s is None:
        return "subject not found (merged or deleted record)"
    if s["status"] in COLD_CLASS:
        return "abundance rule: client status '%s'" % s["status"]
    if status_filter:
        if s["status"] not in status_filter:
            return ("rule is scoped to status %s; this subject is %s"
                    % ("{" + ", ".join(status_filter) + "}",
                       ("'%s'" % s["status"]) if s["status"] else "unstatused"))
    if s["owner"] is None:
        return "no owner on record — a ball with no holder is not a ball"
    return None


# ── the two trigger types ────────────────────────────────────────────────────

def plan_on_complete(conn, ctx, rule, today, plan, skips):
    rid, lane, stype, _trig, interval, template, _active, sfilter = rule
    if interval is None:
        skips.append((lane, "rule has no interval_days — an on_complete rule without "
                            "one cannot date its successor", None))
        return
    rows = conn.execute("""
        select n.id, n.subject_id, n.owner_id, n.updated_at::date, n.description
          from next_action n
         where n.status = 'done' and n.subject_type = %s
         order by n.updated_at""", (stype,)).fetchall()
    for aid, sid, owner, done_on, desc in rows:
        key = "cadence:%s:%s" % (rid, aid)
        if key in ctx["spawned"]:
            skips.append((lane, "already spawned for this completion", key))
            continue
        if (today - done_on).days > ctx["lookback_days"]:
            skips.append((lane, "completed %d days ago, past the %d-day lookback"
                          % ((today - done_on).days, ctx["lookback_days"]), key))
            continue
        why = guard_reason(ctx, stype, sid, sfilter)
        if why:
            skips.append((lane, why, key))
            continue
        s = ctx["subjects"][(stype, sid)]
        # the completed action's own owner inherits the successor: the ball
        # stays with whoever was carrying it.
        own = owner or s["owner"]
        if (stype, sid, own) in ctx["open_balls"]:
            skips.append((lane, "owner already holds an open ball on this subject "
                                "— the engine never takes a human's ball", key))
            continue
        plan.append(dict(
            key=key, rule_id=rid, lane=lane, subject_type=stype, subject_id=sid,
            owner=own, due_on=date.fromordinal(done_on.toordinal() + interval),
            description=fmt(template, subject=s["name"], ref=s["ref"] or "",
                            date=done_on.isoformat()),
            why="on_complete of %s (%s)" % (aid, (desc or "")[:60]),
            display="%s %s" % (s["ref"] or "", s["name"])))


def plan_on_date(conn, ctx, rules_for_lane, lane, stype, today, plan, skips):
    """All on_date rules of one lane at once — the milestone windows have to know
    about each other. Each rule's window runs from (event - interval_days) up to
    the NEXT TIGHTER milestone; the tightest runs to the event itself.

    Without bounded windows, a lead that lands two months before its lease event
    would trip T-18, T-12, T-6 and T-3 in turn on four consecutive nights. A T-18
    action is a T-18 action; when it is discovered late it is stale, not owed.
    """
    src = ON_DATE_SOURCES.get((lane, stype))
    if src is None:
        skips.append((lane, "on_date lane '%s' has no wired date source for subject "
                            "type '%s' — reported, never guessed" % (lane, stype), None))
        return
    table, column = src
    rules = sorted(rules_for_lane, key=lambda r: -(r[4] or 0))   # widest first
    rows = conn.execute(
        "select id, %s from %s where %s is not null" % (column, table, column)).fetchall()
    for i, rule in enumerate(rules):
        rid, _lane, _st, _tg, interval, template, _a, sfilter = rule
        if interval is None:
            skips.append((lane, "on_date rule has no interval_days — nothing to count "
                                "back from the date", None))
            continue
        tighter = rules[i + 1][4] if i + 1 < len(rules) else None
        for sid, event_on in rows:
            fire_on = date.fromordinal(event_on.toordinal() - interval)
            window_end = (date.fromordinal(event_on.toordinal() - tighter)
                          if tighter is not None else event_on)
            key = "cadence:%s:date:%s:%s" % (rid, sid, event_on.isoformat())
            if key in ctx["spawned"]:
                continue                      # silent: this is the normal state
            if not (fire_on <= today <= window_end):
                continue                      # window not open (or already past)
            why = guard_reason(ctx, stype, sid, sfilter)
            if why:
                skips.append((lane, why, key))
                continue
            s = ctx["subjects"][(stype, sid)]
            if (stype, sid, s["owner"]) in ctx["open_balls"]:
                skips.append((lane, "owner already holds an open ball on this subject "
                                    "— the engine never takes a human's ball", key))
                continue
            plan.append(dict(
                key=key, rule_id=rid, lane=lane, subject_type=stype, subject_id=sid,
                owner=s["owner"], due_on=max(fire_on, today),
                description=fmt(template, subject=s["name"], ref=s["ref"] or "",
                                date=event_on.isoformat()),
                why="on_date %s = %s, milestone opens %s"
                    % (column, event_on.isoformat(), fire_on.isoformat()),
                display="%s %s" % (s["ref"] or "", s["name"])))


# ── report ───────────────────────────────────────────────────────────────────

def write_report(path, rules, plan, skips, applied, wrote, lookback):
    cold = [s for s in skips if s[1].startswith("abundance rule")]
    L, A = [], None
    A = L.append
    A("# Cadence engine — %s" % ("APPLIED" if applied else "DRY RUN"))
    A("")
    A("Generated %s · %s"
      % (datetime.now(timezone.utc).isoformat(timespec="seconds"),
         "wrote to the database" if applied else "nothing written"))
    A("")
    A("## Counts")
    A("")
    A("- active rules read: **%d**" % len(rules))
    A("- actions the rules want to spawn: **%d**" % len(plan))
    A("- actions actually spawned this run: **%d**" % wrote)
    A("- **skipped %d per the abundance rule** (client status cold or paused)" % len(cold))
    A("- other skips (already spawned, ball held, no owner, past the %d-day lookback): **%d**"
      % (lookback, len(skips) - len(cold)))
    A("")
    if not rules:
        A("**No active cadence_rule rows.** The engine is armed and reads an empty "
          "table honestly — rules are rows, so seeding them is a write, not a deploy.")
        A("")
    A("## Rules read")
    A("")
    if rules:
        A("| lane | subject | trigger | interval_days | status filter |")
        A("|---|---|---|---|---|")
        for r in rules:
            A("| %s | %s | %s | %s | %s |"
              % (r[1], r[2], r[3], r[4],
                 ("{" + ", ".join(r[7]) + "}") if r[7] else "any (hard guard only)"))
    else:
        A("None.")
    A("")
    A("## Spawned" if applied else "## Would spawn")
    A("")
    if plan:
        A("| lane | subject | due | description | why |")
        A("|---|---|---|---|---|")
        for p in plan:
            A("| %s | %s %s | %s | %s | %s |"
              % (p["lane"], p["subject_type"], p["display"].strip(),
                 p["due_on"], p["description"], p["why"]))
    else:
        A("Nothing. No completed action and no dated trigger matched an active rule.")
    A("")
    A("## Skipped by the abundance rule (cold / paused — never pester a ghost)")
    A("")
    if cold:
        A("| lane | reason | key |")
        A("|---|---|---|")
        for lane, why, key in cold:
            A("| %s | %s | %s |" % (lane, why, key or ""))
    else:
        A("None matched a rule this run.")
    A("")
    A("## Other skips")
    A("")
    other = [s for s in skips if not s[1].startswith("abundance rule")]
    if other:
        A("| lane | reason | key |")
        A("|---|---|---|")
        for lane, why, key in other[:200]:
            A("| %s | %s | %s |" % (lane, why, key or ""))
        if len(other) > 200:
            A("")
            A("_(%d more not listed)_" % (len(other) - 200))
    else:
        A("None.")
    A("")
    path.write_text("\n".join(L) + "\n")


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="spawn the actions (default is a dry run that writes nothing)")
    ap.add_argument("--today", help="YYYY-MM-DD, for rehearsal only")
    a = ap.parse_args()

    # [ORDER 19a] CARR_DB_JOBS_URL is THE name: one nightly-jobs role for every
    # unattended pipeline, not one credential per script. The older names stay as
    # fallbacks so nothing that already works stops working.
    url = (os.environ.get("CARR_DB_JOBS_URL")
           or os.environ.get("CARR_DB_CADENCE_URL")
           or os.environ.get("DATABASE_URL"))
    if not url:
        print("cadence_engine: NOT CONFIGURED — no CARR_DB_JOBS_URL, CARR_DB_CADENCE_URL "
              "or DATABASE_URL. "
              "This engine WRITES (next_action + event), so the read-only exporter "
              "credential in ~/.config/carr/db.env cannot run it. Nothing attempted.",
              file=sys.stderr)
        return 78

    today = date.fromisoformat(a.today) if a.today else date.today()
    OUT.mkdir(exist_ok=True)

    with psycopg.connect(url) as conn:
        ctx = load_context(conn)
        rules = conn.execute("""
            select id, lane, subject_type, trigger, interval_days, action_template,
                   active, status_filter
              from cadence_rule where active order by lane, interval_days desc nulls last
        """).fetchall()

        plan, skips = [], []
        by_lane = {}
        for r in rules:
            if r[3] == "on_complete":
                plan_on_complete(conn, ctx, r, today, plan, skips)
            elif r[3] == "on_date":
                by_lane.setdefault((r[1], r[2]), []).append(r)
        for (lane, stype), group in by_lane.items():
            plan_on_date(conn, ctx, group, lane, stype, today, plan, skips)

        wrote = 0
        if a.apply:
            for p in plan:
                # last-instant re-check: two spawns in one run must not collide on
                # the one-open-ball-per-owner index.
                if (p["subject_type"], p["subject_id"], p["owner"]) in ctx["open_balls"]:
                    skips.append((p["lane"], "owner already holds an open ball on this "
                                             "subject — the engine never takes a human's "
                                             "ball", p["key"]))
                    continue
                r = conn.execute("""
                    insert into next_action
                      (subject_type, subject_id, owner_id, due_on, description, created_by)
                    values (%s,%s,%s,%s,%s,%s) returning id""",
                    (p["subject_type"], p["subject_id"], p["owner"], p["due_on"],
                     p["description"], ctx["automation"])).fetchone()[0]
                conn.execute("""
                    insert into event (occurred_at, actor_id, verb, subject_type, subject_id,
                                       new_value, cause, agent_rationale, idempotency_key)
                    values (now(), %s, %s, %s, %s, %s::jsonb, 'automation_job', %s, %s)""",
                    (ctx["automation"], SPAWN_VERB, p["subject_type"], p["subject_id"],
                     psycopg.types.json.Jsonb(dict(
                         next_action=p["description"], due=p["due_on"].isoformat(),
                         next_action_id=str(r), lane=p["lane"], rule_id=str(p["rule_id"]))),
                     "cadence engine spawned this action from rule %s (%s). Trigger: %s. "
                     "Owner unchanged from the record; the engine never reassigns a ball."
                     % (p["rule_id"], p["lane"], p["why"]),
                     p["key"]))
                ctx["open_balls"].add((p["subject_type"], p["subject_id"], p["owner"]))
                ctx["spawned"].add(p["key"])
                wrote += 1
            conn.commit()

        write_report(REPORT, rules, plan, skips, a.apply, wrote, ctx["lookback_days"])

    cold = sum(1 for s in skips if s[1].startswith("abundance rule"))
    print("cadence engine %s — rules %d · planned %d · spawned %d · "
          "skipped %d per the abundance rule · other skips %d · report %s"
          % ("APPLIED" if a.apply else "DRY RUN", len(rules), len(plan), wrote,
             cold, len(skips) - cold, REPORT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
