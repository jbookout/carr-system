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
manual release already reads them (~/.config/carr/db.env,
~/.config/carr/mcp-tokens.env), plus CLOUDFLARE_API_TOKEN from
~/.config/carr/tokens.env, which is loaded into the environment of the
wrangler-running steps only (never argv, never a log), and each step is the SAME sanctioned command the
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
            5 bin/deploy-worker.sh --upload-version  (verifier bound HERE; a pending
              Durable Object migration is applied first: Production attachment
              check, staging precheck, then a deploy of S whose own version
              becomes the candidate, so Production has moved before steps 6-8
              for that release only; see the do-migration block there. Its tag
              lands in the run record)
            6 bin/deploy-worker.sh --env staging --recovery-step forward_fix
            7 bin/deploy-worker.sh --promote-version <id from step 5>
            8 live /release reads back S, ./run.sh health
            9 a db/schema.sql follow-up PR when step 4 applied anything
           10 after SHIPPED, best-effort: ops/slice-done-marker.py --release-key K
              marks the DoctorCRE v5 slices this release shipped (register from
              the catalog, bind, gather server-resolved evidence, mark). Its exit
              is recorded as `slice_marker` in the run record; it never fails,
              blocks or retries the release it follows.
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
so the next tick re-evaluates it; any worktree the run created is removed. The
Cloudflare deploy token is checked before any worktree exists, and it is the
one credential that FAILS rather than holds: a missing tokens.env or key, or a
token wrangler rejects, stops the lane at step `credential-missing` and
dispatches, instead of wrangler falling back to its interactive OAuth login. An unexpected error before
any step ran is recorded and dispatched without burning the SHA; after a step
ran it is a failure like any other. A failure after migrate-apply records
db_ahead_of_worker: true and says so to the fix session. A missing credential or capability files one
CARR loop naming it exactly (once per name), because no retry can supply it.

REVIEW EVIDENCE. Both repositories are public, so any comment is untrusted until
proven otherwise. For EVERY commit in the batch: it came from a merged PR; the
LATEST comment carrying a verdict (first line APPROVE-marker or BLOCK-marker,
config review_markers/block_markers) from a trusted author (author_association
OWNER/MEMBER/COLLABORATOR, or a configured login) decides; it must be APPROVE;
and it must carry exactly one `Reviewed-SHA: <40-hex>` line equal to the PR's
head SHA (no dates: an exact SHA is the only freshness proof). Worker lane
also: every PR in the batch that touches a release path has a green `ops/ci.sh
--strict` (and secret-class) CI run. The Worker releases up to the NEWEST
first-parent commit whose own main canary concluded success (plus any
canary-ignored commits directly above it), not necessarily HEAD: commits whose
canary is absent, in progress or cancelled are walked past and wait for a later
green; a red verdict is never shipped nor anything above it until a newer green
fix-forward; no green commit newer than the last release holds
(Pipeline.release_target). Review evidence, CI, upload and live readback all
name that target.

THE TRUST BOUNDARY, stated plainly. Every CARR session posts to GitHub as
jbookout, which GitHub reports as OWNER. Comment authorship therefore proves only
"posted from inside CARR", NOT which agent reviewed. Maker ≠ verifier is
enforced by (1) the reviewer identity in the merge event the local
review-and-merge pipeline writes, which must differ from its author, and (2) the
database, which refuses a verifier slug equal to the maker (carr_jobs). The
comment is evidence of the verdict and the reviewed SHA, not of identity. App lane: the named required checks (`test`) are present
and green; an empty check list is a hold.

VERIFIER ≠ MAKER. The database derives the release maker from the filing login
(carr_jobs) and refuses a verifier equal to it (ops.approve_program5_release,
ops.record_program5_release_readiness). The verifier slug comes ONLY from the
merge event (`reviewer`) the local review-and-merge pipeline wrote, else the
configured default (`claude-review-agent`) — never from comment text. It is
refused if it names the maker, a human partner (the review was not a human's and
must never be booked as one), this pipeline, the GitHub account comments are
posted through, or the merge event's author.

CLEARING A FAILURE. A fix merged to main needs nothing: the new SHA is attempted.
To retry the SAME failed SHA after a fix outside the repository (a restored
credential, a provider outage), run
  ops/release-pipeline.py clear-failed --lane worker --sha <sha> --reason "<why>"
which records the clearance; never hand-edit state.json.

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

# The Workers deploy credential. Loaded from its file into the environment of
# the wrangler-running steps ONLY (never child_env, never argv, never a log
# line), so no step can fall back to wrangler's interactive OAuth login.
CLOUDFLARE_TOKEN_NAME = "CLOUDFLARE_API_TOKEN"
CLOUDFLARE_TOKEN_FILE = "tokens.env"   # under credential_dir


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


def read_env_value(path: Path, name: str) -> str | None:
    """The value of NAME in a NAME=value file (optional `export `, optional
    matching quotes), or None when the file or the key is absent or empty.
    The value is returned to the caller only; nothing here prints it."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    value = None
    for line in text.splitlines():
        m = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line)
        if not m or m.group(1) != name:
            continue
        v = m.group(2)
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        value = v or None
    return value


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

    def api(self, path: str, paginate: bool = False) -> Any:
        argv = ["gh", "api", *(["--paginate", "--slurp"] if paginate else []), path]
        proc = subprocess.run(argv, env=self.env, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            raise Blocked("github_unreadable", f"gh api {path.split('?')[0]} exited {proc.returncode}")
        data = json.loads(proc.stdout or "null")
        if paginate:   # --slurp yields one list per page
            return [item for page in (data or []) for item in (page or [])]
        return data

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
        return list(self.api(f"repos/{self.repo}/issues/{pr}/comments?per_page=100", paginate=True) or [])

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

def _glob_regex(glob: str) -> "re.Pattern[str]":
    """GitHub Actions path-filter semantics, so a glob means the same thing
    here as it does in a workflow's paths/paths-ignore: `*` and `?` never
    cross `/`, `**` crosses any number of directories, and `**/` also matches
    zero directories (`**/*.md` covers a top-level `README.md`, `a/**/*.md`
    covers `a/README.md`). fnmatch is NOT safe here: its `*` crosses `/`, so
    `docs/*.md` would silently swallow `docs/deep/x.md` and diverge from the
    canary's own filter."""
    out, i, n = ["^"], 0, len(glob)
    while i < n:
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        elif glob[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    out.append("$")
    return re.compile("".join(out))


def _glob_hit(path: str, glob: str) -> bool:
    return bool(_glob_regex(glob).match(path))


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


def choose_verifier(cfg: dict, event: dict | None) -> str:
    """The independent reviewer's slug, or Blocked. Never the maker.

    Taken ONLY from the merge event the review-and-merge pipeline wrote on this
    machine, else the configured default. Never from comment text: the
    repositories are public, so a comment is not a place an identity can be
    asserted from."""
    event = event or {}
    raw = str(event.get("reviewer") or event.get("verifier_actor") or cfg.get("default_verifier") or "")
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


def verdict(body: str, cfg: dict) -> str | None:
    """'approve', 'block' or None, read from the comment's FIRST line only."""
    first = (body or "").strip().splitlines()[0].strip().lower() if (body or "").strip() else ""
    if any(first.startswith(m.lower()) for m in cfg.get("block_markers") or []):
        return "block"
    if any(first.startswith(m.lower()) for m in cfg.get("review_markers") or []):
        return "approve"
    return None


def trusted_commenter(comment: dict, cfg: dict) -> bool:
    """Both repositories are public: anyone can comment on a merged PR. Only a
    comment from the owner, a member, a collaborator, or a configured login can
    carry a verdict."""
    assoc = str(comment.get("author_association") or "").upper()
    login = str((comment.get("user") or {}).get("login") or "").lower()
    allowed = {str(x).upper() for x in cfg.get("review_author_associations") or []}
    logins = {str(x).lower() for x in cfg.get("review_logins") or []}
    return assoc in allowed or (bool(login) and login in logins)


REVIEWED_SHA_RE = re.compile(r"^\s*Reviewed-SHA:\s*([0-9a-f]{40})\s*$", re.M)


def approval_of(comments: list[dict], cfg: dict, head_sha: str,
                covers: Callable[[str], str | None] | None = None) -> tuple[dict, str, str]:
    """(comment, rule, reviewed_sha). The LATEST trusted comment that carries a
    verdict decides. It must be APPROVE and carry exactly one
    `Reviewed-SHA: <40-hex>` line R. No clocks: a committer date says when a
    commit was made, not when it was pushed, so an approval of H1 posted
    between H2's commit and its push would otherwise cover H2 unreviewed.

    rule "exact": R is the merged PR head H.
    rule "main-merge-only": R != H, but `covers(R)` returns None, i.e. H is R
    plus nothing but merges of main (what `gh pr update-branch` adds after a
    review); covers() returns the reason otherwise, and the approval is stale."""
    last = _latest_approval(comments, cfg)
    reviewed = REVIEWED_SHA_RE.findall(str(last.get("body") or ""))
    if head_sha and reviewed == [head_sha]:
        return last, "exact", head_sha
    why = "no main-merge rule available"
    if head_sha and len(reviewed) == 1 and covers is not None:
        why = covers(reviewed[0]) or ""
        if not why:
            return last, "main-merge-only", reviewed[0]
    raise Blocked("review_stale", f"the approval {last.get('html_url')} carries "
                                  f"Reviewed-SHA {reviewed or 'none'}, not the merged head {head_sha}, "
                                  f"and the main-merge-only rule does not apply: {why}")


def latest_verdict(comments: list[dict], cfg: dict, head_sha: str) -> dict:
    """The approval comment under the exact rule only (see approval_of)."""
    return approval_of(comments, cfg, head_sha)[0]


def _latest_approval(comments: list[dict], cfg: dict) -> dict:
    carrying = [c for c in comments if trusted_commenter(c, cfg) and verdict(c.get("body", ""), cfg)]
    if not carrying:
        raise Blocked("no_independent_review", "no trusted comment carries a review verdict")
    last = max(carrying, key=lambda c: (str(c.get("created_at") or ""), int(c.get("id") or 0)))
    if verdict(last.get("body", ""), cfg) != "approve":
        raise Blocked("review_blocked", f"the latest review verdict is BLOCK ({last.get('html_url')})")
    return last


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


def queue_turn(lane: str, sha: str, step: str, rc: int, log: str, record_path: str,
               db_ahead_of_worker: bool = False, *, run_id: str, attempt: int = 1,
               do_migration: dict | None = None) -> dict:
    """msg_id is derived from (sha, step, run id): the room insert is `on
    conflict (msg_id) do nothing`, so a second, different failure of the same
    SHA must never share an id with the first. The queue key gains a suffix on
    later attempts for the same reason at the Hermes queue."""
    key = f"release-fix-{sha[:8]}" + (f"-{attempt}" if attempt > 1 else "")
    ahead = ("PRODUCTION MIGRATIONS WERE APPLIED in this run before it stopped "
             "(db_ahead_of_worker: true): the database is ahead of the serving Worker, so the fix "
             "must keep the new schema working with the currently deployed Worker.\n"
             if db_ahead_of_worker else "")
    if do_migration:
        what = ("WAS APPLIED" if do_migration.get("applied")
                else "WAS POSSIBLY APPLIED (the deploy's outcome could not be read back)")
        ahead += (f"A DURABLE OBJECT MIGRATION {what} in this run (tag {do_migration.get('tag')}): "
                  f"treat Production as serving {sha[:12]} through the deploy that applied it. Cloudflare "
                  "blocks rollback to any version from before that migration, so the fix is forward "
                  "only and must keep working with the migrated Durable Object class.\n")
    body = (f"@queue enqueue target=claude-desktop cap=repo-write priority=P1 runtime=3h "
            f"key={key} :: Fix forward: {lane} release of {sha[:12]} failed at {step}\n"
            f"The scripted release pipeline (ops/release-pipeline.py) stopped: step `{step}` "
            f"exited {rc} releasing {sha} ({lane} lane).\n{ahead}"
            f"Step log: {log}\nRun record: {record_path}\n"
            "Diagnose from the log and fix forward through an ordinary PR; do not merge it and "
            "do not run any deploy, migration or promotion yourself. This SHA is never retried: "
            "the pipeline releases the next main SHA after your fix merges. Reply in the room "
            "with the PR URL.")
    return {"idempotency_key": str(uuid.uuid4()), "room": "model-room", "seat": "claude",
            "kind": "turn", "body": body,
            "msg_id": str(uuid.uuid5(ROOM_NAMESPACE, f"{lane}:{sha}:{step}:{run_id}"))}


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
                 slice_marker: Callable[[str, str], dict] | None = None,
                 dry_run: bool = False, env: dict[str, str] | None = None,
                 today: str | None = None, out: Callable[[str], None] = print):
        self.cfg, self.repo, self.dry_run = cfg, repo, dry_run
        self.runner = runner or Runner()
        self.env = env if env is not None else child_env()
        self.github_factory = github or (lambda repo_name: GitHub(repo_name, self.env))
        self.http = http
        self.call_verb = call_verb or self._call_verb
        self.slice_marker = slice_marker or self._run_slice_marker
        self.today = today or dt.date.today().isoformat()
        self.out = out
        self.store = Store(repo / cfg.get("state_dir", "out/release-pipeline"))
        self.run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
        self.run_dir = self.store.root / "runs" / self.run_id
        self.executed: list[str] = []   # step names that actually ran, in order
        self.worktrees: list[tuple[Path, Path]] = []
        self.db_ahead_of_worker = False
        self.do_migration: dict | None = None   # a Durable Object migration the upload step applied
        self.mutated = False   # set when the first worktree is created; nothing before it writes

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

    def _run_slice_marker(self, release_key: str, sha: str) -> dict:
        """ops/slice-done-marker.py in the pipeline's own checkout, through the
        same run.sh call door and with the same minimal environment as
        _call_verb: no database or deploy credential reaches it."""
        log = self.run_dir / "slice-marker.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        venv = self.repo / ".venv" / "bin" / "python"
        env = {k: v for k, v in self.env.items() if k in ("HOME", "PATH", "LANG")}
        proc = subprocess.run([str(venv if venv.exists() else sys.executable),
                               str(self.repo / "ops" / "slice-done-marker.py"), "--release-key", release_key],
                              cwd=str(self.repo), env=env, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=1800)
        log.write_text((proc.stdout or "") + (proc.stderr or ""), encoding="utf-8")
        return {"rc": proc.returncode, "log": str(log), "release_sha": sha}

    def mark_slices(self, release_key: str, sha: str) -> dict:
        """Step 10, best-effort. Whatever happens here is recorded and never
        raised: the release it follows has already shipped."""
        try:
            outcome = self.slice_marker(release_key, sha)
        except Exception as exc:  # noqa: BLE001 — a marker failure is recorded, never raised
            outcome = {"rc": None, "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
        self.out(f"  -> slice-marker: {outcome}")
        return outcome

    def git(self, *args: str, cwd: Path | None = None) -> str:
        proc = subprocess.run(["git", "-C", str(cwd or self.repo), *args], env=self.env,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            raise StepFailed(f"git {args[0]}", proc.returncode, "", (proc.stderr or "").strip()[:300])
        return proc.stdout.strip()

    def step(self, name: str, argv: list[str], cwd: Path, *, timeout: int = 3600,
             env: dict[str, str] | None = None) -> Result:
        """Run one step, or in dry-run only print it. Nonzero is StepFailed.
        `env` replaces the base environment for this step only (the deploy
        steps pass deploy_env()); only argv is ever echoed or logged."""
        shown = " ".join(argv)
        if self.dry_run:
            self.out(f"  [dry-run] (cd {cwd}) {shown}")
            return Result(0, "")
        n = len(self.executed) + 1
        log = self.run_dir / f"{n:02d}-{name}.log"
        self.out(f"  -> {name}: {shown}")
        self.executed.append(name)
        res = self.runner.run(argv, cwd=cwd, log=log, env=self.env if env is None else env,
                              timeout=timeout)
        if res.rc != 0:
            raise StepFailed(name, res.rc, str(log))
        res.log = str(log)
        return res

    def health_read(self, name: str, cwd: Path) -> tuple[Result, list[dict], bool]:
        """Run `./run.sh health --findings-json PATH` once, logged like a
        normal step but never raising on its own — the caller decides what
        the findings mean. `cwd` need not be a release worktree: this reads
        canonical production database state, not the candidate's own code, so
        the pipeline's own repo checkout works and lets the baseline run
        BEFORE any worktree (or any --apply step) exists — see release_worker
        and points B/E of an independent review of PR #1237."""
        if self.dry_run:
            self.out(f"  [dry-run] (cd {cwd}) ./run.sh health --findings-json <path>  # {name}")
            return Result(0, ""), [], True
        n = len(self.executed) + 1
        log = self.run_dir / f"{n:02d}-{name}.log"
        findings_path = self.run_dir / f"{n:02d}-{name}-findings.json"
        self.out(f"  -> {name}: ./run.sh health --findings-json {findings_path}")
        self.executed.append(name)
        res = self.runner.run(["./run.sh", "health", "--findings-json", str(findings_path)],
                              cwd=cwd, log=log, env=self.env, timeout=900)
        res.log = str(log)
        findings, complete = read_health_findings(res.out, findings_path)
        return res, findings, complete

    HEALTH_BASELINE_ESCALATE_AFTER = 3

    def _health_baseline_or_block(self, wt: Path, state: dict, lane: str, sha: str
                                  ) -> tuple[list[dict], bool, int]:
        """The pre-promote health baseline, read in the release worktree
        `wt` (a checkout only, no --apply yet — see add_worktree's
        `mark_mutated=False` and release_worker) so the baseline and the
        later post-promote read run against the exact same code AND folder.
        The previous design read the baseline from the pipeline's own
        checkout and the post-promote health from the release worktree —
        two different `db/schema.sql` states after migrate-apply, so
        repo_loose_work spuriously "changed" on every migration release
        (point 1 of the second round of an independent review of PR #1237).

        An incomplete baseline is a clean Blocked hold (capability=None:
        retried next tick, no loop filed, the SHA never burned) UNLESS it
        has now failed to complete for HEALTH_BASELINE_ESCALATE_AFTER
        consecutive ticks on this SAME sha, in which case it escalates to a
        filed loop instead of holding silently forever (point 5) — the
        per-sha counter lives in `state[lane]["health_baseline_incomplete"]`
        and is cleared the moment a baseline for that sha completes.

        A baseline that DID complete but already carries a hard_error
        finding blocks too, before promote: every rc=1 path in
        tools/health-check.py records one (source-verified by
        tools/health-check-findings-selftest.py), so promoting on top of an
        already-broken read can only ever fail its own post-promote
        comparison — better to block here, before any --apply step, than to
        burn the SHA on a predictable post-promote failure (point 2).
        """
        baseline_res, findings, complete = self.health_read("health-baseline", wt)
        counters = state.setdefault(lane, {}).setdefault("health_baseline_incomplete", {})
        if not complete:
            n = int(counters.get(sha, 0)) + 1
            counters[sha] = n
            self.store.save(state)
            if n < self.HEALTH_BASELINE_ESCALATE_AFTER:
                raise Blocked("health_baseline_unavailable",
                              f"the pre-promote health baseline did not complete (attempt {n}/"
                              f"{self.HEALTH_BASELINE_ESCALATE_AFTER}); retrying next tick rather "
                              "than releasing without one to compare against")
            raise Blocked("health_baseline_stalled",
                          f"the pre-promote health baseline has not completed for {n} consecutive "
                          f"ticks on {sha[:12]}; escalating instead of holding silently forever",
                          capability="health_baseline_stalled")
        if counters.pop(sha, None) is not None:
            self.store.save(state)
        hard = sorted({row["key"] for row in findings if row.get("hard_error")})
        if hard:
            raise Blocked("health_baseline_hard_error",
                          "the pre-promote health baseline already shows a hard_error finding "
                          f"({', '.join(hard)}); promoting on top of an already-broken read can "
                          "only fail its own post-promote comparison, so this blocks before any "
                          "--apply step rather than burning the SHA on a predictable failure",
                          capability="health_baseline_hard_error")
        return findings, complete, baseline_res.rc

    def health_gate(self, cwd: Path, baseline: tuple[list[dict], bool, int]) -> list[str]:
        """Fail the release on a NEW canonical finding, not on standing debt
        that already existed before this promote. Returns the list of
        `excused` time-rolling findings from `health_regression` — round 13:
        a caller (`release_worker`) folds this into the shipped-release
        receipt so an unattributed clock-driven finding is visible even on
        a release that passes, not only inside a failing gate's log.

        `./run.sh health` reads canonical database state — export receipts,
        active-rule gaps, loose-work, credentials — almost none of which is
        about any one release; failing every release on every pre-existing
        gap (98 active rule gaps, 6 stuck export receipts, one loose path, on
        a normal day) trains nobody to read the gate, because it is never
        green. This compares the post-promote read against a baseline taken
        before any mutation (same production database, see release_worker)
        and fails only on health_regression()'s verdict: a new (key,subject),
        a risen count, or any live hard_error — the last of those unconditional,
        so a structural read failure (export receipts UNREADABLE) can never
        hide behind a baseline that had the same problem yesterday (point A).

        An unavailable read is never a pass, on either side: a baseline that
        did not complete excuses nothing (every current finding counts as
        new), and a live read that did not complete fails the gate outright
        regardless of what it printed.

        The fix-session dispatch (see Pipeline.fail/queue_turn) is pointed at
        a small file holding ONLY the new findings below, not the full
        `./run.sh health` output — a fix session should not have to read past
        98 unrelated standing rule gaps to find the two lines that are its
        actual job (point G).
        """
        baseline_findings, baseline_complete, baseline_rc = baseline
        res, findings, complete = self.health_read("health", cwd)
        if not complete:
            raise StepFailed("health", res.rc or 1, res.log,
                              "health read did not complete — an unavailable read is never a pass")
        new, excused = health_regression([] if not baseline_complete else baseline_findings, findings)
        # NARROW backstop for point A ("a nonzero health exit with no new
        # finding line now passes... any rc != 0 must fail") and point 3 of
        # the second AND third rounds of review — NOT the "hard runtime
        # guard" this comment used to call it, and not what actually
        # guarantees the invariant any more either. This condition is keyed
        # on `not findings` (the WHOLE parsed findings list being empty),
        # which on a real run is essentially never true — a real baseline/
        # live read is essentially always rc=1 with SOME findings present
        # (98 standing rule gaps on a normal day), so this line almost never
        # fires in production, same as the whole-run `not _FINDINGS` check
        # it mirrors inside tools/health-check.py's own `_canonical_health`.
        #
        # The REAL guarantee now lives in tools/health-check.py itself, and
        # it is static rather than runtime: round 8 of the same review
        # replaced `_canonical_health`'s earlier per-section runtime guard
        # (`_section_runtime_guard`, round 7 — since removed, along with
        # every call site) with a `_red()` helper that every `rc = 1` path
        # inside `_canonical_health` now goes through, recording the finding
        # FIRST and returning 1 in one call. tools/health-check-findings-
        # selftest.py statically walks `_canonical_health`'s AST and asserts
        # `rc` is never assigned a bare literal 1 (or `|=`'d with one)
        # directly — only via a call — so a section that would flip this run
        # red without a finding to explain it fails that mechanical check
        # before it can ever reach a real run, rather than depending on a
        # runtime guard (per-section or whole-run) to catch it after the
        # fact. THIS line only needs to catch what a static AST proof over
        # `_canonical_health` alone cannot: the `--findings-json` payload
        # never landing at all (a crash between the happy path and `_write_
        # findings_json`, or between `sys.exit` and this reading it) turning
        # `findings` into an empty list here even though `res.rc` is
        # nonzero — `complete` already guards the "never landed" case above,
        # so by the time we reach here `findings` is a genuine reflection of
        # what ran, and an empty list with a nonzero rc can now only mean
        # something outside `_canonical_health`'s own AST-proven contract
        # went wrong (e.g. `rc` set through a path the static check does not
        # model). Kept as a narrow, last-resort belt-and-suspenders alongside
        # that static proof and its mutation tests.
        if not new and res.rc != 0 and not findings:
            new = [f"./run.sh health exited {res.rc} but recorded no finding at all "
                  f"to explain it — treated as unavailable, never a pass"]
        if new:
            n = len(self.executed) + 1
            findings_log = self.run_dir / f"{n:02d}-health-new-findings.log"
            excused_section = (
                "\n\nExcused as clock noise, never attributed to this release (time-rolling, "
                "not attributed — see HEALTH_REGRESSION_FIRST_APPEARANCE_ALLOWLIST):\n"
                + "\n".join(f"- {line}" for line in excused) + "\n"
            ) if excused else ""
            findings_log.write_text(
                "New canonical finding(s) since the pre-promote baseline "
                "(ops/release-pipeline.py health_gate; full health output is in the "
                "health-baseline/health run logs alongside this file, not repeated here):\n"
                + "\n".join(f"- {line}" for line in new) + "\n" + excused_section, encoding="utf-8")
            raise StepFailed("health", res.rc or 1, str(findings_log),
                              "new canonical finding(s) since the pre-promote baseline: "
                              + "; ".join(new))
        return excused

    def _release_worktree_ready(self, name: str, wt: Path, mcp: Path, sha: str, *,
                                mark_mutated: bool) -> None:
        """The exact prefix that must run before ANY health read against a
        release worktree: checkout, link the venv, `npm ci`. Called from
        BOTH release_worker and health_preflight (point 2 of the third
        round of an independent review of PR #1237: the two must call the
        SAME step list, not two hand-copies of it, so they cannot drift
        apart again the way they already did once).

        `npm ci` is required BEFORE the health read, not merely BEFORE
        staging — a real health-preflight run (point 1, BLOCKER, of the
        third round) showed the baseline still failing with only venv-link
        done first: tools/db-tap.py's `dsn()` shells out to
        `mcp-server/node_modules/.bin/neonctl`, which does not exist until
        `npm ci` has installed it, so a fresh worktree with venv-link alone
        still hard-errors on `source_unreadable`/`job_ledger`/
        `control_state` for every DB-backed section. Neither venv-link nor
        `npm ci` writes any production state, so running them ahead of the
        baseline does not widen what a clean Blocked hold is allowed to be.

        venv-link FAILS CLOSED if the SOURCE venv (this pipeline checkout's
        own `.venv`) is missing or has no `bin/python` (a real run at
        a8619391 found this the hard way: the coordinator's first
        health-preflight attempt had no `.venv` on the machine at all,
        `ln -s` happily created a DANGLING symlink anyway, and the health
        read then reported 5 hard errors for missing psycopg/openpyxl —
        reading exactly like a code bug when it was actually this
        machine's environment, never mentioning the real cause). Checking
        the source before linking turns that into one clear StepFailed
        naming the actual missing path, the same "loud failure, dispatch,
        never a silent hold" contract wrangler_auth already uses for a
        missing/rejected deploy credential — a missing venv does not heal
        itself either.
        """
        self.add_worktree(name, self.repo, wt, sha, mark_mutated=mark_mutated)
        src_venv = self.repo / ".venv"
        src_python = src_venv / "bin" / "python"
        if not self.dry_run and not src_python.exists():
            raise StepFailed("venv-link", 1, "",
                             f"{src_venv} has no bin/python — this pipeline checkout's own venv "
                             f"is missing or incomplete, so linking it into the release worktree "
                             f"would only produce a dangling symlink whose health read then "
                             f"reports psycopg/openpyxl as MISSING, reading like a code bug when "
                             f"it is really this machine's environment. Fix the venv at "
                             f"{self.repo} (a missing venv never heals itself; retrying the same "
                             f"SHA will not help).")
        self.step("venv-link", ["ln", "-s", str(src_venv), str(wt / ".venv")], self.repo)
        self.step("npm-ci", ["npm", "ci", "--no-audit", "--no-fund"], mcp, timeout=1800)

    def health_preflight(self, sha: str) -> int:
        """Real evidence for point 1 (BLOCKER) of the third round of an
        independent review of PR #1237: a fake-runner selftest cannot prove
        `./run.sh health` actually succeeds against the linked venv and
        installed node_modules, because the fake runner never executes
        anything. This checks out a REAL, throwaway release worktree at
        `sha` (no --apply, no promote — the exact same
        `_release_worktree_ready` prefix release_worker runs before it ever
        mutates anything), runs the REAL `./run.sh health --findings-json`
        subprocess twice (mirroring the baseline and the post-promote read,
        both against the same folder, per point 1 of the SECOND round of
        review), prints what each one found, and always removes the
        worktree before returning — this command mutates nothing beyond
        that throwaway checkout, so it is safe to run against production
        data as a preflight.
        """
        wt = self.store.root / "worktrees" / f"preflight-{sha[:12]}"
        mcp = wt / "mcp-server"
        try:
            self._release_worktree_ready("preflight", wt, mcp, sha, mark_mutated=False)
            baseline_res, baseline_findings, baseline_complete = self.health_read("health-baseline", wt)
            self.out(f"  health-baseline: rc={baseline_res.rc} complete={baseline_complete} "
                     f"findings={len(baseline_findings)} "
                     f"hard_error={[f['key'] for f in baseline_findings if f.get('hard_error')]}")
            live_res, live_findings, live_complete = self.health_read("health", wt)
            self.out(f"  health (post):   rc={live_res.rc} complete={live_complete} "
                     f"findings={len(live_findings)} "
                     f"hard_error={[f['key'] for f in live_findings if f.get('hard_error')]}")
        except StepFailed as f:
            # A fail-closed environment problem (e.g. a missing source
            # venv) is exactly what this command exists to surface plainly
            # — print it and return 1 rather than a raw traceback.
            self.out(f"  health-preflight: FAILED at {f.step}: {f.detail}")
            return 1
        finally:
            self.remove_worktrees()
        ok = baseline_complete and live_complete and not any(
            f.get("hard_error") for f in baseline_findings)
        self.out("  health-preflight: " + ("OK" if ok else "FAILED — see rc/findings above"))
        return 0 if ok else 1

    # -- evidence -----------------------------------------------------------
    def batch_commits(self, repo_dir: Path, base: str, sha: str) -> list[str]:
        return self.git("rev-list", "--first-parent", f"{base}..{sha}", cwd=repo_dir).split()

    def review_evidence(self, gh: Any, lane_cfg: dict, repo_dir: Path, base: str, sha: str) -> dict:
        """Every commit in the batch came from a merged PR whose latest trusted
        review verdict is a fresh APPROVE. Returns the head PR's evidence and
        the PRs that touched a release path (each must have green CI)."""
        events = read_merge_events(self.repo / self.cfg.get("merge_event_file", "out/merge-events.jsonl"))
        reviewed: list[int] = []
        reviews: list[dict] = []
        pre_pipeline: list[int] = []
        release_prs: list[dict] = []
        head: dict | None = None
        for commit in self.batch_commits(repo_dir, base, sha):
            pr = gh.pr_for_commit(commit)
            if pr is None:
                raise Blocked("no_pull_request", f"{commit[:12]} reached main without a merged pull request")
            number, head_sha = int(pr["number"]), str(pr["head"]["sha"])
            parent = self.git("rev-parse", f"{commit}^1", cwd=repo_dir)
            touches, _ = classify(self.git("diff", "--name-only", parent, commit, cwd=repo_dir).splitlines(),
                                  lane_cfg)
            approval: dict | None
            rule = reviewed_sha = ""
            try:
                approval, rule, reviewed_sha = approval_of(
                    gh.comments(number), lane_cfg, head_sha,
                    # called synchronously inside this iteration, so the closure sees this commit's values
                    covers=lambda r: self.main_merge_only(repo_dir, number, r, head_sha, commit))
            except Blocked as b:
                cutover = str(lane_cfg.get("review_required_after") or "")
                if (b.reason == "no_independent_review" and commit != sha and cutover
                        and str(pr.get("merged_at") or "") < cutover):
                    pre_pipeline.append(number)
                    approval = None
                else:
                    raise Blocked(b.reason, f"PR #{number} ({commit[:12]}): {b.detail}")
            if approval is not None:
                reviewed.append(number)
                reviews.append({"pr": number, "rule": rule, "reviewed_sha": reviewed_sha, "head_sha": head_sha})
            if touches:
                release_prs.append({"pr": number, "head_sha": head_sha})
            if commit == sha and approval is not None:
                head = {"pr": number, "head_sha": head_sha, "url": str(approval.get("html_url") or ""),
                        "event": events.get(commit) or {}, "rule": rule, "reviewed_sha": reviewed_sha}
        if head is None:
            raise Blocked("no_independent_review", f"the head commit {sha[:12]} has no approval")
        return {"head": head, "prs": reviewed, "pre_pipeline_prs": pre_pipeline, "release_prs": release_prs,
                "reviews": reviews,
                "verifier": choose_verifier(lane_cfg, head["event"]),
                "verifier_evidence": evidence_ref_from_url(head["url"])}

    def main_merge_only(self, repo_dir: Path, number: int, reviewed: str, head: str,
                        merged: str) -> str | None:
        """None when merged PR head H is reviewed head R plus ONLY merges of
        main (the commits `gh pr update-branch` adds after a review);
        otherwise the reason. `merged` is the PR's commit on main, so
        `merged^1` is main as it stood when the PR landed.
          (a) R is an ancestor of H, and every commit in R..H that is not on
              main (main's own commits arrive through the merges) is a two-parent
              merge whose second parent is on main at that time and which,
              against that parent, changes nothing but the PR's own files
              (no content of its own smuggled in through a merge);
          (b) none of the PR's own files, the paths changed between
              merge-base(R, main-at-merge) and R, differ between R and H.
        main-at-merge, not today's main: today's main contains the PR, so its
        merge-base with R is R itself and the PR's file set would be empty."""
        if not SHA_RE.fullmatch(reviewed or "") or not SHA_RE.fullmatch(head or ""):
            return "Reviewed-SHA or PR head is not a full SHA"

        def have(obj: str) -> bool:
            try:
                self.git("cat-file", "-e", f"{obj}^{{commit}}", cwd=repo_dir)
                return True
            except StepFailed:
                return False

        def ancestor(a: str, b: str) -> bool:
            try:
                self.git("merge-base", "--is-ancestor", a, b, cwd=repo_dir)
                return True
            except StepFailed:
                return False

        def names(a: str, b: str) -> set[str]:
            return {x for x in self.git("diff", "--name-only", a, b, cwd=repo_dir).splitlines() if x.strip()}

        if not (have(reviewed) and have(head)):
            with contextlib.suppress(StepFailed):   # squash merges leave H off main: fetch the PR head
                self.git("fetch", "--quiet", "origin", f"refs/pull/{number}/head", cwd=repo_dir)
        if not have(head):
            return f"PR head {head[:12]} is not fetchable"
        if not have(reviewed):
            return f"Reviewed-SHA {reviewed[:12]} is not in the PR's history"
        if not ancestor(reviewed, head):
            return f"Reviewed-SHA {reviewed[:12]} is not an ancestor of the PR head {head[:12]}"
        main_then = self.git("rev-parse", f"{merged}^1", cwd=repo_dir)
        pr_files = names(self.git("merge-base", reviewed, main_then, cwd=repo_dir), reviewed)
        # The PR-side commits of R..H: main's own commits arrive through the
        # merges and are excluded (they were reviewed as their own PRs).
        added = self.git("rev-list", head, f"^{reviewed}", f"^{main_then}", cwd=repo_dir).split()
        parents_of = {c: self.git("rev-list", "--parents", "-n", "1", c, cwd=repo_dir).split()[1:]
                      for c in added}
        for c in added:
            if len(parents_of[c]) != 2:
                return f"{c[:12]} in R..H is not a two-parent merge"
        for c in added:
            second = parents_of[c][1]
            if not ancestor(second, main_then):
                return f"merge {c[:12]}'s second parent {second[:12]} is not on main"
            extra = sorted(names(second, c) - pr_files)
            if extra:
                return f"merge {c[:12]} changes non-PR file(s) against main: {', '.join(extra[:5])}"
        touched = sorted(names(reviewed, head) & pr_files)
        if touched:
            return f"PR file(s) changed after review: {', '.join(touched[:5])}"
        return None

    def canary_ignored(self, commit: str, lane_cfg: dict) -> bool:
        """True when EVERY path this commit changed matches main-canary's
        paths-ignore (so the canary legitimately never ran for it)."""
        try:
            parent = self.git("rev-parse", f"{commit}^1")
        except StepFailed:      # a root commit: nothing to call ignored
            return False
        paths = [p for p in self.git("diff", "--name-only", parent, commit).splitlines() if p.strip()]
        ignore = lane_cfg.get("canary_ignored_globs") or []
        return bool(paths) and all(any(_glob_hit(p, g) for g in ignore) for p in paths)

    def canary_verdict(self, gh: Any, lane_cfg: dict, commit: str) -> str:
        """One commit's own main-canary state: 'green', 'red', 'pending' (in
        progress or queued), 'cancelled' (cancelled/skipped/neutral) or 'none'."""
        runs = [r for r in gh.runs_for(commit) if r.get("name") == lane_cfg["canary_workflow_name"]]
        if not runs:
            return "none"
        latest = max(runs, key=lambda r: int(r.get("id") or 0))
        if latest.get("status") != "completed":
            return "pending"
        conclusion = latest.get("conclusion")
        if conclusion == "success":
            return "green"
        if conclusion in ("cancelled", "skipped", "neutral"):
            return "cancelled"
        return "red"

    def release_target(self, gh: Any, lane_cfg: dict, base: str, head: str) -> str:
        """The newest first-parent commit in base..head the Worker may ship.

        main-canary runs ~20 minutes with cancel-in-progress while merges land
        every 10-20 minutes, so HEAD's own run is nearly always in progress or
        cancelled. Requiring HEAD itself to be green starved the lane; instead
        walk back from HEAD and ship the NEWEST commit whose own canary
        concluded success. The canary judges the whole tree at its commit, so:
          - a commit with no run, a run in progress, or a cancelled/skipped run
            is walked past (never shipped itself unless it is canary-ignored
            and sits directly on a green commit, below);
          - a contiguous run of canary-IGNORED commits (every changed path in
            paths-ignore) directly above a green commit ships with it: none of
            them can change the canary's verdict;
          - a RED verdict excludes itself and everything above it. The walk
            continues and may ship an older green commit strictly below it,
            never the red commit. A green commit ABOVE a red one (a
            fix-forward) is the newest verdict and ships, as before.
        No green commit newer than `base` is a HOLD (canary_red when a red
        verdict is what stands in the way, else canary_pending)."""
        batch = set(self.batch_commits(self.repo, base, head))
        commits = self.git("rev-list", "--first-parent", "--max-count",
                           str(lane_cfg.get("canary_lookback", 50)), head).split()
        candidate: str | None = None       # top of a canary-ignored run awaiting its verdict
        red: str | None = None             # newest red verdict seen
        newest_unfinished: tuple[str, str] | None = None
        for commit in commits:
            if commit not in batch and candidate is None:
                break                      # at or below the last release, with nothing pending above
            verdict = self.canary_verdict(gh, lane_cfg, commit)
            if verdict == "green":
                target = candidate or commit
                if target in batch:
                    return target
                break
            if verdict == "red":
                red = red or commit
                candidate = None
                continue
            if self.canary_ignored(commit, lane_cfg) and verdict in ("none", "cancelled"):
                if candidate is None and commit in batch:
                    candidate = commit
                continue
            # a code commit with no verdict of its own: not shippable, and it
            # breaks any ignored run above it (that run's tree is unverified)
            candidate = None
            if newest_unfinished is None:
                newest_unfinished = (commit, verdict)
        else:
            if candidate is not None:
                raise Blocked("canary_missing", f"no main canary verdict within {len(commits)} "
                                                f"first-parent commits of {head[:12]}")
        if red is not None:
            raise Blocked("canary_red", f"main canary on {red[:12]} concluded red and no older green "
                                        f"commit is newer than the last release {base[:12]}")
        what = {"none": "has no run yet", "pending": "has not finished",
                "cancelled": "was cancelled or skipped"}
        detail = (f"; newest unverified: {newest_unfinished[0][:12]} {what[newest_unfinished[1]]}"
                  if newest_unfinished else "")
        raise Blocked("canary_pending", f"no commit newer than the last release {base[:12]} has a green "
                                        f"main canary{detail}")

    def ci_run(self, gh: Any, lane_cfg: dict, pr: int, head_sha: str) -> int:
        ci = [r for r in gh.runs_for(head_sha)
              if r.get("name") == lane_cfg["ci_workflow_name"] and r.get("event") == "pull_request"
              and r.get("conclusion") == "success"]
        if not ci:
            raise Blocked("ci_not_green", f"PR #{pr} has no successful {lane_cfg['ci_workflow_name']} run")
        run_id = max(int(r["id"]) for r in ci)
        jobs = gh.jobs(run_id)
        if not any(j.get("name") == lane_cfg["ci_required_job"] and j.get("conclusion") == "success" for j in jobs):
            raise Blocked("ci_not_green", f"PR #{pr} run {run_id} lacks a green `{lane_cfg['ci_required_job']}`")
        if not any("secret" in str(j.get("name", "")) and j.get("conclusion") == "success" for j in jobs):
            raise Blocked("ci_not_green", f"PR #{pr} run {run_id} lacks a green secret-class job")
        return run_id

    def worker_evidence(self, lane_cfg: dict, base: str, sha: str) -> dict:
        """Review and CI evidence for the batch base..sha. `sha` is already the
        canary-chosen release target (run_lane), never a later HEAD."""
        gh = self.github_factory(lane_cfg["github_repo"])
        rev = self.review_evidence(gh, lane_cfg, self.repo, base, sha)
        head = rev["head"]
        for item in rev["release_prs"]:   # EVERY release-path PR in the batch, not only the newest
            self.ci_run(gh, lane_cfg, item["pr"], item["head_sha"])
        run_id = self.ci_run(gh, lane_cfg, head["pr"], head["head_sha"])
        repo_name = lane_cfg["github_repo"]
        return {"pr": head["pr"], "prs": rev["prs"], "pre_pipeline_prs": rev["pre_pipeline_prs"],
                "reviews": rev["reviews"], "review_rule": head["rule"],
                "reviewed_sha": head["reviewed_sha"], "pr_head_sha": head["head_sha"],
                "verifier": rev["verifier"], "verifier_evidence": rev["verifier_evidence"],
                "test_evidence": f"github-actions:{repo_name}/runs/{run_id}#{lane_cfg['test_evidence_label']}",
                "security_evidence": f"github-actions:{repo_name}/runs/{run_id}#{lane_cfg['security_evidence_label']}"}

    def deploy_env(self) -> dict[str, str]:
        """The environment for a wrangler-running step: the base child env
        plus CLOUDFLARE_API_TOKEN from <credential_dir>/tokens.env. A missing
        file or key FAILS the step (stop, record, dispatch) instead of letting
        wrangler fall back to its interactive OAuth login. Only the NAME and
        the file path ever appear in output."""
        path = expand(self.cfg.get("credential_dir", "~/.config/carr")) / CLOUDFLARE_TOKEN_FILE
        token = read_env_value(path, CLOUDFLARE_TOKEN_NAME)
        if not token:
            detail = (f"credential missing: {CLOUDFLARE_TOKEN_NAME} is absent from {path}; "
                      "refusing to fall back to wrangler's interactive OAuth login")
            if self.dry_run:
                self.out(f"  [dry-run] a real run would FAIL here: {detail}")
                return dict(self.env)
            self.out(f"  !! {detail}")
            raise StepFailed("credential-missing", 1, "", detail)
        env = dict(self.env)
        env[CLOUDFLARE_TOKEN_NAME] = token
        return env

    def wrangler_auth(self, wrangler: Path, cwd: Path) -> None:
        """Before any worktree exists: the deploy token must be present and
        accepted. Either failure stops the lane and dispatches; neither is a
        silent hold, because a missing token never heals itself."""
        who = self.step("wrangler-auth", [str(wrangler), "whoami"], cwd, timeout=120,
                        env=self.deploy_env())
        if not self.dry_run and "not authenticated" in who.out.lower():
            raise StepFailed("credential-missing", 1, who.log,
                             f"credential rejected: wrangler whoami does not accept {CLOUDFLARE_TOKEN_NAME}")

    def add_worktree(self, name: str, repo_dir: Path, wt: Path, sha: str, *, mark_mutated: bool = True) -> None:
        """`mark_mutated=False` is for the worker lane's health baseline: the
        release worktree is created (a checkout only, no --apply) BEFORE the
        baseline read so the baseline and the post-promote read run against
        the exact same code and folder (point 1 of the second round of an
        independent review of PR #1237 — the previous design read the
        baseline from the pipeline's own checkout and the post-promote
        health from the release worktree, two different `db/schema.sql`
        states after migrate-apply, so repo_loose_work spuriously "changed"
        on every migration release). Deferring self.mutated keeps an
        incomplete baseline a clean Blocked hold (see release_worker): a
        plain checkout is trivially re-creatable and remove_worktrees()
        still cleans it up on that path, so it should not by itself burn
        the SHA the way a real --apply step's failure does."""
        if wt.exists():
            raise StepFailed(name, 1, "", f"{wt} already exists; a prior run left it for diagnosis")
        self.step(name, ["git", "-C", str(repo_dir), "worktree", "add", "--detach", str(wt), sha], repo_dir)
        self.worktrees.append((repo_dir, wt))
        if mark_mutated:
            self.mutated = True

    def remove_worktrees(self) -> None:
        """Called on success and on a hold. A failure keeps its worktree for
        diagnosis; a hold must leave nothing that turns the next tick into a
        failure."""
        for repo_dir, wt in reversed(self.worktrees):
            if wt.exists():
                self.runner.run(["git", "-C", str(repo_dir), "worktree", "remove", "--force", str(wt)],
                                cwd=repo_dir, log=self.run_dir / "worktree-cleanup.log", env=self.env, timeout=300)
        self.worktrees.clear()

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
    def _at_or_before(self, a: str, b: str, repo_dir: Path) -> bool:
        """True when commit `a` is `b` or an ancestor of it."""
        try:
            self.git("merge-base", "--is-ancestor", a, b, cwd=repo_dir)
            return True
        except StepFailed:
            return False

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
            if lane == "worker" and classify(self.git("diff", "--name-only", base, sha, cwd=repo_dir)
                                             .splitlines(), lane_cfg)[0]:
                # From here on `sha` is the RELEASE TARGET, the newest green
                # canary commit, not HEAD: review, CI, upload, live readback and
                # the state/record rows all name it. A doc/test-only batch needs
                # no canary and still advances to HEAD below.
                head = sha
                sha = self.dry_tolerant("canary", lambda: self.release_target(
                    self.github_factory(lane_cfg["github_repo"]), lane_cfg, base, head), head)
                if sha != head:
                    self.out(f"release-pipeline[{lane}]: main is {head[:12]}; newest green canary "
                             f"target is {sha[:12]}")
                failed = lane_state.get("failed_sha")
                if failed and self._at_or_before(sha, failed, repo_dir):
                    self.out(f"release-pipeline[{lane}]: target {sha[:12]} is at or before the failed "
                             f"{failed[:12]} ({lane_state.get('failed_step')}); waiting for a green "
                             "fix-forward")
                    return 0
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
                result = self.release_worker(lane_cfg, base, sha, state, lane)
            else:
                result = self.release_app(lane_cfg, repo_dir, base, sha)
            if self.dry_run:
                self.out(f"release-pipeline[{lane}]: dry run complete; nothing executed")
                return 0
            lane_state.update({"last_released_sha": sha, "failed_sha": None, "failed_step": None,
                               "last_shipped_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")})
            self.store.save(state)
            if lane == "worker" and result.get("release_key"):
                result["slice_marker"] = self.mark_slices(result["release_key"], sha)
            self.store.record({"lane": lane, "sha": sha, "from_sha": base, "status": "shipped",
                               "run_id": self.run_id, "paths": hits[:50], **result})
            self.out(f"release-pipeline[{lane}]: SHIPPED {sha[:12]}")
            return 0
        except Blocked as b:
            self.out(f"release-pipeline[{lane}]: BLOCKED {b.reason} — {b.detail}")
            if self.dry_run:
                return 0
            if self.mutated:
                # Something was already created or changed (a worktree, maybe a
                # production migration): this is no longer a clean hold.
                return self.fail(lane, state, sha, base, f"blocked:{b.reason}", 1, "-", b.detail)
            self.remove_worktrees()
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
            return self.fail(lane, state, sha, base, f.step, f.rc, f.log, f.detail)
        except Exception as exc:  # noqa: BLE001 — an unexpected error is recorded and dispatched, never lost
            detail = f"{type(exc).__name__}: {str(exc)[:300]}"
            self.out(f"release-pipeline[{lane}]: UNEXPECTED {detail}")
            if self.dry_run:
                return 1
            if not self.mutated:
                # Nothing was created or changed yet (a GitHub/HTTP/JSON read failed): record and
                # dispatch, but do not burn the SHA — the next tick re-reads.
                # Same SHA and same error class re-dispatch to the SAME msg_id,
                # so a transient read error cannot enqueue a session per tick.
                ok, _ = (False, "no SHA") if not sha else self.dispatch(
                    state, lane, sha, queue_turn(lane, sha, "unexpected-before-any-step", 1, "-",
                                                 str(self.store.records_path),
                                                 run_id=type(exc).__name__), allow_dedup=True)
                self.store.record({"lane": lane, "sha": sha, "from_sha": base, "status": "error",
                                   "detail": detail, "dispatched": ok, "run_id": self.run_id})
                return 1
            return self.fail(lane, state, sha, base, "unexpected", 1, "-", detail)

    def dispatch(self, state: dict, lane: str, sha: str, turn: dict, *, allow_dedup: bool = False
                 ) -> tuple[bool, Any]:
        """Post the fix-session turn. A `deduplicated: true` answer means the
        room kept an EARLIER turn and dropped this one; for a real failure that
        is not a dispatch (its content — the step, the db_ahead warning — was
        lost), so it is reported as not dispatched."""
        ok, res = self.call_verb("add-room-turn", turn)
        if ok and isinstance(res, dict) and res.get("deduplicated") and not allow_dedup:
            return False, "the room deduplicated this turn onto an earlier one; its content was dropped"
        if ok:
            counts = state.setdefault(lane, {}).setdefault("dispatches", {})
            counts[sha] = int(counts.get(sha, 0)) + 1
            self.store.save(state)
        return ok, res

    def fail(self, lane: str, state: dict, sha: str, base: str, step: str, rc: int, log: str,
             detail: str) -> int:
        self.out(f"release-pipeline[{lane}]: FAILED at {step} (exit {rc}); log {log or '-'}")
        if self.dry_run:
            return 1
        state.setdefault(lane, {}).update({
            "failed_sha": sha, "failed_step": step,
            "failed_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")})
        self.store.save(state)
        attempt = int((state[lane].get("dispatches") or {}).get(sha, 0)) + 1
        ok, res = (False, "no SHA") if not sha else self.dispatch(
            state, lane, sha, queue_turn(lane, sha, step, rc, log, str(self.store.records_path),
                                         db_ahead_of_worker=self.db_ahead_of_worker,
                                         do_migration=self.do_migration,
                                         run_id=self.run_id, attempt=attempt))
        self.store.record({"lane": lane, "sha": sha, "from_sha": base, "status": "failed",
                           "step": step, "rc": rc, "log": log, "detail": detail,
                           "db_ahead_of_worker": self.db_ahead_of_worker,
                           "do_migration": self.do_migration,
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

    def release_worker(self, lane_cfg: dict, base: str, sha: str, state: dict, lane: str) -> dict:
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

        # 0. the unattended Cloudflare login, before anything is created. A
        # missing/rejected token raises StepFailed from deploy_env() itself,
        # before any subprocess ever runs — so this still needs to come
        # before the release worktree for that failure to stay a clean,
        # zero-subprocess failure (existing DeployCredential selftests pin
        # `runner.calls == []` for it).
        self.wrangler_auth(self.repo / "mcp-server/node_modules/.bin/wrangler", self.repo / "mcp-server")

        # 1. the release worktree at exactly S, venv-linked, `npm ci`'d — a
        # checkout and install only, no --apply yet, created BEFORE the
        # health baseline so the baseline and the later post-promote read
        # run in the exact same code AND folder, with the exact same
        # dependencies available to them (add_worktree's docstring; points
        # 1 of the second round and 1/2 of the third round of an
        # independent review of PR #1237). Shared with health_preflight via
        # `_release_worktree_ready` so the two step lists cannot drift
        # apart. An incomplete or already-broken baseline is still a clean
        # Blocked hold here: the worktree is a trivially re-creatable
        # checkout, and remove_worktrees() (called from run_lane's `except
        # Blocked` path) cleans it up, so it does not by itself burn the
        # SHA the way a real --apply step would.
        self._release_worktree_ready("worktree", wt, mcp, sha, mark_mutated=False)

        # Health baseline, read INSIDE that same worktree — now with the
        # venv linked and node_modules installed, so it has exactly what
        # every DB-backed and neonctl-backed section needs (points B/E of
        # the first review round: before staging-prepare/staging-app-writer/
        # migrate-apply — every --apply step — so a release's OWN migration
        # damage can never hide inside its own "baseline" and forgive
        # itself; point 1 of the second round: in the SAME folder the
        # post-promote read will use, so a `db/schema.sql` rewritten by this
        # release's own migrate-apply does not spuriously diff against a
        # baseline read from a different checkout).
        health_baseline = self.dry_tolerant(
            "health baseline",
            lambda: self._health_baseline_or_block(wt, state, lane, sha),
            ([], True, 0))
        if not self.dry_run:
            # The checkout is now confirmed useful — a baseline was read
            # from it. From here on a hold is no longer "nothing happened
            # yet"; treat it like every other real failure.
            self.mutated = True

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
            if not self.dry_run:
                self.db_ahead_of_worker = True

        # 5. upload the immutable candidate, verifier bound at upload time
        key = ("<next free r-%s-NN>" % self.today) if self.dry_run else next_release_key(
            self.today, lambda k: self._release_exists(wt, py, k))
        #    A pending Durable Object migration is applied INSIDE this step
        #    (bin/deploy-worker.sh: `versions upload` cannot apply one): the
        #    Production attachment check and the staging precheck first, and
        #    Production traffic moves only after both are green. The migration
        #    deploy's own version is then the candidate, so for that release
        #    steps 6-8 run after Production has moved (their database writers
        #    need an uploaded Production version, which cannot exist earlier).
        #    Its marker line is carried into the run record whether the step
        #    then succeeds or fails.
        if self.dry_run:
            self.out("  [dry-run] the upload step first applies any pending Durable Object migration: "
                     "Production attachments unchanged, S to staging and its checks green, then a deploy "
                     "of S (100% traffic) whose version is the candidate; it refuses before Production "
                     "when the applied tag is unknown, attachments differ or the staging precheck fails")
        try:
            up = self.step("upload", ["bin/deploy-worker.sh", "--upload-version", "--release-sha", sha,
                                      "--release-key", key, "--test-evidence", ev["test_evidence"],
                                      "--security-evidence", ev["security_evidence"],
                                      "--verifier", ev["verifier"], "--verifier-evidence", ev["verifier_evidence"],
                                      *budget], wt, env=self.deploy_env())
        except StepFailed as failed:
            self.do_migration = parse_do_migration(read_log(failed.log))
            raise
        self.do_migration = parse_do_migration(up.out)
        version = "<provider version from upload>" if self.dry_run else parse_provider_version(up)

        # 6. staging forward-fix rehearsal; promotion is unreachable unless it returned 0
        staging_ok = False
        self.step("staging", ["bin/deploy-worker.sh", "--env", "staging", "--recovery-step", "forward_fix",
                              "--release-key", key, "--release-sha", sha, *budget], wt,
                  env=self.deploy_env())
        staging_ok = True

        # 7. promotion
        if not staging_ok:  # pragma: no cover — structural; StepFailed above already left
            raise StepFailed("promote", 1, "", "staging did not pass")
        self.step("promote", ["bin/deploy-worker.sh", "--promote-version", version, *budget], wt,
                  env=self.deploy_env())

        # 8. live verification
        if self.dry_run:
            self.out(f"  [dry-run] GET {lane_cfg['live_release_url']} and require git_sha.value == {sha}")
        else:
            live = self.http(lane_cfg["live_release_url"])
            if (live.get("git_sha") or {}).get("value") != sha:
                raise StepFailed("verify-live", 1, "", "production /release does not serve the released SHA")
        health_excused = self.health_gate(wt, health_baseline)

        # 9. the schema snapshot goes back to main through its own PR
        schema_pr = None
        if self.dry_run:
            self.out("  [dry-run] when migrate-apply ran and db/schema.sql changed: branch from origin/main, "
                     "commit db/schema.sql, push, gh pr create (the merge pipeline merges it)")
        elif pending and self.git("status", "--porcelain", "db/schema.sql", cwd=wt):
            schema_pr = self.schema_followup(wt, sha)
        if not self.dry_run:
            self.remove_worktrees()
        return {"release_key": key, "provider_version_id": version, "migrations_applied": pending,
                "do_migration": self.do_migration,
                "pr": ev["pr"], "prs": ev["prs"], "reviews": ev.get("reviews", []),
                "review_rule": ev.get("review_rule"), "reviewed_sha": ev.get("reviewed_sha"),
                "pr_head_sha": ev.get("pr_head_sha"), "verifier": ev["verifier"],
                "verifier_evidence": ev["verifier_evidence"], "test_evidence": ev["test_evidence"],
                "health_time_rolling_not_attributed": health_excused,
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
        """Same review evidence as the Worker lane; the named required checks
        must be PRESENT and green (an empty check list is not a pass)."""
        gh = self.github_factory(lane_cfg["github_repo"])

        def checks_green() -> None:
            checks = gh.check_runs(sha)
            for name in lane_cfg.get("required_checks") or ["test"]:
                runs = [c for c in checks if c.get("name") == name]
                if not runs:
                    raise Blocked("checks_missing", f"required check `{name}` has not reported on {sha[:12]}")
                if any(c.get("status") != "completed" for c in runs):
                    raise Blocked("checks_pending", f"`{name}` still running on {sha[:12]}")
                if any(c.get("conclusion") != "success" for c in runs):
                    raise Blocked("checks_red", f"`{name}` is not green on {sha[:12]}")
        self.dry_tolerant("app checks", checks_green, None)
        rev = self.dry_tolerant("app review", lambda: self.review_evidence(gh, lane_cfg, repo_dir, base, sha),
                                {"prs": [], "pre_pipeline_prs": [], "verifier_evidence": "<approval>"})
        self.out(f"  evidence: PRs {rev['prs']} approved; head approval {rev['verifier_evidence']}")
        self.wrangler_auth(self.repo / "mcp-server/node_modules/.bin/wrangler", self.repo / "mcp-server")
        wt = self.store.root / "worktrees" / f"app-{sha[:12]}"
        self.add_worktree("app-worktree", repo_dir, wt, sha)
        self.step("app-npm-ci", ["npm", "ci", "--no-audit", "--no-fund"], wt, timeout=1800)
        self.step("app-release", ["npm", "run", "release:production"], wt, timeout=3600,
                  env=self.deploy_env())
        if self.dry_run:
            self.out(f"  [dry-run] GET {lane_cfg['live_release_url']} and require source_commit == {sha}")
        else:
            live = self.http(lane_cfg["live_release_url"])
            if live.get("source_commit") != sha or live.get("environment") != "production":
                raise StepFailed("app-verify-live", 1, "", "/app-release does not serve the released SHA")
            self.remove_worktrees()
        head = rev.get("head") or {}
        return {"run_dir": str(self.run_dir), "prs": rev["prs"], "pre_pipeline_prs": rev["pre_pipeline_prs"],
                "reviews": rev.get("reviews", []), "review_rule": head.get("rule"),
                "reviewed_sha": head.get("reviewed_sha"), "pr_head_sha": head.get("head_sha"),
                "review_evidence": rev["verifier_evidence"]}


# The line tools/health-check.py's _canonical_health() prints last, on every
# completed pass regardless of rc. Its presence is what tells the release
# gate the read is trustworthy; its absence (a crash, REFUSED, a timeout) means
# the read is unavailable and must never be treated as clean — see
# ReleasePipeline.health_read below. Point F of an independent review of
# PR #1237: the marker must be the LAST NON-EMPTY LINE, not merely present
# anywhere in the output — a marker printed early followed by a traceback
# (the process crashed on its way out, after the happy-path print but before
# actually exiting clean) must not read as complete.
#
# MUST match tools/health-check.py's actual `print(...)` call EXACTLY — this
# was truncated (missing "; use --recovery --reason <why>.") from round 1
# through round 3, so `lines[-1].strip() == HEALTH_COMPLETE_MARKER` was
# FALSE on every real run, no matter how clean, and every real baseline and
# every real post-promote read came back `complete=False`. Every selftest
# passed anyway because FakeRunner builds its marker text FROM this same
# (wrong) constant, so the mismatch was invisible until a real run (point 3
# of the third round of review) exposed it. A change to health-check.py's
# printed line must update this constant in the SAME commit.
HEALTH_COMPLETE_MARKER = ("Projection freshness/tamper checks are recovery evidence; "
                          "use --recovery --reason <why>.")

# The finding fields ops/release-pipeline.py's health gate relies on out of
# tools/health-check.py's `--findings-json` payload (schema decided against
# Jev, architecture_or_design, 2026-09-24 — see that file's own
# `_canonical_finding` docstring for the reasoning): `key` is the finding
# category, `subject` scopes it to a specific target/job/gate (or "" for a
# whole-section-unreadable finding), `count` accumulates repeated occurrences
# of the same (key, subject) within one run, `hard_error` marks a finding
# that must always fail the release gate regardless of any baseline, and
# `time_rolling` marks one whose (key, subject) changes purely because
# wall-clock time passed (excluded from the regression diff entirely).
FINDING_FIELDS = ("key", "subject", "count", "hard_error", "time_rolling")


def read_health_findings(output: str, findings_path: Path) -> tuple[list[dict], bool]:
    """(findings, complete) out of one `./run.sh health --findings-json PATH`
    run's stdout and the JSON file it wrote.

    `complete` requires BOTH signals to agree the read reached its own end:
    the completion marker is the LAST NON-EMPTY line of stdout (point F), and
    `findings_path` holds a valid, schema-shaped payload — written by
    tools/health-check.py only after `_canonical_health()` returns without
    raising, so its mere presence is a second, independent proof the run
    finished. Either one being false means the read is unavailable and must
    never be treated as clean, nor diffed as a baseline: standing debt cannot
    be excused by a read that never actually looked for it.
    """
    lines = [ln for ln in (output or "").splitlines() if ln.strip()]
    marker_ok = bool(lines) and lines[-1].strip() == HEALTH_COMPLETE_MARKER
    findings: list[dict] = []
    json_ok = False
    try:
        payload = json.loads(findings_path.read_text(encoding="utf-8"))
        rows = payload["findings"]
        if not isinstance(rows, list):
            raise ValueError("findings is not a list")
        for row in rows:
            if not all(field in row for field in FINDING_FIELDS):
                raise ValueError(f"finding row missing a required field: {row!r}")
        findings = rows
        json_ok = True
    except (OSError, ValueError, KeyError, json.JSONDecodeError):
        findings = []
        json_ok = False
    return findings, (marker_ok and json_ok)


# repo_loose_work is excluded from the regression diff entirely (point 1 of
# the second round of an independent review of PR #1237): even reading the
# baseline and the post-promote check in the same worktree (see
# release_worker), migrate-apply legitimately rewrites db/schema.sql as
# part of a normal migration release, so this key can "change" on every
# migration release for a reason that is the release's own intended work,
# not a regression. It still prints in the health log for a human to read;
# it just never fails the gate.
HEALTH_REGRESSION_EXCLUDED_KEYS = frozenset({"repo_loose_work"})

# Point 2 of round 4 of an independent review of PR #1237: a `time_rolling`
# finding with NO baseline entry is excused as "first appearance of a
# clock-driven finding, nothing to regress against" ONLY when its key is one
# of these two genuinely clock-driven keys — confirmed against
# tools/health-check.py's actual `_canonical_finding` call sites: the STALE
# branch of the exports loop (`export_receipt`, `time_rolling=True` only
# when the 26h clock has crossed; the other three export_receipt reasons —
# NEVER RAN, LATEST FAILED, NEVER OK — are real state, not clock noise, and
# are correctly emitted with `time_rolling` left False, so they are not
# excused by this allowlist either) and the MISSING DUE branch of the jobs
# loop (`job_missing_due`; the sibling `job_due_non_success` branch in that
# same loop is deliberately NOT time_rolling, see its own comment there, and
# so never reaches this branch of health_regression at all).
#
# Blanket-excusing ANY `time_rolling` key with no baseline entry (the prior
# behavior) was too permissive: `doctrine_gate` and `doctrine_stale` are also
# marked `time_rolling=True` in health-check.py's doctrine/rule-gaps loop,
# but a release that introduces 40 gate failures where there were 0 before
# is a real regression, not a clock crossing, and must fail the gate. Jev
# consultation (architecture_or_design, 0.9 confidence) chose this explicit
# allowlist over marking `doctrine_gate` as not-`time_rolling` in
# health-check.py, since `doctrine_gate`'s COUNT (not just its first
# appearance) is still legitimately excused from a same-baseline RISE by the
# `time_rolling` branch below — only the null-baseline "first appearance"
# carve-out needed narrowing, not the whole flag.
#
# `doctrine_stale` joined this allowlist in round 7: unlike `doctrine_gate`
# (a count of gate BLOCKS in the last 24h, driven by what actually happened
# on this release — a real regression can push it from 0 to 40), a
# `doctrine_stale` finding's COUNT is how many doctrine sections have
# crossed their own, independently-set `review_after` date — a clock
# threshold with nothing to do with any particular release. A section
# crossing that threshold for the first time between the pre-promote
# baseline read and the post-promote live read is exactly the "first
# appearance of a clock-driven finding, nothing to regress against" case
# this allowlist exists for (see the docstring below), the same as
# `export_receipt`'s STALE branch crossing its own 26h clock. `doctrine_
# gate` stays OFF this allowlist for the reason above: it is time_rolling
# only in the narrower sense that an EXISTING baseline entry's count may
# rise on the clock (a rolling 24h window aging forward), not in the sense
# that its first-ever appearance is clock noise.
#
# ROUND 12 CORRECTION to the paragraph above: it described a key on this
# allowlist as "legitimately excused from a same-baseline RISE" while the
# NULL-baseline case still failed as new — that was backwards, and round 12
# reverses BOTH halves for a key on this allowlist specifically. The gate
# runs once, after promote, on a SHA it never retries, so a subject already
# present in the baseline is now excused no matter how far its count rises
# (an already-known-broken sub-daily job's job_missing_due count climbing
# every run must not fail every release forever), while a subject with NO
# baseline entry at all — the case round 2 through round 11 excused as
# "nothing to regress against" — is now the one that fails, because a job
# or section crossing its threshold for the very first time is real new
# information, not clock noise. `doctrine_gate` still sits OFF this
# allowlist and is unaffected: it keeps the original count-sensitive rule
# in full (see health_regression's docstring and the code below).
#
# ROUND 13 CORRECTION: round 12's "a new subject still fails" half brought
# back exactly the clock-driven false failure the first-appearance excuse
# existed to prevent. A job's own due window can pass during the tens of
# minutes a release takes between the pre-promote baseline read and the
# post-promote live read — nothing about THIS release causes that, the
# clock alone does — and that job then shows up as a brand-new
# `job_missing_due` subject with no baseline entry, which round 12 would
# fail. Round 13 takes every key on this allowlist OUT of the regression
# comparison entirely: neither a new subject NOR a count rise on an
# allowlisted key is ever attributed to a release. They are never hidden —
# `_canonical_health` still records them as ordinary findings (unchanged),
# and `health_gate` lists every one of them, explicitly labeled "time-
# rolling, not attributed", in both the release receipt (a shipped
# release) and the new-findings log (a failed one) — but none of them can
# ever be the REASON a release fails. `doctrine_gate` stays off this
# allowlist and is unaffected: still fully count-sensitive, new subject
# and count rise both still fail it, exactly as before round 12.
HEALTH_REGRESSION_FIRST_APPEARANCE_ALLOWLIST = frozenset(
    {"export_receipt", "job_missing_due", "doctrine_stale"})


def health_regression(baseline: list[dict], live: list[dict]) -> tuple[list[str], list[str]]:
    """Returns `(bad, excused)`. `bad` is the live finding(s) that should
    fail THIS release: a (key, subject) pair absent from the baseline, a
    count that rose since the baseline, or any live finding whose
    hard_error flag is set — unconditionally, never excused by a matching
    baseline entry, because a structural whole-section-unreadable read is
    never acceptable standing debt (point A of an independent review of PR
    #1237: "export receipts UNREADABLE" must always fail, every release,
    until it is fixed). A count that FELL, or is unchanged, is not a
    regression — improvement and no-op are both allowed to pass, which is
    the whole point of diffing against a baseline instead of failing on any
    standing finding.

    `excused` is every live finding whose key is on `HEALTH_REGRESSION_
    FIRST_APPEARANCE_ALLOWLIST` (`export_receipt`'s STALE variant,
    `job_missing_due`, `doctrine_stale`) — round 13, point [coordinator]:
    round 12 tried excusing an allowlisted key's already-present subjects
    while still failing a brand-new one, but a new subject is exactly what
    a job's due window crossing during the tens of minutes a release takes
    produces, bringing back the clock-driven false failure the allowlist
    existed to prevent in the first place. Round 13 takes these three keys
    OUT of the regression comparison ENTIRELY — neither a new subject nor a
    count rise on an allowlisted key can ever land in `bad`, regardless of
    whether the baseline had any entry for it at all. They are never
    hidden, though: every one of them is still recorded as an ordinary
    finding by `_canonical_health` (unchanged) and is returned here,
    explicitly, so the caller can list it — visibly labeled "time-rolling,
    not attributed" — in both a shipped release's receipt and a failed
    release's new-findings log, so nothing about them disappears from view,
    they simply cannot be the REASON a release fails.

    Every OTHER `time_rolling` key (i.e. `doctrine_gate`, the only one
    today) stays fully count-sensitive, unaffected by any of this, the
    original round-2/round-4 rule: a (key, subject) pair with NO baseline
    entry at all still fails as "new" (see `test_point2r4_a_new_doctrine_
    gate_finding_with_no_baseline_still_fails` in ops/release-pipeline-
    selftest.py), and a count RISE against an EXISTING baseline entry also
    still fails — only a FALL (or no change) is excused as clock noise for
    this non-allowlisted key, because a release that introduces 40 gate
    failures where there were 0 before is a real regression and must still
    fail, whether that lands as a new subject or a rising count.

    Note for a reader of a failing gate: `count` accumulates by (key,
    subject) within one run (see tools/health-check.py's `_canonical_
    finding`), so two findings that individually rose and fell inside a
    wider aggregate subject can cancel out in the total and never surface
    here (point 6) — a caller who needs finer resolution than that should
    split the subject by rule id rather than read the aggregate as exact.
    This applies with extra force to `job_missing_due`, whose subject is
    the job's definition key alone: several DIFFERENT missing due-windows
    for the same job accumulate onto ONE (key, subject) count — moot for
    `bad` now that this key is never count-compared, but still relevant to
    reading what `excused` reports.
    """
    # Round 14 (pre-existing since round 3): keyed by (key, subject) ALONE,
    # this dict let a time-rolling baseline row and an ordinary live row for
    # the same (key, subject) match each other as "prior" — e.g. export_
    # receipt[x.xlsx] STALE (time_rolling=True) at baseline, then LATEST
    # FAILED (time_rolling=False, not hard_error) after the release: the
    # live row matched the STALE baseline row, saw an unchanged count (1
    # against 1), and neither the count-rose check nor the time_rolling
    # branch fired — the finding vanished from both `bad` and `excused`
    # with the release never held accountable for it. Keying by (key,
    # subject, time_rolling) as well closes this: a row can only match a
    # PRIOR of the same rolling-ness, so a STALE->FAILED transition (or the
    # reverse) is a genuinely absent baseline entry for that row's own
    # (key, subject, time_rolling) identity, not a false match.
    baseline_by_key_subject = {(row["key"], row["subject"], bool(row.get("time_rolling"))): row
                               for row in baseline}
    bad: list[str] = []
    excused: list[str] = []
    for row in live:
        if row["key"] in HEALTH_REGRESSION_EXCLUDED_KEYS:
            continue
        detail = row.get("detail", "")
        if row.get("hard_error"):
            bad.append(f"{row['key']}[{row['subject']}]: hard_error — {detail}")
            continue
        prior = baseline_by_key_subject.get((row["key"], row["subject"], bool(row.get("time_rolling"))))
        if row.get("time_rolling"):
            if row["key"] in HEALTH_REGRESSION_FIRST_APPEARANCE_ALLOWLIST:
                # Round 13: these three confirmed-clock-driven keys are OUT
                # of the comparison entirely — neither a new subject nor a
                # count rise ever fails the gate — but every one is still
                # reported back, labeled, so nothing is hidden.
                excused.append(f"{row['key']}[{row['subject']}]: time-rolling, not "
                               f"attributed — {detail}")
                continue
            # Every other time_rolling key (doctrine_gate) stays fully
            # count-sensitive: a release that introduces 40 gate failures
            # where there were 0 before must still fail, whether that shows
            # up as a brand-new subject or a rising count on one already
            # present.
            if prior is None:
                bad.append(f"{row['key']}[{row['subject']}]: new — {detail}")
                continue
            if row.get("count", 0) > prior.get("count", 0):
                bad.append(f"{row['key']}[{row['subject']}]: time_rolling count "
                           f"{prior.get('count')} -> {row.get('count')} — {detail}")
            continue
        if prior is None:
            bad.append(f"{row['key']}[{row['subject']}]: new — {detail}")
        elif row.get("count", 0) > prior.get("count", 0):
            bad.append(f"{row['key']}[{row['subject']}]: count {prior.get('count')} -> "
                       f"{row.get('count')} — {detail}")
    return bad, excused


def parse_json_field(text: str, field: str, step: str) -> str:
    for line in reversed((text or "").strip().splitlines()):
        with contextlib.suppress(ValueError):
            obj = json.loads(line)
            if isinstance(obj, dict) and isinstance(obj.get(field), str):
                return obj[field]
    raise StepFailed(step, 1, "", f"no {field} in output")


DO_MIGRATION_RE = re.compile(
    r"^DO migration (?P<kind>applied|possibly applied): tag=(?P<tag>\S+) from=(?P<from_tag>\S+) "
    r"version=(?P<version>\S+)$",
    re.MULTILINE)


def parse_do_migration(text: str) -> dict | None:
    """The Durable Object migration bin/deploy-worker.sh applied (or possibly
    applied: the deploy's outcome could not be read back) in the upload step,
    from its one marker line; None when it applied none."""
    hits = list(DO_MIGRATION_RE.finditer(text or ""))
    if not hits:
        return None
    m = hits[-1]
    version = m.group("version")
    applied = m.group("kind") == "applied"
    return {"applied": applied, "possibly_applied": not applied, "tag": m.group("tag"),
            "from_tag": None if m.group("from_tag") == "none" else m.group("from_tag"),
            "provider_version_id": version if UUID_RE.fullmatch(version) else None}


def read_log(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8") if path else ""
    except OSError:
        return ""


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


def clear_failed(store: Store, lane: str, sha: str, reason: str) -> str:
    """The one sanctioned way to let a failed SHA be attempted again (after a
    fix outside the repository, such as a restored credential). A fix merged to
    main needs none of this: the new SHA is attempted on its own."""
    state = store.load()
    lane_state = state.get(lane) or {}
    if not lane_state.get("failed_sha"):
        return f"release-pipeline[{lane}]: nothing to clear"
    if lane_state["failed_sha"] != sha:
        raise SystemExit(f"release-pipeline[{lane}]: failed SHA is {lane_state['failed_sha']}, not {sha}")
    previous = {k: lane_state.get(k) for k in ("failed_sha", "failed_step", "failed_at")}
    lane_state.update({"failed_sha": None, "failed_step": None, "failed_at": None})
    state[lane] = lane_state
    store.save(state)
    store.record({"lane": lane, "sha": sha, "status": "failure_cleared", "reason": reason, **{
        "cleared_" + k: v for k, v in previous.items()}})
    return f"release-pipeline[{lane}]: cleared failed {sha[:12]} ({previous['failed_step']}); next tick retries it"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", nargs="?", default="tick",
                    choices=("tick", "report", "clear-failed", "health-preflight"))
    ap.add_argument("--sha", help="clear-failed: the exact failed SHA being cleared; "
                    "health-preflight: the sha to check out (defaults to origin/main)")
    ap.add_argument("--reason", help="clear-failed: why a retry of the same SHA is now right")
    ap.add_argument("--dry-run", action="store_true", help="print the exact commands; execute no deploy")
    ap.add_argument("--lane", choices=("worker", "app"), action="append")
    ap.add_argument("--date", default=dt.date.today().isoformat())
    args = ap.parse_args(argv)
    cfg = load_config()
    if args.command == "clear-failed":
        if not args.lane or len(args.lane) != 1 or not args.sha or not args.reason:
            ap.error("clear-failed needs exactly one --lane, --sha and --reason")
        print(clear_failed(Store(REPO / cfg.get("state_dir", "out/release-pipeline")),
                           args.lane[0], args.sha, args.reason))
        return 0
    if args.command == "report":
        print(report(Store(REPO / cfg.get("state_dir", "out/release-pipeline")), args.date))
        return 0
    if args.command == "health-preflight":
        # Always a REAL run — --dry-run would defeat the entire point (point
        # 1, BLOCKER, of the third round of review: proving the venv-linked
        # `./run.sh health` actually works, which only a real subprocess
        # execution can show).
        pipe = Pipeline(cfg, dry_run=False)
        sha = args.sha or pipe.git("rev-parse", "origin/main", cwd=pipe.repo)
        return pipe.health_preflight(sha)
    pipe = Pipeline(cfg, dry_run=args.dry_run)
    return pipe.tick(args.lane or ["worker", "app"])


if __name__ == "__main__":
    raise SystemExit(main())
