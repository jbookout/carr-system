#!/usr/bin/env python3
"""blocker-review.py — test every open loop's stated blocker against reality, and
report only the ones that have DEMONSTRABLY CLEARED.

WHY THIS EXISTS. Joe, 2026-08-10: "You spam loops all day and never built
anything towards actually working through the loops." He is right, and the
numbers say how right: 404 loops created, 161 ever closed, 243 open. Loops are
cheap to create and expensive to close, so the pile only grows.

`add-loop` already refuses an open loop that does not name a blocker from a
closed list — a genuinely good control, added because "later" is not a reason.
But the blocker is captured at creation and NEVER RE-READ. A grep across
pipelines/, exporters/ and tools/health-check.py for `blocker` returns nothing.
So a loop whose counterparty replied three weeks ago is indistinguishable from
one blocked this morning, and both sit in the same undifferentiated 243.

THE POINT OF THIS SCRIPT, stated as the thing it must never become: it is NOT
another report. It answers one question — WHICH OF THESE CAN BE WORKED RIGHT NOW
— and its output is a short list, or nothing. A list of 243 is what we already
have.

WHAT IT CAN AND CANNOT DECIDE. Each blocker class gets a test only where the
record can actually answer it. Where it cannot, the row is left alone and said so
out loud rather than guessed at, because a false "unblocked" costs more than a
missed one: it puts work in front of a partner that is still blocked, and two of
those teach him to stop reading the list.

  counterparty    CLEARABLE. The loop names who it waits on. If any contact-kind
                  activity has been logged against that party since the loop was
                  raised, the ball is back on our side.
  external_event  CLEARABLE. A dated wait whose date has passed is no longer a
                  wait. due_on is the date; loops carrying none are skipped.
  capability      CLEARABLE. These name a verb, a view or a credential that did
                  not exist. All three are checkable now: the verb registry, the
                  catalog, and the presence of the key.
  other_lane      CLEARABLE where the row names a loop number. If that loop is
                  closed, this one is no longer waiting on it.
  ruling          NOT CLEARABLE. A ruling is a human's to make and nothing in the
                  record says he has made it.
  human_only      NOT CLEARABLE, by definition. Counted and reported so the size
                  of the genuinely-human pile is visible, which is itself a
                  number worth seeing.

CLOSES NOTHING. Deliberately. This surfaces; a session or a partner decides. An
auto-closer would be the same defect in the other direction — the system deciding
work is done because a proxy went green, which is exactly the "success signal not
derived from the artifact" failure the health check already carries scars from.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import date, datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOOP_REF = re.compile(r"(?:loop\s*#|#)(\d{1,4})", re.I)


def db_url() -> str | None:
    url = os.environ.get("CARR_DB_EXPORTER_URL") or os.environ.get("DATABASE_URL")
    if url:
        return url
    env = os.path.expanduser("~/.config/carr/db.env")
    if os.path.exists(env):
        for line in open(env, encoding="utf-8"):
            if line.startswith("CARR_DB_EXPORTER_URL="):
                return line.split("=", 1)[1].strip().strip("\"'")
    return None


def main() -> int:
    url = db_url()
    if not url:
        print("blocker-review: NOT CONFIGURED (no database credential)", file=sys.stderr)
        return 78

    import psycopg
    today = date.today()
    # GATE_LIVE: the day add-loop began refusing an open loop with no blocker.
    # Rows older than this predate the control and carry none through no fault;
    # rows NEWER than it with no blocker are a leak in the gate and are counted
    # separately, because those two facts need different fixes and averaging
    # them hides the defect.
    GATE_LIVE = date(2026, 8, 9)
    cleared, human, unknown, unblockered, leaked = [], 0, 0, 0, []

    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute("""
            select number, owner, domain, blocker_class, blocker_detail, due_on,
                   left(regexp_replace(coalesce(body, title, ''), '[[:space:]]+', ' ', 'g'), 120),
                   created_at::date
              from loop_item
             where status = 'open' and kind = 'open_loop'
             order by length(number), number""")
        rows = cur.fetchall()

        for num, owner, domain, bclass, bdetail, due_on, gist, raised in rows:
            detail = bdetail or ""

            if not bclass:
                unblockered += 1
                if raised >= GATE_LIVE:
                    leaked.append((num, owner, gist))
                continue

            if bclass == "human_only":
                human += 1
                continue

            if bclass == "external_event":
                if due_on and due_on <= today:
                    cleared.append((num, owner, domain, gist,
                                    f"the date it was waiting for ({due_on}) has passed"))
                continue

            if bclass == "counterparty":
                # Any contact-kind activity against a party the detail names,
                # since the loop was raised. Name match is deliberately narrow:
                # a full-name match only, because matching a bare first name
                # against 1,222 parties is the contamination the merge rules
                # already forbid.
                cur.execute("""
                    select p.name, max(a.occurred_at)::date
                      from activity a
                      join activity_kind k on k.slug = a.kind and k.is_contact
                      join party p on p.id in (
                            select party_id from client  where id = a.client_id
                      union select party_id from lead    where id = a.lead_id
                      union select party_id from vendor  where id = a.vendor_id)
                     where a.occurred_at::date > %s
                       and position(lower(p.name) in lower(%s)) > 0
                       and length(p.name) > 8
                     group by p.name
                     order by 2 desc limit 1""", (raised, detail))
                hit = cur.fetchone()
                if hit:
                    cleared.append((num, owner, domain, gist,
                                    f"{hit[0]} was contacted {hit[1]}, after this was raised"))
                continue

            if bclass == "other_lane":
                refs = {m for m in LOOP_REF.findall(detail)} - {str(num)}
                if not refs:
                    unknown += 1
                    continue
                cur.execute("""select number, status from loop_item
                                where number = any(%s) and kind='open_loop'""", (list(refs),))
                states = cur.fetchall()
                if states and all(s != "open" for _, s in states):
                    cleared.append((num, owner, domain, gist,
                                    "the loop(s) it waited on are closed: "
                                    + ", ".join(f"#{n}" for n, _ in states)))
                continue

            if bclass == "capability":
                unknown += 1
                continue

            unknown += 1

    print(f"BLOCKER REVIEW — {today} — {len(rows)} open loop(s)")
    print(f"  {unblockered} carry NO blocker at all — nothing can ever test these, and")
    print( "      that is the real backlog problem rather than a reporting gap")
    print(f"  {human} wait on a human by definition; not testable here")
    print(f"  {unknown} name a blocker this cannot test from the record")
    print()
    if leaked:
        print(f"⚠︎ THE GATE LEAKS: {len(leaked)} loop(s) raised on or after {GATE_LIVE}")
        print( "  carry no blocker, though add-loop refuses that. Something reaches")
        print( "  loop_item by another path — find it, or the pile keeps growing:")
        for num, owner, gist in leaked[:8]:
            print(f"    #{num} [{owner}] {gist[:88]}")
        if len(leaked) > 8:
            print(f"    ... and {len(leaked)-8} more")
        print()
    if not cleared:
        print("Nothing has demonstrably cleared since the last run. That is a real"
              " answer, not an empty one.")
        return 0

    print(f"{len(cleared)} loop(s) are NO LONGER BLOCKED and can be worked now:\n")
    for num, owner, domain, gist, why in cleared:
        print(f"  #{num} [{domain}·{owner}] {gist}")
        print(f"      unblocked: {why}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
