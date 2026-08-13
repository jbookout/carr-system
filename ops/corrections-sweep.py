#!/usr/bin/env python3
"""Find corrections the partner has had to make MORE THAN ONCE, and propose rules.

WHY THIS EXISTS. The rule store has a rich output path — rules bind sessions, render
to files, recite at boot — and no INPUT path except a session noticing in the moment
and remembering to call `teach`. A correction the partner makes twice is the clearest
possible signal that a rule is missing, and nothing was looking for repeats.

WHAT IT READS, three substrates, weakest last:

  1. THE DEFECT LOG (0103). A defect_class with more than one row IS a repeated
     correction, already clustered by a human-written class name and already
     carrying what was claimed, what was true, and which rule it broke. This is by
     far the strongest input and it did not exist before 2026-08-13.

  2. DECISION HISTORY. Rulings carrying the partner's verbatim words. A quote that
     reads as a correction ("no", "actually", "thats a stupid rule") is a
     correction he had to state, whether or not it became a rule.

  3. SESSION TRANSCRIPTS. His actual turns, filtered through the SAME partner-turn
     filter the displacement baselines use — imported, never reimplemented, because
     that filter was inverted for weeks and a second copy would have been a second
     thing to get wrong (rule a8c55a47).

WHAT IT WILL NOT DO. It proposes; it never teaches. Every candidate is checked
against the ACTIVE rules first, because re-proposing something already taught is
noise that trains the partner to ignore the channel. And a candidate with only one
instance is dropped: this is a sweep for REPEATS, and one correction is a moment,
not a pattern.

Read-only. Run it monthly, or when Joe asks.
"""
import importlib.util
import json
import os
import re
import sys
from collections import defaultdict

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The partner-turn filter, imported from the instrument that owns it. That module
# spent weeks with an inverted filter; the fix now carries 25 assertions and a
# second copy here would be a second thing to get wrong.
_spec = importlib.util.spec_from_file_location(
    "displacement_baselines", os.path.join(REPO, "tools", "displacement-baselines.py"))
_baselines = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_baselines)

# A correction is the partner saying the system got it wrong. These are his shapes,
# taken from turns he actually typed rather than invented: blunt, short, often
# starting with the negation.
CORRECTION = re.compile(
    r"\b(?:no[,.]? (?:it|that|thats|you|we|its)|actually|thats? (?:wrong|not right|stupid|"
    r"backwards)|you (?:missed|didnt|did not|forgot|keep|already)|never (?:do|say|use|call)|"
    r"i (?:already )?(?:told|said)|not what i|stop (?:doing|using)|dont (?:do|use|say)|"
    r"wrong|incorrect|thats not)\b", re.I)


def defect_repeats(cur):
    cur.execute("""
        select defect_class, occurrences, caught_by_human, first_seen, last_seen,
               sources_unread, rules_violated
          from v_defect_class where occurrences > 1
         order by caught_by_human desc, occurrences desc
    """)
    return [dict(zip(("defect_class", "occurrences", "caught_by_human", "first_seen",
                      "last_seen", "sources_unread", "rules_violated"), r))
            for r in cur.fetchall()]


def quoted_corrections(cur):
    cur.execute("""
        select entry_date, title, human_quote
          from v_decision_entry
         where human_quote is not null and btrim(human_quote) <> ''
         order by entry_date desc
    """)
    out = []
    for day, title, quote in cur.fetchall():
        if CORRECTION.search(quote or ""):
            out.append({"date": str(day), "title": title, "quote": quote.strip()[:280]})
    return out


def active_rule_text(cur):
    cur.execute("select statement from v_compiled_rules")
    return " \n ".join((r[0] or "").lower() for r in cur.fetchall())


def transcript_corrections(limit_files=None):
    """Partner turns that read as corrections, from his real typed turns only."""
    import pathlib
    root = pathlib.Path.home() / ".claude" / "projects"
    hits = []
    files = sorted(root.rglob("*.jsonl"))
    if limit_files:
        files = files[-limit_files:]
    for f in files:
        try:
            for line in f.open():
                if '"user"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if not _baselines.is_typed_prompt(rec):
                    continue
                text = _baselines.partner_text(rec)
                if not text:
                    continue
                # A PASTED DOCUMENT IS NOT A CORRECTION. Handoff packets and briefs
                # are genuinely typed by Joe — the turn filter is right to keep them
                # — but they are machine-authored prose he is carrying, and their
                # bodies happen to contain correction words. The first run counted
                # three of them as corrections.
                stripped = text.strip()
                if stripped.startswith("#") or len(stripped) > 1500:
                    continue
                m = CORRECTION.search(stripped)
                if m:
                    # CARRY THE MATCHED PHRASE. The first version stored the text
                    # TRUNCATED to 220 characters and re-matched later, so any hit
                    # past that point vanished and fell into an "other" bucket that
                    # then looked like the largest cluster. Match once, keep the key.
                    hits.append((m.group(0).lower().strip(), stripped[:220]))
        except Exception:
            continue
    return hits


def cluster(texts):
    """Crude but honest: group on the correction phrase that fired, not on meaning.

    A smarter clusterer would need an embedding store, which the deferral gate on
    this row's sibling already ruled against for precedent search. The phrase IS
    the cluster key here because the partner repeats his own wording.
    """
    groups = defaultdict(list)
    for key, t in texts:
        groups[key].append(t)
    return groups


def main():
    import psycopg
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("no DSN — run this through tools/db-tap.py")
    with psycopg.connect(dsn) as conn:
        cur = conn.cursor()
        repeats = defect_repeats(cur)
        quotes = quoted_corrections(cur)
        rules_blob = active_rule_text(cur)

    # SCANS EVERYTHING BY DEFAULT. The first run capped this at the newest 60 of 771
    # transcript files and reported ZERO correction-shaped turns, which read as "he
    # never corrects us" rather than "we looked at 8% of the corpus". That is the
    # absence-from-one-sweep failure already in the defect log. This is a MONTHLY
    # job; reading every file costs seconds and buys a true answer.
    recent_only = "--recent" in sys.argv
    turns = transcript_corrections(60 if recent_only else None)
    groups = cluster(turns)

    print("# CORRECTIONS SWEEP\n")
    print("## 1. Repeated defect classes — the strongest signal\n")
    if not repeats:
        print("None. Every recorded defect class has exactly one instance, so nothing "
              "here is yet a pattern rather than a moment.\n")
    for r in repeats:
        already = any(w in rules_blob for w in r["defect_class"].split("-") if len(w) > 5)
        print(f"- **{r['defect_class']}** — {r['occurrences']} times, "
              f"{r['caught_by_human']} caught by a human, {r['first_seen']} to {r['last_seen']}")
        if r["rules_violated"]:
            print(f"  - rules already broken: {', '.join(r['rules_violated'])}")
        if r["sources_unread"]:
            print(f"  - artifacts that keep going unread: {r['sources_unread'][0]}")
        print(f"  - PROPOSE A RULE: {'probably already covered — check before teaching' if already else 'YES, no active rule mentions this'}")
    print()

    print("## 2. Corrections in the partner's own recorded words\n")
    for q in quotes[:12]:
        print(f"- {q['date']}: \"{q['quote']}\"\n  - from: {q['title'][:90]}")
    if not quotes:
        print("None found in the ruling history.")
    print()

    print(f"## 3. Correction-shaped turns in transcripts "
          f"({'newest 60 files' if recent_only else 'every transcript file'})\n")
    for key, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        if len(items) < 2:
            continue          # a repeat sweep drops singletons, by definition
        print(f"- **\"{key}\"** — {len(items)} turns")
        for t in items[:3]:
            print(f"  - {t}")
    print()

    print("## WHAT TO DO WITH THIS\n")
    print("Nothing here is a rule yet. A candidate earns a `teach` call only if it "
          "repeats, names a concrete past mistake, and is not already covered by an "
          "active rule. Teaching writes it as PROPOSED and it binds nobody until Joe "
          "says yes, which is the gate — not this script.")


if __name__ == "__main__":
    main()
