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

SO THIS READS EVERY COMMIT IN THE WINDOW, IN ONE REQUEST. The first version
asked one question per commit — 214 requests, 4.2 seconds — on the belief that
the published method forbids carrying every candidate in one state. That belief
was a misreading. One request per candidate is the RERANKING rule and it
applies to a shortlist of thirty that a keyword search produced first; picking
one item from a roster is a Choice question carrying the whole roster, which is
how the vendor ranks 182 agent skills and scores 218 document lines.

Re-measured on the same four claims: ONE request, 0.6 seconds, the same four
answers out of four. The commits that answer a claim came back at 0.90 and
0.88, and on the two claims nothing answers, the explicit "no commit here
answers this" option came back at 0.96 and 0.77. That option is why this reads
better than the old floor did — declining is now a calibrated answer the model
gives rather than a threshold chosen by hand.

A Choice carries up to 255 options and the window holds around 214, so it fits
with room. If a window ever exceeds the cap, trim it by date before asking
rather than splitting into two requests: probabilities sum to one WITHIN a
request, so scores from two separate Choices are not comparable and pooling
them across normalizations is a measurement error, not a workaround.

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

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The chosen commit is announced when it beats the none-of-these option by this
# much. Measured: real answers took 0.90 and 0.88 against a none option at 0.07
# and 0.08, while the two claims with no answer put none at 0.96 and 0.77. The
# gap is wide in both directions, so this is not a knife edge. Re-derive it from
# real traffic rather than defending the number.
CONFIRM_AT = 0.50

# Never announce more than this, matching the gate's own cap. A wall of commits
# is not more useful than the best few.
MAX_HITS = 4

# A Choice carries at most this many options. The window is trimmed to the most
# recent when it runs over, because two Choices cannot be compared to each other.
MAX_OPTIONS = 254

# The escape hatch. Without an explicit none-of-these option a Choice must pick
# something, and this gate's silence is load-bearing.
NONE_OF_THESE = "no commit here answers this claim"

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


def build_question(commits, client=None):
    """The one Choice, carrying every commit in the window.

    The none-of-these option is the whole contract. Choice probabilities sum to
    one across the options, so without an explicit way to decline, the model
    must hand back a commit for every claim — and a gate that finds something
    every time is worse than the stem matcher it replaces, because this gate's
    silence is load-bearing. Its rubric names the boundary the stem matcher
    cannot see: shared vocabulary is not a shared problem.

    `commits` is a list of (hash, subject). Option names are the hashes, which
    is what the caller needs back; each subject is that option's rubric, cut to
    150 characters because a ranking pass over hundreds of full-length options
    returns HTTP 400 max_tokens_exceeded.
    """
    tsc = client or _sibling("typesafe_client")
    options = {commit: (subject or "")[:150] for commit, subject in commits}
    options[NONE_OF_THESE] = (
        "None of the commits listed answers this claim. Choose this when the "
        "others only share words with it — the same file, the same tool, the "
        "same corner of the system — rather than fixing the very thing the "
        "claim says is wrong. THIS IS THE COMMON CASE: most windows hold "
        "nothing that answers any given claim, and saying so is the right "
        "answer rather than a failure to find one.")
    return tsc.choice(
        "The claim in `state.claim` says something is broken, unbuilt, blocked "
        "or still failing. Which of these recent commit subjects already "
        "answers it — fixes or delivers the very thing the claim says is "
        "missing?", options)


def refuting_commits(claim, commits, *, floor=CONFIRM_AT, limit=MAX_HITS,
                     client=None, api_key=None, judge=None):
    """Commits that answer the claim, best first, or None when unavailable.

    ONE request carrying every commit, because the candidates are competing for
    a single slot rather than each being independently true. Returns a list of
    (hash, subject, why) triples in the gate's own hit shape.

    An EMPTY LIST is a real answer and means the window holds nothing that
    refutes the claim — the measured common case, and the one the gate must
    stay silent on. It is returned when the none-of-these option wins, or when
    no commit clears the floor.

    Returns None, distinct from an empty list, when no judgment could be
    obtained at all. A caller then falls back to the stem matcher, which is
    today's behaviour. Never raises.
    """
    if not commits:
        return []
    if os.environ.get(DISABLE) == "0":
        return None
    # Trim by recency rather than splitting: probabilities sum to one WITHIN a
    # request, so two Choices produce numbers that cannot be compared and
    # pooling them across normalizations is a measurement error.
    commits = list(commits)[:MAX_OPTIONS]
    subjects = dict(commits)
    try:
        judge = judge or _sibling("jev_judge")
        question = build_question(commits, client)
        answer = judge.judge({"claim": claim[:1500]}, {"pick": question},
                             timeout=TIMEOUT_SECONDS, client=client,
                             api_key=api_key)
        picked = answer["answers"]["pick"]
    except Exception:
        return None
    probabilities = picked.get("probabilities") or {}
    if not probabilities:
        return None
    declined = float(probabilities.get(NONE_OF_THESE, 0.0))
    ranked = sorted(((commit, float(p)) for commit, p in probabilities.items()
                     if commit != NONE_OF_THESE and commit in subjects),
                    key=lambda item: -item[1])
    # The model declining outright ends it, whatever the runners-up scored.
    if not ranked or declined >= ranked[0][1]:
        return []
    return [(commit, subjects[commit],
             "read as answering this claim (%.2f, against %.2f that nothing here does)"
             % (probability, declined))
            for commit, probability in ranked
            if probability >= floor][:limit]
