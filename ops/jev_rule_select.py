"""jev_rule_select.py — decide which RULES bind to a moment, by judging them.

THE PROBLEM THIS EXISTS FOR, measured on 2026-09-18 rather than argued.

Rule delivery today is ops/config/rule-jit-triggers.v1.json: a short list of
regexes, each capped at a handful of rules. Counting the distinct ids those
triggers can ever emit against the active rules in
ops/config/rule-triage.v1.json gives the number that motivates this file — a
large majority of active rules have no detector at all and therefore CANNOT
REACH A SESSION, ever. They are counted, versioned, governed and unreachable.
No figure is quoted here: every one of them moves whenever a detector is
written or a rule is retired, and a stale number in a docstring is the
`dated-artifact-read-as-present-state` failure this system logs more than any
other. Call unreachable_rules() for the live count.

That is not a delivery bug. It is the shape of the mechanism. Writing a regex
for "never make a partner decode an id" or "say when you are not sure" is not
hard, it is impossible — there is no token to match on, because the trigger is
a MEANING. Those rules did not get weaker when they were written as prose. They
left the system while still appearing in the rule count.

A judged relevance question is exactly the shape that fits: the moment is the
query, the rules are the candidates, and "does this bind here" is a noul. The
published reranking method applies directly, including its central constraint —
ONE REQUEST PER CANDIDATE, so no rule's score is influenced by its competitors.

WHAT THIS IS NOT, AND THE TRAP THAT PRODUCED THE WARNING. It is tempting to
measure a selector by how well it reproduces what the regexes deliver. Do not.
On 2026-09-18 that comparison was run and read as a failure of the selector,
until somebody read the five rules the `git push` regex actually emits: only
one of them is about pushing. The others are a sizing philosophy, a rule about
where an asset lives, and a build-order discipline, bundled together because
they are all loosely engineering-flavoured. THE EXISTING DELIVERY IS NOT A GOLD
STANDARD, and scoring against it measures agreement with a known-crude bundle.
That is why shadow_selection() records both sets side by side for a human to
read and deliberately computes no accuracy number.

IT IS A LIBRARY AND MUST STAY ONE. No shebang, no main guard — either one makes
a .py file a registered script entrypoint in the sealed source inventory, moves
the frontier, and owes a forward-only registry successor. The detector is a
regex over the whole file with no notion of docstrings, so the construct is
described here and never spelled. ops/typesafe_client.py carries the long form.

SHADOW ONLY. Nothing here removes a rule from delivery or adds one to it. It
writes what it WOULD have surfaced beside what the regexes DID surface, and
returns. A threshold for acting has to be measured on that log against real
moments; the floor below is a placeholder, and the reason it is set high is a
measurement too — on the day this was written, "weekends are off, both humans"
scored 0.73 against a session about to push a branch. Noise at 0.7 is real, and
a selector that surfaces noise trains a session to ignore rules, which is worse
than delivering none.
"""

import importlib.util
import json
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRIAGE = os.path.join(REPO, "ops", "config", "rule-triage.v1.json")
TRIGGERS = os.path.join(REPO, "ops", "config", "rule-jit-triggers.v1.json")
SHADOW_LOG = os.path.join(REPO, "out", "jev-rule-select.jsonl")

# Deliberately high, and deliberately a placeholder. See the docstring: 0.73 was
# observed on a rule that plainly did not bind. Replace this with a number
# measured from SHADOW_LOG on real moments, not with a number that feels right.
BIND_AT = 0.85

# A moment that surfaces twenty rules has surfaced none, because nobody reads
# twenty. The cap is part of the design, not a performance concern.
MAX_SURFACED = 5


def _sibling(name):
    """Load a sibling ops/ module by path. ops/ is not a package, and the whole
    point of these files is that they carry no entrypoint, so there is nothing
    to import them as. Same plumbing jev_judge uses to reach typesafe_client."""
    path = os.path.join(REPO, "ops", f"{name}.py")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - import plumbing
        raise RuntimeError(f"cannot load ops/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_rules(path=TRIAGE):
    """Every active rule as {id, gist, context}. The corpus, not a selection."""
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
    rows = data if isinstance(data, list) else next(
        (value for value in data.values()
         if isinstance(value, list) and value and isinstance(value[0], dict)), [])
    return [{"id": row["id"],
             "gist": row.get("title_gist", ""),
             "context": (row.get("reason") or "")[:600]}
            for row in rows if row.get("id")]


def reachable_rule_ids(path=TRIGGERS):
    """Rule ids some regex can emit. Everything else is unreachable today."""
    with open(path, "r", encoding="utf-8") as handle:
        triggers = json.load(handle)["triggers"]
    reachable = set()
    for trigger in triggers:
        reachable.update(trigger.get("rule_ids", []))
    return reachable


def unreachable_rules(rules=None, reachable=None):
    """The live count behind this file. Never hardcode it — it moves."""
    rules = load_rules() if rules is None else rules
    reachable = reachable_rule_ids() if reachable is None else reachable
    return [rule for rule in rules if rule["id"] not in reachable]


def regex_delivery(command_text, path=TRIGGERS):
    """What the CURRENT mechanism would deliver for a command. For comparison.

    Only the text-matching trigger kinds are reproduced, because those are the
    ones a moment described in prose can be compared against. Verb and path
    triggers key on structured fields this function is not given, and guessing
    them would make the comparison dishonest rather than partial.
    """
    with open(path, "r", encoding="utf-8") as handle:
        triggers = json.load(handle)["triggers"]
    delivered = set()
    for trigger in triggers:
        if trigger.get("kind") not in ("bash_family", "content_regex"):
            continue
        try:
            if re.search(trigger["pattern"], command_text, re.I):
                delivered.update(trigger.get("rule_ids", []))
        except re.error:
            continue
    return delivered


def binding_question(client=None):
    """The one question. Its criteria are the whole contract, so they live here.

    Written so that a rule which is good, active, and simply about a different
    moment reads as FALSE. Without that the answer drifts toward "is this a
    sound rule", which every active rule passes and which selects nothing.
    """
    ts = client or _sibling("typesafe_client")
    return ts.noul(
        "This rule BINDS the moment described in `state.situation`: a session "
        "about to act as described would be violating this rule, or this is "
        "the moment the rule asks the session to do something.",
        true="The rule governs this exact kind of moment, and the described "
             "action either breaks it or is the occasion it names. A session "
             "that had not read this rule could get this moment wrong.",
        false="The rule concerns a different kind of work, a different surface, "
              "or a moment that is not happening here. IT MAY BE AN EXCELLENT "
              "RULE AND STILL NOT BIND NOW — soundness is not the question, "
              "and a rule that binds everywhere binds nothing.")


def select(situation, rules=None, *, floor=BIND_AT, limit=MAX_SURFACED,
           client=None, api_key=None, judge=None):
    """Rank rules by whether they bind to `situation`. One request per rule.

    Returns [{"id", "gist", "probability"}] over the floor, longest odds last,
    capped at `limit`. A rule whose request fails is reported with probability
    None rather than dropped, because a silently missing candidate is
    indistinguishable from one that was judged and rejected.
    """
    rules = load_rules() if rules is None else rules
    judge = judge or _sibling("jev_judge")
    scored = []
    question = {"binds": binding_question(client)}
    for rule in rules:
        subject = {"situation": situation,
                   "rule": rule["gist"],
                   "rule_context": rule["context"]}
        try:
            answer = judge.judge(subject, question, client=client, api_key=api_key)
            probability = float(answer["answers"]["binds"]["noul"])
        except (judge.JudgeUnavailable, KeyError, TypeError, ValueError):
            scored.append({**rule, "probability": None})
            continue
        scored.append({**rule, "probability": probability})
    over = [row for row in scored if row["probability"] is not None
            and row["probability"] >= floor]
    over.sort(key=lambda row: -row["probability"])
    failed = [row for row in scored if row["probability"] is None]
    return over[:limit] + failed


def shadow_selection(situation, command_text, *, log_path=SHADOW_LOG, **kwargs):
    """Record what WOULD be surfaced beside what the regexes DO surface.

    Deliberately reports no accuracy, no precision and no agreement rate. The
    existing delivery is a bundle of five-per-regex, not a labelled answer key,
    and a number comparing the two would be read as a score when it is not one.
    What it records is both sets and the two differences, for a person to read.

    A logging failure never reaches the caller: a shadow observer that can break
    the thing it observes is worse than no observer.
    """
    judged = select(situation, **kwargs)
    would = [row["id"] for row in judged if row.get("probability") is not None]
    did = regex_delivery(command_text)
    record = {
        "situation": situation[:600],
        "command_text": command_text[:400],
        "judged_would_surface": would,
        "regex_did_surface": sorted(did),
        "judged_only": sorted(set(would) - did),
        "regex_only": sorted(did - set(would)),
        "unreachable_today_among_judged": sorted(
            set(would) - reachable_rule_ids()),
        "floor": kwargs.get("floor", BIND_AT),
        "detail": judged,
    }
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    except OSError:
        pass
    return record
