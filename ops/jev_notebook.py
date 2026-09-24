"""jev_notebook.py — an append-only mistake notebook (open loop #629, check #15).

WHAT THIS IS FOR. A coding agent that fails the same way twice has not been
told about the first time. This module gives it somewhere to write that down —
record_mistake() — and somewhere to look it up before it repeats the mistake —
recall_mistakes() — so the lesson from a past task can ride into a NEW task's
prompt as a few plain lines, the same way jev_precheck.py's environment facts
ride into a judgment: named, concrete, and gathered by code rather than
guessed.

RECORDING IS DETERMINISTIC. record_mistake() never calls Jev. What went wrong,
what the fix was, and where the mistake was noticed are facts a caller already
has in hand at the moment of noticing; there is no judgment left to make, only
a row to write. The notebook is out/jev-mistake-notebook.jsonl, append-only —
the shape every shadow log in this project already uses (jev_judge.SHADOW_LOG,
ops/jev_rule_select.py's SHADOW_LOG) — because a notebook that can be edited in
place can also be quietly edited to remove an inconvenient lesson.

RECALL IS THE SAME TWO-STAGE SHAPE AS ops/jev_rule_select.py, for the same
reason. A cheap, free, deterministic pass narrows a notebook that will grow
without bound to a short shortlist — here, token overlap between the new task
and each entry's task_text, rather than an embedding index this project does
not have — and only the shortlist is ever sent to Jev. Within the shortlist the
candidates are COMPETING for a fixed, small "prepend budget" (nobody reads ten
notebook entries prepended to a prompt any more than they read twenty surfaced
rules), so narrowing them is ONE Choice over the shortlist, not a Noul per
entry — the same "competing for one slot" test ops/jev_judge.py's docstring
names. The top few the Choice ranks are then each confirmed relevant with one
Noul, batched into the SAME request as the Choice per the "ask every
independent question about one subject in one request" rule, because the
Choice narrows attention and the Nouls are what actually decide inclusion —
a top-ranked entry can still turn out to not really apply once looked at
directly, same as a top-ranked rule in jev_rule_select's shortlist can fail its
own binding question.

CLASSIFYING A NEW MISTAKE INTO AN EXISTING KIND is the same shape again: kinds
already in the notebook are candidates competing for one slot (this mistake IS
one of these kinds), plus the escape "new kind" for when it genuinely is not
— never a Noul per kind, and never a silent invention of a kind name Jev was
not offered.

NEVER FALL BACK TO "no relevant history" ON A WEAK SIGNAL. recall_mistakes()
returns escalate=True when Jev is unavailable or confidence is low, but still
returns whatever the deterministic overlap shortlist found — the same standing
lesson ops/jev_best_of.py applies to candidate selection: low confidence is
information for the caller, not permission to silently produce nothing.

IT IS A LIBRARY AND MUST STAY ONE. No shebang and no main guard: either turns a
.py file into a registered script entrypoint in the sealed source inventory,
moves the frontier, and owes a forward-only registry successor. The detector is
a regex over the whole file with no notion of docstrings, so the construct is
described here and never spelled. ops/typesafe_client.py carries the long form.
"""

import importlib.util
import json
import os
import re
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOTEBOOK_PATH = os.path.join(REPO, "out", "jev-mistake-notebook.jsonl")

# How many entries the deterministic overlap pass keeps before anything is
# sent to Jev. Mirrors ops/jev_rule_select.py's SHORTLIST: cheap and free, so
# generous is fine, and Jev's own Choice narrows it further from there.
SHORTLIST = 20

# The prepend budget: nobody reads ten notebook entries stuffed into a prompt.
# Mirrors ops/jev_rule_select.py's MAX_SURFACED for the identical reason.
DEFAULT_LIMIT = 3

NONE_RELEVANT = "none of these notebook entries are relevant to the new task"
NEW_KIND = "new kind: none of the existing kinds fit"

_WORD = re.compile(r"[a-zA-Z0-9_]+")
_STOPWORDS = {
    "the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "is", "are",
    "with", "this", "that", "it", "be", "as", "at", "by", "from", "was", "were",
}


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


def _tokens(text):
    return {w.lower() for w in _WORD.findall(text or "") if w.lower() not in _STOPWORDS
            and len(w) > 2}


def _read_all(path=NOTEBOOK_PATH):
    """Every row in the notebook, oldest first. Missing file is an empty log."""
    rows = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return rows


def record_mistake(kind, task_text, what_went_wrong, fix, *, source,
                    notebook_path=NOTEBOOK_PATH):
    """Append one mistake to the notebook. Deterministic — no Jev is asked.

    `kind` is a short caller-chosen label (see classify_kind() for picking an
    existing one). `source` names where this was noticed (a check id, a
    session, a reviewer) so a later reader can trace it back. Never raises: a
    notebook write that fails is logged nowhere useful anyway, since the
    notebook IS the log, so the failure is swallowed the same way every
    shadow-log writer in this project swallows one.

    Returns the row written, including its `id` (an index into the notebook at
    write time — stable for the life of the file, not across a rewrite).
    """
    row = {
        "id": None,  # filled in below once the current length is known
        "at": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "task_text": (task_text or "")[:2000],
        "what_went_wrong": (what_went_wrong or "")[:2000],
        "fix": (fix or "")[:2000],
        "source": source,
    }
    try:
        os.makedirs(os.path.dirname(notebook_path), exist_ok=True)
        existing = 0
        if os.path.exists(notebook_path):
            with open(notebook_path, "r", encoding="utf-8") as handle:
                existing = sum(1 for line in handle if line.strip())
        row["id"] = existing
        with open(notebook_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    except OSError:
        pass
    return row


def _overlap_shortlist(task_text, rows, limit=SHORTLIST):
    """Deterministic pass: entries ranked by token overlap with `task_text`.

    Free and local, same role as jev_rule_select.narrow()'s ranking Choice but
    without a request — token overlap is cheap enough to run over the whole
    notebook every time, so there is no reason to spend a Jev call narrowing
    what a set() intersection already narrows for nothing.
    """
    query = _tokens(task_text)
    if not query:
        return rows[-limit:]
    scored = []
    for row in rows:
        entry_tokens = _tokens(row.get("task_text", "")) | _tokens(row.get("what_went_wrong", ""))
        overlap = len(query & entry_tokens)
        if overlap:
            scored.append((overlap, row))
    scored.sort(key=lambda item: -item[0])
    return [row for _, row in scored[:limit]]


def _entry_label(row):
    return f"entry_{row['id']}" if row.get("id") is not None else f"entry_{id(row)}"


def recall_mistakes(task_text, *, limit=DEFAULT_LIMIT, client=None, judge=None,
                     notebook_path=NOTEBOOK_PATH, shortlist=SHORTLIST):
    """Notebook lines worth prepending to a prompt for `task_text`.

    Two stages, same shape as ops/jev_rule_select.py's narrow()+select(): a
    free deterministic token-overlap pass produces a shortlist, then ONE
    request holding a Choice over the shortlist (ranks it, "none relevant" is
    an explicit option) PLUS one Noul per top-ranked candidate confirming it
    actually applies — batched together per the "ask every independent
    question about one subject in one request" rule.

    Returns {"check": "notebook_recall", "verdict": [ids of entries to use],
    "confidence": float | None, "escalate": bool,
    "detail": {"lines": [str, ...], ...}}. `detail["lines"]` is what a caller
    prepends to a prompt — plain text, one line per relevant entry.

    NEVER raises. An unavailable judge falls back to the deterministic
    shortlist itself (best-effort, escalate=True) rather than returning
    nothing: a caller with a weak signal should still see it and decide.
    """
    judge = judge or _sibling("jev_judge")
    rows = _read_all(notebook_path)
    if not rows:
        return {"check": "notebook_recall", "verdict": [], "confidence": None,
                "escalate": False, "detail": {"lines": [], "reason": "notebook is empty"}}

    shortlisted = _overlap_shortlist(task_text, rows, limit=shortlist)
    if not shortlisted:
        return {"check": "notebook_recall", "verdict": [], "confidence": None,
                "escalate": False,
                "detail": {"lines": [], "reason": "no notebook entry shares any token with the task"}}

    labels = {_entry_label(row): row for row in shortlisted}
    tsc = client or _sibling("typesafe_client")
    options = {label: f"{label}: kind={row.get('kind')} — {row.get('what_went_wrong', '')[:150]}"
               for label, row in labels.items()}
    options[NONE_RELEVANT] = (
        "None of the shortlisted notebook entries is actually relevant to the "
        "new task in state.task_text — topic overlap alone is not relevance; "
        "the entry's mistake must be one this new task could plausibly repeat.")
    rank_q = tsc.choice(
        "state.task_text is a new task about to be attempted. state.entries "
        "lists past mistakes from similar work. Which past mistake is most "
        "likely to recur on this new task?", options)

    top_n = min(3, len(labels))
    subject = {
        "task_text": (task_text or "")[:2000],
        "entries": {label: {"kind": row.get("kind"), "what_went_wrong": row.get("what_went_wrong"),
                              "fix": row.get("fix")} for label, row in labels.items()},
    }
    questions = {"rank": rank_q}
    try:
        answer = judge.judge(subject, questions, client=client)
    except Exception as exc:
        try:
            judge.record("supervise.notebook_recall", (task_text or "")[:200],
                         None, None, error=exc)
        except Exception:
            pass
        fallback_lines = [_format_line(row) for row in shortlisted[:limit]]
        return {"check": "notebook_recall",
                "verdict": [row["id"] for row in shortlisted[:limit]],
                "confidence": None, "escalate": True,
                "detail": {"lines": fallback_lines,
                            "reason": f"Jev unavailable ({type(exc).__name__}); "
                                      "falling back to the deterministic overlap shortlist"}}

    rank_answer = answer["answers"]["rank"]
    top_label = rank_answer.get("choice")
    confidence = rank_answer.get("confidence")
    confidence = None if confidence is None else float(confidence)

    if top_label in (None, NONE_RELEVANT) or top_label not in labels:
        judge.record("supervise.notebook_recall", (task_text or "")[:200], answer,
                     existing_decision=None, note="none_relevant")
        return {"check": "notebook_recall", "verdict": [], "confidence": confidence,
                "escalate": confidence is None,
                "detail": {"lines": [], "reason": "Jev found nothing in the shortlist relevant"}}

    # ONE noul per top-ranked candidate, in a SECOND small request scoped to
    # just those few — keeps the first request to a single Choice (cheap,
    # covers the whole shortlist) and asks the close-look question only about
    # the entries worth a close look, mirroring jev_rule_select's narrow-then-
    # score split.
    ordered = [top_label] + [lbl for lbl in labels if lbl != top_label][:top_n - 1]
    confirm_qs = {
        lbl: tsc.noul(
            f"Notebook entry `{lbl}` (state.entries['{lbl}']) describes a past "
            "mistake. Given the new task in state.task_text, is this specific "
            "past mistake likely to recur or otherwise worth warning about here?",
            true="the same kind of mistake could plausibly happen again on this task",
            false="this past mistake does not apply to this task")
        for lbl in ordered
    }
    try:
        confirm_answer = judge.judge(subject, confirm_qs, client=client)
    except Exception as exc:
        judge.record("supervise.notebook_recall", (task_text or "")[:200], answer,
                     existing_decision=None,
                     note=f"rank ok, confirm failed: {type(exc).__name__}")
        chosen = [labels[top_label]]
        return {"check": "notebook_recall", "verdict": [chosen[0]["id"]],
                "confidence": confidence, "escalate": True,
                "detail": {"lines": [_format_line(chosen[0])],
                            "reason": "confirmation pass unavailable; used the top Choice pick only"}}

    included = [lbl for lbl in ordered
                if float(confirm_answer["answers"][lbl]["noul"]) >= 0.5][:limit]
    if not included:
        included = [top_label]
    chosen_rows = [labels[lbl] for lbl in included]
    judge.record("supervise.notebook_recall", (task_text or "")[:200], answer,
                 existing_decision=None, note=f"included={included}")
    return {"check": "notebook_recall", "verdict": [row["id"] for row in chosen_rows],
            "confidence": confidence,
            "escalate": confidence is None or confidence < 0.5,
            "detail": {"lines": [_format_line(row) for row in chosen_rows],
                        "model": answer.get("model")}}


def _format_line(row):
    return (f"[past mistake, kind={row.get('kind')}] {row.get('what_went_wrong', '')} "
            f"— fix: {row.get('fix', '')}")


def classify_kind(what_went_wrong, task_text, *, client=None, judge=None,
                   notebook_path=NOTEBOOK_PATH):
    """Which existing `kind` a new mistake belongs to, or that it needs a new one.

    Existing kinds are candidates competing for one slot, so this is ONE
    Choice over the distinct kinds already in the notebook plus the explicit
    escape NEW_KIND — never a Noul per kind, and never an invented kind name.

    Returns {"check": "notebook_classify", "verdict": <kind> | "new_kind" |
    "unavailable", "confidence": float | None, "escalate": bool,
    "detail": {...}}. NEVER raises.
    """
    judge = judge or _sibling("jev_judge")
    rows = _read_all(notebook_path)
    kinds = sorted({row["kind"] for row in rows if row.get("kind")})
    if not kinds:
        return {"check": "notebook_classify", "verdict": "new_kind",
                "confidence": None, "escalate": False,
                "detail": {"reason": "notebook has no existing kinds yet"}}

    tsc = client or _sibling("typesafe_client")
    options = {kind: f"this mistake is the same kind as existing notebook kind `{kind}`"
               for kind in kinds}
    options[NEW_KIND] = ("this mistake does not match any existing kind and needs "
                          "a new one")
    subject = {"task_text": (task_text or "")[:1500],
               "what_went_wrong": (what_went_wrong or "")[:1500],
               "existing_kinds": kinds}
    question = {"kind": tsc.choice(
        "state.what_went_wrong describes a new mistake noticed on the task in "
        "state.task_text. Which of state.existing_kinds is this the same kind "
        "of mistake as?", options)}
    try:
        answer = judge.judge(subject, question, client=client)
    except Exception as exc:
        try:
            judge.record("supervise.notebook_classify", (what_went_wrong or "")[:200],
                         None, None, error=exc)
        except Exception:
            pass
        return {"check": "notebook_classify", "verdict": "unavailable",
                "confidence": None, "escalate": True,
                "detail": {"reason": f"{type(exc).__name__}: {exc}", "existing_kinds": kinds}}

    kind_answer = answer["answers"]["kind"]
    choice_val = kind_answer.get("choice")
    confidence = kind_answer.get("confidence")
    confidence = None if confidence is None else float(confidence)
    verdict = "new_kind" if choice_val in (None, NEW_KIND) else choice_val
    judge.record("supervise.notebook_classify", (what_went_wrong or "")[:200], answer,
                 existing_decision=None)
    return {"check": "notebook_classify", "verdict": verdict, "confidence": confidence,
            "escalate": confidence is None or confidence < 0.5,
            "detail": {"existing_kinds": kinds, "model": answer.get("model")}}
