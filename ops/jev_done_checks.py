"""jev_done_checks.py — the BEFORE-"done" checks: is the work actually finished?

WHY THESE FOUR, TOGETHER. A turn can look finished and not be: tests that pass
without proving anything (#13), a final message that claims success past what
the evidence shows (#14), a diff nobody with the right eyes looked at (#17), or
a claim about CARR doctrine that the doctrine itself does not support (#24).
None of these are things a grep can decide — each is a judgment about whether
text supports text — so each asks Jev, narrowly, once a cheap deterministic
trigger says it is worth asking.

A CHECK, DELIBERATELY, NOT AN ACTING GATE. Every function here only REPORTS:
{"check", "verdict", "confidence", "escalate", "detail"}. Nothing here blocks a
turn, deletes a file, or refuses a commit — that stays true after Joe's
2026-09-24 ruling (decision 5ec806a4, "every jev check in the system too is
not a shadow") retired shadow as a default holding pattern, because these
checks were never held back by that default in the first place: a caller (a
hook, a gate, a dispatcher) reads the verdict and decides what, if anything,
to do about it, and this module has no acting behaviour of its own to turn
on. ops/jev_judge.record() still writes every judgment to out/jev-judge.jsonl
beside whatever the caller ends up doing, so a threshold can be measured on
real traffic — that discipline is now the permanent audit trail rather than a
precondition for acting. See ops/jev_judge.py for the current framing.

TALK TO JEV THROUGH ops/jev_judge.py ONLY. Every check builds its questions
with the typesafe client's noul/choice/score helpers (ops/typesafe_client.py)
and sends them in ONE request per check — several independent questions about
one subject, batched, per the vendor's own measured 12.2x efficiency finding
that ops/jev_judge.py documents at length. `client` is that typesafe-client
object; pass a fake in tests, leave it None in production and the real one
loads lazily. `judge_module` is an additional, undocumented-to-callers escape
hatch that stands in for ops/jev_judge.py itself in the offline selftest, so
the selftest never has to touch the real shadow log or the network; production
callers never pass it and get the real module.

A LIBRARY, NOT A SCRIPT. No shebang line and no construct that marks a module
as a registered entrypoint in the sealed source inventory — see
ops/typesafe_client.py's docstring for exactly why that matters and why even
describing the construct in a comment is enough to trip the detector. Every
public function below NEVER raises: any failure, including Jev being
unreachable, is caught and returned as verdict "unavailable" with the error
message folded into `detail`.

detail["advice"] IS THE ONE THING A CALLER SHOWS WITHOUT READING THE REST. Any
verdict a caller would want to act on (weak, unsupported, contradicted,
needs_review) carries a short plain-English sentence there — the kind of line
a hook dispatcher can surface to the model with no further formatting. Verdicts
that need no action (solid, supported, ok, no_claim, not_triggered) leave it
out.

PER-CHECK THRESHOLDS ARE PROVISIONAL. Every numeric constant below is a
starting point, named at the top of its section, to be replaced once
out/jev-judge.jsonl has real traffic to measure against — the same discipline
ops/jev_requirements.py and ops/jev_handoff.py already follow.
"""

import hashlib
import importlib.util
import json
import os
import re
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TIMEOUT_SECONDS = 15.0

# Any noul probability inside this band is treated as "Jev could not really
# tell" rather than as a soft yes or a soft no; callers get escalate=True
# instead of a guess. Nouls carry no separate confidence, so this band is the
# stand-in for one.
AMBIGUOUS_LO = 0.35
AMBIGUOUS_HI = 0.65


def _sibling(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(REPO, "ops", f"{name}.py"))
    if spec is None or spec.loader is None:
        raise ImportError(name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _result(check_id, verdict, *, confidence=None, escalate=False, detail=None, advice=None):
    out = {"check": check_id, "verdict": verdict, "confidence": confidence,
           "escalate": bool(escalate), "detail": dict(detail or {})}
    if advice:
        out["detail"]["advice"] = advice
    return out


def _text_ref(text, n=12):
    return hashlib.sha1((text or "").encode("utf-8", "replace")).hexdigest()[:n]


def _safe_id(text, n=60):
    return re.sub(r"[^A-Za-z0-9_]", "_", text)[:n] or "x"


def _noul(answer, qid):
    try:
        prob = float(answer["answers"][qid]["noul"])
    except Exception:
        return None
    return prob if 0.0 <= prob <= 1.0 else None


# =========================================================================
# #13 — test-quality check
# =========================================================================
#
# Trigger: the caller determined the turn added or edited a test file and
# hands over its source. A second, cheap trigger runs here too: text with no
# test-shaped marker at all (no `def test_`, no test class, no framework
# assertion) costs no Jev call, because there is nothing to judge.

TEST_MARKERS = re.compile(
    r"\bdef\s+test_\w+|\bclass\s+\w*Tests?\b|@[Tt]est\b|\bit\(|\bdescribe\(|\bassert\b|\bexpect\(", re.I)

MAX_TEST_SOURCE_CHARS = 8000
MAX_CODE_UNDER_TEST_CHARS = 8000
MAX_TASK_TEXT_CHARS = 3000

ASSERTS_LOW = 0.50
TAUTOLOGICAL_HIGH = 0.60
HAPPY_PATH_HIGH = 0.60
EDGE_COVERAGE_LOW = 0.40

TEST_QUALITY_QUESTIONS = {
    "asserts_behavior": (
        "Do the tests in `test_source` assert on the actual behaviour, return "
        "value, state change or side effect of `code_under_test` (a specific "
        "expected value or state), rather than merely checking that a call "
        "completed without raising or that some result is truthy/not-None?",
        "At least one assertion checks a specific expected value, state "
        "change, or side effect.",
        "Assertions only check that the call ran, or that a result is merely "
        "truthy/not-None, with no specific expected value."),
    "covers_stated_edge_cases": (
        "`task_text` is the request that produced this change, when known. "
        "Does `test_source` include a test for at least one edge case that "
        "`task_text` explicitly asks for or clearly implies (an error "
        "condition, a boundary value, empty/None input, a concurrency case, "
        "and so on)? Answer yes when `task_text` is empty or names no such "
        "edge case.",
        "`test_source` covers an edge case `task_text` calls for, or "
        "`task_text` names no specific edge case.",
        "`task_text` calls for a specific edge case that `test_source` does "
        "not exercise."),
    "could_pass_with_wrong_impl": (
        "Could `test_source` pass even against a WRONG or incomplete "
        "implementation of `code_under_test` — because an assertion is "
        "tautological (comparing a value to itself, asserting a literal "
        "constant with no real check), or because the function under test is "
        "mocked, stubbed, or monkey-patched out rather than actually "
        "exercised?",
        "At least one test would pass regardless of whether `code_under_test` "
        "behaves correctly.",
        "Every test would fail if `code_under_test` were wrong in a way "
        "relevant to what that test asserts."),
    "happy_path_only": (
        "Do the tests in `test_source` cover ONLY the happy path, with no "
        "assertion for an error, invalid input, or boundary condition, even "
        "though `code_under_test` contains such a branch?",
        "No test exercises an error, invalid-input, or boundary branch that "
        "`code_under_test` contains.",
        "At least one test exercises an error, invalid-input, or boundary "
        "branch, or `code_under_test` has no such branch."),
}


def check_test_quality(test_source, code_under_test, task_text, *, client=None, judge_module=None):
    """Does the diff's test change actually prove the behaviour it claims to?

    `code_under_test` may be "" — the caller does not always have it on hand —
    and every question is written to still make sense without it.
    """
    check_id = "test_quality"
    try:
        if not test_source or not test_source.strip():
            return _result(check_id, "not_triggered", detail={"reason": "no test source given"})
        if not TEST_MARKERS.search(test_source):
            return _result(check_id, "not_triggered",
                           detail={"reason": "test_source has no test-shaped marker"})

        jj = judge_module or _sibling("jev_judge")
        tsc = client or jj._client()
        questions = {qid: tsc.noul(text, true=true, false=false)
                     for qid, (text, true, false) in TEST_QUALITY_QUESTIONS.items()}
        state = {
            "test_source": test_source[:MAX_TEST_SOURCE_CHARS],
            "code_under_test": (code_under_test or "")[:MAX_CODE_UNDER_TEST_CHARS],
            "task_text": (task_text or "")[:MAX_TASK_TEXT_CHARS],
        }
        answer = jj.judge(state, questions, client=client, timeout=TIMEOUT_SECONDS)
        jj.record("supervise.test_quality", _text_ref(test_source), answer, None)

        probs = {qid: _noul(answer, qid) for qid in TEST_QUALITY_QUESTIONS}

        red_flags = []
        if probs["asserts_behavior"] is not None and probs["asserts_behavior"] < ASSERTS_LOW:
            red_flags.append("weak_assertions")
        if (probs["could_pass_with_wrong_impl"] is not None
                and probs["could_pass_with_wrong_impl"] >= TAUTOLOGICAL_HIGH):
            red_flags.append("tautological_or_mocked")
        if probs["happy_path_only"] is not None and probs["happy_path_only"] >= HAPPY_PATH_HIGH:
            red_flags.append("happy_path_only")
        if (probs["covers_stated_edge_cases"] is not None
                and probs["covers_stated_edge_cases"] < EDGE_COVERAGE_LOW):
            red_flags.append("missing_stated_edge_case")

        verdict = "weak" if red_flags else "solid"
        escalate = any(p is not None and AMBIGUOUS_LO <= p <= AMBIGUOUS_HI
                       for p in (probs["asserts_behavior"], probs["could_pass_with_wrong_impl"]))
        advice = None
        if red_flags:
            advice = "test changes look weak (" + ", ".join(red_flags) + ") — read them before trusting a green run"
        return _result(check_id, verdict, confidence=None, escalate=escalate,
                       detail={"probabilities": probs, "red_flags": red_flags}, advice=advice)
    except Exception as exc:
        return _result(check_id, "unavailable", detail={"error": str(exc)[:300]})


# =========================================================================
# #14 — "done" claim check
# =========================================================================
#
# Trigger: the final assistant message contains a completion word at all. No
# such word, no call — most turns end without claiming anything.

DONE_CLAIM = re.compile(
    r"\b(done|fixed|passes|passing|works|working|complete(?:d)?|resolved|finished|"
    r"all\s+set|should\s+be\s+good|no\s+more\s+errors|no\s+failures)\b", re.I)

EVIDENCE_FIELDS = ("test_command", "test_output", "test_exit_code", "diff_stat")
MAX_MESSAGE_CHARS = 4000
MAX_EVIDENCE_FIELD_CHARS = 4000

SUPPORT_HIGH = 0.60
SUPPORT_LOW = 0.40
OMITTED_FAILURE_HIGH = 0.50


def check_done_claim(final_message, evidence, *, client=None, judge_module=None):
    """Does the evidence back up a completion claim in the final message?

    `evidence` carries whichever of test_command / test_output / test_exit_code
    / diff_stat the caller has; missing fields are simply left out of the call.
    """
    check_id = "done_claim"
    try:
        if not final_message or not DONE_CLAIM.search(final_message):
            return _result(check_id, "no_claim")

        ev = {k: v for k, v in (evidence or {}).items()
              if k in EVIDENCE_FIELDS and v not in (None, "")}
        for k, v in list(ev.items()):
            ev[k] = str(v)[:MAX_EVIDENCE_FIELD_CHARS]

        jj = judge_module or _sibling("jev_judge")
        tsc = client or jj._client()
        questions = {
            "claims_supported": tsc.noul(
                "`final_message` claims the work is done, fixed, passing, working "
                "or complete. Does `evidence` (whichever of test_command, "
                "test_output, test_exit_code, diff_stat is present) support that "
                "claim?",
                true="The evidence is consistent with the claim: for example a "
                     "zero test_exit_code, test_output showing the relevant tests "
                     "passing, or a diff_stat matching what was claimed done.",
                false="The evidence is missing, insufficient, or contradicts the "
                      "claim."),
            "evidence_shows_omitted_failure": tsc.noul(
                "Does `evidence` show a failure, error, non-zero exit code, or "
                "unresolved problem that `final_message` does not mention or "
                "acknowledge?",
                true="`evidence` contains a failure, error or non-zero exit that "
                     "`final_message` is silent about.",
                false="`evidence` shows no such unmentioned failure, or there is "
                      "no evidence to check."),
        }
        state = {"final_message": final_message[:MAX_MESSAGE_CHARS], "evidence": ev}
        answer = jj.judge(state, questions, client=client, timeout=TIMEOUT_SECONDS)
        jj.record("supervise.done_claim", _text_ref(final_message), answer, None,
                  note={"evidence_fields": sorted(ev)})

        supported = _noul(answer, "claims_supported")
        omitted = _noul(answer, "evidence_shows_omitted_failure")

        if omitted is not None and omitted >= OMITTED_FAILURE_HIGH:
            verdict = "unsupported"
        elif supported is not None and supported >= SUPPORT_HIGH:
            verdict = "supported"
        elif supported is not None and supported < SUPPORT_LOW:
            verdict = "unsupported"
        else:
            verdict = "unsupported"

        escalate = ((supported is not None and AMBIGUOUS_LO <= supported <= AMBIGUOUS_HI) or
                    (omitted is not None and AMBIGUOUS_LO <= omitted <= AMBIGUOUS_HI))
        advice = None
        if verdict == "unsupported":
            if omitted is not None and omitted >= OMITTED_FAILURE_HIGH:
                advice = "message claims success but the evidence shows a failure it does not mention"
            else:
                advice = "message claims success but the evidence does not clearly back that up"
        return _result(check_id, verdict, confidence=None, escalate=escalate,
                       detail={"claims_supported": supported,
                               "evidence_shows_omitted_failure": omitted}, advice=advice)
    except Exception as exc:
        return _result(check_id, "unavailable", detail={"error": str(exc)[:300]})


# =========================================================================
# #17 — review triage
# =========================================================================
#
# Deterministic floor first, always: a path matching RISKY_PATH is "high" with
# no Jev call at all, because that judgment does not need to be asked — it is
# already the rule the caller wrote. Everything else rides one `score`
# question per file, ALL of them in one request.

RISKY_PATH = re.compile(r"auth|security|migrat|db/|payment|crypto|secret", re.I)

FILE_HEADER = re.compile(r"^diff --git a/(?P<a>.+?) b/(?P<b>.+?)$", re.M)
MAX_HUNK_CHARS = 3000
MAX_TRIAGE_FILES = 25

RISK_LEVELS = [
    "low: cosmetic, documentation, test-only, logging/formatting, or "
    "non-executable configuration; no authentication, authorization, "
    "security-control, input-validation, money/billing/payment, migration, "
    "or concurrency involvement.",
    "medium: ordinary application logic with no authentication, "
    "authorization, security-control, input-validation, money/billing, "
    "migration, or concurrency involvement.",
    "high: touches authentication, authorization, or other security "
    "controls; input validation; money, billing, or payment logic; a "
    "database migration or schema change; or concurrency/locking behaviour.",
]
RISK_RUBRIC = (
    "Rate the risk of this change using the level rubrics: a high-risk change "
    "touches auth/security/validation/money/migrations/concurrency; a "
    "medium-risk change is ordinary logic touching none of those; a low-risk "
    "change is cosmetic, docs, tests, logging, or config.")

HIGH_AT = 1.5
MED_AT = 0.5


def split_diff_by_file(diff_text):
    """A unified diff, split into {path: hunk text}. One group per `diff --git`."""
    if not diff_text or not diff_text.strip():
        return {}
    matches = list(FILE_HEADER.finditer(diff_text))
    if not matches:
        return {"(change)": diff_text[:MAX_HUNK_CHARS]}
    out = {}
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(diff_text)
        path = m.group("b") or m.group("a")
        out[path] = diff_text[start:end][:MAX_HUNK_CHARS]
    return out


# The Stop hook re-scores `git diff HEAD` at every Stop, and a diff nobody has
# touched since the last Stop is the same question with the same answer.
# Measured 2026-09-25 in out/jev-judge.jsonl: 223 review_triage calls, 219 of
# them repeats of an earlier subject, one unchanged path asked 220 times. The
# key is the exact state sent (hunks and task) plus the asking code, so a
# changed hunk, a different task or a code change always asks again.
REVIEW_CACHE_PATH = os.path.join(REPO, "out", "jev-review-triage-cache.json")
REVIEW_CACHE_SOURCES = ("ops/jev_done_checks.py", "ops/jev_judge.py",
                        "ops/typesafe_client.py", "ops/jev_verdict_cache.py")


def _review_answer(jj, state, questions, client, cache_path, now):
    """The judge's answer for this exact state, cached for identical repeats.

    Returns (answer, fresh). Any cache failure falls through to asking; an
    answer is stored only when every question came back with a score, so a
    partial reply is never replayed."""
    cache = entry_key = None
    if cache_path:
        try:
            cache = _sibling("jev_verdict_cache")
            entry_key = cache.key({"state": state, "questions": questions,
                                   "source": cache.source_digest(*REVIEW_CACHE_SOURCES)})
            cached = cache.get(cache_path, entry_key, now=now)
            if isinstance(cached, dict) and isinstance(cached.get("answers"), dict):
                return cached, False
        except Exception:
            cache = None
    answer = jj.judge(state, questions, client=client, timeout=TIMEOUT_SECONDS)
    answers = answer.get("answers") if isinstance(answer, dict) else None
    if (cache is not None and isinstance(answers, dict)
            and all(isinstance((answers.get(qid) or {}).get("score"), (int, float))
                    for qid in questions)):
        cache.put(cache_path, entry_key, answer, now=now)
    return answer, True


def triage_review(diff_text, task_text, *, client=None, judge_module=None,
                  cache_path=None, now=None):
    """Per-file risk for a diff. verdict "needs_review" if any file is high risk.

    Production callers (no injected judge or client) reuse the answer for a
    byte-identical diff and task inside the cache window; a test caches only
    when it names `cache_path`."""
    check_id = "review_triage"
    if cache_path is None and judge_module is None and client is None:
        cache_path = REVIEW_CACHE_PATH
    try:
        files = split_diff_by_file(diff_text)
        if not files:
            return _result(check_id, "not_triggered", detail={"reason": "empty diff"})
        files = dict(list(files.items())[:MAX_TRIAGE_FILES])

        results = {}
        to_judge = {}
        for path, chunk in files.items():
            if RISKY_PATH.search(path):
                results[path] = {"risk": "high", "source": "deterministic_floor"}
            else:
                to_judge[path] = chunk

        if to_judge:
            jj = judge_module or _sibling("jev_judge")
            tsc = client or jj._client()
            keys = {path: _safe_id(path) for path in to_judge}
            questions = {
                keys[path]: tsc.score(
                    f"{RISK_RUBRIC} The change is to path {path!r}, shown in "
                    f"`files.{keys[path]}`; `task`, when present, is the request "
                    "that produced it.",
                    RISK_LEVELS)
                for path, chunk in to_judge.items()
            }
            state = {"files": {keys[path]: chunk for path, chunk in to_judge.items()}}
            if task_text:
                state["task"] = task_text[:MAX_TASK_TEXT_CHARS]
            answer, fresh = _review_answer(jj, state, questions, client, cache_path, now)
            if fresh:
                # out/jev-judge.jsonl is the record of calls Jev answered; a
                # cache hit made no call and so writes no row.
                jj.record("supervise.review_triage", "|".join(sorted(to_judge))[:200], answer,
                          None, note={"file_count": len(to_judge)})
            for path in to_judge:
                body = (answer.get("answers") or {}).get(keys[path], {})
                value = body.get("score")
                confidence = body.get("confidence")
                value = float(value) if isinstance(value, (int, float)) else None
                confidence = float(confidence) if isinstance(confidence, (int, float)) else None
                if value is None:
                    risk = "medium"
                elif value >= HIGH_AT:
                    risk = "high"
                elif value >= MED_AT:
                    risk = "medium"
                else:
                    risk = "low"
                results[path] = {"risk": risk, "source": "jev", "score": value,
                                 "confidence": confidence}

        high_paths = [p for p, r in results.items() if r["risk"] == "high"]
        verdict = "needs_review" if high_paths else "ok"
        judged_confidences = [r["confidence"] for r in results.values()
                              if r.get("source") == "jev" and r.get("confidence") is not None]
        confidence = min(judged_confidences) if judged_confidences else None
        advice = None
        if high_paths:
            advice = (high_paths[0] + " is high risk — review it") if len(high_paths) == 1 else (
                f"{len(high_paths)} files are high risk, starting with {high_paths[0]} — review them")
        return _result(check_id, verdict, confidence=confidence, escalate=verdict == "needs_review",
                       detail={"files": results}, advice=advice)
    except Exception as exc:
        return _result(check_id, "unavailable", detail={"error": str(exc)[:300]})


# =========================================================================
# #24 — CARR fact check
# =========================================================================
#
# The caller retrieves the passages (search-doctrine, read-doctrine, however
# it found them); this only judges whether the ones it was handed back up the
# claim. No passages, no call — there is nothing to check the claim against.

MAX_CLAIM_CHARS = 2000
MAX_PASSAGES = 8
MAX_PASSAGE_CHARS = 2000
MAX_OPTION_PREVIEW_CHARS = 200

CONTRADICT_AT = 0.60
FACT_CONFIDENCE_FLOOR = 0.60


def fact_check(claim_text, doctrine_passages, *, client=None, judge_module=None):
    """Does a supplied doctrine passage support, contradict, or not address a claim?"""
    check_id = "fact_check"
    try:
        if not claim_text or not claim_text.strip():
            return _result(check_id, "unsupported", detail={"reason": "empty claim"})
        passages = [p for p in (doctrine_passages or [])
                    if isinstance(p, dict) and p.get("ref") and p.get("text")]
        if not passages:
            return _result(check_id, "unsupported",
                           detail={"reason": "no doctrine passages supplied"},
                           advice="no doctrine passages were supplied to check this claim against")
        passages = passages[:MAX_PASSAGES]

        jj = judge_module or _sibling("jev_judge")
        tsc = client or jj._client()

        refs = []
        state_passages = {}
        options = {"__none__": "No supplied passage addresses the claim."}
        for p in passages:
            ref = str(p["ref"])[:80]
            if ref in state_passages:
                continue
            refs.append(ref)
            text = str(p["text"])[:MAX_PASSAGE_CHARS]
            state_passages[ref] = text
            options[ref] = f"{ref}: {text[:MAX_OPTION_PREVIEW_CHARS]}"

        keys = {ref: _safe_id(ref) for ref in refs}
        questions = {
            "which_supports": tsc.choice(
                "Which passage in `passages`, if any, supports `claim`? Pick the "
                "single best match, or the none option when no supplied passage "
                "addresses the claim.",
                options),
        }
        for ref in refs:
            questions["contra_" + keys[ref]] = tsc.noul(
                f"Does the passage `passages.{keys[ref]}` directly contradict "
                "`claim`?",
                true="The passage states something incompatible with the claim.",
                false="The passage does not contradict the claim (it may "
                      "support it, be silent, or be unrelated).")

        state = {"claim": claim_text[:MAX_CLAIM_CHARS],
                 "passages": {keys[ref]: state_passages[ref] for ref in refs}}
        answer = jj.judge(state, questions, client=client, timeout=TIMEOUT_SECONDS)
        jj.record("supervise.fact_check", "|".join(refs)[:200], answer, None,
                  note={"claim": claim_text[:200]})

        contradicted_by = []
        for ref in refs:
            prob = _noul(answer, "contra_" + keys[ref])
            if prob is not None and prob >= CONTRADICT_AT:
                contradicted_by.append((ref, prob))
        contradicted_by.sort(key=lambda t: -t[1])

        choice_body = (answer.get("answers") or {}).get("which_supports", {})
        chosen = choice_body.get("choice")
        confidence = choice_body.get("confidence")
        confidence = float(confidence) if isinstance(confidence, (int, float)) else None

        if contradicted_by:
            advice = f"claim is contradicted by {contradicted_by[0][0]}"
            return _result(check_id, "contradicted", confidence=confidence, escalate=True,
                           detail={"contradicted_by": contradicted_by, "which_supports": chosen},
                           advice=advice)
        if chosen and chosen != "__none__" and (confidence is None or confidence >= FACT_CONFIDENCE_FLOOR):
            escalate = confidence is not None and confidence < FACT_CONFIDENCE_FLOOR
            return _result(check_id, "supported", confidence=confidence, escalate=escalate,
                           detail={"supporting_passage": chosen})
        return _result(check_id, "unsupported", confidence=confidence, escalate=False,
                       detail={"which_supports": chosen},
                       advice="no supplied passage clearly supports this claim")
    except Exception as exc:
        return _result(check_id, "unavailable", detail={"error": str(exc)[:300]})


# =========================================================================
# #10 — mid-task handoff pack
# =========================================================================
#
# Deterministic collection, deterministic budget check; Jev is only asked
# which collected items to drop when the deterministic collection is already
# over `max_chars`. This is the pack a session hands to Claude or Codex when
# a local model escalates, so it returns {"pack", "kept", "dropped"} rather
# than the {check, verdict, ...} shape the checks above use — there is no
# verdict to give, only a budget to fill with the most useful context.

ASSISTANT_TAIL_MESSAGES = 6
ASSISTANT_TEXT_CHARS = 1500
TRANSCRIPT_TAIL_BYTES = 2 * 1024 * 1024
FAILURE_CHARS = 3000
DIFF_CHARS_PER_FILE = 4000
JUDGE_ITEM_CHARS = 1200
MAX_HANDOFF_FILES = 20
HANDOFF_TIMEOUT_SECONDS = 20.0

RELEVANCE_QUESTION = (
    "`items.{key}` is one piece of context collected for a session picking up "
    "this task fresh, with no memory of the work so far. Is it likely needed "
    "to decide what to do next — the task itself, an in-progress change, a "
    "failure to fix, or a decision already made? Answer no for detail that is "
    "redundant with another item, or no longer relevant to finishing the "
    "task.")


def _content(rec):
    message = rec.get("message") if isinstance(rec.get("message"), dict) else {}
    return message.get("content", rec.get("content"))


def _assistant_text_blocks(rec):
    if rec.get("type") != "assistant":
        return []
    content = _content(rec)
    if not isinstance(content, list):
        return []
    return [b.get("text", "") for b in content
            if isinstance(b, dict) and b.get("type") == "text" and b.get("text")]


def tail_assistant_notes(transcript_path, *, tail_messages=ASSISTANT_TAIL_MESSAGES,
                         tail_bytes=TRANSCRIPT_TAIL_BYTES, per_note_chars=ASSISTANT_TEXT_CHARS):
    """The text of the last few assistant messages in a JSONL transcript, oldest first.

    Tail-reads the file, because a real transcript can be large and only the
    end of it is relevant to a mid-task handoff. Never raises: an unreadable
    or missing transcript yields an empty list.
    """
    if not transcript_path or not isinstance(transcript_path, str):
        return []
    try:
        with open(transcript_path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - tail_bytes))
            raw = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    notes = []
    for line in reversed(raw.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        blocks = _assistant_text_blocks(rec)
        if blocks:
            notes.append("\n".join(blocks)[:per_note_chars])
        if len(notes) >= tail_messages:
            break
    notes.reverse()
    return notes


def _git_diff(path, *, timeout=5.0, git_env_module=None):
    """`git diff HEAD -- path`, run from path's own directory. "" on any failure."""
    folder = os.path.dirname(path) or "."
    if not os.path.isdir(folder):
        return ""
    try:
        env = (git_env_module or _sibling("git_env")).scrubbed_env()
    except Exception:
        env = None
    try:
        run = subprocess.run(["git", "-C", folder, "diff", "--no-color", "HEAD", "--", path],
                             capture_output=True, text=True, timeout=timeout, env=env)
        return run.stdout if run.returncode == 0 else ""
    except Exception:
        return ""


def _collect_items(task_text, transcript_path, changed_paths, failure_output):
    items = []
    if task_text and task_text.strip():
        items.append({"id": "task", "text": "## Task\n" + task_text.strip()})
    for path in (changed_paths or [])[:MAX_HANDOFF_FILES]:
        diff = _git_diff(path)
        if diff.strip():
            items.append({"id": f"diff:{path}",
                          "text": f"## Diff: {path}\n" + diff[:DIFF_CHARS_PER_FILE]})
    if failure_output and str(failure_output).strip():
        items.append({"id": "last_failure",
                      "text": "## Last failure\n" + str(failure_output)[:FAILURE_CHARS]})
    for i, note in enumerate(tail_assistant_notes(transcript_path)):
        items.append({"id": f"assistant_note_{i}", "text": f"## Assistant note {i}\n" + note})
    return items


def build_handoff(task_text, transcript_path, changed_paths, failure_output=None, *,
                  max_chars=12000, client=None, judge_module=None):
    """Collect what a fresh session needs and fit it under `max_chars`.

    Deterministic collection (task, each changed path's `git diff`, the last
    failure, the transcript's last few assistant notes). When the collection
    already fits `max_chars`, everything is kept and Jev is never asked. Over
    budget, ONE request asks a noul per item — is it worth keeping — and items
    are kept, highest-relevance first, until the budget is full.
    """
    items = []
    try:
        items = _collect_items(task_text, transcript_path, changed_paths, failure_output)
        if not items:
            return {"pack": "", "kept": [], "dropped": []}

        total = sum(len(it["text"]) + 2 for it in items)
        if total <= max_chars:
            pack = "\n\n".join(it["text"] for it in items)[:max_chars]
            return {"pack": pack, "kept": [it["id"] for it in items], "dropped": []}

        probs = None
        try:
            jj = judge_module or _sibling("jev_judge")
            tsc = client or jj._client()
            keys = {it["id"]: _safe_id(it["id"]) for it in items}
            questions = {keys[it["id"]]: tsc.noul(RELEVANCE_QUESTION.format(key=keys[it["id"]]))
                        for it in items}
            state = {"items": {keys[it["id"]]: it["text"][:JUDGE_ITEM_CHARS] for it in items}}
            answer = jj.judge(state, questions, client=client, timeout=HANDOFF_TIMEOUT_SECONDS)
            jj.record("supervise.handoff_pack_relevance", _text_ref(task_text or ""), answer, None,
                      note={"item_count": len(items), "over_budget_by": total - max_chars})
            probs = {it["id"]: _noul(answer, keys[it["id"]]) for it in items}
        except Exception:
            probs = None

        if probs is None:
            # Fail open with a deterministic priority: the task itself first,
            # then everything else in collection order (diffs before the
            # failure before the oldest assistant notes).
            probs = {it["id"]: (1.0 if it["id"] == "task" else 0.5 - i * 1e-6)
                     for i, it in enumerate(items)}

        ranked = sorted(items, key=lambda it: -(probs.get(it["id"]) or 0.0))
        kept, dropped, used = [], [], 0
        for it in ranked:
            cost = len(it["text"]) + 2
            if used + cost <= max_chars:
                kept.append(it)
                used += cost
            else:
                dropped.append(it["id"])
        kept_ids = {it["id"] for it in kept}
        kept_in_order = [it for it in items if it["id"] in kept_ids]
        pack = "\n\n".join(it["text"] for it in kept_in_order)[:max_chars]
        return {"pack": pack, "kept": [it["id"] for it in kept_in_order], "dropped": dropped}
    except Exception:
        try:
            fallback = "\n\n".join(it["text"] for it in items)[:max_chars] if items else ""
        except Exception:
            fallback = ""
        return {"pack": fallback, "kept": [it["id"] for it in items] if fallback else [], "dropped": []}
