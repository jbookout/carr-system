#!/usr/bin/env python3
"""session-brief.py — the ZERO-FILE local bootstrap (Joe, 2026-08-08:
"Couldn't that claude Md file just become the project instructions? And then
we truly don't have any Md files").

SessionStart hook for sessions rooted in the CARR vault: stdout injects the
briefing directive that CLAUDE.md used to carry, built FRESH from the store
each session — so unlike the file, it cannot go stale (the file lagged its
own rule counts by 4 within a day of the last refresh; measured 2026-08-08).

Three layers of the same directive, by surface:
  - MCP server `instructions` field  → every connector-holding session, all
    surfaces, Dell included (mcp.js)
  - Cowork project-instructions FIELD → the rev-7 pointer stub (no file)
  - THIS HOOK                         → local Claude Code, code not markdown

FAIL-SOFT IS A STOP RAIL, NOT SILENCE: if the store is unreachable the hook
still injects the identity line and the stop-and-say-so instruction — a blank
session that improvises files is the failure this whole build exists to end.
"""
import json
import os
import sys

STATIC_RAIL = (
    "CARR AI (Joe Bookout's healthcare-CRE system, partner Dell McCraney; "
    "business only — personal is Life AI). OPENING ACT: call standing-context "
    "FIRST and recite its rule counts in your first response. Doctrine and "
    "records live in the store (doctrine-index / search-doctrine / "
    "read-doctrine / catch-me-up); there are NO doctrine files. WRITE LAW "
    "(rule 14181e60): verbs only, never a .md file — a hard gate enforces it. "
    "Code lives in ~/carr-system, nowhere else."
)


def dynamic_counts():
    env = os.path.expanduser("~/.config/carr/db.env")
    url = None
    with open(env) as fh:
        for line in fh:
            if line.startswith("CARR_DB_EXPORTER_URL="):
                url = line.split("=", 1)[1].strip().strip("\"'")
    if not url:
        raise RuntimeError("no exporter url")
    import psycopg
    with psycopg.connect(url, connect_timeout=4) as conn, conn.cursor() as cur:
        cur.execute(
            "select coalesce(personal_to,'shared'), count(*) from v_compiled_rules "
            "where coalesce(scope->>'kind','') <> 'intro_politics' "
            "group by 1")
        counts = dict(cur.fetchall())
        cur.execute(
            "select count(*) from loop_item where kind='action_required' "
            "and status='open'")
        ar = cur.fetchone()[0]
    shared = counts.pop("shared", 0)
    personal = ", ".join(f"{v} {k}-personal" for k, v in sorted(counts.items()))
    total = shared + sum(counts.values())

    # ── THE ENFORCEMENT RATIO (Joe, 2026-08-10, decision c2c02983) ──────────
    # Joe: "what mechanism is ensuring that you actually obey the rules of the
    # system? If it's just expected that you will remember to read the rules
    # then it's not adequate."
    #
    # He is right, and the session that heard it was the proof: it opened by
    # reciting 164 rule counts and then violated at least three of them —
    # handing him a shell command to paste, and putting bare identifiers in
    # front of him twice — each caught AFTER the fact by a Stop hook rather
    # than by having read the rule. The recitation was a count, not
    # comprehension.
    #
    # A bare count of 164 is therefore FALSE ASSURANCE: it reads as "164 rules
    # are in force" when the honest statement is "10 gates enforce a subset and
    # the rest are advisory". This line exists to stop the brief making a claim
    # the system cannot back. It is the smallest durable piece of the rule
    # monitor — the rest (a binding moment and a checkable signal per rule)
    # is the build that follows.
    #
    # ENFORCED means a gate that can DENY or BLOCK, not a rule that is merely
    # written down. Counted from the gate scripts actually wired into
    # settings.json, so this number falls on its own if a gate is removed.
    gates = 0
    try:
        settings = json.load(open(os.path.expanduser("~/.claude/settings.json")))
        gates = len({
            h.get("command", "")
            for group in settings.get("hooks", {}).values()
            for entry in group
            for h in entry.get("hooks", [])
            if "carr-system/hooks/" in h.get("command", "")
        })
    except Exception:
        pass

    ratio = ""
    if gates and total:
        ratio = (f" · ENFORCEMENT: {gates} gates can actually deny or block; the"
                 f" other ~{max(total - gates, 0)} rules are ADVISORY — reciting the"
                 f" count is not the same as obeying them, and this session will"
                 f" violate one it recited unless it checks before acting.")

    return (f" LIVE COUNTS to verify against standing-context: {shared} shared"
            + (f", {personal}" if personal else "")
            + (f" · {ar} action-required item(s) open" if ar else "")
            + ratio)


def nightly_verdict():
    """One line when the last nightly chain ended red, nothing when it was clean.

    Added 2026-08-10. The chain had been exiting non-zero for three nights and
    the only place that fact existed was line 1948 of out/nightly.log. It was
    found by a human reading an exit code by hand, not by any surface reporting
    it. A failure nobody is told about is indistinguishable from no failure, so
    the news now arrives at session start, where Joe cannot miss it.

    Deliberately SILENT on a good night: a line that prints every session is a
    line nobody reads by the end of the week."""
    log = os.path.expanduser("~/carr-system/out/nightly.log")
    fails: list[str] = []
    last_ok = None
    for line in open(log, errors="replace"):
        if "  FAIL  " in line:
            fails.append(line.split("  FAIL  ", 1)[1].split(" (exit")[0].strip())
        elif "chain OK" in line or "FINISHED WITH FAILURES" in line:
            last_ok = "chain OK" in line
            if last_ok:
                fails = []
            else:
                seen, uniq = set(), []
                for f in fails:
                    if f not in seen:
                        seen.add(f)
                        uniq.append(f)
                fails = uniq
    if last_ok is False and fails:
        return (f" ⚠ THE LAST NIGHTLY CHAIN FAILED: {', '.join(fails)}. "
                f"Say so in your first response; details in out/nightly.log and "
                f"the 'nightly chain result' row of `./run.sh health`.")
    return ""


def main():
    try:
        json.load(sys.stdin)          # hook payload; nothing needed from it
    except Exception:
        pass
    extra = ""
    try:
        sys.path.insert(0, os.path.expanduser("~/carr-system/.venv/lib"))
        extra = dynamic_counts()
    except Exception:
        extra = (" (store unreachable at session start — STOP AND SAY SO "
                 "before doing anything that would normally need it)")
    try:
        extra += nightly_verdict()
    except Exception:
        pass                          # the brief must never fail on this
    print(STATIC_RAIL + extra)
    sys.exit(0)


if __name__ == "__main__":
    main()
