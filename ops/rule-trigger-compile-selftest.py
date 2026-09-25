#!/usr/bin/env python3
"""Offline contract for the compiled rule triggers. No credential, no network,
no Jev call: every judgment arrives through an injected fake.

  ops/rule_trigger_compile.py   — Jev judges each pack-layer rule once; the
                                  answers become deterministic triggers.
  ops/rule_trigger_delivery.py  — a partner message is matched against them;
                                  Jev is asked only about residual rules, once
                                  per session and pack.
  ops/rule-trigger-compile.py --check — the committed compile covers every
                                  current pack-layer rule (this suite runs it,
                                  which is how CI enforces it).

THE MUTANTS AT THE END are the point of the suite's shape. Each one edits the
source of a module in memory to break a single safety property — fail-open,
negative masking, once-per-session residual, dedupe, coverage, staleness, the
surfacing floor — and the suite asserts that the property check written for
it now FAILS. A property check that still passes on its mutant proves
nothing, and the suite fails on it.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FAILURES: list[str] = []


def check(name, condition, detail=""):
    if condition:
        print(f"PASS  {name}")
    else:
        FAILURES.append(f"{name}: {detail}")
        print(f"FAIL  {name}: {detail}")


def load(name, path, *, replace=None):
    """Load a module from `path`, optionally with one source substitution."""
    source = Path(path).read_text(encoding="utf-8")
    if replace:
        old, new = replace
        if old not in source:
            raise AssertionError(f"mutant anchor not found in {path}: {old!r}")
        source = source.replace(old, new, 1)
    spec = importlib.util.spec_from_loader(name, loader=None, origin=str(path))
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(path)
    exec(compile(source, str(path), "exec"), module.__dict__)
    return module


RTC_PATH = REPO / "ops" / "rule_trigger_compile.py"
RTD_PATH = REPO / "ops" / "rule_trigger_delivery.py"
rtc = load("rule_trigger_compile_t", RTC_PATH)
rtd = load("rule_trigger_delivery_t", RTD_PATH)


class Client:
    @staticmethod
    def noul(instructions, true=None, false=None):
        return {"type": "noul", "instructions": instructions}


RULES = [
    {"id": "aaaa0001", "gist": "git push discipline", "packs": ["engineering-git"],
     "statement": "Before you git push, fetch origin and compare against origin/main."},
    {"id": "aaaa0002", "gist": "vendor intro", "packs": ["vendor-intros"],
     "statement": "When introducing a vendor, check who-do-we-know first."},
    {"id": "aaaa0003", "gist": "judgment only", "packs": ["governance-rules"],
     "statement": "Say plainly when you are not sure."},
]


def entry(rule, mode="triggered", keywords=None, negatives=None, model="jev-test"):
    return {"id": rule["id"], "packs": rule["packs"],
            "statement_sha256": rtc.sha256_text(rule["statement"]), "mode": mode,
            "triggers": {"keywords": keywords or {}, "verbs": {}, "commands": {},
                         "paths": {}, "tools": {}},
            "negatives": negatives or {}, "always_on_probability": 0.1,
            "no_cue_probability": 0.1, "model": model}


def compiled_doc():
    return rtc.document([
        entry(RULES[0], keywords={"git push": 0.8, "push": 0.6},
              negatives={"push notification": 0.7}),
        entry(RULES[1], keywords={"vendor": 0.7}),
        entry(RULES[2], mode="residual"),
    ])


def table_for(doc, tmp, extra=()):
    rows = rtc.trigger_rows(doc) + list(extra)
    path = os.path.join(tmp, "triggers.json")
    Path(path).write_text(json.dumps({"schema": "rule-jit-triggers/v1", "triggers": rows}),
                          encoding="utf-8")
    return path


class Asker:
    def __init__(self, value=0.9, fail=False):
        self.calls = []
        self.value = value
        self.fail = fail

    def __call__(self, state, questions, **kwargs):
        self.calls.append(sorted(questions))
        if self.fail:
            raise RuntimeError("synthetic outage")
        return {"model": "jev-residual",
                "answers": {q: {"type": "noul", "noul": self.value} for q in questions}}


def run(module, text, tmp, *, session="s1", now=1000.0, doc=None, ask=None,
        fallback=None, table=None, caches=None):
    doc = compiled_doc() if doc is None else doc
    residual, delivered = caches or (os.path.join(tmp, "r.json"), os.path.join(tmp, "d.json"))
    return module.advise(
        text, session_id=session, now=now,
        triggers_path=table or table_for(doc if doc else compiled_doc(), tmp),
        compiled=doc, rules=RULES, ask=ask or Asker(0.1), client=Client,
        fallback=fallback, residual_cache=residual, delivered_cache=delivered,
        log_path=os.path.join(tmp, "log.jsonl"))


# ---------------------------------------------------------------- properties
# Each takes (compile module, delivery module) and returns True when the
# property holds. The mutants below must each turn exactly one of them False.

def prop_negative_masks(rtc_m, rtd_m):
    with tempfile.TemporaryDirectory() as tmp:
        masked = run(rtd_m, "send a push notification to the phone", tmp)
    with tempfile.TemporaryDirectory() as tmp:
        plain = run(rtd_m, "then git push the branch", tmp)
    return ("aaaa0001" not in [r["id"] for r in masked]
            and "aaaa0001" in [r["id"] for r in plain])


def prop_fail_open(rtc_m, rtd_m):
    called = []

    def fallback(text):
        called.append(text)
        return [{"id": "fallback", "probability": 0.9}]

    try:
        with tempfile.TemporaryDirectory() as tmp:
            missing = os.path.join(tmp, "absent.json")
            out = rtd_m.advise("git push", session_id="s", now=1.0, triggers_path=missing,
                               compiled=compiled_doc(), rules=RULES, ask=Asker(),
                               client=Client, fallback=fallback,
                               residual_cache=os.path.join(tmp, "r"),
                               delivered_cache=os.path.join(tmp, "d"),
                               log_path=os.path.join(tmp, "l"))
    except Exception:
        return False
    return called == ["git push"] and out == [{"id": "fallback", "probability": 0.9}]


def prop_residual_once_per_session(rtc_m, rtd_m):
    ask = Asker(0.9)
    with tempfile.TemporaryDirectory() as tmp:
        first = run(rtd_m, "unrelated words", tmp, ask=ask)
        run(rtd_m, "other unrelated words", tmp, ask=ask, now=1010.0)
        run(rtd_m, "unrelated words", tmp, ask=ask, session="s2", now=1020.0)
    return (len(ask.calls) == 2 and ask.calls[0] == ["aaaa0003"]
            and [r["id"] for r in first] == ["aaaa0003"])


def prop_dedupe(rtc_m, rtd_m):
    with tempfile.TemporaryDirectory() as tmp:
        first = run(rtd_m, "a vendor intro", tmp)
        second = run(rtd_m, "a vendor intro", tmp, now=1100.0)
        later = run(rtd_m, "a vendor intro", tmp, now=1000.0 + rtd_m.DEDUPE_TTL_SECONDS + 1)
    return ([r["id"] for r in first] == ["aaaa0002"] and second == []
            and [r["id"] for r in later] == ["aaaa0002"])


def prop_no_session_no_pooling(rtc_m, rtd_m):
    """No session id: nothing to be 'once per' and no context to dedupe
    against, so every message is judged and delivered afresh and no shared
    default key is ever written."""
    ask = Asker(0.9)
    with tempfile.TemporaryDirectory() as tmp:
        first = run(rtd_m, "unrelated words", tmp, ask=ask, session=None)
        second = run(rtd_m, "unrelated words", tmp, ask=ask, session="  ", now=1010.0)
        vendor = run(rtd_m, "a vendor intro", tmp, session=None, now=1020.0)
        vendor_again = run(rtd_m, "a vendor intro", tmp, session=None, now=1030.0)
        wrote = os.path.exists(os.path.join(tmp, "r.json")) or os.path.exists(
            os.path.join(tmp, "d.json"))
    return (len(ask.calls) == 2 and [r["id"] for r in first] == ["aaaa0003"]
            and [r["id"] for r in second] == ["aaaa0003"]
            and "aaaa0002" in [r["id"] for r in vendor]
            and "aaaa0002" in [r["id"] for r in vendor_again] and not wrote)


def prop_coverage_flags_empty_trigger(rtc_m, rtd_m):
    bad = rtc_m.document([entry(RULES[0]), entry(RULES[1], keywords={"vendor": 0.7}),
                          entry(RULES[2], mode="residual")])
    return any("triggered with no trigger" in p for p in rtc_m.coverage_problems(bad, RULES))


def prop_stale_detected(rtc_m, rtd_m):
    changed = [dict(RULES[0], statement="re-taught text"), RULES[1], RULES[2]]
    return rtc_m.stale_or_missing(compiled_doc(), changed) == ["aaaa0001"]


def prop_surface_floor(rtc_m, rtd_m):
    index = {"c00": ("candidate", "keyword", "push", "statement"),
             "c01": ("candidate", "keyword", "weather", "statement")}
    answer = {"model": "m", "answers": {"c00": {"noul": 0.5}, "c01": {"noul": 0.49},
                                       "always_on": {"noul": 0.1}, "no_cue": {"noul": 0.1}}}
    got = rtc_m.interpret(RULES[0], answer, index, model="m")
    return got["triggers"]["keywords"] == {"push": 0.5} and got["mode"] == "triggered"


PROPERTIES = {
    "negative phrases mask their shared word": prop_negative_masks,
    "a missing trigger table falls back to judging, never to silence": prop_fail_open,
    "residual rules are judged once per session": prop_residual_once_per_session,
    "a rule already delivered this session is not resent inside the window": prop_dedupe,
    "no session id means no residual marker, no dedupe, no shared key":
        prop_no_session_no_pooling,
    "coverage flags a triggered rule with no trigger": prop_coverage_flags_empty_trigger,
    "a re-taught rule is detected as stale": prop_stale_detected,
    "the surfacing floor is inclusive at 0.5 and excludes below it": prop_surface_floor,
}

for name, prop in PROPERTIES.items():
    check(f"property holds: {name}", prop(rtc, rtd))

# ---------------------------------------------------------------- direct cases

with tempfile.TemporaryDirectory() as tmp:
    hit = run(rtd, "please git push this", tmp)
check("a trigger hit carries Jev's compile-time probability and model",
      hit and hit[0]["id"] == "aaaa0001" and hit[0]["probability"] == 0.8
      and hit[0]["binding_model"] == "jev-test" and hit[0]["ranking_model"] is None, hit)

with tempfile.TemporaryDirectory() as tmp:
    stale_doc = compiled_doc()
    stale_doc["rules"]["aaaa0002"]["statement_sha256"] = "0" * 64
    ask = Asker(0.9)
    out = run(rtd, "a vendor intro", tmp, doc=stale_doc, ask=ask)
check("a stale rule is not matched on old triggers; it is judged as residual instead",
      ask.calls and "aaaa0002" in ask.calls[0]
      and [r for r in out if r["id"] == "aaaa0002"][0]["source"] == "residual_judged", out)

with tempfile.TemporaryDirectory() as tmp:
    down = Asker(fail=True)
    out1 = run(rtd, "unrelated", tmp, ask=down)
    up = Asker(0.9)
    out2 = run(rtd, "unrelated", tmp, ask=up, now=1001.0)
check("a failed residual request still returns matched rules and is retried next message",
      out1 == [] and len(up.calls) == 1 and [r["id"] for r in out2] == ["aaaa0003"])

with tempfile.TemporaryDirectory() as tmp:
    ask = Asker(0.9)
    out = run(rtd, "git push to the vendor", tmp, ask=ask)
check("a pack with a trigger hit is not also judged as residual; others are",
      {r["id"] for r in out} >= {"aaaa0001", "aaaa0002"} and ask.calls == [["aaaa0003"]], out)

with tempfile.TemporaryDirectory() as tmp:
    structural = {"kind": "prompt_regex", "pattern": r"^\s*<task-notification>",
                  "packs": ["governance-rules"], "rule_ids": ["aaaa0003"],
                  "source": "structural_extra"}
    table = table_for(compiled_doc(), tmp, extra=[structural])
    out = run(rtd, "<task-notification><status>completed</status>", tmp, table=table)
check("a reviewed structural trigger delivers without a model judgment",
      [r for r in out if r["id"] == "aaaa0003"][0]["binding_model"] == "structural-trigger", out)

with tempfile.TemporaryDirectory() as tmp:
    blocker = os.path.join(tmp, "file")
    Path(blocker).write_text("x", encoding="utf-8")
    caches = (os.path.join(blocker, "r.json"), os.path.join(blocker, "d.json"))
    out = run(rtd, "git push now", tmp, caches=caches)
check("unwritable caches still deliver",
      [r["id"] for r in out][:1] == ["aaaa0001"], out)

many = [dict(RULES[1], id=f"bbbb{i:04d}") for i in range(8)]
doc_many = rtc.document([entry(r, keywords={"vendor": 0.6 + i / 100})
                         for i, r in enumerate(many)])
with tempfile.TemporaryDirectory() as tmp:
    out = rtd.advise("vendor", session_id="s", now=1.0, triggers_path=table_for(doc_many, tmp),
                     compiled=doc_many, rules=many, ask=Asker(), client=Client,
                     residual_cache=os.path.join(tmp, "r"), delivered_cache=os.path.join(tmp, "d"),
                     log_path=os.path.join(tmp, "l"))
check("at most MAX_SURFACED rules per message, strongest first",
      len(out) == rtd.MAX_SURFACED and out[0]["id"] == "bbbb0007", out)

rows = rtc.trigger_rows(rtc.document([
    dict(entry(RULES[0], keywords={"git push": 0.8}), triggers={
        "keywords": {"git push": 0.8}, "verbs": {"add-loop": 0.7}, "commands": {"git diff": 0.6},
        "paths": {"ops/ci.sh": 0.6}, "tools": {"Agent": 0.6}})]))
kinds = sorted(r["kind"] for r in rows)
check("trigger_rows emits prompt, verb, command and anchored path rows",
      kinds == ["bash_family", "path_pattern", "prompt_regex", "verb"]
      and [r["pattern"] for r in rows if r["kind"] == "path_pattern"] == ["*ops/ci.sh"], rows)
check("an always-on rule emits no match rows",
      rtc.trigger_rows(rtc.document([entry(RULES[0], mode="always_on",
                                           keywords={"x": 0.9})])) == [])

emap = rtc.load_json(rtc.MAP_PATH)
pack_keywords = {n: p.get("triggers", []) for n, p in emap["rule_packs"].items()}
real_rules = rtc.pack_rules()
first = rtc.candidates(real_rules[0], all_rules=real_rules, pack_keywords=pack_keywords,
                       verbs=rtc.known_verbs())
second = rtc.candidates(real_rules[0], all_rules=real_rules, pack_keywords=pack_keywords,
                        verbs=rtc.known_verbs())
check("candidate generation is deterministic", first == second)
questions, state, index = rtc.questions_for(real_rules[0], *first, Client)
check("one request per rule carries every candidate, probe and the two rule-level questions",
      set(questions) == set(index) | {"always_on", "no_cue"}
      and len(json.dumps(state)) < 96_000)

# --------------------------------------------------- the committed compile (CI)
committed = rtc.load_compiled()
check("the committed compile covers every current pack-layer rule",
      committed is not None and rtc.coverage_problems(committed, real_rules) == [],
      rtc.coverage_problems(committed, real_rules)[:5] if committed else "missing")
result = subprocess.run([sys.executable, str(REPO / "ops" / "rule-trigger-compile.py"), "--check"],
                        capture_output=True, text=True, timeout=60)
check("ops/rule-trigger-compile.py --check passes on the committed files",
      result.returncode == 0, result.stdout[-400:] + result.stderr[-400:])
for rid, item in (committed or {}).get("rules", {}).items():
    if item["mode"] == "triggered" and not any(item["triggers"].values()):
        check(f"{rid} is deliverable", False, "triggered with no trigger")

# ---------------------------------------------------------------- mutants
MUTANTS = [
    ("negative phrases mask their shared word", RTD_PATH,
     ('body = re.sub(row["negative_pattern"], " ", body, flags=re.I)', "body = body")),
    ("a missing trigger table falls back to judging, never to silence", RTD_PATH,
     ("if compiled is None or rows is None:", "if False:")),
    ("residual rules are judged once per session", RTD_PATH,
     ("cache.put(residual_cache, marker(pack), True",
      "(lambda *a, **k: None)(residual_cache, marker(pack), True")),
    ("a rule already delivered this session is not resent inside the window", RTD_PATH,
     ("if not (isinstance(recent.get(rule_id), (int, float))",
      "if True or not (isinstance(recent.get(rule_id), (int, float))")),
    ("no session id means no residual marker, no dedupe, no shared key", RTD_PATH,
     ('if isinstance(session_id, str) and session_id.strip() else None',
      'if isinstance(session_id, str) and session_id.strip() else "no-session"')),
    ("coverage flags a triggered rule with no trigger", RTC_PATH,
     ('problems.append(f"{rid}: triggered with no trigger")', "pass")),
    ("a re-taught rule is detected as stale", RTC_PATH,
     ('entry.get("statement_sha256") != sha256_text(rule["statement"])',
      'entry.get("statement_sha256") != entry.get("statement_sha256")')),
    ("the surfacing floor is inclusive at 0.5 and excludes below it", RTC_PATH,
     ('if role == "candidate" and prob >= SURFACE_AT:',
      'if role == "candidate" and prob > SURFACE_AT:')),
]
for prop_name, path, substitution in MUTANTS:
    mutated = load(f"mutant_{abs(hash(prop_name))}", path, replace=substitution)
    rtc_m = mutated if path == RTC_PATH else rtc
    rtd_m = mutated if path == RTD_PATH else rtd
    try:
        survived = PROPERTIES[prop_name](rtc_m, rtd_m)
    except Exception:
        survived = False
    check(f"mutant is killed: {prop_name}", not survived)

if FAILURES:
    print("rule-trigger-compile-selftest: FAIL")
    for line in FAILURES:
        print("  " + line)
    raise SystemExit(1)
print("rule-trigger-compile-selftest: all cases passed")
