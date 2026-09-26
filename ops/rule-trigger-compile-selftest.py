#!/usr/bin/env python3
"""Offline contract for the compiled rule triggers. No credential, no network,
no Jev call: every judgment arrives through an injected fake.

  ops/rule_trigger_compile.py   — Jev judges each pack-layer rule once; the
                                  answers become deterministic triggers.
  ops/rule_trigger_delivery.py  — a message is matched against them. A machine
                                  envelope stops there (zero Jev requests); a
                                  human prompt then gets one budgeted judgment
                                  (1 ranking + single-rule binding requests
                                  for its top 7, at most 8 requests), with
                                  stale rules judged and residual rules
                                  ranked on every human prompt.
  ops/rule-trigger-compile.py --check — the committed compile covers every
                                  current pack-layer rule (this suite runs it,
                                  which is how CI enforces it).

THE MUTANTS AT THE END are the point of the suite's shape. Each one edits the
source of a module in memory to break a single safety property — fail-open,
negative masking, residual/stale judged every human prompt, the request
budget, zero requests on an envelope, dedupe, no shared session key,
coverage, staleness, the surfacing floor — and the suite asserts that the
property check written for it now FAILS. A property check that still passes on
its mutant proves nothing, and the suite fails on it.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import urllib.error
from pathlib import Path
from types import SimpleNamespace

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
TSC_PATH = REPO / "ops" / "typesafe_client.py"
BUILD_PATH = REPO / "ops" / "jev_build_advisory.py"
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
    ] + filler_entries())


def filler_entries():
    """Fillers are compiled (so they are neither stale nor missing) and match
    only a word no test prompt contains."""
    return [entry(rule, keywords={f"fillerword{i}": 0.6}) for i, rule in enumerate(FILLERS)]


def table_for(doc, tmp, extra=()):
    rows = rtc.trigger_rows(doc) + list(extra)
    path = os.path.join(tmp, "triggers.json")
    Path(path).write_text(json.dumps({"schema": "rule-jit-triggers/v1", "triggers": rows}),
                          encoding="utf-8")
    return path



# Fillers make the roster bigger than the binding capacity, so the ranking
# call decides what is judged and "always judged" is a real claim rather than
# an accident of a small roster.
FILLERS = [{"id": f"ffff{i:04d}", "gist": f"filler {i}", "packs": ["filler-pack"],
            "statement": f"Filler rule number {i} about an unrelated topic."}
           for i in range(40)]
FILLERS[0]["packs"] = ["governance-rules"]  # shares the residual rule's pack
FILLERS[1]["packs"] = ["vendor-intros"]  # shares the stale-test rule's pack
ROSTER = RULES + FILLERS
NOTIFICATION = ("<task-notification>\n<task-id>a1</task-id>\n<status>completed</status>\n"
                "<summary>Agent \"x\" finished with an error, see https://example.com/log"
                "</summary>\n</task-notification>")


class Asker:
    """A single-rule binding request: records the rule each request was about."""

    def __init__(self, value=0.1, fail=False):
        self.calls = []
        self.value = value
        self.fail = fail

    def __call__(self, subject, questions, *, rule_id=None, **kwargs):
        self.calls.append(rule_id)
        self.subjects = getattr(self, "subjects", []) + [subject]
        if self.fail:
            raise RuntimeError("synthetic outage")
        return {"model": "jev-binder",
                "answers": {q: {"type": "noul", "noul": self.value} for q in questions}}

    def asked(self):
        return set(self.calls)


class Ranker:
    """The ranking request: fillers first, so a rule it is not told about sinks."""

    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    def __call__(self, text, pool, limit, client):
        self.calls += 1
        self.pools = getattr(self, "pools", []) + [{rule["id"] for rule in pool}]
        if self.fail:
            raise RuntimeError("synthetic outage")
        ids = sorted(rule["id"] for rule in pool)
        ids.sort(key=lambda rule_id: (not rule_id.startswith("ffff"), rule_id))
        return ids[:limit], 1


def run(module, text, tmp, *, session="s1", now=1000.0, doc=None, ask=None, rank=None,
        table=None, caches=None, rules=None, envelope=None, compiled="default"):
    doc = compiled_doc() if doc is None else doc
    delivered = caches or os.path.join(tmp, "d.json")
    return module.advise(
        text, session_id=session, now=now,
        triggers_path=table or table_for(doc, tmp),
        compiled=doc if compiled == "default" else compiled,
        rules=ROSTER if rules is None else rules,
        ask=ask if ask is not None else Asker(), client=Client,
        rank=rank if rank is not None else Ranker(),
        delivered_cache=delivered, envelope=envelope,
        log_path=os.path.join(tmp, "log.jsonl"))


def ids(rows):
    return [row["id"] for row in rows]


# ---------------------------------------------------------------- properties
# Each takes (compile module, delivery module) and returns True when the
# property holds. The mutants below must each turn exactly one of them False.

def prop_negative_masks(rtc_m, rtd_m):
    with tempfile.TemporaryDirectory() as tmp:
        masked = run(rtd_m, "send a push notification to the phone", tmp)
    with tempfile.TemporaryDirectory() as tmp:
        plain = run(rtd_m, "then git push the branch", tmp)
    return "aaaa0001" not in ids(masked) and "aaaa0001" in ids(plain)


def prop_fail_open(rtc_m, rtd_m):
    """No trigger table: a human prompt is still judged, within budget."""
    ask, rank = Asker(0.9), Ranker()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            out = run(rtd_m, "git push please", tmp, ask=ask, rank=rank,
                      table=os.path.join(tmp, "absent.json"))
    except Exception:
        return False
    return (bool(out) and len(ask.calls) >= 1
            and rank.calls + len(ask.calls) <= rtd_m.MAX_JEV_CALLS)


def prop_residual_every_human_prompt(rtc_m, rtd_m):
    """On every human prompt of a session — including one where their pack
    already had a trigger hit — stale rules are judged and residual rules are
    in the ranking. (Always judging residual rules too would spend 3 of the 7
    single-rule requests every time; measured, that caps recall at 70%.)"""
    stale_doc = compiled_doc()
    stale_doc["rules"]["aaaa0002"]["statement_sha256"] = "0" * 64
    # A reviewed row that puts a hit in the residual rule's own pack.
    same_pack = {"kind": "prompt_regex", "pattern": r"\bnow\b", "packs": ["governance-rules"],
                 "rule_ids": ["ffff0000"], "source": "structural_extra"}
    asks, pools = [], []
    with tempfile.TemporaryDirectory() as tmp:
        stale_pack = {"kind": "prompt_regex", "pattern": r"\bvendor\b",
                      "packs": ["vendor-intros"], "rule_ids": ["ffff0001"],
                      "source": "structural_extra"}
        table = table_for(stale_doc, tmp, extra=[same_pack, stale_pack])
        for step, text in enumerate(("unrelated words", "other words",
                                     "git push to the vendor now")):
            ask, rank = Asker(0.1), Ranker()
            run(rtd_m, text, tmp, ask=ask, rank=rank, now=1000.0 + step, doc=stale_doc,
                table=table)
            asks.append(ask.asked())
            pools.append(set().union(*getattr(rank, "pools", [set()])))
    return (all("aaaa0002" in asked for asked in asks)
            and all("aaaa0003" in pool for pool in pools))


def prop_budget_cap(rtc_m, rtd_m):
    """At most MAX_JEV_CALLS requests per human prompt, even with more stale
    rules than BIND_TOP_K; the excess is reported, not silently dropped."""
    many = [{"id": f"rrrr{i:04d}", "gist": "r", "packs": ["stale-pack"],
             "statement": f"Stale rule {i}."} for i in range(40)]
    doc = rtc_m.document(filler_entries())  # the 40 are uncompiled, i.e. stale
    ask, rank = Asker(0.1), Ranker()
    with tempfile.TemporaryDirectory() as tmp:
        run(rtd_m, "anything at all", tmp, ask=ask, rank=rank, doc=doc,
            rules=many + FILLERS, table=table_for(doc, tmp))
        log = [json.loads(line) for line in Path(tmp, "log.jsonl").read_text().splitlines()]
    total = rank.calls + len(ask.calls)
    k = rtd.BIND_TOP_K
    return (total <= 8 and log[-1]["jev_calls"] == total
            and len(ask.asked()) == k and len(log[-1]["overflow"]) == 40 - k)


def prop_envelope_zero_calls(rtc_m, rtd_m):
    """A machine envelope: compiled triggers only, zero Jev requests, and the
    partner-prompt cues do not run on its boilerplate."""
    cue = {"kind": "prompt_regex", "pattern": r"\berror\b", "packs": ["governance-rules"],
           "rule_ids": ["aaaa0003"], "source": "prompt_cue"}
    ask, rank = Asker(0.9), Ranker()
    with tempfile.TemporaryDirectory() as tmp:
        table = table_for(compiled_doc(), tmp, extra=[cue])
        out = run(rtd_m, NOTIFICATION, tmp, ask=ask, rank=rank, table=table)
    return ask.calls == [] and rank.calls == 0 and "aaaa0003" not in ids(out)


class ChoiceClient(Client):
    @staticmethod
    def choice(instructions, options):
        return {"type": "choice", "options": options}


def prop_default_rank(rtc_m, rtd_m):
    """The REAL default ranker (jev_rule_select's ranking Choice), driven
    through advise() with only the judge module stubbed: exactly one ranking
    request, its top choices are what gets judged, and the ranking model is
    carried on what it returns. The injected fake ranker elsewhere in this
    suite hid a NameError here that made every live ranking fail before
    reaching Jev."""
    requests = []
    model = []

    class StubJudge:
        JudgeUnavailable = RuntimeError

        @staticmethod
        def judge(subject, questions, **kwargs):
            requests.append(sorted(questions))
            order = sorted(r["id"] for r in ROSTER if r["id"] != "aaaa0001")
            order.sort(key=lambda rule_id: (rule_id != "ffff0039", rule_id))
            return {"answers": {"rank": {"probabilities": {
                rule_id: 1.0 / (i + 1) for i, rule_id in enumerate(order)}}},
                "model": "stub-ranker"}

        @staticmethod
        def record(*args, **kwargs):
            return None

    real_sibling = rtd_m._sibling

    def sibling(name):
        module = real_sibling(name)
        if name == "jev_rule_select":
            inner = module._sibling
            module._sibling = lambda n: StubJudge if n == "jev_judge" else inner(n)
        return module

    rtd_m._sibling = sibling
    try:
        ask = Asker(0.1)
        with tempfile.TemporaryDirectory() as tmp:
            rtd_m.advise("git push please", session_id=None, now=1.0,
                         triggers_path=table_for(compiled_doc(), tmp), compiled=compiled_doc(),
                         rules=ROSTER, ask=ask, client=ChoiceClient, rank=None,
                         delivered_cache=os.path.join(tmp, "d"), envelope=False,
                         log_path=os.path.join(tmp, "log.jsonl"))
            row = json.loads(Path(tmp, "log.jsonl").read_text().splitlines()[-1])
        bound, _ = rtd_m.judge_budgeted("git push please", ROSTER[1:], [],
                                        ask=Asker(0.9), client=ChoiceClient)
        model.extend({r["ranking_model"] for r in bound.values()})
    except Exception:
        return False
    finally:
        rtd_m._sibling = real_sibling
    return (requests == [["rank"]] * 2 and row["rank_status"] == "ok"
            and "ffff0039" in ask.asked() and row["jev_calls"] == 1 + len(ask.calls)
            and model == ["stub-ranker"])


def prop_ranking_fails_binding_up(rtc_m, rtd_m):
    """Ranking unavailable, binding up: the budget still buys fresh
    single-rule judgments, of the rules a deterministic word-overlap pick
    chooses (ties by rule id), and the outage is logged as such."""
    ask, rank = Asker(0.9), Ranker(fail=True)
    with tempfile.TemporaryDirectory() as tmp:
        out = run(rtd_m, "an unrelated topic, plainly", tmp, ask=ask, rank=rank)
        row = json.loads(Path(tmp, "log.jsonl").read_text().splitlines()[-1])
    expected = [f"ffff{i:04d}" for i in range(rtd_m.BIND_TOP_K)]
    return (ask.calls == expected and row["rank_status"] == "unavailable_overlap_fallback"
            and row["jev_calls"] == 1 + rtd_m.BIND_TOP_K
            and all(r["ranking_model"] is None for r in out) and bool(out))


def prop_none_binds_is_ok(rtc_m, rtd_m):
    """A ranking that answers "no rule binds" is a successful ranking with
    an empty shortlist: logged ok, one request, nothing judged."""
    jrs = rtd_m._sibling("jev_rule_select")

    class NoneJudge:
        @staticmethod
        def judge(subject, questions, **kwargs):
            return {"answers": {"rank": {"probabilities": {jrs.NONE_BIND: 0.97}}},
                    "model": "stub-ranker"}

    real_sibling = rtd_m._sibling

    def sibling(name):
        module = real_sibling(name)
        if name == "jev_rule_select":
            inner = module._sibling
            module._sibling = lambda n: NoneJudge if n == "jev_judge" else inner(n)
        return module

    rtd_m._sibling = sibling
    try:
        ask = Asker(0.9)
        with tempfile.TemporaryDirectory() as tmp:
            rtd_m.advise("an unrelated topic, plainly", session_id=None, now=1.0,
                         triggers_path=table_for(compiled_doc(), tmp), compiled=compiled_doc(),
                         rules=ROSTER, ask=ask, client=ChoiceClient, rank=None,
                         delivered_cache=os.path.join(tmp, "d"), envelope=False,
                         log_path=os.path.join(tmp, "log.jsonl"))
            row = json.loads(Path(tmp, "log.jsonl").read_text().splitlines()[-1])
    finally:
        rtd_m._sibling = real_sibling
    return row["rank_status"] == "ok" and row["jev_calls"] == 1 and ask.calls == []


class SlowTransport:
    """A binding transport that takes `seconds` of a fake clock per request
    and says every rule binds, so what was judged before the deadline shows
    up in the result."""

    def __init__(self, seconds):
        self.seconds = seconds
        self.now = 0.0
        self.calls = []

    def clock(self):
        return self.now

    def __call__(self, subject, questions, *, rule_id=None, **kwargs):
        self.calls.append(rule_id)
        self.now += self.seconds
        return {"answers": {"binds": {"noul": 0.9}}, "model": "slow-stub"}


def prop_deadline(rtc_m, rtd_m):
    """A slow transport: binding stops once the deadline is near, what was
    judged before it is still returned, and the rest is reported unjudged.
    At 5 s a request against the 12 s clock: requests start at 0, 5 and 10 s
    (10 s leaves 2 s, above the 1 s floor); the 4th would start at 15 s."""
    slow = SlowTransport(5.0)
    selected, report = rtd_m.judge_budgeted(
        "anything", ROSTER, [], rank=Ranker(), ask=slow, client=Client, titles={},
        clock=slow.clock)
    expected = int((rtd_m.DEADLINE_SECONDS - rtd_m.MIN_CALL_SECONDS) // 5.0) + 1
    return (len(slow.calls) == expected < rtd_m.BIND_TOP_K
            and sorted(selected) == sorted(slow.calls) == sorted(report["judged"])
            and report["deadline_hit"] is True
            and len(report["unjudged"]) == rtd_m.BIND_TOP_K - expected
            and report["calls"] == 1 + expected)


def prop_deadline_keeps_matches(rtc_m, rtd_m):
    """A deadline already passed when the judgment starts: no Jev request at
    all, and the compiled match is still delivered and the log says why."""
    ask, rank = Asker(0.9), Ranker()
    with tempfile.TemporaryDirectory() as tmp:
        # "unrelated topic" overlaps every filler, so the fallback has rules
        # it would have judged: they must be reported unjudged, not asked.
        out = rtd_m.advise("please git push this unrelated topic", session_id="s1",
                           now=1000.0,
                           triggers_path=table_for(compiled_doc(), tmp),
                           compiled=compiled_doc(), rules=ROSTER, ask=ask, client=Client,
                           rank=rank, delivered_cache=os.path.join(tmp, "d.json"),
                           envelope=False, log_path=os.path.join(tmp, "log.jsonl"),
                           deadline=0.0)
        row = json.loads(Path(tmp, "log.jsonl").read_text().splitlines()[-1])
    return (ids(out) == ["aaaa0001"] and ask.calls == [] and rank.calls == 0
            and row["deadline_hit"] is True and row["jev_calls"] == 0
            and row["rank_status"] == "deadline_overlap_fallback"
            and row["bind_status"] == "deadline" and len(row["unjudged"]) > 0)


# ------------------------------------------------ the transport under a clock
# The #1281 review: a 429 with retry-after inside typesafe_client.ask escaped
# every deadline above it (3 retries, unbounded retry-after, each attempt at
# the full timeout). These drive the REAL client (EXTRA["tsc"], swapped for a
# mutant below) through a stub opener on a fake clock: nothing reaches the
# network and nothing really sleeps.
EXTRA = {"tsc": load("typesafe_client_t", TSC_PATH),
         "build": load("jev_build_advisory_t", BUILD_PATH)}


class FakeClock:
    def __init__(self):
        self.now = 100.0
        self.slept = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


class Transport:
    """Every attempt takes `took` seconds, then answers 429 with the given
    retry-after — or, with `hang`, uses its whole timeout and times out.
    Records (start time, timeout) per attempt."""

    def __init__(self, clock, *, retry_after="1", took=0.5, hang=False):
        self.clock, self.retry_after, self.took, self.hang = clock, retry_after, took, hang
        self.attempts = []

    def __call__(self, request, timeout=None):
        self.attempts.append((self.clock.now, timeout))
        if self.hang:
            self.clock.now += timeout
            raise urllib.error.URLError("timed out")
        self.clock.now += self.took
        raise urllib.error.HTTPError("https://jev.test", 429, "Too Many Requests",
                                     {"retry-after": self.retry_after}, io.BytesIO(b""))


class Via:
    """A client for jev_judge / the build advisory: the real ask(), served by
    a stub transport."""

    def __init__(self, tsc_m, opener):
        self.tsc_m, self.opener = tsc_m, opener

    def __getattr__(self, name):
        return getattr(self.tsc_m, name)

    def ask(self, state, questions, **kwargs):
        kwargs.pop("api_key", None)
        return self.tsc_m.ask(state, questions, api_key="k", opener=self.opener,
                              calls_log=os.devnull, **kwargs)


def _on_clock(fn):
    """Run fn(clock, tsc_m) with the client's time module on a fake clock."""
    tsc_m, clock = EXTRA["tsc"], FakeClock()
    real = tsc_m.time
    tsc_m.time = SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep,
                                 time=clock.monotonic)
    try:
        return fn(clock, tsc_m)
    finally:
        tsc_m.time = real


def _in_time(transport, deadline):
    return all(start + timeout <= deadline + 1e-9 for start, timeout in transport.attempts)


def prop_ask_honours_deadline(rtc_m, rtd_m):
    """typesafe_client.ask with a deadline: a retry-after past the deadline
    stops the retries (no sleep); a short one is honoured, but every attempt's
    timeout and every sleep fits inside the deadline."""
    def long_wait(clock, tsc_m):
        t = Transport(clock, retry_after="7")
        deadline = clock.now + 5
        try:
            tsc_m.ask("s", {"q": tsc_m.noul("?")}, api_key="k", opener=t,
                      calls_log=os.devnull, timeout=20, retries=3, deadline=deadline)
            return False
        except tsc_m.TypeSafeError:
            pass
        return (len(t.attempts) == 1 and clock.slept == [] and clock.now <= deadline
                and _in_time(t, deadline))

    def short_wait(clock, tsc_m):
        t = Transport(clock, retry_after="1")
        deadline = clock.now + 5
        try:
            tsc_m.ask("s", {"q": tsc_m.noul("?")}, api_key="k", opener=t,
                      calls_log=os.devnull, timeout=20, retries=3, deadline=deadline)
            return False
        except tsc_m.TypeSafeError:
            pass
        return len(t.attempts) > 1 and clock.now <= deadline and _in_time(t, deadline)

    return _on_clock(long_wait) and _on_clock(short_wait)


def _judged_once(call):
    """A 429 with a 1 s retry-after, 10 s of deadline: exactly one attempt,
    no sleep, inside the deadline."""
    def run_it(clock, tsc_m):
        t = Transport(clock, retry_after="1")
        deadline = clock.now + 10
        try:
            call(Via(tsc_m, t), deadline)
            return False
        except Exception:
            pass
        return (len(t.attempts) == 1 and clock.slept == [] and clock.now <= deadline
                and _in_time(t, deadline))
    return _on_clock(run_it)


def prop_bind_429_not_retried(rtc_m, rtd_m):
    return _judged_once(lambda via, deadline: rtd_m._default_bind(
        {"situation": "s", "rule_title": "t", "rule": "r", "rule_context": ""},
        {"binds": via.noul("?")}, via, timeout=10, deadline=deadline))


def prop_rank_429_not_retried(rtc_m, rtd_m):
    return _judged_once(lambda via, deadline: rtd_m._default_rank(
        "anything", ROSTER, rtd_m.BIND_TOP_K, via, timeout=10, deadline=deadline))


def prop_build_advisory_bounded(rtc_m, rtd_m):
    """The build advisory, as the prompt hook calls it (defaults): a hanging
    transport costs one attempt of at most 6 s; a 429 is not retried."""
    build_m = EXTRA["build"]

    def hanging(clock, tsc_m):
        t = Transport(clock, hang=True)
        start = clock.now
        try:
            build_m.advise("please build the deal room panel", client=Via(tsc_m, t))
            return False
        except Exception:
            pass
        return len(t.attempts) == 1 and t.attempts[0][1] <= 6.0 and clock.now - start <= 6.0

    def limited(clock, tsc_m):
        t = Transport(clock, retry_after="1")
        try:
            build_m.advise("please build the deal room panel", client=Via(tsc_m, t))
            return False
        except Exception:
            pass
        return len(t.attempts) == 1 and clock.slept == []

    return _on_clock(hanging) and _on_clock(limited)


def prop_dedupe(rtc_m, rtd_m):
    with tempfile.TemporaryDirectory() as tmp:
        first = run(rtd_m, "a vendor intro", tmp)
        second = run(rtd_m, "a vendor intro", tmp, now=1100.0)
        later = run(rtd_m, "a vendor intro", tmp, now=1000.0 + rtd_m.DEDUPE_TTL_SECONDS + 1)
    return ids(first) == ["aaaa0002"] and second == [] and ids(later) == ["aaaa0002"]


def prop_no_session_no_pooling(rtc_m, rtd_m):
    """No session id: nothing to dedupe against, so every message delivers
    afresh and no shared default key is ever written."""
    with tempfile.TemporaryDirectory() as tmp:
        first = run(rtd_m, "a vendor intro", tmp, session=None)
        again = run(rtd_m, "a vendor intro", tmp, session="  ", now=1010.0)
        wrote = os.path.exists(os.path.join(tmp, "d.json"))
    return ids(first) == ["aaaa0002"] and ids(again) == ["aaaa0002"] and not wrote


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
    "a missing trigger table still judges a human prompt, within budget": prop_fail_open,
    "residual and stale rules are judged on every human prompt": prop_residual_every_human_prompt,
    "a human prompt never costs more than MAX_JEV_CALLS requests": prop_budget_cap,
    "a machine envelope costs zero Jev requests": prop_envelope_zero_calls,
    "the real default ranker makes one request and its choices are judged":
        prop_default_rank,
    "ranking unavailable, binding up: rules are still judged fresh": prop_ranking_fails_binding_up,
    "a none-binds ranking is ok with an empty shortlist": prop_none_binds_is_ok,
    "binding stops at the deadline and keeps what was judged": prop_deadline,
    "a passed deadline makes no request and still delivers matches": prop_deadline_keeps_matches,
    "ask() honours an absolute deadline across 429 retries": prop_ask_honours_deadline,
    "a 429 on a binding request is not retried": prop_bind_429_not_retried,
    "a 429 on the ranking request is not retried": prop_rank_429_not_retried,
    "the build advisory is one attempt of at most 6 s": prop_build_advisory_bounded,
    "a rule already delivered this session is not resent inside the window": prop_dedupe,
    "no session id means no dedupe and no shared key": prop_no_session_no_pooling,
    "coverage flags a triggered rule with no trigger": prop_coverage_flags_empty_trigger,
    "a re-taught rule is detected as stale": prop_stale_detected,
    "the surfacing floor is inclusive at 0.5 and excludes below it": prop_surface_floor,
}

for name, prop in PROPERTIES.items():
    check(f"property holds: {name}", prop(rtc, rtd))

# ---------------------------------------------------------------- direct cases

check("the hard budget is one ranking plus BIND_TOP_K single-rule requests, and is 8",
      rtd.MAX_JEV_CALLS == 1 + rtd.BIND_TOP_K == 8)

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
check("a stale rule is not matched on old triggers; it is judged instead",
      "aaaa0002" in ask.asked()
      and [r for r in out if r["id"] == "aaaa0002"][0]["source"] == "stale_judged", out)

with tempfile.TemporaryDirectory() as tmp:
    out = run(rtd, "please git push", tmp, ask=Asker(fail=True))
check("failed binding requests still return what matched",
      ids(out) == ["aaaa0001"], out)

with tempfile.TemporaryDirectory() as tmp:
    stale_doc = compiled_doc()
    stale_doc["rules"]["aaaa0002"]["statement_sha256"] = "0" * 64
    ask, rank = Asker(0.1), Ranker(fail=True)
    run(rtd, "anything", tmp, ask=ask, rank=rank, doc=stale_doc)
    row = json.loads(Path(tmp, "log.jsonl").read_text().splitlines()[-1])
check("a failed ranking still judges stale rules and is counted",
      "aaaa0002" in ask.asked() and ask.calls[0] == "aaaa0002"
      and row["rank_status"] == "unavailable_overlap_fallback"
      and row["jev_calls"] == 1 + len(ask.calls) <= rtd.MAX_JEV_CALLS, row)

pool = [{"id": "bbbb0002", "statement": "Nothing in common."},
        {"id": "bbbb0001", "statement": "Also nothing."},
        {"id": "bbbb0003", "statement": "A zebra rule."}]
check("the overlap fallback reads compiled keywords, then breaks ties by rule id",
      rtd._overlap_rank("zebra crossing", pool, 7, {"bbbb0002": ["zebra crossing"],
                                                     "bbbb0001": ["crossing"]})
      == ["bbbb0002", "bbbb0001", "bbbb0003"])

with tempfile.TemporaryDirectory() as tmp:
    ask, rank = Asker(0.1), Ranker()
    run(rtd, "anything", tmp, ask=ask, rank=rank)
check("the ranking's top BIND_TOP_K are judged, one rule per request",
      rank.calls == 1 and len(ask.calls) == rtd.BIND_TOP_K == len(ask.asked())
      and all(set(subject) == {"situation", "rule_title", "rule", "rule_context"}
              for subject in ask.subjects), ask.calls)

with tempfile.TemporaryDirectory() as tmp:
    ask, rank = Asker(0.1), Ranker()
    run(rtd, "anything", tmp, ask=ask, rank=rank, rules=RULES)
check("a roster within capacity is judged whole without a ranking request",
      rank.calls == 0 and ask.asked() == {"aaaa0001", "aaaa0002", "aaaa0003"}, ask.calls)

with tempfile.TemporaryDirectory() as tmp:
    structural = {"kind": "prompt_regex", "pattern": r"^\s*<task-notification>",
                  "packs": ["governance-rules"], "rule_ids": ["aaaa0003"],
                  "source": "structural_extra"}
    table = table_for(compiled_doc(), tmp, extra=[structural])
    out = run(rtd, NOTIFICATION, tmp, table=table)
check("a reviewed structural trigger delivers on an envelope without a model",
      [r for r in out if r["id"] == "aaaa0003"][0]["binding_model"] == "structural-trigger", out)

cue = {"kind": "prompt_regex", "pattern": r"\bhttps?://\S+", "packs": ["vendor-intros"],
       "rule_ids": ["aaaa0002"], "source": "prompt_cue"}
with tempfile.TemporaryDirectory() as tmp:
    human_out = run(rtd, "here is the link https://x.com/a/b", tmp,
                    table=table_for(compiled_doc(), tmp, extra=[cue]))
check("a reviewed partner-prompt cue delivers on a human prompt",
      [r for r in human_out if r["id"] == "aaaa0002"][0]["binding_model"]
      == "reviewed-prompt-cue", human_out)

with tempfile.TemporaryDirectory() as tmp:
    blocker = os.path.join(tmp, "file")
    Path(blocker).write_text("x", encoding="utf-8")
    out = run(rtd, "git push now", tmp, caches=os.path.join(blocker, "d.json"))
check("an unwritable cache still delivers", ids(out)[:1] == ["aaaa0001"], out)

many = [dict(RULES[1], id=f"bbbb{i:04d}") for i in range(8)]
doc_many = rtc.document([entry(r, keywords={"vendor": 0.6 + i / 100})
                         for i, r in enumerate(many)])
with tempfile.TemporaryDirectory() as tmp:
    out = run(rtd, "vendor", tmp, doc=doc_many, rules=many, table=table_for(doc_many, tmp))
check("at most MAX_SURFACED rules per message, strongest first",
      len(out) == rtd.MAX_SURFACED and out[0]["id"] == "bbbb0007", out)

# The committed table's partner-prompt cues, against the prompts they came from.
committed_rows = rtd.prompt_rows()
for prompt, rule_id in (
        ("Here’s the article link\n\nhttps://x.com/av1dlive/status/2102802621664985241", "6437ae15"),
        ("Here’s the article link\n\nhttps://x.com/av1dlive/status/2102802621664985241", "57d13061"),
        ("Just click my Carr.us@gmail account. The password is autofilled", "c66dc739"),
        ("It appears that flash got stuck on test 6", "d9ce2b08"),
        ("it didnt ask, it just said no such file", "d9ce2b08"),
        ("look in my email folder for sapala", "49533583")):
    check(f"committed cue delivers {rule_id} on its logged prompt",
          rule_id in rtd.match(prompt, committed_rows or [], human=True), prompt[:40])
for banner in ("Last login: Thu Sep 24 11:01:21 on ttys001\nbooko@Joes-MacBook-Pro ~ % ls",
               "the file to dells computer? or the command\n\n"
               "Last login: Wed Sep 23 21:20:47 on ttys001\nbooko@J"):
    check("a pasted terminal login banner does not fire the password cue",
          "c66dc739" not in rtd.match(banner, committed_rows or [], human=True), banner[:40])
check("the password cue still fires on a real login mention beside a banner",
      "c66dc739" in rtd.match("Last login: Thu Sep 24 on ttys001\nit wants my password again",
                              committed_rows or [], human=True))
check("committed partner-prompt cues never run on an envelope",
      not any(source == "prompt_cue" for sources in rtd.match(
          NOTIFICATION, committed_rows or [], human=False).values() for source in sources))

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
    ("a missing trigger table still judges a human prompt, within budget", RTD_PATH,
     ("    if human:\n        unmatched", "    if human and rows is not None:\n        unmatched")),
    # Stale rules dropped from the always-judged set.
    ("residual and stale rules are judged on every human prompt", RTD_PATH,
     ('always = sorted(rule["id"] for rule in unmatched if rule["id"] in stale)',
      'always = sorted(rule["id"] for rule in [] if rule["id"] in stale)')),
    # The first cut's rule: skip a stale rule whose pack already had a hit.
    ("residual and stale rules are judged on every human prompt", RTD_PATH,
     ('always = sorted(rule["id"] for rule in unmatched if rule["id"] in stale)',
      'always = sorted(rule["id"] for rule in unmatched if rule["id"] in stale and not '
      'set(rule["packs"]) & {p for s in selected for p in by_id[s]["packs"]})')),
    # Residual rules kept out of the ranking (so never judged).
    ("residual and stale rules are judged on every human prompt", RTD_PATH,
     ('unmatched = [rule for rule in rules if rule["id"] not in selected]',
      'unmatched = [rule for rule in rules if rule["id"] not in selected and '
      '(entries.get(rule["id"]) or {}).get("mode") != "residual"]')),
    ("a human prompt never costs more than MAX_JEV_CALLS requests", RTD_PATH,
     ("if len(always) > BIND_TOP_K:", "if False:")),
    ("a machine envelope costs zero Jev requests", RTD_PATH,
     ("human = not (_is_envelope(situation) if envelope is None else envelope)",
      "human = True")),
    ("a machine envelope costs zero Jev requests", RTD_PATH,
     ('if not human and row.get("source") in HUMAN_ONLY_SOURCES:', "if False:")),
    ("the real default ranker makes one request and its choices are judged", RTD_PATH,
     ('return [rule_id for rule_id, _ in ranked][:limit], 1, answer.get("model") or "jev"',
      'return [rule_id for rule_id, _ in ranked][:limit], 1, None')),
    # The e7f4bf80 behaviour: an unavailable ranking judged nothing new.
    ("ranking unavailable, binding up: rules are still judged fresh", RTD_PATH,
     ("ranked = _overlap_rank(text, pool, room, keywords or {})", "ranked = []")),
    # narrow()'s conflation: a none-binds answer treated as an outage.
    ("a none-binds ranking is ok with an empty shortlist", RTD_PATH,
     ("    if not isinstance(probabilities, dict) or not probabilities:",
      "    if not isinstance(probabilities, dict) or not probabilities or "
      "set(probabilities) <= {jrs.NONE_BIND}:")),
    # The deadline removed from the binding loop.
    ("binding stops at the deadline and keeps what was judged", RTD_PATH,
     ("        if left < MIN_CALL_SECONDS:", "        if False:")),
    # The deadline removed from the ranking request.
    ("a passed deadline makes no request and still delivers matches", RTD_PATH,
     ("        elif deadline - clock() < MIN_CALL_SECONDS:", "        elif False:")),
    # Retries restored on the binding and the ranking requests.
    ("a 429 on a binding request is not retried", RTD_PATH,
     ('extra = {"deadline": deadline, "retries": 0}', 'extra = {"deadline": deadline}')),
    ("a 429 on the ranking request is not retried", RTD_PATH,
     ('extra = {"retries": 0, "deadline": deadline}', 'extra = {"deadline": deadline}')),
    # ask() sleeping past the deadline, and attempts at the full timeout.
    ("ask() honours an absolute deadline across 429 retries", TSC_PATH,
     ("if deadline is not None and delay >= deadline - time.monotonic():", "if False:")),
    ("ask() honours an absolute deadline across 429 retries", TSC_PATH,
     ("attempt_timeout = min(timeout, remaining)", "attempt_timeout = timeout")),
    # The build advisory with retries restored, and with the old 20 s cap.
    ("the build advisory is one attempt of at most 6 s", BUILD_PATH,
     ("            retries=0,\n", "")),
    ("the build advisory is one attempt of at most 6 s", BUILD_PATH,
     ("TIMEOUT_SECONDS = 6.0", "TIMEOUT_SECONDS = 20.0")),
    ("a rule already delivered this session is not resent inside the window", RTD_PATH,
     ("if not (isinstance(recent.get(rule_id), (int, float))",
      "if True or not (isinstance(recent.get(rule_id), (int, float))")),
    ("no session id means no dedupe and no shared key", RTD_PATH,
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
for number, (prop_name, path, substitution) in enumerate(MUTANTS):
    mutated = load(f"mutant_{number}", path, replace=substitution)
    rtc_m = mutated if path == RTC_PATH else rtc
    rtd_m = mutated if path == RTD_PATH else rtd
    saved = dict(EXTRA)
    if path == TSC_PATH:
        EXTRA["tsc"] = mutated
    if path == BUILD_PATH:
        EXTRA["build"] = mutated
    try:
        survived = PROPERTIES[prop_name](rtc_m, rtd_m)
    except Exception:
        survived = False
    finally:
        EXTRA.update(saved)
    check(f"mutant {number} is killed: {prop_name}", not survived)

if FAILURES:
    print("rule-trigger-compile-selftest: FAIL")
    for line in FAILURES:
        print("  " + line)
    raise SystemExit(1)
print("rule-trigger-compile-selftest: all cases passed")
