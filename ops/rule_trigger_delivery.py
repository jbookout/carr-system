"""rule_trigger_delivery.py — which rules a partner message surfaces.

The run-time half of ops/rule_trigger_compile.py. Jev judged every pack-layer
rule once, when it was taught or changed, and those judgments were compiled
into `prompt_regex` rows of ops/config/rule-jit-triggers.v1.json, beside a few
hand-reviewed rows (structural facts, and cues taken from real logged partner
prompts). A message is matched against them first: deterministic, no Jev call.

TWO KINDS OF PROMPT, TWO POLICIES (2026-09-25 design ruling on #1276 review):

  * MACHINE ENVELOPE (ops/machine_envelope.py: a prompt that is entirely
    complete task-notification / cross-session blocks) — compiled triggers
    only, ZERO Jev calls. The hand-reviewed partner-prompt cues do not run
    here: a notification's own boilerplate carries URLs and "error".

  * HUMAN PROMPT — compiled triggers first, then ONE BUDGETED JUDGMENT over
    every pack-layer rule that did not match. The review replayed 30 real
    human-prompt verdicts and compiled triggers alone kept 3: a partner does
    not speak in the vocabulary a rule is written in, so the judgment is not
    optional for human prompts. The judgment is:
      - stale rules (text changed since compile) are ALWAYS judged, on EVERY
        human prompt — no once-per-session marker, no skipping a pack that
        already had a hit;
      - every other unmatched rule, residual rules included, is ranked by the
        existing ranking Choice (ops/jev_rule_select.rank_question: one
        request over the roster) on every human prompt, and the top
        BIND_TOP_K are judged. An answer that ranks no rule ("none binds") is
        a successful, empty shortlist (rank_status "ok"). When the ranking
        request is unavailable, a deterministic word-overlap pick (prompt
        words against each rule's statement and compiled keywords, ties by
        rule id) chooses the shortlist instead, and it is judged the same way
        (rank_status "unavailable_overlap_fallback");
      - binding is ONE RULE PER REQUEST with the old path's own question.
        Measured 2026-09-25 on the 15 in-capacity misses of the review set:
        batched questions scored them 0.23-0.71, the same rules asked one at a
        time scored 0.76-0.85 on 14 of 15. Batching was the defect.
    HARD BUDGET: MAX_JEV_CALLS = 1 ranking + BIND_TOP_K binding = 8 requests
    per human prompt, enforced here and counted in the log. HARD CLOCK:
    DEADLINE_SECONDS (12 s, under the hook's 20 s timeout); once it is near,
    no further request starts, and the matches plus whatever was judged are
    returned, with deadline_hit and the unjudged rules in the log.

FAIL OPEN, ALWAYS TOWARD DELIVERY. A missing or malformed compiled file or
trigger table means nothing matched: a human prompt is then judged over the
whole pack roster within the same budget (never the old 21-request path);
an envelope delivers nothing and says so in the log. A failed request only
loses what it would have judged; what matched is still delivered.

Rows returned have the shape hooks/rule-pack-preuse-reselection.py's semantic
receipt consumes: {"id", "probability", "ranking_model", "binding_model",
"source"}.

A LIBRARY, NOT A SCRIPT (see ops/typesafe_client.py for the reason).
"""

import importlib.util
import json
import os
import re
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRIGGERS_PATH = os.path.join(REPO, "ops", "config", "rule-jit-triggers.v1.json")
OUT = os.path.join(REPO, "out")
DELIVERED_CACHE = os.path.join(OUT, "rule-prompt-delivered.json")
LOG_PATH = os.path.join(OUT, "rule-trigger-delivery.jsonl")

# A message that surfaces twenty rules has surfaced none; same cap and reason
# as ops/jev_rule_select.MAX_SURFACED.
MAX_SURFACED = 5
# A rule already delivered to this session is in its context; sending the same
# statement again on every message is what the dedupe prevents. Two hours
# bounds how long a compaction could have dropped it.
DEDUPE_TTL_SECONDS = 2 * 3600
BIND_AT = 0.75
MESSAGE_CHARS = 90_000

# THE BUDGET. One ranking request plus at most BIND_TOP_K single-rule binding
# requests. k = 7 is the largest the 8-request cap allows, and the smallest
# the logged ranks of the review set say reaches 80% recall (k = 6: 77%).
BIND_TOP_K = 7
MAX_JEV_CALLS = 1 + BIND_TOP_K
RUBRIC_CHARS = 150

# THE CLOCK. The prompt hook runs under a 20 s timeout (ops/config/hooks.json,
# rule-pack-preuse-reselection.py); a hook killed at the timeout delivers
# nothing at all, not even what the compiled triggers already matched. The
# whole judgment gets DEADLINE_SECONDS from the start of advise(), leaving ~8 s
# for interpreter start, the standing-context door and the receipt. No request
# starts with less than MIN_CALL_SECONDS left, and each default request's own
# timeout is capped at what is left (jev_judge's default is 20 s by itself).
DEADLINE_SECONDS = 12.0
MIN_CALL_SECONDS = 1.0
STATEMENT_CHARS = 4000
SITUATION_CHARS = 20_000

# Hand-reviewed row sources that are facts, not model judgments: stale rule
# text does not invalidate them. prompt_cue rows run on human prompts only.
REVIEWED_SOURCES = ("structural_extra", "prompt_cue")
HUMAN_ONLY_SOURCES = ("prompt_cue",)


def _sibling(name):
    path = os.path.join(REPO, "ops", f"{name}.py")
    spec = importlib.util.spec_from_file_location(f"{name}_for_delivery", path)
    if spec is None or spec.loader is None:  # pragma: no cover - import plumbing
        raise RuntimeError(f"cannot load ops/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prompt_rows(path=TRIGGERS_PATH):
    """The compiled prompt rows, or None when the table is unusable."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            table = json.load(handle)
        rows = [row for row in table.get("triggers", [])
                if isinstance(row, dict) and row.get("kind") == "prompt_regex"]
        for row in rows:
            re.compile(row["pattern"])
            if row.get("negative_pattern"):
                re.compile(row["negative_pattern"])
    except (OSError, ValueError, KeyError, TypeError, re.error):
        return None
    return rows or None


def match(text, rows, *, human=True):
    """{rule_id: [row source, ...]} for every prompt row the text hits.

    A row's negative pattern masks its near-miss phrases out of the text
    before the positive pattern is tried, so "push notification" does not fire
    a rule about `git push` while "push" elsewhere in the same message still
    does. Rows from HUMAN_ONLY_SOURCES are skipped unless `human`."""
    hits = {}
    for row in rows:
        if not human and row.get("source") in HUMAN_ONLY_SOURCES:
            continue
        body = text
        if row.get("negative_pattern"):
            body = re.sub(row["negative_pattern"], " ", body, flags=re.I)
        if re.search(row["pattern"], body, re.I):
            for rule_id in row.get("rule_ids", []):
                hits.setdefault(rule_id, []).append(row.get("source"))
    return hits


def _matched_probability(text, entry):
    """Jev's compile-time probability for the strongest cue present.

    Only reports a number: whether the rule fires was already decided by
    match(), negatives included, so it is not decided a second time here."""
    best = None
    for keyword, prob in (entry.get("triggers") or {}).get("keywords", {}).items():
        escaped = re.escape(keyword).replace(r"\ ", r"\s+")
        if re.search(rf"(?<!\w){escaped}(?!\w)", text, re.I):
            best = prob if best is None else max(best, prob)
    return best


def _log(record, path):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    except OSError:
        pass


def _is_envelope(text):
    try:
        return bool(_sibling("machine_envelope").is_machine_envelope(text))
    except Exception:
        return False  # cannot tell: treat as a human prompt, which is judged


def _default_rank(text, pool, limit, client, timeout=None):
    """(ranked ids, requests made, ranking model) from ONE ranking Choice.

    The same Choice ops/jev_rule_select.narrow asks (rank_question, with its
    none-binds option), read directly rather than through narrow(): narrow()
    answers an outage AND a "no rule binds" answer alike with the whole
    roster, and those are different facts. Here an outage or malformed answer
    raises (the caller falls back and logs "unavailable"), and an answer that
    ranks no rule is an ordinary empty shortlist (logged "ok")."""
    jrs = _sibling("jev_rule_select")
    ranker = jrs._sibling("jev_judge")
    roster = [{"id": rule["id"], "gist": (rule.get("statement") or "")[:RUBRIC_CHARS]}
              for rule in pool]
    try:
        extra = {} if timeout is None else {"timeout": timeout}
        answer = ranker.judge({"situation": text},
                              {"rank": jrs.rank_question(roster, client)}, client=client,
                              **extra)
    except Exception as exc:
        # An outage must leave a row, as narrow() leaves one (2026-09-23
        # audit). record() never raises; the guard is for a stub without it.
        try:
            ranker.record("rule_select", None, None, None, error=exc)
        except Exception:
            pass
        raise
    probabilities = ((answer.get("answers") or {}).get("rank") or {}).get("probabilities")
    if not isinstance(probabilities, dict) or not probabilities:
        raise RuntimeError("ranking answer carried no probabilities")
    known = {row["id"] for row in roster}
    ranked = sorted(((rule_id, float(p)) for rule_id, p in probabilities.items()
                     if rule_id != jrs.NONE_BIND and rule_id in known),
                    key=lambda item: (-item[1], item[0]))
    return [rule_id for rule_id, _ in ranked][:limit], 1, answer.get("model") or "jev"


_WORD = re.compile(r"[a-z][a-z0-9'-]{2,}")


def _overlap_rank(text, pool, limit, keywords):
    """Deterministic fallback when the ranking request is unavailable: rules
    ordered by how many distinct words of the prompt appear in the rule's
    statement or its compiled keywords, ties broken by rule id. Rules with no
    overlap are not picked. No Jev call; the binding requests it feeds are
    the same single-rule requests a ranked shortlist gets."""
    try:
        stop = set(_sibling("rule_trigger_compile").STOPWORDS)
    except Exception:
        stop = set()
    words = {w for w in _WORD.findall(text.lower()) if w not in stop}
    scored = []
    for rule in pool:
        vocabulary = set(_WORD.findall((rule.get("statement") or "").lower()))
        for keyword in keywords.get(rule["id"], ()):
            vocabulary.update(_WORD.findall(keyword.lower()))
        overlap = len(words & vocabulary)
        if overlap:
            scored.append((-overlap, rule["id"]))
    return [rule_id for _, rule_id in sorted(scored)][:limit]


def _binding_question(client):
    """The old path's single-rule question (ops/jev_rule_select), verbatim."""
    return _sibling("jev_rule_select").binding_question(client)


def _default_bind(subject, questions, client, timeout=None):
    """One single-rule binding request through ops/jev_judge (logged there)."""
    extra = {} if timeout is None else {"timeout": timeout}
    return _sibling("jev_rule_select")._sibling("jev_judge").judge(
        subject, questions, client=client, **extra)


def _rule_titles():
    """{rule id: (title, context)} from the old path's corpus loader, so a
    single-rule question carries exactly what it carried before."""
    try:
        return {row["id"]: (row.get("gist") or "", row.get("context") or "")
                for row in _sibling("jev_rule_select").load_rules()}
    except Exception:
        return {}


def judge_budgeted(text, rules, always, *, rank=None, ask=None, client=None, titles=None,
                   keywords=None, deadline=None, clock=time.monotonic):
    """Judge `always` (stale rules) plus the best-ranked of `rules`, one
    single-rule request each, within MAX_JEV_CALLS.

    Returns (selected rows, report). `always` is judged first, up to
    BIND_TOP_K; the ranking call fills what is left and is skipped when
    nothing is left or when the pool already fits. When the ranking request
    is unavailable the binding budget is not wasted: _overlap_rank picks the
    shortlist deterministically and it is judged exactly as a ranked one.

    `deadline` is a `clock()` time (default: DEADLINE_SECONDS from now). Once
    fewer than MIN_CALL_SECONDS remain no further request starts: the rules
    not yet asked are reported in "unjudged", "deadline_hit" is set, and what
    was judged so far is still returned."""
    if deadline is None:
        deadline = clock() + DEADLINE_SECONDS
    by_id = {rule["id"]: rule for rule in rules}
    report = {"calls": 0, "rank_status": "not_needed", "bind_status": "none",
              "judged": [], "overflow": [], "unjudged": [], "deadline_hit": False}
    always = [rule_id for rule_id in always if rule_id in by_id]
    if len(always) > BIND_TOP_K:
        report["overflow"] = always[BIND_TOP_K:]
        always = always[:BIND_TOP_K]
    room = BIND_TOP_K - len(always)
    pool = [rule for rule in rules if rule["id"] not in set(always)]
    ranked = []
    ranking_model = None
    if room > 0 and pool:
        if len(pool) <= room:
            ranked = [rule["id"] for rule in pool]
        elif deadline - clock() < MIN_CALL_SECONDS:
            report["deadline_hit"] = True
            report["rank_status"] = "deadline_overlap_fallback"
            ranked = _overlap_rank(text, pool, room, keywords=keywords or {})
        else:
            try:
                answer = (rank(text, pool, room, client) if rank is not None else
                          _default_rank(text, pool, room, client,
                                        timeout=deadline - clock()))
                ranked, made = answer[0], answer[1]
                ranking_model = answer[2] if len(answer) > 2 else None
                report["calls"] += made
                report["rank_status"] = "ok" if made else "not_needed"
            except Exception:
                report["calls"] += 1
                report["rank_status"] = "unavailable_overlap_fallback"
                ranked = _overlap_rank(text, pool, room, keywords or {})
            ranked = [rule_id for rule_id in ranked if rule_id in by_id][:room]
    to_judge = always + [rule_id for rule_id in ranked if rule_id not in set(always)]
    selected = {}
    if not to_judge:
        return selected, report
    if client is None:
        import sys
        sys.path.insert(0, os.path.join(REPO, "ops"))
        import typesafe_client as client  # noqa: E402
    titles = _rule_titles() if titles is None else titles
    question = {"binds": _binding_question(client)}
    failures = 0
    to_judge = to_judge[:BIND_TOP_K]
    for position, rule_id in enumerate(to_judge):
        left = deadline - clock()
        if left < MIN_CALL_SECONDS:
            report["deadline_hit"] = True
            report["unjudged"] = to_judge[position:]
            break
        report["judged"].append(rule_id)
        rule = by_id[rule_id]
        title, context = titles.get(rule_id, ("", ""))
        statement = (rule.get("statement") or "")[:STATEMENT_CHARS]
        subject = {"situation": text[:SITUATION_CHARS],
                   "rule_title": title or statement[:RUBRIC_CHARS],
                   "rule": statement or title,
                   "rule_context": context}
        report["calls"] += 1
        try:
            answer = (ask(subject, question, rule_id=rule_id) if ask is not None
                      else _default_bind(subject, question, client, timeout=left))
            value = float(answer["answers"]["binds"]["noul"])
        except Exception:
            failures += 1
            continue
        if value >= BIND_AT:
            selected[rule_id] = {
                "id": rule_id, "probability": value,
                "ranking_model": None if rule_id in always else ranking_model,
                "binding_model": answer.get("model") or "jev",
                "source": "stale_judged" if rule_id in always else "ranked_judged"}
    asked = len(report["judged"])
    report["bind_status"] = ("deadline" if not asked else
                             "judged" if not failures else
                             "partial" if failures < asked else "unavailable")
    if report["calls"] > MAX_JEV_CALLS:  # pragma: no cover - guarded by construction
        raise AssertionError("Jev budget exceeded")
    return selected, report


def advise(situation, *, session_id=None, now=None, triggers_path=TRIGGERS_PATH,
           compiled=None, rules=None, ask=None, client=None, rank=None,
           delivered_cache=DELIVERED_CACHE, log_path=LOG_PATH, envelope=None,
           deadline=None):
    """The rules this message surfaces, within the Jev budget and the clock.

    `deadline` is a time.monotonic() value; by default DEADLINE_SECONDS from
    now, so the clock covers matching as well as the judgment."""
    deadline = time.monotonic() + DEADLINE_SECONDS if deadline is None else deadline
    now = time.time() if now is None else now
    text = situation[:MESSAGE_CHARS]
    human = not (_is_envelope(situation) if envelope is None else envelope)
    rtc = _sibling("rule_trigger_compile")
    try:
        compiled = rtc.load_compiled() if compiled is None else compiled
    except Exception:
        compiled = None
    rows = prompt_rows(triggers_path)
    compiled_status = "ok" if compiled is not None and rows is not None else "unavailable"
    rules = rtc.pack_rules() if rules is None else rules
    by_id = {rule["id"]: rule for rule in rules}
    entries = (compiled or {}).get("rules") or {}
    # With no compiled file there is nothing to be stale against: every rule
    # is simply unmatched and the ranking call picks what to judge.
    stale = set(rtc.stale_or_missing(compiled, rules)) if compiled is not None else set()

    selected = {}
    for rule_id, sources in match(text, rows or [], human=human).items():
        entry = entries.get(rule_id)
        if rule_id not in by_id:
            continue
        if bool(entry) and rule_id not in stale and "jev_compiled" in sources:
            prob = _matched_probability(text, entry)
            selected[rule_id] = {"id": rule_id,
                                 "probability": rtc.SURFACE_AT if prob is None else prob,
                                 "ranking_model": None,
                                 "binding_model": entry.get("model"),
                                 "source": "compiled_trigger"}
        elif any(source in REVIEWED_SOURCES for source in sources):
            # A reviewed fact or reviewed partner-prompt cue, not a model
            # judgment: stale rule text does not invalidate it.
            source = next(s for s in REVIEWED_SOURCES if s in sources)
            selected[rule_id] = {"id": rule_id, "probability": 1.0, "ranking_model": None,
                                 "binding_model": ("structural-trigger"
                                                   if source == "structural_extra"
                                                   else "reviewed-prompt-cue"),
                                 "source": source}
    for rule_id, entry in entries.items():
        if entry.get("mode") == "always_on" and rule_id in by_id and rule_id not in stale:
            selected.setdefault(rule_id, {"id": rule_id, "probability": 1.0,
                                          "ranking_model": None,
                                          "binding_model": entry.get("model"),
                                          "source": "always_on"})

    report = {"calls": 0, "rank_status": "envelope", "bind_status": "envelope",
              "judged": [], "overflow": []}
    if human:
        unmatched = [rule for rule in rules if rule["id"] not in selected]
        always = sorted(rule["id"] for rule in unmatched if rule["id"] in stale)
        keywords = {rule_id: list(((entry.get("triggers") or {}).get("keywords") or {}))
                    for rule_id, entry in entries.items() if isinstance(entry, dict)}
        judged, report = judge_budgeted(text, unmatched, always, rank=rank, ask=ask,
                                        client=client, keywords=keywords,
                                        deadline=deadline)
        for rule_id, row in judged.items():
            selected.setdefault(rule_id, row)

    # Do not resend a statement this session already has in context. The
    # session is the hook payload's own id; with none, nothing is deduped.
    session_key = session_id.strip() if isinstance(session_id, str) and session_id.strip() else None
    recent = {}
    cache = None
    if session_key is not None:
        try:
            cache = _sibling("jev_verdict_cache")
            dedupe_key = cache.key({"session": session_key, "delivered": True})
            recent = cache.get(delivered_cache, dedupe_key, ttl=DEDUPE_TTL_SECONDS, now=now) or {}
        except Exception:
            cache = None
    if not isinstance(recent, dict):
        recent = {}
    fresh = {rule_id: row for rule_id, row in selected.items()
             if not (isinstance(recent.get(rule_id), (int, float))
                     and 0 <= now - recent[rule_id] <= DEDUPE_TTL_SECONDS)}
    ordered = sorted(fresh.values(), key=lambda row: (-row["probability"], row["id"]))[:MAX_SURFACED]
    if ordered and cache is not None:
        recent = {rule_id: at for rule_id, at in recent.items()
                  if isinstance(at, (int, float)) and 0 <= now - at <= DEDUPE_TTL_SECONDS}
        recent.update({row["id"]: now for row in ordered})
        cache.put(delivered_cache, dedupe_key, recent, ttl=DEDUPE_TTL_SECONDS, now=now)
    _log({"at": now, "mode": "human" if human else "envelope", "session": session_id,
          "compiled_status": compiled_status, "matched": sorted(selected),
          "jev_calls": report["calls"], "rank_status": report["rank_status"],
          "bind_status": report["bind_status"], "judged": report["judged"],
          "overflow": report["overflow"],
          "deadline_hit": report.get("deadline_hit", False),
          "unjudged": report.get("unjudged", []),
          "delivered": [row["id"] for row in ordered],
          "situation": situation[:300]}, log_path)
    return ordered
