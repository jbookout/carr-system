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
        existing ranking call (ops/jev_rule_select.narrow: one Choice over the
        roster) on every human prompt, and the top BIND_TOP_K are judged;
      - binding is ONE RULE PER REQUEST with the old path's own question.
        Measured 2026-09-25 on the 15 in-capacity misses of the review set:
        batched questions scored them 0.23-0.71, the same rules asked one at a
        time scored 0.76-0.85 on 14 of 15. Batching was the defect.
    HARD BUDGET: MAX_JEV_CALLS = 1 ranking + BIND_TOP_K binding = 8 requests
    per human prompt, enforced here and counted in the log.

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


def _default_rank(text, pool, limit, client):
    """(ranked ids, requests made) from ops/jev_rule_select's ranking Choice."""
    jrs = _sibling("jev_rule_select")
    real_judge = jrs._sibling("jev_judge")
    made = []

    # NOT named `judge` out here: the class body below defines a method of
    # that name, which makes `judge` class-local, so a reference to the outer
    # module from inside the class raised NameError and every ranking failed
    # before reaching Jev (found by the 2026-09-25 live replay; the fake
    # ranker in the selftest hid it, so a test now drives this function).
    class Counting:
        JudgeUnavailable = getattr(real_judge, "JudgeUnavailable", RuntimeError)

        @staticmethod
        def judge(*args, **kwargs):
            made.append(1)
            return real_judge.judge(*args, **kwargs)

        @staticmethod
        def record(*args, **kwargs):
            return real_judge.record(*args, **kwargs)

    roster = [{"id": rule["id"], "gist": (rule.get("statement") or "")[:RUBRIC_CHARS]}
              for rule in pool]
    short = jrs.narrow(text, roster, limit=limit, client=client, judge=Counting)
    if made and not any("ranking_model" in row for row in short):
        # narrow() answers an outage with the whole roster; that is not a
        # ranking, and judging the first `limit` of it would look like one.
        raise RuntimeError("ranking unavailable")
    return [row["id"] for row in short][:limit], len(made)


def _binding_question(client):
    """The old path's single-rule question (ops/jev_rule_select), verbatim."""
    return _sibling("jev_rule_select").binding_question(client)


def _default_bind(subject, questions, client):
    """One single-rule binding request through ops/jev_judge (logged there)."""
    return _sibling("jev_rule_select")._sibling("jev_judge").judge(
        subject, questions, client=client)


def _rule_titles():
    """{rule id: (title, context)} from the old path's corpus loader, so a
    single-rule question carries exactly what it carried before."""
    try:
        return {row["id"]: (row.get("gist") or "", row.get("context") or "")
                for row in _sibling("jev_rule_select").load_rules()}
    except Exception:
        return {}


def judge_budgeted(text, rules, always, *, rank=None, ask=None, client=None, titles=None):
    """Judge `always` (stale rules) plus the best-ranked of `rules`, one
    single-rule request each, within MAX_JEV_CALLS.

    Returns (selected rows, report). `always` is judged first, up to
    BIND_TOP_K; the ranking call fills what is left and is skipped when
    nothing is left or when the pool already fits."""
    by_id = {rule["id"]: rule for rule in rules}
    report = {"calls": 0, "rank_status": "not_needed", "bind_status": "none",
              "judged": [], "overflow": []}
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
        else:
            try:
                ranked, made = (rank or _default_rank)(text, pool, room, client)
                report["calls"] += made
                report["rank_status"] = "ranked" if made else "not_needed"
            except Exception:
                report["calls"] += 1
                report["rank_status"] = "unavailable"
                ranked = []
            ranked = [rule_id for rule_id in ranked if rule_id in by_id][:room]
    to_judge = always + [rule_id for rule_id in ranked if rule_id not in set(always)]
    report["judged"] = to_judge
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
    for rule_id in to_judge[:BIND_TOP_K]:
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
                      else _default_bind(subject, question, client))
            value = float(answer["answers"]["binds"]["noul"])
        except Exception:
            failures += 1
            continue
        if value >= BIND_AT:
            selected[rule_id] = {
                "id": rule_id, "probability": value, "ranking_model": ranking_model,
                "binding_model": answer.get("model") or "jev",
                "source": "stale_judged" if rule_id in always else "ranked_judged"}
    report["bind_status"] = ("judged" if not failures else
                             "partial" if failures < len(to_judge) else "unavailable")
    if report["calls"] > MAX_JEV_CALLS:  # pragma: no cover - guarded by construction
        raise AssertionError("Jev budget exceeded")
    return selected, report


def advise(situation, *, session_id=None, now=None, triggers_path=TRIGGERS_PATH,
           compiled=None, rules=None, ask=None, client=None, rank=None,
           delivered_cache=DELIVERED_CACHE, log_path=LOG_PATH, envelope=None):
    """The rules this message surfaces, within the Jev budget."""
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
        judged, report = judge_budgeted(text, unmatched, always, rank=rank, ask=ask,
                                        client=client)
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
          "delivered": [row["id"] for row in ordered],
          "situation": situation[:300]}, log_path)
    return ordered
