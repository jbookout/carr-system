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

THE CRITERION EARNED ITS SHAPE, AND THE FIRST VERSION FAILED IN A WAY THAT
LOOKED LIKE SUCCESS. Scored against real shell moments sampled from a session
transcript, the first `false` criterion produced exactly ONE rule across twelve
different commands — "run the command, do not hand the partner a command to
paste" — at a confident probability every time. It was matching the rule's
TOPIC (both are about running commands) while its actual condition (the session
is handing a command over instead of running it) was not merely unmet but
inverted: the session was already complying. A distribution that concentrates
on one rule is the tell, and it is invisible if you only read the top hit of a
single moment. So the criterion now names already-complying as the boundary
case, and the three-case check it has to keep passing lives in the suite: fires
on the moment it governs, silent on a plain read-only command, and fires on the
inverse moment only when the condition is genuinely met.

TWO ENTRY POINTS, AND THE CHOICE BETWEEN THEM IS NOT CAUTION. advise() is live:
it returns the rules a session should be shown. shadow_selection() records what
it WOULD surface beside what the regexes DO, and decides nothing.

Shadow is the right default for a mechanism that BLOCKS, where a false positive
costs a partner their afternoon. This one blocks nothing: it adds rules to what
a session reads, and a false positive costs one irrelevant paragraph. The
incumbent already pays that cost at a worse rate — see the `git push` bundle
above, one useful rule in five. Holding a better selector in shadow to avoid
noise, while a noisier mechanism runs live, is caution pointed backwards.

THE REAL CONSTRAINT IS LATENCY, NOT CORRECTNESS, and it decides where this runs
rather than whether. One request per rule over the whole corpus is seconds, not
milliseconds: fine once at the end of a turn, prohibitive in front of every
shell call. The accuracy measurement points the same way — across twenty
sampled real moments, every rule clearing the floor did so on a MESSAGE being
composed for a partner and none on a read-only command, correctly, because no
rule binds to a grep. So message composition and completion claims are the
home, and the per-command path stays with the regexes, which are free and
precise on literal tokens. Both entry points log, because a live mechanism
nobody can audit afterwards is worse than a shadow one.
"""

import concurrent.futures as cf
import importlib.util
import json
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORPUS = os.path.join(REPO, "ops", "config", "rule-selection-corpus.v1.json")
TRIAGE = os.path.join(REPO, "ops", "config", "rule-triage.v1.json")
TRIGGERS = os.path.join(REPO, "ops", "config", "rule-jit-triggers.v1.json")
SHADOW_LOG = os.path.join(REPO, "out", "jev-rule-select.jsonl")

# MEASURED, not guessed, and the measurement is worth keeping because the first
# guess was wrong in the unexpected direction. Twenty real moments were sampled
# from a session transcript and scored against the whole corpus. At 0.85 only
# one moment in twenty drew any rule at all — too strict to be useful. At 0.75
# every hit was correct on a hand read, no single rule dominated, and the rules
# that surfaced were overwhelmingly ones no regex can reach. Below 0.70 the
# distribution starts including rules whose topic matches and whose condition
# does not. Re-derive this from SHADOW_LOG as real traffic accumulates.
BIND_AT = 0.75

# A moment that surfaces twenty rules has surfaced none, because nobody reads
# twenty. The cap is part of the design, not a performance concern.
MAX_SURFACED = 5

# The corpus asked serially took over a minute live. These requests are
# independent by construction, so the only cost of asking them at once is the
# vendor's rate limit, which the client already backs off from.
WORKERS = 16

# THE CHEAP RANKING PASS. A Choice carrying every rule, which narrows 211 to
# this many before any rule is judged on its own. The vendor's own shape for
# picking from a roster: a cheap ranking over everything, then a close look at
# a few. Their skill selector ranks 182 candidates this way and then examines
# three; measured on defect classes here, one Choice over the roster beat one
# Noul per candidate on accuracy AND cost, 69% top-1 against 38%.
#
# WHY THE CLOSE LOOK SURVIVES HERE AND NOT THERE. Defect classes compete for
# one slot, so the Choice IS the answer. Rules do not: several can bind to one
# moment and usually none do, which is independently true or false per rule and
# therefore a Noul each. So the Choice narrows and the Nouls decide — and the
# Nouls now run over a shortlist, which is the only place the reranking rule
# ever applied.
SHORTLIST = 20

# A Choice carries at most 255 options; one slot is kept for the escape.
MAX_OPTIONS = 254

# Rank on short text, judge on full text. 254 full-length rubrics returns
# HTTP 400 max_tokens_exceeded.
RUBRIC_CHARS = 150

# Without an explicit way to decline, a Choice must return a rule for every
# moment — and MOST MOMENTS BIND NO RULE AT ALL. This option is what lets the
# ranking say so, and it is the same failure the binding question already
# guards against: a rule that matches the topic while its condition is unmet.
NONE_BIND = "no rule here binds to this moment"


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
    # THE RULE, NOT ITS HEADLINE. title_gist is a TITLE -- median 87
    # characters, and all 211 end without terminal punctuation because a title
    # has no sentence to end -- and `reason` is triage metadata about WHERE a
    # rule is delivered, not what it says. Judging relevance from those two is
    # judging a filing label. Measured 2026-09-18: the rule that says measure
    # against origin rather than HEAD before naming who is blocking whom scored
    # 0.39 on a moment its own condition covers, because the 109 characters the
    # model saw ended on a dangling "or" and never reached the instruction.
    #
    # ops/config/rule-selection-corpus.v1.json carries the real statements from
    # v_compiled_rules, 211 of them averaging 1176 characters. It is preferred
    # when present and the triage file remains the fallback, so a missing or
    # stale corpus degrades to the old behaviour rather than to nothing.
    statements = {}
    try:
        with open(CORPUS, "r", encoding="utf-8") as handle:
            for row in json.load(handle).get("rules", []):
                if row.get("id") and (row.get("statement") or "").strip():
                    statements[row["id"]] = row["statement"]
    except (OSError, ValueError):
        statements = {}
    return [{"id": row["id"],
             "gist": row.get("title_gist", ""),
             "statement": statements.get(row["id"], ""),
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
        "This rule BINDS the moment described in `state.situation`: its own "
        "condition is MET right now — the thing it forbids is about to happen, "
        "or the thing it requires has not been done.",
        true="The rule's condition is satisfied by this exact moment. A session "
             "that had not read this rule would get THIS moment wrong.",
        false="Either the rule concerns different work entirely, OR — and this "
              "is the case that is easy to get wrong — the rule is ABOUT this "
              "kind of action but its condition is NOT met: the session is "
              "ALREADY DOING what the rule requires, or the circumstance the "
              "rule names is absent. A rule the session already complies with "
              "does NOT bind. TOPIC OVERLAP IS NOT BINDING. IT MAY BE AN "
              "EXCELLENT RULE AND STILL NOT BIND NOW — soundness is not the "
              "question, and a rule that binds everywhere binds nothing.")


def rank_question(rules, client=None):
    """The cheap pass: one Choice carrying every rule, ranked in one request.

    This does NOT decide what binds — it decides what is worth asking about.
    The none-binds option is why it can be trusted to narrow rather than to
    invent: most moments bind no rule, and an option that says so keeps the
    ranking honest about a roster full of rules that have nothing to do with
    the moment in hand.
    """
    tsc = client or _sibling("typesafe_client")
    options = {rule["id"]: (rule.get("gist") or "")[:RUBRIC_CHARS]
               for rule in rules[:MAX_OPTIONS]}
    options[NONE_BIND] = (
        "None of the rules listed binds to this moment. Choose this when the "
        "others are merely ABOUT this kind of work rather than triggered by it "
        "— including a rule the session is ALREADY COMPLYING WITH, which does "
        "not bind. Most moments bind no rule at all, so this is the common "
        "answer and not a failure to find one.")
    return tsc.choice(
        "The moment a session is in is described in `state.situation`. Which of "
        "these standing rules is most likely to BIND to it — to change what the "
        "session should do right now? Topic overlap is not binding.", options)


def narrow(situation, rules, *, limit=SHORTLIST, client=None, api_key=None,
           judge=None):
    """The rules worth judging one at a time, from one cheap ranking request.

    Returns the shortlist, or the whole roster when the ranking is unavailable
    — falling back to judging everything is slower and more expensive but not
    wrong, and it is what this module did before the ranking pass existed.
    """
    if len(rules) <= limit:
        return list(rules)
    judge = judge or _sibling("jev_judge")
    try:
        answer = judge.judge({"situation": situation},
                             {"rank": rank_question(rules, client)},
                             client=client, api_key=api_key)
        probabilities = answer["answers"]["rank"].get("probabilities") or {}
    except Exception:
        return list(rules)
    if not probabilities:
        return list(rules)
    by_id = {rule["id"]: rule for rule in rules}
    ranked = sorted(((rule_id, float(p)) for rule_id, p in probabilities.items()
                     if rule_id != NONE_BIND and rule_id in by_id),
                    key=lambda item: (-item[1], item[0]))
    return [by_id[rule_id] for rule_id, _ in ranked[:limit]] or list(rules)


def select(situation, rules=None, *, floor=BIND_AT, limit=MAX_SURFACED,
           client=None, api_key=None, judge=None, workers=WORKERS,
           shortlist=SHORTLIST):
    """Rank rules by whether they bind to `situation`.

    TWO STAGES. One Choice over the whole roster narrows it to a shortlist,
    then one Noul per shortlisted rule decides independently whether it binds —
    because several rules can bind to one moment and usually none do, which a
    single Choice cannot express. That is the only shape the one-request-per-
    candidate rule ever governed: a shortlist a cheap pass produced first.

    The first version judged all 211 rules one at a time. That cost ten times
    the requests for the same answer.

    Returns [{"id", "gist", "probability"}] over the floor, longest odds last,
    capped at `limit`. A rule whose request fails is reported with probability
    None rather than dropped, because a silently missing candidate is
    indistinguishable from one that was judged and rejected.
    """
    rules = load_rules() if rules is None else rules
    judge = judge or _sibling("jev_judge")
    rules = narrow(situation, rules, limit=shortlist, client=client,
                   api_key=api_key, judge=judge)
    question = {"binds": binding_question(client)}

    def score(rule):
        # THE STATEMENT IS THE RULE. `gist` stays as the headline because a
        # named thing is easier to judge with a name attached, but the text
        # the question is actually answered against is the statement, and
        # before 2026-09-18 it was never sent at all. Falls back to the
        # headline when the corpus has no statement for this id, so a rule
        # added since the last corpus refresh is judged on less rather than
        # skipped.
        subject = {"situation": situation,
                   "rule_title": rule["gist"],
                   "rule": rule.get("statement") or rule["gist"],
                   "rule_context": rule.get("context", "")}
        try:
            answer = judge.judge(subject, question, client=client, api_key=api_key)
            return {**rule, "probability": float(answer["answers"]["binds"]["noul"])}
        except (judge.JudgeUnavailable, KeyError, TypeError, ValueError):
            return {**rule, "probability": None}

    # CONCURRENT ON PURPOSE, AND THE REASON IS A MEASUREMENT. One request per
    # rule is the method, but the whole corpus asked serially took well over a
    # minute on the first live run — and this module's stated home is a hook
    # that sits in somebody's way. The requests are independent by construction
    # (no rule's state carries another's), so there is nothing to serialise.
    # The work is entirely network wait, so threads are the right tool.
    if workers > 1 and len(rules) > 1:
        with cf.ThreadPoolExecutor(max_workers=workers) as pool:
            scored = list(pool.map(score, rules))
    else:
        scored = [score(rule) for rule in rules]
    over = [row for row in scored if row["probability"] is not None
            and row["probability"] >= floor]
    over.sort(key=lambda row: -row["probability"])
    failed = [row for row in scored if row["probability"] is None]
    return over[:limit] + failed


def advise(situation, *, log_path=SHADOW_LOG, **kwargs):
    """The LIVE path: the rules that bind this moment, to be shown to a session.

    Not shadow, and the distinction is deliberate. Shadow is the right default
    for something that BLOCKS, because a false positive costs a partner their
    afternoon. This blocks nothing — it adds rules to what a session is shown,
    and a false positive costs one irrelevant paragraph. The incumbent already
    pays that cost at a worse rate: the `git push` trigger delivers five rules
    of which one is about pushing. Holding a better selector in shadow to avoid
    noise, while a noisier mechanism runs live, is caution pointed backwards.

    WHERE THIS BELONGS, AND THE ONE REAL CONSTRAINT. Scoring the whole corpus
    costs one request per rule. Measured on real traffic that is seconds, not
    milliseconds, which is fine once at the end of a turn and prohibitive in
    front of every shell call. The measurement says the same thing from the
    accuracy side: across twenty sampled moments, every rule that cleared the
    floor did so on a MESSAGE being composed for a partner, and none on a
    read-only command — correctly, because no rule binds to a grep. So the home
    for this is message composition and completion claims. The per-command path
    stays with the regexes, which are free and precise on literal tokens.

    Still logs. A live mechanism that cannot be audited later is worse than a
    shadow one, and the log is how BIND_AT gets re-derived from real traffic.
    """
    surfaced = select(situation, **kwargs)
    advice = [row for row in surfaced if row.get("probability") is not None]
    _append(log_path, {
        "mode": "live",
        "situation": situation[:600],
        "surfaced": [row["id"] for row in advice],
        "unreachable_by_regex": sorted(
            {row["id"] for row in advice} - reachable_rule_ids()),
        "floor": kwargs.get("floor", BIND_AT),
        "detail": advice,
    })
    return advice


def _append(log_path, record):
    """Append one observation. A logging failure NEVER reaches the caller: an
    observer that can break the thing it observes is worse than no observer."""
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    except OSError:
        pass


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
    _append(log_path, record)
    return record
