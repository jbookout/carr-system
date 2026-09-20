"""Read the codebase for slop and for bugs, with a judgment instead of a grep.

WHY A JUDGMENT AND NOT MORE PATTERNS. Every signature worth scanning for here
is ambiguous at the pattern level, and the ambiguity is the whole problem. A
hook that catches every exception and returns is CORRECT — a gate that raises
wedges the session, so failing open is the design. The same three lines in a
data path are a failure nobody will ever see. 205 of them are in this tree and
a grep cannot tell you which kind it found. Neither can a rule that says "avoid
bare excepts"; it gets suppressed everywhere and stops meaning anything.

THE SHAPE, and it is the documented one rather than the obvious one. A cheap
deterministic pass finds CANDIDATE REGIONS. Only then does a judgment read
them, and it reads each region ONCE with every question asked together, since
independent questions about one subject belong in a single request — measured
by the vendor at 12.2x cheaper than asking them one at a time, and each
question is still scored on its own against the state.

QUESTIONS ARE NARROW ON PURPOSE. "Is this slop?" is a broad question hiding
several judgments, which is the documented mistake. Each question below decides
one thing, states its own criteria, and carries its full meaning in its text —
the ids are for this module and are never sent.

NOTHING HERE DECIDES ANYTHING. It produces a reading list ordered by
probability. A finding is a place to look, and the removal is a person's call
made against the code, because the cost of deleting a live failure path is
far higher than the cost of reading a false positive.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Region sizing. Big enough that the judgment can see why the code is there,
# small enough that the state is not filled with detail unrelated to the
# decision -- accuracy falls as a state fills with the irrelevant.
CONTEXT_BEFORE = 14
CONTEXT_AFTER = 10
MAX_REGION_CHARS = 2600
WORKERS = 8
TIMEOUT_SECONDS = 90.0
REPORT_AT = 0.55


def _client():
    spec = importlib.util.spec_from_file_location(
        "typesafe_client", os.path.join(REPO, "ops", "typesafe_client.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- the cheap pass -------------------------------------------------------
#
# Each entry is a signature that is AMBIGUOUS by design: it must match the
# innocent case too, or the judgment has nothing to rule on and the pass is
# just a grep with extra steps.

SIGNATURES = (
    ("swallowed_failure",
     re.compile(r"except\s+(?:Exception|BaseException|:)[^\n]*:\n\s+(?:pass|return\b[^\n]*)\n")),
    # Only the shape that actually bites: a coercion carrying its own default.
    # str(x) alone is 2,770 places and nearly all of them are printing; str(x
    # or "") is the one that turns None into the truthy string "None" while
    # LOOKING like it defends against None.
    ("truthiness_coercion",
     re.compile(r"str\(\s*[^()\n]*\bor\b[^()\n]*\)")),
    ("mutable_default",
     re.compile(r"def\s+\w+\([^)]*=\s*(?:\[\]|\{\})[^)]*\)")),
    ("unclosed_resource",
     re.compile(r"^\s*\w+\s*=\s*(?:open|socket\.socket|subprocess\.Popen)\(", re.M)),
    ("check_then_use",
     re.compile(r"os\.path\.exists\([^)]+\)[^\n]*\n(?:[^\n]*\n){0,3}?[^\n]*(?:open|remove|unlink|rename)\(")),
    ("silent_numeric_fallback",
     re.compile(r"except\s*(?:\([^)]*\)|\w+)[^\n]*:\n\s+(?:\w+\s*=\s*(?:0|None|\[\]|\{\}|\"\"|''))\n")),
    ("broad_sleep_retry",
     re.compile(r"(?:time\.)?sleep\(\s*\d")),
)


def tracked_sources(repo=REPO, suffixes=(".py", ".mjs", ".js")):
    out = subprocess.run(["git", "ls-files"], capture_output=True, text=True,
                         cwd=repo, timeout=120).stdout
    return [f for f in out.splitlines()
            if f.endswith(suffixes) and not f.startswith("node_modules")]


def regions(paths, repo=REPO):
    """Candidate regions, each carrying enough context to be judged."""
    found = []
    for rel in paths:
        try:
            text = open(os.path.join(repo, rel), encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        lines = text.splitlines()
        for kind, pattern in SIGNATURES:
            for match in pattern.finditer(text):
                line_no = text[:match.start()].count("\n") + 1
                lo = max(0, line_no - 1 - CONTEXT_BEFORE)
                hi = min(len(lines), line_no + CONTEXT_AFTER)
                snippet = "\n".join(lines[lo:hi])[:MAX_REGION_CHARS]
                found.append({"path": rel, "line": line_no, "kind": kind,
                              "code": snippet})
    return _collapse(found)


def _collapse(found, window=CONTEXT_BEFORE + CONTEXT_AFTER):
    """One region per neighbourhood, carrying every reason it was flagged.

    Signatures overlap constantly -- a swallowed failure sits inside the same
    twenty lines as the numeric fallback it assigns. Judging that twice costs
    two requests and returns two nearly identical answers about one piece of
    code, which is a measurement error as much as a cost.
    """
    by_file = {}
    for item in found:
        by_file.setdefault(item["path"], []).append(item)
    out = []
    for path, items in by_file.items():
        items.sort(key=lambda i: i["line"])
        current = None
        for item in items:
            if current and item["line"] - current["line"] <= window:
                if item["kind"] not in current["kind"].split("+"):
                    current["kind"] += "+" + item["kind"]
                continue
            current = dict(item)
            out.append(current)
    return out


def dead_functions(repo=REPO):
    """Module-level functions no tracked file mentions by name.

    Deliberately a separate pass: this one is NOT ambiguous enough to need a
    judgment on its own, but the judgment is still asked whether removing it
    would lose a behaviour, because a name reached only through getattr or a
    dispatch table looks identical to a dead one from here.
    """
    paths = [p for p in tracked_sources(repo) if p.endswith(".py")]
    corpus = {}
    for rel in paths:
        try:
            corpus[rel] = open(os.path.join(repo, rel), encoding="utf-8",
                               errors="ignore").read()
        except OSError:
            continue
    whole = "\n".join(corpus.values())
    counts = {}
    for rel, text in corpus.items():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                if len(re.findall(r"\b%s\b" % re.escape(node.name), whole)) <= 1:
                    counts[f"{rel}::{node.name}"] = node.lineno
    return counts


# --- the questions --------------------------------------------------------
#
# One judgment each. Written so a person reading only the question knows what
# a yes means, because a yes here sends someone to read code.

QUESTIONS = {
    "failure_leaves_no_trace":
        "The code in `region.code` handles a failure. Does that handling discard "
        "the failure with NO trace at all — no log line, no counter, no receipt, "
        "no re-raise, no returned error — so that when it fires nobody can "
        "afterwards tell that it did? Answer no if it records the failure "
        "anywhere, and answer no if the failure is genuinely uninteresting "
        "(a best-effort cleanup, an optional cache, a probe whose absence is "
        "the normal case).",

    "swallow_is_wrong_here":
        "Assume the code in `region.code` does swallow a failure silently. Is "
        "that WRONG in this specific place — meaning a real fault could pass "
        "through it and be read downstream as success? Answer no when failing "
        "quietly is the correct design here, which is the case for a hook or "
        "gate that must never wedge a session, for optional telemetry, and for "
        "cleanup that runs after the work is already done.",

    # REWRITTEN after a polarity check: the first version scored the harmless
    # printing case (0.62) ABOVE the real guard bug (0.44), because it asked
    # whether the coercion was present rather than whether anything downstream
    # was fooled by it. Presence is what the regex already found; the judgment
    # is only worth a request if it rules on the consequence.
    "truthy_coercion_bug":
        "In `region.code` a value is coerced with str(). Is the RESULT of that "
        "coercion then used in a truth test, an emptiness check, a guard, or a "
        "condition that decides whether to proceed? That is the bug, because "
        "str(None) is the four-character string 'None' and str([]) is '[]', "
        "both truthy, so the guard admits the very inputs it was written to "
        "reject. Answer NO when the coerced value is only formatted, printed, "
        "logged, concatenated or returned — those are correct uses of str() "
        "and no decision depends on them.",

    "boundary_bug":
        "Does the code in `region.code` contain an off-by-one, an inverted "
        "comparison, an inclusive bound that should be exclusive, or a slice "
        "or index that can run past the end of what it reads? Answer yes only "
        "for a concrete case you can point at, not for code that merely "
        "involves arithmetic.",

    "state_can_change_between":
        "Does the code in `region.code` check a condition about the world — a "
        "file existing, a lock being free, a row being absent — and then act on "
        "that condition in a separate step, so the world could change in "
        "between and the action fail or do the wrong thing? Answer no when the "
        "gap is harmless because the action is idempotent or the failure is "
        "caught and handled correctly.",

    "comment_no_longer_true":
        "Do the comments or the docstring in `region.code` describe behaviour "
        "the code does not have — naming a parameter, a return value, a file, a "
        "threshold or a sequence of steps that does not match what is written "
        "below them? Answer no for a comment that is merely terse or that "
        "explains why rather than what.",

    "unreachable_or_dead":
        "Is some part of the code in `region.code` unable to run — after an "
        "unconditional return or raise, inside a condition that cannot be true, "
        "or in a branch a preceding check already excluded?",

    "duplicated_and_diverged":
        "Does the code in `region.code` look like a copy of logic that belongs "
        "somewhere shared, such that fixing a bug here would leave the other "
        "copy wrong? Answer yes only when the code itself shows the sign — a "
        "comment saying it is copied, a near-identical helper name, or a "
        "constant that plainly has to agree with one defined elsewhere.",
}


def review_one(region, client=None, api_key=None):
    """Every question about one region, in ONE request."""
    tsc = client or _client()
    questions = {qid: tsc.noul(text) for qid, text in QUESTIONS.items()}
    state = {"region": {"path": region["path"], "line": region["line"],
                        "why_it_was_flagged": region["kind"],
                        "code": region["code"]}}
    answer = tsc.ask(state, questions, timeout=TIMEOUT_SECONDS, api_key=api_key)
    scores = {qid: answer_value(body)
              for qid, body in (answer.get("answers") or {}).items()}
    model = answer.get("model")
    if isinstance(model, str) and model.strip():
        scores["_model"] = model
    return scores


def answer_value(body):
    """Read an answer without guessing which key holds it.

    Every answer names its own primitive in `type`, and the answer sits under
    the key of that same name: a noul returns {"type":"noul","noul":0.93}, a
    choice returns {"type":"choice","choice":"blue", ...}. So the type IS the
    key, which is derived from the response rather than remembered.

    THIS COST A WHOLE SCAN. The first version of this function read
    body["probability"], a key that does not exist, and float(None or 0.0) is
    0.0 -- so 426 regions came back scored zero and looked like a clean
    codebase. It was caught by running two known-bad snippets through it and
    seeing them score the same as two known-good ones, which is the only
    reason it was caught at all. A reader that cannot fail loudly has to be
    checked against an answer known in advance.
    """
    kind = body.get("type")
    if kind and kind in body:
        value = body[kind]
        return float(value) if isinstance(value, (int, float)) else value
    raise KeyError(
        "answer names type %r but carries no key of that name: %r"
        % (kind, sorted(body)))


def review(candidates, *, workers=WORKERS, client=None, api_key=None,
           on_done=None):
    tsc = client or _client()
    results = []

    def one(region):
        try:
            return region, review_one(region, client=tsc, api_key=api_key)
        except Exception as exc:                       # a scan never aborts
            return region, {"_error": str(exc)[:160]}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for region, scores in pool.map(one, candidates):
            region = dict(region)
            region["scores"] = scores
            results.append(region)
            if on_done:
                on_done(region)
    return results


def findings(results, floor=REPORT_AT):
    """Flatten to one row per question that cleared the floor.

    A region is not a finding; a QUESTION about a region is. The same twenty
    lines can be a correct fail-open and a stale comment at once, and folding
    those into one score would hide both.
    """
    rows = []
    for item in results:
        for qid, probability in (item.get("scores") or {}).items():
            if qid.startswith("_"):
                continue
            if probability >= floor:
                rows.append({"path": item["path"], "line": item["line"],
                             "question": qid, "probability": probability,
                             "flagged_as": item["kind"]})
    rows.sort(key=lambda r: -r["probability"])
    return rows
