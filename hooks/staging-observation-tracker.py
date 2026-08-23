#!/usr/bin/env python3
"""staging-observation-tracker.py — Pre+PostToolUse Bash: attribute by
OBSERVATION, not by tool type.

WHY. staging-attribution-gate.py (decision 9e1f83c2) answers "did THIS
session write this file" purely from the session transcript's own
Write/Edit/MultiEdit tool_use records. That is ground truth for the
harness's own file-editing tools, but it is blind to a tracked file changed
by a SCRIPT this session ran through Bash — a pipeline run, a shell
redirect, `sed -i`, a migration, the nightly chain regenerating a render.
Those leave no Write/Edit/MultiEdit record, so the file reads as "not
written by this session" and staging it is refused — even though a standing
rule (bc9188b4) requires every unattended run, scheduled ones included, to
commit its own work. Tonight's nightly chain could be refused staging its
own regenerated output.

THE FIX: attribute by OBSERVATION. This one script runs on BOTH sides of
every Bash call this session makes (it is wired twice in ops/config/
hooks.json: once as a PreToolUse hook and once as a PostToolUse hook on the
same "Bash" matcher, and branches on payload["hook_event_name"]):

  PreToolUse  — snapshot `git status --porcelain` for the repo, keyed to
                THIS EXACT tool_use_id, before the command runs.
  PostToolUse — snapshot again after the command runs, diff against the
                matching pre-snapshot for that same tool_use_id, and credit
                every TRACKED path that went from clean-or-absent to dirty
                as OBSERVED-DIRTY for this session. Accumulates across the
                whole session in out/staging-observed/<session_id>.json.

staging-attribution-gate.py unions this observed set with its own
transcript scan (own_written_paths() UNION observed_dirty_paths()) when it
decides whether a staged path belongs to this session.

THE PostToolUse PAYLOAD WAS VERIFIED BEFORE COMMITTING TO THIS DESIGN, not
assumed: `strings` on the installed Claude Code binary
(cli.js-equivalent) shows the exact PreToolUse/PostToolUse hook-input
construction —

    PreToolUse:  {...mh(...), hook_event_name:"PreToolUse",
                  tool_name, tool_input, tool_use_id}
    PostToolUse: {...mh(...), hook_event_name:"PostToolUse",
                  tool_name, tool_input, tool_response, tool_use_id,
                  duration_ms}

where mh(...) supplies {session_id, transcript_path, cwd, prompt_id,
permission_mode, agent_id, agent_type, effort}. Two things that finding
settles: (1) `tool_use_id` is present and IDENTICAL on both the Pre and the
Post call for one invocation, which is exactly the correlation key this
design needs and was not assumed; (2) this tracker never needs to parse
`tool_response` (Bash stdout/stderr) at all, because it computes the actual
post-command git state itself via a real `git status` call rather than
trying to infer it from command output — which sidesteps needing to know
that field's exact shape.

WHY DIFF AGAINST THE PER-CALL PRE-SNAPSHOT, NOT SESSION START. A path
already dirty from another session's in-flight edit before this session's
first Bash call must NEVER become "observed" just because this session later
runs an unrelated Bash command in the same shared tree — that would reopen
exactly the incident staging-attribution-gate.py exists to close. Diffing
against the immediately-prior (per-call) snapshot means only a path that
changed STATE as a direct result of the command just run gets credited,
never a path that was already dirty and stayed exactly as dirty.

SCOPE: only Bash calls whose cwd resolves inside this repo. A brand-new
untracked path ("??") is never credited here — staging-attribution-gate.py
already allows those unconditionally, for the same reason its own docstring
gives, so crediting them here would be redundant, not additive.

FAILS OPEN AND SILENT on both sides, like every hook in this file: this
tracker never blocks a tool call (it holds no gate logic at all) and never
surfaces an error to the session. Losing an observation only costs
staging-attribution-gate.py one honest "not attributed, name it or override"
refusal on that one path later — the safe direction, and recoverable by the
override envelope that already exists.

Fixtures: ops/staging-observation-tracker-selftest.py
"""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_DIR = os.path.join(REPO, "out", "staging-observed")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:                                    # telemetry only — never load-bearing
    import hook_meter
    DEBUG = hook_meter.guard_log_path(REPO)
except Exception:                       # a missing meter must not change a verdict
    DEBUG = os.path.join(REPO, "out", "hook-guard.log")
MAX_PENDING = 200  # defensive cap: a killed session must not leak forever


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def dlog(msg):
    try:
        os.makedirs(os.path.dirname(DEBUG), exist_ok=True)
        with open(DEBUG, "a") as fh:
            fh.write(f"{now()} staging-observation-tracker {msg}\n")
    except Exception:
        pass


def _safe_session(session_id):
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in (session_id or "unknown"))


def state_path(session_id):
    return os.path.join(STATE_DIR, f"{_safe_session(session_id)}.json")


def read_state_unlocked(path):
    try:
        with open(path) as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            data.setdefault("observed", [])
            data.setdefault("pending", {})
            return data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return {"observed": [], "pending": {}}


def write_state_unlocked(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".staging-observed-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


@contextmanager
def locked_state(session_id):
    path = state_path(session_id)
    os.makedirs(STATE_DIR, exist_ok=True)
    lock_path = path + ".lock"
    with open(lock_path, "a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        data = read_state_unlocked(path)
        try:
            yield data
        finally:
            write_state_unlocked(path, data)
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def porcelain_status(repo=REPO):
    """{repo-relative-posix-path: 2-char XY status} for the whole tree."""
    try:
        out = subprocess.run(
            ["git", "-C", repo, "status", "--porcelain", "--untracked-files=all"],
            capture_output=True, text=True, timeout=20,
        ).stdout
    except Exception:
        return {}
    status = {}
    for line in out.splitlines():
        if len(line) <= 3:
            continue
        code = line[:2]
        rest = line[3:].strip()
        if " -> " in rest:
            rest = rest.split(" -> ", 1)[1]
        status[rest.strip('"')] = code
    return status


def in_repo(cwd, repo=REPO):
    try:
        path = os.path.realpath(os.path.expanduser(cwd or ""))
        # realpath the comparison root too: on macOS /var is a symlink to
        # /private/var, so an un-resolved `repo` (REPO itself is built from
        # abspath, not realpath, and a caller/fixture may pass a raw
        # tempfile.mkdtemp() path) would never equal a realpath'd cwd even
        # when they name the same directory.
        repo_real = os.path.realpath(os.path.expanduser(repo or ""))
    except Exception:
        return False
    return path == repo_real or path.startswith(repo_real + os.sep)


def handle(payload, repo=REPO):
    """Pure-ish core, given a decoded payload. Returns nothing; only side
    effect is the per-session state file. Split out from main() so the
    selftest can drive it directly against a fixture repo without spawning a
    subprocess for every case."""
    if (payload.get("tool_name") or payload.get("toolName")) != "Bash":
        return
    if not in_repo(payload.get("cwd"), repo):
        return
    session_id = payload.get("session_id") or payload.get("sessionId") or "unknown"
    call_id = payload.get("tool_use_id") or payload.get("toolUseId") or ""
    event = payload.get("hook_event_name") or payload.get("hookEventName") or ""

    if event == "PreToolUse":
        snapshot = porcelain_status(repo)
        with locked_state(session_id) as data:
            if call_id:
                data["pending"][call_id] = snapshot
            if len(data["pending"]) > MAX_PENDING:
                for stale_key in list(data["pending"])[: len(data["pending"]) - MAX_PENDING]:
                    data["pending"].pop(stale_key, None)
        return

    if event == "PostToolUse":
        after = porcelain_status(repo)
        with locked_state(session_id) as data:
            before = data["pending"].pop(call_id, None) if call_id else None
            if before is None:
                # No matching pre-snapshot -- harness restarted mid-call, no
                # tool_use_id on this build, or this Bash call somehow never
                # fired the Pre side. Fail SAFE: credit nothing rather than
                # guess, exactly like every other gate in this file.
                dlog(f"no pre-snapshot for call_id={call_id!r} session={session_id!r}")
                return
            observed = set(data["observed"])
            for path, code in after.items():
                if code == "??":
                    continue  # brand-new file; staging-attribution-gate.py
                              # already allows these unconditionally
                if before.get(path) != code:
                    observed.add(path)
            data["observed"] = sorted(observed)
        return


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        sys.exit(0)
    try:
        handle(payload)
    except Exception as exc:
        dlog(f"ALLOW(internal-error) {exc}")
    sys.exit(0)  # this tracker never blocks; it only ever observes


if __name__ == "__main__":
    main()
