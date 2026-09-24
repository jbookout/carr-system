"""jev_intake.py — BEFORE-the-task checks for a coding agent session.

Six of the checks from open loop #629's twenty-five: pick the files to read
before touching anything (#1), pick a thinking-level recommendation (#2), stop
on ambiguous asks before writing code (#3), route a task to a local model or
escalate it (#5), split a multi-step plan into per-step routing (#18), and
pick the closest past worked example (#23).

EVERY PUBLIC FUNCTION HERE IS A CHECK, NEVER A GATE. It reports; it never
blocks and never raises. On any failure to reach Jev — including a missing
credential, a timeout, or a malformed response — it catches the failure and
returns verdict "unavailable" rather than letting the caller crash or hang.
That is a deliberate widening beyond the two- or three-way verdict named for
each check below: "unavailable" is always a fourth possible outcome, and a
caller checks for it before trusting the named verdicts at all.

SHADOW FIRST. Every check that actually reaches Jev calls
ops/jev_judge.py's record() with what Jev said, so a threshold here can later
be measured against real traffic rather than argued for in a docstring. A
check that never reaches Jev because its deterministic trigger did not fire
has nothing to record — there is no judgment to shadow, only a skip — so
those paths return without a record() call. That is documented per function
below, not left implicit.

THE SHAPE PER CHECK. A cheap deterministic trigger runs first; a Jev round
trip only happens when it fires, because a hook runs on every tool call and a
round trip is 0.5-2s. Independent facts about ONE subject are asked together
in ONE request — several nouls, or several scores, in a single judge.judge()
call — never as separate requests, per the vendor's own measured 12.2x/10x
efficiency finding. Candidates competing for a single slot (a central file, a
worked example) are a single Choice with an explicit "none of these" option,
never one Noul per candidate.

THRESHOLDS ARE NAMED CONSTANTS AND ARE PROVISIONAL. The generic 0.6
confidence default measured elsewhere on this project did not transfer —
evidence-state choice confidences had a median of 0.33 while still being
right — so every threshold below is a documented placeholder to replace once
this module's own shadow log has real traffic to measure against. Low
confidence is treated as information (escalate=True), never smoothed over by
falling back to a first guess.

ONE MEASURED FACT THAT SHAPES pick_effort AND split_plan: on the local Qwen
model, HIGHER thinking effort HURT on hard tasks — the 27B model at high
effort went 0 for 9. So "low" is the default recommendation everywhere in
this module and "high" is returned only when the evidence for it is strong:
a score placed near the hardest level AND the judgment itself confident about
that placement. This is the opposite of what intuition suggests ("harder
task, ask for more effort") and it is why the mapping below is conservative
on purpose rather than proportional.

IT IS A LIBRARY AND MUST STAY ONE. No shebang line and no main guard: either
one turns a .py file into a registered script entrypoint in the sealed source
inventory, moves the frontier, and owes a forward-only registry successor.
The inventory's detector is a regex over the whole file with no notion of a
docstring, so the construct is described here and never spelled out, even as
an example. ops/typesafe_client.py carries the long version of this warning.
Its sibling selftest, ops/jev-intake-selftest.py, is exempt by its own name.
"""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _client():
    """Load ops/typesafe_client.py the same way jev_judge._client() does.

    ops/ carries no __init__.py on purpose, so a sibling module is loaded by
    path rather than imported by name. Used only as the DEFAULT when a caller
    passes no `client`; every public function here takes `client=None` so a
    fake can be injected in tests without any network reachable.
    """
    path = os.path.join(REPO, "ops", "typesafe_client.py")
    spec = importlib.util.spec_from_file_location("typesafe_client", path)
    if spec is None or spec.loader is None:  # pragma: no cover - import plumbing
        raise RuntimeError("cannot load ops/typesafe_client.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _judge():
    """Load ops/jev_judge.py by path. The only door to Jev, per the brief."""
    path = os.path.join(REPO, "ops", "jev_judge.py")
    spec = importlib.util.spec_from_file_location("jev_judge", path)
    if spec is None or spec.loader is None:  # pragma: no cover - import plumbing
        raise RuntimeError("cannot load ops/jev_judge.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _skip(check_id, verdict, detail, *, confidence=1.0, escalate=False):
    """A deterministic-trigger skip: no Jev call happened, so nothing to record."""
    return {"check": check_id, "verdict": verdict, "confidence": confidence,
            "escalate": escalate, "detail": detail}


def _unavailable(check_id, kind, subject_ref, exc):
    """Jev could not be reached or answered oddly. Never raises past this point."""
    try:
        _judge().record(kind, str(subject_ref)[:160], None, None, error=exc)
    except Exception:
        pass
    return {"check": check_id, "verdict": "unavailable", "confidence": None,
            "escalate": True,
            "detail": {"error": f"{type(exc).__name__}: {exc}"[:300]}}


# --- shared tokenising, for keyword/identifier overlap ---------------------
#
# One tokenizer used by every check that ranks candidates against task text:
# splits CamelCase and snake_case/kebab-case into words, lowercases, and
# treats a path's segments the same as any other token.

_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_WORD = re.compile(r"[A-Za-z0-9]+")


def _identifier_words(token):
    token = token.replace("_", " ").replace("-", " ").replace("/", " ")
    token = _CAMEL_BOUNDARY.sub(" ", token)
    return [w.lower() for w in _WORD.findall(token) if len(w) > 1]


def _tokenize(text):
    words = set()
    for tok in re.findall(r"[A-Za-z0-9_./-]+", text or ""):
        words.update(_identifier_words(tok))
    return words


# ============================================================================
# #1 — context picker
# ============================================================================
#
# DETERMINISTIC TRIGGER: only runs when `task_text` names NO file path at
# all. A task that already says "fix ops/foo.py" does not need a picker
# second-guessing it — the deterministic regex already found the answer, and
# asking Jev to re-derive it would spend a round trip on a solved problem.

FILE_TOKEN = re.compile(r"\b[\w./-]+\.[A-Za-z0-9]{1,8}\b")
CONTEXT_CANDIDATE_CAP = 120   # keyword-ranked shortlist handed to the Choice
CONTEXT_SHORTLIST = 15        # top of that, each also asked its own noul
CONTEXT_OPTION_CHARS = 200    # per-option text truncation for the Choice
CONTEXT_NONE = "no single file is the natural place to start"
CONTEXT_MIN_CONFIDENCE = 0.5  # provisional; unmeasured on this module's own traffic


def _task_names_files(task_text):
    return bool(FILE_TOKEN.search(task_text or ""))


def _tracked_files(repo_root):
    try:
        out = subprocess.run(["git", "ls-files"], cwd=repo_root, capture_output=True,
                             text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [p for p in out.splitlines() if p]


def _rank_by_overlap(task_text, paths, cap):
    """Cheap, offline: rank tracked paths by token overlap with the task text."""
    query = _tokenize(task_text)
    if not query:
        return paths[:cap]
    scored = []
    for path in paths:
        overlap = len(query & _tokenize(path))
        scored.append((overlap, path))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [path for _, path in scored[:cap]]


def _first_comment_or_docstring(path, repo_root, max_chars=140):
    """The file's own one-line self-description, read without importing it."""
    full = os.path.join(repo_root, path)
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as handle:
            head = handle.read(4000)
    except OSError:
        return ""
    for line in head.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(('"""', "'''")):
            return stripped.strip("\"' ")[:max_chars]
        if stripped.startswith(("#", "//", "/*", "*")):
            return stripped.lstrip("#/* ").strip()[:max_chars]
        break  # first non-blank, non-comment line: nothing to say here
    return ""


def _context_option_text(path, repo_root):
    note = _first_comment_or_docstring(path, repo_root)
    text = f"{path} — {note}" if note else path
    return text[:CONTEXT_OPTION_CHARS]


def pick_context(task_text, repo_root, *, max_files=8, client=None):
    """Which files this task plausibly needs to read or edit, ordered.

    Deterministic shortlist first: `git ls-files`, ranked by keyword and
    identifier overlap with `task_text` (CamelCase/snake_case tokens
    included), capped to CONTEXT_CANDIDATE_CAP candidates, each option
    truncated to its path plus its own first docstring/comment line. Then ONE
    Jev request carrying both shapes at once — a single Choice for the most
    central file, plus one Noul per file in the top CONTEXT_SHORTLIST asking
    "does this task need this file" — because several files may genuinely be
    needed and a single Choice can only name one winner.

    Returns the standard check dict; `detail.paths` is the chosen paths,
    central file first if one was named, then noul-approved files by
    descending probability, deduplicated, capped at `max_files`.

    TRIGGER: skipped entirely when `task_text` already names a file path —
    see FILE_TOKEN — or when the repo has no tracked files to rank.
    """
    check_id = "context_picker"
    kind = "supervise.context_picker"
    if _task_names_files(task_text):
        return _skip(check_id, "picked",
                     {"reason": "task text already names a file path; the "
                                "deterministic reference is used as-is",
                      "paths": list(dict.fromkeys(FILE_TOKEN.findall(task_text)))[:max_files]})
    paths = _tracked_files(repo_root)
    if not paths:
        return _skip(check_id, "none_found",
                     {"reason": "no tracked files under repo_root", "paths": []})

    ranked = _rank_by_overlap(task_text, paths, CONTEXT_CANDIDATE_CAP)
    shortlist = ranked[:CONTEXT_SHORTLIST]
    try:
        tsc = client or _client()
        judge_mod = _judge()
        options = {path: _context_option_text(path, repo_root) for path in ranked}
        options[CONTEXT_NONE] = ("No file among the options is the natural place to "
                                 "start reading or editing for this task.")
        questions = {"central": tsc.choice(
            "`state.task_text` describes a piece of work in this repository. Of "
            "the files listed as options, which ONE is the single most central "
            "place to start reading or editing for this task?", options)}
        for path in shortlist:
            questions[f"needs::{path}"] = tsc.noul(
                f"`state.task_text` describes a piece of work. Does completing it "
                f"plausibly require reading or editing `{path}`? Answer no for a "
                "file that only shares a topic but would not itself need to "
                "change or be read.",
                true=f"the task would need to read or edit {path}",
                false=f"the task would not need {path}")
        answer = judge_mod.judge({"task_text": task_text, "candidate_count": len(ranked)},
                                 questions, client=tsc)
    except Exception as exc:  # never raises past a check: see module docstring
        return _unavailable(check_id, kind, task_text, exc)

    central = judge_mod.read(answer, "central", min_confidence=CONTEXT_MIN_CONFIDENCE)
    ordered = []
    if central["outcome"] == "value" and central["value"] not in (None, CONTEXT_NONE):
        ordered.append(central["value"])
    yes_files = []
    for path in shortlist:
        decision = judge_mod.read(answer, f"needs::{path}")
        if decision["outcome"] == "yes":
            yes_files.append((decision["value"], path))
    yes_files.sort(key=lambda item: -item[0])
    for _, path in yes_files:
        if path not in ordered:
            ordered.append(path)
    ordered = ordered[:max_files]

    judge_mod.record(kind, task_text[:160], answer, None,
                     note="picked" if ordered else "none_picked")

    return {"check": check_id, "verdict": "picked" if ordered else "none_found",
            "confidence": central["confidence"],
            "escalate": (not ordered) or bool(central["escalate"]),
            "detail": {"paths": ordered, "candidate_count": len(ranked),
                      "shortlist": shortlist}}


# ============================================================================
# #2 — thinking-level picker
# ============================================================================
#
# DETERMINISTIC TRIGGER: a small keyword signal for obviously trivial work
# (a rename, a typo, a comment/docstring fix) skips Jev outright and returns
# "low" — the cheapest correct answer for the cheapest class of task, spending
# no round trip on it. Everything else asks Jev one Score question.

EFFORT_LEVELS = [
    "trivial — a small, mechanical, well-scoped change with an obviously "
    "correct edit",
    "moderate — needs some understanding of surrounding code or a few "
    "coupled edits, but the right approach is not in doubt",
    "hard — unfamiliar, novel, or algorithmically tricky work where the "
    "approach itself is uncertain",
]
TRIVIAL_TASK_SIGN = re.compile(
    r"\b(typo|rename|comment|docstring|formatting|whitespace|bump version|"
    r"update changelog)\b", re.I)

# Provisional thresholds. score_value is the client's probability-weighted
# position across EFFORT_LEVELS (0 = lowest level, len-1 = highest); see
# ops/typesafe_client.py's score() docstring. Deliberately conservative per
# the module docstring's measured-fact warning: "high" needs BOTH a score
# near the top level AND high confidence in that placement.
EFFORT_HIGH_SCORE_AT = 1.6
EFFORT_HIGH_CONFIDENCE_AT = 0.75
EFFORT_MEDIUM_SCORE_AT = 0.8
EFFORT_MIN_CONFIDENCE = 0.5


def _effort_from_score(score_value, confidence):
    if confidence is None or confidence < EFFORT_MIN_CONFIDENCE or score_value is None:
        return "low", True  # low confidence is information: escalate, don't guess
    if score_value >= EFFORT_HIGH_SCORE_AT and confidence >= EFFORT_HIGH_CONFIDENCE_AT:
        return "high", False
    if score_value >= EFFORT_MEDIUM_SCORE_AT:
        return "medium", False
    return "low", False


def pick_effort(task_text, *, context_summary=None, client=None):
    """Recommend a thinking-level verdict in {"low", "medium", "high"}.

    Asks ONE Score question over EFFORT_LEVELS. The mapping from score to
    verdict is deliberately biased toward "low": on the local Qwen model,
    HIGHER effort measurably HURT on hard tasks (27B at high effort went 0/9),
    so "high" is only returned when the score sits near the hardest level AND
    the judgment is itself confident about that placement — see
    _effort_from_score. Low confidence returns "low" with escalate=True,
    never a guess.

    TRIGGER: skipped, returning "low" with confidence=1.0 and no Jev call,
    when TRIVIAL_TASK_SIGN matches the task text.
    """
    check_id = "effort_picker"
    kind = "supervise.effort_picker"
    match = TRIVIAL_TASK_SIGN.search(task_text or "")
    if match:
        return _skip(check_id, "low",
                     {"reason": "deterministic trivial-task signal matched",
                      "matched": match.group(0)})
    try:
        tsc = client or _client()
        judge_mod = _judge()
        questions = {"difficulty": tsc.score(
            "Rate how difficult and how novel `state.task_text` is to implement "
            "correctly in one pass, against the ordered levels.", EFFORT_LEVELS)}
        subject = {"task_text": task_text}
        if context_summary:
            subject["context_summary"] = context_summary
        answer = judge_mod.judge(subject, questions, client=tsc)
    except Exception as exc:
        return _unavailable(check_id, kind, task_text, exc)

    decision = judge_mod.read(answer, "difficulty", min_confidence=EFFORT_MIN_CONFIDENCE)
    try:
        score_value = float(decision["value"])
    except (TypeError, ValueError):
        score_value = None
    verdict, escalate = _effort_from_score(score_value, decision["confidence"])

    judge_mod.record(kind, task_text[:160], answer, None)

    return {"check": check_id, "verdict": verdict, "confidence": decision["confidence"],
            "escalate": escalate,
            "detail": {"score": score_value, "levels": EFFORT_LEVELS}}


# ============================================================================
# #3 — ambiguity stop
# ============================================================================
#
# DETERMINISTIC TRIGGER: only runs when the task text is short (under
# SHORT_TASK_CHARS) OR names no concrete file/function target at all. A long,
# targeted task description has already answered most of what this check
# would ask, and spending a round trip re-litigating it would be the same
# mistake ops/jev_precheck.py documents: a note that fires on topic rather
# than on its actual condition.

SHORT_TASK_CHARS = 50
FUNC_TOKEN = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\s*\(")
AMBIGUITY_MIN_CONFIDENCE = 0.5

# id -> (question, true criterion, false criterion). One Noul each, asked
# together in ONE request about the same subject — independent facts about
# one task, not candidates competing for a slot.
AMBIGUITY_KINDS = {
    "missing_target": (
        "Does `state.task_text` fail to name a specific file, function, class, "
        "or component to change — so a session would have to guess WHERE to "
        "make the change? Answer no when a target is named, even loosely.",
        "no file, function or component is named",
        "a target is named, even loosely"),
    "conflicting_requirements": (
        "Does `state.task_text` (with `state.context_summary` if present) ask "
        "for two things that cannot both be true at once, or contradict "
        "something already known about the codebase? Answer no when the "
        "request is internally consistent.",
        "the request contradicts itself or known context",
        "the request is internally consistent"),
    "unstated_acceptance_test": (
        "Does `state.task_text` give NO way to tell when the work is done — no "
        "expected output, no passing test, no described behaviour to verify? "
        "Answer no when the request implies a checkable outcome, even "
        "informally.",
        "there is no way to tell when the work is done",
        "a checkable outcome is implied, even informally"),
    "unclear_scope": (
        "Is it unclear from `state.task_text` how FAR the change should reach "
        "— whether it touches one file or many, whether related code should "
        "also change, or where the edit should stop? Answer no when the "
        "boundary of the change is reasonably clear.",
        "the boundary of the change is unclear",
        "the boundary of the change is reasonably clear"),
}


def _needs_ambiguity_check(task_text):
    text = task_text or ""
    return len(text.strip()) < SHORT_TASK_CHARS or not (
        FILE_TOKEN.search(text) or FUNC_TOKEN.search(text))


def check_ambiguity(task_text, *, context_summary=None, client=None):
    """Verdict "clear" or "ambiguous", naming which fixed kind(s) apply.

    Every kind in AMBIGUITY_KINDS is asked as one Noul, all in ONE request.
    `detail.kinds` names, per kind, whether it applies and Jev's own
    escalate flag for that specific Noul. verdict is "ambiguous" the moment
    ANY kind applies.

    TRIGGER: skipped, returning "clear", when the task text is at least
    SHORT_TASK_CHARS long AND names a concrete file or function — see
    _needs_ambiguity_check.
    """
    check_id = "ambiguity_stop"
    kind = "supervise.ambiguity_stop"
    if not _needs_ambiguity_check(task_text):
        return _skip(check_id, "clear",
                     {"reason": "task text is long enough and names a concrete "
                                "file or function target"})
    try:
        tsc = client or _client()
        judge_mod = _judge()
        questions = {qid: tsc.noul(text, true=true, false=false)
                     for qid, (text, true, false) in AMBIGUITY_KINDS.items()}
        subject = {"task_text": task_text}
        if context_summary:
            subject["context_summary"] = context_summary
        answer = judge_mod.judge(subject, questions, client=tsc)
    except Exception as exc:
        return _unavailable(check_id, kind, task_text, exc)

    kinds = {}
    any_applies = False
    any_escalate = False
    for qid in AMBIGUITY_KINDS:
        decision = judge_mod.read(answer, qid, min_confidence=AMBIGUITY_MIN_CONFIDENCE)
        applies = decision["outcome"] == "yes"
        kinds[qid] = {"applies": applies, "probability": decision["value"],
                     "escalate": decision["escalate"]}
        any_applies = any_applies or applies
        any_escalate = any_escalate or decision["escalate"]

    judge_mod.record(kind, task_text[:160], answer, None)

    return {"check": check_id, "verdict": "ambiguous" if any_applies else "clear",
            "confidence": None, "escalate": any_applies or any_escalate,
            "detail": {"kinds": kinds}}


# ============================================================================
# #5 — escalation router
# ============================================================================
#
# DETERMINISTIC RULES RUN FIRST AND ARE FINAL WHEN THEY FIRE — no Jev round
# trip needed to know that a 400-line diff, or untested parser code, escalates.
# Jev is only asked to judge difficulty on what is LEFT after every
# deterministic rule has had a chance to say no.

SECURITY_SIGN = re.compile(
    r"\b(auth(?:entication|orization)?|security|validat(?:e|es|ion)|sanitiz\w*|"
    r"pars(?:er|ing)|crypto\w*|password|token|secret|injection|csrf|xss|ssrf|"
    r"deserializ\w*)\b", re.I)
ALGO_SIGN = re.compile(
    r"\b(algorithm\w*|optimi[sz]e\w*|concurren(?:cy|t)|race condition|"
    r"distributed|consensus)\b", re.I)
ROUTE_MAX_DIFF_LINES = 300
ROUTE_MAX_FILES = 5
ROUTE_DIFFICULTY_YES_AT = 0.7  # provisional
ROUTE_MIN_CONFIDENCE = 0.5


def _deterministic_route_reasons(task_text, files, diff_size_estimate, has_tests):
    text = task_text or ""
    reasons = []
    if SECURITY_SIGN.search(text):
        reasons.append("security, auth, sanitization or parser work")
    if ALGO_SIGN.search(text) and has_tests is not True:
        reasons.append("untested algorithm/concurrency work")
    if diff_size_estimate is not None and diff_size_estimate > ROUTE_MAX_DIFF_LINES:
        reasons.append(f"diff estimate {diff_size_estimate} lines exceeds "
                       f"{ROUTE_MAX_DIFF_LINES}")
    if files is not None and len(files) > ROUTE_MAX_FILES:
        reasons.append(f"{len(files)} files exceeds {ROUTE_MAX_FILES}")
    if has_tests is False:
        reasons.append("no test command is available to check the work")
    return reasons


def route_task(task_text, *, files=None, diff_size_estimate=None, has_tests=None,
               client=None):
    """Verdict "local" or "escalate" plus detail.reason.

    Deterministic rules run first and are FINAL when any fires: security or
    parser-shaped work, untested algorithm/concurrency work, a diff over
    ROUTE_MAX_DIFF_LINES, more than ROUTE_MAX_FILES files, or no test command
    at all. Only when NONE of those fire does this ask Jev one Noul —
    "is this still hard or risky enough to escalate" — on what is left.

    TRIGGER: the deterministic rules ARE the trigger for skipping Jev; there
    is no separate gate on top of them.
    """
    check_id = "escalation_router"
    kind = "supervise.escalation_router"
    reasons = _deterministic_route_reasons(task_text, files, diff_size_estimate,
                                           has_tests)
    if reasons:
        return {"check": check_id, "verdict": "escalate", "confidence": 1.0,
                "escalate": True,
                "detail": {"reason": reasons[0], "reasons": reasons,
                          "deterministic": True}}
    try:
        tsc = client or _client()
        judge_mod = _judge()
        questions = {"hard_enough_to_escalate": tsc.noul(
            "`state.task_text` describes a coding task that already passed "
            "every deterministic escalation rule (not security-sensitive, "
            "tested algorithm work if any, a small diff, few files, tests "
            "available). Given everything else known about it, is it STILL "
            "hard or risky enough that a person or a larger model should do "
            "it rather than a small local model? Answer no for ordinary, "
            "well-scoped work.",
            true="a person or a larger model should do this",
            false="ordinary work for a small local model")}
        subject = {"task_text": task_text}
        if files:
            subject["files"] = list(files)
        if diff_size_estimate is not None:
            subject["diff_size_estimate"] = diff_size_estimate
        if has_tests is not None:
            subject["has_tests"] = has_tests
        answer = judge_mod.judge(subject, questions, client=tsc)
    except Exception as exc:
        return _unavailable(check_id, kind, task_text, exc)

    decision = judge_mod.read(answer, "hard_enough_to_escalate",
                              yes_at=ROUTE_DIFFICULTY_YES_AT,
                              min_confidence=ROUTE_MIN_CONFIDENCE)
    judge_mod.record(kind, task_text[:160], answer, None)

    verdict = "escalate" if decision["outcome"] == "yes" else "local"
    return {"check": check_id, "verdict": verdict, "confidence": decision["confidence"],
            "escalate": verdict == "escalate" or bool(decision["escalate"]),
            "detail": {"reason": ("Jev judged this hard or risky enough to "
                                  "escalate" if verdict == "escalate" else
                                  "no deterministic or judged escalation signal"),
                      "deterministic": False, "probability": decision["value"]}}


# ============================================================================
# #18 — plan splitting
# ============================================================================
#
# ONE request for the WHOLE plan: a Score question per step, all about the
# same subject (`state.plan.steps`), batched together per the vendor's own
# headline efficiency finding rather than one request per step.

PLAN_DIFFICULTY_LEVELS = EFFORT_LEVELS
PLAN_HIGH_SCORE_AT = EFFORT_HIGH_SCORE_AT
PLAN_HIGH_CONFIDENCE_AT = EFFORT_HIGH_CONFIDENCE_AT
PLAN_MEDIUM_SCORE_AT = EFFORT_MEDIUM_SCORE_AT
PLAN_MIN_CONFIDENCE = 0.5


def split_plan(plan_steps: list, *, client=None):
    """Per-step {"step", "route": "local"|"escalate", "difficulty"}.

    One request carries one Score question per step of `plan_steps`, all
    about the single subject `state.plan.steps` — never one request per
    step. Reuses pick_effort's conservative low-by-default mapping per step
    (see _effort_from_score's docstring for the measured reason).

    Returns the standard check dict; `detail.steps` is the ordered per-step
    list the brief describes, each carrying "step", "route", "difficulty",
    and "confidence".

    TRIGGER: an empty `plan_steps` skips Jev and returns an empty step list.
    """
    check_id = "plan_split"
    kind = "supervise.plan_split"
    if not plan_steps:
        return _skip(check_id, "routed", {"reason": "no steps to route", "steps": []})

    try:
        tsc = client or _client()
        judge_mod = _judge()
        questions = {f"step_{i}": tsc.score(
            f"Rate how difficult and how novel step {i} of `state.plan.steps` is "
            "to implement correctly in one pass, against the ordered levels.",
            PLAN_DIFFICULTY_LEVELS)
            for i in range(len(plan_steps))}
        answer = judge_mod.judge({"plan": {"steps": list(plan_steps)}}, questions,
                                 client=tsc)
    except Exception as exc:
        return _unavailable(check_id, kind, f"plan with {len(plan_steps)} steps", exc)

    steps_out = []
    any_escalate = False
    for i, step_text in enumerate(plan_steps):
        decision = judge_mod.read(answer, f"step_{i}", min_confidence=PLAN_MIN_CONFIDENCE)
        try:
            score_value = float(decision["value"])
        except (TypeError, ValueError):
            score_value = None
        difficulty, low_confidence_escalate = _effort_from_score(
            score_value, decision["confidence"])
        route = "escalate" if difficulty == "high" else "local"
        any_escalate = any_escalate or low_confidence_escalate or route == "escalate"
        steps_out.append({"step": step_text, "route": route, "difficulty": difficulty,
                          "confidence": decision["confidence"]})

    judge_mod.record(kind, f"plan with {len(plan_steps)} steps", answer, None)

    return {"check": check_id, "verdict": "routed", "confidence": None,
            "escalate": any_escalate, "detail": {"steps": steps_out}}


# ============================================================================
# #23 — worked-example picker
# ============================================================================
#
# Candidates competing for ONE slot: a single Choice over `examples`, with an
# explicit "none of these fits" option, never one Noul per example.

EXAMPLE_OPTION_CAP = 254  # a Choice caps at 255 options; leave room for none-of-these
EXAMPLE_SUMMARY_CHARS = 180
EXAMPLE_NONE = "none of these fits"
EXAMPLE_MIN_CONFIDENCE = 0.5


def _rank_examples(task_text, examples, cap):
    if len(examples) <= cap:
        return list(examples)
    query = _tokenize(task_text)
    scored = []
    for example in examples:
        words = _tokenize(f"{example.get('title') or ''} {example.get('summary') or ''}")
        overlap = len(query & words)
        scored.append((overlap, str(example.get("id") or ""), example))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [example for _, _, example in scored[:cap]]


def pick_example(task_text, examples: list, *, client=None):
    """One choice over past solved examples, plus "none fits".

    `examples` is a list of {"id", "title", "summary"}. When there are more
    than EXAMPLE_OPTION_CAP, a cheap offline keyword-overlap pass trims to
    that cap before asking Jev, the same free-lexical-trim shape
    ops/jev_defect_class.py uses ahead of its own Choice over 320 classes.

    Returns the standard check dict; `detail.example_id` is the chosen
    example's id, or None when nothing fit.

    TRIGGER: an empty `examples` list skips Jev and returns "none_fits".
    """
    check_id = "example_picker"
    kind = "supervise.example_picker"
    if not examples:
        return _skip(check_id, "none_fits",
                     {"reason": "no worked examples supplied", "example_id": None})

    trimmed = _rank_examples(task_text, examples, EXAMPLE_OPTION_CAP)
    try:
        tsc = client or _client()
        judge_mod = _judge()
        options = {}
        for example in trimmed:
            eid = str(example.get("id"))
            title = (example.get("title") or "").strip()
            summary = (example.get("summary") or "").strip()[:EXAMPLE_SUMMARY_CHARS]
            options[eid] = f"{title} — {summary}" if summary else (title or eid)
        options[EXAMPLE_NONE] = ("None of the worked examples listed solves the "
                                 "same kind of problem as `state.task_text` closely "
                                 "enough to be worth reading before starting.")
        questions = {"best_example": tsc.choice(
            "`state.task_text` describes a new piece of work. Which past worked "
            "example, if any, solves the closest problem and would be worth "
            "reading before starting this one?", options)}
        answer = judge_mod.judge({"task_text": task_text}, questions, client=tsc)
    except Exception as exc:
        return _unavailable(check_id, kind, task_text, exc)

    decision = judge_mod.read(answer, "best_example", min_confidence=EXAMPLE_MIN_CONFIDENCE)
    judge_mod.record(kind, task_text[:160], answer, None)

    chosen = decision["value"]
    if chosen in (None, EXAMPLE_NONE):
        return {"check": check_id, "verdict": "none_fits",
                "confidence": decision["confidence"], "escalate": bool(decision["escalate"]),
                "detail": {"example_id": None, "candidate_count": len(trimmed)}}
    return {"check": check_id, "verdict": "matched", "confidence": decision["confidence"],
            "escalate": bool(decision["escalate"]),
            "detail": {"example_id": chosen, "candidate_count": len(trimmed)}}
