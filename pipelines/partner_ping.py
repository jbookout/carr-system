#!/usr/bin/env python3
"""partner_ping.py — buzz Joe when his partner needs him, within a couple of minutes.

WHY THIS EXISTS (Joe, 2026-08-03, after Dell filed T63 to the team board): "he
shouldnt have to wait until my claude reads the team board at some random time in
the future." The classification layer already existed — action_required is the
cross-brain interrupt, drift_critical is the daily-escalation flag, marker
'decision' is the open question. What did not exist was any transport. Nothing
pushed; every channel waited for Joe to open a session and for a rule to be obeyed.

WHY THIS IS A PLAIN SCRIPT AND NOT A ROUTINE. A claude.ai routine can poll, and
Joe asked about exactly that. But a routine run is a FULL CLAUDE SESSION: polling
every five minutes is ~288 agent runs a day to answer "did anything arrive?" This
does one query and fires a notification for effectively nothing, so it can run
every two minutes without anyone thinking about cost. The routine is the right
tool for the SECOND layer — reading the thing and briefing on it — and should be
woken by this, not run blind.

YELLOW VERSUS BLUE (Joe's framing, and the whole point of the design): "what makes
the color yellow is that it asks a question. if it's blue that means its just
making a statement." A ping that needs an ANSWER has to look different from one
that is merely telling him something. The record already encodes this, so nothing
is guessed: marker 'decision' is the open question, action_required is the
cross-brain interrupt, and drift_critical is the one that compounds if ignored.
Those go out as ❓ (yellow glyph). Everything else is ℹ️ (blue glyph).

WHAT IT WILL NOT DO: it never writes to the record, never closes a row, and never
decides anything. It reads, it buzzes, it remembers what it has already buzzed
about. The board stays the place work actually happens.

RUN IT:  .venv/bin/python tools/db-tap.py run pipelines/partner_ping.py
         (db-tap supplies DATABASE_URL; that is the sanctioned credential path)
         --dry-run   print what WOULD fire, notify nothing, do not advance state
         --since ISO override the watermark for a one-off catch-up
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import psycopg

STATE = Path.home() / ".config" / "carr" / "partner-ping.json"
WHOAMI = Path.home() / ".config" / "carr" / "partner"

# SYMMETRIC BY DESIGN (Joe, 2026-08-03: "i want both dell and i to have this
# script capability"). One copy in the repo, which both partners clone, and it
# watches whichever partner is NOT the one running it. Nothing here is
# Joe-shaped: hardcoding a slug would have meant Dell running a script that
# quietly watched himself and never fired.
PARTNERS = ("joe", "dell")
DISPLAY = {"joe": "Joe", "dell": "Dell"}


def resolve_self():
    """Who is running this? Env var wins, then ~/.config/carr/partner.

    Deliberately explicit rather than inferred from hostname or git config: a
    wrong guess here produces a watcher that silently never fires, which is
    indistinguishable from a quiet week and is the worst failure this can have.
    """
    who = (os.environ.get("CARR_PARTNER") or "").strip().lower()
    if not who:
        try:
            who = WHOAMI.read_text().strip().lower()
        except FileNotFoundError:
            who = ""
    if who not in PARTNERS:
        sys.exit(
            f"who is this? set CARR_PARTNER to one of {PARTNERS}, or write it "
            f"into {WHOAMI}. Refusing to guess — a wrong guess makes this watch "
            f"the wrong partner and never fire."
        )
    return who

# A row is a QUESTION — Joe's yellow — when it needs an answer from him rather
# than merely informing him. All three signals are already in the schema.
QUESTION_MARKERS = ("decision",)
QUESTION_KINDS = ("action_required",)


def load_state():
    try:
        return json.loads(STATE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2))


def is_question(row):
    """Yellow or blue. Joe's line: yellow asks something of me, blue just tells me.

    Structural signals first, because they are recorded rather than inferred:
    marker 'decision' IS the ❓, action_required IS the interrupt, drift_critical
    compounds if ignored.

    Then the one that is not obvious until you test it. A team_loop's title field
    is literally the partner's ASK — that is what the column means — so a team_loop
    aimed at me is a request by construction, not an FYI. Found the hard way: T63,
    the row that caused this script to be written, is a team_loop asking Joe to
    apply a patch, and the structural rules alone rendered it blue. The partners'
    own "FYI:" prefix convention is the honest exception and is respected.
    """
    if row["marker"] in QUESTION_MARKERS:
        return True
    if row["kind"] in QUESTION_KINDS:
        return True
    if row["drift_critical"]:
        return True
    text = ((row["title"] or "") + " " + (row["body"] or "")).strip()
    if row["kind"] == "team_loop":
        return not text.upper().startswith("FYI")
    return "?" in text.split("\n")[0]


def notify(title, subtitle, message):
    """macOS notification. osascript because terminal-notifier is not installed.

    Text is passed through a quoted AppleScript string, so quotes and backslashes
    in a partner's row title are escaped rather than becoming syntax.
    """
    def esc(s):
        return (s or "").replace("\\", "\\\\").replace('"', '\\"')[:220]

    script = (
        f'display notification "{esc(message)}" '
        f'with title "{esc(title)}" subtitle "{esc(subtitle)}"'
    )
    subprocess.run(["osascript", "-e", script], capture_output=True)


def fetch(conn, since, me, partner):
    """Open rows the OTHER partner authored, plus anything aimed at me.

    Deliberately keyed on created_at rather than on a row id high-water mark: an
    id watermark would silently skip a row inserted out of order, and a missed
    interrupt is the exact failure this script exists to prevent.

    Rows I wrote myself are excluded — being notified about your own writes
    trains you to ignore the notification, which costs more than it gives.
    """
    sql = """
        select l.id::text, l.kind, l.number, l.title, l.body, l.owner,
               l.marker, l.drift_critical, l.created_at,
               coalesce(a.slug, '?') as author
        from loop_item l
        left join actor a on a.id = l.created_by
        where l.status = 'open'
          and l.created_at > %s
          and coalesce(a.slug, '') <> %s
          -- CROSS-BRAIN ONLY. team_loop and action_required are the two channels
          -- between the partners; everything else is one partner's own backlog.
          -- Without this, Joe's ordinary open_loops fired at him as if Dell had
          -- sent them — the precise noise that teaches a person to swipe the
          -- notification away, which costs more than the alert gives.
          and (l.kind in ('team_loop', 'action_required') or a.slug = %s)
          -- AIMED AT ME. The owner column uses an arrow convention, X→Y meaning
          -- from X to Y, so a bare ilike '%%Joe%%' wrongly matches 'Joe→Dell' —
          -- a row Joe sent TO Dell. When an arrow is present only the RIGHT side
          -- counts; without one, any mention of me does. Caught in testing when
          -- Dell's own adoption rows and a Joe→Dell handoff both fired at Joe.
          and (
                (l.owner like '%%→%%'     and l.owner ilike %s)
             or (l.owner not like '%%→%%' and l.owner ilike %s)
          )
          and (
                l.personal_to is null
             or l.personal_to = (select id from actor where slug = %s)
          )
        order by l.created_at
    """
    arrow_to_me = f"%→%{DISPLAY[me]}%"
    names_me = f"%{DISPLAY[me]}%"
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute(sql, (since, me, partner, arrow_to_me, names_me, me))
        return cur.fetchall()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--since", help="ISO timestamp overriding the stored watermark")
    args = ap.parse_args()

    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL not set — run this through tools/db-tap.py run")

    me = resolve_self()
    partner = next(p for p in PARTNERS if p != me)

    state = load_state()
    since = args.since or state.get("last_seen")
    if not since:
        # First ever run: start from now so it does not dump the whole backlog
        # into notification centre. The board already holds the history.
        since = datetime.now(timezone.utc).isoformat()
        if not args.dry_run:
            save_state({"last_seen": since})
            print(f"initialised watermark at {since}; nothing fired on first run")
            return

    with psycopg.connect(url) as conn:
        rows = fetch(conn, since, me, partner)

    if not rows:
        print(f"[{me}] nothing new since {since}")
        return

    questions = [r for r in rows if is_question(r)]
    statements = [r for r in rows if not is_question(r)]

    for r in rows:
        glyph = "❓" if is_question(r) else "ℹ️"
        kind = "needs your answer" if is_question(r) else "FYI"
        label = r["title"] or (r["body"] or "").split("\n")[0]
        ref = f"#{r['number']}" if r["number"] else r["kind"]
        line = f"{glyph} {ref} from {r['author']} — {kind}"
        if args.dry_run:
            print(f"WOULD FIRE: {line} :: {label[:90]}")
        else:
            notify(line, r["owner"] or "", label)

    newest = max(r["created_at"] for r in rows).isoformat()
    if not args.dry_run:
        save_state({"last_seen": newest})
    print(f"{len(questions)} question(s), {len(statements)} statement(s); "
          f"watermark {'unchanged (dry run)' if args.dry_run else '-> ' + newest}")


if __name__ == "__main__":
    main()
