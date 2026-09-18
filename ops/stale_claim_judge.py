"""stale_claim_judge.py — find the commit that refutes a claim, by reading them.

WHAT hooks/stale-claim-gate.py DOES. It fires when a session is about to tell
Joe something is broken AND a commit from the last fortnight matches the claim
on rare word stems. It guards the most frequent failure class on record here,
"dated-artifact-read-as-present-state", most instances of which were caught by
Joe rather than by the system.

WHAT IS ACTUALLY WRONG WITH IT, measured 2026-09-18 and NOT what this module
was first written to fix. The obvious worry about a keyword gate is that it
cries wolf. This one has the opposite fault: it is nearly deaf. Four claims
were put through the shipped matcher unchanged, and it returned nothing for any
of them — including a claim that the shell-command pre-check "has not shipped"
while a commit reading "Warn before a shell command runs, in every session" sat
in the window. The matcher asks for two rare stems shared with a commit
subject, and two texts can describe the very same thing without sharing two
rare words. Its caution is well argued — three cruder ranking designs were
tried and rejected before it — but rarity is all it has, and rarity is not
meaning.

  claim                              refuting commit   stem matcher   judged
  --------------------------------   ---------------   ------------   ------
  pre-check "has not shipped"        in the window     nothing        1st, 0.84
  Doc conversation list "never       in the window     nothing        1st, 0.89
    built"
  "still guarded twice over"         NOT in window     nothing        top 0.66
  "empty string ... never fixed"     NOT in window     nothing        top 0.25

THE LAST TWO ROWS ARE THE CONTROLS AND THEY MATTER AS MUCH AS THE FIRST TWO.
Their refuting commits were squashed out of history when an earlier pull
request merged, so nothing in the window answers them. Asked about all 214
commits anyway, the judgment put nothing above 0.66 and 0.25 — it declined to
invent a match. A search that finds something for every query would be worse
than the matcher, not better, because this gate's silence is load-bearing:
reporting real breakage is core work and must never need an argument.

SO THIS READS EVERY COMMIT IN THE WINDOW, ONE REQUEST PER COMMIT. Around 214 of
them, about four seconds across the pool, a fraction of a cent, and only on a
message that already looks like a staleness claim. One request per candidate
and never one request carrying all of them: a state holding every commit lets
each judgment see its competitors and does not reproduce the published method.

THE STEM MATCHER REMAINS THE FALLBACK, not a competitor. When the judgment is
unavailable for any reason — no credential, a timeout, an outage, a malformed
answer — the caller keeps the matcher's own hits and the gate behaves exactly
as it does today. Today's behaviour is the floor.

IT IS A LIBRARY AND MUST STAY ONE. No shebang and no main guard: either turns a
.py file into a registered script entrypoint in the sealed source inventory,
moves the frontier and owes a forward-only registry successor. The detector is
a regex over the whole file with no notion of docstrings, so the construct is
described here and never spelled. ops/typesafe_client.py carries the long form.
"""

import importlib.util
import os
from concurrent.futures import ThreadPoolExecutor

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# A commit at or above this refutes the claim. The measured true positives sit
# at 0.84 and 0.89 and the best score against a window holding no answer at all
# was 0.66, so this sits in the gap rather than on a knife edge. Re-derive it
# from real traffic rather than defending the number.
CONFIRM_AT = 0.75

# Never announce more than this, matching the gate's own cap. A wall of commits
# is not more useful than the best few.
MAX_HITS = 4

# The whole window goes out at once; this is what keeps it inside four seconds.
WORKERS = 32

# Short enough that a Stop door does not notice, long enough to be answered.
TIMEOUT_SECONDS = 15.0

# Set to 0 to leave the gate exactly as it was.
DISABLE = "CARR_STALE_CLAIM_JUDGE"


def _sibling(name):
    """Load a module from ops/ by path.

    ops/ holds no __init__.py, so it is not a package and a relative import
    from a sibling raises.
    """
    path = os.path.join(REPO, "ops", name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - import plumbing
        raise RuntimeError("cannot load ops/" + name + ".py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def addresses_question(client=None):
    """The one question. The false criterion is the whole contract.

    A claim and a commit that share vocabulary are exactly what a stem matcher
    finds and exactly what it cannot tell apart from a real match, so a
    criterion that does not name shared vocabulary as the boundary case simply
    re-implements the matcher's mistake at greater expense.
    """
    tsc = client or _sibling("typesafe_client")
    return tsc.noul(
        "The state holds a claim a session is about to make — that something "
        "is broken, unbuilt, blocked or still failing — and the subject line "
        "of one commit from the last two weeks. Does that commit address the "
        "very problem the claim describes?",
        true="A reader who knew about this commit would treat the claim as "
             "already answered, or would at least have to explain why the "
             "commit does not cover it. The commit is about the same specific "
             "problem, not merely the same area of the system.",
        false="The commit is about a different problem. THIS IS THE CASE THAT "
              "IS EASY TO GET WRONG: a claim and a commit can name the same "
              "file, the same tool, the same command or the same corner of the "
              "system and still concern different things, and most commits in "
              "any window have nothing to do with any given claim. Also false "
              "when the commit touches the right subject but plainly does not "
              "resolve what the claim says is wrong. Answering no to every "
              "commit is the correct outcome when nothing in the window "
              "answers the claim, and that is the common case.")


def _score(claim, commit, question, judge, api_key, client):
    try:
        answer = judge.judge({"claim": claim[:1500], "commit_subject": commit[1]},
                             {"addresses": question}, timeout=TIMEOUT_SECONDS,
                             client=client, api_key=api_key)
        return commit, float(answer["answers"]["addresses"]["noul"])
    except Exception:
        return commit, None


def refuting_commits(claim, commits, *, floor=CONFIRM_AT, limit=MAX_HITS,
                     client=None, api_key=None, judge=None, workers=WORKERS):
    """Commits that answer the claim, best first, or None when unavailable.

    Returns a list of (hash, subject, why) triples in the gate's own hit shape,
    where `why` is the probability rendered for a reader. An EMPTY LIST is a
    real answer and means the window holds nothing that refutes the claim —
    the measured common case, and the one the gate must stay silent on.

    Returns None, distinct from an empty list, when no judgment could be
    obtained at all. A caller then falls back to the stem matcher, which is
    today's behaviour. Never raises.
    """
    if not commits:
        return []
    if os.environ.get(DISABLE) == "0":
        return None
    try:
        judge = judge or _sibling("jev_judge")
        question = addresses_question(client)
        with ThreadPoolExecutor(max(1, min(workers, len(commits)))) as executor:
            scored = list(executor.map(
                lambda commit: _score(claim, commit, question, judge, api_key, client),
                commits))
    except Exception:
        return None
    answered = [(commit, probability) for commit, probability in scored
                if probability is not None]
    if not answered:
        # Every request failed. That is an outage, not a finding of "nothing
        # refutes this", and the two must never look the same to the caller.
        return None
    answered.sort(key=lambda item: -item[1])
    return [(commit[0], commit[1], "read as answering this claim (%.2f)" % probability)
            for commit, probability in answered
            if probability >= floor][:limit]
