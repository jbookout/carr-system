#!/usr/bin/env python3
"""
backfill_lease_event.py — ORDER 19(d). Turn the `est_lease_event_raw` shim into
structured dates where, and only where, a date is actually there.

WHY IT MATTERS. `lead.est_lease_event` is the ONLY wired source for the cadence
engine's on_date half (the T-18/T-12/T-6/T-3 lease-event countdowns). ORDER 14
measured it: the column is non-null on ZERO of 207 leads, so that half of the
engine is armed and cannot fire. This is its input path.

THE GRAMMAR, AND IT REFUSES RATHER THAN GUESSES (the order: "where a date parses
unambiguously (YYYY-MM or better); ambiguous stays null and counts in the
report"). Accepted, all resolving to month precision or finer:
    2027-04-15 · 2027/04/15 · 2027-04 · 2027/04 · 04-2027 · 04/2027
    Apr 2027 · April 2027 · Apr-2027
Refused as AMBIGUOUS, every one of them counted and listed:
    M2M · TBD · a bare year (2027 is twelve possible months) · a quarter
    (Q3 2027 is three) · anything with a range, a tilde, "mid", "late", "early"
    · any string this grammar does not recognise.
A refusal is never a failure — it is the shim doing its job, which is to carry
what a human actually wrote until a human resolves it.

THE RAW SHIM IS NOT TOUCHED, EVER (amendment 5 sunset rules, the order's own
stop rule). `est_lease_event_raw` keeps the verbatim string after this runs;
the two columns say different things and both stay true. The exports already
render `coalesce(est_lease_event::text, est_lease_event_raw)`, so a backfilled
row's Est-Lease-Event cell changes from the raw string to the ISO date — that
is a real, visible export diff and it is reported below before it happens.

PROVENANCE. Every update writes one `event` row, verb `backfill-lease-event`,
cause `import_migration`, actor `system` (the importer precedent), carrying the
raw string and the parsed date, keyed `order19:lease-event:<lead id>` so a rerun
is a no-op rather than a second claim.

CREDENTIAL. This UPDATES `lead`, which neither the exporter nor the nightly-jobs
role can do (both proved by running them). It is an owner-credential step and
therefore Joe's tap, exactly like ORDER 17's parse.

Usage:
    DATABASE_URL=<owner url> .venv/bin/python pipelines/backfill_lease_event.py           # dry run
    DATABASE_URL=<owner url> .venv/bin/python pipelines/backfill_lease_event.py --apply

Exit codes: 0 ran · 78 EX_CONFIG (no credential) · 3 nothing to do (also a pass).
"""

import argparse
import os
import re
import sys
from datetime import date

import psycopg

VERB = "backfill-lease-event"
ACTOR_SLUG = "system"

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun",
     "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}

# Anything hinting at a range or an approximation is refused before parsing, so
# "mid 2027" and "2027-2028" can never fall through to a lucky regex match.
FUZZY = re.compile(r"(mid|early|late|~|\?|--|\bto\b|\bthru\b|\bq[1-4]\b|\bm2m\b|\btbd\b)", re.I)


def parse(raw):
    """Returns (date, None) or (None, reason). Month precision resolves to the
    first of the month, which is what a countdown needs and is the only reading
    that does not invent a day."""
    s = (raw or "").strip()
    if not s:
        return None, "empty"
    if FUZZY.search(s):
        return None, "not a date, or coarser than a month"
    s2 = s.replace(".", "-").replace("/", "-").replace(",", " ").strip()

    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", s2)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        return _mk(y, mo, d, s)
    m = re.fullmatch(r"(\d{4})-(\d{1,2})", s2)
    if m:
        return _mk(int(m.group(1)), int(m.group(2)), 1, s)
    m = re.fullmatch(r"(\d{1,2})-(\d{4})", s2)
    if m:
        return _mk(int(m.group(2)), int(m.group(1)), 1, s)
    m = re.fullmatch(r"([A-Za-z]{3,9})-?\s*(\d{4})", s2)
    if m and m.group(1)[:3].lower() in MONTHS:
        return _mk(int(m.group(2)), MONTHS[m.group(1)[:3].lower()], 1, s)
    m = re.fullmatch(r"(\d{1,2})-(\d{1,2})-(\d{4})", s2)
    if m:
        # US convention only, and only when the first field cannot be a day
        # under any other reading is it safe. 09-01-2027 is month-first here;
        # 01-02-2027 is refused because two readings are both plausible.
        a, b, y = (int(x) for x in m.groups())
        if b > 12 >= a:
            return _mk(y, a, b, s)
        return None, "day/month order is ambiguous (both fields <= 12)"
    if re.fullmatch(r"\d{4}", s2):
        return None, "year only — twelve possible months"
    return None, "unrecognised format"


def _mk(y, mo, d, raw):
    if not (1900 < y < 2100 and 1 <= mo <= 12 and 1 <= d <= 31):
        return None, "out of range"
    try:
        return date(y, mo, d), None
    except ValueError:
        return None, "not a real calendar date (%s)" % raw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="write the parsed dates (default is a dry run that writes nothing)")
    a = ap.parse_args()

    url = (os.environ.get("DATABASE_URL")
           or os.environ.get("CARR_DB_WRITER_URL")
           or os.environ.get("CARR_IMPORT_DB_URL"))
    if not url:
        print("backfill_lease_event: NOT CONFIGURED — this UPDATES `lead`, so it needs "
              "an owner/writer credential (DATABASE_URL). The exporter and the "
              "nightly-jobs roles cannot write it. Nothing attempted.", file=sys.stderr)
        return 78

    with psycopg.connect(url) as conn:
        actor = conn.execute("select id from actor where slug=%s", (ACTOR_SLUG,)).fetchone()
        if actor is None:
            print("actor '%s' does not exist — stop and report" % ACTOR_SLUG, file=sys.stderr)
            return 1
        actor = actor[0]
        done = {r[0] for r in conn.execute(
            "select idempotency_key from event where verb=%s and idempotency_key is not null",
            (VERB,)).fetchall()}

        rows = conn.execute("""
            select id, registry_ref, est_lease_event_raw, est_lease_event
              from lead
             where est_lease_event_raw is not null
             order by registry_ref""").fetchall()

        parsed, ambiguous, already, wrote = [], [], [], 0
        for lid, ref, raw, current in rows:
            if current is not None:
                already.append((ref, raw, current))
                continue
            d, why = parse(raw)
            (parsed if d else ambiguous).append((lid, ref, raw, d or why))

        if a.apply:
            for lid, ref, raw, d in parsed:
                key = "order19:lease-event:%s" % lid
                if key in done:
                    continue
                conn.execute(
                    "update lead set est_lease_event=%s, updated_by=%s where id=%s and est_lease_event is null",
                    (d, actor, lid))
                conn.execute("""
                    insert into event (occurred_at, actor_id, verb, subject_type, subject_id,
                                       field, old_value, new_value, cause, agent_rationale,
                                       idempotency_key)
                    values (now(), %s, %s, 'lead', %s, 'est_lease_event', %s::jsonb, %s::jsonb,
                            'import_migration', %s, %s)""",
                    (actor, VERB, lid,
                     psycopg.types.json.Jsonb({"est_lease_event": None}),
                     psycopg.types.json.Jsonb({"est_lease_event": d.isoformat()}),
                     "ORDER 19(d): parsed from est_lease_event_raw %r, which resolves to a "
                     "month or better. The raw shim is unchanged; nothing was inferred beyond "
                     "the first of the stated month." % raw,
                     key))
                wrote += 1
            conn.commit()

        # ── report ──────────────────────────────────────────────────────────
        print("backfill_lease_event %s" % ("APPLIED" if a.apply else "DRY RUN"))
        print("  leads carrying a raw shim value : %d" % len(rows))
        print("  parseable (month or better)     : %d" % len(parsed))
        print("  ambiguous, left null            : %d" % len(ambiguous))
        print("  already structured, untouched   : %d" % len(already))
        print("  rows written this run           : %d" % wrote)
        print()
        print("  SAMPLES — every row is listed when there are five or fewer, because a")
        print("  sample of a population of two is the population.")
        for _lid, ref, raw, d in parsed[:5]:
            print("    PARSED    %-8s %-24r -> %s" % (ref, raw, d))
        for _lid, ref, raw, why in ambiguous[:5]:
            print("    AMBIGUOUS %-8s %-24r -> stays null (%s)" % (ref, raw, why))
        for ref, raw, cur in already[:5]:
            print("    ALREADY   %-8s %-24r -> %s (untouched)" % (ref, raw, cur))
        if parsed:
            print()
            print("  EXPORT EFFECT, stated before it lands: v_export_leads renders")
            print("  coalesce(est_lease_event::text, est_lease_event_raw), so each parsed row's")
            print("  Est-Lease-Event cell changes from the raw string to the ISO date. That is")
            print("  %d cell diff(s) on the next reconcile, and it is the intended change."
                  % len(parsed))

    return 0 if (parsed or wrote) else 3


if __name__ == "__main__":
    sys.exit(main())
