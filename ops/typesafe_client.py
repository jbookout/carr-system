"""typesafe_client.py — the one way CARR calls TypeSafe's Jev decision model.

WHAT JEV IS, because the shape decides how this file is written. Jev takes text
plus typed questions and returns calibrated probabilities. It does not write
text, produce code, or explain itself. Three question types exist and nothing
else: a yes/no probability (noul), a pick-one over a defined set (choice), and
a position on ordered levels (score). Code owns the workflow; the model supplies
the semantic judgment where ordinary code has none.

THIS FILE IS A LIBRARY ON PURPOSE. It carries no shebang and no main guard, and
it must never gain either. A .py file with either becomes a registered script
entrypoint in the sealed source inventory — see isScriptEntrypoint() in
ops/scac-mutation-inventory.mjs — which moves the frontier and owes a
forward-only registry successor. Callers are entrypoints that already exist.
Adding a shebang here turns a one-line import into a multi-file sealing cascade.

That detector is a REGEX OVER THE WHOLE FILE and does not know what a docstring
is, so writing the guard construct out even as an example — in prose, in a
comment, inside backticks — is enough to seal this file. The first draft of
this paragraph did exactly that and ops/typesafe-client-selftest.py caught it.
Describe the construct; never spell it.

FOUR THINGS THE VENDOR'S OWN DOCS SAY THAT THIS FILE ENFORCES RATHER THAN
TRUSTS A CALLER TO REMEMBER:

  1. ASK TOGETHER. Jev ingests the state once and evaluates every question
     against it in parallel. The published parallel-questions measurement put
     one batched request at 12.2x cheaper and 10.0x faster than the same
     questions sent separately, with no change in the answers. So `ask` takes a
     MAP of questions, never one, and there is deliberately no single-question
     helper to reach for by accident.

  2. KEEP ARITHMETIC IN CODE. jev-1.13 reads dates as text rather than as
     ordered quantities, and does not count reliably. Asking it which of two
     dates is earlier, how far apart they are, or how many items match is
     documented as unreliable. Extraction is a judgment and belongs here;
     comparison, duration, and totals are not and belong in the caller.

  3. TYPED IS NOT TRUE. The response schema guarantees the SHAPE of an answer,
     never its correctness — "cannot hallucinate" means it cannot emit an
     illegal type, and it can still pick the wrong option. Every caller needs a
     threshold and a fallback, which is why `decide` exists beside `ask` and
     returns an explicit escalate outcome rather than a bare value.

  4. FILTER FIRST. Accuracy falls as the state grows with content unrelated to
     the decision, and there is a hard ceiling besides. Retrieve and narrow in
     code, then send only the fields the question needs.

CREDENTIAL. Read from ~/.config/carr/typesafe.env, mode 600, outside the repo,
recorded by name in secrets-inventory.md. The value is never logged, never
echoed into an exception, and never written to a receipt. On an HTTP error this
module reports the status and a truncated response body, and deliberately does
not report the request it sent, because the request carries the bearer token.

AUTHORITY. Joe ruled on 2026-09-17 that CARR records, client and deal material
included, may be sent to third-party model APIs and admitted this vendor on that
basis. Network reachability is separate and already granted: both hosts sit in
KNOWN_HOSTS in hooks/guard-unattended.py.
"""

import json
import os
import time
import urllib.error
import urllib.request

ENDPOINT = "https://api.typesafe.ai/v1/systemone"
KEY_PATH = os.path.expanduser("~/.config/carr/typesafe.env")
KEY_NAME = "TYPESAFE_API_KEY"

# jev-latest is an alias and MOVES when a release ships, so answers can change
# with no change on our side. A caller that has tuned thresholds against a
# specific version should pass that version's exact id instead and step forward
# deliberately. The response reports which version actually answered; `ask`
# returns it untouched under "model" so a receipt can record it.
DEFAULT_MODEL = "jev-latest"

# The documented ceiling is 64k tokens per request for state plus every
# question, and 32k for state plus the single longest question. This is a
# CHARACTER guard against the tighter of those, at a deliberately pessimistic
# 3 characters per token, and it is an ESTIMATE rather than a token count: it
# exists so an oversized state fails here, named and local, instead of as an
# opaque 422 from the service. Narrow the state in code rather than raising it.
STATE_BUDGET_CHARS = 32_000 * 3

TIMEOUT_SECONDS = 60.0
RATE_LIMIT_RETRIES = 3


class TypeSafeError(RuntimeError):
    """Any failure reaching or being understood by the service."""


def read_api_key(path=KEY_PATH):
    """The bearer token, from the 600-mode env file. Never logged."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError as err:
        raise TypeSafeError(
            f"cannot read the TypeSafe credential at {path}: {err.strerror}. "
            "Joe creates it by hand; no agent holds the value."
        ) from None
    for line in lines:
        line = line.strip()
        if line.startswith(f"{KEY_NAME}="):
            value = line.split("=", 1)[1].strip()
            if not value:
                raise TypeSafeError(f"{path}: {KEY_NAME} is present but empty")
            return value
    raise TypeSafeError(f"{path}: no {KEY_NAME} line")


def noul(instructions, true=None, false=None):
    """A yes/no question. Returns the probability that the answer is yes.

    There is NO separate confidence on a noul: the probability is the whole
    answer, and 0.5 means the model finds yes and no about equally likely
    rather than meaning medium intensity. Use one noul per label when several
    labels could apply at once.

    Write `true` and `false` so they agree with `instructions`. A noul whose
    true side describes a no reads as contradictory and measurably degrades.
    """
    question = {"type": "noul", "instructions": instructions}
    criteria = {}
    if true is not None:
        criteria["true"] = true
    if false is not None:
        criteria["false"] = false
    if criteria:
        question["criteria"] = criteria
    return question


def choice(instructions, options):
    """Pick one option from a defined set. `options` maps option -> rubric.

    Include an explicit no-match option wherever nothing may fit. The model
    cannot choose a value that was never offered, so for source-value selection
    check that the candidates actually cover the answer before blaming the
    judgment.
    """
    if not isinstance(options, dict) or len(options) < 2:
        raise TypeSafeError("a choice needs a mapping of at least two options")
    return {"type": "choice", "instructions": instructions, "criteria": dict(options)}


def score(instructions, levels):
    """Rate against ordered levels, lowest first.

    Each level must describe a concrete situation and stand on its own. The
    returned value is probability-weighted across the levels, so it is useful
    for comparing against a threshold and NOT for reconstructing an exact
    number by interpolating between levels — the docs call that out directly.
    """
    levels = list(levels)
    if len(levels) < 2:
        raise TypeSafeError("a score needs at least two ordered levels")
    return {"type": "score", "instructions": instructions, "criteria": levels}


def ask(state, questions, *, model=DEFAULT_MODEL, timeout=TIMEOUT_SECONDS,
        api_key=None, retries=RATE_LIMIT_RETRIES, endpoint=ENDPOINT, opener=None):
    """Evaluate `state` against a map of questions in ONE request.

    `state` is a string, or a mapping when the context has several parts —
    prefer named fields, and reference them from an instruction with backticked
    paths such as `ticket.messages[0].text`. `questions` maps a caller-chosen
    id to a question built by noul/choice/score; the ids come back unchanged and
    are never sent to the model, so the instruction must carry its full meaning.

    Returns the decoded response: {"model": ..., "answers": {...},
    "usage": {...}}. `opener` is for the offline selftest and is not used in
    production.
    """
    if not isinstance(questions, dict) or not questions:
        raise TypeSafeError("ask needs a non-empty map of questions")

    payload = {"state": state, "model": model, "questions": questions}
    body = json.dumps(payload).encode("utf-8")

    state_chars = len(json.dumps(state))
    if state_chars > STATE_BUDGET_CHARS:
        raise TypeSafeError(
            f"state is roughly {state_chars} characters, over this module's "
            f"{STATE_BUDGET_CHARS} guard. Narrow it in code: accuracy falls as "
            "a state fills with detail unrelated to the decision, so trimming "
            "is the fix rather than raising the guard."
        )

    request = urllib.request.Request(
        endpoint, data=body, method="POST",
        headers={
            "Authorization": f"Bearer {api_key or read_api_key()}",
            "Content-Type": "application/json",
        },
    )

    send = opener or urllib.request.urlopen
    attempt = 0
    while True:
        try:
            with send(request, timeout=timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as err:
            # 429 is documented as expected under load, and the service's own
            # limits "can change without notice". Honour retry-after when it is
            # sent; fall back to a short backoff when it is not.
            if err.code == 429 and attempt < retries:
                wait = err.headers.get("retry-after") if err.headers else None
                try:
                    delay = float(wait)
                except (TypeError, ValueError):
                    delay = 2.0 * (attempt + 1)
                time.sleep(delay)
                attempt += 1
                continue
            # Report the status and what came back. NEVER report the request:
            # it carries the bearer token in a header.
            detail = ""
            try:
                detail = err.read().decode("utf-8", "replace")[:400]
            except Exception:
                pass
            raise TypeSafeError(f"TypeSafe returned HTTP {err.code}: {detail}") from None
        except urllib.error.URLError as err:
            raise TypeSafeError(
                f"could not reach {endpoint}: {err.reason}. If this is a refusal "
                "rather than a network fault, check that the host is still in "
                "KNOWN_HOSTS in hooks/guard-unattended.py."
            ) from None


def decide(answer, *, yes_at=0.8, no_at=0.2, min_confidence=0.6):
    """Turn one raw answer into an act: accept it, or send it to someone else.

    Returns {"outcome": "yes"|"no"|"value", "escalate": bool, "value": ...,
    "confidence": float|None}. `escalate` true means the caller routes this case
    to a person or to a reasoning model rather than acting on it — the pattern
    every published build used, and the reason a typed answer is safe to
    automate at all.

    THE DEFAULTS ARE PLACEHOLDERS, NOT FINDINGS. Thresholds have to be measured
    on CARR's own data and against the cost of being wrong in that specific
    place, and a threshold that suits gate precision will not suit a client
    document. Treat any number here as a starting point to replace.

    Two cautions the docs make explicitly. A noul near 0.5 means yes and no are
    about equally likely, not that the answer is moderately true. And for a
    choice or score, confidence summarises how concentrated the distribution
    is — it is not a probability that the workflow is correct, and low
    confidence across several equally acceptable options need not invalidate a
    harmless preference.
    """
    kind = answer.get("type")
    if kind == "noul":
        probability = float(answer["noul"])
        if probability >= yes_at:
            return {"outcome": "yes", "escalate": False,
                    "value": probability, "confidence": None}
        if probability <= no_at:
            return {"outcome": "no", "escalate": False,
                    "value": probability, "confidence": None}
        return {"outcome": "no", "escalate": True,
                "value": probability, "confidence": None}
    if kind in ("choice", "score"):
        confidence = answer.get("confidence")
        value = answer.get("choice") if kind == "choice" else answer.get("score")
        return {
            "outcome": "value",
            "escalate": confidence is None or float(confidence) < min_confidence,
            "value": value,
            "confidence": None if confidence is None else float(confidence),
        }
    raise TypeSafeError(f"unknown answer type: {kind!r}")
