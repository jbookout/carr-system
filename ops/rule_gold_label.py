"""rule_gold_label.py — dense gold labels for the rule-delivery benchmark.

WHAT IT PRODUCES. For every benchmark case, a probability for EVERY live rule
that the rule binds on that turn — not a shortlist. ops/rule_delivery_eval.py
then scores every delivery path against the gold set these become. The gold
is the answer key the rule-system gate is tuned against, so the procedure is
spelled out here rather than left to whoever runs it.

THE BATCHING SCHEME (architecture_or_design, judged by Jev before the scale
run; see the PR body for the numbers). The case is the SUBJECT and each rule
is an independent true-or-false question about it: one request per case, the
case as the state, one noul per live rule (195 today) carrying that rule's
statement. That is the shape ops/jev_judge.py names for independent labels
("ask every independent question about one subject in one request"), and it
keeps the state small, which is where Jev's accuracy lives. Measured on the
live corpus on 2026-09-26: the whole roster in one request with this wording
returns HTTP 400 max_tokens_exceeded, so a case is two requests of about 100
rules (about 82k input tokens a case), each answering in under a second. On
the 16 hand-labelled v1 cases it recovered 81% of the hand gold at p >= 0.5,
against 59% for the rule-as-state shape; Jev put 1.00 on this scheme over the
four alternatives below. The rejected alternatives: one Choice over the roster (wrong shape —
several rules bind at once, and a Choice's probabilities sum to one); a
keyword shortlist then nouls (a shortlist is exactly the recall ceiling this
benchmark exists to measure, so it cannot be inside the labeller); and the
rule as the state with one noul per case (used here only as the SECOND PASS,
because it asks the same question from the other side).

THE BANDS. p >= YES_AT is gold, p <= NO_AT is not, anything between is
BORDERLINE. Every borderline pair gets a second Jev pass from the other side
(the rule as state, the case as the question, a stricter wording) and then a
written human-or-model adjudication; the adjudication, not either Jev pass,
decides the label, and its reason is kept beside it. An adjudication file row
is {"case": id, "rule": id, "gold": bool, "reason": text}.

THE SPLIT. 30 per cent of cases are held out as TEST, fixed by seed:
within each stratum, cases are ordered by sha256(seed:id) and the first
ceil(0.3 n) are test. A case added later (a regression case from the intake
command) is assigned by the same hash against a threshold, so adding a case
never moves an existing one. Tuning may read only the train split.

RULE CLASSES. The recall question differs by how a rule is supposed to reach
a session, so every rule gets one class, decided by these ordered questions
against ops/config/rule-enforcement-map.json and the JIT trigger table:
  1. Is its load layer `layer0` (recited at every boot)?        -> a always_on
  2. Is its load layer `control` (an installed deny, stop or schema gate
     prints it where it binds)?                                  -> d gate_named
  3. Does a compiled JIT tool trigger name it, or is its enforcement class
     `surfacing` (delivered at a named action)?                  -> b action_point
  4. Otherwise (a pack rule delivered on topic).                 -> c topic

A LIBRARY. No entrypoint construct, for the sealed-inventory reason
ops/typesafe_client.py documents; the command lines are tools/rule-gold-label.py
and tools/rule-delivery-eval-intake.py.
"""

import hashlib
import json
import math
import os
import re

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

YES_AT = 0.75
NO_AT = 0.35
STATEMENT_CHARS = 2500
# Rules per request. The whole roster in one request with this wording returns
# HTTP 400 max_tokens_exceeded (measured 2026-09-26); two requests per case fit.
CHUNK = 100
TEST_FRACTION = 0.30
DEFAULT_SEED = "rule-delivery-eval-v2"
RULE_CLASSES = ("always_on", "action_point", "topic", "gate_named")

SYSTEM_NOTE = ("An AI assistant (Claude Code) works for the two partners of a small "
               "healthcare commercial real estate brokerage. It works in the firm's own "
               "software repository and record layer (deals, clients, tours, rules), and "
               "can run tools. `turn` is ONE turn: the message it received (from a partner, "
               "or a machine notification) and the tool calls it made in reply.")


# ------------------------------------------------------------------ the question

def _clip(text, limit):
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def call_line(call):
    """One readable line for a tool call: its name and its salient input."""
    name = call.get("tool_name") or "?"
    inp = call.get("tool_input") or {}
    if isinstance(inp, dict):
        for key in ("command", "file_path", "url", "description", "prompt", "query", "action"):
            if isinstance(inp.get(key), str) and inp[key].strip():
                return f"{name}: {_clip(inp[key], 240)}"
        if inp:
            return f"{name}: {_clip(json.dumps(inp, sort_keys=True), 240)}"
    return name


def case_state(case):
    is_machine = case["prompt"].lstrip().startswith("<task-notification>")
    return {"system": SYSTEM_NOTE,
            "turn": {"from": "machine notification" if is_machine else "partner",
                     "message": case["prompt"],
                     "tool_calls": [call_line(c) for c in case.get("tool_calls") or []]}}


def rule_question(tsc, rule, limit=STATEMENT_CHARS):
    text = _clip(rule["statement"], limit)
    return tsc.noul(
        "A standing rule for the assistant:\n<<<\n" + text + "\n>>>\n\n"
        "Does this rule BIND on `turn`? It binds when the turn's message or its tool "
        "calls meet the rule's own trigger, so that an assistant handling exactly this "
        "turn must apply, check or respect the rule to act correctly. A rule about the "
        "project in general, or about some other kind of turn, does not bind.",
        true="The turn meets this rule's trigger; the assistant must apply or check it on this turn.",
        false="This turn does not meet the rule's trigger; the rule governs something this turn does not do.")


def label_case(case, rules, tsc, *, calls_log, timeout=120.0, chunk=None):
    """{rule id: probability} for one case, every rule, in one request (or in
    `chunk`-sized requests when a smaller request is wanted). Returns
    (probabilities, usage) with usage {"input_tokens", "output_tokens", "requests"}."""
    probs, usage = {}, {"input_tokens": 0, "output_tokens": 0, "requests": 0}
    size = chunk or CHUNK
    state = case_state(case)
    for start in range(0, len(rules), size):
        part = rules[start:start + size]
        answer = tsc.ask(state, {r["id"]: rule_question(tsc, r) for r in part},
                         calls_log=calls_log, timeout=timeout)
        for rid, row in (answer.get("answers") or {}).items():
            probs[rid] = round(float(row["noul"]), 4)
        u = answer.get("usage") or {}
        usage["input_tokens"] += int(u.get("input_tokens") or 0)
        usage["output_tokens"] += int(u.get("output_tokens") or 0)
        usage["requests"] += 1
    return probs, usage


def second_pass(rule, cases, tsc, *, calls_log, timeout=120.0):
    """The other side of the question: the rule is the state and each borderline
    case is a noul, worded more strictly. {case id: probability}, usage."""
    state = {"system": SYSTEM_NOTE, "rule": {"id": rule["id"],
                                             "statement": _clip(rule["statement"], 6000)}}
    questions = {}
    for case in cases:
        s = case_state(case)["turn"]
        calls = "; ".join(s["tool_calls"]) or "none"
        questions[case["id"]] = tsc.noul(
            f"Turn from {s['from']}:\n<<<\n{_clip(s['message'], 1800)}\n>>>\n"
            f"Tool calls made in reply: {calls}\n\n"
            "Is `rule` binding on this specific turn — is its trigger actually met here, "
            "so that ignoring the rule on this turn would be a violation of it? "
            "Topical overlap alone is not enough.",
            true="The rule's trigger is met on this turn; ignoring it here would violate it.",
            false="The rule's trigger is not met on this turn.")
    answer = tsc.ask(state, questions, calls_log=calls_log, timeout=timeout)
    u = answer.get("usage") or {}
    return ({cid: round(float(row["noul"]), 4) for cid, row in (answer.get("answers") or {}).items()},
            {"input_tokens": int(u.get("input_tokens") or 0),
             "output_tokens": int(u.get("output_tokens") or 0), "requests": 1})


# ------------------------------------------------------------------ doctrine (second target)
#
# 2,272 doctrine sections cannot each be asked about every case, so doctrine
# gold is a SHORTLIST-THEN-LABEL scheme, and its recall ceiling is the
# shortlist's (said plainly in the fixture header):
#   1. one Jev noul per doctrine DOCUMENT (title and opening text, all 261),
#      case as state: which documents govern this turn;
#   2. plus the deterministic search-doctrine hits for the turn's text;
#   3. candidates = every section of the documents at p >= DOC_AT (the top
#      DOC_TOP at most) plus the search hits, capped at SECTION_CAP;
#   4. one Jev noul per candidate section, case as state; the same bands as
#      rules, and every borderline pair is adjudicated in writing.

DOC_AT = 0.5
DOC_TOP = 6
SECTION_CAP = 120
SECTION_CHARS = 1200
DOC_CHUNK = 90
SECTION_CHUNK = 60


def document_question(tsc, doc):
    return tsc.noul(
        f"A doctrine document of the firm (standing procedure, playbook or reference):\n"
        f"<<<\nTitle: {_clip(doc.get('title') or '', 200)}\n"
        f"Opening: {_clip(doc.get('opening') or '', 400)}\n>>>\n\n"
        "Does this document GOVERN `turn` — would an assistant handling exactly this turn need "
        "to follow or consult a section of it to act correctly? General topical relevance "
        "is not enough.",
        true="A section of this document governs how this turn must be handled.",
        false="This document does not govern this turn.")


def section_question(tsc, section):
    return tsc.noul(
        f"A section of the firm's doctrine ({_clip(section.get('doc_title') or section['doc'], 160)}"
        f" — {_clip(section.get('title') or '', 160)}):\n<<<\n"
        f"{_clip(section.get('text') or '', SECTION_CHARS)}\n>>>\n\n"
        "Does this section GOVERN `turn`: does it state a procedure, constraint or standard "
        "that an assistant handling exactly this turn must follow or check to act correctly? "
        "Background that is merely on the same topic does not govern.",
        true="This section governs how this turn must be handled.",
        false="This section does not govern this turn.")


def _ask_nouls(tsc, state, items, builder, key, chunk, calls_log, timeout):
    probs, usage = {}, {"input_tokens": 0, "requests": 0}
    for start in range(0, len(items), chunk):
        part = items[start:start + chunk]
        answer = tsc.ask(state, {str(i): builder(tsc, item) for i, item in enumerate(part)},
                         calls_log=calls_log, timeout=timeout)
        for i, item in enumerate(part):
            row = (answer.get("answers") or {}).get(str(i))
            if row is not None:
                probs[item[key]] = round(float(row["noul"]), 4)
        usage["input_tokens"] += int((answer.get("usage") or {}).get("input_tokens") or 0)
        usage["requests"] += 1
    return probs, usage


def doctrine_label_case(case, documents, sections, tsc, *, calls_log, search_refs=(),
                        timeout=120.0):
    """({doc id: p}, {section ref: p}, usage) for one case: the document pass,
    the shortlist, and the section pass. `documents` rows carry id, title,
    opening; `sections` rows carry ref, doc, doc_title, title, text. Refs and
    doc ids are opaque store ids (slugs carry names; see the CLI catalog)."""
    state = case_state(case)
    doc_p, u1 = _ask_nouls(tsc, state, documents, document_question, "id", DOC_CHUNK,
                           calls_log, timeout)
    top = [did for did, p in sorted(doc_p.items(), key=lambda kv: -kv[1]) if p >= DOC_AT][:DOC_TOP]
    by_ref = {s["ref"]: s for s in sections}
    picked = [s["ref"] for s in sections if s["doc"] in top]
    picked += [ref for ref in search_refs if ref in by_ref and ref not in picked]
    picked = picked[:SECTION_CAP]
    sec_p, u2 = _ask_nouls(tsc, state, [by_ref[r] for r in picked], section_question, "ref",
                           SECTION_CHUNK, calls_log, timeout)
    usage = {"input_tokens": u1["input_tokens"] + u2["input_tokens"],
             "requests": u1["requests"] + u2["requests"], "shortlist": len(picked)}
    return doc_p, sec_p, usage


# ------------------------------------------------------------------ bands and gold

def band(p, yes_at=YES_AT, no_at=NO_AT):
    if p >= yes_at:
        return "gold"
    if p <= no_at:
        return "not"
    return "borderline"


def borderlines(probs, yes_at=YES_AT, no_at=NO_AT):
    """[(case id, rule id, p)] for every pair strictly inside the band."""
    return sorted((cid, rid, p) for cid, row in probs.items() for rid, p in row.items()
                  if band(p, yes_at, no_at) == "borderline")


def gold_sets(probs, adjudications, yes_at=YES_AT, no_at=NO_AT):
    """{case id: sorted gold ids}. A borderline pair with no adjudication is an
    error, not a silent 'not gold': every one must be decided in writing."""
    decided = {(a["case"], a["rule"]): bool(a["gold"]) for a in adjudications}
    missing = []
    out = {}
    for cid, row in probs.items():
        gold = []
        for rid, p in row.items():
            b = band(p, yes_at, no_at)
            if b == "gold":
                gold.append(rid)
            elif b == "borderline":
                if (cid, rid) not in decided:
                    missing.append((cid, rid))
                elif decided[(cid, rid)]:
                    gold.append(rid)
        out[cid] = sorted(gold)
    if missing:
        raise ValueError(f"{len(missing)} borderline labels have no adjudication, "
                         f"first {missing[:5]}")
    return out


# ------------------------------------------------------------------ split

def _unit(seed, case_id):
    digest = hashlib.sha256(f"{seed}:{case_id}".encode("utf-8")).hexdigest()
    return int(digest[:16], 16) / float(1 << 64)


def assign_splits(cases, seed=DEFAULT_SEED, fraction=TEST_FRACTION):
    """{case id: "train"|"test"}: per stratum, the ceil(fraction n) cases with
    the smallest seeded hash are test."""
    by = {}
    for case in cases:
        by.setdefault(case["stratum"], []).append(case["id"])
    out = {}
    for ids in by.values():
        ranked = sorted(ids, key=lambda cid: (_unit(seed, cid), cid))
        k = math.ceil(fraction * len(ranked))
        for i, cid in enumerate(ranked):
            out[cid] = "test" if i < k else "train"
    return out


def split_for_new_case(case_id, seed=DEFAULT_SEED, fraction=TEST_FRACTION):
    """A case added after the split (the intake) is placed by hash threshold,
    so no existing case moves."""
    return "test" if _unit(seed, case_id) < fraction else "train"


# ------------------------------------------------------------------ rule classes

def rule_classes(repo=REPO, rule_ids=None):
    """{rule id: class} by the ordered questions in the module docstring."""
    with open(os.path.join(repo, "ops", "config", "rule-enforcement-map.json"),
              "r", encoding="utf-8") as handle:
        emap = json.load(handle)
    with open(os.path.join(repo, "ops", "config", "rule-jit-triggers.v1.json"),
              "r", encoding="utf-8") as handle:
        jit = {rid for row in json.load(handle).get("triggers") or []
               for rid in row.get("rule_ids") or []}
    layers = emap.get("rule_load_layers") or {}
    controls = emap.get("rule_controls") or {}
    ids = list(rule_ids) if rule_ids is not None else list(layers)
    out = {}
    for rid in ids:
        layer = (layers.get(rid) or {}).get("load_layer")
        if layer == "layer0":
            out[rid] = "always_on"
        elif layer == "control":
            out[rid] = "gate_named"
        elif rid in jit or (controls.get(rid) or {}).get("enforcement_class") == "surfacing":
            out[rid] = "action_point"
        else:
            out[rid] = "topic"
    return out


GUIDANCE_MANIFEST = os.path.join("audits", "guidance-migration-manifest.v1.tsv")


def rule_groups(repo=REPO, rule_ids=None):
    """{rule id: "guidance_deferred"|"other"}. A REPORTING split, not a label:
    the rules the August guidance migration retyped as judgment_ambient
    guidance, which standing-context now defers to consumers never built.
    They are still live rules and already in the dense gold."""
    import csv
    with open(os.path.join(repo, GUIDANCE_MANIFEST), "r", encoding="utf-8") as handle:
        deferred = {row["source_id"].strip() for row in csv.DictReader(handle, delimiter="\t")
                    if (row.get("source_id") or "").strip()}
    ids = list(rule_ids) if rule_ids is not None else sorted(deferred)
    return {rid: ("guidance_deferred" if rid in deferred else "other") for rid in ids}


# ------------------------------------------------------------------ hygiene

# What a committed case must never carry. Deliberately broad: a false alarm
# costs a reworded case, a miss puts private material in the repository.
SCRUB_PATTERNS = {
    "email": r"[\w.+-]+@[\w-]+\.[\w.]+",
    "phone": r"\(?\b\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b",
    # Two or more digits, or a unit: a shell positional ($1 in awk) is not money.
    "dollar": r"\$\s?\d{2,}|\$\s?\d+(?:\.\d+)?\s?(?:k|m|mm|million|/)",
    "square_feet": r"\b\d[\d,]*\s?(?:sf|sq\.? ?ft|square feet|rsf|usf)\b",
    "url_host": r"https?://(?!example\.com|x\.com/example)[\w.-]+",
    "ip": r"\b\d{1,3}(?:\.\d{1,3}){3}\b",
    "credential_path": r"(?:~/\.config/|\.env\b|\.age\b|id_rsa|id_ed25519|typesafe\.env|api[_-]?key\s*=)",
    "token_like": r"\b(?:sk|pk|ghp|gho|xox[bp])[-_][A-Za-z0-9]{12,}",
}


def scrub_findings(text, extra_names=()):
    """[(pattern name, match)] for anything a committed case must not carry.
    `extra_names` are literal strings (person or practice names known to the
    caller) to refuse as well."""
    hits = []
    for name, pattern in SCRUB_PATTERNS.items():
        for m in re.finditer(pattern, text, flags=re.I):
            hits.append((name, m.group(0)))
    low = text.lower()
    for name in extra_names:
        if name and name.lower() in low:
            hits.append(("name", name))
    return hits


def longest_shared_run(a, b):
    """Length in words of the longest run of consecutive words two texts share
    (case- and punctuation-insensitive): the verbatim-copy test."""
    wa = re.findall(r"[a-z0-9']+", (a or "").lower())
    wb = re.findall(r"[a-z0-9']+", (b or "").lower())
    if not wa or not wb:
        return 0
    best = 0
    prev = [0] * (len(wb) + 1)
    for x in wa:
        cur = [0] * (len(wb) + 1)
        for j, y in enumerate(wb, 1):
            if x == y:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


# ------------------------------------------------------------------ regression intake

MAX_SHARED_RUN = 5


def intake_case(doc, case_id, missed_rule, *, live_rules, prompt=None, stratum=None,
                tool_calls=None, source_text=None, probs=None, extra_names=(),
                seed=None):
    """A LIVE MISS BECOMES A BENCHMARK CASE. Returns the new case dict (the
    caller appends it). Raises ValueError when the case may not be committed.

    `case_id` is either a case already in `doc` (a benchmark shape that missed
    live: the new case copies it) or a live reference (a drift-observer event,
    a transcript turn) whose partner words are `source_text` — then `prompt`
    must be a PARAPHRASE of it: no run of more than MAX_SHARED_RUN words in
    common, nothing scrub_findings() refuses.

    `missed_rule` is gold by observation: the rule was needed and not
    delivered. `probs` ({rule id: p}, one Jev first pass over every live rule)
    fills the rest densely: p >= YES_AT is gold, a borderline p is DISPUTED
    (excluded from scoring on both sides) until someone adjudicates it in
    writing, which keeps the intake from inventing labels. Without `probs`
    the case carries the copied case's gold (or only the missed rule) and is
    marked labels="partial"."""
    if missed_rule not in live_rules:
        raise ValueError(f"{missed_rule} is not a live rule")
    existing = {c["id"]: c for c in doc.get("cases") or []}
    base = existing.get(case_id)
    if base is None:
        if not prompt or not stratum:
            raise ValueError("a live reference needs --prompt (a paraphrase) and --stratum")
        if source_text is not None:
            run = longest_shared_run(prompt, source_text)
            if run > MAX_SHARED_RUN:
                raise ValueError(f"prompt shares a {run}-word run with the live turn: paraphrase it")
    prompt = prompt or base["prompt"]
    stratum = stratum or base["stratum"]
    calls = tool_calls if tool_calls is not None else ((base or {}).get("tool_calls") or [])
    hits = scrub_findings(prompt + " " + json.dumps(calls), extra_names)
    if hits:
        raise ValueError(f"case carries material that may not be committed: {hits[:5]}")
    slug = re.sub(r"[^a-z0-9]+", "-", case_id.lower()).strip("-")[:24]
    new_id = f"reg-{slug}-{missed_rule}"
    if new_id in existing:
        raise ValueError(f"{new_id} already exists: this miss is already in the benchmark")
    disputed, labels = [], "dense"
    if probs:
        gold = {rid for rid, p in probs.items() if rid in live_rules and p >= YES_AT}
        disputed = sorted(rid for rid, p in probs.items()
                          if rid in live_rules and band(p) == "borderline" and rid != missed_rule)
    else:
        gold = set((base or {}).get("gold") or [])
        labels = "partial"
    gold.add(missed_rule)
    return {"id": new_id, "stratum": stratum,
            "split": split_for_new_case(new_id, seed or DEFAULT_SEED),
            "origin": "live-miss", "live_ref": case_id, "missed_rule": missed_rule,
            "labels": labels, "prompt": prompt, "tool_calls": calls,
            "gold": sorted(gold), "gold_doctrine": [], "disputed": disputed,
            "adjudicated": []}
