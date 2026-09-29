#!/usr/bin/env python3
"""CARR's small, local progress board for orchestrated work.

The JSON file is the durable local state.  The HTML file is a derived view that
can be opened directly and refreshes itself without a server.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit


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
STUCK_AFTER = timedelta(hours=2)
HOSTED_BOARD_ORIGIN = "https://app.doctorcre.com"
LAUNCHD_BOARD = "carr-v5"
DEFAULT_PR_REPO = "jbookout/carr-system"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def stamp() -> str:
    return now_utc().isoformat(timespec="seconds")


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


def state_path(project: str) -> Path:
    return board_dir() / f"{safe_project(project)}.json"


def html_path(project: str) -> Path:
    return board_dir() / f"{safe_project(project)}.html"


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
    state_path(state["project"]).write_text(
        json.dumps(state, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )


def elapsed_text(timestamp: str) -> str:
    try:
        then = datetime.fromisoformat(timestamp)
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        minutes = max(0, int((now_utc() - then.astimezone(timezone.utc)).total_seconds() // 60))
    except (TypeError, ValueError):
        return "updated recently"
    return f"updated {minutes} min ago"


def is_stuck(task: dict[str, Any], at: datetime | None = None) -> bool:
    if task.get("status") == "blocked":
        return True
    if task.get("status") != "running":
        return False
    try:
        updated = datetime.fromisoformat(task["updated_at"])
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        return True
    return (at or now_utc()) - updated.astimezone(timezone.utc) > STUCK_AFTER


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


def executor_metadata(executor: str) -> tuple[str, str, str]:
    """Recover structured identity from legacy executor labels."""
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
    pool = executor_pool(value)
    provider = {"codex": "Codex", "claude-cloud": "Anthropic", "grok": "xAI",
                "flash-next": "Google"}.get(pool, "Unknown")
    return provider, value if value else "unknown", effort


def task_identity(task: dict[str, Any]) -> tuple[str, str, str]:
    derived = executor_metadata(task.get("executor", ""))
    return (str(task.get("provider") or derived[0]),
            str(task.get("model") or derived[1]),
            str(task.get("effort") or derived[2]))


def task_summary(task: dict[str, Any]) -> str:
    summary = str(task.get("summary") or "").strip()
    if summary:
        return summary
    title = str(task.get("title") or "This task").strip().rstrip(".")
    return title + "."


def one_sentence(value: str) -> str:
    text = re.sub(r"\s+", " ", re.sub(r"(?m)^\s*(?:[-*]\s+|#+\s+)", "", value or "")).strip()
    text = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0].rstrip(".")
    return text + "." if text else ""


def backfill_state(state: dict[str, Any], lookup: Any) -> int:
    """Fill legacy task metadata without changing recorded status or timestamps."""
    changed = 0
    for task in state.get("tasks", {}).values():
        before = dict(task)
        provider, model, effort = task_identity(task)
        task.setdefault("provider", provider)
        task.setdefault("model", model)
        task.setdefault("effort", effort)
        if not task.get("summary"):
            if task.get("pr") is not None:
                info = lookup(int(task["pr"]), task_repo(task))
                if not info or not info.get("title") or not info.get("body"):
                    raise RuntimeError(f"PR {task['pr']} title/body unavailable for summary backfill")
                task["summary"] = one_sentence(info["title"])
            else:
                task["summary"] = one_sentence(task.get("note") or task.get("title") or "")
        if not task.get("stage_history"):
            task["stage_history"] = [{"stage": task_stage(task), "status": task.get("status"),
                                      "at": task.get("updated_at") or task.get("created_at"),
                                      "source": "observed snapshot"}]
        if task != before:
            changed += 1
    return changed


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
    timestamp = task.get("completed_at") or task.get("updated_at")
    if not isinstance(timestamp, str):
        return None
    try:
        value = datetime.fromisoformat(timestamp)
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    except ValueError:
        return None


def task_health(task: dict[str, Any]) -> str:
    if task.get("status") in {"blocked", "failed"} or task.get("health") == "blocked" or is_stuck(task):
        return "blocked"
    if task.get("status") == "review" or task.get("health") == "question" or task.get("question"):
        return "question"
    return "healthy"


def pulse_state(task: dict[str, Any]) -> str:
    if task_health(task) == "blocked":
        return "critical"
    if task_health(task) == "question":
        return "attention"
    if task.get("status") in {"done", "queued"}:
        return "still"
    return "healthy"


def executor_glyph(executor: str) -> str:
    return {"codex": "C", "grok": "G", "flash-next": "F", "claude-cloud": "✦", "orchestrator": "O"}.get(executor_pool(executor), "?")


def pipeline_svg(tasks: dict[str, dict[str, Any]]) -> str:
    """Draw task cards in six connected SVG stages for desk and phone."""
    columns: dict[str, list[tuple[str, dict[str, Any]]]] = {stage: [] for stage in PIPELINE_STAGES}
    for task_id, task in tasks.items():
        columns[task_stage(task)].append((task_id, task))

    def node(task_id: str, task: dict[str, Any], stage: str, x: int, y: int, width: int, phone: bool) -> str:
        executor = task.get("executor", "unassigned")
        provider, model, effort = task_identity(task)
        pool = executor_pool(executor)
        health = task_health(task)
        pulse = pulse_state(task)
        title = str(task.get("title", task_id))
        max_chars = 35 if phone else 16
        lines = textwrap.wrap(title, width=max_chars, break_long_words=True) or [task_id]
        max_lines = 2
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = lines[-1][: max_chars - 1] + "…"
        label = "".join(
            f'<tspan x="{x + 54}" y="{y + 24 + i * 15}">{esc(line)}</tspan>'
            for i, line in enumerate(lines)
        )
        pr = f"PR {task['pr']}" if task.get("pr") is not None else "No PR"
        summary = task_summary(task)
        summary_limit = 44 if phone else 26
        if len(summary) > summary_limit:
            summary = summary[:summary_limit - 1] + "…"
        halo = f'<circle class="node-halo" cx="{x + 28}" cy="{y + 29}" r="15"/>'
        return (
            f'<g class="pipeline-node node-{health} node-state-{pulse} pulse-{pulse}" data-task-id="{esc(task_id)}" '
            f'data-stage="{esc(stage)}" data-executor-pool="{esc(pool)}" tabindex="0" role="button" '
            f'aria-label="{esc(title)} · {esc(task_summary(task))} · {esc(STAGE_LABELS[stage])} · {esc(provider)} · {esc(model)} · {esc(effort)} · Open details">'
            f'<rect class="node-shape" x="{x}" y="{y}" width="{width}" height="{110 if phone else 112}" rx="13"/>'
            f'{halo}'
            f'<text class="executor-glyph" x="{x + 28}" y="{y + 33}" text-anchor="middle">{esc(executor_glyph(executor))}</text>'
            f'<text class="node-label">{label}</text>'
            f'<text class="node-summary" x="{x + 16}" y="{y + 64}">{esc(summary)}</text>'
            f'<text class="node-meta" x="{x + 16}" y="{y + 83}">{esc(provider)} · {esc(pr)}</text>'
            f'<text class="node-meta" x="{x + 16}" y="{y + 99}">{esc(model)} · {esc(effort)}</text>'
            '</g>'
        )

    desktop_height = max(216, 104 + max((len(items) for items in columns.values()), default=0) * 124)
    desk_parts = []
    for i, stage in enumerate(PIPELINE_STAGES):
        x = 8 + i * 202
        items = columns[stage]
        desk_parts.append(
            f'<g class="stage" data-stage="{stage}"><rect class="stage-well" x="{x}" y="30" width="190" height="{desktop_height - 40}" rx="17"/>'
            f'<text class="stage-index" x="{x + 14}" y="59">0{i + 1}</text>'
            f'<text class="stage-label" x="{x + 14}" y="82">{STAGE_LABELS[stage]}</text>'
            f'<text class="stage-count" x="{x + 174}" y="58" text-anchor="end">{len(items):02d}</text>'
            + "".join(node(task_id, task, stage, x + 8, 96 + j * 124, 174, False) for j, (task_id, task) in enumerate(items))
            + '</g>'
        )
    connectors = "".join(
        f'<path class="pipeline-connector" d="M{198 + i * 202} 120 H{210 + i * 202}"/>'
        for i in range(5)
    )
    desktop = (
        f'<svg class="pipeline-diagram pipeline-desktop" viewBox="0 0 1224 {desktop_height}" role="img" aria-label="Delivery pipeline from queued to live">'
        f'{connectors}{"".join(desk_parts)}</svg>'
    )

    mobile_parts = []
    y = 12
    for i, stage in enumerate(PIPELINE_STAGES):
        items = columns[stage]
        section_height = 45 + max(len(items), 1) * 118
        mobile_parts.append(
            f'<g class="stage" data-stage="{stage}"><rect class="stage-well" x="0" y="{y}" width="360" height="{section_height}" rx="16"/>'
            f'<text class="stage-index" x="18" y="{y + 27}">0{i + 1}</text>'
            f'<text class="stage-label" x="49" y="{y + 29}">{STAGE_LABELS[stage]}</text>'
            f'<text class="stage-count" x="340" y="{y + 27}" text-anchor="end">{len(items):02d}</text>'
            + ("".join(node(task_id, task, stage, 12, y + 43 + j * 118, 336, True) for j, (task_id, task) in enumerate(items))
               if items else f'<text class="pipeline-empty" x="19" y="{y + 82}">No tasks at this stage</text>')
            + '</g>'
        )
        if i < 5:
            mobile_parts.append(f'<path class="pipeline-connector" d="M180 {y + section_height} V{y + section_height + 14}"/>')
        y += section_height + 14
    phone = f'<svg class="pipeline-diagram pipeline-phone" viewBox="0 0 360 {y}" role="img" aria-label="Delivery pipeline from queued to live">{"".join(mobile_parts)}</svg>'
    return desktop + phone

def checks_summary(payload: dict[str, Any]) -> str:
    rollup = payload.get("statusCheckRollup") or []
    if not isinstance(rollup, list) or any(not isinstance(check, dict) for check in rollup):
        return "checks unavailable"
    passed = pending = failed = 0
    for check in rollup:
        conclusion = str(check.get("conclusion") or "").upper()
        status = str(check.get("status") or "").upper()
        if conclusion in {"SUCCESS", "SKIPPED", "NEUTRAL"}:
            passed += 1
        elif conclusion in {"FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STARTUP_FAILURE"}:
            failed += 1
        elif status:
            pending += 1
    total = passed + pending + failed
    if total == 0:
        return "no checks"
    return f"{passed} pass · {pending} pending · {failed} fail"


def pr_info(number: int, repo: str) -> dict[str, Any] | None:
    if os.environ.get("PROGRESS_BOARD_SKIP_GH") or shutil.which("gh") is None:
        return None
    try:
        result = subprocess.run(
            ["gh", "pr", "view", str(number), "--repo", repo,
             "--json", "state,isDraft,headRefOid,statusCheckRollup,comments,author"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode != 0:
            return None
        payload = json.loads(result.stdout)
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
        return payload
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return None


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
    head = str(payload.get("headRefOid") or "").lower()
    author = payload.get("author") or {}
    maker = str(author.get("login") or "").lower()
    verdicts = []
    if maker:
        for index, comment in enumerate(payload.get("comments") or []):
            commenter = comment.get("author") or {}
            login = str(commenter.get("login") or "").lower() if isinstance(commenter, dict) else ""
            association = str(comment.get("authorAssociation") or "").upper()
            lines = str(comment.get("body") or "").splitlines()
            if (login and login != maker and association in {"OWNER", "MEMBER", "COLLABORATOR"}
                    and lines and lines[0] in {"APPROVE", "BLOCK"}):
                verdicts.append((str(comment.get("createdAt") or ""), index, lines))
    if not verdicts:
        return "review", "review", "Awaiting review"
    lines = max(verdicts)[2]
    if lines[0] == "BLOCK":
        return "blocked", "review", "Review blocked"
    approved = (re.fullmatch(r"[0-9a-f]{40}", head) is not None and len(lines) >= 2
                and lines[1] == f"Reviewed-SHA: {head}"
                and not any("reviewed-sha:" in line.lower() for line in lines[2:]))
    return "review", "review", "Ready to merge" if approved else "Awaiting review"


def review_verdict(payload: dict[str, Any]) -> str:
    """Display only the latest trusted reviewer decision for this PR head."""
    head = str(payload.get("headRefOid") or "").lower()
    maker = str((payload.get("author") or {}).get("login") or "").lower()
    verdicts = []
    for index, comment in enumerate(payload.get("comments") or []):
        commenter = comment.get("author") or {}
        login = str(commenter.get("login") or "").lower()
        association = str(comment.get("authorAssociation") or "").upper()
        lines = str(comment.get("body") or "").splitlines()
        if login and login != maker and association in {"OWNER", "MEMBER", "COLLABORATOR"} \
                and lines and lines[0] in {"APPROVE", "BLOCK"}:
            verdicts.append((str(comment.get("createdAt") or ""), index, lines))
    if not verdicts:
        return "Not recorded"
    lines = max(verdicts)[2]
    if lines[0] == "BLOCK":
        return "BLOCK"
    if (re.fullmatch(r"[0-9a-f]{40}", head) and len(lines) >= 2
            and lines[1] == f"Reviewed-SHA: {head}"
            and not any("reviewed-sha:" in line.lower() for line in lines[2:])):
        return "APPROVE"
    return "Not recorded"


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def local_updated(timestamp: str) -> str:
    try:
        from zoneinfo import ZoneInfo

        local = datetime.fromisoformat(timestamp).astimezone(ZoneInfo("America/Chicago"))
        return local.strftime("%b %-d, %Y · %-I:%M %p CT")
    except (ValueError, TypeError, ImportError):
        return timestamp


def render_state(state: dict[str, Any], pr_infos: dict[tuple[str, int], dict[str, Any] | None] | None = None,
                 rendered_at: str | None = None) -> str:
    tasks = state.get("tasks", {})
    render_time = datetime.fromisoformat(rendered_at) if rendered_at else now_utc()
    completed = sorted(((task_id, task) for task_id, task in tasks.items() if task_stage(task) == "live"),
                       key=lambda item: completed_at(item[1]) or datetime.min.replace(tzinfo=timezone.utc),
                       reverse=True)
    active = {}
    for task_id, task in tasks.items():
        completion_time = completed_at(task)
        if completion_time is None or render_time - completion_time < timedelta(hours=24):
            active[task_id] = task
    pr_infos = pr_infos or {}
    rendered_at = rendered_at or stamp()
    questions = state.get("questions", {})
    deliverables = state.get("deliverables", [])
    waiting = [(qid, q) for qid, q in questions.items() if not q.get("answer")]
    stuck = [(task_id, task) for task_id, task in active.items() if is_stuck(task) or task.get("status") == "failed"]
    grouped: dict[str, list[tuple[str, dict[str, Any]]]] = {status: [] for status in STATUSES}
    for task_id, task in active.items():
        grouped.setdefault(task.get("status", "queued"), []).append((task_id, task))
    pools = Counter(executor_pool(task.get("executor", "unassigned")) for task in tasks.values())
    github_unreachable = any(task.get("pr") is not None and pr_infos.get(pr_key(task)) is None
                             for task in tasks.values())
    task_change = max((task.get("updated_at") or "" for task in tasks.values()),
                      default=state.get("created_at") or rendered_at)

    def fingerprint(value: Any) -> str:
        return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()[:12]

    def card(task_id: str, task: dict[str, Any]) -> str:
        provider, model, effort = task_identity(task)
        info = pr_infos.get(pr_key(task)) if task.get("pr") is not None else None
        pr = task.get("pr")
        pr_label = (f"{task_repo(task)} · PR {pr} · {task.get('pr_phase', str(info.get('state', 'unknown')).title() if info else 'status unavailable')}"
                    f" · {task.get('pr_checks', checks_summary(info) if info else 'checks unavailable')}") if pr is not None else "No PR"
        note = f'<p class="task-note">{esc(task["note"])}</p>' if task.get("note") else ""
        age = elapsed_text(task.get("updated_at", ""))
        state_class = pulse_state(task)
        marker = "◇" if task.get("status") == "queued" else "!" if state_class == "critical" else "?" if state_class == "attention" else "✓" if state_class == "still" else "↗"
        stage = task_stage(task)
        return (
            f'<article class="task-card pulse-{state_class}" data-stage="{stage}" data-task-ref="{esc(task_id)}" data-item-key="task:{esc(task_id)}" data-fingerprint="{fingerprint(task)}" role="button" tabindex="0" aria-label="{esc(task.get("title", task_id))}. Open details">'
            f'<div class="task-main"><span class="state-mark" aria-hidden="true">{marker}</span>'
            f'<div class="task-copy"><strong>{esc(task.get("title", task_id))}</strong><span class="task-summary">{esc(task_summary(task))}</span><span class="task-id">{esc(task_id)}</span></div>'
            f'<span class="stage-chip">{STAGE_LABELS[stage]}</span>'
            f'<span class="task-age" data-age-at="{esc(task.get("updated_at", ""))}">{esc(age)}</span></div>'
            f'<div class="task-data"><span><b>PROVIDER</b>{esc(provider)}</span><span class="task-model">{esc(model)} · {esc(effort)}</span>'
            f'<span><b>DELIVERY</b>{esc(pr_label)}</span></div>{note}</article>'
        )

    question_cards = "".join(
        f'<article class="question-card pulse-attention" data-item-key="question:{esc(qid)}" data-fingerprint="{fingerprint(q)}">'
        f'<span class="question-mark" aria-hidden="true">?</span><div><strong>{esc(q.get("question", qid))}</strong>'
        f'<p><b>IF JOE DOES NOT ANSWER</b> {esc(q.get("default", "Continue"))}</p>'
        f'<time datetime="{esc(q.get("updated_at", ""))}" data-age-at="{esc(q.get("updated_at", q.get("created_at", "")))}">{esc(elapsed_text(q.get("updated_at", q.get("created_at", ""))))}</time></div></article>'
        for qid, q in waiting
    ) or '<p class="empty"><span class="empty-symbol">✓</span>No questions are waiting on Joe.</p>'
    stuck_cards = "".join(card(task_id, task) for task_id, task in stuck) or '<p class="empty"><span class="empty-symbol">✓</span>Nothing is stuck.</p>'
    status_sections = "".join(
        f'<section class="status-group status-{esc(status)}"><div class="status-heading"><h3>{esc(status.title())}</h3><span>{len(grouped.get(status, [])):02d}</span></div>'
        f'{"".join(card(task_id, task) for task_id, task in grouped.get(status, [])) or "<p class=\"empty compact\">No tasks</p>"}</section>'
        for status in STATUSES
    )

    completed_cards = "".join(
        f'<article class="completed-card task-card" data-stage="live" data-task-ref="{esc(task_id)}" '
        f'data-item-key="completed:{esc(task_id)}" data-fingerprint="{fingerprint(task)}" role="button" tabindex="0" aria-label="{esc(task.get("title", task_id))}. Open details">'
        f'<div class="completed-top"><strong>{esc(task.get("title", task_id))}</strong><span class="stage-chip">Live</span>'
        f'<time datetime="{esc((completed_at(task) or render_time).isoformat())}">'
        f'{esc(local_updated((completed_at(task) or render_time).isoformat()))}</time></div>'
        f'<p class="task-summary">{esc(task_summary(task))}</p>'
        f'<p class="completed-evidence"><b>MEASURED</b> {esc(task["evidence"])}</p>'
        f'<div class="completed-meta"><span><b>PROVIDER</b> {esc(task_identity(task)[0])}<small class="task-model">{esc(task_identity(task)[1])} · {esc(task_identity(task)[2])}</small></span>'
        + (f'<a href="https://github.com/{esc(task_repo(task))}/pull/{int(task["pr"])}">{esc(task_repo(task))} · PR {int(task["pr"])} ↗</a>'
           if isinstance(task.get("pr"), int) and task["pr"] > 0 else '<span>No PR</span>')
        + '</div></article>'
        for task_id, task in completed
    ) or '<p class="empty"><span class="empty-symbol">◇</span>No completed tasks yet.</p>'

    def deliverable_link(item: dict[str, Any]) -> str:
        link = str(item.get("link", "")).strip()
        # A link is inert until clicked. Never allow active URL schemes in a local artifact.
        if link and urlsplit(link).scheme.lower() in {"", "http", "https", "file"} and not link.startswith("//"):
            return f'<a href="{esc(link)}">{esc(item.get("title", "Deliverable"))}<span aria-hidden="true">↗</span></a>'
        return f'<strong>{esc(item.get("title", "Deliverable"))}</strong>'

    deliverable_cards = "".join(
        f'<article class="deliverable" data-item-key="deliverable:{i}" data-fingerprint="{fingerprint(item)}">'
        f'{deliverable_link(item)}<time datetime="{esc(item.get("created_at", ""))}">{esc(local_updated(item.get("created_at", "")))}</time></article>'
        for i, item in enumerate(deliverables[:12])
    ) or '<p class="empty"><span class="empty-symbol">◇</span>No deliverables yet.</p>'

    pool_labels = (
        ("codex", "Codex", "C"),
        ("grok", "Grok", "G"),
        ("flash-next", "Flash Next", "F"),
        ("claude-cloud", "Claude cloud credits", "✦"),
        ("orchestrator", "Orchestrator seat", "O"),
    )
    ledger_rows = []
    for key, label, glyph in pool_labels:
        offenders = [t for t in tasks.values() if executor_pool(t.get("executor", "")) == key and violation(t.get("executor", ""))]
        violation_note = '<em>POLICY VIOLATION · in-plan Claude subagent</em>' if offenders else ""
        ledger_rows.append(
            f'<div class="ledger-row {"ledger-violation" if offenders else ""}">'
            f'<span class="ledger-glyph" aria-hidden="true">{glyph}</span><span>{label}{violation_note}</span>'
            f'<strong>{pools[key]:02d}</strong></div>'
        )
    if pools["unassigned"]:
        ledger_rows.append(f'<div class="ledger-row"><span class="ledger-glyph">?</span><span>Unassigned</span><strong>{pools["unassigned"]:02d}</strong></div>')

    headline = (
        f'<strong>{len(grouped["running"])} running</strong>'
        f'<strong>{len(waiting)} need Joe</strong>'
        f'<strong>{len(stuck)} blocked</strong>'
        f'<strong class="completion-count">{len(completed)} completed · {len(tasks) - len(completed)} remaining</strong>'
        f'<span class="headline-clock"><span>Live · refreshed {esc(local_updated(rendered_at))}</span>'
        f'<span class="task-change-clock">Last task change <span class="relative-age" data-age-at="{esc(task_change)}">{esc(elapsed_text(task_change))}</span></span></span>'
    )
    detail_tasks = {}
    for task_id, task in tasks.items():
        provider, model, effort = task_identity(task)
        related = []
        for qid, question in questions.items():
            if qid in task.get("question_ids", []) or (
                len(task_id) > 5 and task_id in qid
            ):
                related.append({"question": question.get("question"), "answer": question.get("answer"),
                                "default": question.get("default")})
        detail_tasks[task_id] = {
            **task, "id": task_id, "stage_label": STAGE_LABELS[task_stage(task)],
            "provider": provider, "model": model, "effort": effort,
            "summary": task_summary(task), "related_questions": related,
        }
    task_data = json.dumps(detail_tasks, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e")
    replacements = {
        "__TITLE__": esc(state.get("title", state["project"])),
        "__PROJECT__": esc(state["project"]),
        "__HEADLINE__": headline,
        "__RENDERED_AT__": esc(rendered_at),
        "__GITHUB_BANNER__": '<div class="github-banner" role="status">GitHub PR data unavailable or invalid · showing previous PR status</div>' if github_unreachable else "",
        "__QUESTIONS__": question_cards,
        "__QUESTION_COUNT__": str(len(waiting)),
        "__STUCK__": stuck_cards,
        "__STUCK_COUNT__": str(len(stuck)),
        "__PIPELINE__": pipeline_svg(active),
        "__TASK_COUNT__": str(len(active)),
        "__STATUSES__": status_sections,
        "__COMPLETED__": completed_cards,
        "__COMPLETED_COUNT__": str(len(completed)),
        "__DELIVERABLES__": deliverable_cards,
        "__DELIVERABLE_COUNT__": str(len(deliverables)),
        "__LEDGER__": "".join(ledger_rows),
        "__HOSTED_BOARD__": esc(f"{HOSTED_BOARD_ORIGIN}/progress-board?board={quote(safe_project(state['project']), safe='')}"),
        "__TASK_DATA__": task_data,
    }
    page = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="10"><title>__TITLE__ · CARR progress board</title>
<style>
:root{color-scheme:dark;--ground:#030914;--navy:#0a203b;--ink:#f2f6fc;--muted:#8fa9c2;--line:rgba(151,190,226,.17);--orange:#fb7b32;--blue:#65baff;--red:#ff696b;--green:#7dddc0;--stage-queued:#f2f6fc;--stage-build:#fb7b32;--stage-review:#bf9cff;--stage-ci:#ff88bd;--stage-merged:#65baff;--stage-live:#7dddc0}
*{box-sizing:border-box}html{background:var(--ground)}body{margin:0;min-width:0;overflow-x:hidden;color:var(--ink);font:15px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:radial-gradient(ellipse 58rem 38rem at 12% -8%,rgba(23,83,145,.35),transparent 68%),radial-gradient(ellipse 38rem 25rem at 91% 9%,rgba(251,123,50,.11),transparent 70%),linear-gradient(180deg,#071628 0,#030914 50rem,#040c18 100%);background-attachment:fixed}
body:before{content:"";position:fixed;inset:0;pointer-events:none;opacity:.16;background-image:linear-gradient(rgba(117,176,229,.13) 1px,transparent 1px),linear-gradient(90deg,rgba(117,176,229,.13) 1px,transparent 1px);background-size:46px 46px;mask-image:linear-gradient(#000,transparent 72%)}
h1,h2,h3,.headline strong,.metric,.stage-label{font-family:"Avenir Next Condensed","Arial Narrow","Helvetica Neue",sans-serif;font-stretch:condensed}
h1{font-size:clamp(2.35rem,5vw,4.4rem);line-height:1.02;letter-spacing:-.035em;margin:4px 0 8px;font-weight:700}h2{font-size:1.05rem;letter-spacing:.08em;text-transform:uppercase;margin:0}h3{margin:0}
.shell{position:relative;max-width:1510px;margin:auto;padding:28px clamp(16px,3.4vw,56px) 70px}.masthead{display:flex;align-items:center;justify-content:space-between;gap:16px;margin-bottom:22px}.brand{display:flex;align-items:center;gap:10px;color:#c8ddf0;font-size:.72rem;font-weight:800;letter-spacing:.2em;text-transform:uppercase}.brand-mark{width:19px;height:19px;border:2px solid var(--orange);border-right-color:transparent;border-radius:50%;box-shadow:0 0 17px rgba(251,123,50,.55)}.edition{color:var(--muted);font-size:.72rem;letter-spacing:.1em;text-transform:uppercase}.eyebrow{color:var(--orange);font-size:.72rem;font-weight:800;letter-spacing:.2em;text-transform:uppercase}.subtitle{color:#a9bfd4;margin:0 0 20px;font-size:.91rem}
.headline{display:flex;align-items:center;gap:0;min-height:58px;margin-bottom:18px;padding:8px 16px;border:1px solid rgba(251,123,50,.29);border-radius:14px;background:linear-gradient(90deg,rgba(251,123,50,.13),rgba(17,51,87,.65) 39%,rgba(8,27,48,.48));box-shadow:0 16px 42px rgba(0,0,0,.24),inset 0 1px rgba(255,255,255,.08);backdrop-filter:blur(18px);-webkit-backdrop-filter:blur(18px)}
.headline strong{font-size:1.3rem;white-space:nowrap;padding:0 19px;border-right:1px solid var(--line);letter-spacing:.015em}.headline strong:first-child{padding-left:0;color:var(--blue)}.headline strong:nth-child(2){color:var(--orange)}.headline strong:nth-child(3){color:var(--red)}.headline strong.completion-count{color:var(--green);font-size:1.08rem;border:0}.headline-clock{display:grid;margin-left:auto;color:#b8ccdd;font-size:.76rem;text-align:right}.task-change-clock,.relative-age{color:var(--muted)}.stall-banner,.github-banner{margin:0 0 14px;padding:11px 15px;border-radius:12px;font-weight:750}.stall-banner{border:1px solid var(--red);color:#fff;background:rgba(176,29,39,.45);animation:stall-pulse 1s ease-in-out infinite}.github-banner{border:1px solid var(--orange);color:#ffd2ad;background:rgba(125,64,20,.34)}[hidden]{display:none!important}@keyframes stall-pulse{50%{box-shadow:0 0 24px rgba(255,105,107,.5)}}
.panel{position:relative;min-width:0;padding:20px 22px;border:1px solid var(--line);border-radius:18px;background:linear-gradient(145deg,rgba(17,46,80,.67),rgba(5,18,34,.82) 58%,rgba(7,24,44,.76));box-shadow:0 24px 52px rgba(0,0,0,.23),inset 0 1px rgba(255,255,255,.065);backdrop-filter:blur(22px);-webkit-backdrop-filter:blur(22px)}.panel:before{content:"";position:absolute;inset:0;border-radius:inherit;pointer-events:none;background:linear-gradient(120deg,rgba(255,255,255,.055),transparent 34%)}.panel-head{position:relative;display:flex;align-items:baseline;justify-content:space-between;gap:10px;margin-bottom:14px}.panel-head .count{color:var(--orange);font-size:.77rem;font-weight:800;letter-spacing:.11em}.upper-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px}.pipeline-panel{margin-bottom:14px;overflow:hidden}.pipeline-panel .panel-head{margin-bottom:8px}.section-caption{color:var(--muted);font-size:.77rem;margin:0 0 10px}
.empty{display:flex;align-items:center;gap:10px;min-height:46px;color:#a5bbcd;margin:0;font-size:.89rem}.empty-symbol{display:inline-grid;place-items:center;width:27px;height:27px;border-radius:50%;background:rgba(125,221,192,.12);color:var(--green);font-weight:800}.compact{min-height:30px;font-size:.78rem}
.question-card,.task-card,.deliverable,.ledger-row{position:relative;border:1px solid var(--line);border-radius:12px;background:rgba(1,9,19,.47)}.question-card{display:flex;gap:12px;padding:13px 15px;border-color:rgba(251,123,50,.31)}.question-card+.question-card,.task-card+.task-card,.deliverable+.deliverable{margin-top:8px}.question-card strong{display:block;font-size:.94rem}.question-card p{margin:5px 0;color:#cad9e6;font-size:.82rem}.question-card p b{color:var(--orange);font-size:.63rem;letter-spacing:.1em}.question-card time{color:var(--muted);font-size:.7rem}.question-mark{display:grid;place-items:center;flex:0 0 30px;height:30px;border:1px solid var(--orange);border-radius:9px;color:var(--orange);font-weight:800}
.pipeline-diagram{display:block;width:100%;height:auto;overflow:visible}.pipeline-phone{display:none}.stage-well{fill:rgba(2,13,29,.52);stroke:var(--stage-accent);stroke-width:1.5}.stage-index{font:700 12px -apple-system,sans-serif;letter-spacing:.1em;fill:var(--stage-accent)}.stage-label{font-size:22px;font-weight:800;fill:var(--stage-accent)}.stage-count{font:700 13px -apple-system,sans-serif;fill:var(--muted)}.pipeline-connector{fill:none;stroke:var(--orange);stroke-width:2;stroke-linecap:round;opacity:.72}.node-shape{fill:rgba(10,31,54,.95);stroke:var(--stage-accent);stroke-width:1.15}.node-halo{fill:var(--stage-accent)}.executor-glyph{fill:#03101d;font:800 12px -apple-system,sans-serif}.node-label{fill:var(--ink);font:650 12px -apple-system,sans-serif}.node-meta{fill:#a6bfd2;font:700 9px -apple-system,sans-serif;letter-spacing:.03em}.pipeline-empty{fill:var(--muted);font:13px -apple-system,sans-serif}.stage[data-stage="queued"],.pipeline-node[data-stage="queued"],.task-card[data-stage="queued"]{--stage-accent:var(--stage-queued)}.stage[data-stage="build"],.pipeline-node[data-stage="build"],.task-card[data-stage="build"]{--stage-accent:var(--stage-build)}.stage[data-stage="review"],.pipeline-node[data-stage="review"],.task-card[data-stage="review"]{--stage-accent:var(--stage-review)}.stage[data-stage="ci"],.pipeline-node[data-stage="ci"],.task-card[data-stage="ci"]{--stage-accent:var(--stage-ci)}.stage[data-stage="merged"],.pipeline-node[data-stage="merged"],.task-card[data-stage="merged"]{--stage-accent:var(--stage-merged)}.stage[data-stage="live"],.pipeline-node[data-stage="live"],.task-card[data-stage="live"]{--stage-accent:var(--stage-live)}.pipeline-node:focus .node-shape,.pipeline-node:hover .node-shape{stroke-width:2.5;fill:#173957}.pipeline-node{cursor:default;outline:none}.pipeline-node.linked .node-shape{stroke-width:2.5;fill:#173957}.task-card.linked{border-color:var(--stage-accent);background:rgba(251,123,50,.13)}
.status-groups{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.status-group{min-width:0}.status-heading{display:flex;justify-content:space-between;align-items:center;gap:10px;border-bottom:1px solid var(--line);padding:3px 0 9px;margin-bottom:10px}.status-heading h3{font-size:.75rem;letter-spacing:.13em;text-transform:uppercase;color:#b4c9d9}.status-heading span{font-family:"Arial Narrow",sans-serif;color:var(--orange);font-weight:800;font-size:1.12rem}.task-card{padding:11px 12px;min-width:0;border-left:3px solid var(--stage-accent)}.task-main{display:flex;align-items:flex-start;gap:9px;flex-wrap:wrap}.task-copy{display:flex;flex-direction:column;min-width:0}.task-copy strong{font-size:.87rem;line-height:1.27}.task-id{color:var(--muted);font-size:.66rem;margin-top:2px}.task-age{margin-left:auto;color:var(--muted);font-size:.64rem;white-space:nowrap}.stage-chip{display:inline-block;border:1px solid var(--stage-accent);border-radius:999px;padding:1px 7px;color:var(--stage-accent);font-size:.67rem;font-weight:800;white-space:nowrap}.state-mark{display:grid;place-items:center;flex:0 0 22px;height:22px;border-radius:7px;color:var(--state-accent);border:1px solid var(--state-accent);font-size:.7rem;font-weight:800}.task-data{display:grid;gap:3px;margin:8px 0 0 31px;font-size:.72rem;color:#c5d7e5}.task-data span{min-width:0;overflow-wrap:anywhere}.task-data b{display:inline-block;margin-right:7px;color:#7595ad;font-size:.57rem;letter-spacing:.08em}.task-note{margin:6px 0 0 31px;color:#a5bbce;font-size:.72rem;overflow-wrap:anywhere}.completed-panel{margin-top:14px}.completed-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px}.completed-card{margin:0!important}.completed-top{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap}.completed-top time{margin-left:auto;color:var(--muted);font-size:.7rem}.completed-evidence{margin:8px 0;color:#d8e8f2;font-size:.8rem}.completed-evidence b,.completed-meta b{color:var(--green);font-size:.63rem;letter-spacing:.08em}.completed-meta{display:flex;justify-content:space-between;gap:8px;font-size:.73rem}.completed-meta a{color:var(--blue)}.pipeline-legend{margin:8px 0 0;color:#afc5d7;font-size:.78rem}
.task-summary{display:block;margin:3px 0 0;color:#a9bfd2;font-size:.7rem;line-height:1.35}.task-model{display:block;color:#9db6c9;font-size:.68rem}.node-summary{fill:#a9bfd2;font:500 9px -apple-system,sans-serif}.task-card[role=button],.pipeline-node[role=button]{cursor:pointer}.task-card:focus-visible{outline:2px solid var(--blue);outline-offset:2px}#task-detail{width:min(600px,calc(100vw - 24px));max-height:85vh;overflow:auto;padding:25px;border:1px solid rgba(101,186,255,.42);border-radius:17px;color:var(--ink);background:linear-gradient(145deg,#102d4d,#030b19);box-shadow:0 35px 90px #000b}#task-detail::backdrop{background:#000c;backdrop-filter:blur(8px)}#task-detail button{float:right;border:1px solid var(--line);border-radius:50%;width:33px;height:33px;background:#0b2947;color:var(--ink);font-size:21px;cursor:pointer}#task-detail dl{margin:14px 0 0}#task-detail .detail-row{display:grid;grid-template-columns:105px 1fr;gap:12px;padding:9px 0;border-top:1px solid var(--line)}#task-detail dt{color:var(--muted);font-size:.7rem;text-transform:uppercase}#task-detail dd{margin:0;overflow-wrap:anywhere;white-space:pre-wrap;font-size:.84rem}#task-detail a{color:var(--blue)}
.pulse-healthy{--state-accent:var(--blue)}.pulse-attention{--state-accent:var(--orange)}.pulse-critical{--state-accent:var(--red)}.pulse-still{--state-accent:var(--green)}
.pipeline-node.pulse-healthy .node-halo{animation:breath 3.5s ease-in-out infinite;transform-box:fill-box;transform-origin:center}.pipeline-node.pulse-attention .node-halo{animation:breath 2s ease-in-out infinite;transform-box:fill-box;transform-origin:center}.pipeline-node.pulse-critical .node-halo{animation:breath 1s ease-in-out infinite;transform-box:fill-box;transform-origin:center}.pulse-healthy.task-card,.pulse-attention.question-card,.pulse-attention.task-card,.pulse-critical.task-card{animation:glow var(--pulse-speed) ease-in-out infinite}.pulse-healthy{--pulse-speed:3.5s}.pulse-attention{--pulse-speed:2s}.pulse-critical{--pulse-speed:1s}@keyframes breath{50%{opacity:.55;transform:scale(.8)}}@keyframes glow{50%{box-shadow:inset 0 0 18px rgba(101,186,255,.085)}}.changed{animation:changed-flash 1s ease-out 1!important}@keyframes changed-flash{0%{background:rgba(251,123,50,.34)}100%{background:rgba(1,9,19,.47)}}
.lower-grid{display:grid;grid-template-columns:1.2fr .8fr;gap:14px;margin-top:14px}.deliverable{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 12px;font-size:.84rem}.deliverable a{color:var(--ink);font-weight:700;text-decoration:none}.deliverable a:hover{text-decoration:underline;color:var(--orange)}.deliverable a span{margin-left:7px;color:var(--orange)}.deliverable time{color:var(--muted);font-size:.69rem;white-space:nowrap}.ledger-row{display:flex;align-items:center;gap:10px;padding:8px 11px;font-size:.8rem}.ledger-row+.ledger-row{margin-top:6px}.ledger-glyph{display:grid;place-items:center;width:25px;height:25px;border-radius:8px;background:rgba(101,186,255,.14);color:var(--blue);font-size:.7rem;font-weight:800}.ledger-row strong{margin-left:auto;color:var(--blue);font-family:"Arial Narrow",sans-serif;font-size:1.1rem}.ledger-row em{display:block;color:var(--red);font-size:.64rem;font-style:normal;font-weight:800;letter-spacing:.03em}.ledger-violation{border-color:var(--red);background:rgba(255,105,107,.09)}.ledger-violation .ledger-glyph,.ledger-violation strong{color:var(--red)}
@media(max-width:1000px){.status-groups{grid-template-columns:repeat(2,minmax(0,1fr))}.headline-clock{max-width:24ch}}
@media(max-width:700px){.shell{padding:16px 12px 50px}.masthead{margin-bottom:16px}.edition{display:none}.subtitle{margin-bottom:15px}.headline{display:grid;grid-template-columns:repeat(2,1fr);gap:4px 0;padding:10px 8px}.headline strong{padding:0 6px;text-align:center;font-size:.98rem}.headline-clock{grid-column:1/-1;max-width:none;margin:4px 0 0;text-align:center;font-size:.69rem}.upper-grid,.lower-grid,.status-groups,.completed-list{grid-template-columns:1fr}.panel{padding:15px 13px;border-radius:15px}.pipeline-desktop{display:none}.pipeline-phone{display:block}.status-groups{gap:13px}.deliverable{align-items:flex-start;flex-direction:column;gap:3px}h1{font-size:clamp(2rem,8vw,2.5rem);overflow-wrap:anywhere}.stage-label{font-size:18px}}
@media(prefers-reduced-motion:reduce){*,*:before,*:after{animation:none!important;transition:none!important;scroll-behavior:auto!important}.changed{outline:2px solid var(--orange)}.node-halo{opacity:1!important;transform:none!important}}
</style></head><body><main class="shell">
<div class="masthead"><div class="brand"><span class="brand-mark" aria-hidden="true"></span>CARR <span style="color:#789cb9">/</span> SYSTEMS</div><span class="edition">Orchestration · __PROJECT__</span></div>
<header><div class="eyebrow">Mission control / __PROJECT__</div><h1>__TITLE__</h1><p class="subtitle"><a href="__HOSTED_BOARD__">Open the interactive board ↗</a> · This saved copy is read-only when offline.</p></header>
<div id="stall-banner" class="stall-banner" role="alert" hidden>Board refresh stalled</div>__GITHUB_BANNER__
<div class="headline" aria-label="Project summary">__HEADLINE__</div>
<div class="upper-grid">
<section class="panel"><div class="panel-head"><h2>Questions waiting on Joe</h2><span class="count">__QUESTION_COUNT__ OPEN</span></div>__QUESTIONS__</section>
<section class="panel"><div class="panel-head"><h2>Stuck</h2><span class="count">__STUCK_COUNT__ ITEMS</span></div>__STUCK__</section>
</div>
<section class="panel pipeline-panel"><div class="panel-head"><h2>Delivery pipeline</h2><span class="count">__TASK_COUNT__ ACTIVE TASKS</span></div><p class="section-caption">Queued → Building → Review → CI → Merged → Live</p>__PIPELINE__<p class="pipeline-legend">Merged = code on main. Live = released where it runs and verified by a measured outcome.</p></section>
<section class="panel"><div class="panel-head"><h2>Tasks by status</h2><span class="count">__TASK_COUNT__ TOTAL</span></div><div class="status-groups">__STATUSES__</div></section>
<section class="panel completed-panel"><div class="panel-head"><h2>Completed</h2><span class="count">__COMPLETED_COUNT__ LIVE</span></div><div class="completed-list">__COMPLETED__</div></section>
<div class="lower-grid">
<section class="panel"><div class="panel-head"><h2>Latest deliverables</h2><span class="count">__DELIVERABLE_COUNT__ LINKS</span></div>__DELIVERABLES__</section>
<section class="panel"><div class="panel-head"><h2>Executor ledger</h2><span class="count">5 POOLS</span></div>__LEDGER__</section>
</div></main>
<dialog id="task-detail" aria-labelledby="task-detail-title"><form method="dialog"><button aria-label="Close task detail">×</button></form><p class="eyebrow">DELIVERY / TASK</p><h2 id="task-detail-title"></h2><dl id="task-detail-body"></dl></dialog>
<script type="application/json" id="board-task-data">__TASK_DATA__</script>
<script>
(function(){
  var taskData=JSON.parse(document.getElementById('board-task-data').textContent||'{}');
  var detail=document.getElementById('task-detail'),detailBody=document.getElementById('task-detail-body');
  function row(label,value){if(value===undefined||value===null||value==='')return;var item=document.createElement('div');item.className='detail-row';var dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=label;dd.textContent=String(value);item.append(dt,dd);detailBody.append(item)}
  function taskDetail(id){var task=taskData[id];if(!task)return;document.getElementById('task-detail-title').textContent=task.title||id;detailBody.replaceChildren();row('Summary',task.summary);row('Status',task.status);row('Stage',task.stage_label);row('Provider',task.provider);row('Model',task.model);row('Effort',task.effort);row('Repository',task.repo);row('Review',task.review_verdict||task.pr_phase);row('CI',task.pr_checks);row('Created',task.created_at);row('Updated',task.updated_at);row('Completed',task.completed_at);row('Note',task.note);row('Evidence',task.evidence);String(task.evidence||'').split(' ').filter(function(part){return part.indexOf('https://')===0||part.indexOf('http://')===0}).forEach(function(raw){try{var url=new URL(raw.replace(/[.)]+$/,''));if(!['http:','https:'].includes(url.protocol))return;var link=document.createElement('a');link.href=url.href;link.textContent=url.href;link.target='_blank';link.rel='noopener noreferrer';var item=document.createElement('div');item.className='detail-row';var dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent='Evidence link';dd.append(link);item.append(dt,dd);detailBody.append(item)}catch(_){}});if(task.pr){var link=document.createElement('a');link.href='https://github.com/'+(task.repo||'jbookout/carr-system')+'/pull/'+Number(task.pr);link.textContent='PR '+task.pr+(task.pr_head?' · '+task.pr_head:'');var item=document.createElement('div');item.className='detail-row';var dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent='Pull request';dd.append(link);item.append(dt,dd);detailBody.append(item)}(task.pr_links||[]).forEach(function(pr){var number=Number(pr.number),repo=String(pr.repo||'');if(!Number.isSafeInteger(number)||number<=0||repo.split('/').length!==2)return;var link=document.createElement('a');link.href='https://github.com/'+repo+'/pull/'+number;link.textContent=repo+' · PR '+number+(pr.head_sha?' · '+pr.head_sha:'');var item=document.createElement('div');item.className='detail-row';var dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent='Pull request';dd.append(link);item.append(dt,dd);detailBody.append(item)});(task.related_questions||[]).forEach(function(q){row('Question',q.question);row('Answer',q.answer||'Unanswered · '+(q.default||''))});(task.stage_history||[]).forEach(function(h){row('Stage history',(h.stage||'')+' · '+(h.status||'')+' · '+(h.at||''))});detail.showModal()}
  document.querySelectorAll('[data-task-id],[data-task-ref]').forEach(function(el){function open(event){if(event.type==='keydown'&&event.key!=='Enter'&&event.key!==' ')return;if(event.target.closest&&event.target.closest('a'))return;event.preventDefault();taskDetail(el.dataset.taskId||el.dataset.taskRef)}el.addEventListener('click',open);el.addEventListener('keydown',open)});
  var renderedAt=Date.parse('__RENDERED_AT__');
  function checkStall(){var banner=document.getElementById('stall-banner');var age=Date.now()-renderedAt;banner.hidden=Number.isFinite(age)&&age>=-30000&&age<=360000}
  checkStall();setInterval(checkStall,1000);
  var key='carr-board:'+location.pathname+':';
  try{var saved=sessionStorage.getItem(key+'scrollY');if(saved!==null){requestAnimationFrame(function(){scrollTo(0,Number(saved)||0)})}}catch(_){}
  var changedItems={};
  document.querySelectorAll('[data-item-key]').forEach(function(el){
    try{
      var itemKey=key+el.dataset.itemKey,now=el.dataset.fingerprint;
      if(!Object.prototype.hasOwnProperty.call(changedItems,itemKey)){
        var prior=sessionStorage.getItem(itemKey);
        changedItems[itemKey]=Boolean(prior&&prior!==now);
        sessionStorage.setItem(itemKey,now);
      }
      if(changedItems[itemKey]){el.classList.add('changed');setTimeout(function(){el.classList.remove('changed')},1100)}
    }catch(_){}
  });
  document.querySelectorAll('[data-task-id],[data-task-ref]').forEach(function(el){
    function link(on){var id=el.dataset.taskId||el.dataset.taskRef;document.querySelectorAll('[data-task-id],[data-task-ref]').forEach(function(other){if((other.dataset.taskId||other.dataset.taskRef)===id){other.classList.toggle('linked',on)}})}
    el.addEventListener('mouseenter',function(){link(true)});el.addEventListener('mouseleave',function(){link(false)});
    el.addEventListener('focus',function(){link(true)});el.addEventListener('blur',function(){link(false)});
  });
  function updateAges(){document.querySelectorAll('[data-age-at]').forEach(function(el){
    var at=Date.parse(el.dataset.ageAt);if(!Number.isFinite(at))return;
    var minutes=Math.max(0,Math.floor((Date.now()-at)/60000));
    var age=minutes+' min ago';el.textContent=el.classList.contains('relative-age')?age:'updated '+age;
  })}
  updateAges();setInterval(updateAges,10000);
  addEventListener('beforeunload',function(){try{sessionStorage.setItem(key+'scrollY',String(scrollY))}catch(_){}});
})();
</script></body></html>"""
    for key, value in replacements.items():
        page = page.replace(key, value)
    return page

def render(project: str) -> None:
    state = read_state(project)
    pr_infos: dict[tuple[str, int], dict[str, Any] | None] = {}
    changed = False
    for task_id, task in state.get("tasks", {}).items():
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
        observed = (("status", status), ("stage", stage), ("pr_phase", phase),
                    ("pr_checks", checks_summary(info)), ("pr_head", info.get("headRefOid") or ""),
                    ("review_verdict", review_verdict(info)))
        if any(task.get(key) != value for key, value in observed):
            previous_stage = task_stage(task)
            task.update(observed)
            task["updated_at"] = now_utc().isoformat(timespec="microseconds")
            if stage != previous_stage:
                task["stage_history"] = [*task.get("stage_history", []),
                                         {"stage": stage, "status": status, "at": task["updated_at"]}]
            changed = True
    if changed:
        state["updated_at"] = max(task["updated_at"] for task in state["tasks"].values())
        write_json(state)
    board_dir().mkdir(parents=True, exist_ok=True)
    html_path(project).write_text(render_state(state, pr_infos,
                                              now_utc().isoformat(timespec="microseconds")), encoding="utf-8")


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
    return {key: state[key] for key in ("project", "title", "tasks", "deliverables", "updated_at")}


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
    render(args.project)
    if args.publish or args.project == LAUNCHD_BOARD:
        publish_board(args.project)
    if args.project == LAUNCHD_BOARD:
        poll_board_answers(args.project)


def command_poll(args: argparse.Namespace) -> None:
    poll_board_answers(args.project)


def command_init(args: argparse.Namespace) -> None:
    path = state_path(args.project)
    if path.exists():
        raise SystemExit(f"board already exists: {args.project}")
    created = stamp()
    state = {
        "version": 1,
        "project": safe_project(args.project),
        "title": args.title,
        "created_at": created,
        "updated_at": created,
        "tasks": {},
        "questions": {},
        "deliverables": [],
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
    derived_provider, derived_model, derived_effort = executor_metadata(executor)
    task.update({
        "title": args.title or prior.get("title"),
        "status": args.status or prior.get("status"),
        "executor": executor,
        "provider": args.provider or (prior.get("provider") if args.executor is None else None) or derived_provider,
        "model": args.model or (prior.get("model") if args.executor is None else None) or derived_model,
        "effort": args.effort or (prior.get("effort") if args.executor is None else None) or derived_effort,
        "summary": (args.summary.strip() if args.summary is not None else prior.get("summary"))
                   or task_summary({"title": args.title or prior.get("title")}),
        "pr": args.pr if args.pr is not None else prior.get("pr"),
        "repo": safe_repo(args.repo or prior.get("repo") or DEFAULT_PR_REPO),
        "note": args.note if args.note is not None else prior.get("note"),
        "created_at": prior.get("created_at", task_time),
        "updated_at": task_time,
    })
    if prior.get("pr") is not None and pr_key(prior) != pr_key(task):
        for field in ("pr_phase", "pr_checks", "pr_head", "evidence", "completed_at"):
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
    if task_stage(task) != task_stage(prior) or not prior:
        task["stage_history"] = [*prior.get("stage_history", []),
                                 {"stage": task_stage(task), "status": task["status"], "at": task_time}]
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
    question["updated_at"] = stamp()
    write_and_render(state)


def command_deliver(args: argparse.Namespace) -> None:
    state = read_state(args.project)
    state.setdefault("deliverables", []).insert(
        0, {"title": args.title, "link": args.link, "created_at": stamp()}
    )
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
    render_cmd = commands.add_parser("render")
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
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
