"""jev_judge.py — ask Jev a narrow question about a body of text, and RECORD it.

WHAT THIS IS FOR. CARR decides a great many things by matching strings: whether
a commit fixes the problem a claim describes, whether a response accounts for a
clause, whether a defect belongs in a class that already exists, whether a
doctrine section answers a question. Every one of those is a judgment about
text wearing a keyword table's clothing, and every one of them has misfired.
This module is the one place those judgments get asked properly.

IT IS A LIBRARY AND MUST STAY ONE. No shebang, no main guard, for the reason
ops/typesafe_client.py spells out at length: either one makes a .py file a
registered script entrypoint in the sealed source inventory, moves the frontier
and owes a forward-only registry successor. Callers are entrypoints that exist.
The detector is a regex over the whole file and does not know what a docstring
is, so describe the construct and never spell it.

SHADOW FIRST, AND THAT IS NOT A PHASE — IT IS THE DEFAULT MODE. record() writes
what the judgment WOULD have decided next to what the existing mechanism DID
decide, and returns without acting. A caller only moves to acting after the log
shows agreement on real traffic, with a threshold measured on that traffic
rather than guessed. This is the same shadow-first discipline rule delivery
already uses, and it exists because a typed answer guarantees an answer's shape
and never its truth: a gate that blocks on an unvalidated threshold converts a
model's uncertainty into a partner's blocked afternoon.

THREE THINGS LEARNED THE EXPENSIVE WAY ON 2026-09-18, all of them shaping the
interface below:

  1. PICK THE SHAPE FROM THE JOB, AND THERE ARE TWO SHAPES. This entry said
     "one request per subject, never one request carrying every subject" as
     though it were a law. That is the RERANKING rule, it is real, and it is
     narrow: the vendor's reranking walkthrough scores query-and-candidate
     pairs one request at a time, no request seeing another — over a SHORTLIST
     OF THIRTY that a keyword search produced first. Reading it as universal
     was wrong and it cost this repository four modules built the expensive way.

     SELECTING FROM A ROSTER IS THE OTHER SHAPE, and the vendor's own examples
     are exactly that: 182 agent skills ranked in ONE Choice question, 218
     document line identifiers scored in ONE request. A Choice carries up to
     255 options, its probabilities sum to one across them, and an explicit
     "none of these fits" option is how it declines. Then, if anything, a close
     look at the top two or three.

     MEASURED HERE ON 2026-09-18, same held-out data, same corpus, the two
     shapes against each other on picking a defect class from 320:

         one Noul per class ....... 320 requests  12.5s  38% top-1  81% top-8
         one Choice, then a look ...  9 requests   1.4s  69% top-1  88% top-8

     And on finding the commit that answers a claim, over 214 commits: 214
     requests and 4.2 seconds became ONE request and 0.6 seconds, with the same
     four answers out of four, and with "nothing here answers this" arriving as
     a calibrated option rather than as a hand-set floor.

     THE TEST, so this does not get misread again: are the candidates competing
     for one slot, or is each independently true or false? Competing for a slot
     is a Choice over all of them. Independently true or false — several may
     apply, or none — is a Noul each, and then it belongs on a shortlist rather
     than on the whole roster. Truncate option text in the ranking pass and
     keep the full text for the close look; 255 full-length options returns
     HTTP 400 max_tokens_exceeded, which is how that stops being optional.

  1b. ASK EVERY INDEPENDENT QUESTION ABOUT ONE SUBJECT IN ONE REQUEST. This is
     the vendor's headline efficiency principle and the opposite of what the
     old entry above implied. Their measurement: thirteen questions about one
     document batched into a single call came out 12.2 times cheaper and 10
     times faster, and each question is scored on its own against the state, so
     an answer does not depend on what else rides along. A broad question that
     hides several judgments is the thing to avoid — decompose it into narrow
     ones and combine them in code, in ONE call, not in several.

  2. LOW CONFIDENCE IS THE ANSWER, NOT A FAILURE TO ANSWER. On the day this was
     written, Jev's wrong calls arrived under 0.5 confidence and its right ones
     above 0.8, twice in a row, on real code. A caller that ignores confidence
     throws away the most useful half of the signal.

  3. A QUESTION NOBODY THINKS TO ASK RETURNS NOTHING. Jev sharpened a review; it
     did not replace one. These judgments narrow where a human or a larger model
     looks. They do not decide that nobody needs to look.
"""

import json
import os
import time
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Shadow observations land here. One JSON object per line, append-only, so a
# threshold can be measured later from real traffic rather than argued for now.
SHADOW_LOG = os.path.join(REPO, "out", "jev-judge.jsonl")

# Deliberately pessimistic defaults. THEY ARE PLACEHOLDERS: every caller is
# expected to replace them with numbers measured on its own shadow log, because
# the cost of a wrong call is wildly different between a commit-message warning
# and anything a partner or a client sees.
YES_AT = 0.80
NO_AT = 0.20
MIN_CONFIDENCE = 0.60


class JudgeUnavailable(RuntimeError):
    """The judgment could not be obtained. NEVER a reason to fail a caller.

    A gate that hard-fails when an outside service is slow is a gate that turns
    someone else's outage into this repository's outage. Callers catch this and
    proceed on their existing mechanism; record() notes the miss so an outage
    is visible in the log rather than silently reducing coverage.
    """


def _client():
    """Import the vendor client lazily, so importing this module costs nothing."""
    import importlib.util
    path = os.path.join(REPO, "ops", "typesafe_client.py")
    spec = importlib.util.spec_from_file_location("typesafe_client", path)
    if spec is None or spec.loader is None:  # pragma: no cover - import plumbing
        raise JudgeUnavailable("cannot load ops/typesafe_client.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def judge(subject, questions, *, timeout=20.0, client=None, api_key=None):
    """Ask every question in `questions` about ONE subject, in one request.

    `subject` is a mapping describing the single thing being judged — a diff, a
    response, a defect, a candidate section. `questions` maps a caller-chosen id
    to a question built with the client's noul/choice/score helpers.

    Returns {"answers": {...}, "usage": {...}, "model": ..., "elapsed_ms": int}.
    Raises JudgeUnavailable for ANY failure, including a missing credential, so
    a caller has exactly one thing to catch.

    The timeout is short on purpose. This is meant to be called from hooks and
    gates that sit in someone's way, and a judgment that has not arrived in
    twenty seconds has already cost more than it is worth.
    """
    started = time.monotonic()
    try:
        tsc = client or _client()
        answer = tsc.ask(subject, questions, timeout=timeout, api_key=api_key)
    except Exception as exc:  # deliberately broad: see JudgeUnavailable
        raise JudgeUnavailable(f"{type(exc).__name__}: {exc}") from None
    answer["elapsed_ms"] = int((time.monotonic() - started) * 1000)
    return answer


def read(answer, key, *, yes_at=YES_AT, no_at=NO_AT, min_confidence=MIN_CONFIDENCE):
    """One answer, turned into an act: {"outcome", "escalate", "value", "confidence"}.

    Thin wrapper over the client's own decide(), kept here so a caller imports
    one module rather than two, and so the escalate contract stays identical
    everywhere: escalate true means a person or a larger model looks, and the
    caller does NOT act on the value by itself.
    """
    tsc = _client()
    return tsc.decide(answer["answers"][key], yes_at=yes_at, no_at=no_at,
                      min_confidence=min_confidence)


def record(kind, subject_ref, answer, existing_decision=None, *, note=None,
           log_path=SHADOW_LOG, error=None):
    """Append one shadow observation. Writes what Jev said BESIDE what we did.

    `existing_decision` is what the mechanism in place actually decided, so the
    log answers the only question that matters before switching anything on:
    on real traffic, how often do they disagree, and who was right? Pass it even
    when it is None — a row with no comparison is still evidence about coverage.

    Never raises. A logging failure must not take down the caller it was added
    to observe; that would be the tail wagging the dog.
    """
    row = {
        "at": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "subject_ref": subject_ref,
        "existing_decision": existing_decision,
        "note": note,
    }
    if error is not None:
        row["error"] = str(error)
    else:
        row["model"] = answer.get("model")
        row["usage"] = answer.get("usage")
        row["elapsed_ms"] = answer.get("elapsed_ms")
        row["answers"] = answer.get("answers")
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    except OSError:
        pass
    return row


def agreement(log_path=SHADOW_LOG, kind=None):
    """Read the shadow log back: how often the judgment and the mechanism agree.

    This is the function that decides whether a caller may stop shadowing. It
    reports counts rather than a verdict, because "is this good enough to act
    on" depends on what acting costs and that is never this module's call.
    """
    rows = []
    try:
        with open(log_path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if kind is None or row.get("kind") == kind:
                    rows.append(row)
    except OSError:
        return {"rows": 0, "errors": 0, "comparable": 0, "agreed": 0, "disagreed": 0}
    errors = sum(1 for row in rows if "error" in row)
    comparable = [row for row in rows
                  if "error" not in row and row.get("existing_decision") is not None]
    agreed = sum(1 for row in comparable if row.get("note") == "agreed")
    return {
        "rows": len(rows),
        "errors": errors,
        "comparable": len(comparable),
        "agreed": agreed,
        "disagreed": len(comparable) - agreed,
    }
