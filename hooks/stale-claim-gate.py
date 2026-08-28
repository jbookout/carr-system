#!/usr/bin/env python3
"""stale-claim-gate.py — before telling Joe something is broken, check whether
it was already fixed.

THE FAILURE CLASS, and the half of it nothing covered. The most common way this
system has been wrong is "dated-artifact-read-as-present-state": 10 rows since
2026-08-04, 6 of them caught by Joe rather than by the system. Read all ten back
and they split in two.

  WAS IT CHOSEN?  A present state read accurately, and a RULING behind it left
                  unread. hooks/drift-claim-gate.py covers this: it searches
                  decision-history.md and puts the governing decision in front
                  of the session before the claim is written.

  WAS IT ALREADY  A claim that something was never run, is a live hazard, is
  FIXED?          blocked, or is still failing — when the fix had already
                  shipped. Four rows, and nothing covered them. The refuting
                  artifact is a COMMIT, and drift-claim-gate cannot find it:
                  it reads the decision log only, and a merged pull request
                  never lands there.

THE MOMENT WAS ALSO WRONG, AND THAT HALF IS ALREADY FIXED BY SOMEONE ELSE.
drift-claim-gate is PreToolUse on record-defect and add-loop, so it speaks only
when a claim reaches the record; eight of the ten rows never reached a write
verb at all. hooks/drift-assertion-gate.py (commit cfaec02, merged 2026-08-14
19:52 while this file was being written in a worktree) moved exactly that
judgement to the Stop door, reusing drift-claim-gate's own detector and search.
It reached the right diagnosis independently and got there first.

SO WHAT IS LEFT IS THE SOURCE, WHICH IS THE WHOLE OF THIS FILE. Both existing
gates read decision-history.md and nothing else. Neither can refute "still
failing" with a merged pull request, because a pull request never appears in the
decision log. The two doors now divide cleanly:

    drift-assertion-gate.py   Stop + decision log   was it CHOSEN?
    stale-claim-gate.py       Stop + git subjects   was it already FIXED?

Both fire once per claim and both stay silent when their own source says
nothing, so a message carrying neither kind of error passes untouched.

WHAT IT SEARCHES. `git log` over the last STALE_WINDOW_DAYS, subject lines only.
Commit subjects in this repo are written as plain sentences about what changed,
which is exactly the shape a staleness claim collides with: on 2026-08-14 the
subject line read "The Friday social batch gets its writer, so building next
week's drafts stops failing the nightly chain" and the session read past it.

TWO CONDITIONS, BOTH REQUIRED, for the anti-alarm-fatigue reason drift-claim-
gate documents at length: the text must read like a staleness claim AND recent
history must hold a commit about that subject. A staleness claim with no
matching commit passes in silence — reporting real breakage is core work and
must never need an argument.

RARITY, NOT OVERLAP, decides a match. Three ranking designs failed on
drift-claim-gate's replay and are not retried here: any-single-token, hyphen-
weighting, and equal counting. One rare word beats three common ones. A stem in
more than GENERIC_SHARE of subjects identifies nothing and is discarded.

THE GUARD THAT MATTERS MOST is already-cited. A session quoting the commit —
a correction, a status report, a summary of what landed — has plainly read it
and passes untouched. Without this the gate would block the very message that
corrects the error it exists to catch.

IT ANNOUNCES, IT NO LONGER REOPENS (2026-08-23, Joe's Stop-gate rationing off
the gates-audit council). It used to exit 2, which forces a whole extra
assistant message. Eleven Stop hooks held that power and one measured shipped
session paid nine such reopens for findings that changed nothing, against this
system's standing constraint of no steady-state token ceremony. Three keep it:
core conduct, completion-evidence, drift-assertion.

WHY THIS ONE LOST IT, and the reasoning is specific to what this gate finds. A
reopen earns its cost when the next message is the RESULT OF WORK rather than a
restatement — the split hooks/chat-lint-carryover.py drew on 2026-08-16. Here
the work is a re-check the session runs anyway once it knows the commit exists,
and by Stop the stale sentence has already reached Joe and cannot be unsent. So
the reopen bought a second copy of a message he had read, plus the re-check;
announcing buys the re-check alone, at the same moment, with the commit subjects
in hand. If a week of hook telemetry shows announced staleness findings being
carried past uncorrected where reopened ones were fixed, the register goes back
and this paragraph is the record of why it was tried.

NEVER LOOPS: stop_hook_active short-circuits, same as every Stop gate here.
FAILS OPEN on any error, including no git, no repo, and a git call that hangs.
Audit rows share out/conduct-gate.jsonl; fixtures (session 'selftest') do not
count. Debug to out/hook-guard.log.

Fixtures: ops/stale-claim-gate-selftest.py.
"""
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from stop_latch import announce  # noqa: E402
LOG = os.path.join(REPO, "out", "conduct-gate.jsonl")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:                                    # telemetry only — never load-bearing
    import hook_meter
    DEBUG = hook_meter.guard_log_path(REPO)
except Exception:                       # a missing meter must not change a verdict
    DEBUG = os.path.join(REPO, "out", "hook-guard.log")

# How far back a fix could plausibly have landed and still be news to a session
# reading a dated artifact. The four rows this targets were 0 to 3 days stale;
# 14 days gives margin without dragging in history nobody is claiming about.
STALE_WINDOW_DAYS = 14
GIT_TIMEOUT = 10

# Language that asserts something IS broken, was never done, or needs building.
# Deliberately narrower than drift-claim-gate's DRIFT pattern: that one also
# catches "was chosen wrongly" shapes, which the decision-log search handles.
STALE = re.compile(
    r"\b(?:"
    r"still (?:fail(?:s|ing)?|broken|red|failing|not|un\w+)|"
    r"(?:is|are|was|were) still\b|"
    r"never (?:ran|run|built|done|wired|registered|scoped|shipped|landed|fixed)|"
    r"(?:has|have|had) no writer|"
    r"was never\b|"
    r"not yet (?:built|wired|done|fixed|registered|scoped)|"
    r"(?:is|are) (?:currently )?(?:failing|broken|blocked|unfixed)|"
    r"blocks? (?:a |the )?\w+ (?:every|each) \w+|"
    r"needs? (?:a )?(?:fix|writer|migration|build)|"
    r"one-line fix|"
    r"until it lands|"
    r"i'?ll (?:take a worktree|build|add|register|wire|fix|write) \w+"
    r")\b", re.I)

# Same token shape drift-claim-gate uses: hyphenated and dotted names, or long
# plain words. Short generic words would match every commit in the repo.
TOKEN = re.compile(r"\b([a-z][a-z0-9]*(?:[-_][a-z0-9]+)+(?:\.[a-z]+)?|[a-z]{6,})\b", re.I)
SHORT_HASH = re.compile(r"\b[0-9a-f]{7,40}\b")

# Words that carry no subject. Shared in spirit with drift-claim-gate's STOP
# list; the additions here are the vocabulary of talking ABOUT commits and
# claims, which is everywhere in this repo's own history.
STOP = {
    "should", "actually", "without", "because", "however", "already", "session",
    "sessions", "system", "record", "records", "recorded", "closed", "filed",
    "finding", "findings", "before", "against", "instead", "reported", "report",
    "nothing", "something", "anything", "another", "current", "present",
    "artifact", "artifacts", "verified", "confirm", "confirmed", "message",
    "written", "writing", "thing", "things", "change", "changed", "changes",
    "working", "produce", "produced", "result", "results", "wrong", "correct",
    "itself", "which", "whose", "state", "stated", "would", "could", "still",
    "never", "commit", "commits", "committed", "branch", "worktree", "request",
    "pull", "merge", "merged", "landed", "shipped", "update", "updates",
    "updated", "readme", "docs", "documentation", "broken", "failing", "fixed",
    "problem", "problems", "issue", "issues", "morning", "tonight", "yesterday",
    "afterwards", "previously", "currently", "entirely", "deliberately",
    "immediately", "eventually", "generally", "running", "either", "status",
}

MAX_TOKENS = 14
MAX_HITS = 4
# A stem appearing in more than this share of subjects says nothing about which
# subject matters. Floor of 2 keeps a tiny history from disqualifying every stem.
GENERIC_SHARE = 0.02
GENERIC_FLOOR = 2

# TWO DISTINCT STEMS, MEASURED NOT GUESSED. This gate DENIES, so its precision
# bar is far higher than drift-claim-gate's, which only attaches context and can
# afford a loose match. Replayed against the real 780-commit window, rarity
# alone produced two false positives on the first run, and both were a SINGLE
# rare word colliding by accident: a genuine new finding about "queue semantics"
# matched a retrieval commit on the stem "semant", and a true report about
# Salesforce export matched three unrelated commits on "addres" and "chrome".
# One shared rare word is a coincidence; two is a subject. The true positive
# survives comfortably — the 2026-08-14 commit shares friday, social and week.
MIN_STEMS = 2
MIN_SCORE = 0.4
STEM_LEN = 6


def now():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def dlog(msg):
    try:
        os.makedirs(os.path.dirname(DEBUG), exist_ok=True)
        with open(DEBUG, "a") as fh:
            fh.write(f"{now()} stale-claim-gate {msg.rstrip()}\n")
    except Exception:
        pass


def audit(row):
    if row.get("session") == "selftest":
        return
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a") as fh:
            fh.write(json.dumps(row) + "\n")
    except Exception:
        pass


def read_tail(path, limit=400):
    out = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return []
    return out[-limit:]


def text_of(rec, kinds):
    if rec.get("type") not in kinds:
        return None
    msg = rec.get("message") or rec
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content
                 if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(p for p in parts if p)
    return None


def strip_fences(text):
    """Fenced blocks are evidence being SHOWN, not a claim being made. A session
    pasting this morning's failing log is doing the right thing."""
    return re.sub(r"```.*?```", " ", text, flags=re.S)


def stem(token):
    alpha = re.match(r"[a-z]+", token)
    return (alpha.group(0)[:STEM_LEN] if alpha else token[:STEM_LEN])


def salient_tokens(text):
    seen, hyphen, plain = set(), [], []
    for m in TOKEN.finditer(text):
        t = m.group(1).lower()
        if t in STOP or t in seen:
            continue
        seen.add(t)
        (hyphen if ("-" in t or "_" in t or "." in t) else plain).append(t)
    return (hyphen + plain)[:MAX_TOKENS]


def recent_commits(repo):
    """(short_hash, subject) for the window. Subjects only: the body is often a
    long explanation whose vocabulary would match anything."""
    try:
        p = subprocess.run(
            ["git", "-C", repo, "log", f"--since={STALE_WINDOW_DAYS}.days",
             "--no-merges", "--format=%h%x09%s"],
            capture_output=True, text=True, timeout=GIT_TIMEOUT)
    except Exception as exc:
        dlog(f"ALLOW(git-unavailable) {exc}")
        return []
    if p.returncode != 0:
        dlog(f"ALLOW(git-rc={p.returncode}) {(p.stderr or '').strip()[:120]}")
        return []
    rows = []
    for line in (p.stdout or "").splitlines():
        if "\t" in line:
            h, s = line.split("\t", 1)
            if s.strip():
                rows.append((h.strip(), s.strip()))
    return rows


def already_cited(prose, commits):
    """A session quoting the commit has read it. Two ways to count as cited:
    naming the short hash anywhere (fences included — quoting `git log` output
    IS reading it), or reproducing a distinctive run of its subject line."""
    hashes = {h.lower() for h in SHORT_HASH.findall(prose)}
    low = prose.lower()
    for h, subject in commits:
        if h.lower() in hashes or any(x.startswith(h.lower()) for x in hashes):
            return h, subject
        words = [w for w in re.findall(r"[a-z']+", subject.lower()) if len(w) > 2]
        for i in range(0, max(0, len(words) - 4)):
            if " ".join(words[i:i + 5]) in low:
                return h, subject
    return None


def match_commits(tokens, commits):
    if not commits:
        return []
    subjects = [s.lower() for _, s in commits]
    cutoff = max(GENERIC_FLOOR, int(len(subjects) * GENERIC_SHARE))

    stems = []
    for t in tokens:
        s = stem(t)
        if s and s not in stems and len(s) >= 4:
            stems.append(s)

    weights = {}
    for s in stems:
        n = sum(1 for sub in subjects if s in sub)
        if 0 < n <= cutoff:
            weights[s] = 1.0 / n
    if not weights:
        return []

    scored = []
    for idx, sub in enumerate(subjects):
        matched = [s for s in weights if s in sub]
        if len(matched) < MIN_STEMS:
            continue
        score = sum(weights[s] for s in matched)
        if score < MIN_SCORE:
            continue
        matched.sort(key=lambda s: -weights[s])
        scored.append((-score, idx, ", ".join(matched[:3])))
    scored.sort()
    return [(commits[i][0], commits[i][1], m) for _, i, m in scored[:MAX_HITS]]


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)

    try:
        if (payload.get("hook_event_name") or "Stop") != "Stop":
            sys.exit(0)
        if payload.get("stop_hook_active"):
            sys.exit(0)

        path = payload.get("transcript_path")
        if not path or not os.path.exists(path):
            sys.exit(0)

        assistant = ""
        for rec in read_tail(path):
            t = text_of(rec, ("assistant",))
            if t and t.strip():
                assistant = t.strip()
        if not assistant:
            sys.exit(0)

        prose = strip_fences(assistant)
        if len(prose) < 60 or not STALE.search(prose):
            sys.exit(0)

        repo = os.environ.get("CARR_STALE_CLAIM_REPO") or REPO
        commits = recent_commits(repo)
        if not commits:
            sys.exit(0)

        cited = already_cited(assistant, commits)
        if cited:
            dlog(f"ALLOW(already-cited) {cited[0]} {cited[1][:70]}")
            sys.exit(0)

        hits = match_commits(salient_tokens(prose), commits)
        if not hits:
            # No commit about this subject. Probably a real finding, and
            # silence is the correct output.
            sys.exit(0)

        audit({"ts": now(), "hook": "stale-claim-gate", "register": "announce",
               "session": payload.get("session_id"),
               "commits": [h for h, _, _ in hits],
               "excerpt": (STALE.search(prose).group(0) if STALE.search(prose)
                           else "")[:120]})

        lines = [
            "STALE CLAIM — SOMETHING SHIPPED ABOUT THIS SUBJECT IN THE LAST "
            f"{STALE_WINDOW_DAYS} DAYS. Check it before this reaches Joe.",
            "",
            "You are telling him something is broken, unfinished, or needs "
            "building. That is the most frequent failure class on record here, "
            "running since 2026-08-04, most of them caught by Joe rather than "
            "by a session, and a large share were work that had already "
            "shipped. `standing-context` returns the live ledger with current "
            "counts. On 2026-08-14 a session read a failing log from that "
            "morning, missed the fix merged three hours later, and offered to "
            "go rebuild it.",
            "",
            "Recent commits about this subject:",
            "",
        ]
        for h, subject, matched in hits:
            lines.append(f"  · {h}  {subject[:150]}")
            lines.append(f"      matched on: {matched}")
        lines += [
            "",
            "Re-check the CURRENT state, not the artifact you read: run the "
            "check, read the file, or show the commit does not cover it. Then "
            "either correct the claim, or quote the commit and say why it "
            "leaves the problem open — citing it clears this gate.",
            "",
            "This does not reopen your turn. Act on it in the next thing you "
            "do rather than restating the last thing you said.",
        ]
        raise SystemExit(announce("\n".join(lines)))

    except SystemExit:
        raise
    except Exception as exc:
        dlog(f"ALLOW(internal-error) {exc}")
        sys.exit(0)


if __name__ == "__main__":
    main()
