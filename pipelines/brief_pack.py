#!/usr/bin/env python3
# mypy: ignore-errors
# GRANDFATHERED 2026-08-06: predates the nightly type-check tripwire and fails it.
# Fix this file's mypy errors and delete these three lines when you next touch it.
"""
brief_pack.py — ORDER 16(b), wave2-design §2g. The brief generator's four
sections, as CALLABLE UNITS.

WHY UNITS AND NOT A BRIEF
  The daily heartbeat and the Monday brief already exist, already have their own
  voice, their own output format, and their own change logs full of corrections
  Joe made by hand. Rewriting either of them to compute these four things would
  throw all of that away. So this computes the four sections, writes them where
  those tasks can read them, and the tasks each gain ONE call line. Each section
  runs on its own (--section one-thing) so a caller takes only what it needs.

THE FOUR
  one-thing      The single highest-leverage move per partner, WITH its reasoning.
                 Reasoning is not decoration here: a ranked list a person does not
                 believe is a list they ignore.
  prebriefs      Every event on today's calendars, both partners: who, where the
                 deal stands, when they were last touched, and the one thing to
                 come away with. Generated for the whole day each morning, NOT
                 pushed 30 minutes ahead of each meeting. A page you choose to
                 read is calm; a tap on the shoulder before every meeting is not.
  capacity       Open balls per partner, and rebalance PROPOSALS. Proposals only.
                 Nothing in this file moves work from one person to the other.
  monday-agenda  Decisions only. No status recitation: status is what the boards
                 are for, and a partner meeting that recites it spends the hour
                 without deciding anything.

WHAT THIS CANNOT SEE, SAID OUT LOUD RATHER THAN PAPERED OVER
  The only credential on this Mac is the views-only exporter role. Two
  consequences, both stated on the surfaces themselves rather than hidden:
   1. NO DEAL CARRIES A MONETARY VALUE in any view this can read, so "deal value"
      is ranked by PHASE (how close the deal is to a fee), and every line says so.
   2. `v_deal_board.last_touch` reads 2026-07-31 for all 40 deals, which is the
      import stamp and not a touch. Decay is therefore measured on the client and
      lead touches in v_last_touch, which are real dates spanning January to now.
  A number that looks precise and is not is worse than an honest proxy.

  Nothing here writes to the database, sends anything, or pushes anything.

Usage:
  ./run.sh brief-pack [--section all|one-thing|prebriefs|capacity|monday-agenda]
                      [--date YYYY-MM-DD] [--quiet]

EXIT CODES
  0  ran, including when a section is honestly empty.
  78 EX_CONFIG — no database credential at all. The chain reads that as SKIP.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "out" / "brief-pack"
VAULT = Path(os.environ.get(
    "CARR_VAULT",
    "/Users/booko/Library/CloudStorage/GoogleDrive-joe.bookout.carr.us@gmail.com/My Drive/CARR AI"))
CAL = {"joe": VAULT / "DNA/Team/calendar-latest.ics",
       "dell": VAULT / "DNA/Team/calendar-latest-dell.ics"}
PARTNERS = ("joe", "dell")

# Phase is the value proxy. Later phase = nearer a fee, which is the only
# ordering the record can actually support today (see the header).
PHASE_WEIGHT = {"legal": 95, "closing": 90, "due_diligence": 80, "negotiation": 60,
                "research": 35, "pending": 20, "closed": 0}
PHASE_WORDS = {"legal": "in legal review", "closing": "closing", "due_diligence": "in diligence",
               "negotiation": "in negotiation", "research": "in research",
               "pending": "not started", "closed": "closed"}


def db_url():
    # [ORDER 19a, and the ONE script that deliberately does NOT prefer
    # CARR_DB_JOBS_URL — flagged in the execution log rather than done quietly.]
    # This file reads v_deal_board, v_today_triage, v_last_touch and v_ref_index.
    # The nightly-jobs role holds exactly one of those four (v_ref_index) and is
    # not granted the others on purpose: reaching for it here would trade a
    # working brief pack for a literal reading of the word "prefer". The reader
    # tier is the right tier for a read-only brief; the jobs role is for the
    # lanes that WRITE or that need ingest_inbox.
    url = os.environ.get("CARR_DB_EXPORTER_URL") or os.environ.get("DATABASE_URL")
    if url:
        return url
    env = Path.home() / ".config/carr/db.env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("CARR_DB_EXPORTER_URL="):
                # .strip("\"'") — db.env values are shell-quoted so `set -a; . db.env`
                # survives an `&` in the DSN; psycopg needs them unquoted. Full reasoning
                # in exporters/common.py. Added 2026-08-02.
                return line.split("=", 1)[1].strip().strip("\"'")
    return None


def q(cur, sql, args=()):
    cur.execute(sql, args)
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def plain(s) -> str:
    """No em-dash reaches a surface, including text quoted out of Joe's own
    notes (writing-rules, and it applies to what the system renders). Also
    undoes RFC 5545 text escaping, so a calendar address reads like an address
    instead of like a file format."""
    s = str(s if s is not None else "")
    s = s.replace("\\,", ",").replace("\\;", ";").replace("\\n", " ").replace("\\\\", "\\")
    s = re.sub(r"\s*—\s*", ", ", s)
    return " ".join(s.split()).strip()


ID_WORDS = {"C": "a client record", "L": "a lead record", "V": "a vendor record"}


def no_ids(s: str) -> str:
    """Doctrine law 5: internal ids are join keys and never reach the eye. This
    text is QUOTED out of open-loops rows Joe writes for himself, where an id is
    perfectly normal, so the swap happens on the way to the surface."""
    return re.sub(r"\b([LCV])-(?:[A-Z]{3}-)?\d{3}\b",
                  lambda m: ID_WORDS[m.group(1)], str(s or ""))


def days_since(d, today) -> int | None:
    if not d:
        return None
    if isinstance(d, datetime):
        d = d.date()
    return (today - d).days


# ─────────────────────────────────────────────────────────────────────────────
# 1. The One Thing, per partner
# ─────────────────────────────────────────────────────────────────────────────

def section_one_thing(cur, today) -> str:
    deals = q(cur, """select name, client_name, phase, lead_owner, deal_type, outcome
                        from v_deal_board where outcome is null or outcome = ''""")
    balls = q(cur, """select t.owner, t.due_on, t.subject_type, t.subject_id, t.what,
                             r.display_name, r.org_name, r.status
                        from v_today_triage t
                        left join v_ref_index r
                          on r.subject_id = t.subject_id and r.subject_type = t.subject_type""")
    touch = {(r["subject_type"], str(r["subject_id"])): r["last_touch"]
             for r in q(cur, "select subject_type, subject_id, last_touch from v_last_touch")}

    lines = ["# The one thing", "",
             f"_Built {today.isoformat()}. Ranked on how close a deal sits to a fee, because no "
             "deal in the record carries a dollar value yet, and on how long a real touch has "
             "been owed. Both inputs are named on every line so the ranking can be argued with._",
             ""]

    for partner in PARTNERS:
        cands = []

        for b in balls:
            if (b["owner"] or "").lower() != partner:
                continue
            who = plain(b["display_name"] or b["org_name"] or "an unnamed record")
            quiet = days_since(touch.get((b["subject_type"], str(b["subject_id"]))), today)
            overdue = days_since(b["due_on"], today) if b["due_on"] else None
            score = 0
            reasons = []
            if overdue is not None and overdue > 0:
                score += 40 + min(overdue, 60)
                reasons.append(f"you set this one for {b['due_on'].strftime('%b %-d')} "
                               f"and it is {overdue} days past")
            if (b["status"] or "") in ("active_deal", "engaged", "opportunity"):
                score += 30
                reasons.append(f"they are {plain(b['status']).replace('_', ' ')}")
            if quiet is not None and quiet > 21:
                score += min(quiet // 7, 8) * 4
                reasons.append(f"nobody has touched them in {quiet} days")
            if not reasons:
                continue
            cands.append({"who": who, "score": score, "reasons": reasons,
                          "what": plain(b["what"]), "kind": "ball"})

        for d in deals:
            if (d["lead_owner"] or "").lower() != partner:
                continue
            w = PHASE_WEIGHT.get(d["phase"], 10)
            if w < 60:
                continue
            # The DEAL name, not the client name. Several of Dell's startup deals
            # share one client record, so ranking by client printed the same
            # person three times and told him nothing about which deal to move.
            cands.append({"who": plain(d["name"]),
                          "score": w,
                          "reasons": [f"the deal is {PHASE_WORDS.get(d['phase'], d['phase'])}, "
                                      "which is the nearest thing to a fee on your board"],
                          "what": "move it to the next step", "kind": "deal"})

        title = partner.capitalize()
        if not cands:
            lines += [f"## {title}", "",
                      "Nothing is ranking today. No ball of yours is past its date, no deal of "
                      "yours sits in negotiation or later, and nothing you own has gone quiet "
                      "past three weeks. That is a real answer, not an empty section.", ""]
            continue

        cands.sort(key=lambda c: -c["score"])
        seen, unique = set(), []
        for c in cands:
            if c["who"] in seen:
                continue
            seen.add(c["who"])
            unique.append(c)
        cands = unique
        top = cands[0]
        # Runners get a DIFFERENT reason from the winner where one exists. Three
        # lines reading "the deal is in legal review" is a list nobody can rank.
        runners = [c for c in cands[1:] if c["reasons"][0] != top["reasons"][0]][:2] \
            or cands[1:3]
        reason = top["reasons"][0]
        if len(top["reasons"]) > 1:
            reason += ", and " + top["reasons"][1]
        lines += [f"## {title}", "",
                  f"**{top['who']}.** {top['what']}", "",
                  f"Why this one first: {reason}.", ""]
        if runners:
            lines.append("Next after it: " + " · ".join(
                f"{r['who']} ({r['reasons'][0]})" for r in runners) + ".")
            lines.append("")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Calendar pre-briefs
# ─────────────────────────────────────────────────────────────────────────────

def read_events(path: Path, day: date):
    if not path.exists():
        return None
    raw = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
    raw = re.sub(r"\n[ \t]", "", raw)          # unfold, per RFC 5545
    stamp = day.strftime("%Y%m%d")
    events = []
    for blk in re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", raw, re.S):
        f = {}
        for line in blk.strip().split("\n"):
            if ":" in line:
                k, v = line.split(":", 1)
                f[k.split(";")[0]] = v
        start = f.get("DTSTART", "")
        if not start.startswith(stamp):
            continue
        t = ""
        m = re.match(r"\d{8}T(\d{2})(\d{2})", start)
        if m:
            h, mi = int(m.group(1)), m.group(2)
            ampm = "am" if h < 12 else "pm"
            h12 = h % 12 or 12
            t = f"{h12}:{mi}{ampm}"
        events.append({"time": t, "sort": start,
                       "title": plain(f.get("SUMMARY", "(untitled)")),
                       "where": plain(f.get("LOCATION", ""))})
    return sorted(events, key=lambda e: e["sort"])


def match_event(title: str, index, touch, today):
    low = title.lower()
    for field in ("display_name", "org_name"):
        hits = {}
        for r in index:
            name = (r.get(field) or "").strip()
            if len(name) >= 5 and name.lower() in low:
                hits[r["subject_id"]] = r
        if len(hits) == 1:
            r = list(hits.values())[0]
            quiet = days_since(touch.get((r["subject_type"], str(r["subject_id"]))), today)
            return r, quiet
    return None, None


def section_prebriefs(cur, today) -> str:
    index = q(cur, """select subject_type, subject_id, ref, display_name, org_name, status
                        from v_ref_index where merged is not true""")
    touch = {(r["subject_type"], str(r["subject_id"])): r["last_touch"]
             for r in q(cur, "select subject_type, subject_id, last_touch from v_last_touch")}
    balls = {}
    for b in q(cur, "select subject_type, subject_id, owner, what from v_today_triage"):
        balls.setdefault((b["subject_type"], str(b["subject_id"])), []).append(b)

    out = ["# Today's meetings", "",
           f"_{today.strftime('%A, %B %-d')}. Everyone on today's calendars, with where they "
           "stand and the one thing to come away with. Written once this morning; nothing here "
           "interrupts you before a meeting._", ""]

    total = 0
    for partner in PARTNERS:
        events = read_events(CAL[partner], today)
        name = partner.capitalize()
        if events is None:
            out += [f"## {name}", "",
                    "The calendar file is not on disk, so this section cannot be built for "
                    f"{name} today. It normally lands at 7:55am.", ""]
            continue
        if not events:
            out += [f"## {name}", "", "Nothing on the calendar today.", ""]
            continue
        total += len(events)
        out += [f"## {name}", ""]
        for e in events:
            rec, quiet = match_event(e["title"], index, touch, today)
            head = f"**{e['time']} · {e['title']}**"
            if e["where"]:
                head += f" · {e['where'][:60]}"
            out.append(head)
            if rec:
                who = plain(rec["display_name"] or rec["org_name"])
                state = plain(rec.get("status") or "").replace("_", " ") or "no stage on file"
                bits = [f"{who} is {state}"]
                bits.append(f"last touched {quiet} days ago" if quiet is not None
                            else "no touch has ever been logged")
                open_balls = balls.get((rec["subject_type"], str(rec["subject_id"])), [])
                mine = [b for b in open_balls if (b["owner"] or "").lower() == partner]
                out.append("  " + ". ".join(bits) + ".")
                if mine:
                    out.append(f"  The one thing to get: {plain(mine[0]['what'])[:150]}")
                else:
                    out.append("  The one thing to get: what has to happen next, and by when. "
                               "Nobody holds an open item on them right now.")
            else:
                out.append("  Not matched to anyone on file, so this is title and time only.")
            out.append("")

    if total == 0:
        out.append("_No meetings on either calendar today._")
    return "\n".join(out)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Capacity snapshot
# ─────────────────────────────────────────────────────────────────────────────

def section_capacity(cur, today) -> str:
    rows = q(cur, """select owner, subject_type, due_on from v_today_triage""")
    per = {}
    for r in rows:
        o = (r["owner"] or "unassigned").lower()
        p = per.setdefault(o, {"total": 0, "dated": 0, "overdue": 0, "kinds": {}})
        p["total"] += 1
        p["kinds"][r["subject_type"]] = p["kinds"].get(r["subject_type"], 0) + 1
        if r["due_on"]:
            p["dated"] += 1
            if r["due_on"] < today:
                p["overdue"] += 1

    out = ["# Who is carrying what", "",
           f"_Open items per person, {today.isoformat()}. Proposals only. Nothing here moves "
           "work from one person to the other; that is a conversation, not a job._", ""]

    for o in sorted(per, key=lambda k: -per[k]["total"]):
        p = per[o]
        kinds = ", ".join(f"{v} {k}{'s' if v != 1 else ''}"
                          for k, v in sorted(p["kinds"].items(), key=lambda x: -x[1]))
        label = "Unassigned" if o in ("unassigned", "system") else o.capitalize()
        line = f"**{label}: {p['total']} open.** {kinds}."
        if p["dated"]:
            verb = "carries" if p["dated"] == 1 else "carry"
            is_are = "is" if p["overdue"] == 1 else "are"
            line += (f" {p['dated']} {verb} a date, and {p['overdue']} of those "
                     f"{is_are} past it.")
        else:
            line += " None of them carry a date."
        out += [line, ""]

    human = {k: v for k, v in per.items() if k in PARTNERS}
    if len(human) == 2:
        hi = max(human, key=lambda k: human[k]["total"])
        lo = min(human, key=lambda k: human[k]["total"])
        gap = human[hi]["total"] - human[lo]["total"]
        out += ["## Worth talking about", ""]
        if gap >= 20:
            out.append(f"{hi.capitalize()} is carrying {gap} more open items than "
                       f"{lo.capitalize()}. Before treating that as an imbalance, check whether "
                       "it is one: a stack of untouched prospecting items and a stack of live "
                       "deal items are not the same weight. If some of them really are just "
                       "sitting, the proposal is to pick a handful and either hand them over or "
                       "close them out.")
        else:
            out.append("The two of you are carrying comparable loads. No rebalance to propose.")
        out.append("")

    stale = [k for k, v in per.items() if v["total"] and v["dated"] == 0]
    if stale:
        out += ["Worth noting: "
                + " and ".join(k.capitalize() for k in sorted(stale))
                + " holds open items with no date on any of them, so nothing will ever surface "
                  "them as due. A date is what makes an item come back.", ""]
    if not per:
        out += ["Nobody is carrying an open item. That is either a very good week or a sign "
                "nothing is being logged.", ""]
    return "\n".join(out)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Monday partner agenda — decisions only
# ─────────────────────────────────────────────────────────────────────────────

def read_decisions():
    """❓ rows are the house marker for a pending decision, and the open-loops
    header is the authority on that. This reads the marker, so a row becomes an
    agenda item the moment it is marked, with nobody remembering to copy it."""
    items = []
    for name in ("00_Context/open-loops-backlog.md", "00_Context/open-loops.md"):
        f = VAULT / name
        if not f.exists():
            continue
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.startswith("|") or "❓" not in line:
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 5 or not cells[0].isdigit():
                continue
            body = cells[2]
            head = re.sub(r"\*\*(.+?)\*\*", r"\1", body).replace("❓", "").strip()
            head = plain(" ".join(head.split()))
            question = head.split(". ")[0].rstrip(" .")
            if len(question) > 210:
                question = question[:210].rsplit(" ", 1)[0].rstrip(",;") + "..."
            items.append({"owner": plain(cells[1]), "question": no_ids(question) + ".",
                          "unblocks": no_ids(plain(cells[4])) if len(cells) > 4 else ""})
    return items


def section_monday(cur, today) -> str:
    days = (7 - today.weekday()) % 7 or 7
    nxt = today + timedelta(days=days)
    items = read_decisions()
    both = [i for i in items if "+" in i["owner"] or "/" in i["owner"] or "Dell" in i["owner"]]
    solo = [i for i in items if i not in both]

    out = ["# Monday agenda", "",
           f"_For {nxt.strftime('%A, %B %-d')}. Decisions only. Where each thing stands is on "
           "the boards; an hour spent reciting it is an hour spent deciding nothing._", ""]

    if not items:
        out += ["No decision is waiting on the two of you. Nothing to meet about, which is the "
                "outcome worth having.", ""]
        return "\n".join(out)

    if both:
        out += [f"## Needs both of you ({len(both)})", ""]
        for i in both:
            out.append(f"- {i['question']}")
            if i["unblocks"]:
                out.append(f"  Deciding it unblocks: {i['unblocks'][:140]}")
        out.append("")
    if solo:
        out += [f"## One of you decides, the other should know ({len(solo)})", ""]
        for i in solo:
            out.append(f"- ({i['owner']}) {i['question']}")
        out.append("")
    out += [f"That is {len(items)} decision{'' if len(items) == 1 else 's'} on the table. "
            "Anything not decided Monday keeps its marker and comes back next week.", ""]
    return "\n".join(out)


# ─────────────────────────────────────────────────────────────────────────────

SECTIONS = {
    "one-thing": ("one-thing.md", section_one_thing),
    "prebriefs": ("prebriefs.md", section_prebriefs),
    "capacity": ("capacity.md", section_capacity),
    "monday-agenda": ("monday-agenda.md", section_monday),
}

BANNED = ["delve", "unlock", "unleash", "harness", "elevate", "unveil", "seamless",
          "cutting-edge", "game-changing", "transformative", "holistic", "tapestry",
          "realm", "testament", "myriad", "plethora", "leverage", "utilize", "synergy",
          "robust", "streamline", "empower", "foster", "facilitate", "paramount",
          "meticulous", "intricate", "multifaceted", "beacon", "embark", "quietly",
          "it's worth noting", "at the end of the day", "the reality is", "at its core"]


def lint(text: str):
    findings = []
    if "—" in text:
        findings.append("em-dash")
    low = text.lower()
    findings += [f"banned term: {b}" for b in BANNED if b in low]
    if re.search(r"\b[LCV]-[A-Z]{0,4}-?\d{3}\b", text):
        findings.append("an internal record id reached the copy (doctrine law 5)")
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the brief pack sections.")
    ap.add_argument("--section", default="all",
                    choices=["all"] + list(SECTIONS), help="which unit to run")
    ap.add_argument("--date", help="YYYY-MM-DD; defaults to today")
    ap.add_argument("--quiet", action="store_true", help="write the files, print one line")
    a = ap.parse_args()

    today = date.fromisoformat(a.date) if a.date else date.today()
    url = db_url()
    if not url:
        print("brief_pack: NOT CONFIGURED, no CARR_DB_EXPORTER_URL or DATABASE_URL. "
              "Nothing was written.", file=sys.stderr)
        return 78

    import psycopg
    OUT.mkdir(parents=True, exist_ok=True)
    wanted = list(SECTIONS) if a.section == "all" else [a.section]
    written, findings = [], []

    with psycopg.connect(url) as conn, conn.cursor() as cur:
        for key in wanted:
            fname, fn = SECTIONS[key]
            body = fn(cur, today)
            findings += [f"{key}: {f}" for f in lint(body)]
            (OUT / fname).write_text(body.rstrip() + "\n", encoding="utf-8")
            written.append(fname)
            if not a.quiet:
                print(body)
                print("\n" + "-" * 72 + "\n")

    if a.section == "all":
        combined = "\n\n".join((OUT / SECTIONS[k][0]).read_text(encoding="utf-8")
                               for k in SECTIONS)
        (OUT / "brief-pack-latest.md").write_text(combined, encoding="utf-8")
        written.append("brief-pack-latest.md")

    print(f'brief pack: {len(wanted)} section(s) -> {OUT} ({", ".join(written)})')
    print(f'  writing check: {"clean" if not findings else "; ".join(findings)}')
    return 0


if __name__ == "__main__":
    sys.exit(main())
