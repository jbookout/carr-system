"""Deterministic evidence classification and local job/watchdog ledgers."""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]


def load_config(path):
    config = json.loads(Path(path).read_text())
    if config["schema_version"] != 1:
        raise ValueError("unsupported watchdog config")
    for key, value in config["thresholds"].items():
        if not isinstance(value, (int, float)) or value <= 0:
            raise ValueError(f"invalid threshold {key}")
    for action in config["actions"].values():
        if isinstance(action, str) and action not in {"restart_once", "fix_once", "enqueue", "report"}:
            raise ValueError("unknown watchdog action")
    return config


def epoch(value):
    if isinstance(value, (float, int)):
        return float(value)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def stamp(now=None):
    return datetime.fromtimestamp(time.time() if now is None else now, timezone.utc).isoformat()


def finding(kind, subject, reason, config, **fields):
    key = f"{kind}:{subject}"
    needs_joe = next((k for k, patterns in config["needs_joe_patterns"].items()
                      if any(p in reason.lower() for p in patterns)), None)
    return {"key": key, "kind": kind, "subject": subject, "reason": reason,
            "next_action": config["next_actions"][kind], "owner": "orchestrator", "needs_joe": needs_joe, **fields}


def reviewed_head(comment):
    body = comment.get("body", "")
    match = re.search(r"(?mi)^Reviewed-SHA:\s*([0-9a-f]{7,40})\s*$", body)
    return match.group(1) if match else (comment.get("commit") or {}).get("oid")


def green(checks):
    if not checks:
        return False
    return all((c.get("conclusion") in {"SUCCESS", "SKIPPED", "NEUTRAL"}
                and c.get("status", "COMPLETED") == "COMPLETED") or
               c.get("state") in {"SUCCESS", "SKIPPED", "NEUTRAL"} for c in checks)


def detect(facts, config, now):
    """Classify an immutable snapshot; no I/O, models, or effects."""
    found = []
    t = config["thresholds"]
    jobs = facts.get("jobs", [])
    for job in jobs:
        subject = job["id"]
        fields = {"job": job, "card": job.get("card", subject)}
        if "exit_code" in job:
            if job["exit_code"] != 0 and not job.get("superseded"):
                found.append(finding("job_failed", subject, f"exit {job['exit_code']}: {job.get('log_tail', '')}", config, **fields))
            continue
        if not job.get("alive"):
            found.append(finding("job_dead", subject, "registered PID is dead or changed without an exit record", config, **fields))
            continue
        tail = job.get("log_tail", "")
        if any(re.search(p, tail) for p in config["hang_patterns"]):
            found.append(finding("job_hang", subject, "interactive hang signature in log tail", config, **fields))
        elif now - epoch(job.get("log_mtime", job["start"])) >= t["silent_seconds"]:
            found.append(finding("job_silent", subject, "log silent for at least the configured limit", config, **fields))
        if now - epoch(job["start"]) >= job["limit"]:
            found.append(finding("job_over_limit", subject, "registered run exceeded its time limit", config, **fields))
    queue = {tuple(line.split()[:3]) for line in facts.get("queue", "").splitlines() if len(line.split()) >= 3}
    for pr in facts.get("prs", []):
        repo, number, head = pr["repo"], pr["number"], pr["headRefOid"]
        subject = f"{repo}#{number}@{head}"
        fields = {"repo": repo, "pr": number, "head": head,
                  "card": pr.get("card", f"pr-{repo.split('/')[-1]}-{number}")}
        comments = pr.get("comments", []) + pr.get("reviews", [])
        verdicts = []
        for c in comments:
            first = c.get("body", "").splitlines()[:1]
            first = first[0].strip() if first else ""
            if reviewed_head(c) != head:
                continue
            state = c.get("state")
            if first in {"REVIEW: BLOCKED", "CHANGES REQUESTED", "APPROVE", "REVIEW: APPROVED"} or state in {"APPROVED", "CHANGES_REQUESTED"}:
                verdicts.append((epoch(c.get("submittedAt") or c.get("createdAt") or pr["updatedAt"]), c,
                                 first in {"REVIEW: BLOCKED", "CHANGES REQUESTED"} or state == "CHANGES_REQUESTED"))
        latest = max(verdicts, key=lambda v: v[0]) if verdicts else None
        commits = pr.get("commits", [])
        head_time = max((epoch(c["committedDate"]) for c in commits), default=epoch(pr["updatedAt"]))
        active_fixer = any(j.get("pr") == number and j.get("repo") == repo and j.get("head") == head
                           and "exit_code" not in j and j.get("alive") for j in jobs)
        if latest and latest[2] and now - max(head_time, latest[0]) >= t["review_idle_seconds"] and not active_fixer:
            found.append(finding("pr_blocked_review", subject, latest[1].get("body", "CHANGES REQUESTED"), config, **fields))
        checks = pr.get("statusCheckRollup") or []
        if any(c.get("conclusion") in {"FAILURE", "TIMED_OUT", "CANCELLED", "ACTION_REQUIRED", "STARTUP_FAILURE"}
               or c.get("state") in {"FAILURE", "ERROR"} for c in checks):
            found.append(finding("pr_ci_red", subject, "hosted CI failed on current head", config, **fields))
        if pr.get("mergeable") == "CONFLICTING" or pr.get("mergeStateStatus") == "DIRTY":
            found.append(finding("pr_conflict", subject, "current head has merge conflicts", config, **fields))
        if pr.get("isDraft") and now - epoch(pr["updatedAt"]) >= t["draft_idle_seconds"]:
            found.append(finding("pr_draft_idle", subject, "draft idle for configured limit", config, **fields))
        if latest and not latest[2] and green(checks) and not pr.get("isDraft") and pr.get("mergeable") == "MERGEABLE" and (repo, str(number), head) not in queue and not pr.get("mergeQueueEntry"):
            found.append(finding("pr_ready", subject, "approved current head with green CI outside merge queue", config, **fields))
    for log in facts.get("logs", []):
        kind = "queue_error" if log["type"] == "queue" else "pipeline_blocked"
        patterns = config["queue_error_patterns"] if kind == "queue_error" else ["BLOCKED"]
        # Line digest identifies a durable failure, rather than rediscovering it each interval.
        for line in log.get("tail", "").splitlines():
            if any(p.lower() in line.lower() for p in patterns):
                subject = log["path"] + ":" + hashlib.sha256(line.encode()).hexdigest()[:16]
                found.append(finding(kind, subject, line, config))
        if log["type"] == "release" and now - epoch(log["mtime"]) >= t["pipeline_stale_seconds"]:
            found.append(finding("pipeline_stale", log["path"], "release log stopped updating", config))
    for branch in facts.get("branches", []):
        if branch["name"].startswith("claude/") and now - epoch(branch["updated"]) >= t["branch_idle_seconds"]:
            found.append(finding("branch_idle", branch["repo"] + ":" + branch["name"], "claude branch idle for configured limit", config))
    for error in facts.get("errors", []):
        found.append(finding("collection_error", error["source"], error["reason"], config))
    for f in found:
        text = f["reason"].lower()
        f["needs_joe"] = next((k for k, patterns in config["needs_joe_patterns"].items()
                               if any(p in text for p in patterns)), None)
    return found


def path_at(root, configured):
    path = Path(configured).expanduser()
    return path if path.is_absolute() else root / path


@contextlib.contextmanager
def locked(path, blocking=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        yield handle


def append(path, row):
    with locked(path) as handle:
        handle.seek(0, os.SEEK_END)
        handle.write(json.dumps(row, separators=(",", ":")) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def read_latest(path):
    if not path.exists():
        return {}
    with path.open() as handle:
        fcntl.flock(handle, fcntl.LOCK_SH)
        rows = {}
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            key = row.get("key", row.get("id"))
            rows[key] = {**rows.get(key, {}), **row}
        return rows


def tail(path, limit):
    with path.open("rb") as handle:
        handle.seek(max(0, path.stat().st_size - limit))
        return handle.read().decode("utf-8", errors="replace")


def command(argv, config, cwd=None):
    result = subprocess.run(argv, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True,
                            text=True, timeout=config["thresholds"]["command_timeout_seconds"])
    if result.returncode:
        raise RuntimeError(f"{argv[0]} exit {result.returncode}: {result.stderr.strip()} {result.stdout.strip()}")
    return result.stdout


def process_identity(pid, config=None):
    if not isinstance(pid, int) or pid <= 1:
        return None
    config = config or load_config(SOURCE / "ops/config/job-watchdog.json")
    result = subprocess.run(["ps", "-p", str(pid), "-o", "lstart=", "-o", "pgid="],
                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=config["thresholds"]["process_probe_seconds"])
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def process_group_alive(pgid):
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False


def board_task(root, config, card, executor, status, note, project=None, pr=None, repo=None, needs_joe=False):
    project = project or config["board"]
    board = root / "out/boards" / (project + ".json")
    env = dict(os.environ, PROGRESS_BOARD_ROOT=str(root / "out"))
    with locked(root / "out/watchdog/board.lock"):
        if not board.exists():
            result = subprocess.run([sys.executable, str(SOURCE / "tools/progress_board.py"), "init",
                                     project, "--title", "Agent jobs"],
                                    env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                    timeout=config["thresholds"]["command_timeout_seconds"])
            if result.returncode:
                raise RuntimeError(result.stderr)
        prior = json.loads(board.read_text()).get("tasks", {}).get(card, {})
        argv = [sys.executable, str(SOURCE / "tools/progress_board.py"), "task", project, card,
                "--title", prior.get("title", card), "--executor", prior.get("executor", executor) if executor == "orchestrator" else executor, "--status", status,
                "--health", "blocked" if status == "blocked" else "healthy", "--note", note]
        argv.extend(["--lane", config["needs_joe_lane"] if needs_joe else "status"])
        if pr is not None:
            argv.extend(["--pr", str(pr), "--repo", repo])
        result = subprocess.run(argv, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                timeout=config["thresholds"]["command_timeout_seconds"])
        if result.returncode:
            raise RuntimeError(result.stderr)


def run_job(root, config, card, executor, minutes, argv):
    if minutes <= 0 or not argv or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", card):
        raise ValueError("positive minutes, safe card id, and command required")
    job_id = os.environ.get("CARR_JOB_ID") or uuid.uuid4().hex
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", job_id):
        raise ValueError("unsafe job id")
    registry = path_at(root, config["paths"]["registry"])
    if job_id in read_latest(registry):
        raise ValueError("job id already registered; refusing duplicate execution")
    log = path_at(root, config["paths"]["job_logs"]) / (job_id + ".log")
    log.parent.mkdir(parents=True, exist_ok=True)
    board_task(root, config, card, executor, "running", "Job starting; log " + str(log))
    started = time.time()
    proc = None
    row = {"id": job_id, "card": card, "executor": executor, "start": started,
           "limit": minutes * 60, "log_path": str(log), "cwd": str(Path.cwd()),
           "command": argv, "root_id": os.environ.get("CARR_JOB_ROOT_ID", job_id),
           "restart_count": int(os.environ.get("CARR_JOB_RESTART_COUNT", "0")),
           "wrapper_pid": os.getpid(), "board": config["board"]}
    for key in ("repo", "pr", "head"):
        value = os.environ.get("CARR_JOB_" + key.upper())
        if value:
            row[key] = int(value) if key == "pr" else value
    try:
        with log.open("wb") as output:
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                                    start_new_session=True)
            row.update(pid=proc.pid, pgid=proc.pid, process_identity=process_identity(proc.pid, config))
            append(registry, row)
            def stop(signum, frame):
                if proc.poll() is None:
                    os.killpg(proc.pid, signum)
            previous = {sig: signal.signal(sig, stop) for sig in (signal.SIGTERM, signal.SIGINT)}
            try:
                code = proc.wait()
            finally:
                for sig, handler in previous.items():
                    signal.signal(sig, handler)
    except OSError as exc:
        code = 127
        row.update(pid=None, process_identity=None)
        append(registry, row)
        with log.open("ab") as output:
            output.write(str(exc).encode())
    except BaseException:
        if proc and proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
        raise
    code = 128 - code if code < 0 else code
    end = {"id": job_id, "exit_code": code, "ended_at": stamp(),
           "log_tail": tail(log, config["thresholds"]["log_tail_bytes"])}
    append(registry, end)
    board_task(root, config, card, executor, "done" if code == 0 else "blocked",
               f"Exit {code}; log {log}; next: " + ("orchestrator verify result" if code == 0 else config["next_actions"]["job_failed"]))
    print(json.dumps({**row, **end}))
    return code


def reconcile(root, config, found, effects, now, complete=True):
    """Called under the scan lock. Persist intent before each non-repeatable effect."""
    findings_path = path_at(root, config["paths"]["findings"])
    actions_path = path_at(root, config["paths"]["actions"])
    previous = read_latest(findings_path)
    actions = read_latest(actions_path)
    current = {f["key"]: f for f in found}
    extras = []
    for key, f in current.items():
        prior = previous.get(key, {})
        row = {**f, "first_seen": prior.get("first_seen", stamp(now)), "cleared_at": None}
        if not prior or prior.get("cleared_at") or prior.get("reason") != f["reason"]:
            append(findings_path, row)
        # Failed reporting is retried with the SAME record-layer idempotency key.
        if not prior.get("reported") or prior.get("cleared_at"):
            try:
                effects.report(row)
                row["reported"] = True
                append(findings_path, row)
            except Exception as exc:
                extras.append(finding("record_error", key, str(exc), config))
        action = config["actions"].get(f["kind"], config["actions"]["default"])
        if f.get("needs_joe") or action == "report":
            continue
        action_key = key
        if action == "restart_once":
            job = f["job"]
            action_key = "restart:" + job.get("root_id", job["id"])
            if job.get("restart_count", 0) >= config["thresholds"]["restart_limit"]:
                continue
        existing = actions.get(action_key)
        if existing:
            if existing["status"] != "done":
                extras.append(finding("action_error", action_key, "prior action intent is unresolved; inspect before retry", config))
            continue
        intent = {"key": action_key, "action": action, "status": "intent", "at": stamp(now)}
        append(actions_path, intent)
        actions[action_key] = intent
        try:
            result = effects.act(action, f)
            append(actions_path, {**intent, "status": "done", "result": result})
        except Exception as exc:
            append(actions_path, {**intent, "status": "failed", "error": str(exc)})
            extras.append(finding("action_error", action_key, str(exc), config))
    for f in extras:
        current[f["key"]] = f
        prior = previous.get(f["key"], {})
        row = {**f, "first_seen": prior.get("first_seen", stamp(now)), "cleared_at": None}
        if not prior or prior.get("cleared_at") or prior.get("reason") != f["reason"]:
            append(findings_path, row)
        if not prior.get("reported") or prior.get("cleared_at"):
            try:
                effects.report(row)
                append(findings_path, {**row, "reported": True})
            except Exception:
                pass  # Original failure remains durable and visible; no recursive record writes.
    # Evidence-source failures cannot prove an old condition has cleared.
    if complete and not any(f["kind"] == "collection_error" for f in found):
        for key, prior in previous.items():
            if key not in current and not prior.get("cleared_at"):
                append(findings_path, {**prior, "cleared_at": stamp(now)})
    return list(current.values())


PR_FIELDS = "number,headRefOid,headRefName,updatedAt,isDraft,mergeable,comments,reviews,commits,statusCheckRollup,mergeStateStatus"


def collect_pr(repo, number, config):
    pr = json.loads(command(["gh", "pr", "view", str(number), "--repo", repo, "--json", PR_FIELDS], config))
    # gh pr's JSON fields omit queue membership; query the provider's queue entry.
    owner, name = repo.split("/")
    query = "query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){pullRequest(number:$number){mergeQueueEntry{id}}}}"
    queued = json.loads(command(["gh", "api", "graphql", "-f", "query=" + query,
                                "-f", "owner=" + owner, "-f", "name=" + name,
                                "-F", "number=" + str(number)], config))
    if queued.get("errors"):
        raise RuntimeError(json.dumps(queued["errors"]))
    pr["mergeQueueEntry"] = queued["data"]["repository"]["pullRequest"]["mergeQueueEntry"]
    pr["repo"] = repo
    return pr


def collect(root, config):
    facts = {"jobs": [], "prs": [], "logs": [], "branches": [], "errors": [], "queue": ""}
    def error(source, exc):
        facts["errors"].append({"source": source, "reason": str(exc)})
    try:
        jobs = read_latest(path_at(root, config["paths"]["registry"]))
        for job in jobs.values():
            job = dict(job)
            identity = process_identity(job.get("pid"), config)
            job["alive"] = bool(identity and identity == job.get("process_identity"))
            if "exit_code" not in job:
                log = Path(job["log_path"])
                if log.exists():
                    job["log_mtime"] = log.stat().st_mtime
                    job["log_tail"] = tail(log, config["thresholds"]["log_tail_bytes"])
                else:
                    job["log_mtime"] = job["start"]
            facts["jobs"].append(job)
    except Exception as exc:
        error("job registry", exc)
    queue = path_at(root, config["paths"]["merge_queue"])
    if queue.exists():
        try:
            facts["queue"] = queue.read_text()
        except OSError as exc:
            error("merge queue", exc)
    for repo in config["repositories"]:
        try:
            pages = json.loads(command(["gh", "api", "--paginate", "--slurp", f"repos/{repo}/pulls?state=open&per_page=100"], config))
            for page in pages:
                for pr in page:
                    try:
                        facts["prs"].append(collect_pr(repo, pr["number"], config))
                    except Exception as exc:
                        error(f"{repo}#{pr['number']}", exc)
        except Exception as exc:
            error(repo + " PRs", exc)
        try:
            pages = json.loads(command(["gh", "api", "--paginate", "--slurp", f"repos/{repo}/branches?per_page=100"], config))
            for page in pages:
                for branch in page:
                    if branch["name"].startswith("claude/"):
                        commit = json.loads(command(["gh", "api", f"repos/{repo}/commits/{branch['commit']['sha']}"], config))
                        facts["branches"].append({"repo": repo, "name": branch["name"],
                                                  "updated": commit["commit"]["committer"]["date"]})
        except Exception as exc:
            error(repo + " branches", exc)
    logs = [(p, "queue") for p in config["paths"]["queue_logs"]] + [(config["paths"]["release_log"], "release")]
    for configured, kind in logs:
        log = path_at(root, configured)
        if not log.exists():
            continue  # Optional pipelines that have never run have no stale log.
        try:
            facts["logs"].append({"path": str(log), "type": kind, "mtime": log.stat().st_mtime,
                                  "tail": tail(log, config["thresholds"]["log_tail_bytes"])})
        except OSError as exc:
            error(str(log), exc)
    # A successful restarted job supersedes the killed attempt's expected exit.
    recovered = {j.get("root_id") for j in facts["jobs"] if j.get("restart_count", 0) and j.get("exit_code") == 0}
    for job in facts["jobs"]:
        if job["id"] in recovered:
            job["superseded"] = True
    return facts


class Effects:
    def __init__(self, root, config):
        self.root, self.config = root, config
        self.children = []

    def report(self, f):
        c = self.config
        card = f.get("card") or "wd-" + hashlib.sha256(f["subject"].encode()).hexdigest()[:16]
        board_task(self.root, c, card, "orchestrator", "blocked",
                   f["reason"] + "\nNext action: " + f["next_action"], pr=f.get("pr"), repo=f.get("repo"), needs_joe=bool(f.get("needs_joe")))
        if c["actions"]["file_defects"] and f["kind"] != "pr_ready":
            digest_key = hashlib.sha256(f["key"].encode()).hexdigest()
            payload = {"idempotency_key": "job-watchdog:" + digest_key,
                       "kind": "open_loop", "owner": "orchestrator", "domain": "system",
                       "body": f["reason"] + "\nNext action: " + f["next_action"],
                       "source_note": "job watchdog: " + f["subject"],
                       "marker": "decision" if f.get("needs_joe") else "none",
                       "blocker": "capability" if f.get("needs_joe") == "credentials" else "ruling" if f.get("needs_joe") else "other_lane",
                       "blocker_detail": f["reason"] if f.get("needs_joe") else "Orchestrator's named executor or queue repair: " + f["subject"]}
            result = command([str(SOURCE / "run.sh"), "call", "add-loop", json.dumps(payload)], c)
            # run.sh emits an identity banner before JSON; validate the response itself.
            start = result.find("{")
            response = json.loads(result[start:])
            if response.get("ok") is not True or not response.get("loop_id"):
                raise RuntimeError("record layer refused watchdog defect: " + str(response))
        return {"ok": True}

    def launch(self, f, argv, cwd, *, job_id, restart_count=0, root_id=None):
        c = self.config
        env = dict(os.environ, CARR_JOB_ROOT=str(self.root), CARR_WATCHDOG_CONFIG=str(self.config_path),
                   CARR_JOB_ID=job_id, CARR_JOB_ROOT_ID=root_id or job_id,
                   CARR_JOB_RESTART_COUNT=str(restart_count), CARR_JOB_BOARD=f.get("board", c["board"]))
        for key in ("repo", "pr", "head"):
            if key in f:
                env["CARR_JOB_" + key.upper()] = str(f[key])
        output = self.root / "out/watchdog" / (job_id + ".launch.log")
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("ab") as log:
            proc = subprocess.Popen(["/bin/bash", str(SOURCE / "bin/agent-run.sh"), f["card"],
                                     f.get("executor", "Codex " + c["fixer"]["model"] + " " + c["fixer"]["effort"]),
                                     str(f.get("limit", c["thresholds"]["fixer_minutes"] * 60) / 60), "--", *argv],
                                    cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                    start_new_session=True)
        self.children.append(proc)
        return {"job_id": job_id, "wrapper_pid": proc.pid, "launch_log": str(output)}

    def restart(self, f):
        job = f["job"]
        pid = job["pid"]
        identity = process_identity(pid, self.config)
        if not identity or identity != job.get("process_identity") or job.get("pgid") != pid or os.getpgid(pid) != pid:
            raise RuntimeError("process identity/group mismatch; refusing kill")
        if "exit_code" in read_latest(path_at(self.root, self.config["paths"]["registry"])).get(job["id"], {}):
            raise RuntimeError("job exited before recovery; refusing restart")
        os.killpg(pid, signal.SIGTERM)
        deadline = time.monotonic() + self.config["thresholds"]["kill_grace_seconds"]
        while process_group_alive(pid) and time.monotonic() < deadline:
            time.sleep(self.config["thresholds"]["recovery_poll_seconds"])
        if process_group_alive(pid):
            os.killpg(pid, signal.SIGKILL)
            deadline = time.monotonic() + self.config["thresholds"]["kill_grace_seconds"]
            while process_group_alive(pid) and time.monotonic() < deadline:
                time.sleep(self.config["thresholds"]["recovery_poll_seconds"])
        # A surviving descendant must never overlap the replacement job.
        if process_group_alive(pid):
            raise RuntimeError("process group still present after termination; inspect before relaunch")
        append(path_at(self.root, self.config["paths"]["registry"]), {"id": job["id"], "superseded": True})
        return self.launch(job, job["command"], job["cwd"], job_id=uuid.uuid4().hex,
                           restart_count=job.get("restart_count", 0) + 1, root_id=job.get("root_id", job["id"]))

    def enqueue(self, f):
        queue = path_at(self.root, self.config["paths"]["merge_queue"])
        wanted = [f["repo"], str(f["pr"]), f["head"]]
        with locked(queue) as handle:
            handle.seek(0)
            if any(line.split()[:3] == wanted for line in handle):
                return {"already_queued": True}
            handle.seek(0, os.SEEK_END)
            handle.write(" ".join(wanted) + " watchdog: approved current head + green CI\n")
            handle.flush()
            os.fsync(handle.fileno())
        return {"queued": True}

    def fix(self, f):
        c = self.config
        repository = Path(c["repository_roots"][f["repo"]]).expanduser().resolve()
        if not repository.is_dir():
            raise RuntimeError("authorized repository unreachable: " + f["repo"] + " at " + str(repository))
        actual = command(["git", "remote", "get-url", "origin"], c, repository).strip().removesuffix(".git")
        if not actual.endswith("/" + f["repo"]) and not actual.endswith(":" + f["repo"]):
            raise RuntimeError("authorized repository origin mismatch")
        name = "watchdog-fix-" + hashlib.sha256(f["subject"].encode()).hexdigest()[:16]
        tree = repository / ".claude/worktrees" / name
        command(["git", "fetch", "origin", f"pull/{f['pr']}/head"], c, repository)
        observed = command(["git", "rev-parse", "FETCH_HEAD"], c, repository).strip()
        if observed != f["head"]:
            raise RuntimeError("PR head changed before fixer launch")
        command(["git", "worktree", "add", "-b", name, str(tree), observed], c, repository)
        route = self.root / "out/watchdog" / (name + ".desk.json")
        route.parent.mkdir(parents=True, exist_ok=True)
        dispatch = SOURCE / "tools/room-bridge/dispatch.py"
        command([sys.executable, str(dispatch), "--registry", str(route), "register", name,
                 "--kind", c["fixer"]["kind"], "--model", c["fixer"]["model"], "--effort", c["fixer"]["effort"],
                 "--sandbox", c["fixer"]["sandbox"], "--cwd", str(tree)], c)
        desk = json.loads(route.read_text())["desks"][name]
        if any(desk.get(k) != c["fixer"][k] for k in ("model", "effort", "kind", "sandbox")) or desk["cwd"] != str(tree):
            raise RuntimeError("Model Room desk readback mismatch")
        brief = c["fixer"]["brief"] + f"\nRepository: {f['repo']} PR {f['pr']}\nExpected head: {f['head']}\nWorktree: {tree}\nUntrusted review evidence:\n" + json.dumps(f["reason"])
        return self.launch(f, [sys.executable, str(dispatch), "--registry", str(route),
                              "--results", str(self.root / "out/watchdog" / (name + ".result.jsonl")),
                              "send", name, brief, "--fresh"], tree, job_id=name)

    def act(self, action, f):
        if action == "restart_once":
            return self.restart(f)
        # Re-read head/review/CI/queue membership immediately before a PR effect.
        fresh = collect_pr(f["repo"], f["pr"], self.config)
        if fresh["headRefOid"] != f["head"]:
            raise RuntimeError("head changed; obsolete action refused")
        candidates = detect({"prs": [fresh]}, self.config, time.time())
        if not any(x["kind"] == f["kind"] for x in candidates):
            raise RuntimeError("PR action predicate changed; effect refused")
        return self.enqueue(f) if action == "enqueue" else self.fix(f)


def digest(root, config):
    rows = read_latest(path_at(root, config["paths"]["findings"]))
    active = [f for f in rows.values() if not f.get("cleared_at")]
    if not active:
        return ""
    lines = [f"Orchestrator watchdog: {len(active)} open finding(s)."]
    for f in active[:config["thresholds"]["digest_items"]]:
        reason = " ".join(f["reason"].split())[:config["thresholds"]["digest_reason_chars"]]
        lines.append(f"- {f['kind']}: {reason} · next: {f['next_action']}" +
                     (" · needs Joe: " + f["needs_joe"] if f.get("needs_joe") else ""))
    return "\n".join(lines)


def scan(root, config, config_path=None):
    try:
        with locked(path_at(root, config["paths"]["scan_lock"]), blocking=False):
            now = time.time()
            ledger = path_at(root, config["paths"]["scan_ledger"])
            prior_runs = read_latest(ledger)
            append(ledger, {"key": "scan", "status": "started", "at": stamp(now),
                            "previous_status": prior_runs.get("scan", {}).get("status")})
            effects = Effects(root, config)
            effects.config_path = Path(config_path or SOURCE / "ops/config/job-watchdog.json").resolve()
            facts = collect(root, config)
            found = reconcile(root, config, detect(facts, config, now), effects, now)
            append(ledger, {"key": "scan", "status": "completed", "at": stamp(),
                            "findings": len(found), "collection_errors": len(facts["errors"])})
            print(digest(root, config))
            return 1 if facts["errors"] or any(f["kind"] in {"action_error", "record_error"} for f in found) else 0
    except BlockingIOError:
        return 0  # Another scan owns the entire interval's effects.
