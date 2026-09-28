#!/usr/bin/env python3
"""CARR's small, local progress board for orchestrated work.

The JSON file is the durable local state.  The HTML file is a derived view that
can be opened directly and refreshes itself without a server.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


STATUSES = ("queued", "running", "review", "blocked", "done", "failed")
PIPELINE_STAGES = ("queued", "build", "review", "ci", "merged", "measured")
PR_STAGES = PIPELINE_STAGES[1:]
STAGE_LABELS = {
    "queued": "Queued",
    "build": "Building",
    "review": "Review",
    "ci": "CI",
    "merged": "Merged",
    "measured": "Measured",
}
STATUS_TO_STAGE = {
    "queued": "queued",
    "running": "build",
    "review": "review",
    "blocked": "review",
    "failed": "ci",
    "done": "measured",
}
STUCK_AFTER = timedelta(hours=2)


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
    return (executor.strip().split(maxsplit=1) or ["unassigned"])[0].lower()


def task_stage(task: dict[str, Any]) -> str:
    requested = task.get("stage")
    if task.get("pr") is not None and requested in PIPELINE_STAGES:
        return requested
    return STATUS_TO_STAGE.get(task.get("status", "queued"), "queued")


def task_health(task: dict[str, Any]) -> str:
    if task.get("status") == "blocked" or task.get("health") == "blocked":
        return "blocked"
    if task.get("health") == "question" or task.get("question"):
        return "question"
    return "healthy"


def executor_glyph(executor: str) -> str:
    pool = executor_pool(executor)
    return pool[:1].upper() or "?"


def pipeline_svg(tasks: dict[str, dict[str, Any]]) -> str:
    columns: dict[str, list[tuple[str, dict[str, Any]]]] = {stage: [] for stage in PIPELINE_STAGES}
    for task_id, task in tasks.items():
        columns[task_stage(task)].append((task_id, task))

    node_height = 58
    stage_header_height = 48
    max_rows = max((len(nodes) for nodes in columns.values()), default=0)
    svg_height = max(300, 54 + stage_header_height + max_rows * node_height + 18)
    mobile_rows = sum(1 + len(nodes) for nodes in columns.values())
    svg_height = max(svg_height, 54 + mobile_rows * 48)

    nodes = []
    for stage in PIPELINE_STAGES:
        stage_nodes = []
        for task_id, task in columns[stage]:
            health = task_health(task)
            pool = executor_pool(task.get("executor", "unassigned"))
            stage_nodes.append(
                f'<div class="pipeline-node node-{health}" data-task-id="{esc(task_id)}" '
                f'data-stage="{esc(stage)}" data-executor-pool="{esc(pool)}" '
                f'aria-label="{esc(task.get("title", task_id))} · {esc(STAGE_LABELS[stage])}">'
                f'<span class="executor-glyph" aria-hidden="true">{esc(executor_glyph(task.get("executor", "unassigned")))}</span>'
                f'<span class="pipeline-node-copy"><strong>{esc(task.get("title", task_id))}</strong>'
                f'<small>{esc(pool)} · {esc(health)}</small></span></div>'
            )
        nodes.append(
            f'<section class="pipeline-stage" data-stage="{esc(stage)}">'
            f'<h3>{esc(STAGE_LABELS[stage])}</h3>'
            f'<div class="pipeline-nodes">{"".join(stage_nodes) or "<span class=\"pipeline-empty\">—</span>"}</div>'
            "</section>"
        )

    arrows = "".join(
        f'<path class="pipeline-arrow" d="M{x} 34 H{x + 142}" />'
        for x in (188, 380, 572, 764, 956)
    )
    return (
        f'<svg class="pipeline-diagram" viewBox="0 0 1200 {svg_height}" role="img" '
        f'aria-labelledby="pipeline-title pipeline-desc" preserveAspectRatio="xMidYMin meet">'
        '<title id="pipeline-title">Task delivery pipeline</title>'
        '<desc id="pipeline-desc">Tasks move from queued through building, review, CI, merged, and measured.</desc>'
        f'<g aria-hidden="true">{arrows}</g>'
        f'<foreignObject class="pipeline-foreign-object" x="20" y="52" width="1160" height="{svg_height - 52}">'
        '<div class="pipeline-grid">'
        f'{"".join(nodes)}'
        '</div></foreignObject></svg>'
    )


def checks_summary(payload: dict[str, Any]) -> str:
    rollup = payload.get("statusCheckRollup") or []
    if not isinstance(rollup, list):
        return "checks unavailable"
    passed = pending = failed = 0
    for check in rollup:
        conclusion = str(check.get("conclusion") or "").upper()
        status = str(check.get("status") or "").upper()
        if conclusion in {"SUCCESS", "SK success".upper()}:
            passed += 1
        elif conclusion in {"FAILURE", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED"}:
            failed += 1
        elif status:
            pending += 1
    total = passed + pending + failed
    if total == 0:
        return "no checks"
    return f"{passed} pass · {pending} pending · {failed} fail"


def pr_info(number: int | None) -> dict[str, str]:
    if number is None:
        return {"state": "—", "checks": "—", "head": ""}
    if os.environ.get("PROGRESS_BOARD_SKIP_GH") or shutil.which("gh") is None:
        return {"state": "offline", "checks": "checks unavailable", "head": ""}
    try:
        result = subprocess.run(
            ["gh", "pr", "view", str(number), "--json", "state,headRefOid,statusCheckRollup"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode != 0:
            return {"state": "offline", "checks": "checks unavailable", "head": ""}
        payload = json.loads(result.stdout)
        head = str(payload.get("headRefOid") or "")[:8]
        return {
            "state": str(payload.get("state") or "unknown").lower(),
            "checks": checks_summary(payload),
            "head": head,
        }
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return {"state": "offline", "checks": "checks unavailable", "head": ""}


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def local_updated(timestamp: str) -> str:
    try:
        from zoneinfo import ZoneInfo

        local = datetime.fromisoformat(timestamp).astimezone(ZoneInfo("America/Chicago"))
        return local.strftime("%b %-d, %Y · %-I:%M %p CT")
    except (ValueError, TypeError, ImportError):
        return timestamp


def render_state(state: dict[str, Any]) -> str:
    tasks = state.get("tasks", {})
    questions = state.get("questions", {})
    deliverables = state.get("deliverables", [])
    stuck = [(task_id, task) for task_id, task in tasks.items() if is_stuck(task)]
    waiting = [(qid, question) for qid, question in questions.items() if not question.get("answer")]
    grouped: dict[str, list[tuple[str, dict[str, Any]]]] = {status: [] for status in STATUSES}
    for task_id, task in tasks.items():
        grouped.setdefault(task.get("status", "queued"), []).append((task_id, task))
    pools = Counter(executor_pool(task.get("executor", "unassigned")) for task in tasks.values())
    refreshed_prs = {task_id: pr_info(task.get("pr")) for task_id, task in tasks.items()}
    counts = [len(grouped.get(status, [])) for status in STATUSES]
    max_count = max(max(counts), 1)

    def task_card(task_id: str, task: dict[str, Any]) -> str:
        pr = task.get("pr")
        pr_text = "No PR"
        if pr is not None:
            info = refreshed_prs[task_id]
            pr_text = f"PR {esc(pr)} · {esc(info['state'])} · {esc(info['checks'])}"
            if info["head"]:
                pr_text += f" · {esc(info['head'])}"
        note = f'<p class="task-note">{esc(task["note"])}</p>' if task.get("note") else ""
        stuck_badge = '<span class="badge danger">STUCK</span>' if is_stuck(task) else ""
        return (
            f'<article class="task-card {"task-stuck" if is_stuck(task) else ""}">'
            f'<div class="task-top"><strong>{esc(task.get("title", task_id))}</strong>'
            f'<span class="task-id">{esc(task_id)}</span>{stuck_badge}</div>'
            f'<div class="task-meta"><span>{esc(task.get("executor", "unassigned"))}</span>'
            f'<span>{pr_text}</span></div>{note}'
            f'<time datetime="{esc(task.get("updated_at", ""))}">{esc(elapsed_text(task.get("updated_at", "")))}</time>'
            "</article>"
        )

    question_cards = "".join(
        f'<article class="question pulse"><div class="question-title">{esc(question.get("question", qid))}</div>'
        f'<div class="default"><span>DEFAULT</span> {esc(question.get("default", "Continue"))}</div>'
        f'<div class="question-time">{esc(elapsed_text(question.get("updated_at", question.get("created_at", ""))))}</div></article>'
        for qid, question in waiting
    ) or '<p class="empty">No decisions are waiting on Joe.</p>'

    stuck_cards = "".join(task_card(task_id, task) for task_id, task in stuck) or '<p class="empty">Nothing is stuck.</p>'
    status_sections = []
    for status in STATUSES:
        cards = grouped.get(status, [])
        status_sections.append(
            f'<section class="status-group"><div class="status-heading"><h3>{esc(status.title())}</h3>'
            f'<span>{len(cards)}</span></div>{"".join(task_card(task_id, task) for task_id, task in cards) or "<p class=\"empty\">—</p>"}</section>'
        )
    deliverable_cards = "".join(
        f'<article class="deliverable"><strong>{esc(item.get("title", "Deliverable"))}</strong>'
        f'<span>{esc(item.get("link", ""))}</span>'
        f'<time datetime="{esc(item.get("created_at", ""))}">{esc(local_updated(item.get("created_at", "")))}</time></article>'
        for item in deliverables
    ) or '<p class="empty">No deliverables yet.</p>'
    ledger_cards = "".join(
        f'<div class="ledger-row {"ledger-violation" if any(violation(t.get("executor", "")) and executor_pool(t.get("executor", "")) == pool for t in tasks.values()) else ""}">'
        f'<span>{esc(pool)}</span><strong>{count}</strong>'
        f'{"<em>POLICY VIOLATION · Claude-plan draw</em>" if any(violation(t.get("executor", "")) and executor_pool(t.get("executor", "")) == pool for t in tasks.values()) else ""}</div>'
        for pool, count in sorted(pools.items())
    ) or '<p class="empty">No executors recorded.</p>'
    bars = "".join(
        f'<div class="bar-row"><span>{esc(status[:3].upper())}</span><div class="bar"><i style="width:{int((count / max_count) * 100)}%"></i></div><b>{count}</b></div>'
        for status, count in zip(STATUSES, counts)
    )
    pipeline = pipeline_svg(tasks)
    updated = state.get("updated_at", stamp())

    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="10"><title>{esc(state.get("title", state["project"]))} · CARR progress board</title>
<style>
:root {{ color-scheme: dark; --ground:#06101e; --panel:rgba(13,34,59,.78); --panel-strong:#0b2545; --ink:#edf4fb; --muted:#8ca4bb; --line:rgba(152,188,220,.18); --orange:#f26b1d; --blue:#59a9df; --red:#ff6b5f; --green:#69d6a0; }}
* {{ box-sizing:border-box; }} html {{ background:var(--ground); }} body {{ margin:0; min-width:0; overflow-x:hidden; background:radial-gradient(circle at 15% 0%,#123863 0,transparent 36rem),var(--ground); color:var(--ink); font-family:ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; line-height:1.45; }}
h1,h2,h3,strong,.eyebrow {{ font-family:"Arial Narrow","Avenir Next Condensed",ui-sans-serif,system-ui,sans-serif; letter-spacing:.02em; }} h1 {{ margin:.15rem 0 .35rem; font-size:clamp(1.65rem,4vw,2.7rem); }} h2 {{ margin:0; font-size:1rem; text-transform:uppercase; letter-spacing:.12em; }} h3 {{ margin:0; font-size:.83rem; text-transform:uppercase; letter-spacing:.13em; color:var(--muted); }}
.shell {{ max-width:1280px; margin:auto; padding:28px clamp(16px,4vw,48px) 54px; }} .eyebrow {{ color:var(--orange); font-size:.68rem; font-weight:800; text-transform:uppercase; letter-spacing:.2em; }} .subhead {{ color:var(--muted); margin:0; }} .updated {{ color:var(--muted); font-size:.78rem; margin-top:18px; }}
.panels {{ display:grid; gap:16px; margin-top:24px; }} .panel {{ padding:20px; border:1px solid var(--line); border-radius:18px; background:linear-gradient(145deg,rgba(20,52,87,.78),var(--panel)); box-shadow:0 18px 50px rgba(0,0,0,.22); backdrop-filter:blur(14px); }} .panel-head {{ display:flex; justify-content:space-between; gap:12px; align-items:baseline; margin-bottom:16px; }} .count {{ color:var(--orange); font-size:1.15rem; font-weight:800; }}
.question,.task-card,.deliverable,.ledger-row {{ border:1px solid var(--line); border-radius:12px; background:rgba(3,14,28,.4); padding:13px 14px; }} .question + .question,.task-card + .task-card,.deliverable + .deliverable {{ margin-top:10px; }} .question-title {{ font-weight:750; }} .default {{ margin-top:7px; color:#d6e2ed; font-size:.9rem; }} .default span {{ color:var(--orange); font-size:.68rem; font-weight:800; letter-spacing:.12em; }} .question-time,.task-card time {{ display:block; color:var(--muted); font-size:.72rem; margin-top:8px; }}
.status-groups {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:14px; }} .status-group {{ min-width:0; }} .status-heading {{ display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid var(--line); padding-bottom:8px; margin-bottom:10px; }} .status-heading span {{ color:var(--blue); font-weight:800; }} .task-top {{ display:flex; align-items:center; gap:8px; flex-wrap:wrap; }} .task-id {{ color:var(--muted); font-size:.72rem; }} .task-meta {{ display:flex; flex-wrap:wrap; gap:5px 12px; color:#bfd1e1; font-size:.78rem; margin-top:7px; }} .task-note {{ color:var(--muted); font-size:.82rem; margin:.6rem 0 0; }} .badge {{ border-radius:99px; padding:2px 7px; font-size:.61rem; font-weight:900; letter-spacing:.08em; }} .danger {{ background:rgba(255,107,95,.18); color:var(--red); }}
.board-visual {{ display:grid; grid-template-columns:minmax(0,1fr) 260px; gap:20px; align-items:center; margin-bottom:18px; }} .bars {{ min-width:0; }} .bar-row {{ display:grid; grid-template-columns:38px minmax(0,1fr) 24px; align-items:center; gap:9px; color:var(--muted); font-size:.67rem; font-weight:800; }} .bar-row + .bar-row {{ margin-top:7px; }} .bar {{ height:7px; border-radius:99px; background:#102944; overflow:hidden; }} .bar i {{ display:block; height:100%; border-radius:99px; background:linear-gradient(90deg,var(--blue),var(--orange)); }} .board-visual svg {{ width:100%; height:auto; }}
.pipeline-panel {{ margin-top:24px; }} .pipeline-diagram {{ display:block; width:100%; height:auto; overflow:visible; }} .pipeline-arrow {{ fill:none; stroke:var(--orange); stroke-width:2; stroke-linecap:round; stroke-dasharray:5 7; opacity:.65; }} .pipeline-grid {{ display:grid; grid-template-columns:repeat(6,minmax(0,1fr)); gap:10px; width:100%; }} .pipeline-stage {{ min-width:0; min-height:92px; padding:10px; border:1px solid var(--line); border-radius:12px; background:rgba(3,14,28,.45); }} .pipeline-stage h3 {{ color:var(--orange); font-size:.68rem; margin:0 0 9px; }} .pipeline-nodes {{ display:grid; gap:8px; }} .pipeline-node {{ display:flex; align-items:center; gap:8px; min-width:0; padding:8px; border:1px solid color-mix(in srgb,var(--node-accent) 60%,transparent); border-radius:10px; background:color-mix(in srgb,var(--node-accent) 12%,rgba(3,14,28,.8)); color:var(--ink); }} .pipeline-node-copy {{ display:grid; min-width:0; gap:2px; }} .pipeline-node-copy strong,.pipeline-node-copy small {{ overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }} .pipeline-node-copy strong {{ font-size:.74rem; }} .pipeline-node-copy small {{ color:var(--muted); font-size:.61rem; text-transform:uppercase; letter-spacing:.06em; }} .executor-glyph {{ display:grid; flex:0 0 22px; width:22px; height:22px; place-items:center; border-radius:50%; background:var(--node-accent); color:#06101e; font-size:.67rem; font-weight:900; }} .node-healthy {{ --node-accent:var(--green); }} .node-question {{ --node-accent:var(--orange); }} .node-blocked {{ --node-accent:var(--red); animation:block-pulse 2.2s ease-in-out infinite; }} .pipeline-empty {{ color:var(--muted); font-size:.8rem; }}
.ledger-row {{ display:flex; align-items:center; gap:12px; }} .ledger-row + .ledger-row {{ margin-top:8px; }} .ledger-row strong {{ margin-left:auto; color:var(--blue); }} .ledger-row em {{ color:var(--red); font-size:.67rem; font-style:normal; font-weight:800; letter-spacing:.04em; }} .empty {{ color:var(--muted); margin:0; }}
.pulse {{ animation:pulse 2.8s ease-in-out infinite; }} @keyframes pulse {{ 0%,100% {{ box-shadow:0 0 0 0 rgba(242,107,29,0); }} 50% {{ box-shadow:0 0 0 5px rgba(242,107,29,.08); }} }} @keyframes block-pulse {{ 0%,100% {{ box-shadow:0 0 0 0 rgba(255,107,95,0); }} 50% {{ box-shadow:0 0 0 5px rgba(255,107,95,.2); }} }} @media (prefers-reduced-motion:reduce) {{ .pulse,.node-blocked {{ animation:none; }}}}
@media (max-width:900px) {{ .status-groups {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} .board-visual {{ grid-template-columns:1fr 190px; }} }} @media (max-width:700px) {{ .shell {{ padding-top:20px; }} .panel {{ padding:16px; border-radius:14px; }} .status-groups,.board-visual {{ grid-template-columns:1fr; }} .board-visual svg {{ max-height:100px; }} .task-meta {{ display:grid; }} .pipeline-diagram {{ min-height:520px; }} .pipeline-arrow {{ display:none; }} .pipeline-grid {{ grid-template-columns:1fr; gap:8px; }} .pipeline-stage {{ display:grid; grid-template-columns:minmax(90px,1fr) minmax(0,3fr); align-items:start; gap:10px; }} .pipeline-stage h3 {{ margin:4px 0 0; }} }}
</style></head><body><main class="shell"><header><div class="eyebrow">CARR · orchestrated work</div><h1>{esc(state.get("title", state["project"]))}</h1><p class="subhead">Project <strong>{esc(state["project"])}</strong> · live local board</p><div class="updated">{esc(elapsed_text(updated))} · {esc(local_updated(updated))}</div></header>
<section class="panel pipeline-panel"><div class="panel-head"><h2>Delivery pipeline</h2><span class="count">{len(tasks)} tasks</span></div>{pipeline}</section>
<div class="panels">
<section class="panel"><div class="panel-head"><h2>Questions waiting on Joe</h2><span class="count">{len(waiting)}</span></div>{question_cards}</section>
<section class="panel"><div class="panel-head"><h2>Stuck</h2><span class="count">{len(stuck)}</span></div>{stuck_cards}</section>
<section class="panel"><div class="panel-head"><h2>Tasks by status</h2><span class="count">{len(tasks)}</span></div><div class="board-visual"><div class="bars">{bars}</div><svg viewBox="0 0 320 110" role="img" aria-label="Task status distribution"><path d="M18 86 C62 22 101 68 143 31 S218 12 302 45" fill="none" stroke="#59a9df" stroke-width="3" opacity=".8"/><circle cx="18" cy="86" r="6" fill="#f26b1d"/><circle cx="143" cy="31" r="6" fill="#f26b1d"/><circle cx="302" cy="45" r="6" fill="#f26b1d"/><path d="M18 96H302" stroke="rgba(152,188,220,.22)"/><text x="18" y="106" fill="#8ca4bb" font-size="10">flow</text><text x="267" y="106" fill="#8ca4bb" font-size="10">delivery</text></svg></div><div class="status-groups">{"".join(status_sections)}</div></section>
<section class="panel"><div class="panel-head"><h2>Latest deliverables</h2><span class="count">{len(deliverables)}</span></div>{deliverable_cards}</section>
<section class="panel"><div class="panel-head"><h2>Executor ledger</h2><span class="count">{len(pools)} pools</span></div>{ledger_cards}</section>
</div></main></body></html>'''


def render(project: str) -> None:
    state = read_state(project)
    board_dir().mkdir(parents=True, exist_ok=True)
    html_path(project).write_text(render_state(state), encoding="utf-8")


def write_and_render(state: dict[str, Any]) -> None:
    state["updated_at"] = stamp()
    write_json(state)
    render(state["project"])


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
    if args.stage and args.pr is None:
        raise SystemExit("--stage requires --pr")
    task_time = stamp()
    prior = state.setdefault("tasks", {}).get(args.task_id, {})
    task = {
        "title": args.title,
        "status": args.status,
        "executor": args.executor,
        "pr": args.pr,
        "note": args.note,
        "created_at": prior.get("created_at", task_time),
        "updated_at": task_time,
    }
    if args.stage:
        task["stage"] = args.stage
    if args.health != "healthy":
        task["health"] = args.health
    state["tasks"][args.task_id] = task
    write_and_render(state)


def command_ask(args: argparse.Namespace) -> None:
    state = read_state(args.project)
    question_time = stamp()
    prior = state.setdefault("questions", {}).get(args.q_id, {})
    state["questions"][args.q_id] = {
        "question": args.question,
        "default": args.default,
        "answer": prior.get("answer"),
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
    task.add_argument("--title", required=True)
    task.add_argument("--status", required=True, choices=STATUSES)
    task.add_argument("--executor", required=True)
    task.add_argument("--pr", type=int)
    task.add_argument("--stage", choices=PR_STAGES)
    task.add_argument("--health", choices=("healthy", "question", "blocked"), default="healthy")
    task.add_argument("--note")
    task.set_defaults(func=command_task)
    ask = commands.add_parser("ask")
    ask.add_argument("project")
    ask.add_argument("q_id")
    ask.add_argument("--question", required=True)
    ask.add_argument("--default", required=True)
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
    render_cmd.set_defaults(func=lambda args: render(args.project))
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
