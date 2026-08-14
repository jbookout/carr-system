#!/usr/bin/env python3
"""settings-change-gate.py — a settings change records itself, or does not happen.

THE INCIDENT, 2026-08-14. A session was commissioned to fix main's merge
treadmill. Its instructions said, in Joe's own words, "this likely changes repo
settings, so present the finished configuration to Joe with what changed and
why." It chose a remedy Joe had listed himself, turned off the branch ruleset's
"require branches to be up to date" flag — and was INTERRUPTED before it
delivered the report.

The change reached the repository. The reason reached nothing: no commit, no
loop, no decision entry. Eight hours later a different session found the setting
missing, could not distinguish an authorised change from silent tampering, and
rebuilt authorship out of ruleset version history, a shell-history timestamp and
a transcript search. The change was correct the whole time. Nobody could tell.

THE CRUX, and it is NOT "sessions should write things down". That session was
told to write it down and would have. The record died because it depended on the
session SURVIVING TO THE END — and a session is the least durable thing in this
system. It can be interrupted, compacted, or closed mid-sentence.

So the record is taken by the same mechanism that performs the change, at the
moment it performs it. Prose asks; this takes.

HOW IT WORKS, two halves of one gate:
  PreToolUse   a settings write with no stated reason is REFUSED, with the one
               prefix that satisfies it printed in the refusal.
  PostToolUse  the change is recorded WITH ITS OUTCOME, because a change that
               failed and a change that landed are different facts and only the
               post hook knows which happened.

WHAT IT DELIBERATELY DOES NOT DO.

It never blocks on the recorder. If the record layer is unreachable the change
still stands and the row spools locally. A gate that can strand a partner
mid-incident because a database is down gets removed within a week, and should
be — the failure it prevents is silence, not urgency.

It never stores a secret VALUE. `gh secret set NEON_API_KEY` records that the
secret was set and which one; the value never appears on the command line in the
first place, and the recorded command is the invocation, not its stdin.

It does not fire on reads. A gate that trips on `gh api` GET or `git config
--get` gets muted within a day, and then it protects nothing.
"""

import json
import os
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_SPOOL = REPO / "out" / "settings-changes.jsonl"

# ── what counts as a settings change ─────────────────────────────────────────
# Every entry is here because it actually went invisible on 2026-08-14, not
# because it is conceivable: a ruleset flag, an Actions variable, git's own
# core.bare, and a launchd job registered by hand that no plist describes.
GH_API_WRITE = re.compile(r"\bgh\s+api\b.*?(?:-X|--method)\s+(POST|PATCH|PUT|DELETE)\b", re.I | re.S)
GH_API_SETTINGS_PATH = re.compile(
    r"(rulesets|/actions/(variables|secrets|permissions)|/branches/[^/\s]+/protection"
    r"|/repos/[^/\s]+/[^/\s]+(?:\s|$|\')|/collaborators|/keys|/hooks)", re.I)

PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bgh\s+secret\s+(set|delete|remove)\b", re.I), "github_secret"),
    (re.compile(r"\bgh\s+variable\s+(set|delete|remove)\b", re.I), "github_variable"),
    (re.compile(r"\bgh\s+repo\s+edit\b", re.I), "github_repo_setting"),
    (re.compile(r"\bgh\s+ruleset\s+(create|edit|delete)\b", re.I), "github_ruleset"),
    (re.compile(r"\bgit\s+config\b(?!.*(--get|--list|-l\b))", re.I), "git_config"),
    (re.compile(r"\blaunchctl\s+(load|unload|bootstrap|bootout|submit|enable|disable)\b", re.I),
     "launchd_job"),
    (re.compile(r"\bwrangler\s+secret\s+(put|delete)\b", re.I), "worker_secret"),
]

# A reason has to say something. These are the words a session reaches for when
# it is satisfying a gate rather than explaining a change.
JUNK_REASONS = {"", "x", "y", "test", "testing", "tmp", "temp", "n/a", "na", "none",
                "reason", "because", "fix", "change", "update", "asdf", "."}
MIN_REASON_CHARS = 8


def classify(command: str) -> tuple[str, str] | None:
    """Return (kind, target) when this command CHANGES a setting, else None."""
    for pattern, kind in PATTERNS:
        if pattern.search(command):
            return kind, _target(command)
    if GH_API_WRITE.search(command):
        path = GH_API_SETTINGS_PATH.search(command)
        if path:
            return "github_api", _target(command)
    return None


def _target(command: str) -> str:
    """The thing being changed, for the record. Never the whole command line —
    a command can carry a value, and this row is read by people who should not
    be shown one."""
    for token in shlex.split(command, posix=True) if _splittable(command) else command.split():
        if token.startswith(("repos/", "/repos/", "orgs/")) or "rulesets" in token:
            return token
        if token.startswith("com.carr.") or token.endswith(".plist"):
            return os.path.basename(token)
    words = command.split()
    return " ".join(words[:4])[:120]


def _splittable(command: str) -> bool:
    try:
        shlex.split(command, posix=True)
        return True
    except ValueError:
        return False


def reason_of() -> str:
    return (os.environ.get("CARR_CHANGE_REASON") or "").strip()


def reason_is_real(reason: str) -> bool:
    return (len(reason) >= MIN_REASON_CHARS
            and reason.strip().lower().rstrip(".") not in JUNK_REASONS)


def refuse(kind: str, target: str) -> None:
    sys.stderr.write(
        f"SETTINGS CHANGE GATE — refused an unrecorded {kind} change.\n\n"
        f"  target: {target}\n\n"
        "A settings change that carries no reason is indistinguishable from\n"
        "tampering the moment the session that made it ends. On 2026-08-14 an\n"
        "AUTHORISED ruleset change went unexplained for eight hours for exactly\n"
        "that reason: the session was interrupted before it could report, and\n"
        "the change outlived the only account of why it happened.\n\n"
        "State the reason and the change records itself:\n\n"
        f'  CARR_CHANGE_REASON="why this is being changed" <your command>\n\n'
        "The reason is stored with the change, its outcome, and this session's\n"
        "id, so the next session reads it instead of reconstructing it.\n")
    sys.exit(2)


def record(kind: str, target: str, command: str, reason: str,
           outcome: str, session_id: str) -> None:
    """Write the change to the record layer, and to a local spool if that fails.
    NEVER raises into the caller: the change has already happened, and refusing
    to acknowledge it would make the gate the thing that hides history."""
    row = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "target": target,
        "reason": reason,
        "outcome": outcome,
        "session_id": session_id,
        # The invocation, never its stdin. `gh secret set X` records that X was
        # set; the value was never on the command line to begin with.
        "command": command[:300],
    }

    if os.environ.get("CARR_SETTINGS_GATE_OFFLINE") != "1":
        try:
            subprocess.run(
                [str(REPO / ".venv" / "bin" / "python"), str(REPO / "tools" / "ops-record.py"),
                 "settings-change",
                 "--kind", kind, "--target", target, "--reason", reason,
                 "--outcome", outcome, "--session", session_id],
                capture_output=True, timeout=20, check=True)
            return
        except Exception:                                    # noqa: BLE001
            pass  # fall through to the spool — never block

    spool = Path(os.environ.get("CARR_SETTINGS_SPOOL") or DEFAULT_SPOOL)
    try:
        spool.parent.mkdir(parents=True, exist_ok=True)
        with open(spool, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
        sys.stderr.write(
            f"settings-change-gate: recorded {kind} locally "
            f"({spool}) — the record layer was unreachable.\n")
    except Exception as exc:                                 # noqa: BLE001
        sys.stderr.write(
            f"settings-change-gate: COULD NOT RECORD this {kind} change ({exc}). "
            f"The change stands. Say so in your reply.\n")


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:                                        # noqa: BLE001
        return 0

    command = (payload.get("tool_input") or {}).get("command") or ""
    if not command:
        return 0

    found = classify(command)
    if not found:
        return 0
    kind, target = found

    event = payload.get("hook_event_name") or "PreToolUse"
    session_id = payload.get("session_id") or "unknown"
    reason = reason_of()

    if event == "PreToolUse":
        if not reason_is_real(reason):
            refuse(kind, target)
        return 0

    # PostToolUse — the change has run and its outcome is knowable.
    response = payload.get("tool_response") or {}
    code = response.get("exit_code")
    outcome = "applied" if code in (0, None) else "failed"
    record(kind, target, command, reason or "(none stated)", outcome, session_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
