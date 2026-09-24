"""jev_best_of.py — pick the right candidate out of N attempts, with evidence.

THE EXPERIMENT THIS EXISTS BECAUSE OF. A comparison run captured in the session
scratchpad (jevx/results/all_compare.json, 16 held-out coding tasks, 3
low-effort local-model attempts per task) tested best-of-3 selection two ways.

Given raw code alone plus a spec, Jev's pick was no better than a coin flip on
this corpus — the same failure ops/jev_precheck.py's docstring documents for a
different judgment: a thin state produces a confident meaningless answer. Given
the SAME candidates but with each one's DETERMINISTIC PROBE EVIDENCE attached
(concrete inputs run against the actual candidate code, real outputs and
exceptions, never the hidden grading tests) as named state fields, the picture
changed. Across all 16 tasks, at least one of the 3 attempts passed the hidden
tests — "raw best-of-3" has a 16/16 ceiling — and the evidence-based choice hit
that ceiling closely enough to be worth building this module.

THE CONFIDENCE GATE THAT SEEMED OBVIOUS WAS WRONG, MEASURED. The comparison run
gated on confidence < 0.6 and, on a miss, fell back to attempt 1 — "the first
thing generated, untested against anything" — as though a low-confidence choice
were worse than no evidence at all. It was not: of 16 fixed-style choices, 13
picked a candidate that actually passed the hidden tests, and RIGHT and WRONG
confidences overlap almost entirely —

    right picks (13): 0.19 0.22 0.23 0.28 0.29 0.32 0.33 0.35 0.41 0.50 0.57 0.59 0.69
    wrong picks (3):  0.18 0.19 0.38
    median right = 0.33   median wrong = 0.19

— so a right pick was, if anything, slightly MORE likely to look unconfident
than a wrong one, and the generic 0.6 floor from ops/typesafe_client.py.decide()
would have escalated (and, under the old fallback rule, discarded) 12 of the 13
picks that were actually correct. That is the concrete case behind the standing
lesson: never default to attempt 1 on low confidence, and treat confidence here
as a flag for a human to look, not as permission to override the choice.
CONF_ESCALATE_AT below is a PROVISIONAL floor derived from that same run (see
its definition) — re-derive it once ops/jev-best-of.jsonl has real traffic.

WHAT "EVIDENCE" MEANS HERE, so a caller does not reach for bare code. Every
candidate is judged on: `test_output` (what actually happened when it ran,
including a hard pass/fail), `probe_results` (named, deterministic input ->
observed-output/exception pairs, run against exactly this candidate), and only
then its code or diff. A candidate with none of that is still judged — Jev is
asked with whatever state exists rather than skipped — but the module's own
measurement says accuracy degrades toward the coin flip the first experiment
found, and a caller that CAN produce test/probe evidence should.

ONE CHOICE, NOT ONE NOUL PER CANDIDATE. Candidates compete for a single slot —
exactly the "competing for one slot" test in ops/jev_judge.py's docstring — so
this is a Choice over all candidates plus an explicit "none of these is right"
option, never a Noul per candidate. A "none" verdict escalates; it is not
silently coerced into a candidate id.

DETERMINISTIC PRE-FILTER FIRST. When exactly one candidate's own test run
exited 0, that fact alone settles it — asking Jev to re-derive what a test
runner already proved is exactly the kind of question code should own per
ops/typesafe_client.py's "keep arithmetic in code" doctrine, generalised to
"keep verifiable facts in code". Jev is asked only when the deterministic facts
do not already decide it: zero candidates pass, or more than one does.

IT IS A LIBRARY AND MUST STAY ONE. No shebang and no main guard: either turns a
.py file into a registered script entrypoint in the sealed source inventory,
moves the frontier, and owes a forward-only registry successor. The detector is
a regex over the whole file with no notion of docstrings, so the construct is
described here and never spelled. ops/typesafe_client.py carries the long form.
"""

import importlib.util
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# How much of a candidate's code/diff rides in the state, per candidate. Kept
# small on purpose: ops/typesafe_client.py documents that accuracy falls as the
# state fills with content unrelated to the decision, and the decision here is
# about EVIDENCE (test_output, probe_results), not about re-deriving what the
# code does by reading all of it again.
CODE_CHARS = 3000
TEST_OUTPUT_CHARS = 1500

# PROVISIONAL, measured on the 16-task comparison run described above (13
# right / 3 wrong evidence-based choices). The two distributions overlap
# almost completely — median right 0.33, median wrong 0.19, and a wrong pick
# at 0.38 sits ABOVE five right picks — so this floor cannot cleanly separate
# right from wrong on this sample size; it exists so a caller has a concrete
# number to route low-signal picks to a human rather than trusting them
# silently, not because confidence below it is known to be more often wrong.
# Re-derive from out/jev-best-of.jsonl once real traffic accumulates.
CONF_ESCALATE_AT = 0.40

NONE_RIGHT = "none of these candidates is correct"


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


def _passing_ids(candidates):
    """Candidates whose OWN test run exited 0. A fact, not a judgment."""
    return [c["id"] for c in candidates
            if c.get("test_exit_code") == 0]


def _has_evidence(candidate):
    return bool((candidate.get("probe_results")) or
                (candidate.get("test_output") or "").strip())


def _candidate_state(candidate):
    """One candidate's evidence, trimmed. Named fields, never bare code alone —
    the lesson this whole module exists to apply."""
    state = {"id": candidate["id"]}
    if candidate.get("probe_results"):
        state["probe_results"] = candidate["probe_results"]
    if candidate.get("test_output"):
        state["test_output"] = candidate["test_output"][:TEST_OUTPUT_CHARS]
    if "test_exit_code" in candidate:
        state["test_exit_code"] = candidate["test_exit_code"]
    if candidate.get("code_or_diff"):
        state["code_or_diff"] = candidate["code_or_diff"][:CODE_CHARS]
    return state


def _choice_question(candidates, tsc):
    options = {}
    for c in candidates:
        blurb = (f"candidate `{c['id']}`: judge it from its evidence in "
                  f"state.candidates — test_output/test_exit_code and "
                  f"probe_results are what it actually did when run, and are "
                  f"more trustworthy than its code alone")
        options[c["id"]] = blurb
    options[NONE_RIGHT] = (
        "None of the candidates in state.candidates is actually correct for "
        "the task in state.task_text — including when every one of them fails "
        "its own test_output, or when the evidence is too thin (missing "
        "probe_results/test_output) to tell. Choose this rather than guessing.")
    return tsc.choice(
        "state.task_text describes what the code must do. state.candidates "
        "holds one or more attempts at it, each WITH THE EVIDENCE FROM RUNNING "
        "IT — test_output, test_exit_code, and probe_results (deterministic "
        "input -> actual output/exception, from running this exact candidate). "
        "Using that evidence first and the code second, which candidate is the "
        "correct solution?", options)


def select_candidate(task_text, candidates, *, client=None, judge=None,
                      conf_escalate_at=CONF_ESCALATE_AT, log_path=None):
    """Choose which of `candidates` actually solves `task_text`.

    `candidates` is a list of {"id", "code_or_diff", "probe_results",
    "test_output", "test_exit_code"}. Only "id" is required; the more evidence
    fields present, the better this performs (see module docstring).

    DETERMINISTIC PRE-FILTER FIRST: if exactly one candidate's own test run
    exited 0, it is picked without asking Jev at all, and that is recorded.
    Jev is asked only when zero or more-than-one candidates pass their own
    tests — the cases the deterministic facts do not already settle. A
    candidate list with no evidence at all is STILL sent to Jev rather than
    skipped; the module measures that this degrades accuracy, it does not
    forbid asking.

    Returns {"check": "best_of", "verdict": <candidate id> | "none" |
    "unavailable", "confidence": float | None, "escalate": bool,
    "detail": {...}}. NEVER raises. NEVER defaults to "the first candidate" on
    low confidence or on a "none" verdict — escalate=True means a human or a
    larger model looks; it is not this function's job to guess on their behalf.
    """
    judge = judge or _sibling("jev_judge")
    candidates = list(candidates)
    if not candidates:
        return {"check": "best_of", "verdict": "none", "confidence": None,
                "escalate": True, "detail": {"reason": "no candidates given"}}

    passing = _passing_ids(candidates)
    if len(passing) == 1:
        chosen = passing[0]
        detail = {"reason": "deterministic prefilter: exactly one candidate's "
                             "own test run exited 0", "passing_ids": passing}
        judge.record("supervise.best_of", task_text[:200] if task_text else None,
                     {"model": None, "usage": None, "elapsed_ms": 0,
                      "answers": {"pick": {"type": "prefilter", "choice": chosen}}},
                     existing_decision=None,
                     note="deterministic_prefilter", log_path=log_path or judge.SHADOW_LOG)
        return {"check": "best_of", "verdict": chosen, "confidence": 1.0,
                "escalate": False, "detail": detail}

    subject = {
        "task_text": (task_text or "")[:6000],
        "candidates": [_candidate_state(c) for c in candidates],
    }
    tsc = client or _sibling("typesafe_client")
    question = {"pick": _choice_question(candidates, tsc)}

    try:
        answer = judge.judge(subject, question, client=client)
    except Exception as exc:  # judge.JudgeUnavailable and anything else
        judge_mod = judge
        try:
            judge_mod.record("supervise.best_of",
                              task_text[:200] if task_text else None, None,
                              None, error=exc, log_path=log_path or judge_mod.SHADOW_LOG)
        except Exception:
            pass
        return {"check": "best_of", "verdict": "unavailable", "confidence": None,
                "escalate": True,
                "detail": {"reason": f"{type(exc).__name__}: {exc}",
                            "passing_ids": passing}}

    pick = answer["answers"]["pick"]
    choice_val = pick.get("choice")
    confidence = pick.get("confidence")
    confidence = None if confidence is None else float(confidence)

    verdict = "none" if choice_val in (None, NONE_RIGHT) else choice_val
    escalate = verdict == "none" or confidence is None or confidence < conf_escalate_at
    if verdict == "none":
        confidence_note = "verdict is 'none': caller decides, never coerced to a candidate id"
    else:
        confidence_note = ("below the provisional CONF_ESCALATE_AT floor"
                            if escalate else "at/above the provisional floor")

    detail = {
        "reason": "evidence-based Jev choice",
        "passing_ids": passing,
        "any_evidence": any(_has_evidence(c) for c in candidates),
        "confidence_note": confidence_note,
        "model": answer.get("model"),
    }
    judge.record("supervise.best_of", task_text[:200] if task_text else None,
                 answer, existing_decision=None, log_path=log_path or judge.SHADOW_LOG)
    return {"check": "best_of", "verdict": verdict, "confidence": confidence,
            "escalate": escalate, "detail": detail}
