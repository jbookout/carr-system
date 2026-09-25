"""rule_trigger_delivery.py — which rules a partner message surfaces, by match.

The run-time half of ops/rule_trigger_compile.py. Jev judged every pack-layer
rule once, when it was taught or changed, and those judgments were compiled
into `prompt_regex` rows of ops/config/rule-jit-triggers.v1.json. Here a
message is matched against them: a pure, deterministic step with no Jev call.

JEV IS STILL ASKED IN EXACTLY ONE NARROW CASE. A RESIDUAL rule — one Jev said
no surface cue reliably signals, or one whose text changed since it was
compiled — cannot be matched. Those are judged against the message, in ONE
request covering every such rule still pending for the session, and only for
packs that had no trigger hit on this message. Each (session, pack) is judged
at most once; the marker is kept in out/rule-residual-checks.json.

FAIL OPEN, ALWAYS TOWARD DELIVERY. A missing or malformed compiled file or
trigger table hands the message to the previous judged path
(ops/jev_rule_select.advise) rather than delivering nothing. A residual
request that fails is not marked done, so the next message asks again. A
cache that cannot be read or written only costs a repeat.

Rows returned have the shape hooks/rule-pack-preuse-reselection.py's semantic
receipt consumes: {"id", "probability", "ranking_model", "binding_model",
"source"}. For a trigger hit, `probability` is Jev's compile-time judgment of
the matched cue and `binding_model` the model that made it.

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
RESIDUAL_CACHE = os.path.join(OUT, "rule-residual-checks.json")
DELIVERED_CACHE = os.path.join(OUT, "rule-prompt-delivered.json")
LOG_PATH = os.path.join(OUT, "rule-trigger-delivery.jsonl")

# A message that surfaces twenty rules has surfaced none; same cap and reason
# as ops/jev_rule_select.MAX_SURFACED.
MAX_SURFACED = 5
# "Once per session": sessions rarely outlive this, and a marker older than it
# is safer re-asked than trusted.
RESIDUAL_TTL_SECONDS = 12 * 3600
# A rule already delivered to this session is in its context; sending the same
# statement again on every message is what the dedupe prevents. Two hours
# bounds how long a compaction could have dropped it.
DEDUPE_TTL_SECONDS = 2 * 3600
BIND_AT = 0.75
MESSAGE_CHARS = 90_000


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


def match(text, rows):
    """{rule_id: [row source, ...]} for every prompt row the text hits.

    A row's negative pattern masks its near-miss phrases out of the text
    before the positive pattern is tried, so "push notification" does not fire
    a rule about `git push` while "push" elsewhere in the same message still
    does."""
    hits = {}
    for row in rows:
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
    body = text
    for keyword, prob in (entry.get("triggers") or {}).get("keywords", {}).items():
        escaped = re.escape(keyword).replace(r"\ ", r"\s+")
        if re.search(rf"(?<!\w){escaped}(?!\w)", body, re.I):
            best = prob if best is None else max(best, prob)
    return best


def _residual_question(rule_id, client):
    return client.noul(
        f"The rule in `rules.{rule_id}` BINDS the moment described in "
        "`situation`: its own condition is MET right now — the thing it forbids "
        "is about to happen, or the thing it requires has not been done.",
        true="The rule's condition is satisfied by this exact moment.",
        false="The rule concerns different work, OR it is about this kind of "
              "action but its condition is not met — including a rule the "
              "session is already complying with. Topic overlap is not binding.")


def _log(record, path):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    except OSError:
        pass


def advise(situation, *, session_id=None, now=None, triggers_path=TRIGGERS_PATH,
           compiled=None, rules=None, ask=None, client=None, fallback=None,
           residual_cache=RESIDUAL_CACHE, delivered_cache=DELIVERED_CACHE,
           log_path=LOG_PATH):
    """The rules this message surfaces. Jev is asked only for residual rules."""
    now = time.time() if now is None else now
    rtc = _sibling("rule_trigger_compile")
    compiled = rtc.load_compiled() if compiled is None else compiled
    rows = prompt_rows(triggers_path)
    if compiled is None or rows is None:
        # Fail open to the judged path, never to silence.
        _log({"at": now, "mode": "fallback_judged", "session": session_id}, log_path)
        if fallback is not None:
            return fallback(situation)
        return _sibling("jev_rule_select").advise(situation, session_id=session_id)
    text = situation[:MESSAGE_CHARS]
    rules = rtc.pack_rules() if rules is None else rules
    by_id = {rule["id"]: rule for rule in rules}
    stale = set(rtc.stale_or_missing(compiled, rules))
    entries = compiled.get("rules") or {}

    selected = {}
    for rule_id, sources in match(text, rows).items():
        entry = entries.get(rule_id)
        if rule_id not in by_id:
            continue
        fresh_compile = bool(entry) and rule_id not in stale and "jev_compiled" in sources
        if fresh_compile:
            prob = _matched_probability(text, entry)
            selected[rule_id] = {"id": rule_id,
                                 "probability": rtc.SURFACE_AT if prob is None else prob,
                                 "ranking_model": None,
                                 "binding_model": entry.get("model"),
                                 "source": "compiled_trigger"}
        elif "structural_extra" in sources:
            # A reviewed structural fact (rule-jit-compile's
            # STRUCTURAL_EXTRA_TRIGGERS), not a judgment: stale text does not
            # invalidate it and no model made it.
            selected[rule_id] = {"id": rule_id, "probability": 1.0, "ranking_model": None,
                                 "binding_model": "structural-trigger",
                                 "source": "structural_trigger"}
    for rule_id, entry in entries.items():
        if entry.get("mode") == "always_on" and rule_id in by_id and rule_id not in stale:
            selected.setdefault(rule_id, {"id": rule_id, "probability": 1.0,
                                          "ranking_model": None,
                                          "binding_model": entry.get("model"),
                                          "source": "always_on"})

    # Residual: rules no cue can signal, plus rules not yet compiled against
    # their current text, in packs this message did not already hit.
    hit_packs = {pack for rule_id in selected for pack in by_id[rule_id]["packs"]}
    residual = sorted(rule_id for rule_id in by_id
                      if (rule_id in stale or (entries.get(rule_id) or {}).get("mode") == "residual")
                      and not set(by_id[rule_id]["packs"]) & hit_packs)
    cache = _sibling("jev_verdict_cache")
    # The hook payload's own session id, or nothing. With no session there is
    # no "once per session" to keep and no context to dedupe against, so no
    # marker is read or written — never a shared default key two sessions
    # could pool under.
    session_key = session_id.strip() if isinstance(session_id, str) and session_id.strip() else None

    def marker(pack):
        return cache.key({"session": session_key, "pack": pack})

    pending_packs = sorted({pack for rule_id in residual for pack in by_id[rule_id]["packs"]
                            if session_key is None
                            or cache.get(residual_cache, marker(pack), ttl=RESIDUAL_TTL_SECONDS,
                                         now=now) is None})
    pending = [rule_id for rule_id in residual
               if set(by_id[rule_id]["packs"]) & set(pending_packs)]
    residual_status = "none"
    if pending:
        try:
            if ask is None or client is None:
                import sys
                sys.path.insert(0, os.path.join(REPO, "ops"))
                import typesafe_client as tsc  # noqa: E402
                ask = ask or tsc.ask
                client = client or tsc
            state = {"situation": text[:20_000],
                     "rules": {rule_id: by_id[rule_id]["statement"][:4000] for rule_id in pending}}
            questions = {rule_id: _residual_question(rule_id, client) for rule_id in pending}
            answer = ask(state, questions)
            answers = answer.get("answers") or {}
            for rule_id in pending:
                value = (answers.get(rule_id) or {}).get("noul")
                if isinstance(value, (int, float)) and value >= BIND_AT:
                    selected[rule_id] = {"id": rule_id, "probability": float(value),
                                         "ranking_model": None,
                                         "binding_model": answer.get("model") or "jev",
                                         "source": "residual_judged"}
            if session_key is not None:
                for pack in pending_packs:
                    cache.put(residual_cache, marker(pack), True,
                              ttl=RESIDUAL_TTL_SECONDS, now=now)
            residual_status = "judged"
        except Exception:
            residual_status = "unavailable_retry_next_message"

    # Do not resend a statement this session already has in context.
    dedupe_key = cache.key({"session": session_key, "delivered": True})
    recent = ((cache.get(delivered_cache, dedupe_key, ttl=DEDUPE_TTL_SECONDS, now=now) or {})
              if session_key is not None else {})
    if not isinstance(recent, dict):
        recent = {}
    fresh = {rule_id: row for rule_id, row in selected.items()
             if not (isinstance(recent.get(rule_id), (int, float))
                     and 0 <= now - recent[rule_id] <= DEDUPE_TTL_SECONDS)}
    ordered = sorted(fresh.values(), key=lambda row: (-row["probability"], row["id"]))[:MAX_SURFACED]
    if ordered and session_key is not None:
        recent = {rule_id: at for rule_id, at in recent.items()
                  if isinstance(at, (int, float)) and 0 <= now - at <= DEDUPE_TTL_SECONDS}
        recent.update({row["id"]: now for row in ordered})
        cache.put(delivered_cache, dedupe_key, recent, ttl=DEDUPE_TTL_SECONDS, now=now)
    _log({"at": now, "mode": "compiled", "session": session_id,
          "matched": sorted(selected), "residual_asked": pending,
          "residual_status": residual_status,
          "delivered": [row["id"] for row in ordered],
          "situation": situation[:300]}, log_path)
    return ordered
