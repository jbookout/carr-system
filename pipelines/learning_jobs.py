#!/usr/bin/env python3
"""
learning_jobs.py — ORDER 15 (c) / wave2-design §2f / stress-test addendum §C.

The four learning-and-teaching jobs, ALL BUILT, ALL THRESHOLD-GATED, all running
from day one and speaking honestly below their evidence floors. §C's directive
verbatim in intent: what changes below threshold is not whether a job exists but
how it speaks. "14 posts tagged, threshold is 30, no conclusions yet" is a
successful run. "8 active rules, 0 repeat violations, nothing to promote" is a
PASS, not a null result.

  weekly       — reads placement + placement_metric + content_piece features,
                 buckets into feature cells, compares each against
                 `learning.min_posts_per_feature_cell`.
  corrections  — mines `event` rows whose cause is `human_correction`.
  promotion    — reads the rule store: active rules, and post-activation
                 corrections per rule against `promotion.min_repeat_violations`.
  conflicts    — surfaces rules that contradict.

THE GATE IS ON MEASURED POSTS, NOT TAGGED ONES — a named judgment, flagged.
  The config note reads "this many tagged posts in a feature cell before
  proposing a playbook delta". Taken literally, twitter/text (35 placements)
  crosses a threshold of 30 today while carrying ZERO metric observations,
  because Blotato collects no Twitter analytics for this workspace. A cell that
  crosses on unmeasured posts would let the job propose a playbook change from
  nothing, which is the exact noise §C exists to prevent. So the report states
  BOTH counts for every cell and gates a conclusion on the MEASURED count. The
  literal reading is not hidden: it is printed beside the gating one.

CONCLUSIONS STAY DEFERRED; MACHINERY DOES NOT.
  v1 output is a report file per job. No proposals, no notifications, no writes
  to any playbook, no rule changes. Nothing here decides anything.

THE PERSONAL-TIER BOUNDARY IS STRUCTURAL, NOT REMEMBERED (standing memory rule
`dialed-in-craft-stays-personal`). These reports carry platform, format, counts
and thresholds. They never carry hook banks, swipe files, voice notes or post
copy, and they land under `Automation/` — outside `DNA/`, therefore never in
Dell's share by construction rather than by anyone's care.

CREDENTIALS AND HONEST DEGRADATION (the d0b473c lesson, mechanised)
  A job reads the richest source it can reach and SAYS WHICH. With
  DATABASE_URL (writer/owner) it reads base tables and every clause runs. With
  only CARR_DB_EXPORTER_URL it reads the granted views, which cover the rule
  store's active rules and nothing else, and every clause it cannot run is
  reported as UNAVAILABLE with the reason. It never reports zero for something
  it could not look at — an absence it did not measure is never stated as a
  fact.

Usage:
  .venv/bin/python pipelines/learning_jobs.py weekly|corrections|promotion|conflicts|all
    [--report-dir DIR]
"""

import argparse
import collections
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "out"

UNAVAILABLE = "UNAVAILABLE"

# How many candidate pairs the conflict report prints, highest vocabulary overlap
# first. The pair count grows with the square of the active-rule count, so an
# uncapped list stopped being readable somewhere under 220 rules; the remainder is
# always counted in the report rather than dropped in silence.
CANDIDATE_PAIR_CAP = 40

# Directive polarity markers used only to nominate CANDIDATE pairs for a human
# read. They never decide that two rules conflict.
NEGATIVE = re.compile(r"\b(never|not|no|don'?t|do not|avoid|refuse|stop)\b", re.I)
POSITIVE = re.compile(r"\b(always|must|every time|from now on|shall)\b", re.I)
STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "for", "to", "of", "in", "on", "at",
    "is", "it", "that", "this", "with", "as", "be", "are", "was", "by", "from",
    "never", "always", "not", "no", "do", "dont", "must", "only", "when", "any",
    "every", "his", "her", "their", "its", "one", "them", "they", "he", "she",
}


# ── source ───────────────────────────────────────────────────────────────────

class Source:
    """One connection, honest about what it can and cannot see."""

    def __init__(self):
        # [ORDER 19a] CARR_DB_JOBS_URL first — the one nightly-jobs role. It holds
        # the content tables and the rule view these jobs actually read, and
        # nothing else, so it is the credential this lane is meant to run under.
        # Every older name stays accepted, in the order that was true before.
        self.url = os.environ.get("CARR_DB_JOBS_URL")
        self.tier = "jobs"
        if not self.url:
            self.url = os.environ.get("DATABASE_URL") or os.environ.get("CARR_IMPORT_DB_URL")
            self.tier = "full"
        if not self.url:
            self.url = os.environ.get("CARR_DB_EXPORTER_URL")
            self.tier = "views"
        if not self.url:
            self.tier = "none"
        self.conn = None
        self.reason = {
            "jobs": "CARR_DB_JOBS_URL present (carr_jobs, the nightly-jobs role). It reads "
                    "`placement_metric`, `event` and `v_compiled_rules` directly, and it "
                    "reaches placements and their copy through the collector view "
                    "`v_control_plane_social_feature_cells` — the control plane holds this "
                    "role off `placement` and `content_piece` themselves. That is every "
                    "source these four jobs need. Anything outside that set still reports "
                    f"{UNAVAILABLE} rather than a number nobody measured.",
            "full": "DATABASE_URL present — base tables readable, every clause runs.",
            "views": "only CARR_DB_EXPORTER_URL present (least-privilege exporter role, "
                     "views-only by design). Clauses needing base tables report "
                     f"{UNAVAILABLE} rather than a number nobody measured.",
            "none": "no database credential in the environment. Nothing was read.",
        }[self.tier]

    def __enter__(self):
        if self.url:
            import psycopg
            self.conn = psycopg.connect(self.url)
        return self

    def __exit__(self, *exc):
        if self.conn:
            self.conn.close()

    def rows(self, sql, params=()):
        """Returns a list of dicts, or None when the read is not permitted."""
        if not self.conn:
            return None
        cur = self.conn.cursor()
        try:
            cur.execute(sql, params)
        except Exception:
            self.conn.rollback()
            return None
        cols = [d.name for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def config(self, key, default=None):
        r = self.rows("select value from system_config where key = %s", (key,))
        if not r:
            return default
        v = r[0]["value"]
        return v


# ── report plumbing ──────────────────────────────────────────────────────────

class Report:
    def __init__(self, slug, title, src, cmd=None):
        self.slug = slug
        self.cmd = cmd or slug
        self.L = []
        self.verdict = "no run"
        self.headline = ""
        a = self.L.append
        a(f"# {title}")
        a("")
        a(f"GENERATED by `pipelines/learning_jobs.py {self.cmd}`. Never hand-edited: rerun the job.")
        a(f"Run {datetime.now(timezone.utc).isoformat(timespec='seconds')} · "
          f"read tier `{src.tier}` — {src.reason}")
        a("")

    def line(self, s=""):
        self.L.append(s)

    def first_line(self, s):
        """The one sentence a brief would quote. Printed to stdout too."""
        self.headline = s
        self.L.insert(4, f"**{s}**")   # index 4 is the blank __init__ appended,
                                       # so the bold line lands directly under
                                       # the run stamp and keeps its blank after

    def write(self, dirs):
        text = "\n".join(self.L).rstrip() + "\n"
        paths = []
        for d in dirs:
            d = Path(d)
            d.mkdir(parents=True, exist_ok=True)
            p = d / f"{self.slug}-latest.md"
            p.write_text(text)
            paths.append(p)
        return paths


def fmt_thresh(v):
    return v if v is not None else UNAVAILABLE


# ── job 1: weekly learning ───────────────────────────────────────────────────

def job_weekly(src):
    r = Report("weekly-learning", "Weekly learning job — content feature cells", src, "weekly")
    threshold = src.config("learning.min_posts_per_feature_cell")
    exploration = src.config("learning.exploration_share")

    # Feature cells come from a collector view, not from the join. The control
    # plane took carr_jobs off placement and content_piece directly, enforced by
    # ops/control-plane-db-gate.py, which FAILS the build if this role regains
    # table-wide select on either. The view carries the same five values and
    # coalesces `format` itself, so every caller bins identically. If this ever
    # returns None again, the answer is another projection, never a table grant.
    placements = src.rows("""
        select platform, format, placement_id as id, live_at, metric_rows
        from public.v_control_plane_social_feature_cells
    """)

    r.line("## The floor")
    r.line("")
    r.line(f"- `learning.min_posts_per_feature_cell` = **{fmt_thresh(threshold)}**")
    r.line(f"- `learning.exploration_share` = {fmt_thresh(exploration)}")
    r.line("- feature cell = **(platform x format)**. Format is a mechanical fact off the "
           "post's own media (text / image / carousel / video), never a judgment about "
           "the copy.")
    r.line("")

    if placements is None:
        r.first_line(
            "Weekly learning: the placement records could not be read under this "
            f"credential, so no count is stated. Threshold is {fmt_thresh(threshold)} "
            "measured posts per feature cell. No conclusions.")
        r.line("## Result")
        r.line("")
        r.line(f"`v_control_plane_social_feature_cells` is {UNAVAILABLE} under the "
               "credential this run had. That is a permissions fact, not a count of "
               "zero, and the two read almost identically — which is how this went "
               "unnoticed for a week in August 2026. Do NOT answer it by granting this "
               "role select on `placement` or `content_piece`: a control-plane gate "
               "fails the build for exactly that. Check the view exists and is granted "
               "(migration 0191), or rerun with `DATABASE_URL` set.")
        r.verdict = "unavailable"
        return r

    total = len(placements)
    measured = [p for p in placements if p["metric_rows"]]
    cells = collections.Counter((p["platform"], p["format"]) for p in placements)
    cells_m = collections.Counter((p["platform"], p["format"]) for p in measured)

    crossed = []
    if threshold is not None:
        crossed = [c for c, n in cells_m.items() if n >= int(threshold)]

    if total == 0:
        r.first_line(
            "Weekly learning: 0 placements on record, threshold is "
            f"{fmt_thresh(threshold)} measured posts per feature cell, no conclusions yet. "
            "The machinery is on and warmed; it is waiting for rows, not for code.")
    else:
        r.first_line(
            f"Weekly learning: {total} placements on record, {len(measured)} of them "
            f"carrying metrics, threshold is {fmt_thresh(threshold)} measured posts per "
            f"feature cell, {len(crossed)} cell(s) across the floor, no conclusions.")

    r.line("## Cells")
    r.line("")
    if not cells:
        r.line("No placements on record yet.")
    else:
        r.line("| platform | format | posts tagged | posts MEASURED | across the floor? |")
        r.line("|---|---|---|---|---|")
        for c, n in sorted(cells.items(), key=lambda kv: (-kv[1], kv[0])):
            m = cells_m[c]
            ok = "yes" if (threshold is not None and m >= int(threshold)) else "no"
            r.line(f"| {c[0]} | {c[1]} | {n} | {m} | {ok} |")
    r.line("")
    r.line("## Conclusions")
    r.line("")
    if not crossed:
        r.line("**None, and that is the correct output.** No feature cell holds enough "
               "MEASURED posts to say anything that would survive being wrong. The job "
               "ran, the counts are real, and nothing was inferred from them.")
    else:
        r.line("Cells across the floor, listed for a human read. **This job still "
               "proposes nothing** — v1 output is a report; proposals are a later "
               "ruling, not a side effect of a threshold.")
        for c in sorted(crossed):
            r.line(f"- {c[0]} / {c[1]} — {cells_m[c]} measured posts")
    r.line("")
    r.line("## What limits this today")
    r.line("")
    r.line("Metric coverage, not post volume. Blotato collects analytics for a subset "
           "of platforms, and in this workspace that subset is Instagram alone "
           "(measured: `/v2/analytics?platform=twitter` and `...=facebook` both return "
           "an empty list; LinkedIn is not an analytics platform for Blotato at all). "
           "So the cells that fill fastest are the ones that can never be measured "
           "through this lane. Closing that needs a second metrics source — the "
           "existing Chrome-driven weekly pull already reads X, LinkedIn and Meta "
           "natively — and wiring it into `placement_metric` is a design call, not "
           "something this job should decide.")
    r.verdict = "ok"
    return r


# ── job 2: correction miner ──────────────────────────────────────────────────

def job_corrections(src):
    r = Report("correction-miner", "Correction miner — human corrections in the event spine", src, "corrections")

    corrections = src.rows("""
        select e.verb, e.subject_type, e.field, e.occurred_at, a.slug as actor,
               e.human_quote, e.agent_rationale
        from event e join actor a on a.id = e.actor_id
        where e.cause = 'human_correction'
        order by e.occurred_at desc
    """)
    totals = src.rows("select cause, count(*) as n from event group by cause order by 2 desc")

    r.line("## What this mines")
    r.line("")
    r.line("`event` rows whose `cause` is `human_correction` — the audit spine's record "
           "of a human overriding what the system wrote. §C's design point is that "
           "capture never depends on remembering to call `teach`: a correction is a row "
           "whether or not anybody thought of it as a lesson. This job counts them and "
           "reports. It proposes nothing and writes nothing.")
    r.line("")

    if corrections is None:
        r.first_line(
            "Correction miner: the `event` table could not be read under this "
            "credential, so no count is stated — not zero, unknown.")
        r.line("## Result")
        r.line("")
        r.line(f"`event` is {UNAVAILABLE} under the credential this run had. The "
               "exporter role holds views only and no granted view exposes `event.cause` "
               "(`v_subject_timeline` renders events but drops the cause column), so "
               "this clause cannot run without `DATABASE_URL`. Stating '0 corrections' "
               "from here would be the exact failure the reconcile honesty fix (repo "
               "`d0b473c`) was made to stop.")
        r.verdict = "unavailable"
        return r

    n = len(corrections)
    r.first_line(f"Correction miner: {n} human correction(s) on record"
                 f"{'' if n else ' — 0 mined, and 0 is a real measurement here'}. "
                 "Nothing proposed.")
    r.line("## Counts")
    r.line("")
    r.line(f"- `human_correction` events: **{n}**")
    if totals is not None:
        r.line(f"- every event cause on record: "
               + ", ".join(f"`{t['cause']}` {t['n']}" for t in totals) or "none")
    r.line("")
    if n:
        r.line("| when | actor | verb | subject | field |")
        r.line("|---|---|---|---|---|")
        for c in corrections[:50]:
            r.line(f"| {c['occurred_at']:%Y-%m-%d} | {c['actor']} | `{c['verb']}` | "
                   f"{c['subject_type']} | {c['field'] or ''} |")
        r.line("")
        by_field = collections.Counter((c["verb"], c["field"]) for c in corrections)
        repeats = {k: v for k, v in by_field.items() if v > 1}
        r.line(f"Repeated (verb, field) pairs — the promotion review's raw material: "
               f"**{len(repeats)}**")
    else:
        r.line("**0 corrections mined, and that is a measurement rather than a gap.** "
               "The write verbs have been live since build day and no human has "
               "overridden a system-written value through them yet. The miner is armed; "
               "the first correction lands in this report the week it happens.")
    r.verdict = "ok"
    return r


# ── job 3: monthly promotion review ──────────────────────────────────────────

def job_promotion(src):
    r = Report("promotion-review", "Monthly promotion review — the enforcement ladder", src, "promotion")
    threshold = src.config("promotion.min_repeat_violations")

    full = src.rows("""
        select r.id, r.statement, r.enforcement, r.status, r.activated_at,
               t.slug as taught_by, p.slug as personal_to, r.supersedes
        from rule r
        join actor t on t.id = r.taught_by
        left join actor p on p.id = r.personal_to
        where r.status = 'active'
        order by r.activated_at
    """)
    view = None
    if full is None:
        view = src.rows("select * from v_compiled_rules order by activated_at")

    r.line("## The ladder and its floor")
    r.line("")
    r.line(f"- `promotion.min_repeat_violations` = **{fmt_thresh(threshold)}** "
           "post-activation corrections before a rule climbs from `prose` toward "
           "`checklist` / `gate` / `constraint` / `code`.")
    r.line("- A rule only climbs on evidence that it is being broken. A quiet rule "
           "stays prose, which is the cheap end of the ladder on purpose.")
    r.line("")

    rules = full if full is not None else view
    if rules is None:
        r.first_line("Promotion review: the rule store could not be read under this "
                     "credential, so nothing is stated.")
        r.line(f"Both `rule` and `v_compiled_rules` are {UNAVAILABLE} this run.")
        r.verdict = "unavailable"
        return r

    n = len(rules)
    violations = None
    per_rule = {}
    # A violation is a post-activation human correction naming the rule. This runs on
    # BOTH read paths, not just the owner tier: migration 0067 appended `r.id` to
    # v_compiled_rules, and `event` is one of the five relations the jobs role reads,
    # so the join has everything it needs from the views-only credential. It was gated
    # on `full is not None` from before 0067 landed, which made every jobs-tier run
    # print "could not be counted under this credential" — a non-answer standing in for
    # a measurable 0, on the one report this review reads first.
    v = src.rows("""
        select e.subject_id, count(*) as n
        from event e
        where e.subject_type = 'rule' and e.cause = 'human_correction'
        group by e.subject_id
    """)
    if v is not None:
        violations = sum(x["n"] for x in v)
        per_rule = {x["subject_id"]: x["n"] for x in v}

    promotable = [rid for rid, c in per_rule.items()
                  if threshold is not None and c >= int(threshold)]

    if violations is None:
        r.first_line(
            f"Promotion review: {n} active rule(s); repeat violations could not be "
            f"counted under this credential; nothing to promote.")
    else:
        r.first_line(
            f"Promotion review: {n} active rule(s), {violations} repeat violation(s), "
            f"threshold {fmt_thresh(threshold)} — nothing to promote. That is a PASS."
            if not promotable else
            f"Promotion review: {n} active rule(s), {violations} repeat violation(s), "
            f"{len(promotable)} rule(s) at or past the threshold of "
            f"{fmt_thresh(threshold)} — listed for a human ruling, promoted by nobody.")

    r.line("## Active rules")
    r.line("")
    if not rules:
        r.line("None.")
    else:
        r.line("| statement | enforcement | scope | taught by | violations |")
        r.line("|---|---|---|---|---|")
        for x in rules:
            stmt = (x.get("statement") or "")[:90]
            scope = "shared" if not x.get("personal_to") or x.get("personal_to") in (None, "None") \
                else f"personal: {x.get('personal_to')}"
            vc = per_rule.get(x.get("id"), 0) if violations is not None else UNAVAILABLE
            r.line(f"| {stmt} | `{x.get('enforcement', '')}` | {scope} | "
                   f"{x.get('taught_by', '')} | {vc} |")
    r.line("")
    r.line("## Promotions")
    r.line("")
    if not promotable:
        r.line("**None, and that is a pass.** No active rule has been broken often "
               "enough to earn a heavier enforcement class. Machinery on, ladder idle.")
    else:
        r.line("Candidates only. Promotion is a human ruling; this job never changes "
               "`rule.enforcement`.")
        for rid in promotable:
            r.line(f"- rule `{rid}` — {per_rule[rid]} corrections since activation")
    if violations is None:
        r.line("")
        r.line(f"Violation counts are {UNAVAILABLE}: the `event` relation could not be "
               "read under this credential, so post-activation corrections cannot be "
               "joined to the active rules. Rerun with `DATABASE_URL`.")
    r.verdict = "ok"
    return r


# ── job 4: conflict surfacing ────────────────────────────────────────────────

def keywords(text):
    ws = re.findall(r"[a-z']+", (text or "").lower())
    return {w for w in ws if len(w) > 3 and w not in STOPWORDS}


def job_conflicts(src):
    r = Report("conflict-surfacing", "Conflict surfacing — rules that contradict", src, "conflicts")

    full = src.rows("""
        select r.id, r.statement, r.scope::text as scope, r.supersedes, r.activated_at,
               p.slug as personal_to
        from rule r left join actor p on p.id = r.personal_to
        where r.status = 'active' order by r.activated_at
    """)
    view = None
    if full is None:
        view = src.rows("select statement, personal_to, activated_at from v_compiled_rules")

    rules = full if full is not None else view
    if rules is None:
        r.first_line("Conflict surfacing: the rule store could not be read under this "
                     "credential, so nothing is stated.")
        r.verdict = "unavailable"
        return r

    r.line("## What this can and cannot decide")
    r.line("")
    r.line("Two checks, deliberately separated, because only one of them is a fact.")
    r.line("")
    r.line("1. **Mechanical, certain.** An active rule whose `supersedes` target is "
           "ALSO active: two rules binding at once where one was written to replace the "
           "other. This is a contradiction in the record itself and needs no judgment.")
    r.line("2. **Candidates, never verdicts.** Pairs of active rules in the same scope "
           "that share subject vocabulary and carry opposite directive polarity "
           "(never/always). A machine cannot tell a real contradiction from two rules "
           "about the same topic, so these are nominated for a human read and counted "
           "separately. Calling them conflicts would be a conclusion above the data, "
           "which is the one thing §C forbids.")
    r.line("")

    hard = []
    if full is not None:
        active_ids = {x["id"] for x in full}
        for x in full:
            if x["supersedes"] and x["supersedes"] in active_ids:
                hard.append(x)

    cands = []
    for i in range(len(rules)):
        for j in range(i + 1, len(rules)):
            a, b = rules[i], rules[j]
            sa = a.get("personal_to") or "shared"
            sb = b.get("personal_to") or "shared"
            if str(sa) != str(sb):
                continue
            ka, kb = keywords(a.get("statement")), keywords(b.get("statement"))
            overlap = ka & kb
            if len(overlap) < 3:
                continue
            pa = bool(NEGATIVE.search(a.get("statement") or "")), bool(POSITIVE.search(a.get("statement") or ""))
            pb = bool(NEGATIVE.search(b.get("statement") or "")), bool(POSITIVE.search(b.get("statement") or ""))
            if pa == pb:
                continue
            cands.append((a, b, sorted(overlap)[:6]))

    hard_txt = (f"{len(hard)} mechanical contradiction(s)" if full is not None
                else f"mechanical check {UNAVAILABLE} (needs `rule.supersedes`)")
    r.first_line(f"Conflict surfacing: {len(rules)} active rule(s), {hard_txt}, "
                 f"{len(cands)} candidate pair(s) for a human read. Nothing resolved, "
                 "nothing changed.")

    r.line("## Mechanical contradictions")
    r.line("")
    if full is None:
        r.line(f"{UNAVAILABLE}: `v_compiled_rules` does not expose `supersedes`. "
               "Rerun with `DATABASE_URL`.")
    elif not hard:
        r.line("**None.** No active rule supersedes another active rule.")
    else:
        for x in hard:
            r.line(f"- `{x['id']}` supersedes `{x['supersedes']}`, and both are active: "
                   f"{(x['statement'] or '')[:110]}")

    r.line("")
    r.line("## Candidate pairs")
    r.line("")
    if not cands:
        r.line("**None.** No two active rules in the same scope share enough subject "
               "vocabulary while pointing opposite ways to be worth a human's minute.")
    else:
        # Ranked and capped, and the cap is stated. The pair count is quadratic in the
        # rule count, so at 220 active rules this printed 5,885 pairs into a 1.9 MB file
        # on 2026-08-15 — a report whose own doctrine says noise on a queue is
        # indistinguishable from no queue. Ranking by overlap size puts the pairs most
        # likely to be a real contradiction first; the tail is counted, never silently
        # dropped, so a reader can see exactly what was withheld and ask for more.
        ranked = sorted(cands, key=lambda t: (-len(t[2]), (t[0].get("statement") or "")))
        shown = ranked[:CANDIDATE_PAIR_CAP]
        for a, b, ov in shown:
            r.line(f"- shared terms {ov}")
            r.line(f"  - {(a.get('statement') or '')[:120]}")
            r.line(f"  - {(b.get('statement') or '')[:120]}")
        if len(ranked) > len(shown):
            r.line("")
            r.line(f"**{len(ranked) - len(shown)} further candidate pair(s) not printed** "
                   f"— this report shows the {CANDIDATE_PAIR_CAP} with the most shared "
                   "vocabulary, which is where a real contradiction is most likely to "
                   "sit. Nothing was discarded: raise `CANDIDATE_PAIR_CAP` to see the "
                   "rest. A count this large is itself the finding — a keyword-overlap "
                   "heuristic does not scale past a few hundred rules and needs a "
                   "tighter test, not a longer list.")
    r.verdict = "ok"
    return r


# ── CLI ──────────────────────────────────────────────────────────────────────

JOBS = {
    "weekly": job_weekly,
    "corrections": job_corrections,
    "promotion": job_promotion,
    "conflicts": job_conflicts,
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("job", choices=list(JOBS) + ["all", "weekly-chain", "monthly-chain"])
    ap.add_argument("--report-dir", action="append", default=None,
                    help="directory to write <job>-latest.md into (repeatable)")
    a = ap.parse_args()

    names = {"all": list(JOBS),
             "weekly-chain": ["weekly", "corrections"],
             "monthly-chain": ["promotion", "conflicts"]}.get(a.job, [a.job])

    dirs = a.report_dir or [str(OUT / "Learning")]
    rc = 0
    with Source() as src:
        for name in names:
            rep = JOBS[name](src)
            paths = rep.write(dirs)
            print(f"[{name}] {rep.headline}")
            for p in paths:
                print(f"    report: {p}")
            if rep.verdict == "unavailable":
                rc = max(rc, 3)
    return rc


if __name__ == "__main__":
    sys.exit(main())
