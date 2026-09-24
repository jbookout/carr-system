#!/usr/bin/env python3
"""tools/pr-pipeline/pipeline.py — the scripted review-and-merge pipeline.

WHY THIS EXISTS. Decisions 17ef11fa and b729859d (2026-09-24, Joe): "CARR runs
itself... Even the review, merge, and release steps can be triggered by
scripts too. You can engineer automated agent processes." Until this file, an
orchestrating Claude session closed that loop by hand for every agent-opened
PR: dispatch a reviewer, read its verdict, dispatch a fixer on BLOCK, merge on
APPROVE+green. This module is that loop, as a launchd-ticked script instead of
a human-supervised session. A separate, later pipeline consumes the merge
events this one emits and owns RELEASE; this file stops at MERGE.

SCOPE. Only pull requests opened by an agent session are touched — never
Joe's own PRs, never anything labelled `pipeline:hold` (see `in_scope`).
Everything here is read-mostly against GitHub (`gh` CLI, the local login) and
write-only through two narrow doors: `gh pr merge --squash --match-head-commit
<exact-sha>` for MERGE, and `gh pr comment` to post a verdict a reviewer
produced in the Model Room (the reviewer itself never gets GitHub write
access). GitHub auto-merge is never enabled and no repository setting is ever
touched.

THE VERDICT LINE FORMAT. A reviewer dispatched by this pipeline is told to
answer with exactly one line of this shape, anywhere in its Model Room reply:

    CARR-PR-VERDICT: APPROVE pr=<N> sha=<head-sha-or-prefix> reviewer=<target> key=<dispatch-key>
    CARR-PR-VERDICT: BLOCK pr=<N> sha=<head-sha-or-prefix> reviewer=<target> key=<dispatch-key>

`parse_verdict` is the only reader of that line's TEXT, and it is deliberately
strict: the token must be exactly `APPROVE` or `BLOCK` (never case-folded),
`pr=` must equal the PR this dispatch was about, `sha=` must be a genuine
prefix of this pipeline's OWN recorded head SHA for that PR (a moved head
voids the verdict), and `key=` must equal the exact dispatch key this
pipeline minted for THIS review round — a verdict naming a different key is a
reply to a different, older dispatch and is refused. A reply with no matching
line, a misspelled token, a wrong PR number, a stale SHA, a wrong key, or two
conflicting verdict lines in the same reply all return `None` — never a
verdict, and never treated as approval by default.

Text alone is not enough to trust, though: `scan_room_for_verdicts` never
hands `parse_verdict` the body of just any room turn that happens to mention
the right PR and SHA. A candidate turn must ALSO be `origin_channel == "mcp"`
(server-derived provenance, the same gate `queue_grammar.py`'s `_origin`
enforces — never forgeable by a client) and carry a room `seq` strictly after
this pipeline's own dispatch turn. Only a turn passing BOTH the provenance/seq
binding AND the text-level key/sha match is ever treated as a verdict. Among
several qualifying verdicts for the same SHA, the one with the highest seq
wins — a later BLOCK overrides an earlier APPROVE, and a stale replay can
never re-win an already-superseded verdict. See `decide`'s "latest-verdict-
wins" branch and the `test_verdict_*`/`test_scan_room_*` selftests, which
specifically cover a forged/unbound turn, a stale SHA, and BLOCK overriding
APPROVE.

When this pipeline discovers a verdict, IT posts the PR comment itself — the
reviewer never needs GitHub write access — with two lines a consumer can key
on directly: `Reviewed-SHA: <sha>` and `Verdict: APPROVE|BLOCK`. The merge
event's `reviewer` field (below) is populated only from this pipeline's own
verified state, never parsed back out of comment text.

THE MERGE EVENT FORMAT. On every successful merge, one JSON line is appended
to `out/pr-pipeline-merge-events.jsonl`, schema_version 1:

    {"schema_version": 1, "event": "pr_merged", "repo": "<owner/name>",
     "pr_number": <int>, "head_sha": "<40-hex>", "merge_commit_sha": "<40-hex>",
     "base_branch": "<str>", "merged_at": "<iso8601 UTC>",
     "reviewer": "<target>", "review_sha": "<sha reviewer approved>"}

A later release pipeline tails this file. No record-layer verb was found that
fits a generic engineering "PR merged" event honestly: `record-signal` is
scoped to investigations and `log-activity` requires a CRM party/deal link,
so this pipeline does not force either — the JSONL file is the documented,
authoritative interchange format. See the PR description for the search that
led here.

THE KILL SWITCH. Two independent gates, checked before any dispatch or merge:
`ops/config/pr-pipeline-policy.json`'s `"enabled"` field (tracked, reviewed
like any other config change) and the untracked file at
`out/pr-pipeline.disable` (a same-machine, no-review-needed emergency stop —
its mere presence disables the tick, whatever it contains). Either one being
"off" skips the whole tick with no dispatch and no merge; the tick still
records that it ran and why it did nothing, through the normal
bin/run-scheduled.sh receipt.

WHY SHA-KEYED STATE. `fresh_entry_if_new_sha` resets a tracked PR's whole
state to `needs_review` the moment its head SHA changes from what this
pipeline last recorded, discarding blocked-round counters and any prior
verdict. A new push is a new candidate; nothing about the old commit's review
should carry an unearned pass onto a commit no one has read.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools" / "room-bridge"))
import verb_io  # noqa: E402  (the sanctioned, server-derived-origin path to add-room-turn)

STATE_PATH = REPO / "out" / "pr-pipeline-state.json"
MERGE_EVENTS_PATH = REPO / "out" / "pr-pipeline-merge-events.jsonl"
ACTIONS_LOG_PATH = REPO / "out" / "pr-pipeline-actions.jsonl"
POLICY_PATH = REPO / "ops" / "config" / "pr-pipeline-policy.json"
KILL_SWITCH_PATH = REPO / "out" / "pr-pipeline.disable"
DEFAULT_ROOM = "partner-line"

JOE_LOGIN = "jbookout"
HOLD_LABEL = "pipeline:hold"
AGENT_BRANCH_PREFIXES = ("claude/", "codex/", "agent/", "flash/")
AGENT_AUTHOR_LOGINS: frozenset[str] = frozenset()  # extend via policy file if a bot login is ever added
MAX_BLOCKED_ROUNDS = 3

VERDICT_RE = re.compile(
    r"(?m)^\s*CARR-PR-VERDICT:\s+(APPROVE|BLOCK)\s+pr=(\d+)\s+sha=([0-9a-f]{7,40})"
    r"\s+reviewer=([a-z][a-z0-9-]{0,40})\s+key=([a-z0-9][a-z0-9-]{0,79})\s*$"
)

STRONG_REVIEW_PATH_PREFIXES = (
    "migrations/", ".github/workflows/", "hooks/", "ops/githooks/",
)
STRONG_REVIEW_EXACT = {"db/schema.sql", ".claude/settings.json"}
STRONG_REVIEW_CONTENT_RE = re.compile(
    r"security\s+definer|\bauth\b|credential|secret|permission", re.IGNORECASE
)

STATES = {
    "needs_review", "reviewing", "approved", "blocked", "fixing", "merged", "escalated",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ───────────────────────── scope ─────────────────────────

def in_scope(pr: dict, *, joe_login: str = JOE_LOGIN, hold_label: str = HOLD_LABEL,
             agent_author_logins: frozenset[str] = AGENT_AUTHOR_LOGINS) -> bool:
    """An agent-opened PR: never Joe's own, never held, never a draft.

    "Agent-opened" is defined precisely, not impressionistically: the head
    branch starts with one of AGENT_BRANCH_PREFIXES (the real naming this repo's
    agent sessions use — `claude/<adjective>-<name>-<hash>` and similar), OR the
    author login is one of AGENT_AUTHOR_LOGINS (empty today; a bot account, if
    one is ever added, extends this set rather than the branch heuristic).
    """
    if pr.get("isDraft"):
        return False
    author = ((pr.get("author") or {}).get("login") or "").strip().lower()
    if author == joe_login.lower():
        return False
    labels = {str(l.get("name", "")).strip().lower() for l in pr.get("labels", [])}
    if hold_label.lower() in labels:
        return False
    branch = pr.get("headRefName") or ""
    if any(branch.startswith(p) for p in AGENT_BRANCH_PREFIXES):
        return True
    if author in agent_author_logins:
        return True
    return False


# ───────────────────────── reviewer strength ─────────────────────────

def classify_strength(files: list[str], diff_text: Optional[str] = None) -> str:
    """"strong" (Opus, target=claude-desktop) for migrations, auth, SECURITY
    DEFINER or hook/workflow changes; "standard" (target=claude) otherwise."""
    for f in files:
        if f in STRONG_REVIEW_EXACT:
            return "strong"
        if any(f.startswith(prefix) for prefix in STRONG_REVIEW_PATH_PREFIXES):
            return "strong"
        if STRONG_REVIEW_CONTENT_RE.search(f):
            return "strong"
    if diff_text and STRONG_REVIEW_CONTENT_RE.search(diff_text):
        return "strong"
    return "standard"


def reviewer_target(strength: str) -> str:
    return "claude-desktop" if strength == "strong" else "claude"


# ───────────────────────── verdict parsing ─────────────────────────

def parse_verdict(text: str, expected_pr: int, expected_sha: str,
                   expected_key: Optional[str] = None) -> Optional[dict]:
    """The ONLY reader of a reviewer's reply. See module docstring for the
    format and the refusal rules. Returns None on anything short of an exact,
    unambiguous, current match — never a guess, never a default approval.

    `expected_key` binds this parse to the exact dispatch this pipeline sent:
    the reviewer's task text is told to echo back the `key=` this pipeline
    minted for that review (see `build_review_task`), and a verdict line
    naming any other key — including one for a different, older dispatch of
    the same PR — is refused. This is on top of, not instead of, the
    caller-side binding in `scan_room_for_verdicts` (origin_channel, seat,
    and turn-seq-after-dispatch): a room turn can forge a plausible-looking
    body, but the caller only ever offers this function text from a turn it
    has already confirmed came from the dispatched reviewer's own reply.
    """
    if not isinstance(text, str) or not text or not expected_sha:
        return None
    matches = VERDICT_RE.findall(text)
    if not matches:
        return None
    distinct = {m[0] for m in matches}
    if len(distinct) > 1:
        return None  # conflicting verdict lines in one reply: refuse, don't guess
    verdict, pr_str, sha, reviewer, key = matches[-1]
    try:
        pr_number = int(pr_str)
    except ValueError:
        return None
    if pr_number != int(expected_pr):
        return None
    if not expected_sha.startswith(sha):
        return None  # the head moved since this review was dispatched: void
    if expected_key is not None and key != expected_key:
        return None  # names a different dispatch's key: not a reply to ours
    return {"verdict": verdict, "pr": pr_number, "sha": sha, "reviewer": reviewer, "key": key}


def extract_findings(text: str) -> str:
    """Everything in a reply that is not the verdict line itself, trimmed —
    used verbatim as the body of the PR comment this pipeline posts."""
    lines = [ln for ln in (text or "").splitlines() if not VERDICT_RE.match(ln)]
    return "\n".join(lines).strip()


# ───────────────────────── state store ─────────────────────────

def fresh_entry(head_sha: str) -> dict:
    return {
        "state": "needs_review",
        "head_sha": head_sha,
        "blocked_rounds": 0,
        "fix_reason": None,
        "dispatch_key": None,
        # The room seq of THIS pipeline's own dispatch turn. A candidate
        # verdict turn must have a strictly greater seq — "later than the
        # dispatch" — or it is refused, whatever it claims to be a reply to.
        "dispatch_seq": None,
        # The room seq of the verdict currently applied (last_verdict/reviewer
        # below). Only a verdict turn newer than this one can override it —
        # latest-verdict-wins, so a later BLOCK overrides an earlier APPROVE,
        # and a stale replay of an old APPROVE can never re-win.
        "verdict_seq": None,
        "reviewer": None,
        "last_verdict": None,
        "escalated_loop_id": None,
        "merge": None,
        "updated_at": _now_iso(),
    }


def load_state(path: Path = STATE_PATH) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(state: dict, path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def reconcile_sha(entry: Optional[dict], head_sha: str) -> dict:
    """Idempotent, SHA-keyed: an entry whose recorded head SHA no longer
    matches the PR's current head SHA is a stale read of an old commit and is
    replaced wholesale by a fresh `needs_review` entry. Calling this twice
    with the same head_sha is a no-op (idempotent), which is what lets a tick
    run any number of times against an unchanged PR safely."""
    if entry is None or entry.get("head_sha") != head_sha:
        return fresh_entry(head_sha)
    return dict(entry)


# ───────────────────────── pure state-machine decision ─────────────────────────

@dataclass
class Decision:
    entry: dict
    action: Optional[str] = None  # dispatch_review|dispatch_fix|dispatch_ci_diagnosis|request_update_branch|attempt_merge|escalate|None


def decide(entry: dict, *, checks_ok: Optional[bool], mergeable_clean: bool, behind: bool,
           verdict: Optional[dict], max_rounds: int = MAX_BLOCKED_ROUNDS) -> Decision:
    """Pure transition function: no I/O, no gh, no room. `entry` must already
    be SHA-reconciled by the caller (`reconcile_sha`). `verdict`, when given,
    is a `parse_verdict` result already confirmed to match this entry's SHA.
    """
    state = entry.get("state", "needs_review")
    out = dict(entry)
    out["updated_at"] = _now_iso()

    if state == "needs_review":
        out["state"] = "reviewing"
        return Decision(out, "dispatch_review")

    # Latest-verdict-wins, checked ahead of the per-state logic below and for
    # every state that still has a review "live" (reviewing, approved, and
    # blocked — the last of those because a late-arriving verdict for a PR
    # this pipeline already marked blocked should still be able to flip it,
    # e.g. a corrected APPROVE after a reviewer initially misread the diff).
    # `verdict["seq"]` is the room seq of the turn that carried it; a verdict
    # is applied only when it is newer than whichever verdict (if any) is
    # already recorded, so a stale or replayed turn can never re-win, and a
    # later BLOCK overrides an earlier APPROVE for the same SHA.
    if verdict is not None and state in {"reviewing", "approved", "blocked"}:
        seq = verdict.get("seq")
        current_seq = out.get("verdict_seq")
        if seq is not None and (current_seq is None or seq > current_seq):
            out["last_verdict"] = verdict["verdict"]
            out["reviewer"] = verdict["reviewer"]
            out["verdict_seq"] = seq
            out["state"] = "approved" if verdict["verdict"] == "APPROVE" else "blocked"
            return Decision(out, None)
        if state == "reviewing":
            return Decision(out, None)  # a verdict arrived but did not qualify; keep waiting

    if state == "reviewing":
        return Decision(out, None)

    if state == "blocked":
        if out.get("blocked_rounds", 0) >= max_rounds:
            out["state"] = "escalated"
            return Decision(out, "escalate")
        out["blocked_rounds"] = out.get("blocked_rounds", 0) + 1
        out["fix_reason"] = "review"
        out["state"] = "fixing"
        return Decision(out, "dispatch_fix")

    if state == "approved":
        if behind:
            return Decision(out, "request_update_branch")
        if checks_ok is False:
            if out.get("blocked_rounds", 0) >= max_rounds:
                out["state"] = "escalated"
                return Decision(out, "escalate")
            out["blocked_rounds"] = out.get("blocked_rounds", 0) + 1
            out["fix_reason"] = "ci"
            out["state"] = "fixing"
            return Decision(out, "dispatch_ci_diagnosis")
        if checks_ok is True and mergeable_clean:
            return Decision(out, "attempt_merge")
        return Decision(out, None)  # checks still running, or state not yet clean

    if state == "fixing":
        return Decision(out, None)  # waiting for a push; SHA reset handles the rest

    # merged, escalated: terminal, untouched
    return Decision(out, None)


# ───────────────────────── room dispatch ─────────────────────────

def _dispatch_key(prefix: str, repo: str, pr_number: int, head_sha: str, round_: int = 0) -> str:
    owner_repo = repo.replace("/", "-").lower()
    base = f"{prefix}-{owner_repo}-{pr_number}-{head_sha[:8]}"
    return (f"{base}-{round_}" if round_ else base)[:80]


def _deterministic_msg_id(key: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"carr-pr-pipeline:{key}"))


def build_queue_body(*, target: str, cap: str, key: str, title: str, task_text: str,
                      priority: str = "P2") -> str:
    key_slug = re.sub(r"[^a-z0-9-]", "-", key.lower())[:80].strip("-") or "task"
    head = f"@queue enqueue target={target} cap={cap} priority={priority} key={key_slug} :: {title}"
    return f"{head}\n{task_text}"


def build_review_task(repo: str, pr_number: int, head_sha: str, title: str, target: str,
                       dispatch_key: str) -> str:
    return (
        f"Review pull request #{pr_number} in {repo} at commit {head_sha}.\n"
        f"Title: {title}\n\n"
        f"Read the diff yourself (`gh pr diff {pr_number} -R {repo}`, "
        f"`gh pr view {pr_number} -R {repo} --json files,body`). You are an "
        f"independent reviewer with no repository write access; do not attempt "
        f"to push, comment on GitHub, or merge anything yourself.\n\n"
        f"Reply in this room with your findings, path:line for anything you "
        f"flag, and end your reply with EXACTLY ONE line of this shape — no "
        f"other text on that line:\n\n"
        f"  CARR-PR-VERDICT: APPROVE pr={pr_number} sha={head_sha} reviewer={target} key={dispatch_key}\n"
        f"  CARR-PR-VERDICT: BLOCK pr={pr_number} sha={head_sha} reviewer={target} key={dispatch_key}\n\n"
        f"Use BLOCK if you found anything that should stop this PR merging as-is; "
        f"APPROVE otherwise. This exact line is parsed by a script, not read by a "
        f"person — get every field exactly right, INCLUDING key={dispatch_key} "
        f"copied verbatim from this task, or your review will not be counted "
        f"(a verdict naming any other key is treated as a reply to a different "
        f"dispatch and ignored)."
    )


def build_fix_task(repo: str, pr_number: int, branch: str, head_sha: str, findings: str) -> str:
    return (
        f"Push a fix to branch `{branch}` (PR #{pr_number} in {repo}, currently at "
        f"{head_sha}) that addresses this reviewer's findings, then push. Do not "
        f"open a new PR; push directly to the existing branch.\n\n"
        f"Findings:\n{findings or '(reviewer gave no findings text; re-read the diff for likely issues)'}"
    )


def build_ci_diagnosis_task(repo: str, pr_number: int, branch: str, head_sha: str, log_excerpt: str) -> str:
    return (
        f"Required CI is failing on PR #{pr_number} in {repo}, branch `{branch}`, "
        f"commit {head_sha}. Diagnose the failure from the excerpt below. If the "
        f"cause is unrelated to this PR's own change, say so and stop (a human "
        f"or a separate session fixes shared infrastructure); otherwise push a "
        f"fix directly to `{branch}`.\n\nFailing check log excerpt:\n{log_excerpt}"
    )


def dispatch_room_task(*, target: str, cap: str, key: str, title: str, task_text: str,
                        room: str = DEFAULT_ROOM, add_room_turn: Callable[..., dict] = verb_io.add_room_turn,
                        priority: str = "P2") -> dict:
    body = build_queue_body(target=target, cap=cap, key=key, title=title, task_text=task_text, priority=priority)
    msg_id = _deterministic_msg_id(key)
    return add_room_turn(body, "carr-pr-pipeline", kind="turn", room=room, msg_id=msg_id,
                         idempotency_key=f"pr-pipeline:{key}")


# ───────────────────────── GitHub shell ─────────────────────────

class GhError(RuntimeError):
    pass


class GhClient:
    """Thin wrapper over the local `gh` login. Never touches auto-merge or
    repository settings; `merge()` is the only write path besides `comment()`."""

    def __init__(self, gh_bin: str = "gh", timeout: int = 120):
        self.gh_bin = gh_bin
        self.timeout = timeout

    def _run(self, args: list[str], input_text: Optional[str] = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [self.gh_bin, *args], input=input_text, capture_output=True, text=True,
            timeout=self.timeout,
        )

    def _json(self, args: list[str]) -> Any:
        p = self._run(args)
        if p.returncode != 0:
            raise GhError((p.stderr or p.stdout or "gh failed").strip()[:500])
        return json.loads(p.stdout or "null")

    def list_open_prs(self, repo: str) -> list[dict]:
        return self._json([
            "pr", "list", "-R", repo, "--state", "open", "--json",
            "number,title,author,headRefName,headRefOid,baseRefName,labels,isDraft,url",
        ])

    def pr_snapshot(self, repo: str, number: int) -> dict:
        return self._json([
            "pr", "view", str(number), "-R", repo, "--json",
            "number,state,isDraft,headRefName,headRefOid,baseRefName,"
            "mergeStateStatus,mergeable,statusCheckRollup,mergeCommit,url",
        ])

    def update_branch(self, repo: str, number: int) -> bool:
        p = self._run(["pr", "update-branch", str(number), "-R", repo])
        return p.returncode == 0

    def comment(self, repo: str, number: int, body: str) -> bool:
        p = self._run(["pr", "comment", str(number), "-R", repo, "--body-file", "-"], input_text=body)
        return p.returncode == 0

    def merge(self, repo: str, number: int, head_sha: str) -> dict:
        p = self._run([
            "pr", "merge", str(number), "-R", repo, "--squash",
            "--match-head-commit", head_sha,
        ])
        if p.returncode != 0:
            raise GhError((p.stderr or p.stdout or "merge failed").strip()[:500])
        snap = self.pr_snapshot(repo, number)
        return snap

    def failing_check_log_excerpt(self, repo: str, number: int, max_chars: int = 4000) -> str:
        try:
            checks = self._json(["pr", "checks", str(number), "-R", repo, "--json", "name,state,link"])
        except GhError as exc:
            return f"(could not read checks: {exc})"
        failing = [c for c in checks if str(c.get("state", "")).upper() in {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT"}]
        if not failing:
            return "(no failing checks found at read time)"
        lines = [f"- {c.get('name')}: {c.get('state')} {c.get('link', '')}" for c in failing]
        run_match = re.search(r"/actions/runs/(\d+)", (failing[0].get("link") or ""))
        if run_match:
            p = self._run(["run", "view", run_match.group(1), "-R", repo, "--log-failed"])
            if p.returncode == 0 and p.stdout:
                lines.append("")
                lines.append(p.stdout[-max_chars:])
        return "\n".join(lines)[:max_chars]


def check_status(snapshot: dict) -> Optional[bool]:
    """True/False/None (still running) from statusCheckRollup — SKIPPED and
    NEUTRAL are not failures."""
    rollup = snapshot.get("statusCheckRollup") or []
    if not rollup:
        return None
    bad = [c for c in rollup if str(c.get("conclusion") or c.get("state") or "").upper()
           not in {"SUCCESS", "SKIPPED", "NEUTRAL", ""}]
    pending = [c for c in rollup if not (c.get("conclusion") or c.get("state"))]
    if bad:
        return False
    if pending:
        return None
    return True


# ───────────────────────── kill switch & policy ─────────────────────────

def load_policy(path: Path = POLICY_PATH) -> dict:
    if not path.exists():
        return {"enabled": False, "repos": ["jbookout/carr-system"]}
    return json.loads(path.read_text(encoding="utf-8"))


def kill_switch_active(policy: dict, kill_switch_path: Path = KILL_SWITCH_PATH) -> Optional[str]:
    if not policy.get("enabled", False):
        return "policy ops/config/pr-pipeline-policy.json enabled=false"
    if kill_switch_path.exists():
        return f"local override file present: {kill_switch_path}"
    return None


# ───────────────────────── escalation (record layer) ─────────────────────────

def escalate_loop(repo: str, pr_number: int, head_sha: str, reason: str,
                   call_verb: Callable[[str, dict], dict] = verb_io._run_verb) -> Optional[str]:
    """File a CARR loop for a PR this pipeline gave up on. Only escalations
    reach Joe — everything else in this pipeline is fully unattended."""
    try:
        result = call_verb("add-loop", {
            "idempotency_key": str(uuid.uuid5(uuid.NAMESPACE_URL,
                                              f"carr-pr-pipeline-escalation:{repo}:{pr_number}:{head_sha}")),
            "kind": "blocker",
            "title": f"PR pipeline escalation: {repo} #{pr_number}",
            "body": (f"The scripted review-and-merge pipeline gave up on {repo} #{pr_number} "
                     f"at {head_sha} after {MAX_BLOCKED_ROUNDS} rounds ({reason}). "
                     f"Needs a human look."),
        })
        return result.get("id") or result.get("loop_id")
    except Exception:
        return None


# ───────────────────────── the tick ─────────────────────────

@dataclass
class TickResult:
    ran: bool
    skip_reason: Optional[str] = None
    actions: list[dict] = field(default_factory=list)
    merged: Optional[dict] = None


def run_tick(*, repos: list[str], gh: GhClient, state_path: Path = STATE_PATH,
             policy_path: Path = POLICY_PATH, kill_switch_path: Path = KILL_SWITCH_PATH,
             add_room_turn: Callable[..., dict] = verb_io.add_room_turn,
             call_verb: Callable[[str, dict], dict] = verb_io._run_verb,
             merge_events_path: Path = MERGE_EVENTS_PATH,
             actions_log_path: Path = ACTIONS_LOG_PATH,
             now: Optional[str] = None) -> TickResult:
    policy = load_policy(policy_path)
    reason = kill_switch_active(policy, kill_switch_path)
    if reason:
        return TickResult(ran=False, skip_reason=reason)

    state = load_state(state_path)
    actions: list[dict] = []
    merge_candidates: list[tuple[str, str, int, dict]] = []  # (state_key, repo, number, snapshot)

    for repo in repos:
        try:
            prs = gh.list_open_prs(repo)
        except GhError as exc:
            actions.append({"repo": repo, "error": f"list_open_prs failed: {exc}"})
            continue
        for pr in prs:
            if not in_scope(pr):
                continue
            number = pr["number"]
            head_sha = pr["headRefOid"]
            key = f"{repo}#{number}"
            entry = reconcile_sha(state.get(key), head_sha)

            try:
                snapshot = gh.pr_snapshot(repo, number)
            except GhError as exc:
                actions.append({"repo": repo, "pr": number, "error": f"pr_snapshot failed: {exc}"})
                state[key] = entry
                continue

            verdict = None
            if entry["state"] in {"reviewing", "approved", "blocked"}:
                # A verdict, when present, is discovered by a separate room scan
                # (see scan_room_for_verdicts/main()) that has ALREADY bound it
                # to this pipeline's own dispatch (origin_channel=mcp, a room
                # seq strictly after the dispatch, and the dispatch's own key
                # echoed back) before it ever reaches here. run_tick accepts
                # one pre-resolved verdict per PR via entry["_pending_verdict"].
                # Kept out of GhClient because verdicts come from the Model
                # Room, not GitHub.
                verdict = entry.pop("_pending_verdict", None)

            rollup_ok = check_status(snapshot)
            mergeable_clean = snapshot.get("mergeStateStatus") == "CLEAN"
            behind = snapshot.get("mergeStateStatus") == "BEHIND"

            decision = decide(entry, checks_ok=rollup_ok, mergeable_clean=mergeable_clean,
                              behind=behind, verdict=verdict)
            new_entry = decision.entry
            action = decision.action

            if action == "dispatch_review":
                try:
                    files_out = gh._run(["pr", "diff", str(number), "-R", repo, "--name-only"])
                    files = [ln for ln in (files_out.stdout or "").splitlines() if ln.strip()]
                except Exception:
                    files = []
                strength = classify_strength(files)
                target = reviewer_target(strength)
                dispatch_key = _dispatch_key("review", repo, number, head_sha)
                new_entry["dispatch_key"] = dispatch_key
                task = build_review_task(repo, number, head_sha, pr.get("title", ""), target, dispatch_key)
                dispatch_result = dispatch_room_task(target=target, cap="read", key=dispatch_key,
                                                     title=f"Review PR #{number}", task_text=task,
                                                     add_room_turn=add_room_turn)
                # The room seq THIS dispatch landed at. Only a verdict turn
                # strictly after it can ever be accepted for this review round
                # (see decide()'s latest-verdict-wins gate) — a turn with a
                # lower or equal seq predates the question being asked.
                new_entry["dispatch_seq"] = dispatch_result.get("seq")
                actions.append({"repo": repo, "pr": number, "action": "dispatch_review",
                                "target": target, "strength": strength, "key": dispatch_key})

            elif action == "dispatch_fix":
                findings = entry.get("last_findings", "")
                dispatch_key = _dispatch_key("fix", repo, number, head_sha, new_entry["blocked_rounds"])
                new_entry["dispatch_key"] = dispatch_key
                task = build_fix_task(repo, number, pr["headRefName"], head_sha, findings)
                dispatch_room_task(target="claude", cap="repo-write", key=dispatch_key,
                                   title=f"Fix PR #{number}: reviewer findings", task_text=task,
                                   add_room_turn=add_room_turn)
                actions.append({"repo": repo, "pr": number, "action": "dispatch_fix", "key": dispatch_key})

            elif action == "dispatch_ci_diagnosis":
                log_excerpt = gh.failing_check_log_excerpt(repo, number)
                dispatch_key = _dispatch_key("ci", repo, number, head_sha, new_entry["blocked_rounds"])
                new_entry["dispatch_key"] = dispatch_key
                task = build_ci_diagnosis_task(repo, number, pr["headRefName"], head_sha, log_excerpt)
                dispatch_room_task(target="claude", cap="repo-write", key=dispatch_key,
                                   title=f"CI failing on PR #{number}", task_text=task,
                                   add_room_turn=add_room_turn)
                actions.append({"repo": repo, "pr": number, "action": "dispatch_ci_diagnosis", "key": dispatch_key})

            elif action == "request_update_branch":
                ok = gh.update_branch(repo, number)
                actions.append({"repo": repo, "pr": number, "action": "request_update_branch", "ok": ok})

            elif action == "escalate":
                loop_id = escalate_loop(repo, number, head_sha, new_entry.get("fix_reason") or "review", call_verb)
                new_entry["escalated_loop_id"] = loop_id
                actions.append({"repo": repo, "pr": number, "action": "escalate", "loop_id": loop_id})

            elif action == "attempt_merge":
                merge_candidates.append((key, repo, number, snapshot))

            if verdict is not None:
                # Reviewed-SHA and Verdict are the two lines the release
                # pipeline's PR-comment reader keys on (it accepts only
                # OWNER/MEMBER/COLLABORATOR comments newer than the head
                # commit, and only the LATEST such comment's Verdict wins);
                # this pipeline posts them itself, from its own gh login, so
                # the reviewer session never needs GitHub write access at all.
                comment_body = (
                    f"Reviewed-SHA: {new_entry['head_sha']}\n"
                    f"Verdict: {verdict['verdict']}\n\n"
                    f"Reviewer: {verdict['reviewer']}\n\n"
                    f"{entry.get('_verdict_findings', '')}"
                ).rstrip()
                gh.comment(repo, number, comment_body)
                actions.append({"repo": repo, "pr": number, "action": "posted_verdict_comment",
                                "verdict": verdict["verdict"]})

            state[key] = new_entry

    merged = None
    if merge_candidates:
        # Serialize: exactly one merge per tick, oldest tracked entry first.
        merge_candidates.sort(key=lambda item: state.get(item[0], {}).get("updated_at", ""))
        key, repo, number, snapshot = merge_candidates[0]
        head_sha = snapshot["headRefOid"]
        try:
            result = gh.merge(repo, number, head_sha)
            merge_commit_sha = result.get("mergeCommit", {}).get("oid") or head_sha
            entry = state[key]
            entry["state"] = "merged"
            entry["merge"] = {"merge_commit_sha": merge_commit_sha, "merged_at": _now_iso()}
            entry["updated_at"] = _now_iso()
            state[key] = entry
            event = {
                "schema_version": 1, "event": "pr_merged", "repo": repo, "pr_number": number,
                "head_sha": head_sha, "merge_commit_sha": merge_commit_sha,
                "base_branch": snapshot.get("baseRefName"), "merged_at": entry["merge"]["merged_at"],
                "reviewer": entry.get("reviewer"), "review_sha": entry.get("head_sha"),
            }
            merge_events_path.parent.mkdir(parents=True, exist_ok=True)
            with merge_events_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, sort_keys=True) + "\n")
            merged = event
            actions.append({"repo": repo, "pr": number, "action": "merged", "merge_commit_sha": merge_commit_sha})
        except GhError as exc:
            actions.append({"repo": repo, "pr": number, "action": "merge_failed", "error": str(exc)})

    save_state(state, state_path)
    actions_log_path.parent.mkdir(parents=True, exist_ok=True)
    with actions_log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": _now_iso(), "actions": actions}, sort_keys=True) + "\n")
    return TickResult(ran=True, actions=actions, merged=merged)


def scan_room_for_verdicts(state: dict, *, room: str = DEFAULT_ROOM,
                            read_room: Callable[..., dict] = verb_io.read_room,
                            cursor_path: Path = REPO / "out" / "pr-pipeline-room-cursor.json") -> dict:
    """Reads new room turns since the last cursor and attaches, to each
    relevant state entry, the newest verdict this pipeline can PROVE answers
    its own dispatch — never a verdict merely inferred from a matching pr/sha
    in the text of an arbitrary turn. A turn qualifies only if ALL of:

      1. origin_channel == "mcp" — server-derived provenance (the same gate
         queue_grammar.py's own `_origin` enforces); a turn the Worker did not
         stamp as MCP-sourced cannot carry a verdict at all. This is what
         makes a forged or unbound room turn inert even if its body is a
         byte-perfect CARR-PR-VERDICT line.
      2. seq > entry["dispatch_seq"] — strictly later than the room seq this
         pipeline's own review dispatch landed at. A turn is not "later than
         the dispatch" merely because it was read after it; it must carry a
         genuinely greater sequence number.
      3. parse_verdict(..., expected_key=entry["dispatch_key"]) succeeds —
         the reply must echo the exact key this pipeline minted for THIS
         review round, which also enforces the stale-SHA rule (parse_verdict
         refuses unless the sha names entry's current head_sha).

    States considered: reviewing, approved, and blocked — a PR that already
    has a verdict can still receive a newer, overriding one (latest wins) as
    long as it has not yet merged or moved to a fresh SHA. Among several
    qualifying turns for the same PR in one scan, the one with the highest
    seq is kept, so decide() only ever sees the single newest one.
    """
    cursor = 0
    if cursor_path.exists():
        try:
            cursor = json.loads(cursor_path.read_text(encoding="utf-8")).get("after_seq", 0)
        except (json.JSONDecodeError, OSError):
            cursor = 0
    result = read_room(cursor, room=room, limit=200)
    turns = result.get("turns", []) if isinstance(result, dict) else []
    live = {key: entry for key, entry in state.items()
            if entry.get("state") in {"reviewing", "approved", "blocked"}}
    by_pr_sha = {}
    for key, entry in live.items():
        repo, _, num_s = key.rpartition("#")
        by_pr_sha[(int(num_s), entry["head_sha"])] = key

    best: dict[str, dict] = {}  # key -> {"verdict": ..., "findings": ..., "seq": ...}
    last_seq = cursor
    for turn in turns:
        seq = int(turn.get("seq", turn.get("id", 0)) or 0)
        last_seq = max(last_seq, seq)
        if turn.get("origin_channel") != "mcp":
            continue  # not server-derived MCP provenance: never a candidate
        body = turn.get("body") or ""
        for (pr_number, sha), key in by_pr_sha.items():
            entry = state[key]
            dispatch_seq = entry.get("dispatch_seq")
            if dispatch_seq is None or seq <= dispatch_seq:
                continue  # not strictly later than this pipeline's own dispatch
            verdict = parse_verdict(body, pr_number, sha, expected_key=entry.get("dispatch_key"))
            if verdict is None:
                continue
            verdict = dict(verdict, seq=seq)
            current = best.get(key)
            if current is None or seq > current["verdict"]["seq"]:
                best[key] = {"verdict": verdict, "findings": extract_findings(body)}

    for key, found in best.items():
        target = state[key]
        target["_pending_verdict"] = found["verdict"]
        target["_verdict_findings"] = found["findings"]

    cursor_path.parent.mkdir(parents=True, exist_ok=True)
    cursor_path.write_text(json.dumps({"after_seq": last_seq}), encoding="utf-8")
    return state


# ───────────────────────── CLI ─────────────────────────

def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["tick"])
    ap.add_argument("--repo", action="append", dest="repos", default=None,
                    help="owner/name; may be repeated. Default: policy file's repos list.")
    ap.add_argument("--state-file", default=str(STATE_PATH))
    ap.add_argument("--policy-file", default=str(POLICY_PATH))
    ap.add_argument("--kill-switch-file", default=str(KILL_SWITCH_PATH))
    ap.add_argument("--gh-bin", default="gh")
    args = ap.parse_args(argv)

    policy_path = Path(args.policy_file)
    policy = load_policy(policy_path)
    repos = args.repos or policy.get("repos", ["jbookout/carr-system"])

    gh = GhClient(gh_bin=args.gh_bin)
    state_path = Path(args.state_file)

    reason = kill_switch_active(policy, Path(args.kill_switch_file))
    if reason:
        print(f"pr-pipeline: kill switch active, skipping tick: {reason}")
        return 0

    state = load_state(state_path)
    state = scan_room_for_verdicts(state)
    save_state(state, state_path)

    result = run_tick(repos=repos, gh=gh, state_path=state_path, policy_path=policy_path,
                      kill_switch_path=Path(args.kill_switch_file))
    for action in result.actions:
        print(f"pr-pipeline: {json.dumps(action, sort_keys=True)}")
    if result.merged:
        print(f"pr-pipeline: merged {result.merged['repo']}#{result.merged['pr_number']} "
              f"as {result.merged['merge_commit_sha']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
