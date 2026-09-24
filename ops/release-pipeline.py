#!/usr/bin/env python3
"""release-pipeline.py — release what merged to main, by script, unattended.

# doctrine: scripted-release-pipeline

WHY (decisions 17ef11fa and b729859d, 2026-09-24). Joe: CARR runs itself, and
"even the review, merge, and release steps can be triggered by scripts too".
A separate pipeline reviews and merges and appends a merge event (one JSON line)
to out/merge-events.jsonl. This file is the RELEASE step that consumes merges to
main. It is ticked by launchd (ops/launchd/com.carr.release-pipeline.plist,
whose header carries the three install commands; installing it is a deliberate
human act) through bin/run-scheduled.sh, like every other local job.

IT IS A SCRIPT, NOT A MODEL SESSION. Production credentials stay where the
manual release already reads them (~/.config/carr/db.env, the wrangler login,
~/.config/carr/mcp-tokens.env), and each step is the SAME sanctioned command the
manual release on 2026-09-23 and 2026-09-24 ran, in the same order, from a
detached release worktree at the exact SHA. The only thing a model ever gets is
a diagnosis task AFTER a failure, through the Model Room queue, with repo-write
and nothing more: it fixes forward through a PR, and the next merge to main is
what this pipeline releases next. Production is never routed through a session.

TWO LANES, one tick:

  worker  the CARR MCP Worker. Released when the batch (last released SHA ..
          origin/main) touches a release path in ops/config/release-pipeline.v1.json
          (mcp-server/, migrations/, dealroom/, the DoctorCRE artifact pin), less
          test-only and doc-only files. Sequence, all non-interactive (stdin is
          /dev/null for every child):
            1 release worktree at S (+ .venv link, npm ci in mcp-server)
            2 tools/staging-project-replacement.py prepare --apply --local-checks-green
            3 tools/provision-staging-app-writer.py --apply
            4 bin/migrate-prod.sh (dry) and, only when it lists pending, --apply
            5 bin/deploy-worker.sh --upload-version  (verifier bound HERE)
            6 bin/deploy-worker.sh --env staging --recovery-step forward_fix
            7 bin/deploy-worker.sh --promote-version <id from step 5>
            8 live /release reads back S, ./run.sh health
            9 a db/schema.sql follow-up PR when step 4 applied anything
  app     the DoctorCRE app (its own repository). Released when its origin/main
          moves by anything other than docs/tests: `npm ci` and
          `npm run release:production` from a clean detached origin/main
          checkout, then /app-release reads the SHA back.

BATCHING. Each lane releases the LATEST main SHA, never each merge separately.
The last released SHA per lane lives in out/release-pipeline/state.json; when it
is absent it is bootstrapped from what production serves (/release, /app-release).

STOP, RECORD, DISPATCH. Any nonzero exit stops the lane at that step. The run
record (out/release-pipeline/releases.jsonl) names the step, exit code and log
path; the failing SHA is remembered and NEVER retried: only a new main SHA (the
fix-forward merge) starts a new attempt. A Model Room diagnosis session is
enqueued (`@queue enqueue target=claude-desktop cap=repo-write`,
key=release-fix-<sha8>). Promotion is structurally unreachable unless the
staging step of the SAME run returned 0.

BLOCKED IS NOT FAILED. Missing review evidence, a pending or red main canary, or
a missing unattended credential stops the lane WITHOUT marking the SHA failed,
so the next tick re-evaluates it. A missing credential or capability files one
CARR loop naming it exactly (once per name), because no retry can supply it.

VERIFIER ≠ MAKER. The database derives the release maker from the filing login
(carr_jobs) and refuses a verifier equal to it (ops.approve_program5_release,
ops.record_program5_release_readiness). The verifier here is the independent
review agent whose approving comment is on the merged PR: its slug comes from the
merge event (`reviewer`), else a `Verifier: <slug>` line in the comment, else the
configured default. It is refused if it names the maker, a human partner (the
review was not a human's and must never be booked as one), this pipeline, the
GitHub account the comment was posted through, or the merge event's author.

KILL SWITCH. `enabled` in ops/config/release-pipeline.v1.json (a commit), or the
file ~/.config/carr/release-pipeline.off (this machine, no commit). Per-lane
`enabled` flags too. A single-run lock (flock on out/release-pipeline/lock)
makes a second tick a no-op while one is running.

  ops/release-pipeline.py                 # one tick, both lanes (launchd)
  ops/release-pipeline.py --dry-run       # print the exact commands, run no deploy
  ops/release-pipeline.py --lane worker   # one lane
  ops/release-pipeline.py report [--date YYYY-MM-DD]   # what shipped, for a daily note

Tested offline by ops/release-pipeline-selftest.py with fake runners.
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime as dt
import fcntl
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable

REPO = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO / "ops" / "config" / "release-pipeline.v1.json"
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
SLUG_RE = re.compile(r"^[a-z][a-z0-9._:-]{1,63}$")
ROOM_NAMESPACE = uuid.UUID("3d0e3c55-6a51-4c3e-9b8f-2b6c1f0a7e41")
BREW_PATH = ("/opt/homebrew/opt/node@22/bin:/usr/local/opt/node@22/bin:/opt/homebrew/bin:"
             "/opt/homebrew/sbin:/opt/homebrew/opt/libpq/bin:/usr/local/bin:/usr/bin:/bin:"
             "/usr/sbin:/sbin")
CHILD_ENV_NAMES = ("HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR", "SHELL",
                   "SSL_CERT_FILE", "SSL_CERT_DIR")


# ── results and the command runner seam ───────────────────────────────────────

@dataclasses.dataclass
class Result:
    rc: int
    out: str = ""
    log: str = ""


class StepFailed(Exception):
    def __init__(self, step: str, rc: int, log: str, detail: str = ""):
        super().__init__(f"{step} exited {rc}")
        self.step, self.rc, self.log, self.detail = step, rc, log, detail


class Blocked(Exception):
    """The lane cannot proceed and no retry of the same inputs would help it
    today, but nothing failed: re-evaluated on the next tick."""

    def __init__(self, reason: str, detail: str, capability: str | None = None):
        super().__init__(f"{reason}: {detail}")
        self.reason, self.detail, self.capability = reason, detail, capability


def child_env(environ: dict[str, str] | None = None) -> dict[str, str]:
    """The environment every step runs under: `brew shellenv`'s PATH and the
    login basics, and NOTHING credential-shaped. Each step loads its own
    credential from its own file, exactly as it does when a human runs it;
    tools/provision-staging-app-writer.py refuses outright if an ambient
    DATABASE_URL or CARR_DB_* is present, so passing ours through would break
    step 3 and would be wrong anyway."""
    environ = dict(os.environ if environ is None else environ)
    env = {k: environ[k] for k in CHILD_ENV_NAMES if environ.get(k)}
    env["PATH"] = BREW_PATH
    env["HOMEBREW_PREFIX"] = "/opt/homebrew"
    env["NO_COLOR"] = "1"
    return env


class Runner:
    """Runs one command with stdin closed, output to its own log file."""

    def run(self, argv: list[str], *, cwd: Path, log: Path, env: dict[str, str],
            timeout: int = 3600) -> Result:
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(f"$ (cd {cwd}) {' '.join(argv)}\n")
            fh.flush()
            try:
                proc = subprocess.run(argv, cwd=str(cwd), env=env, stdin=subprocess.DEVNULL,
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      text=True, timeout=timeout)
                out, rc = proc.stdout or "", proc.returncode
            except subprocess.TimeoutExpired as exc:
                out, rc = (exc.stdout or "") if isinstance(exc.stdout, str) else "", 124
                out += f"\nrelease-pipeline: timed out after {timeout}s\n"
            except OSError as exc:
                out, rc = f"release-pipeline: could not start: {exc}\n", 127
            fh.write(out)
            fh.write(f"\n[exit {rc}]\n")
        return Result(rc, out)


def http_json(url: str, timeout: int = 30) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": "carr-release-pipeline"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — fixed config URLs
        return json.loads(resp.read(262144).decode("utf-8"))


class GitHub:
    """Read-only GitHub lookups through the authenticated `gh` CLI."""

    def __init__(self, repo: str, env: dict[str, str]):
        self.repo, self.env = repo, env

    def api(self, path: str) -> Any:
        proc = subprocess.run(["gh", "api", path], env=self.env, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            raise Blocked("github_unreadable", f"gh api {path.split('?')[0]} exited {proc.returncode}")
        return json.loads(proc.stdout or "null")

    def pr_for_commit(self, sha: str) -> dict | None:
        rows = self.api(f"repos/{self.repo}/commits/{sha}/pulls") or []
        merged = [r for r in rows if r.get("merged_at")]
        return merged[0] if merged else None

    def runs_for(self, head_sha: str) -> list[dict]:
        data = self.api(f"repos/{self.repo}/actions/runs?head_sha={head_sha}&per_page=50") or {}
        return list(data.get("workflow_runs") or [])

    def jobs(self, run_id: int) -> list[dict]:
        data = self.api(f"repos/{self.repo}/actions/runs/{run_id}/jobs?per_page=100") or {}
        return list(data.get("jobs") or [])

    def comments(self, pr: int) -> list[dict]:
        return list(self.api(f"repos/{self.repo}/issues/{pr}/comments?per_page=100") or [])

    def comment(self, comment_id: int) -> dict:
        return self.api(f"repos/{self.repo}/issues/comments/{comment_id}") or {}

    def check_runs(self, sha: str) -> list[dict]:
        data = self.api(f"repos/{self.repo}/commits/{sha}/check-runs?per_page=100") or {}
        return list(data.get("check_runs") or [])


# ── configuration, state, records ─────────────────────────────────────────────

def load_config(path: Path = CONFIG_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def expand(p: str) -> Path:
    return Path(os.path.expanduser(p))


def kill_switch(cfg: dict, lane: str | None = None) -> str | None:
    """Why this run must not proceed, or None. Checked before any command."""
    if cfg.get("enabled") is not True:
        return "disabled by ops/config/release-pipeline.v1.json (enabled != true)"
    off = expand(cfg.get("local_off_file", "~/.config/carr/release-pipeline.off"))
    if off.exists():
        return f"disabled on this machine by {off}"
    if lane is not None and (cfg.get(lane) or {}).get("enabled") is not True:
        return f"lane {lane} disabled by ops/config/release-pipeline.v1.json"
    return None


class Store:
    def __init__(self, root: Path):
        self.root = root
        self.state_path = root / "state.json"
        self.records_path = root / "releases.jsonl"

    def load(self) -> dict:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def save(self, state: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(tmp, self.state_path)

    def record(self, row: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        row = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), **row}
        with open(self.records_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")

    def records(self) -> list[dict]:
        try:
            lines = self.records_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out = []
        for line in lines:
            with contextlib.suppress(ValueError):
                out.append(json.loads(line))
        return out


@contextlib.contextmanager
def single_run_lock(root: Path):
    """flock, not a pid file: the kernel drops it when the holder dies, so a
    crashed tick can never wedge every later one. Yields False when another
    run holds it; the caller treats that as a clean no-op."""
    root.mkdir(parents=True, exist_ok=True)
    fh = open(root / "lock", "a+")
    try:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        fh.seek(0); fh.truncate(); fh.write(f"{os.getpid()}\n"); fh.flush()
        try:
            yield True
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    finally:
        fh.close()


def read_merge_events(path: Path) -> dict[str, dict]:
    """Index the merge-event file by merge SHA. The format belongs to the
    review-and-merge pipeline; this reads it tolerantly and only takes review
    evidence from it — main's SHA, not this file, is what decides a release."""
    events: dict[str, dict] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return events
    for line in lines:
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        sha = str(ev.get("merge_sha") or ev.get("merge_commit_sha") or ev.get("sha") or "").lower()
        if SHA_RE.fullmatch(sha):
            events[sha] = ev
    return events


# ── pure decisions (the selftest's main surface) ─────────────────────────────

def _glob_hit(path: str, glob: str) -> bool:
    """fnmatch, plus `**/` matching zero directories (so `a/**/*.md` covers
    `a/README.md` and `**/*.md` covers a top-level `README.md`)."""
    candidates = {glob, glob.replace("/**/", "/")}
    if glob.startswith("**/"):
        candidates.add(glob[3:])
    return any(fnmatch.fnmatchcase(path, g) for g in candidates)


def classify(paths: Iterable[str], lane_cfg: dict) -> tuple[bool, list[str]]:
    """(needs_release, the paths that make it so). A lane with release_paths
    counts only paths under them; a lane without counts everything. Either way
    doc-only and test-only paths (non_release_globs) never release."""
    prefixes = lane_cfg.get("release_paths")
    ignore = lane_cfg.get("non_release_globs") or []
    hits = []
    for p in paths:
        p = p.strip()
        if not p:
            continue
        if prefixes is not None and not any(p == x.rstrip("/") or p.startswith(x) for x in prefixes):
            continue
        if any(_glob_hit(p, g) for g in ignore):
            continue
        hits.append(p)
    return bool(hits), hits


def choose_verifier(cfg: dict, event: dict | None, comment_body: str) -> str:
    """The independent reviewer's slug, or Blocked. Never the maker."""
    event = event or {}
    raw = str(event.get("reviewer") or event.get("verifier_actor") or "").strip()
    if not raw:
        m = re.search(r"^\s*verifier:\s*([A-Za-z0-9._:-]+)\s*$", comment_body or "", re.I | re.M)
        raw = m.group(1) if m else str(cfg.get("default_verifier") or "")
    slug = raw.strip().lower()
    if not SLUG_RE.fullmatch(slug):
        raise Blocked("verifier_invalid", f"verifier {raw!r} is not a lowercase actor slug")
    forbidden = {str(x).lower() for x in cfg.get("forbidden_verifiers") or []}
    makers = {str(event.get(k) or "").strip().lower()
              for k in ("author_actor", "maker_actor", "author", "maker")} - {""}
    if slug in forbidden or slug in makers:
        raise Blocked("verifier_is_maker",
                      f"verifier {slug!r} is the maker or not an independent reviewer")
    return slug


def is_approval(body: str, markers: Iterable[str]) -> bool:
    first = (body or "").strip().splitlines()[0].strip().lower() if (body or "").strip() else ""
    return any(first.startswith(m.lower()) for m in markers)


def evidence_ref_from_url(url: str) -> str:
    m = re.fullmatch(r"https://github\.com/([^/]+/[^/]+)/pull/(\d+)#issuecomment-(\d+)", url or "")
    if not m:
        raise Blocked("review_evidence_malformed", f"not a PR comment URL: {url!r}")
    return f"github:{m.group(1)}/pull/{m.group(2)}#issuecomment-{m.group(3)}"


def next_release_key(today: str, exists: Callable[[str], bool]) -> str:
    for n in range(1, 100):
        key = f"r-{today}-{n:02d}"
        if not exists(key):
            return key
    raise Blocked("release_key_exhausted", f"r-{today}-01..99 all exist")


def queue_turn(lane: str, sha: str, step: str, rc: int, log: str, record_path: str) -> dict:
    key = f"release-fix-{sha[:8]}"
    body = (f"@queue enqueue target=claude-desktop cap=repo-write priority=P1 runtime=3h "
            f"key={key} :: Fix forward: {lane} release of {sha[:12]} failed at {step}\n"
            f"The scripted release pipeline (ops/release-pipeline.py) stopped: step `{step}` "
            f"exited {rc} releasing {sha} ({lane} lane).\n"
            f"Step log: {log}\nRun record: {record_path}\n"
            "Diagnose from the log and fix forward through an ordinary PR; do not merge it and "
            "do not run any deploy, migration or promotion yourself. This SHA is never retried: "
            "the pipeline releases the next main SHA after your fix merges. Reply in the room "
            "with the PR URL.")
    return {"idempotency_key": str(uuid.uuid4()), "room": "model-room", "seat": "claude",
            "kind": "turn", "body": body, "msg_id": str(uuid.uuid5(ROOM_NAMESPACE, key))}


def blocker_loop(capability: str, detail: str) -> dict:
    return {"idempotency_key": str(uuid.uuid5(ROOM_NAMESPACE, "release-pipeline-blocker:" + capability)),
            "kind": "open_loop", "owner": "Joe", "domain": "system", "marker": "none",
            "blocker": "capability", "blocker_detail": detail,
            "body": (f"The scripted release pipeline (ops/release-pipeline.py) cannot run "
                     f"unattended: {detail}. It stops at that step every tick until this "
                     f"exists; nothing is released meanwhile."),
            "unblocks": "unattended Worker/app release on every merge to main"}


# ── the pipeline ──────────────────────────────────────────────────────────────

class Pipeline:
    def __init__(self, cfg: dict, *, repo: Path = REPO, runner: Runner | None = None,
                 github: Callable[[str], Any] | None = None,
                 http: Callable[[str], Any] = http_json,
                 call_verb: Callable[[str, dict], tuple[bool, Any]] | None = None,
                 dry_run: bool = False, env: dict[str, str] | None = None,
                 today: str | None = None, out: Callable[[str], None] = print):
        self.cfg, self.repo, self.dry_run = cfg, repo, dry_run
        self.runner = runner or Runner()
        self.env = env if env is not None else child_env()
        self.github_factory = github or (lambda repo_name: GitHub(repo_name, self.env))
        self.http = http
        self.call_verb = call_verb or self._call_verb
        self.today = today or dt.date.today().isoformat()
        self.out = out
        self.store = Store(repo / cfg.get("state_dir", "out/release-pipeline"))
        self.run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
        self.run_dir = self.store.root / "runs" / self.run_id
        self.executed: list[str] = []   # step names that actually ran, in order

    # -- plumbing ---------------------------------------------------------
    def _call_verb(self, verb: str, args: dict) -> tuple[bool, Any]:
        """The sanctioned Bash door (run.sh call <verb>), same as
        tools/cutover-watch.py; never the generic call-verb passthrough."""
        env = {k: v for k, v in self.env.items() if k in ("HOME", "PATH", "LANG")}
        try:
            proc = subprocess.run([str(self.repo / "run.sh"), "call", verb, json.dumps(args)],
                                  cwd=str(self.repo), env=env, stdin=subprocess.DEVNULL,
                                  capture_output=True, text=True, timeout=120)
        except Exception as exc:  # noqa: BLE001 — a failed filing is reported, not raised
            return False, f"{type(exc).__name__}: {exc}"
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()
            return False, f"run.sh call {verb} exit {proc.returncode}: {tail[-1] if tail else ''}"
        try:
            return True, json.loads(proc.stdout)
        except ValueError:
            return True, proc.stdout.strip()

    def git(self, *args: str, cwd: Path | None = None) -> str:
        proc = subprocess.run(["git", "-C", str(cwd or self.repo), *args], env=self.env,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            raise StepFailed(f"git {args[0]}", proc.returncode, "", (proc.stderr or "").strip()[:300])
        return proc.stdout.strip()

    def step(self, name: str, argv: list[str], cwd: Path, *, timeout: int = 3600) -> Result:
        """Run one step, or in dry-run only print it. Nonzero is StepFailed."""
        shown = " ".join(argv)
        if self.dry_run:
            self.out(f"  [dry-run] (cd {cwd}) {shown}")
            return Result(0, "")
        n = len(self.executed) + 1
        log = self.run_dir / f"{n:02d}-{name}.log"
        self.out(f"  -> {name}: {shown}")
        self.executed.append(name)
        res = self.runner.run(argv, cwd=cwd, log=log, env=self.env, timeout=timeout)
        if res.rc != 0:
            raise StepFailed(name, res.rc, str(log))
        res.log = str(log)
        return res

    # -- evidence -----------------------------------------------------------
    def worker_evidence(self, lane_cfg: dict, base: str, sha: str) -> dict:
        gh = self.github_factory(lane_cfg["github_repo"])
        events = read_merge_events(self.repo / self.cfg.get("merge_event_file", "out/merge-events.jsonl"))
        for run in gh.runs_for(sha):
            if run.get("name") == lane_cfg["canary_workflow_name"]:
                if run.get("status") != "completed":
                    raise Blocked("canary_pending", f"main canary on {sha[:12]} has not finished")
                if run.get("conclusion") not in ("success", "skipped", "neutral"):
                    raise Blocked("canary_red", f"main canary on {sha[:12]} concluded {run.get('conclusion')}")
        commits = self.git("rev-list", "--first-parent", f"{base}..{sha}").split() if base else [sha]
        markers = lane_cfg.get("review_markers") or []
        reviewed: list[int] = []
        pre_pipeline: list[int] = []
        head: dict | None = None
        for commit in commits:
            pr = gh.pr_for_commit(commit)
            if pr is None:
                raise Blocked("no_pull_request", f"{commit[:12]} reached main without a pull request")
            number = int(pr["number"])
            ev = events.get(commit) or {}
            url, body = "", ""
            if ev.get("review_comment_url"):
                m = re.search(r"#issuecomment-(\d+)$", str(ev["review_comment_url"]))
                c = gh.comment(int(m.group(1))) if m else {}
                if str(c.get("issue_url", "")).endswith(f"/issues/{number}") and is_approval(c.get("body", ""), markers):
                    url, body = c.get("html_url", ""), c.get("body", "")
            if not url:
                for c in reversed(gh.comments(number)):
                    if is_approval(c.get("body", ""), markers):
                        url, body = c.get("html_url", ""), c.get("body", "")
                        break
            if not url:
                cutover = str(lane_cfg.get("review_required_after") or "")
                if commit != sha and cutover and str(pr.get("merged_at") or "") < cutover:
                    pre_pipeline.append(number)
                    continue
                raise Blocked("no_independent_review",
                              f"PR #{number} ({commit[:12]}) has no approving independent-review comment")
            reviewed.append(number)
            if commit == sha:
                head = {"pr": number, "head_sha": pr["head"]["sha"], "url": url, "body": body, "event": ev}
        assert head is not None
        verifier = choose_verifier(lane_cfg, head["event"], head["body"])
        ci = [r for r in gh.runs_for(head["head_sha"])
              if r.get("name") == lane_cfg["ci_workflow_name"] and r.get("event") == "pull_request"
              and r.get("conclusion") == "success"]
        if not ci:
            raise Blocked("ci_not_green", f"PR #{head['pr']} has no successful {lane_cfg['ci_workflow_name']} run")
        run_id = max(int(r["id"]) for r in ci)
        jobs = gh.jobs(run_id)
        if not any(j.get("name") == lane_cfg["ci_required_job"] and j.get("conclusion") == "success" for j in jobs):
            raise Blocked("ci_not_green", f"run {run_id} lacks a green `{lane_cfg['ci_required_job']}`")
        if not any("secret" in str(j.get("name", "")) and j.get("conclusion") == "success" for j in jobs):
            raise Blocked("ci_not_green", f"run {run_id} lacks a green secret-class job")
        repo_name = lane_cfg["github_repo"]
        return {"pr": head["pr"], "prs": reviewed, "pre_pipeline_prs": pre_pipeline, "verifier": verifier,
                "verifier_evidence": evidence_ref_from_url(head["url"]),
                "test_evidence": f"github-actions:{repo_name}/runs/{run_id}#{lane_cfg['test_evidence_label']}",
                "security_evidence": f"github-actions:{repo_name}/runs/{run_id}#{lane_cfg['security_evidence_label']}"}

    def unattended_preflight(self, lane_cfg: dict) -> None:
        """Names only — never a value. A missing name is a Blocked with a
        capability, which files one loop naming it."""
        def names(path: Path) -> set[str]:
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                return set()
            return {m.group(2) for m in re.finditer(r"^(export\s+)?([A-Z0-9_]+)=\S", text, re.M)}
        cred = expand(self.cfg.get("credential_dir", "~/.config/carr"))
        db = names(cred / "db.env")
        for name in lane_cfg.get("required_db_env_names") or []:
            if name not in db:
                raise Blocked("credential_missing", f"{name} is absent from ~/.config/carr/db.env",
                              capability=name)
        tokens = names(cred / "mcp-tokens.env")
        for name in lane_cfg.get("required_token_env_names") or []:
            if name not in tokens:
                raise Blocked("credential_missing", f"{name} is absent from ~/.config/carr/mcp-tokens.env",
                              capability=name)

    # -- lanes --------------------------------------------------------------
    def last_released(self, state: dict, lane: str, lane_cfg: dict) -> str:
        sha = (state.get(lane) or {}).get("last_released_sha")
        if sha:
            return sha
        live = self.http(lane_cfg["live_release_url"])
        value = ((live.get("git_sha") or {}).get("value") if lane == "worker"
                 else live.get("source_commit"))
        if not isinstance(value, str) or not SHA_RE.fullmatch(value):
            raise Blocked("bootstrap_unknown", f"{lane_cfg['live_release_url']} names no full SHA")
        return value

    def tick(self, lanes: Iterable[str]) -> int:
        why = kill_switch(self.cfg)
        if why:
            self.out(f"release-pipeline: {why}; nothing run")
            return 0
        with single_run_lock(self.store.root) as got:
            if not got:
                self.out("release-pipeline: another run holds the lock; this tick is a no-op")
                return 0
            rc = 0
            for lane in lanes:
                rc = max(rc, self.run_lane(lane))
            return rc

    def run_lane(self, lane: str) -> int:
        lane_cfg = self.cfg[lane]
        why = kill_switch(self.cfg, lane)
        if why:
            self.out(f"release-pipeline[{lane}]: {why}")
            return 0
        state = self.store.load()
        lane_state = state.setdefault(lane, {})
        sha = base = ""
        try:
            if lane == "worker":
                repo_dir = self.repo
            else:
                repo_dir = expand(lane_cfg["repo_path"])
            self.git("fetch", "--quiet", "origin", "main", cwd=repo_dir)
            sha = self.git("rev-parse", "origin/main", cwd=repo_dir)
            base = self.last_released(state, lane, lane_cfg)
            if sha == base:
                self.out(f"release-pipeline[{lane}]: main {sha[:12]} is already released")
                return 0
            if sha == lane_state.get("failed_sha"):
                self.out(f"release-pipeline[{lane}]: {sha[:12]} failed at "
                         f"{lane_state.get('failed_step')}; waiting for a fix-forward merge")
                return 0
            try:
                self.git("merge-base", "--is-ancestor", base, sha, cwd=repo_dir)
            except StepFailed:
                raise Blocked("history_diverged", f"released {base[:12]} is not an ancestor of main {sha[:12]}")
            changed = self.git("diff", "--name-only", base, sha, cwd=repo_dir).splitlines()
            needed, hits = classify(changed, lane_cfg)
            self.out(f"release-pipeline[{lane}]: batch {base[:12]}..{sha[:12]}: "
                     f"{len(changed)} path(s), {len(hits)} release path(s)")
            if not needed:
                self.out(f"release-pipeline[{lane}]: doc/test-only batch; nothing to release")
                if not self.dry_run:
                    lane_state["last_released_sha"] = sha
                    self.store.save(state)
                    self.store.record({"lane": lane, "sha": sha, "from_sha": base,
                                       "status": "no_release_needed", "run_id": self.run_id})
                return 0
            if lane == "worker":
                result = self.release_worker(lane_cfg, base, sha)
            else:
                result = self.release_app(lane_cfg, repo_dir, base, sha)
            if self.dry_run:
                self.out(f"release-pipeline[{lane}]: dry run complete; nothing executed")
                return 0
            lane_state.update({"last_released_sha": sha, "failed_sha": None, "failed_step": None,
                               "last_shipped_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")})
            self.store.save(state)
            self.store.record({"lane": lane, "sha": sha, "from_sha": base, "status": "shipped",
                               "run_id": self.run_id, "paths": hits[:50], **result})
            self.out(f"release-pipeline[{lane}]: SHIPPED {sha[:12]}")
            return 0
        except Blocked as b:
            self.out(f"release-pipeline[{lane}]: BLOCKED {b.reason} — {b.detail}")
            if self.dry_run:
                return 0
            row: dict[str, Any] = {"lane": lane, "sha": sha, "from_sha": base, "status": "blocked",
                   "reason": b.reason, "detail": b.detail, "run_id": self.run_id}
            if b.capability:
                filed = state.setdefault("filed_blockers", {})
                if b.capability not in filed:
                    ok, res = self.call_verb("add-loop", blocker_loop(b.capability, b.detail))
                    row["loop_filed"] = ok
                    if ok:
                        filed[b.capability] = self.today
                    else:
                        self.out(f"release-pipeline[{lane}]: could not file the loop: {res}")
                self.store.save(state)
            self.store.record(row)
            return 3 if b.capability else 0
        except StepFailed as f:
            self.out(f"release-pipeline[{lane}]: FAILED at {f.step} (exit {f.rc}); log {f.log or '-'}")
            if self.dry_run:
                return 1
            lane_state.update({"failed_sha": sha, "failed_step": f.step,
                               "failed_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")})
            self.store.save(state)
            ok, res = (False, "no SHA") if not sha else self.call_verb(
                "add-room-turn", queue_turn(lane, sha, f.step, f.rc, f.log, str(self.store.records_path)))
            self.store.record({"lane": lane, "sha": sha, "from_sha": base, "status": "failed",
                               "step": f.step, "rc": f.rc, "log": f.log, "detail": f.detail,
                               "run_dir": str(self.run_dir), "executed": list(self.executed),
                               "dispatched": ok, "run_id": self.run_id})
            if not ok:
                self.out(f"release-pipeline[{lane}]: diagnosis dispatch FAILED: {res}")
            return 1

    def dry_tolerant(self, what: str, fn: Callable[[], Any], placeholder: Any) -> Any:
        """In a dry run a stop is REPORTED and the plan still prints, so the
        operator sees both why a real run would stop and what it would run."""
        try:
            return fn()
        except Blocked as b:
            if not self.dry_run:
                raise
            self.out(f"  [dry-run] a real run would STOP here ({what}): {b.reason} — {b.detail}")
            return placeholder

    def release_worker(self, lane_cfg: dict, base: str, sha: str) -> dict:
        ev = self.dry_tolerant("evidence", lambda: self.worker_evidence(lane_cfg, base, sha), {
            "pr": "<PR>", "prs": [], "verifier": "<independent reviewer slug>",
            "verifier_evidence": "github:<repo>/pull/<N>#issuecomment-<X>",
            "test_evidence": f"github-actions:{lane_cfg['github_repo']}/runs/<CI run id>#{lane_cfg['test_evidence_label']}",
            "security_evidence": f"github-actions:{lane_cfg['github_repo']}/runs/<CI run id>#{lane_cfg['security_evidence_label']}"})
        self.out(f"  evidence: PR #{ev['pr']} verifier={ev['verifier']} "
                 f"({ev['verifier_evidence']}); test={ev['test_evidence']}")
        self.dry_tolerant("unattended credentials", lambda: self.unattended_preflight(lane_cfg), None)
        wt = self.store.root / "worktrees" / f"worker-{sha[:12]}"
        mcp = wt / "mcp-server"
        py = str(wt / ".venv/bin/python")
        budget = ["--performance-budget-ref", lane_cfg["performance_budget_ref"],
                  "--performance-budget-ms", str(lane_cfg["performance_budget_ms"]),
                  "--recovery-strategy", lane_cfg["recovery_strategy"],
                  "--rollback-plan-ref", lane_cfg["rollback_plan_ref"]]
        cand = lane_cfg["staging_candidate_operation_id"]

        # 1. the release worktree at exactly S
        if wt.exists():
            raise StepFailed("worktree", 1, "", f"{wt} already exists; a prior run left it for diagnosis")
        self.step("worktree", ["git", "-C", str(self.repo), "worktree", "add", "--detach", str(wt), sha], self.repo)
        self.step("venv-link", ["ln", "-s", str(self.repo / ".venv"), str(wt / ".venv")], self.repo)
        self.step("npm-ci", ["npm", "ci", "--no-audit", "--no-fund"], mcp, timeout=1800)
        who = self.step("wrangler-auth", [str(mcp / "node_modules/.bin/wrangler"), "whoami"], mcp)
        if not self.dry_run and "not authenticated" in who.out.lower():
            raise Blocked("credential_missing",
                          "wrangler has no usable login for unattended use (OAuth refresh failed); "
                          "a scoped CLOUDFLARE_API_TOKEN for Workers deploy is needed",
                          capability="CLOUDFLARE_API_TOKEN")

        # 2-3. staging replacement and app writer
        op = str(uuid.uuid4())
        prep = self.step("staging-prepare", [py, "tools/staging-project-replacement.py", "prepare", "--apply",
                                             "--local-checks-green", "--sha", sha, "--operation-id", op,
                                             "--candidate-operation-id", cand], wt)
        receipt = "<receipt_id from staging-prepare>" if self.dry_run else parse_json_field(prep.out, "receipt_id", "staging-prepare")
        self.step("staging-app-writer", [py, "tools/provision-staging-app-writer.py", "--candidate-operation-id",
                                         cand, "--receipt-id", receipt, "--sha", sha, "--apply"], wt)

        # 4. production migrations, only when something is pending
        plan = self.step("migrate-plan", ["bin/migrate-prod.sh"], wt)
        pending = 0
        if not self.dry_run:
            m = re.search(r"pending:\s*(\d+)", plan.out)
            if not m:
                raise StepFailed("migrate-plan", 1, plan.log, "no pending count in output")
            pending = int(m.group(1))
        if pending or self.dry_run:
            if self.dry_run:
                self.out("  [dry-run] next line runs only when migrate-plan lists pending > 0")
            self.step("migrate-apply", ["bin/migrate-prod.sh", "--apply"], wt)

        # 5. upload the immutable candidate, verifier bound at upload time
        key = ("<next free r-%s-NN>" % self.today) if self.dry_run else next_release_key(
            self.today, lambda k: self._release_exists(wt, py, k))
        up = self.step("upload", ["bin/deploy-worker.sh", "--upload-version", "--release-sha", sha,
                                  "--release-key", key, "--test-evidence", ev["test_evidence"],
                                  "--security-evidence", ev["security_evidence"],
                                  "--verifier", ev["verifier"], "--verifier-evidence", ev["verifier_evidence"],
                                  *budget], wt)
        version = "<provider version from upload>" if self.dry_run else parse_provider_version(up)

        # 6. staging forward-fix rehearsal; promotion is unreachable unless it returned 0
        staging_ok = False
        self.step("staging", ["bin/deploy-worker.sh", "--env", "staging", "--recovery-step", "forward_fix",
                              "--release-key", key, "--release-sha", sha, *budget], wt)
        staging_ok = True

        # 7. promotion
        if not staging_ok:  # pragma: no cover — structural; StepFailed above already left
            raise StepFailed("promote", 1, "", "staging did not pass")
        self.step("promote", ["bin/deploy-worker.sh", "--promote-version", version, *budget], wt)

        # 8. live verification
        if self.dry_run:
            self.out(f"  [dry-run] GET {lane_cfg['live_release_url']} and require git_sha.value == {sha}")
        else:
            live = self.http(lane_cfg["live_release_url"])
            if (live.get("git_sha") or {}).get("value") != sha:
                raise StepFailed("verify-live", 1, "", "production /release does not serve the released SHA")
        self.step("health", ["./run.sh", "health"], wt, timeout=900)

        # 9. the schema snapshot goes back to main through its own PR
        schema_pr = None
        if self.dry_run:
            self.out("  [dry-run] when migrate-apply ran and db/schema.sql changed: branch from origin/main, "
                     "commit db/schema.sql, push, gh pr create (the merge pipeline merges it)")
        elif pending and self.git("status", "--porcelain", "db/schema.sql", cwd=wt):
            schema_pr = self.schema_followup(wt, sha)
        if not self.dry_run:
            self.step("worktree-remove", ["git", "-C", str(self.repo), "worktree", "remove", "--force", str(wt)], self.repo)
        return {"release_key": key, "provider_version_id": version, "migrations_applied": pending,
                "pr": ev["pr"], "prs": ev["prs"], "verifier": ev["verifier"],
                "verifier_evidence": ev["verifier_evidence"], "test_evidence": ev["test_evidence"],
                "schema_pr": schema_pr, "run_dir": str(self.run_dir)}

    def _release_exists(self, wt: Path, py: str, key: str) -> bool:
        res = self.runner.run([py, "tools/ops-record.py", "release", "show", "--key", key], cwd=wt,
                              log=self.run_dir / "release-key.log", env=self.env, timeout=120)
        if res.rc == 0:
            return True
        if res.rc == 2:
            return False
        raise StepFailed("release-key", res.rc, str(self.run_dir / "release-key.log"))

    def schema_followup(self, wt: Path, sha: str) -> str:
        branch = f"release/schema-snapshot-{sha[:8]}"
        fwt = self.store.root / "worktrees" / f"schema-{sha[:12]}"
        self.step("schema-worktree", ["git", "-C", str(self.repo), "worktree", "add", "-b", branch, str(fwt),
                                      "origin/main"], self.repo)
        shutil.copyfile(wt / "db/schema.sql", fwt / "db/schema.sql")
        self.step("schema-commit", ["git", "commit", "-m",
                                    f"Refresh db/schema.sql after the scripted release of {sha[:12]}\n\n"
                                    "bin/migrate-prod.sh regenerated it from production during the release; "
                                    "this carries it back to main.", "--", "db/schema.sql"], fwt)
        self.step("schema-push", ["git", "push", "-u", "origin", branch], fwt, timeout=3600)
        res = self.step("schema-pr", ["gh", "pr", "create", "--base", "main", "--head", branch,
                                      "--title", f"Refresh db/schema.sql after release {sha[:12]}",
                                      "--body", "Opened by ops/release-pipeline.py: the release applied "
                                      "production migrations and bin/migrate-prod.sh regenerated the snapshot."],
                        fwt)
        self.step("schema-worktree-remove", ["git", "-C", str(self.repo), "worktree", "remove", str(fwt)], self.repo)
        return res.out.strip().splitlines()[-1] if res.out.strip() else branch

    def release_app(self, lane_cfg: dict, repo_dir: Path, base: str, sha: str) -> dict:
        gh = self.github_factory(lane_cfg["github_repo"])

        def checks_green() -> None:
            checks = gh.check_runs(sha)
            pending = [c["name"] for c in checks if c.get("status") != "completed"]
            red = [c["name"] for c in checks if c.get("status") == "completed"
                   and c.get("conclusion") not in ("success", "skipped", "neutral")]
            if pending:
                raise Blocked("checks_pending", f"{', '.join(pending)} still running on {sha[:12]}")
            if red:
                raise Blocked("checks_red", f"{', '.join(red)} not green on {sha[:12]}")
        self.dry_tolerant("app checks", checks_green, None)
        wt = self.store.root / "worktrees" / f"app-{sha[:12]}"
        if wt.exists():
            raise StepFailed("app-worktree", 1, "", f"{wt} already exists; a prior run left it for diagnosis")
        self.step("app-worktree", ["git", "-C", str(repo_dir), "worktree", "add", "--detach", str(wt), sha], repo_dir)
        self.step("app-npm-ci", ["npm", "ci", "--no-audit", "--no-fund"], wt, timeout=1800)
        self.step("app-release", ["npm", "run", "release:production"], wt, timeout=3600)
        if self.dry_run:
            self.out(f"  [dry-run] GET {lane_cfg['live_release_url']} and require source_commit == {sha}")
        else:
            live = self.http(lane_cfg["live_release_url"])
            if live.get("source_commit") != sha or live.get("environment") != "production":
                raise StepFailed("app-verify-live", 1, "", "/app-release does not serve the released SHA")
            self.step("app-worktree-remove", ["git", "-C", str(repo_dir), "worktree", "remove", "--force", str(wt)],
                      repo_dir)
        return {"run_dir": str(self.run_dir)}


def parse_json_field(text: str, field: str, step: str) -> str:
    for line in reversed((text or "").strip().splitlines()):
        with contextlib.suppress(ValueError):
            obj = json.loads(line)
            if isinstance(obj, dict) and isinstance(obj.get(field), str):
                return obj[field]
    raise StepFailed(step, 1, "", f"no {field} in output")


def parse_provider_version(res: Result) -> str:
    m = re.search(r"provider version:\s*(" + UUID_RE.pattern + r")", res.out or "")
    if not m:
        raise StepFailed("upload", 1, res.log, "no provider version id in upload output")
    return m.group(1)


def report(store: Store, day: str) -> str:
    rows = [r for r in store.records() if str(r.get("ts", "")).startswith(day)]
    if not rows:
        return f"release-pipeline {day}: nothing shipped, failed or blocked."
    lines = [f"release-pipeline {day}:"]
    for r in rows:
        if r.get("status") == "shipped":
            extra = f" release {r.get('release_key')}" if r.get("release_key") else ""
            lines.append(f"  SHIPPED {r['lane']} {r['sha'][:12]}{extra} (PRs {r.get('prs') or '-'})")
        elif r.get("status") == "failed":
            lines.append(f"  FAILED  {r['lane']} {r['sha'][:12]} at {r.get('step')} "
                         f"(exit {r.get('rc')}; log {r.get('log')}; dispatched={r.get('dispatched')})")
        elif r.get("status") == "blocked":
            lines.append(f"  BLOCKED {r['lane']} {str(r.get('sha'))[:12]}: {r.get('reason')} — {r.get('detail')}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", nargs="?", default="tick", choices=("tick", "report"))
    ap.add_argument("--dry-run", action="store_true", help="print the exact commands; execute no deploy")
    ap.add_argument("--lane", choices=("worker", "app"), action="append")
    ap.add_argument("--date", default=dt.date.today().isoformat())
    args = ap.parse_args(argv)
    cfg = load_config()
    if args.command == "report":
        print(report(Store(REPO / cfg.get("state_dir", "out/release-pipeline")), args.date))
        return 0
    pipe = Pipeline(cfg, dry_run=args.dry_run)
    return pipe.tick(args.lane or ["worker", "app"])


if __name__ == "__main__":
    raise SystemExit(main())
