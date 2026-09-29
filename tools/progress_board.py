#!/usr/bin/env python3
"""CARR's progress board: task, question and answer commands plus the JSON
data contract that the one interactive board renders.

There is exactly one board UI: app.doctorcre.com/progress-board (Joe's
ruling 2026-09-29). This tool never renders a page. It keeps the local JSON
state, derives PR, release and health facts from GitHub and the release
pipeline, and publishes the snapshot the app renders. The system-wide
all-repos board is built here from gh data on every publish.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


STATUSES = ("queued", "running", "review", "blocked", "done", "failed")
PIPELINE_STAGES = ("queued", "build", "review", "ci", "merged", "live")
PR_STAGES = PIPELINE_STAGES[1:] + ("measured",)
STAGE_LABELS = {
    "queued": "Queued",
    "build": "Building",
    "review": "Review",
    "ci": "CI",
    "merged": "Merged",
    "live": "Live",
}
STATUS_TO_STAGE = {
    "queued": "queued",
    "running": "build",
    "review": "review",
    "blocked": "review",
    "failed": "ci",
    "done": "build",
}
# In flight: a card that should keep moving. Stale applies only to these.
IN_FLIGHT = frozenset({"running", "review", "blocked"})
STUCK_AFTER = timedelta(hours=2)
STALE_AFTER = timedelta(hours=6)
LAUNCHD_BOARD = "carr-v5"
DEFAULT_PR_REPO = "jbookout/carr-system"
SNAPSHOT_SCHEMA = "carr-progress-board.v2"
# The server refuses snapshots over 262144 bytes; tasks get most of it.
SNAPSHOT_BUDGET = 200_000

ALL_REPOS_BOARD = "all-repos"
GITHUB_OWNER = "jbookout"
CORE_REPOS = ("jbookout/carr-system", "jbookout/doctorcre-app", "jbookout/software-factory")
RECENT_MERGED = timedelta(days=7)
OPEN_PR_FIELDS = ("number,title,body,author,headRefName,headRefOid,isDraft,createdAt,updatedAt,"
                  "statusCheckRollup,mergeable,reviewDecision,url")
MERGED_PR_FIELDS = "number,title,body,author,headRefName,headRefOid,createdAt,updatedAt,mergedAt,mergeCommit,url"

REPO_ROOT = Path(__file__).resolve().parents[1]
RELEASE_CONFIG = REPO_ROOT / "ops" / "config" / "release-pipeline.v1.json"
SHA_RE = re.compile(r"[0-9a-f]{40}")

# Executor pools in ledger order: key, label, glyph. The app legend carries
# the same letters; its test pins them.
POOLS = (
    ("codex", "Codex", "C"),
    ("grok", "Grok", "G"),
    ("flash-next", "Flash Next", "F"),
    ("claude-cloud", "Claude cloud credits", "✦"),
    ("orchestrator", "Orchestrator seat", "O"),
)

# A blocked PR phase names its own reason and the next action.
PHASE_BLOCKS = {
    "Checks failing": ("CI checks are failing on the PR head", "Read the failing check log, fix, and push"),
    "Review blocked": ("An independent reviewer posted BLOCK", "Address the review findings and push a new head"),
    "Merge conflict": ("Merge conflict with the base branch", "Merge the base branch and resolve the conflict"),
    "Changes requested": ("A reviewer requested changes", "Address the requested changes and re-request review"),
    "Closed unmerged": ("PR closed without merging", "Decide: reopen, replace, or retire the task"),
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def stamp() -> str:
    return now_utc().isoformat(timespec="seconds")


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def age_text(timestamp: Any, at: datetime | None = None) -> str:
    """Compact age: 35m, 2h 14m, 1d 2h."""
    then = parse_time(timestamp)
    if then is None:
        return "unknown"
    minutes = max(0, int(((at or now_utc()) - then).total_seconds() // 60))
    if minutes < 60:
        return f"{minutes}m"
    if minutes < 24 * 60:
        return f"{minutes // 60}h {minutes % 60}m"
    return f"{minutes // 1440}d {(minutes % 1440) // 60}h"


def board_dir() -> Path:
    configured = os.environ.get("PROGRESS_BOARD_ROOT")
    if configured:
        return Path(configured).expanduser().resolve() / "boards"
    return Path.cwd() / "out" / "boards"


def safe_project(project: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", project):
        raise SystemExit("project must contain only letters, numbers, dot, underscore, or hyphen")
    return project


def safe_asker_ref(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}", value):
        raise SystemExit("asker_ref must be a short named session or orchestrator reference")
    return value


def safe_repo(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*", value):
        raise SystemExit("repo must be a GitHub owner/repository name")
    return value


def task_repo(task: dict[str, Any]) -> str:
    return safe_repo(task.get("repo") or DEFAULT_PR_REPO)


def pr_key(task: dict[str, Any]) -> tuple[str, int]:
    return task_repo(task), int(task["pr"])


def card_key(repo: str, number: int) -> str:
    """PR numbers are per repository, so a card key always carries the repo."""
    return f"{safe_repo(repo).split('/', 1)[1]}-{int(number)}"


def state_path(project: str) -> Path:
    return board_dir() / f"{safe_project(project)}.json"


def read_state(project: str) -> dict[str, Any]:
    path = state_path(project)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"board does not exist: {project}; run init first")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"invalid board JSON {path}: {exc}")


def write_json(state: dict[str, Any]) -> None:
    board_dir().mkdir(parents=True, exist_ok=True)
    path = state_path(state["project"])
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def is_stuck(task: dict[str, Any], at: datetime | None = None) -> bool:
    if task.get("status") == "blocked":
        return True
    if task.get("status") != "running":
        return False
    updated = parse_time(task.get("updated_at"))
    if updated is None:
        return True
    return (at or now_utc()) - updated > STUCK_AFTER


def is_stale(task: dict[str, Any], at: datetime | None = None) -> bool:
    """An in-flight card with no update for STALE_AFTER or longer."""
    if task.get("status") not in IN_FLIGHT or task_stage(task) == "live":
        return False
    updated = parse_time(task.get("updated_at"))
    return updated is not None and (at or now_utc()) - updated >= STALE_AFTER


def violation(executor: str) -> bool:
    value = executor.lower()
    if "claude-plan" in value:
        return True
    if "orchestrator" in value and "claude" not in value:
        return False
    return bool(re.search(r"\b(?:opus|sonnet)\s+subagent\b", value)) or (
        "claude" in value and "subagent" in value
    )


def executor_pool(executor: str) -> str:
    value = executor.lower().strip()
    if "orchestrator" in value:
        return "orchestrator"
    if "claude" in value or "opus" in value or "sonnet" in value:
        return "claude-cloud"
    if "flash next" in value or "flash-next" in value:
        return "flash-next"
    if "grok" in value:
        return "grok"
    if "codex" in value or "gpt-" in value:
        return "codex"
    return "unassigned"


def executor_glyph(executor: str) -> str:
    return {key: glyph for key, _, glyph in POOLS}.get(executor_pool(executor), "?")


def executor_metadata(executor: str | None) -> tuple[str, str, str]:
    """Recover provider, model and effort from legacy executor labels."""
    value = (executor or "").strip()
    lower = value.lower()
    effort_match = re.search(r"\b(low|medium|high|xhigh|max|ultra)\b", lower)
    effort = effort_match.group(1) if effort_match else "unknown"
    if "orchestrator" in lower:
        return "Anthropic", "Claude Opus 5.5", effort
    gpt = re.search(r"\bgpt-[\w.-]+", value, re.IGNORECASE)
    if gpt:
        return "Codex", gpt.group(0).lower(), effort
    claude = re.search(r"\bclaude\s+(?:opus|sonnet|haiku)\s+[\d.]+", value, re.IGNORECASE)
    if claude:
        return "Anthropic", " ".join(part.capitalize() if not part[0].isdigit() else part
                                     for part in claude.group(0).split()), effort
    provider = {"codex": "Codex", "claude-cloud": "Anthropic", "grok": "xAI",
                "flash-next": "Google"}.get(executor_pool(value), "Unknown")
    return provider, value or "unknown", effort


def task_identity(task: dict[str, Any]) -> tuple[str, str, str]:
    derived = executor_metadata(task.get("executor"))
    return (str(task.get("provider") or derived[0]),
            str(task.get("model") or derived[1]),
            str(task.get("effort") or derived[2]))


def executor_ledger(tasks: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-pool counts with each provider/model/effort seen, and policy flags."""
    rows = []
    pools = [*POOLS, ("unassigned", "Unassigned", "?")]
    for key, label, glyph in pools:
        members = [t for t in tasks.values() if executor_pool(str(t.get("executor") or "")) == key]
        if key == "unassigned" and not members:
            continue
        models: dict[tuple[str, str, str], int] = {}
        for task in members:
            identity = task_identity(task)
            models[identity] = models.get(identity, 0) + 1
        rows.append({
            "pool": key, "label": label, "glyph": glyph, "count": len(members),
            "violation": any(violation(str(t.get("executor") or "")) for t in members),
            "models": [{"provider": p, "model": m, "effort": e, "count": n}
                       for (p, m, e), n in sorted(models.items(), key=lambda item: (-item[1], item[0]))],
        })
    return rows


def task_stage(task: dict[str, Any]) -> str:
    requested = task.get("stage")
    if requested == "measured":
        requested = "live"
    evidence = task.get("evidence")
    if requested == "live" and not (isinstance(evidence, str) and evidence.strip()):
        requested = None
    if requested in PIPELINE_STAGES:
        return requested
    if task.get("status") == "done":
        return "merged" if task.get("pr") is not None and task.get("pr_phase") == "Merged" else "build"
    if task.get("status") == "measured":
        return "live" if isinstance(evidence, str) and evidence.strip() else "build"
    return STATUS_TO_STAGE.get(task.get("status", "queued"), "queued")


def completed_at(task: dict[str, Any]) -> datetime | None:
    if task_stage(task) != "live":
        return None
    return parse_time(task.get("completed_at") or task.get("updated_at"))


def task_health(task: dict[str, Any], at: datetime | None = None) -> str:
    # A finished card cannot be blocked: a leftover flag from an earlier
    # review round is ignored here and dropped from state by normalize_task.
    if task.get("status") == "done" or task_stage(task) == "live":
        return "healthy"
    if task.get("status") in {"blocked", "failed"} or task.get("health") == "blocked" or is_stuck(task, at):
        return "blocked"
    if task.get("status") == "review" or task.get("health") == "question" or task.get("question"):
        return "question"
    return "healthy"


def pulse_state(task: dict[str, Any], at: datetime | None = None) -> str:
    health = task_health(task, at)
    if health == "blocked":
        return "critical"
    if health == "question":
        return "attention"
    if task.get("status") in {"done", "queued"}:
        return "still"
    return "healthy"


def blocked_detail(task: dict[str, Any], at: datetime | None = None) -> tuple[str, str] | None:
    """Every blocked card names why and what happens next."""
    if task_health(task, at) != "blocked":
        return None
    phase = PHASE_BLOCKS.get(str(task.get("pr_phase") or ""))
    reason = str(task.get("blocked_reason") or "").strip()
    action = str(task.get("next_action") or "").strip()
    if reason:
        return reason, action or (phase[1] if phase else "Orchestrator: record the next action")
    if phase:
        return phase
    if task.get("status") == "failed":
        return "Task failed", "Decide: retry, replace, or retire the task"
    if task.get("status") == "running" and is_stuck(task, at):
        age = age_text(task.get("updated_at"), at)
        return (f"No update for {age}" if age != "unknown" else "No update recorded",
                "Check the executor session; post an update or re-dispatch")
    return "Marked blocked without a recorded reason", "Orchestrator: record the reason and next action"


def stage_entered_at(task: dict[str, Any]) -> str | None:
    history = task.get("stage_history") or []
    last = history[-1] if history and isinstance(history[-1], dict) else {}
    value = task.get("stage_entered_at") or last.get("entered_at") or task.get("updated_at") or task.get("created_at")
    return value if isinstance(value, str) else None


def stage_timer(task: dict[str, Any], at: datetime | None = None) -> str:
    """'build 2h 14m': the stage and how long the card has been in it."""
    return f"{task_stage(task)} {age_text(stage_entered_at(task), at)}"


def stage_durations(task: dict[str, Any], at: datetime | None = None) -> list[tuple[str, str, str]]:
    history = [h for h in task.get("stage_history") or [] if isinstance(h, dict) and h.get("entered_at")]
    rows = []
    for index, entry in enumerate(history):
        end = parse_time(history[index + 1]["entered_at"]) if index + 1 < len(history) else (at or now_utc())
        rows.append((str(entry.get("stage")), str(entry["entered_at"]), age_text(entry["entered_at"], end)))
    return rows


def record_stage(task: dict[str, Any], entered_at: str, prior_stage: str | None) -> None:
    stage = task_stage(task)
    history = list(task.get("stage_history") or [])
    last = history[-1] if history and isinstance(history[-1], dict) else {}
    if (prior_stage == stage and history) or last.get("stage") == stage:
        return
    task["stage_history"] = [*history, {"stage": stage, "entered_at": entered_at}]
    task["stage_entered_at"] = entered_at


def normalize_task(task: dict[str, Any]) -> bool:
    """Idempotent repair of stored state; never moves updated_at."""
    before = json.dumps(task, sort_keys=True)
    if task.get("status") == "done" or task_stage(task) == "live":
        for field in ("health", "blocked_reason", "next_action"):
            task.pop(field, None)
    if task_stage(task) == "live":
        task.pop("release_wait", None)
    history = []
    for entry in task.get("stage_history") or []:
        if not isinstance(entry, dict):
            continue
        entered = entry.get("entered_at") or entry.get("at")
        if isinstance(entered, str) and entry.get("stage"):
            history.append({"stage": entry["stage"], "entered_at": entered})
    fallback = task.get("updated_at") or task.get("created_at")
    stage = task_stage(task)
    if isinstance(fallback, str) and (not history or history[-1]["stage"] != stage):
        history.append({"stage": stage, "entered_at": fallback})
    if history:
        task["stage_history"] = history
        if task.get("stage_entered_at") != history[-1]["entered_at"]:
            task["stage_entered_at"] = history[-1]["entered_at"]
    return json.dumps(task, sort_keys=True) != before


def task_summary(task: dict[str, Any]) -> str:
    summary = str(task.get("summary") or "").strip()
    if summary:
        return summary
    return str(task.get("title") or "This task").strip().rstrip(".") + "."


def pr_summary(body: str | None, limit: int = 160) -> str:
    """First plain line of a PR body: no headings, tables, code or markup."""
    fenced = False
    for raw in (body or "").splitlines():
        line = raw.strip()
        if line.startswith("```"):
            fenced = not fenced
            continue
        if fenced or not line or line.startswith(("#", "|", "<!--", ">", "---")):
            continue
        line = re.sub(r"^(?:[-*+]\s+(?:\[[ xX]\]\s+)?|\d+[.)]\s+)", "", line)
        line = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", line)
        line = re.sub(r"(\*\*|__|`|\*)", "", line)
        line = re.sub(r"<[^>]+>", "", line)
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"
    return ""


def check_counts(rollup: list[dict[str, Any]]) -> tuple[int, int, int]:
    passed = pending = failed = 0
    for check in rollup:
        conclusion = str(check.get("conclusion") or check.get("state") or "").upper()
        status = str(check.get("status") or "").upper()
        if conclusion in {"SUCCESS", "SKIPPED", "NEUTRAL"}:
            passed += 1
        elif conclusion in {"FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE", "ERROR"}:
            failed += 1
        elif status or conclusion:
            pending += 1
    return passed, pending, failed


def checks_summary(payload: dict[str, Any]) -> str:
    rollup = payload.get("statusCheckRollup") or []
    if not isinstance(rollup, list) or any(not isinstance(check, dict) for check in rollup):
        return "checks unavailable"
    passed, pending, failed = check_counts(rollup)
    if passed + pending + failed == 0:
        return "no checks"
    return f"{passed} pass · {pending} pending · {failed} fail"


def gh_available() -> bool:
    return not os.environ.get("PROGRESS_BOARD_SKIP_GH") and shutil.which("gh") is not None


def gh_text(args: list[str], timeout: int = 30) -> str:
    if not gh_available():
        raise RuntimeError("gh CLI unavailable")
    try:
        result = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"gh {' '.join(args[:2])} failed: {exc}") from exc
    if result.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:2])} failed: {(result.stderr or result.stdout).strip()[:200]}")
    return result.stdout


def gh_json(args: list[str], timeout: int = 30) -> Any:
    try:
        return json.loads(gh_text(args, timeout))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"gh {' '.join(args[:2])} returned invalid JSON") from exc


def pr_info(number: int, repo: str) -> dict[str, Any] | None:
    if not gh_available():
        return None
    try:
        payload = gh_json(["pr", "view", str(number), "--repo", repo, "--json",
                           "state,isDraft,headRefOid,statusCheckRollup,comments,author,mergeCommit"], timeout=5)
    except RuntimeError:
        return None
    if not isinstance(payload, dict):
        return None
    state = payload.get("state")
    if not isinstance(state, str) or state not in {"OPEN", "CLOSED", "MERGED"}:
        return None
    if not isinstance(payload.get("isDraft"), bool) or not isinstance(payload.get("headRefOid"), str):
        return None
    author = payload.get("author")
    if not isinstance(author, dict) or not isinstance(author.get("login"), str):
        return None
    checks = payload.get("statusCheckRollup")
    if not isinstance(checks, list):
        return None
    for check in checks:
        if (not isinstance(check, dict) or "conclusion" not in check
                or not (check["conclusion"] is None or isinstance(check["conclusion"], str))
                or not isinstance(check.get("status"), str)):
            return None
    comments = payload.get("comments")
    if not isinstance(comments, list):
        return None
    for comment in comments:
        if not isinstance(comment, dict):
            return None
        commenter = comment.get("author")
        if (not isinstance(commenter, dict) or not isinstance(commenter.get("login"), str)
                or not all(isinstance(comment.get(field), str)
                           for field in ("authorAssociation", "body", "createdAt"))):
            return None
    merge = payload.get("mergeCommit")
    if merge is not None and not (isinstance(merge, dict) and isinstance(merge.get("oid"), str)):
        return None
    return payload


def merge_sha(payload: dict[str, Any]) -> str | None:
    merge = payload.get("mergeCommit")
    oid = str(merge.get("oid") or "").lower() if isinstance(merge, dict) else ""
    return oid if SHA_RE.fullmatch(oid) else None


def latest_verdict(payload: dict[str, Any]) -> list[str] | None:
    """Lines of the latest APPROVE/BLOCK comment from a trusted non-author."""
    maker = str((payload.get("author") or {}).get("login") or "").lower()
    if not maker:
        return None
    verdicts = []
    for index, comment in enumerate(payload.get("comments") or []):
        commenter = comment.get("author") or {}
        login = str(commenter.get("login") or "").lower() if isinstance(commenter, dict) else ""
        association = str(comment.get("authorAssociation") or "").upper()
        lines = str(comment.get("body") or "").splitlines()
        if (login and login != maker and association in {"OWNER", "MEMBER", "COLLABORATOR"}
                and lines and lines[0] in {"APPROVE", "BLOCK"}):
            verdicts.append((str(comment.get("createdAt") or ""), index, lines))
    return max(verdicts)[2] if verdicts else None


def approves_head(lines: list[str], head: str) -> bool:
    return (re.fullmatch(r"[0-9a-f]{40}", head) is not None and len(lines) >= 2
            and lines[1] == f"Reviewed-SHA: {head}"
            and not any("reviewed-sha:" in line.lower() for line in lines[2:]))


def review_verdict(payload: dict[str, Any]) -> str:
    lines = latest_verdict(payload)
    if not lines:
        return "Not recorded"
    if lines[0] == "BLOCK":
        return "BLOCK"
    return "APPROVE" if approves_head(lines, str(payload.get("headRefOid") or "").lower()) else "Not recorded"


def derived_pr_state(payload: dict[str, Any]) -> tuple[str, str, str]:
    state = str(payload.get("state") or "").upper()
    if state == "MERGED":
        return "done", "merged", "Merged"
    if state == "CLOSED":
        return "failed", "ci", "Closed unmerged"
    if payload.get("isDraft"):
        return "running", "build", "Draft"
    checks = payload.get("statusCheckRollup") or []
    failing = {"FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"}
    passing = {"SUCCESS", "SKIPPED", "NEUTRAL"}
    if any(str(check.get("conclusion") or "").upper() in failing for check in checks):
        return "blocked", "ci", "Checks failing"
    if not checks or any(str(check.get("conclusion") or "").upper() not in passing for check in checks):
        return "running", "ci", "CI"
    lines = latest_verdict(payload)
    if not lines:
        return "review", "review", "Awaiting review"
    if lines[0] == "BLOCK":
        return "blocked", "review", "Review blocked"
    approved = approves_head(lines, str(payload.get("headRefOid") or "").lower())
    return "review", "review", "Ready to merge" if approved else "Awaiting review"


# ── verified releases ────────────────────────────────────────────────────────
# A merged card is Live only when its merge commit is an ancestor of the latest
# verified release for its repository: the newest `shipped` row the release
# pipeline wrote for that repo's lane, or, when the pipeline has none yet, the
# SHA the live release endpoint serves.

RELEASE_CACHE: dict[str, Any] = {}


def release_lanes() -> dict[str, dict[str, str]]:
    try:
        config = json.loads(RELEASE_CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    lanes = {}
    for lane in ("worker", "app"):
        entry = config.get(lane)
        if isinstance(entry, dict) and isinstance(entry.get("github_repo"), str):
            lanes[lane] = {"repo": entry["github_repo"], "url": str(entry.get("live_release_url") or "")}
    return lanes


def releases_path() -> Path:
    configured = os.environ.get("PROGRESS_BOARD_RELEASES")
    return Path(configured) if configured else REPO_ROOT / "out" / "release-pipeline" / "releases.jsonl"


def release_rows(repo: str) -> list[dict[str, Any]]:
    """The pipeline's rows for this repo's lanes, oldest first."""
    lanes = {lane for lane, entry in release_lanes().items() if entry["repo"] == repo}
    try:
        lines = releases_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("lane") in lanes:
            rows.append(row)
    return sorted(rows, key=lambda row: str(row.get("ts") or ""))


def probe_json(url: str) -> dict[str, Any] | None:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "carr-progress-board"})
        with urllib.request.urlopen(request, timeout=5) as response:  # noqa: S310 — fixed https config URL
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def latest_release(repo: str) -> dict[str, Any] | None:
    if repo in RELEASE_CACHE:
        return RELEASE_CACHE[repo]
    release = None
    shipped = [row for row in release_rows(repo)
               if row.get("status") == "shipped" and SHA_RE.fullmatch(str(row.get("sha") or "").lower())]
    if shipped:
        row = shipped[-1]
        release = {"sha": str(row["sha"]).lower(), "lane": row.get("lane"), "ts": row.get("ts"),
                   "source": "releases.jsonl"}
    elif not os.environ.get("PROGRESS_BOARD_SKIP_PROBE"):
        for lane, entry in release_lanes().items():
            if entry["repo"] != repo or not entry["url"].startswith("https://"):
                continue
            payload = probe_json(entry["url"]) or {}
            if lane == "worker":
                sha = str((payload.get("git_sha") or {}).get("value") or "").lower()
            else:
                sha = str(payload.get("source_commit") or "").lower() if payload.get("environment") == "production" else ""
            if SHA_RE.fullmatch(sha):
                release = {"sha": sha, "lane": lane, "ts": None, "source": "live probe"}
                break
    RELEASE_CACHE[repo] = release
    return release


def release_wait_reason(repo: str) -> str:
    """Why a merged commit is not live yet, in the pipeline's own words."""
    rows = release_rows(repo)
    shipped_ts = max((str(r.get("ts") or "") for r in rows if r.get("status") == "shipped"), default="")
    later = [r for r in rows if str(r.get("ts") or "") > shipped_ts and r.get("status") != "shipped"]
    if later:
        row = later[-1]
        status = str(row.get("status") or "unknown")
        detail = str(row.get("detail") or "").strip()
        tail = f": {detail[:200]}" if detail else ""
        if status == "blocked":
            return f"release pipeline blocked ({row.get('reason') or 'no reason'}){tail}"
        if status == "failed":
            return f"release pipeline failed at {row.get('step') or row.get('failed_step') or 'unknown step'}{tail}"
        if status == "no_release_needed":
            return "latest batch was doc/test-only; waiting for the next release"
        return f"release pipeline {status}{tail}"
    release = latest_release(repo)
    if release:
        return f"waiting for the next release after {release['sha'][:12]}"
    return "no verified release recorded yet"


def ancestry_cache_path() -> Path:
    return board_dir() / "release-ancestry.json"


def compare_status(repo: str, base: str, head: str) -> str | None:
    """GitHub's compare status of head against base; `behind` or `identical`
    means head is an ancestor of base. Commit ancestry never changes, so every
    answer is cached."""
    key = f"{repo}:{base}...{head}"
    path = ancestry_cache_path()
    try:
        cache = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    if isinstance(cache, dict) and isinstance(cache.get(key), str):
        return cache[key]
    try:
        status = gh_text(["api", f"repos/{repo}/compare/{base}...{head}", "--jq", ".status"], timeout=15).strip()
    except RuntimeError:
        return None
    if status not in {"ahead", "behind", "identical", "diverged"}:
        return None
    cache = cache if isinstance(cache, dict) else {}
    cache[key] = status
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return status


def auto_live(task: dict[str, Any], repo: str, at: str) -> bool:
    """Move a merged card to Live once its merge commit is released."""
    merge = str(task.get("merge_sha") or "")
    if task_stage(task) != "merged" or not SHA_RE.fullmatch(merge):
        return False
    release = latest_release(repo)
    if release and (release["sha"] == merge or compare_status(repo, release["sha"], merge) in {"behind", "identical"}):
        prior = task_stage(task)
        where = (f"the verified {release['lane']} release {release['sha'][:12]}"
                 + (f" shipped {release['ts']}" if release.get("ts") else f" ({release['source']})"))
        task.update({"status": "done", "stage": "live", "completed_at": at, "updated_at": at,
                     "evidence": f"Merged commit {merge[:12]} is in {where}"})
        task.pop("release_wait", None)
        record_stage(task, at, prior)
        normalize_task(task)
        return True
    wait = release_wait_reason(repo)
    if task.get("release_wait") != wait:
        task["release_wait"] = wait
        return True
    return False


# ── the system-wide board ────────────────────────────────────────────────────

def branch_executor(branch: str, author: str) -> str:
    value = branch.lower()
    if value.startswith("claude/"):
        return "Claude cloud"
    if "codex" in value:
        return "Codex"
    if "grok" in value:
        return "Grok"
    if "flash" in value:
        return "Flash Next"
    return author or "unassigned"


def open_pr_state(pr: dict[str, Any]) -> tuple[str, str, str]:
    if pr.get("isDraft"):
        return "running", "build", "Draft"
    raw = pr.get("statusCheckRollup")
    rollup = [check for check in raw if isinstance(check, dict)] if isinstance(raw, list) else []
    _, pending, failed = check_counts(rollup)
    if failed:
        return "blocked", "ci", "Checks failing"
    if str(pr.get("mergeable") or "").upper() == "CONFLICTING":
        return "blocked", "review", "Merge conflict"
    if str(pr.get("reviewDecision") or "").upper() == "CHANGES_REQUESTED":
        return "blocked", "review", "Changes requested"
    if pending:
        return "running", "ci", "CI"
    if str(pr.get("reviewDecision") or "").upper() == "APPROVED":
        return "review", "review", "Approved"
    return "review", "review", "Awaiting review"


def pr_card(repo: str, pr: dict[str, Any], merged: bool) -> dict[str, Any]:
    author = str((pr.get("author") or {}).get("login") or "") if isinstance(pr.get("author"), dict) else ""
    branch = str(pr.get("headRefName") or "")
    if merged:
        status, stage, phase = "done", "merged", "Merged"
    else:
        status, stage, phase = open_pr_state(pr)
    card = {
        "title": str(pr.get("title") or f"PR {pr.get('number')}")[:200],
        "summary": pr_summary(pr.get("body")),
        "repo": repo, "pr": int(pr["number"]),
        "url": str(pr.get("url") or f"https://github.com/{repo}/pull/{int(pr['number'])}"),
        "author": author, "executor": branch_executor(branch, author), "branch": branch,
        "pr_head": str(pr.get("headRefOid") or ""),
        "created_at": pr.get("createdAt"), "updated_at": pr.get("updatedAt") or pr.get("createdAt"),
        "status": status, "stage": stage, "pr_phase": phase,
    }
    if not merged and isinstance(pr.get("statusCheckRollup"), list):
        card["pr_checks"] = checks_summary(pr)
    if merged:
        card["merged_at"] = pr.get("mergedAt")
        sha = merge_sha(pr)
        if sha:
            card["merge_sha"] = sha
    return {key: value for key, value in card.items() if value is not None}


def list_repositories(prior: list[str]) -> list[str]:
    try:
        rows = gh_json(["repo", "list", GITHUB_OWNER, "--no-archived", "--limit", "200", "--json", "nameWithOwner"])
        names = [str(row["nameWithOwner"]) for row in rows if isinstance(row, dict) and row.get("nameWithOwner")]
    except (RuntimeError, TypeError, KeyError):
        names = prior
    extra = sorted({name for name in names if name not in CORE_REPOS and re.fullmatch(r"[\w.-]+/[\w.-]+", name)})
    return [*CORE_REPOS, *extra]


def fit_snapshot_tasks(tasks: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Drop the oldest Live cards until the tasks fit the snapshot budget."""
    kept = dict(tasks)
    live = sorted((key for key, task in kept.items() if task_stage(task) == "live"),
                  key=lambda key: str(kept[key].get("completed_at") or kept[key].get("updated_at") or ""))
    while live and len(json.dumps(kept)) >= SNAPSHOT_BUDGET:
        # Drop in batches so a very large board does not re-serialise per card.
        for key in live[: max(1, len(live) // 10)]:
            kept.pop(key, None)
        live = live[max(1, len(live) // 10):]
    return kept


def build_all_repos() -> dict[str, Any]:
    """Rebuild the all-repos board from gh. A repo that cannot be read keeps
    its previous cards; when none can be read nothing is written."""
    if not gh_available():
        raise RuntimeError("gh CLI unavailable; the all-repos board was not rebuilt")
    path = state_path(ALL_REPOS_BOARD)
    try:
        prior = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        prior = {}
    prior_tasks = prior.get("tasks") if isinstance(prior.get("tasks"), dict) else {}
    prior_repos = [str(row.get("repo")) for row in prior.get("repos") or [] if isinstance(row, dict)]
    at = stamp()
    since = (now_utc() - RECENT_MERGED).date().isoformat()
    tasks: dict[str, dict[str, Any]] = {}
    rows = []
    read_any = False
    for repo in list_repositories(prior_repos):
        try:
            open_prs = gh_json(["pr", "list", "--repo", repo, "--state", "open", "--limit", "100",
                                "--json", OPEN_PR_FIELDS])
            merged_prs = gh_json(["pr", "list", "--repo", repo, "--state", "merged", "--search", f"merged:>={since}",
                                  "--limit", "50", "--json", MERGED_PR_FIELDS])
            if not isinstance(open_prs, list) or not isinstance(merged_prs, list):
                raise RuntimeError("gh pr list did not return a list")
        except RuntimeError as exc:
            kept = {key: task for key, task in prior_tasks.items() if task.get("repo") == repo}
            tasks.update(kept)
            rows.append({"repo": repo, "open": sum(task_stage(t) not in {"merged", "live"} for t in kept.values()),
                         "merged": sum(task_stage(t) in {"merged", "live"} for t in kept.values()),
                         "error": str(exc)[:200]})
            continue
        read_any = True
        for merged, prs in ((False, open_prs), (True, merged_prs)):
            for pr in prs:
                if not isinstance(pr, dict) or not isinstance(pr.get("number"), int):
                    continue
                key = card_key(repo, pr["number"])
                card = pr_card(repo, pr, merged)
                previous = prior_tasks.get(key) or {}
                if task_stage(previous) == "live" and merged:
                    for field in ("status", "stage", "evidence", "completed_at"):
                        card[field] = previous[field]
                history = previous.get("stage_history")
                if history:
                    card["stage_history"] = history
                    card["stage_entered_at"] = previous.get("stage_entered_at")
                    record_stage(card, at, task_stage(previous))
                else:
                    entered = card.get("merged_at") if merged else card.get("updated_at")
                    record_stage(card, str(entered or at), None)
                normalize_task(card)
                auto_live(card, repo, at)
                tasks[key] = card
        rows.append({"repo": repo, "open": len(open_prs), "merged": len(merged_prs)})
    if not read_any:
        raise RuntimeError("no repository could be read from gh; the all-repos board was not rebuilt")
    state = {
        "version": 2, "project": ALL_REPOS_BOARD, "title": "All repositories", "kind": "all-repos",
        "created_at": prior.get("created_at") or at,
        "updated_at": max((str(t.get("updated_at") or "") for t in tasks.values()), default=at) or at,
        "tasks": fit_snapshot_tasks(tasks), "questions": {}, "deliverables": [], "notes": [], "repos": rows,
    }
    write_json(state)
    return state


# ── project boards ───────────────────────────────────────────────────────────

def render(project: str) -> None:
    """Refresh derived PR, release and health facts and write the JSON. The
    name is kept for the launchd job; nothing here renders a page."""
    if project == ALL_REPOS_BOARD:
        build_all_repos()
        return
    state = read_state(project)
    pr_infos: dict[tuple[str, int], dict[str, Any] | None] = {}
    changed = False
    at = now_utc().isoformat(timespec="microseconds")
    for task in state.get("tasks", {}).values():
        if normalize_task(task):
            changed = True
        if task.get("pr") is None:
            continue
        key = pr_key(task)
        if key not in pr_infos:
            pr_infos[key] = pr_info(key[1], key[0])
        info = pr_infos[key]
        if info is None:
            continue
        status, stage, phase = derived_pr_state(info)
        if task_stage(task) == "live":
            status, stage = "done", "live"
        observed = [("status", status), ("stage", stage), ("pr_phase", phase),
                    ("pr_checks", checks_summary(info)), ("pr_head", info.get("headRefOid") or ""),
                    ("review_verdict", review_verdict(info))]
        if merge_sha(info):
            observed.append(("merge_sha", merge_sha(info)))
        if any(task.get(field) != value for field, value in observed):
            prior_stage = task_stage(task)
            task.update(observed)
            task["updated_at"] = at
            record_stage(task, at, prior_stage)
            normalize_task(task)
            changed = True
        if auto_live(task, task_repo(task), at):
            changed = True
    if changed:
        state["updated_at"] = max(str(task.get("updated_at") or "") for task in state["tasks"].values())
        write_json(state)
    # The retired static page: never leave a stale copy to be mistaken for a board.
    (board_dir() / f"{safe_project(project)}.html").unlink(missing_ok=True)


def write_and_render(state: dict[str, Any]) -> None:
    state["updated_at"] = stamp()
    write_json(state)
    render(state["project"])


def call_verb(verb: str, args: dict[str, Any]) -> dict[str, Any]:
    """Use the existing noninteractive local-token route; no model is involved."""
    repo = Path("/Users/booko/carr-system")
    result = subprocess.run(
        [str(repo / "run.sh"), "call", verb, json.dumps(args, sort_keys=True, separators=(",", ":"))],
        cwd=repo, capture_output=True, text=True, timeout=30, check=False,
    )
    if result.returncode:
        raise RuntimeError(f"{verb} failed: {(result.stderr or result.stdout).strip()[:500]}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{verb} returned invalid JSON") from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise RuntimeError(f"{verb} refused: {payload.get('error', 'unknown result') if isinstance(payload, dict) else 'invalid result'}")
    return payload


def stable_key(verb: str, args: dict[str, Any]) -> str:
    body = json.dumps([verb, args], sort_keys=True, separators=(",", ":"))
    return "board-" + hashlib.sha256(body.encode()).hexdigest()




def board_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    """The versioned data contract the app page renders. Deterministic, so an
    unchanged board is never republished."""
    tasks = {}
    for task_id, task in (state.get("tasks") or {}).items():
        provider, model, effort = task_identity(task)
        tasks[task_id] = {**task, "provider": provider, "model": model, "effort": effort}
    decisions = [
        {"id": qid, "question": q.get("question"), "answer": q.get("answer"), "default": q.get("default"),
         "answered_at": q.get("answered_at") or q.get("updated_at")}
        for qid, q in (state.get("questions") or {}).items() if q.get("answer")
    ]
    return {
        "schema": SNAPSHOT_SCHEMA,
        "kind": state.get("kind") or "project",
        "project": state["project"],
        "title": state.get("title") or state["project"],
        "tasks": fit_snapshot_tasks(tasks),
        "deliverables": list(state.get("deliverables") or [])[:24],
        "notes": list(state.get("notes") or [])[:50],
        "decisions": decisions,
        "ledger": executor_ledger(state.get("tasks") or {}),
        "repos": list(state.get("repos") or []),
        "updated_at": state.get("updated_at"),
    }


def question_revision(question: dict[str, Any], project: str) -> dict[str, Any]:
    choices = question.get("choices") or []
    free_text = question.get("free_text")
    return {
        "prompt": question["question"].strip(), "choices": choices,
        "allow_free_text": free_text if isinstance(free_text, bool) else not choices,
        "default_answer": question["default"].strip() if question.get("default") is not None else None,
        "asker_ref": safe_asker_ref(question.get("asker_ref") or f"orchestrator:{project}"),
    }


def publish_board(project: str) -> dict[str, int]:
    state = read_state(project)
    board = safe_project(project)
    before = call_verb("read-progress-board", {"board_id": board})
    remote_snapshot = before.get("snapshot")
    snapshot = board_snapshot(state)
    if remote_snapshot is None or remote_snapshot.get("snapshot_json") != snapshot:
        args = {"board_id": board, "base_version": int(remote_snapshot["version"]) if remote_snapshot else 0,
                "snapshot": snapshot}
        call_verb("publish-board-snapshot", {**args, "idempotency_key": stable_key("publish-board-snapshot", args)})

    remote_questions = {q["question_id"]: q for q in before.get("questions", [])}
    changed = 0
    for qid, current in state.get("questions", {}).items():
        revisions = [*current.get("history", []), current]
        expected_revision = int(current.get("revision") or 1)
        remote = remote_questions.get(qid)
        remote_revision = int(remote["revision"]) if remote else 0
        if remote_revision > expected_revision:
            raise RuntimeError(f"board question {qid} is newer on the server")
        for number, revision in enumerate(revisions, start=1):
            if number <= remote_revision:
                continue
            fields = question_revision(revision, board)
            verb = "ask-board-question" if number == 1 else "revise-board-question"
            args = {"board_id": board, "question_id": qid, "base_version": number - 1, **fields}
            call_verb(verb, {**args, "idempotency_key": stable_key(verb, args)})
            changed += 1
        if remote_revision == expected_revision and remote:
            fields = question_revision(current, board)
            if any(remote.get(key) != value for key, value in fields.items()):
                raise RuntimeError(f"board question {qid} differs on the server; revise it through ask")

    after = call_verb("read-progress-board", {"board_id": board})
    sealed = after.get("snapshot") or {}
    if sealed.get("snapshot_json") != snapshot:
        raise RuntimeError("published board snapshot did not read back")
    after_questions = {q["question_id"]: q for q in after.get("questions", [])}
    for qid, q in state.get("questions", {}).items():
        remote = after_questions.get(qid)
        if not remote or int(remote["revision"]) != int(q.get("revision") or 1):
            raise RuntimeError(f"published board question {qid} did not read back")
    return {"snapshot_version": int(sealed["version"]), "questions_changed": changed}


def answers_path(project: str) -> Path:
    return board_dir() / f"{safe_project(project)}-answers.jsonl"


def read_answer_events(project: str) -> list[dict[str, Any]]:
    path = answers_path(project)
    if not path.exists():
        return []
    contents = path.read_text(encoding="utf-8")
    if contents and not contents.endswith("\n"):
        raise RuntimeError(f"answer inbox has an incomplete trailing line: {path}")
    return [json.loads(line) for line in contents.splitlines()]


def append_answer_event(project: str, event: dict[str, Any]) -> None:
    path = answers_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        if os.write(fd, data) != len(data):
            raise OSError("short answer inbox write")
        os.fsync(fd)
    finally:
        os.close(fd)


def poll_board_answers(project: str) -> dict[str, int]:
    state = read_state(project)
    expected = {(project, qid, int(q.get("revision") or number)):
                safe_asker_ref(q.get("asker_ref") or f"orchestrator:{project}")
                for qid, current in state.get("questions", {}).items()
                for number, q in enumerate([*current.get("history", []), current], start=1)}
    refs = set(expected.values())

    def require_own_answer(answer: dict[str, Any], asker: str | None = None) -> None:
        try:
            key = (answer["board_id"], answer["question_id"], int(answer["question_revision"]))
            owner = answer["asker_ref"]
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("board answer does not match a local question") from exc
        if expected.get(key) != owner or (asker is not None and owner != asker):
            raise RuntimeError("board answer does not match a local question and asker")

    events = read_answer_events(project)
    seen = {event["answer"]["id"]: event["answer"] for event in events if event.get("kind") == "answer"}
    for answer in seen.values():
        require_own_answer(answer)
    acked = {event["answer_id"] for event in events if event.get("kind") == "ack"}
    new_count = ack_count = 0
    for asker in sorted(refs):
        cursor = max((int(answer["cursor"]) for answer in seen.values() if answer["asker_ref"] == asker), default=0)
        while True:
            page = call_verb("read-board-answers", {"after_cursor": cursor, "asker_ref": asker, "limit": 500})
            rows = page.get("answers", [])
            for answer in rows:
                require_own_answer(answer, asker)
                answer_cursor = int(answer["cursor"])
                if answer_cursor <= cursor:
                    raise RuntimeError("board answer cursor did not advance")
                cursor = answer_cursor
                if answer["id"] not in seen:
                    append_answer_event(project, {"kind": "answer", "answer": answer})
                    seen[answer["id"]] = answer
                    new_count += 1
            if len(rows) < 500:
                break

    for answer in sorted(seen.values(), key=lambda item: int(item["cursor"])):
        asker = answer["asker_ref"]
        if answer["id"] in acked or asker not in refs:
            continue
        # Live sessions acknowledge for themselves. The orchestrator's durable
        # inbox is the receiver for finished one-shot sessions and its own asks.
        if not asker.startswith(("one-shot:", "orchestrator:")):
            continue
        if answer.get("status") not in {"Received", "Applied"}:
            args = {"answer_id": answer["id"], "asker_ref": asker, "base_version": int(answer["version"])}
            try:
                receipt = call_verb("acknowledge-board-answer",
                                    {**args, "idempotency_key": stable_key("acknowledge-board-answer", args)})
                if receipt.get("answer", {}).get("status") != "Received":
                    raise RuntimeError("board answer Received did not read back from the write")
            except RuntimeError:
                # The server may have committed Received before the local ack
                # event was fsynced. Re-read that exact cursor before retrying.
                reread = call_verb("read-board-answers", {
                    "after_cursor": int(answer["cursor"]) - 1, "asker_ref": asker, "limit": 1,
                }).get("answers", [])
                if reread:
                    require_own_answer(reread[0], asker)
                if len(reread) != 1 or reread[0].get("id") != answer["id"] or \
                        reread[0].get("status") not in {"Received", "Applied"}:
                    raise
        append_answer_event(project, {"kind": "ack", "answer_id": answer["id"], "asker_ref": asker})
        ack_count += 1
    return {"new_answers": new_count, "acknowledged": ack_count}




def command_render(args: argparse.Namespace) -> None:
    if args.project == ALL_REPOS_BOARD:
        build_all_repos()
        if args.publish:
            publish_board(ALL_REPOS_BOARD)
        return
    render(args.project)
    if args.publish or args.project == LAUNCHD_BOARD:
        publish_board(args.project)
    if args.project == LAUNCHD_BOARD:
        poll_board_answers(args.project)
        # The system-wide board rides the same two-minute job, after the
        # project board so a gh outage never holds that one back.
        build_all_repos()
        publish_board(ALL_REPOS_BOARD)


def command_poll(args: argparse.Namespace) -> None:
    poll_board_answers(args.project)


def command_init(args: argparse.Namespace) -> None:
    if args.project == ALL_REPOS_BOARD:
        raise SystemExit("all-repos is built from gh by render; it has no init")
    path = state_path(args.project)
    if path.exists():
        raise SystemExit(f"board already exists: {args.project}")
    created = stamp()
    state = {
        "version": 2,
        "project": safe_project(args.project),
        "title": args.title,
        "kind": "project",
        "created_at": created,
        "updated_at": created,
        "tasks": {},
        "questions": {},
        "deliverables": [],
        "notes": [],
    }
    write_json(state)
    render(args.project)


def command_task(args: argparse.Namespace) -> None:
    state = read_state(args.project)
    task_time = stamp()
    prior = state.setdefault("tasks", {}).get(args.task_id, {})
    if not prior and not all((args.title, args.status, args.executor)):
        raise SystemExit("new tasks require --title, --status, and --executor")
    stage = "live" if args.stage == "measured" else args.stage
    if stage and stage != "live" and args.pr is None and prior.get("pr") is None:
        raise SystemExit("--stage requires --pr")
    if stage == "live" and not (args.evidence or "").strip():
        raise SystemExit("Live requires --evidence describing the measured operational outcome")
    if args.evidence and stage != "live":
        raise SystemExit("--evidence requires --stage live")
    task = dict(prior)
    executor = args.executor or prior.get("executor")
    derived = executor_metadata(executor)
    new_executor = args.executor is not None
    task.update({
        "title": args.title or prior.get("title"),
        "status": args.status or prior.get("status"),
        "executor": executor,
        "provider": args.provider or (None if new_executor else prior.get("provider")) or derived[0],
        "model": args.model or (None if new_executor else prior.get("model")) or derived[1],
        "effort": args.effort or (None if new_executor else prior.get("effort")) or derived[2],
        "pr": args.pr if args.pr is not None else prior.get("pr"),
        "repo": safe_repo(args.repo or prior.get("repo") or DEFAULT_PR_REPO),
        "note": args.note if args.note is not None else prior.get("note"),
        "created_at": prior.get("created_at", task_time),
        "updated_at": task_time,
    })
    if args.summary is not None:
        task["summary"] = args.summary.strip()
    if prior.get("pr") is not None and pr_key(prior) != pr_key(task):
        for field in ("pr_phase", "pr_checks", "pr_head", "evidence", "completed_at", "merge_sha",
                      "review_verdict", "release_wait"):
            task.pop(field, None)
        task["status"] = args.status or "running"
        task["stage"] = stage or "build"
    if args.stage:
        task["stage"] = stage
    if stage == "live":
        task["status"] = "done"
        task["evidence"] = args.evidence.strip()
        task["completed_at"] = prior.get("completed_at") if task_stage(prior) == "live" else task_time
    elif task_stage(prior) == "live" and (stage or (args.status and args.status != "done")):
        if not stage:
            task.pop("stage", None)
        task.pop("evidence", None)
        task.pop("completed_at", None)
    if args.health is not None:
        task["health"] = args.health
    if args.reason is not None:
        task["blocked_reason"] = args.reason.strip()
    if args.next_action is not None:
        task["next_action"] = args.next_action.strip()
    normalize_task(task)
    finished = task.get("status") == "done" or task_stage(task) == "live"
    if not finished and (task.get("status") == "blocked" or task.get("health") == "blocked"):
        if not (task.get("blocked_reason") and task.get("next_action")):
            raise SystemExit("a blocked task needs --reason and --next-action")
    else:
        task.pop("blocked_reason", None)
        task.pop("next_action", None)
    record_stage(task, task_time, task_stage(prior) if prior else None)
    state["tasks"][args.task_id] = task
    write_and_render(state)


def command_ask(args: argparse.Namespace) -> None:
    state = read_state(args.project)
    question_time = stamp()
    prior = state.setdefault("questions", {}).get(args.q_id, {})
    question_text = args.question.strip()
    default = args.default.strip() if args.default is not None else None
    if not question_text or not default:
        raise SystemExit("question and default must be nonempty")
    choices = args.choice or []
    if len(choices) > 8 or any(not choice.strip() or len(choice) > 500 for choice in choices) or len(set(choices)) != len(choices):
        raise SystemExit("provide at most eight distinct, nonempty choices")
    free_text = args.free_text or not choices
    if choices and not free_text and default not in choices:
        raise SystemExit("a choice-only question needs a default among its choices")
    asker_ref = safe_asker_ref(args.asker_ref or prior.get("asker_ref") or f"orchestrator:{args.project}")
    revision = int(prior.get("revision") or 1) + 1 if prior else 1
    history = list(prior.get("history", []))
    if prior:
        history.append({key: prior.get(key) for key in
                        ("question", "default", "choices", "free_text", "asker_ref", "revision")})
    state["questions"][args.q_id] = {
        "question": question_text,
        "default": default,
        "choices": choices,
        "free_text": free_text,
        "asker_ref": asker_ref,
        "revision": revision,
        "history": history,
        "answer": None,
        "created_at": prior.get("created_at", question_time),
        "updated_at": question_time,
    }
    write_and_render(state)


def command_answer(args: argparse.Namespace) -> None:
    state = read_state(args.project)
    question = state.setdefault("questions", {}).get(args.q_id)
    if question is None:
        raise SystemExit(f"question does not exist: {args.q_id}")
    answer = args.answer
    if answer is None:
        answer = sys.stdin.readline().strip()
    if not answer:
        raise SystemExit("answer must be provided on stdin or with --answer")
    question["answer"] = answer
    question["answered_at"] = question["updated_at"] = stamp()
    write_and_render(state)


def command_deliver(args: argparse.Namespace) -> None:
    state = read_state(args.project)
    state.setdefault("deliverables", []).insert(
        0, {"title": args.title, "link": args.link, "created_at": stamp()}
    )
    write_and_render(state)


def command_note(args: argparse.Namespace) -> None:
    state = read_state(args.project)
    text = args.text.strip()
    if not text:
        raise SystemExit("a note needs text")
    notes = state.setdefault("notes", [])
    notes.insert(0, {"text": text[:2000], "created_at": stamp()})
    del notes[50:]
    write_and_render(state)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("project")
    init.add_argument("--title", required=True)
    init.set_defaults(func=command_init)
    task = commands.add_parser("task")
    task.add_argument("project")
    task.add_argument("task_id")
    task.add_argument("--title")
    task.add_argument("--status", choices=STATUSES)
    task.add_argument("--executor")
    task.add_argument("--provider")
    task.add_argument("--model")
    task.add_argument("--effort")
    task.add_argument("--summary")
    task.add_argument("--pr", type=int)
    task.add_argument("--repo")
    task.add_argument("--stage", choices=PR_STAGES)
    task.add_argument("--health", choices=("healthy", "question", "blocked"))
    task.add_argument("--reason", help="why the task is blocked (required with blocked)")
    task.add_argument("--next-action", dest="next_action", help="what unblocks it (required with blocked)")
    task.add_argument("--note")
    task.add_argument("--evidence")
    task.set_defaults(func=command_task)
    ask = commands.add_parser("ask")
    ask.add_argument("project")
    ask.add_argument("q_id")
    ask.add_argument("--question", required=True)
    ask.add_argument("--default", required=True)
    ask.add_argument("--asker-ref")
    ask.add_argument("--choice", action="append")
    ask.add_argument("--free-text", action="store_true")
    ask.set_defaults(func=command_ask)
    answer = commands.add_parser("answer")
    answer.add_argument("project")
    answer.add_argument("q_id")
    answer.add_argument("--answer")
    answer.set_defaults(func=command_answer)
    deliver = commands.add_parser("deliver")
    deliver.add_argument("project")
    deliver.add_argument("--title", required=True)
    deliver.add_argument("--link", required=True)
    deliver.set_defaults(func=command_deliver)
    note = commands.add_parser("note")
    note.add_argument("project")
    note.add_argument("--text", required=True)
    note.set_defaults(func=command_note)
    render_cmd = commands.add_parser("render", help="refresh derived facts and write the board JSON")
    render_cmd.add_argument("project")
    render_cmd.add_argument("--publish", action="store_true")
    render_cmd.set_defaults(func=command_render)
    poll = commands.add_parser("poll-answers")
    poll.add_argument("project")
    poll.set_defaults(func=command_poll)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if hasattr(args, "task_id") and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", args.task_id):
        raise SystemExit("task_id must contain only letters, numbers, dot, underscore, or hyphen")
    if hasattr(args, "q_id") and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", args.q_id):
        raise SystemExit("q_id must contain only letters, numbers, dot, underscore, or hyphen")
    if getattr(args, "project", None) == ALL_REPOS_BOARD and args.command not in {"render", "poll-answers"}:
        raise SystemExit("all-repos is built from gh; only render and poll-answers apply")
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
